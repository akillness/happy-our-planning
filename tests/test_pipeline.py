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
from scripts.recommend import ai_planner
from scripts.normalize import geocode


class TestConfig(unittest.TestCase):
    def test_canonical_sido_alias_and_partial(self):
        self.assertEqual(config.canonical_sido("부산"), "부산광역시")
        self.assertEqual(config.canonical_sido("경기"), "경기도")
        self.assertEqual(config.canonical_sido("서울특별시"), "서울특별시")

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
                 "application_end": "2026-07-10T18:00:00+09:00", "url": "https://e"}]

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


if __name__ == "__main__":
    unittest.main()
