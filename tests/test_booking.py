"""Booking/detail contracts for AC-F2.11 and AC-F2.12.

These tests use only the existing collector, index, static-page, and link-check
APIs.  Network/provider behavior is represented by the same bounded transport
fake used by ``tests.test_links``.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator, FormatChecker
from scripts.build import build_index, build_pages
from scripts.common import links
from scripts.ingest.kopis import KopisAdapter
from scripts.normalize import to_okf
from scripts.ops import check_links

UTC = timezone.utc
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _cache(url: str, *, status: str = "verified", checked_at: str | None = None,
           final_url: str | None = None, reason: str | None = None) -> dict:
    return {
        _key(url): {
            "link_status": status,
            "link_checked_at": checked_at or NOW.isoformat(),
            "link_url": final_url if final_url is not None else (url if status == "verified" else None),
            "link_reason": reason,
        }
    }


class _Transport:
    def __init__(self, responses: dict[str, dict] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple] = []

    def resolver(self, host: str, port: int) -> list[str]:
        self.calls.append(("resolve", host, port))
        return ["93.184.216.34"]

    def send(self, url: str, method: str, ip: str, host: str, timeout: float):
        self.calls.append((method, url, ip, host, timeout))
        response = self.responses.get(url, {"status": 200})
        headers = {}
        if response.get("final_url") and response["status"] in {301, 302, 303, 307, 308}:
            headers["location"] = response["final_url"]
        return links.LinkResponse(response["status"], headers=headers, body=response.get("body", b""))


def _detail_event(**overrides) -> dict:
    event = {
        "id": "kopis:PF-DETAIL-1",
        "name": "어린이 과학 음악회",
        "description": "원문 설명: 악기 체험과 공연이 함께 열립니다.",
        "start_date": "2026-11-01T00:00:00+09:00",
        "end_date": "2026-11-01T00:00:00+09:00",
        "application_start": "2026-10-01T00:00:00+09:00",
        "application_end": "2026-10-31T23:59:59+09:00",
        "age": "7-13",
        "price": 30000,
        "price_text": "전석 30,000원",
        "duration_text": "90분",
        "duration": "PT90M",
        "location": {
            "name": "세종문화회관",
            "sido": "서울특별시",
            "sigungu": "종로구",
            "address": "서울특별시 종로구 세종대로 175",
        },
        "url": "https://www.kopis.or.kr/por/db/pblprfr/pblprfrView.do?mt20id=PF-DETAIL-1",
        "offers": {"url": "https://ticket.interpark.com/goods/21012345"},
        "source": "kopis",
    }
    event.update(overrides)
    return event

class TestEventStatusTruthfulness(unittest.TestCase):
    def test_status_uses_event_and_application_dates_not_kopis_open_label(self):
        cases = (
            (
                "future performance without application window",
                {"status": "Scheduled", "start_date": "2026-11-01", "end_date": "2026-11-02"},
                "예정",
            ),
            (
                "running performance without application window",
                {"status": "Open", "start_date": "2026-10-01", "end_date": "2026-10-31"},
                "진행중",
            ),
            ("missing dates", {"status": "Scheduled"}, "정보 없음"),
            (
                "explicit future application start",
                {
                    "start_date": "2026-11-01",
                    "end_date": "2026-11-02",
                    "application_start": "2026-10-20",
                    "application_end": "2026-10-30",
                },
                "신청전",
            ),
            (
                "elapsed application before future performance",
                {
                    "status": "Open",
                    "start_date": "2026-11-01",
                    "end_date": "2026-11-02",
                    "application_start": "2026-09-01",
                    "application_end": "2026-09-30",
                },
                "마감",
            ),
        )
        for name, event, expected in cases:
            with self.subTest(name=name):
                actual = build_index.derive_status(event, "2026-10-15")
                self.assertEqual(actual, expected)
                if name == "elapsed application before future performance":
                    self.assertNotEqual(actual, "종료")
    def test_start_only_yesterday_is_ended_and_rendered_as_ended(self):
        event = _detail_event(
            status="Scheduled",
            start_date="2026-10-14",
            end_date=None,
            application_start="2026-10-01",
            application_end="2026-10-13",
        )

        self.assertEqual(build_index.derive_status(event, "2026-10-15"), "종료")

        html = build_pages.render_page(event, checks={}, now=NOW)
        self.assertIn('class="event-date ended"', html)
        self.assertIn("<del>2026-10-14</del>", html)
        self.assertIn('class="status-badge ended"', html)
        self.assertIn('data-status-key="status.ended"', html)
        self.assertIn('class="status-value"', html)

        upcoming = _detail_event(
            status="Open",
            start_date="2026-11-01",
            end_date="2026-11-02",
            application_start=None,
            application_end=None,
        )
        checks = {**_cache(upcoming["url"]), **_cache(upcoming["offers"]["url"])}
        flattened = build_index._flatten(upcoming, link_checks=checks, now=NOW)
        html = build_pages.render_page(flattened, checks=checks, now=NOW)
        self.assertIn("예정", html)



class TestDetailMetadataPublication(unittest.TestCase):
    def test_detail_stats_survive_run_and_are_integer_whitelisted(self):
        class _MetadataAdapter:
            offline = True
            collection_mode = "fixture"
            snapshot_complete = False
            complete = True
            truncated = False

            def __init__(self):
                self.collection_metadata = {
                    "detail": {
                        "candidates": 2,
                        "cached": 0,
                        "fetched": 1,
                        "failed": 1,
                        "capped": 0,
                        "metadata_url": "https://secret.example/should-not-publish",
                        "arbitrary": {"leak": True},
                    }
                }

            def collect(self):
                return [{"id": "fixture:one"}, {"id": "fixture:two"}]

        expected_detail = {
            "candidates": 2,
            "cached": 0,
            "fetched": 1,
            "failed": 1,
            "capped": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = to_okf.SOURCES_LOG_DIR
            old_status = to_okf.COLLECTION_STATUS_PATH
            to_okf.SOURCES_LOG_DIR = Path(tmp)
            to_okf.COLLECTION_STATUS_PATH = Path(tmp) / "collection-status.json"
            try:
                with mock.patch.dict(to_okf.ADAPTERS, {"fixture": _MetadataAdapter}, clear=False), \
                     mock.patch.object(to_okf, "sources", return_value=[{"key": "fixture", "enabled": True}]), \
                     mock.patch.object(to_okf.geocode, "enrich", side_effect=lambda rows, **_: rows), \
                     mock.patch.object(to_okf, "upsert", return_value={"created": 2, "updated": 0, "skipped": 0, "archived": 0}) as upsert:
                    summary = to_okf.run(["fixture"], use_network=False)
            finally:
                to_okf.SOURCES_LOG_DIR = old_dir
                to_okf.COLLECTION_STATUS_PATH = old_status

            upsert.assert_called_once()
            self.assertEqual(len(upsert.call_args.args[0]), 2)
            self.assertEqual(summary["fixture"]["detail"], expected_detail)
            sidecar = json.loads((Path(tmp) / "collection-status.json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["sources"]["fixture"]["detail"], expected_detail)
            serialized = json.dumps({"summary": summary, "sidecar": sidecar}, ensure_ascii=False)
            self.assertNotIn("metadata_url", serialized)
            self.assertNotIn("arbitrary", serialized)

    def test_missing_detail_metadata_does_not_publish_a_detail_key(self):
        class _NoDetailAdapter:
            offline = True
            collection_mode = "fixture"
            complete = True
            truncated = False

            def __init__(self):
                self.collection_metadata = {
                    "window_start": "20261015",
                    "venue_geo": {"cached": 1},
                }

            def collect(self):
                return [{"id": "fixture:one"}]

        with tempfile.TemporaryDirectory() as tmp:
            old_dir = to_okf.SOURCES_LOG_DIR
            old_status = to_okf.COLLECTION_STATUS_PATH
            to_okf.SOURCES_LOG_DIR = Path(tmp)
            to_okf.COLLECTION_STATUS_PATH = Path(tmp) / "collection-status.json"
            try:
                with mock.patch.dict(to_okf.ADAPTERS, {"fixture": _NoDetailAdapter}, clear=False), \
                     mock.patch.object(to_okf, "sources", return_value=[{"key": "fixture", "enabled": True}]), \
                     mock.patch.object(to_okf.geocode, "enrich", side_effect=lambda rows, **_: rows), \
                     mock.patch.object(to_okf, "upsert", return_value={"created": 1, "updated": 0, "skipped": 0, "archived": 0}):
                    summary = to_okf.run(["fixture"], use_network=False)
            finally:
                to_okf.SOURCES_LOG_DIR = old_dir
                to_okf.COLLECTION_STATUS_PATH = old_status

            self.assertNotIn("detail", summary["fixture"])
            sidecar = json.loads((Path(tmp) / "collection-status.json").read_text(encoding="utf-8"))
            self.assertNotIn("detail", sidecar["sources"]["fixture"])
            self.assertNotIn("window_start", json.dumps(summary, ensure_ascii=False))
            self.assertNotIn("venue_geo", json.dumps(sidecar, ensure_ascii=False))


class TestKopisBookingNormalization(unittest.TestCase):
    def test_stale_detail_cache_refreshes_before_per_run_cap(self):
        """Expired detail cache is retained when the refresh budget is exhausted."""
        rows = [
            {
                "mt20id": "PF-STALE",
                "prfnm": "과학 가족 공연",
                "prfstate": "공연예정",
                "prfpdfrom": "2026.11.01",
                "prfpdto": "2026.11.02",
                "fcltynm": "세종문화회관",
                "genrenm": "연극",
                "area": "서울",
            },
            {
                "mt20id": "PF-STALE-2",
                "prfnm": "음악회",
                "prfstate": "공연예정",
                "prfpdfrom": "2026.11.03",
                "prfpdto": "2026.11.04",
                "fcltynm": "예술의전당",
                "genrenm": "서양음악(클래식)",
                "area": "서울",
            },
            {"mt20id": "PF-CAPPED", "prfnm": "전시", "prfstate": "공연예정"},
        ]
        stale_at = (NOW - timedelta(days=2)).isoformat()
        cached = {
            "PF-STALE": {"sty": "오래된 설명", "fetched_at": stale_at},
            "PF-STALE-2": {
                "sty": "기존 설명",
                "prfage": "7-13",
                "pcseguidance": "전석 30,000원",
                "prfruntime": "90분",
                "relatenm": "티켓링크",
                "relateurl": "https://tickets.example.test/event/stale-2",
                "fetched_at": stale_at,
            },
        }
        detail_xml = """<dbs><db><mt20id>PF-STALE</mt20id><sty>새 설명</sty></db></dbs>"""
        requested: list[str] = []

        class _Response:
            def __init__(self, text: str):
                self.text = text

        adapter = KopisAdapter(offline=True, clock=lambda: NOW)
        adapter.collection_metadata = {}

        def fake_request(url, params, sender):
            requested.append(url)
            return _Response(detail_xml)

        with mock.patch("scripts.ingest.kopis._load_detail_cache", return_value=cached), \
             mock.patch("scripts.ingest.kopis._save_detail_cache") as save_cache, \
             mock.patch.object(adapter, "_request", side_effect=fake_request), \
             mock.patch.object(
                 adapter,
                 "_setting",
                 side_effect=lambda name, default: {
                     "detail_max_calls_per_run": 1,
                     "detail_cache_ttl_hours": 24.0,
                 }.get(name, default),
             ):
            stats = adapter._attach_details(rows)

        self.assertEqual(
            stats,
            {"candidates": 3, "cached": 0, "fetched": 1, "failed": 0, "capped": 2, "stale": 1},
        )
        self.assertEqual(requested, ["https://www.kopis.or.kr/openApi/restful/pblprfr/PF-STALE"])
        self.assertEqual(rows[0]["sty"], "새 설명")
        self.assertEqual(rows[1]["sty"], "기존 설명")
        self.assertEqual(rows[1]["relateurl"], "https://tickets.example.test/event/stale-2")
        self.assertNotIn("sty", rows[2])
        save_cache.assert_called_once()

        event = adapter.map_to_okf(rows[1])
        self.assertEqual(event["description"], "기존 설명")
        self.assertEqual(event["age"], "7-13")
        self.assertEqual(event["price_text"], "전석 30,000원")
        self.assertEqual(event["duration_text"], "90분")
        self.assertEqual(event["offers"], {"url": "https://tickets.example.test/event/stale-2"})

        from scripts.common import okf

        with tempfile.TemporaryDirectory() as tmp:
            old_events_dir = okf.EVENTS_DIR
            okf.EVENTS_DIR = Path(tmp)
            try:
                upsert_stats = to_okf.upsert([event])
                stored = next(Path(tmp).rglob("*.md"))
                body = stored.read_text(encoding="utf-8")
            finally:
                okf.EVENTS_DIR = old_events_dir
        self.assertEqual(upsert_stats["created"], 1)
        self.assertIn("기존 설명", body)
        self.assertIn("https://tickets.example.test/event/stale-2", body)


class TestBookingLinkSeparation(unittest.TestCase):
    def test_jsonld_never_reuses_verified_info_url_as_booking_url(self):
        event = _detail_event()
        checks = {**_cache(event["url"]), **_cache(event["offers"]["url"])}
        jsonld = build_pages.event_jsonld(event, checks=checks, now=NOW)

        self.assertEqual(jsonld["url"], event["url"])
        self.assertEqual(jsonld["offers"]["url"], event["offers"]["url"])
        self.assertNotEqual(jsonld["offers"]["url"], jsonld["url"])

    def test_missing_provider_booking_url_is_not_borrowed_from_info_url(self):
        event = _detail_event(offers=None)
        checks = _cache(event["url"])
        jsonld = build_pages.event_jsonld(event, checks=checks, now=NOW)

        self.assertEqual(jsonld["url"], event["url"])
        self.assertNotIn("url", jsonld["offers"])
    def test_unverified_booking_keeps_only_verified_info_external(self):
        event = _detail_event()
        checks = {
            **_cache(event["url"]),
            **_cache(event["offers"]["url"], status="unchecked", final_url=None),
        }
        jsonld = build_pages.event_jsonld(event, checks=checks, now=NOW)
        html = build_pages.render_page(event, checks=checks, now=NOW)

        self.assertEqual(jsonld["url"], event["url"])
        self.assertNotIn("url", jsonld["offers"])
        self.assertIn(f'href="{event["url"]}"', html)
        self.assertNotIn(f'href="{event["offers"]["url"]}"', html)
        self.assertIn("공식 정보 보기", html)
        self.assertIn("예매처에서 확인", html)

    def test_explicit_same_info_booking_url_is_allowed_with_its_own_proof(self):
        info = "https://www.busan.go.kr/event/same"
        event = _detail_event(url=info, offers={"url": info})
        checks = _cache(info)
        jsonld = build_pages.event_jsonld(event, checks=checks, now=NOW)
        html = build_pages.render_page(event, checks=checks, now=NOW)

        self.assertEqual(jsonld["url"], info)
        self.assertEqual(jsonld["offers"]["url"], info)
        self.assertIn('href="https://www.busan.go.kr/event/same"', html)
        self.assertIn("공식 정보 보기", html)
        self.assertIn("예매처에서 확인", html)


    def test_schema_rejects_non_uri_booking_and_free_text_duration(self):
        schema_path = Path(__file__).resolve().parents[1] / "knowledge" / "schema" / "event.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        record = {
            "id": "kopis:invalid-booking",
            "name": "잘못된 예약",
            "start_date": "2026-11-01T00:00:00+09:00",
            "url": "https://info.example.test/event/invalid",
            "location": {"sido": "서울특별시"},
            "source": "kopis",
            "fetched_at": "2026-10-15T00:00:00+09:00",
            "content_hash": "sha256:invalid",
            "offers": {"url": "not a uri"},
            "duration": "ninety minutes",
        }
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(record))
        paths = {tuple(error.absolute_path) for error in errors}

        self.assertIn(("offers", "url"), paths)
        self.assertIn(("duration",), paths)
 

    def test_blocked_or_stale_booking_has_no_clickable_reservation_cta(self):
        event = _detail_event()
        stale = (NOW - timedelta(days=8)).isoformat()
        for status, checked_at, reason in (
            ("blocked", NOW.isoformat(), "사설·예약 IP 차단"),
            ("unverified", NOW.isoformat(), "일시적 서버 응답"),
            ("verified", stale, "검증 기록 만료"),
        ):
            with self.subTest(status=status):
                checks = {
                    **_cache(event["url"]),
                    **_cache(event["offers"]["url"], status=status,
                             checked_at=checked_at, reason=reason),
                }
                html = build_pages.render_page(event, checks=checks, now=NOW)
                self.assertNotIn(f'href="{event["offers"]["url"]}"', html)
                self.assertNotIn(f'"url": "{event["offers"]["url"]}"', html)
                self.assertIn("예매처에서 확인", html)
                self.assertNotIn("신청가능", html)

    def test_verified_booking_without_inventory_proof_stays_unknown(self):
        event = _detail_event()
        checks = {**_cache(event["url"]), **_cache(event["offers"]["url"])}
        html = build_pages.render_page(event, checks=checks, now=NOW)

        self.assertIn("예매처에서 확인", html)
        self.assertNotIn("신청가능", html)
        self.assertNotIn("예약가능", html)


class TestBookingDomainPolicy(unittest.TestCase):
    def test_booking_role_allows_vendor_but_info_role_does_not_reuse_vendor_proof(self):
        vendor = "https://ticket.interpark.com/goods/21012345"
        accepted, reason = links.validate_url(vendor, role="booking")
        self.assertEqual(accepted, vendor)
        self.assertIsNone(reason)

        rejected, reason = links.validate_url(vendor)
        self.assertIsNone(rejected)
        self.assertIsNotNone(reason)
        for info_url in (
            "https://www.busan.go.kr/event/detail",
            "https://museum.or.kr/program",
            "https://university.ac.kr/admission",
        ):
            with self.subTest(info_url=info_url):
                accepted, reason = links.validate_url(info_url)
                self.assertEqual(accepted, info_url)
                self.assertIsNone(reason)

        checks = _cache(vendor)
        booking_fields = links.link_fields(vendor, checks=checks, now=NOW, role="booking")
        info_fields = links.link_fields(vendor, checks=checks, now=NOW, role="info")
        self.assertEqual(booking_fields["link_status"], "verified")
        self.assertEqual(booking_fields["link_url"], vendor)
        self.assertEqual(info_fields["link_status"], "blocked")
        self.assertIsNone(info_fields["link_url"])

    def test_booking_http_upgrade_is_same_host_and_reuses_original_cache_key(self):
        """Curated vendor HTTP is upgraded only for booking proof, never info publication."""
        source = "http://www.ticketlink.co.kr/product/66233?plain=value"
        upgraded = "https://www.ticketlink.co.kr/product/66233?plain=value"

        accepted, reason = links.validate_url(source, role="booking")
        self.assertEqual(accepted, upgraded)
        self.assertIsNone(reason)

        rejected, reason = links.validate_url(source)
        self.assertIsNone(rejected)
        self.assertIsNotNone(reason)

        for unsafe in (
            "http://unlisted.example.com/event/1",
            "https://user:pass@www.ticketlink.co.kr/product/66233",
            "https://www.ticketlink.co.kr:8443/product/66233",
        ):
            with self.subTest(unsafe=unsafe):
                rejected, reason = links.validate_url(unsafe, role="booking")
                self.assertIsNone(rejected)
                self.assertIsNotNone(reason)

        yes24, reason = links.validate_url("https://ticket.yes24.com/Perf/60635", role="booking")
        self.assertEqual(yes24, "https://ticket.yes24.com/Perf/60635")
        self.assertIsNone(reason)

        transport = _Transport({upgraded: {"status": 200}})
        entry = links.check_url(
            source,
            send=transport.send,
            resolver=transport.resolver,
            now=NOW,
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
            role="booking",
        )
        self.assertEqual(entry["link_status"], "verified")
        self.assertEqual(entry["link_url"], upgraded)
        self.assertEqual(transport.calls[1][1], upgraded)

        checks = {_key(source): entry}
        booking_fields = links.link_fields(source, checks=checks, now=NOW, role="booking")
        info_fields = links.link_fields(source, checks=checks, now=NOW, role="info")
        self.assertEqual(booking_fields["link_status"], "verified")
        self.assertEqual(booking_fields["link_url"], upgraded)
        self.assertEqual(info_fields["link_status"], "blocked")
        self.assertIsNone(info_fields["link_url"])
    def test_booking_role_still_blocks_arbitrary_unlisted_host(self):
        rejected, reason = links.validate_url(
            "https://arbitraryunlisted.com/event/1", role="booking"
        )
        self.assertIsNone(rejected)
        self.assertIsNotNone(reason)

        checks = _cache("https://arbitraryunlisted.com/event/1")
        fields = links.link_fields(
            "https://arbitraryunlisted.com/event/1",
            checks=checks,
            now=NOW,
            role="booking",
        )
        self.assertEqual(fields["link_status"], "blocked")
        self.assertIsNone(fields["link_url"])

    def test_booking_redirect_revalidates_unlisted_target(self):
        vendor = "https://ticket.interpark.com/goods/21012345"
        transport = _Transport({
            vendor: {"status": 302, "final_url": "https://arbitraryunlisted.com/phish"},
        })
        entry = links.check_url(
            vendor,
            send=transport.send,
            resolver=transport.resolver,
            now=NOW,
            monotonic=lambda: 0.0,
            sleep=lambda _: None,
            role="booking",
        )

        self.assertNotEqual(entry["link_status"], "verified")
        self.assertIsNone(entry["link_url"])


class TestBookingLinkQueue(unittest.TestCase):
    def test_shared_info_booking_url_dedupes_without_cross_role_cache_reuse(self):
        vendor = "https://ticket.interpark.com/goods/21012345"
        event_a = {
            "id": "event-a",
            "url": vendor,
            "start_date": "2026-10-20",
            "end_date": "2026-10-21",
        }
        event_b = {
            "id": "event-b",
            "url": vendor,
            "offers": {"url": vendor},
            "start_date": "2026-10-20",
            "end_date": "2026-10-21",
        }

        for events in ([event_a, event_b], [event_b, event_a]):
            with self.subTest(order=[event["id"] for event in events]):
                transport = _Transport()
                result = check_links.check_events(
                    events,
                    send=transport.send,
                    resolver=transport.resolver,
                    now=NOW,
                    monotonic=lambda: 0.0,
                    sleep=lambda _: None,
                    max_urls=40,
                )
                get_urls = [call[1] for call in transport.calls if call[0] == "GET"]
                self.assertEqual(get_urls, [vendor])
                self.assertEqual(set(result["checks"]), {_key(vendor)})

                checks_before_info_read = dict(result["checks"])
                booking_fields = links.link_fields(
                    vendor, checks=result["checks"], now=NOW, role="booking"
                )
                info_fields = links.link_fields(
                    vendor, checks=result["checks"], now=NOW, role="info"
                )
                self.assertEqual(booking_fields["link_status"], "verified")
                self.assertEqual(info_fields["link_status"], "blocked")
                self.assertEqual(result["checks"], checks_before_info_read)
    def test_booking_urls_share_cache_and_are_checked_before_info_urls(self):
        events = []
        expected_bookings = []
        for index in range(2):
            info = f"https://www.busan.go.kr/info/{index}"
            booking = f"https://tickets.busan.go.kr/event/{index}"
            expected_bookings.append(booking)
            events.append({
                "id": f"event-{index}",
                "url": info,
                "offers": {"url": booking},
                "start_date": "2026-10-20",
                "end_date": "2026-10-21",
                "is_experience": index == 0,
                "family_friendly": True,
            })
        transport = _Transport()
        result = check_links.check_events(
            events,
            send=transport.send,
            resolver=transport.resolver,
            now=NOW,
            monotonic=lambda: 0.0,
            sleep=lambda _: None,
            max_urls=2,
        )

        get_urls = [call[1] for call in transport.calls if call[0] == "GET"]
        self.assertEqual(get_urls, expected_bookings)
        self.assertIn(_key(expected_bookings[0]), result["checks"])
        self.assertIn(_key(expected_bookings[1]), result["checks"])

    def test_fresh_booking_proof_is_reused_under_combined_budget(self):
        info = "https://www.busan.go.kr/info/fresh"
        booking = "https://tickets.busan.go.kr/event/fresh"
        previous = {**_cache(booking), **_cache(info, status="unverified", final_url=None)}
        transport = _Transport()
        result = check_links.check_events(
            [{"id": "fresh", "url": info, "offers": {"url": booking},
              "start_date": "2026-10-20", "end_date": "2026-10-21"}],
            send=transport.send,
            resolver=transport.resolver,
            now=NOW,
            monotonic=lambda: 0.0,
            sleep=lambda _: None,
            previous=previous,
            max_urls=1,
        )

        self.assertEqual([call[1] for call in transport.calls if call[0] == "GET"], [info])
        self.assertEqual(result["checks"][_key(booking)], previous[_key(booking)])

    def test_information_and_booking_urls_observe_one_forty_url_budget(self):
        events = [
            {
                "url": f"https://www.busan.go.kr/info/{index}",
                "offers": {"url": f"https://tickets.busan.go.kr/event/{index}"},
                "end_date": "2026-10-21",
            }
            for index in range(30)
        ]
        transport = _Transport()
        check_links.check_events(
            events,
            send=transport.send,
            resolver=transport.resolver,
            now=NOW,
            monotonic=lambda: 0.0,
            sleep=lambda _: None,
            max_urls=40,
        )

        get_urls = [call[1] for call in transport.calls if call[0] == "GET"]
        self.assertLessEqual(len(get_urls), 40)
        self.assertEqual(get_urls[:30], [event["offers"]["url"] for event in events])


class TestOfflineBookingSafety(unittest.TestCase):
    def test_offline_kopis_collection_never_calls_network_and_keeps_records(self):
        with mock.patch("httpx.get", side_effect=AssertionError("offline build called network")):
            rows = KopisAdapter(offline=True).collect()

        self.assertGreaterEqual(len(rows), 1)
        self.assertTrue(all(row.get("id") for row in rows))


if __name__ == "__main__":
    unittest.main()
