"""파이프라인 단위 테스트 (stdlib unittest, 외부 의존 없음).

실행: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.common import config, okf
from scripts.ingest.kopis import KopisAdapter
from scripts.ingest.tourapi import TourApiAdapter
from scripts.normalize import upsert as upsert_mod
from scripts.recommend import rank
from scripts.notify import dispatch as notify
from scripts.macro import apply as macro
from scripts.ingest import websearch
from scripts.build import build_sqlite
from scripts.build import build_index
from scripts.build import build_pages
from scripts.build import wiki_index
from scripts.recommend import ai_planner

from scripts.normalize import geocode
from scripts.common import http as common_http
from scripts.ops import usage_report


class TestConfig(unittest.TestCase):
    def test_canonical_sido_alias_and_partial(self):
        self.assertEqual(config.canonical_sido("부산"), "부산광역시")
        self.assertEqual(config.canonical_sido("경기"), "경기도")
        self.assertEqual(config.canonical_sido("서울특별시"), "서울특별시")
        # 형제 시/도(같은 2자 접두)는 보조 글자(남/북)로 정확히 판별 — 첫 항목으로 붕괴 금지
        self.assertEqual(config.canonical_sido("충청남도 천안시"), "충청남도")
        self.assertEqual(config.canonical_sido("충청북도 청주시"), "충청북도")
        self.assertEqual(config.canonical_sido("경상남도 창원시"), "경상남도")
        self.assertEqual(config.canonical_sido("경상북도 포항시"), "경상북도")
        # 짧은 표기는 여전히 동작
        self.assertEqual(config.canonical_sido("서울시"), "서울특별시")
        # 모호한 bare 접두(남/북 단서 없음)는 임의로 고르지 않고 보존
        self.assertEqual(config.canonical_sido("경상"), "경상")

    def test_age_bands_ranges(self):
        self.assertEqual(config.age_bands("7-13"), ["어린이"])
        self.assertIn("청년", config.age_bands("19-"))
        self.assertIn("노년", config.age_bands("19-"))
        self.assertEqual(config.age_bands(None), [])
        self.assertEqual(config.age_bands("12"), ["어린이"])

    def test_map_theme(self):
        self.assertEqual(config.map_theme("tourapi", "A02070100"), "축제")
        self.assertIsNone(config.map_theme("kopis", "ZZZZ"))


class TestOKF(unittest.TestCase):
    def _ev(self):
        return {
            "id": "kopis:X1", "name": "행사", "start_date": "2026-07-18T00:00:00+09:00",
            "url": "https://e", "location": {"sido": "서울특별시"}, "source": "kopis",
            "fetched_at": "2026-06-20T00:00:00+09:00",
        }

    def test_content_hash_ignores_volatile(self):
        a = self._ev(); b = dict(a, fetched_at="2026-06-21T00:00:00+09:00")
        self.assertEqual(okf.content_hash(a), okf.content_hash(b))

    def test_content_hash_detects_change(self):
        a = self._ev(); b = dict(a, name="다른행사")
        self.assertNotEqual(okf.content_hash(a), okf.content_hash(b))

    def test_event_path_layout(self):
        p = okf.event_path(self._ev())
        self.assertTrue(str(p).endswith("events/2026/07/seoul/kopis_X1.md"))

    def test_markdown_roundtrip(self):
        ev = self._ev()
        md = okf.to_markdown(ev, "본문")
        fm, body = okf.parse_markdown(md)
        self.assertEqual(fm["id"], "kopis:X1")
        self.assertIn("본문", body)


class TestAdapters(unittest.TestCase):
    def test_kopis_offline_mapping(self):
        rows = KopisAdapter(offline=True).collect()
        ids = {r["id"] for r in rows}
        self.assertIn("kopis:PF200001", ids)
        first = next(r for r in rows if r["id"] == "kopis:PF200001")
        self.assertEqual(first["location"]["sido"], "서울특별시")
        self.assertEqual(first["event_type"], "MusicEvent")
        self.assertTrue(first["start_date"].startswith("2026-07-18"))
        self.assertTrue(first["content_hash"].startswith("sha256:"))

    def test_tourapi_offline_mapping_geo(self):
        rows = TourApiAdapter(offline=True).collect()
        e = next(r for r in rows if r["id"] == "tourapi:3001001")
        self.assertEqual(e["event_type"], "Festival")
        self.assertEqual(e["location"]["sido"], "강원특별자치도")
        self.assertAlmostEqual(e["location"]["lat"], 37.7519, places=3)


class TestUpsert(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = okf.EVENTS_DIR
        okf.EVENTS_DIR = Path(self.tmp.name)

    def tearDown(self):
        okf.EVENTS_DIR = self._orig
        self.tmp.cleanup()

    def _ev(self, **over):
        ev = {
            "id": "kopis:U1", "name": "행사", "start_date": "2026-07-18T00:00:00+09:00",
            "url": "https://e", "location": {"sido": "서울특별시"}, "source": "kopis",
            "fetched_at": "2026-06-20T00:00:00+09:00", "status": "Scheduled",
        }
        ev.update(over)
        ev["content_hash"] = okf.content_hash(ev)
        return ev

    def test_create_then_skip_idempotent(self):
        ev = self._ev()
        self.assertEqual(upsert_mod.upsert([ev])["created"], 1)
        self.assertEqual(upsert_mod.upsert([ev])["skipped"], 1)

    def test_update_on_change(self):
        upsert_mod.upsert([self._ev()])
        s = upsert_mod.upsert([self._ev(name="변경")])
        self.assertEqual(s["updated"], 1)

    def test_archived_policy(self):
        upsert_mod.upsert([self._ev()])
        s = upsert_mod.upsert([], processed_sources={"kopis"})
        self.assertEqual(s["archived"], 1)
        # 두 번째엔 이미 archived → 다시 archive 안 함
        s2 = upsert_mod.upsert([], processed_sources={"kopis"})
        self.assertEqual(s2["archived"], 0)


class TestToOkfRun(unittest.TestCase):
    """정규화 오케스트레이터가 실제 적재량을 정확히 보고하는지(중복 upsert 회귀 가드)."""

    def setUp(self):
        from scripts.normalize import to_okf
        self.to_okf = to_okf
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = tempfile.TemporaryDirectory()
        self._orig_dir = okf.EVENTS_DIR
        self._orig_logs = to_okf.SOURCES_LOG_DIR
        okf.EVENTS_DIR = Path(self.tmp.name)
        to_okf.SOURCES_LOG_DIR = Path(self.logs.name)

    def tearDown(self):
        okf.EVENTS_DIR = self._orig_dir
        self.to_okf.SOURCES_LOG_DIR = self._orig_logs
        self.tmp.cleanup()
        self.logs.cleanup()

    def test_run_reports_created_not_all_skipped(self):
        # 빈 DB에 오프라인 픽스처를 적재하면 created == collected(>0)여야 한다.
        # 중복 upsert 버그가 있으면 두 번째 호출이 모두 skip으로 덮어써 created=0이 된다.
        summary = self.to_okf.run(["kopis"], use_network=False)
        stats = summary["kopis"]
        self.assertNotIn("error", stats)
        self.assertGreater(stats["collected"], 0)
        self.assertEqual(stats["created"], stats["collected"])
        self.assertEqual(stats["skipped"], 0)



class TestRecommend(unittest.TestCase):
    def _events(self):
        return [
            {"id": "a", "name": "무료공연", "sido": "서울특별시", "themes": ["공연"],
             "age_bands": ["어린이"], "price": "free", "status": "Open",
             "start_date": "2026-07-18T00:00:00+09:00", "end_date": "2026-07-20T00:00:00+09:00",
             "lat": 37.57, "lng": 126.97},
            {"id": "b", "name": "유료전시", "sido": "부산광역시", "themes": ["전시"],
             "age_bands": [], "price": 20000, "status": "Scheduled",
             "start_date": "2026-07-18T00:00:00+09:00"},
        ]

    def test_free_only_excludes_paid(self):
        prof = {"regions": ["서울특별시", "부산광역시"], "themes": ["공연", "전시"],
                "age_band": "어린이", "prefs": {"free_only": True}}
        recs = rank.recommend(prof, self._events())
        self.assertEqual([r["id"] for r in recs], ["a"])

    def test_plan_respects_max_per_day(self):
        prof = {"regions": ["서울특별시"], "themes": ["공연"], "age_band": "어린이",
                "available_dates": ["2026-07-18"], "prefs": {"free_only": True, "max_per_day": 1}}
        cands = rank.recommend(prof, self._events())
        plan = rank.plan_week(prof, cands)
        self.assertEqual(len(plan["days"][0]["items"]), 1)

    def test_scored_event_to_record_preserves_contract(self):
        # ScoredEvent.to_record는 행사 필드 + _score(반올림)/_reasons 계약을 보존한다.
        se = rank.ScoredEvent(event={"id": "a", "name": "x"}, score=3.456,
                              reasons=["지역일치"])
        rec = se.to_record()
        self.assertEqual(rec["id"], "a")
        self.assertEqual(rec["_score"], 3.46)
        self.assertEqual(rec["_reasons"], ["지역일치"])

    def test_recommend_sorted_by_score_desc(self):
        prof = {"regions": ["서울특별시"], "themes": ["공연"], "age_band": "어린이",
                "prefs": {}}
        recs = rank.recommend(prof, self._events())
        scores = [r["_score"] for r in recs]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_haversine_zero(self):
        d = rank._haversine({"lat": 37.5, "lng": 127.0}, 37.5, 127.0)
        self.assertAlmostEqual(d, 0.0, places=4)


class TestNotify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = notify.SENT_PATH
        notify.SENT_PATH = Path(self.tmp.name) / "sent.json"

    def tearDown(self):
        notify.SENT_PATH = self._orig
        self.tmp.cleanup()

    def _events(self):
        return [{"id": "x", "name": "마감임박행사", "sido": "서울특별시", "themes": ["공연"],
                 "age_bands": ["어린이"], "status": "Open",
                 "application_start": "2026-06-25T09:00:00+09:00",
                 "application_end": "2026-07-10T18:00:00+09:00", "url": "https://e",
                 "fetched_at": "2026-06-20T00:00:00+09:00"}]

    def test_new_event_notification(self):
        events = [dict(self._events()[0], fetched_at="2026-07-09T09:00:00+09:00")]
        subs = [{"id": "s1", "filters": {"sido": "서울특별시"}, "channel": "stdout"}]
        now = dt.datetime(2026, 7, 9, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
        out = notify.compute_notifications(events, subs, now)
        # 마감 D-1 알림과 신규 행사 알림 두 개가 생성되어야 함
        self.assertEqual(len(out), 2)
        kinds = {o["kind"] for o in out}
        self.assertIn("deadline-D1", kinds)
        self.assertIn("new-event", kinds)

    def test_application_open_today(self):
        # 신청 시작일이 오늘 + status=Open 이면 application-open 트리거 발생.
        events = [dict(self._events()[0],
                       application_start="2026-07-09T09:00:00+09:00",
                       application_end="2026-08-01T18:00:00+09:00",
                       fetched_at="2026-06-20T00:00:00+09:00", status="Open")]
        subs = [{"id": "s1", "filters": {"sido": "서울특별시"}, "channel": "stdout"}]
        now = dt.datetime(2026, 7, 9, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
        kinds = {o["kind"] for o in notify.compute_notifications(events, subs, now)}
        self.assertIn("application-open", kinds)

    def test_application_open_requires_open_status(self):
        # status가 Open이 아니면 신청 시작일 당일이어도 application-open 미발생.
        events = [dict(self._events()[0],
                       application_start="2026-07-09T09:00:00+09:00",
                       application_end="2026-08-01T18:00:00+09:00",
                       fetched_at="2026-06-20T00:00:00+09:00", status="Closed")]
        subs = [{"id": "s1", "filters": {"sido": "서울특별시"}, "channel": "stdout"}]
        now = dt.datetime(2026, 7, 9, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
        kinds = {o["kind"] for o in notify.compute_notifications(events, subs, now)}
        self.assertNotIn("application-open", kinds)

    def test_dedupe_across_trigger_types(self):
        # 한 행사가 application-open + new-event + deadline-D1 세 트리거를 동시 충족 →
        # 서로 다른 dedupe_key 3건, 2회차 실행은 전부 억제.
        events = [dict(self._events()[0],
                       application_start="2026-07-09T09:00:00+09:00",
                       application_end="2026-07-10T18:00:00+09:00",
                       fetched_at="2026-07-09T09:00:00+09:00", status="Open")]
        subs = [{"id": "s1", "filters": {"sido": "서울특별시"}, "channel": "stdout"}]
        now = dt.datetime(2026, 7, 9, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
        out = notify.compute_notifications(events, subs, now)
        self.assertEqual({o["kind"] for o in out},
                         {"application-open", "new-event", "deadline-D1"})
        self.assertEqual(len({o["dedupe_key"] for o in out}), 3)
        r1 = notify.dispatch(events, subs, now)
        r2 = notify.dispatch(events, subs, now)
        self.assertEqual(r1["delivered"], 3)
        self.assertEqual(r2["suppressed"], 3)

    def test_new_event_24h_boundary(self):
        # new-event는 fetched_at이 정확히 24h(86400s) 전이면 발화, 그 직전(86401s)이면 미발화.
        subs = [{"id": "s1", "filters": {"sido": "서울특별시"}, "channel": "stdout"}]
        now = dt.datetime(2026, 7, 9, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
        far_future_end = "2026-09-01T18:00:00+09:00"  # 마감/오픈 트리거 격리
        at_boundary = (now - dt.timedelta(seconds=86400)).isoformat()
        events = [dict(self._events()[0], application_start="2026-01-01T00:00:00+09:00",
                       application_end=far_future_end, fetched_at=at_boundary, status="Open")]
        kinds = {o["kind"] for o in notify.compute_notifications(events, subs, now)}
        self.assertIn("new-event", kinds)
        over_boundary = (now - dt.timedelta(seconds=86401)).isoformat()
        events2 = [dict(events[0], fetched_at=over_boundary)]
        kinds2 = {o["kind"] for o in notify.compute_notifications(events2, subs, now)}
        self.assertNotIn("new-event", kinds2)

    def test_deadline_d1_and_filter_match(self):
        subs = [{"id": "s1", "filters": {"sido": "서울특별시"}, "channel": "stdout"}]
        now = dt.datetime(2026, 7, 9, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
        out = notify.compute_notifications(self._events(), subs, now)
        self.assertEqual(len(out), 1)
        self.assertIn("D-1", out[0]["title"])

    def test_filter_mismatch_no_notif(self):
        subs = [{"id": "s2", "filters": {"sido": "부산광역시"}, "channel": "stdout"}]
        now = dt.datetime(2026, 7, 9, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
        self.assertEqual(notify.compute_notifications(self._events(), subs, now), [])

    def test_dedupe_suppresses_second_run(self):
        subs = [{"id": "s1", "filters": {"sido": "서울특별시"}, "channel": "stdout"}]
        now = dt.datetime(2026, 7, 9, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
        r1 = notify.dispatch(self._events(), subs, now)
        r2 = notify.dispatch(self._events(), subs, now)
        self.assertEqual(r1["delivered"], 1)
        self.assertEqual(r2["suppressed"], 1)

    def test_dispatch_dryrun_without_token(self):
        # 토큰 없는 telegram 채널 → 무오류 dry-run, DRY 접두로 출력.
        import io
        from contextlib import redirect_stdout
        env = {k: v for k, v in os.environ.items() if k != "TELEGRAM_TOKEN"}
        old = dict(os.environ)
        os.environ.clear(); os.environ.update(env)
        try:
            subs = [{"id": "s1", "filters": {"sido": "서울특별시"}, "channel": "telegram",
                     "target": "12345"}]
            now = dt.datetime(2026, 7, 9, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
            buf = io.StringIO()
            with redirect_stdout(buf):
                summary = notify.dispatch(self._events(), subs, now)
        finally:
            os.environ.clear(); os.environ.update(old)
        self.assertEqual(summary["delivered"], 1)
        self.assertIn("DRY", buf.getvalue())
        self.assertNotIn("SENT", buf.getvalue())

    def test_telegram_payload_shape(self):
        notif = {"target": "999", "title": "[제목] 행사", "body": "본문"}
        payload = notify._telegram_payload(notif)
        self.assertEqual(payload["chat_id"], "999")
        self.assertEqual(payload["text"], "[제목] 행사\n본문")

    def test_webpush_payload_shape(self):
        notif = {"title": "[제목] 행사", "body": "본문", "event_id": "x", "kind": "deadline-D1"}
        payload = notify._webpush_payload(notif)
        self.assertEqual(payload["title"], "[제목] 행사")
        self.assertEqual(payload["data"], {"event_id": "x", "kind": "deadline-D1"})

    def test_webpush_dryrun_without_vapid(self):
        env = {k: v for k, v in os.environ.items() if k != "VAPID_PRIVATE_KEY"}
        old = dict(os.environ)
        os.environ.clear(); os.environ.update(env)
        try:
            self.assertFalse(notify._send_webpush(
                {"title": "t", "body": "b"}, {"endpoint": "https://push"}))
        finally:
            os.environ.clear(); os.environ.update(old)


class TestMacro(unittest.TestCase):
    def test_auto_site_has_submit(self):
        ev = {"id": "m1", "url": "https://mock.local/apply/x"}
        job = macro.plan_job(ev, {"name": "홍길동", "phone": "010"})
        self.assertEqual(job["mode"], "auto")
        self.assertTrue(any(s["action"] == "submit" for s in job["steps"]))
        self.assertTrue(macro.is_auto_submit(job))

    def test_tos_blocked_site_is_semi_no_autosubmit(self):
        ev = {"id": "m2", "url": "https://apply.example-city.go.kr/e"}
        job = macro.plan_job(ev, {"name": "홍길동", "phone": "010"})
        self.assertEqual(job["mode"], "semi")
        self.assertFalse(any(s["action"] == "submit" for s in job["steps"]))
        self.assertTrue(any(s["action"] == "pause" for s in job["steps"]))
        self.assertFalse(macro.is_auto_submit(job))  # 안전 보장

    def test_template_substitution(self):
        ev = {"id": "m3", "url": "https://mock.local/apply/z"}
        job = macro.plan_job(ev, {"name": "김철수", "phone": "010-1234"})
        fills = {s["selector"]: s["value"] for s in job["steps"] if s["action"] == "fill"}
        self.assertEqual(fills["#name"], "김철수")
        self.assertEqual(fills["#phone"], "010-1234")

    def test_unknown_site_manual(self):
        job = macro.plan_job({"id": "m4", "url": "https://unknown.example/x"}, {})
        self.assertEqual(job["mode"], "manual")



class TestWebSearch(unittest.TestCase):
    def test_offline_collect_maps_and_filters(self):
        rows = websearch.WebSearchAdapter(offline=True).collect()
        ids = {r["id"] for r in rows}
        # 3건 매핑(날짜+지역 충족), 저신뢰/무날짜 블로그 1건은 제외
        self.assertEqual(len(rows), 3)
        first = next(r for r in rows if r["location"]["sido"] == "서울특별시"
                     and "한강" in r["name"])
        self.assertEqual(first["source"], "websearch")
        self.assertEqual(first["x_verification"], "web-discovered")
        self.assertTrue(first["start_date"].startswith("2026-08-01"))
        self.assertTrue(first["content_hash"].startswith("sha256:"))
        # 신청기간 명시 → Open
        self.assertEqual(first["status"], "Open")

    def test_low_confidence_blog_excluded(self):
        rows = websearch.WebSearchAdapter(offline=True).collect()
        self.assertFalse(any("some-blog" in r.get("source_url", "") for r in rows))

    def test_trusted_domain_confidence_bump(self):
        ad = websearch.WebSearchAdapter(offline=True)
        plain = ad._confidence({"url": "https://x.example.com/a", "score": 0.5})
        trusted = ad._confidence({"url": "https://busan.go.kr/a", "score": 0.5})
        self.assertAlmostEqual(plain, 0.5, places=3)
        self.assertAlmostEqual(trusted, 0.6, places=3)

    def test_lookalike_domain_no_confidence_bump(self):
        # 라벨 경계가 아닌 유사 도메인(notgo.kr)은 신뢰 도메인(go.kr) 가점 미적용.
        ad = websearch.WebSearchAdapter(offline=True)
        lookalike = ad._confidence({"url": "https://notgo.kr/a", "score": 0.5})
        self.assertAlmostEqual(lookalike, 0.5, places=3)

    def test_sido_extracted_from_text_when_missing(self):
        hit = {"title": "경기도 수원 행사", "url": "https://e.go.kr/a",
               "snippet": "체험", "start_date": "2026-09-12", "score": 0.9}
        ev = websearch.WebSearchAdapter(offline=True).map_to_okf(hit)
        self.assertEqual(ev["location"]["sido"], "경기도")
        self.assertEqual(ev["themes"], ["체험"])

    def test_no_date_hit_skipped(self):
        hit = {"title": "서울특별시 무슨 글", "url": "https://e.go.kr/a", "score": 0.9}
        self.assertIsNone(websearch.WebSearchAdapter(offline=True).map_to_okf(hit))

    def test_parse_exa_shape(self):
        resp = {"results": [{"title": "T", "url": "https://u", "text": "S",
                             "publishedDate": "2026-06-01T00:00:00Z", "score": 0.8}]}
        hits = websearch.parse_exa(resp)
        self.assertEqual(hits[0]["provider"], "exa")
        self.assertEqual(hits[0]["snippet"], "S")
        self.assertAlmostEqual(hits[0]["score"], 0.8)

    def test_parse_brave_rank_score(self):
        resp = {"web": {"results": [
            {"title": "A", "url": "https://a", "description": "d1"},
            {"title": "B", "url": "https://b", "description": "d2"}]}}
        hits = websearch.parse_brave(resp)
        self.assertEqual(hits[0]["provider"], "brave")
        self.assertGreater(hits[0]["score"], hits[1]["score"])  # 상위가 더 높음

    def test_parse_tavily_shape(self):
        resp = {"results": [{"title": "T", "url": "https://u", "content": "C",
                             "score": 0.7, "published_date": "2026-05-01"}]}
        hits = websearch.parse_tavily(resp)
        self.assertEqual(hits[0]["snippet"], "C")
        self.assertEqual(hits[0]["published"], "2026-05-01")


class TestSqlite(unittest.TestCase):
    def _events(self):
        return [
            {"id": "a", "name": "무료 음악축제", "description": "한강 공연",
             "themes": ["축제", "공연"], "start_date": "2026-08-01T00:00:00+09:00",
             "status": "Open", "sido": "서울특별시", "event_type": "Festival",
             "age_bands": ["어린이"], "price": "free"},
            {"id": "b", "name": "부산 전시회", "description": "현대미술",
             "themes": ["전시"], "start_date": "2026-09-01T00:00:00+09:00",
             "status": "Scheduled", "sido": "부산광역시", "event_type": "ExhibitionEvent",
             "age_bands": [], "price": 10000},
        ]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "events.db"
        build_sqlite.build(self.db, events=self._events())

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_counts(self):
        rows = build_sqlite.search(self.db)
        self.assertEqual(len(rows), 2)

    def test_fts_korean_match(self):
        hits = build_sqlite.search(self.db, text="축제")
        self.assertEqual([h["id"] for h in hits], ["a"])

    def test_fts_korean_partial_match_improved(self):
        # unicode61은 "음악"으로 "음악축제"를 못 찾지만 N-gram 색인은 부분일치를 찾는다.
        hits = build_sqlite.search(self.db, text="음악")
        self.assertEqual([h["id"] for h in hits], ["a"])
        # 부분일치는 다른 행사로 새어나가지 않는다(정밀도 회귀 가드).
        self.assertEqual(build_sqlite.search(self.db, text="현대미술")[0]["id"], "b")
        self.assertEqual(build_sqlite.search(self.db, text="음악", sido="부산광역시"), [])

    def test_ngrams_helper_bigrams(self):
        self.assertEqual(build_sqlite._ngrams("음악축제"), ["음악", "악축", "축제"])
        self.assertEqual(build_sqlite._ngrams("a"), ["a"])  # n보다 짧은 어절은 그대로


    def test_filter_sido_and_theme(self):
        self.assertEqual([h["id"] for h in build_sqlite.search(self.db, sido="부산광역시")],
                         ["b"])
        self.assertEqual([h["id"] for h in build_sqlite.search(self.db, theme="공연")],
                         ["a"])

    def test_filter_status_and_themes_decoded(self):
        hits = build_sqlite.search(self.db, status="Open")
        self.assertEqual(len(hits), 1)
        self.assertIn("축제", hits[0]["themes"])  # JSON 디코딩 확인


class TestAiPlanner(unittest.TestCase):
    def _events(self):
        return [
            {"id": "a", "name": "무료공연", "sido": "서울특별시", "themes": ["공연"],
             "age_bands": ["어린이"], "price": "free", "status": "Open",
             "start_date": "2026-07-18T00:00:00+09:00",
             "end_date": "2026-07-20T00:00:00+09:00", "lat": 37.57, "lng": 126.97},
        ]

    def _profile(self):
        return {"regions": ["서울특별시"], "themes": ["공연"], "age_band": "어린이",
                "available_dates": ["2026-07-18"],
                "prefs": {"free_only": True, "max_per_day": 2}}

    def test_build_request_has_schema_and_ids(self):
        cands = rank.recommend(self._profile(), self._events())
        req = ai_planner.build_request(self._profile(), cands)
        self.assertEqual(req["generationConfig"]["responseMimeType"], "application/json")
        self.assertIn("responseSchema", req["generationConfig"])
        self.assertIn("a", json.dumps(req, ensure_ascii=False))

    def test_parse_plan_valid(self):
        text = json.dumps({"week_of": "2026-07-18", "days": [
            {"date": "2026-07-18", "items": [{"event_id": "a", "reason": "무료"}]}]})
        resp = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        plan = ai_planner.parse_plan(resp)
        self.assertEqual(plan["days"][0]["items"][0]["event_id"], "a")

    def test_parse_plan_rejects_bad_shape(self):
        text = json.dumps({"week_of": "2026-07-18"})  # days 누락
        resp = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        with self.assertRaises(ValueError):
            ai_planner.parse_plan(resp)

    def test_validate_plan_requires_event_id(self):
        with self.assertRaises(ValueError):
            ai_planner.validate_plan({"week_of": "w", "days": [
                {"date": "d", "items": [{"reason": "x"}]}]})

    def test_constrain_drops_hallucinated_ids(self):
        plan = {"week_of": "w", "days": [{"date": "d", "items": [
            {"event_id": "a"}, {"event_id": "ghost"}]}]}
        out = ai_planner._constrain_to_candidates(plan, [{"id": "a"}])
        self.assertEqual([i["event_id"] for i in out["days"][0]["items"]], ["a"])

    def test_constrain_filters_unavailable_dates(self):
        plan = {"week_of": "w", "days": [
            {"date": "2026-07-18", "items": [{"event_id": "a"}]},
            {"date": "2026-07-19", "items": [{"event_id": "a"}]}
        ]}
        profile = {"available_dates": ["2026-07-18"]}
        out = ai_planner._constrain_to_candidates(plan, [{"id": "a"}], profile)
        self.assertEqual(len(out["days"][0]["items"]), 1)
        self.assertEqual(len(out["days"][1]["items"]), 0)

    def test_plan_falls_back_without_key(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("GOOGLE_AI_STUDIO_KEY", "GEMINI_KEY")}
        old = dict(os.environ)
        os.environ.clear(); os.environ.update(env)
        try:
            result = ai_planner.plan(self._profile(), self._events())
        finally:
            os.environ.clear(); os.environ.update(old)
        self.assertEqual(result["engine"], "rule-based-fallback")
        self.assertEqual(result["days"][0]["items"][0]["event_id"], "a")


class TestGeocode(unittest.TestCase):
    def test_centroid_table_covers_17_sidos(self):
        # 폴백 centroid 테이블이 17개 시/도 전부를 커버해야 한다(좌표 결측 제거 보장).
        cov = geocode.coverage([])
        self.assertEqual(cov["centroid_table_size"], 17)

    def test_sido_centroid_lookup_and_alias(self):
        lat, lng = geocode.sido_centroid("서울특별시")
        self.assertAlmostEqual(lat, 37.5665, places=3)
        # 별칭도 정규화되어 좌표를 찾는다.
        self.assertEqual(geocode.sido_centroid("서울"), geocode.sido_centroid("서울특별시"))
        self.assertEqual(geocode.sido_centroid("없는도"), (None, None))

    def test_enrich_fills_missing_with_centroid(self):
        ev = {"id": "g1", "location": {"sido": "제주특별자치도"}}
        out = geocode.geocode_event(dict(ev), cache={})
        self.assertAlmostEqual(out["location"]["lat"], 33.4890, places=3)
        self.assertEqual(out["location"]["geo_precision"], "sido-centroid")

    def test_existing_coords_preserved(self):
        ev = {"id": "g2", "location": {"sido": "서울특별시", "lat": 37.1, "lng": 127.1}}
        out = geocode.geocode_event(dict(ev), cache={})
        self.assertEqual(out["location"]["lat"], 37.1)
        self.assertNotIn("geo_precision", out["location"])  # 변경 안 함

    def test_cache_hit_uses_precise_coords(self):
        ev = {"id": "g3", "name": "세종문화회관", "location": {"sido": "서울특별시"}}
        cache = {"세종문화회관": {"lat": 37.5725, "lng": 126.9760}}
        out = geocode.geocode_event(dict(ev), cache=cache)
        self.assertEqual(out["location"]["lat"], 37.5725)
        self.assertEqual(out["location"]["geo_precision"], "address-cache")

    def test_coverage_missing_rate_zero_after_enrich(self):
        events = [{"id": str(i), "location": {"sido": s}}
                  for i, s in enumerate(["서울특별시", "부산광역시", "경기도"])]
        enriched = geocode.enrich(events)
        cov = geocode.coverage(enriched)
        self.assertEqual(cov["missing_rate"], 0.0)
        self.assertLess(cov["missing_rate"], 0.15)

    def test_vworld_no_key_returns_none(self):
        env = {k: v for k, v in os.environ.items() if k != "VWORLD_KEY"}
        old = dict(os.environ)
        os.environ.clear(); os.environ.update(env)
        try:
            self.assertIsNone(geocode.vworld_geocode("서울특별시 세종대로 110"))
        finally:
            os.environ.clear(); os.environ.update(old)

class TestCommonHttp(unittest.TestCase):
    class _Resp:
        def __init__(self, status_code, text=""):
            self.status_code = status_code
            self.text = text

    class _Boom(Exception):
        pass

    def test_should_retry_status(self):
        for s in (429, 500, 502, 503, 504, 599):
            self.assertTrue(common_http.should_retry_status(s), s)
        for s in (200, 301, 400, 401, 404, 422):
            self.assertFalse(common_http.should_retry_status(s), s)

    def test_returns_first_success_without_sleeping(self):
        slept = []
        calls = []

        def send():
            calls.append(1)
            return self._Resp(200)

        resp = common_http.request_with_retry(send, sleep=slept.append)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(calls), 1)  # 재시도 없음
        self.assertEqual(slept, [])

    def test_retries_then_succeeds_on_5xx(self):
        slept = []
        seq = [self._Resp(503), self._Resp(503), self._Resp(200)]

        resp = common_http.request_with_retry(
            lambda: seq.pop(0), retries=2, backoff=0.5, sleep=slept.append)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(slept, [0.5, 1.0])  # 지수 백오프

    def test_gives_up_after_retries_raises_http_error(self):
        slept = []
        with self.assertRaises(common_http.HttpError) as cm:
            common_http.request_with_retry(
                lambda: self._Resp(503, "down"), retries=1, sleep=slept.append)
        self.assertEqual(cm.exception.status, 503)
        self.assertEqual(len(slept), 1)  # retries=1 → 1번만 대기

    def test_4xx_fails_immediately_no_retry(self):
        slept = []
        with self.assertRaises(common_http.HttpError) as cm:
            common_http.request_with_retry(
                lambda: self._Resp(404, "nope"), retries=3, sleep=slept.append)
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(slept, [])  # 4xx는 재시도 안 함

    def test_retries_on_injected_exception_then_reraises(self):
        slept = []
        calls = []

        def send():
            calls.append(1)
            raise self._Boom("transient")

        with self.assertRaises(self._Boom):
            common_http.request_with_retry(
                send, retries=2, retry_exceptions=(self._Boom,), sleep=slept.append)
        self.assertEqual(len(calls), 3)  # 최초 + 2 재시도
        self.assertEqual(slept, [0.5, 1.0])

    def test_non_listed_exception_propagates_immediately(self):
        calls = []

        def send():
            calls.append(1)
            raise ValueError("not retryable")

        with self.assertRaises(ValueError):
            common_http.request_with_retry(
                send, retries=3, retry_exceptions=(self._Boom,))
        self.assertEqual(len(calls), 1)


class TestDisplayStatus(unittest.TestCase):
    TODAY = "2026-07-09"

    def _ev(self, **over):
        e = {"status": "Scheduled", "application_start": "2026-07-01",
             "application_end": "2026-07-31"}
        e.update(over)
        return e

    def test_closed_status_is_margam(self):
        self.assertEqual(build_index.derive_status(self._ev(status="Closed"), self.TODAY), "마감")

    def test_past_application_end_is_margam(self):
        self.assertEqual(
            build_index.derive_status(self._ev(application_end="2026-07-01"), self.TODAY), "마감")

    def test_future_application_start_is_before(self):
        self.assertEqual(
            build_index.derive_status(self._ev(application_start="2026-08-01"), self.TODAY), "신청전")

    def test_open_window_is_open(self):
        self.assertEqual(
            build_index.derive_status(self._ev(status="Open", application_end="2026-07-31"),
                                      self.TODAY), "오픈")

    def test_imminent_within_three_days(self):
        # 마감 D-3 이내(경계 포함)는 마감임박.
        self.assertEqual(
            build_index.derive_status(self._ev(status="Open", application_end="2026-07-11"),
                                      self.TODAY), "마감임박")
        self.assertEqual(
            build_index.derive_status(self._ev(status="Open", application_end="2026-07-09"),
                                      self.TODAY), "마감임박")

    def test_open_inferred_from_window_without_open_status(self):
        # status가 Open이 아니어도 신청기간 안이면 오픈으로 파생.
        self.assertEqual(
            build_index.derive_status(self._ev(status="Scheduled"), self.TODAY), "오픈")

    def test_deadline_only_is_open(self):
        # 마감일만 있는(신청 시작일 부재) 정부지원형도 기간 내면 오픈.
        self.assertEqual(
            build_index.derive_status(
                self._ev(application_start=None, application_end="2026-07-31"),
                self.TODAY), "오픈")

    def test_deadline_only_imminent(self):
        # 마감일만 있는 행사도 마감 D-3 이내면 마감임박(핵심 FOMO 배지).
        self.assertEqual(
            build_index.derive_status(
                self._ev(application_start=None, application_end="2026-07-11"),
                self.TODAY), "마감임박")

    def test_deadline_only_past_is_margam(self):
        self.assertEqual(
            build_index.derive_status(
                self._ev(application_start=None, application_end="2026-07-01"),
                self.TODAY), "마감")

    def test_no_dates_non_open_stays_before(self):
        # 날짜 정보가 전혀 없고 status도 Open이 아니면 오픈으로 오판하지 않는다.
        self.assertEqual(
            build_index.derive_status(
                self._ev(application_start=None, application_end=None, status="Scheduled"),
                self.TODAY), "신청전")


class TestBuildPages(unittest.TestCase):
    """S2-T1: 행사별 정적 상세 HTML(JSON-LD) 빌드 — 멱등 + schema.org 임베드."""

    EVENTS = [
        {"id": "kopis:PF1", "name": "여름 음악축제", "start_date": "2026-07-18",
         "end_date": "2026-07-20", "sido": "서울", "sigungu": "종로구",
         "url": "https://ex.com/pf1", "status": "Open", "price": "free",
         "lat": 37.57, "lng": 126.98, "organizer": "서울문화재단",
         "description": "한여름 밤의 음악 <축제>"},
        {"id": "tour:F2", "name": "강릉 등불 전시", "start_date": "2026-08-01",
         "sido": "강원", "url": "https://ex.com/f2", "status": "Scheduled"},
    ]

    def test_writes_one_page_per_event_with_jsonld(self):
        with tempfile.TemporaryDirectory() as d:
            res = build_pages.build(out_dir=d, events=self.EVENTS)
            self.assertEqual(res["pages"], 2)
            html = (Path(d) / f"{okf._safe_id('kopis:PF1')}.html").read_text(encoding="utf-8")
            self.assertIn('<script type="application/ld+json">', html)
            self.assertIn('"@type": "Event"', html)
            self.assertIn('"name": "여름 음악축제"', html)
            # schema.org status URL + 무료 Offer price 0.
            self.assertIn("https://schema.org/EventScheduled", html)
            self.assertIn('"price": "0"', html)
            # 사용자 텍스트의 HTML 특수문자는 이스케이프(XSS/깨짐 방지).
            self.assertIn("&lt;축제&gt;", html)

    def test_jsonld_is_valid_json(self):
        ld = build_pages.event_jsonld(self.EVENTS[0])
        # 직렬화/역직렬화 왕복이 동일 — 유효한 JSON-LD.
        round_trip = json.loads(json.dumps(ld, ensure_ascii=False))
        self.assertEqual(round_trip["@context"], "https://schema.org")
        self.assertEqual(round_trip["location"]["geo"]["latitude"], 37.57)

    def test_idempotent_same_input_same_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            build_pages.build(out_dir=d, events=self.EVENTS)
            first = (Path(d) / f"{okf._safe_id('kopis:PF1')}.html").read_bytes()
            build_pages.build(out_dir=d, events=self.EVENTS)
            second = (Path(d) / f"{okf._safe_id('kopis:PF1')}.html").read_bytes()
            self.assertEqual(first, second)

    def test_removes_stale_pages(self):
        with tempfile.TemporaryDirectory() as d:
            build_pages.build(out_dir=d, events=self.EVENTS)
            self.assertEqual(len(list(Path(d).glob("*.html"))), 2)
            # 행사가 사라지면 묵은 페이지도 제거(파생물 정합).
            build_pages.build(out_dir=d, events=self.EVENTS[:1])
            self.assertEqual(len(list(Path(d).glob("*.html"))), 1)
class TestUsageReport(unittest.TestCase):
    """S4-T3: 무료티어 사용률 추정 — 페이징/검색 호출량 + 한도 상태(ok/warn/over)."""

    SEARCH = {
        "default_provider": "exa",
        "queries": ["q1", "q2", "q3"],
        "providers": {"exa": {"free_tier": "월 1,000 검색(무료 크레딧)"}},
    }

    def test_paging_source_calls_scale_with_collected(self):
        # 250건 수집 → 100건/페이지 ⇒ 3 호출/run.
        r = usage_report.estimate_source(
            {"key": "kopis", "rate_limit_per_day": 5000}, collected=250)
        self.assertEqual(r["calls_per_run"], 3)
        self.assertEqual(r["daily_estimate"], 3)
        self.assertEqual(r["status"], "ok")

    def test_zero_collected_still_counts_one_call(self):
        r = usage_report.estimate_source(
            {"key": "tourapi", "rate_limit_per_day": 10000}, collected=0)
        self.assertEqual(r["calls_per_run"], 1)

    def test_websearch_uses_query_count_and_monthly_free(self):
        r = usage_report.estimate_source(
            {"key": "websearch", "provider": "exa", "rate_limit_per_day": 1000},
            collected=9, search_cfg=self.SEARCH)
        self.assertEqual(r["calls_per_run"], 3)           # 질의 3개
        self.assertEqual(r["monthly_free"], 1000)          # '월 1,000' 파싱
        self.assertEqual(r["monthly_estimate"], 90)        # 3*30
        self.assertEqual(r["status"], "ok")

    def test_over_limit_flags_status_over(self):
        # 일 한도 2인데 5000건(50 호출) ⇒ over.
        r = usage_report.estimate_source(
            {"key": "kopis", "rate_limit_per_day": 2}, collected=5000)
        self.assertEqual(r["status"], "over")

    def test_warn_band_between_80_and_100_percent(self):
        # 90 호출 / 일한도 100 = 0.9 ⇒ warn.
        r = usage_report.estimate_source(
            {"key": "kopis", "rate_limit_per_day": 100}, collected=9000)
        self.assertEqual(r["calls_per_run"], 90)
        self.assertEqual(r["status"], "warn")

    def test_build_report_skips_disabled_and_sorts(self):
        sources = [
            {"key": "tourapi", "enabled": True, "rate_limit_per_day": 10000},
            {"key": "kopis", "enabled": True, "rate_limit_per_day": 5000},
            {"key": "datagokr", "enabled": False, "rate_limit_per_day": 10000},
        ]
        rep = usage_report.build_report(sources, {"kopis": 100, "tourapi": 50})
        self.assertEqual([r["source"] for r in rep["rows"]], ["kopis", "tourapi"])
        self.assertTrue(rep["within_free_tier"])

    def test_build_report_within_free_tier_false_on_over(self):
        sources = [{"key": "kopis", "enabled": True, "rate_limit_per_day": 1}]
        rep = usage_report.build_report(sources, {"kopis": 1000})
        self.assertEqual(rep["over"], ["kopis"])
        self.assertFalse(rep["within_free_tier"])

    def test_load_runs_picks_latest_snapshot_per_source(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "kopis-2026-06-25.md").write_text(
                "---\nsource: kopis\ndate: 2026-06-25\ncollected: 3\n---\n", encoding="utf-8")
            (base / "kopis-2026-06-26.md").write_text(
                "---\nsource: kopis\ndate: 2026-06-26\ncollected: 7\n---\n", encoding="utf-8")
            runs = usage_report.load_runs(base)
            self.assertEqual(runs, {"kopis": 7})   # 최신 날짜 우선
            self.assertEqual(runs, {"kopis": 7})   # 최신 날짜 우선


class TestWikiIndex(unittest.TestCase):
    """SOURCES 블록의 '최근 갱신'은 fetched_at에서 파생 → SSOT 재생성 멱등."""

    TEMPLATE = (
        "# index\n"
        "<!-- REGIONS:START -->\nx\n<!-- REGIONS:END -->\n"
        "<!-- THEMES:START -->\nx\n<!-- THEMES:END -->\n"
        "<!-- SOURCES:START -->\nx\n<!-- SOURCES:END -->\n"
    )

    EVENTS = [
        (None, {"status": "Scheduled", "source": "kopis", "location": {"sido": "서울특별시"},
                "themes": ["공연"], "fetched_at": "2026-06-20T00:00:00+09:00"}, ""),
        (None, {"status": "Scheduled", "source": "kopis", "location": {"sido": "부산광역시"},
                "themes": ["공연"], "fetched_at": "2026-06-25T00:00:00+09:00"}, ""),
    ]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.index = Path(self.tmp.name) / "index.md"
        self.index.write_text(self.TEMPLATE, encoding="utf-8")
        self._orig_index = wiki_index.INDEX
        self._orig_iter = wiki_index.iter_events
        wiki_index.INDEX = self.index
        wiki_index.iter_events = lambda: iter(self.EVENTS)

    def tearDown(self):
        wiki_index.INDEX = self._orig_index
        wiki_index.iter_events = self._orig_iter
        self.tmp.cleanup()

    def test_source_last_seen_from_max_fetched_at(self):
        wiki_index.build()
        text = self.index.read_text(encoding="utf-8")
        # 소스의 최신 fetched_at(2026-06-25)이 노출되어야 한다(오늘 날짜가 아니라).
        self.assertIn("kopis — 2건 (최근 갱신 2026-06-25)", text)

    def test_regeneration_is_idempotent(self):
        wiki_index.build()
        first = self.index.read_text(encoding="utf-8")
        wiki_index.build()
        second = self.index.read_text(encoding="utf-8")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
