"""Source-link safety and freshness contracts.

All network behavior is represented by injected resolver/sender fakes.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from unittest import mock
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.common import links
from scripts.build import build_pages
from scripts.ops import check_links
UTC = timezone.utc


def _now() -> datetime:
    return datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _cache(url: str, *, status: str = "verified", checked_at: str | None = None,
           final_url: str | None = None, reason: str | None = None) -> dict:
    return {
        _key(url): {
            "link_status": status,
            "link_checked_at": checked_at or _now().isoformat(),
            "link_url": final_url or url,
            "link_reason": reason,
        }
    }


class TestLinkCache(unittest.TestCase):
    def test_fresh_verified_cache_exposes_only_verified_safe_proof(self):
        url = "https://www.busan.go.kr/activity/1"
        fields = links.link_fields(url, _cache(url, final_url=url), now=_now())
        self.assertEqual(fields["link_status"], "verified")
        self.assertEqual(fields["link_checked_at"], _now().isoformat())
        self.assertEqual(fields["link_url"], url)
        self.assertEqual(links.verified_link({"url": url}, now=_now(), checks=_cache(url, final_url=url)), url)

    def test_missing_expired_future_and_mismatched_cache_proof_are_unchecked(self):
        url = "https://www.busan.go.kr/activity/2"
        old = (_now() - timedelta(days=7, seconds=1)).isoformat()
        future = (_now() + timedelta(minutes=1)).isoformat()
        for checks in (
            {},
            _cache(url, checked_at=old, final_url=url),
            _cache(url, checked_at=future, final_url=url),
            _cache("https://www.busan.go.kr/other", final_url="https://www.busan.go.kr/other"),
        ):
            with self.subTest(checks=checks):
                fields = links.link_fields(url, checks, now=_now())
                self.assertEqual(fields["link_status"], "unchecked")
                self.assertIsNone(fields["link_url"])
                self.assertIsNone(links.verified_link({"url": url}, now=_now(), checks=checks))

    def test_cache_file_round_trip_uses_the_hashed_original_url(self):
        original = "https://www.busan.go.kr/activity/3?q=secret-token"
        final = "https://www.busan.go.kr/activity/3"
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "link-checks.json"
            check_links.write_cache({"version": 1, "checks": _cache(original, final_url=final)}, path)
            checks = links.load_link_checks(path)
            serialized = path.read_text(encoding="utf-8")
        self.assertIn(_key(original), checks)
        self.assertNotIn(original, serialized)
        self.assertNotIn("secret-token", serialized)
        self.assertIn(final, serialized)


class TestCredentialQuerySafety(unittest.TestCase):
    def test_secret_after_query_scan_window_is_blocked_before_network(self):
        filler = "&".join(f"public{i}=value" for i in range(64))
        url = "https://www.data.go.kr/path?" + filler + "&serviceKey=SECRETVALUE"
        transport = _Transport({url: {"status": 200}})

        result = check_links.check_events(
            [{"id": "secret", "url": url}],
            send=transport.send,
            resolver=transport.resolver,
            now=_now(),
            monotonic=lambda: 0.0,
            sleep=lambda _: None,
            max_urls=1,
        )

        entry = result["checks"][_key(url)]
        self.assertEqual(entry["link_status"], "blocked")
        self.assertEqual(transport.calls, [])
        self.assertNotIn("SECRETVALUE", json.dumps(result, ensure_ascii=False))

    def test_oversized_benign_query_fails_closed_before_network(self):
        query = "&".join(f"public{i}=value" for i in range(65))
        url = "https://www.data.go.kr/path?" + query
        transport = _Transport({url: {"status": 200}})

        result = check_links.check_events(
            [{"id": "oversized", "url": url}],
            send=transport.send,
            resolver=transport.resolver,
            now=_now(),
            monotonic=lambda: 0.0,
            sleep=lambda _: None,
            max_urls=1,
        )

        self.assertEqual(result["checks"][_key(url)]["link_status"], "blocked")
        self.assertEqual(transport.calls, [])

    def test_public_content_id_query_within_limit_remains_checkable(self):
        url = "https://www.data.go.kr/path?contentId=42"
        transport = _Transport({url: {"status": 200}})

        result = check_links.check_events(
            [{"id": "public", "url": url}],
            send=transport.send,
            resolver=transport.resolver,
            now=_now(),
            monotonic=lambda: 0.0,
            sleep=lambda _: None,
            max_urls=1,
        )

        self.assertEqual(result["checks"][_key(url)]["link_status"], "verified")
        self.assertEqual([call[0] for call in transport.calls], ["resolve", "GET"])
    def test_deferred_prior_secret_proof_is_redacted_from_cache(self):
        url = "https://www.data.go.kr/path?serviceKey=SECRETVALUE"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            events_path = root / "events.json"
            cache_path = root / "link-checks.json"
            events_path.write_text(json.dumps([{"id": "deferred", "url": url}]), encoding="utf-8")
            check_links.write_cache(_cache(url, final_url=url), cache_path)
            transport = _Transport({url: {"status": 200}})

            result = check_links.run(
                events_path=events_path,
                cache_path=cache_path,
                send=transport.send,
                resolver=transport.resolver,
                now=_now(),
                monotonic=lambda: 0.0,
                sleep=lambda _: None,
                max_urls=0,
            )

            self.assertEqual(result["checks"][_key(url)]["link_status"], "blocked")
            serialized = cache_path.read_text(encoding="utf-8")
            self.assertNotIn("SECRETVALUE", serialized)
            self.assertEqual(transport.calls, [])

class _Transport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def resolver(self, host, port):
        self.calls.append(("resolve", host, port))
        return ["93.184.216.34"]

    def send(self, url, method, ip, host, timeout):
        self.calls.append((method, url, ip, host, timeout))
        response = self.responses.get((url, method), self.responses.get(url, links.LinkResponse(200)))
        if callable(response):
            response = response(url=url, method=method, ip=ip, host=host, timeout=timeout)
        if isinstance(response, links.LinkResponse):
            return response
        headers = {}
        if response.get("final_url") and response["status"] in {301, 302, 303, 307, 308}:
            headers["location"] = response["final_url"]
        return links.LinkResponse(response["status"], headers=headers, body=response.get("body", b""))



class TestCheckEventScheduling(unittest.TestCase):
    def test_fresh_verified_previous_proof_skips_network_and_is_preserved(self):
        url = "https://www.busan.go.kr/activity/fresh"
        previous = _cache(url, checked_at="2026-10-15T12:00:00Z", final_url=url, reason="연결 확인")
        transport = _Transport({})
        result = check_links.check_events(
            [{"id": "fresh", "url": url}],
            send=transport.send, resolver=transport.resolver,
            now=_now(), monotonic=lambda: 0.0, sleep=lambda _: None,
            previous=previous, max_urls=1,
        )
        self.assertEqual(result["checks"][_key(url)], previous[_key(url)])
        self.assertEqual(transport.calls, [])

    def test_deferred_current_url_keeps_prior_proof_under_url_budget(self):
        first = "https://www.busan.go.kr/activity/first"
        deferred = "https://www.busan.go.kr/activity/deferred"
        stale = (_now() - timedelta(days=8)).isoformat()
        previous = _cache(deferred, checked_at=stale, final_url=deferred)
        transport = _Transport({first: {"status": 200}})
        result = check_links.check_events(
            [{"id": "first", "url": first}, {"id": "deferred", "url": deferred}],
            send=transport.send, resolver=transport.resolver,
            now=_now(), monotonic=lambda: 0.0, sleep=lambda _: None,
            previous=previous, max_urls=1,
        )
        self.assertEqual(result["checks"][_key(first)]["link_status"], "verified")
        self.assertEqual(result["checks"][_key(deferred)], previous[_key(deferred)])
        self.assertEqual([call[1] for call in transport.calls if call[0] == "GET"], [first])

    def test_deadline_deferred_current_url_keeps_prior_proof(self):
        first = "https://www.busan.go.kr/activity/deadline-first"
        deferred = "https://www.busan.go.kr/activity/deadline-deferred"
        stale = (_now() - timedelta(days=8)).isoformat()
        previous = _cache(deferred, checked_at=stale, final_url=deferred)
        transport = _Transport({first: {"status": 200}})
        state = {"sent": False}

        def clock():
            return 2.0 if state["sent"] else 0.0

        original_send = transport.send

        def send(*args, **kwargs):
            state["sent"] = True
            return original_send(*args, **kwargs)

        result = check_links.check_events(
            [{"id": "first", "url": first}, {"id": "deferred", "url": deferred}],
            send=send, resolver=transport.resolver,
            now=_now(), monotonic=clock, sleep=lambda _: None,
            previous=previous, max_seconds=1,
        )
        self.assertEqual(result["checks"][_key(first)]["link_status"], "verified")
        self.assertEqual(result["checks"][_key(deferred)], previous[_key(deferred)])

    def test_upcoming_child_experience_precedes_ordinary_event(self):
        child = "https://www.busan.go.kr/activity/child"
        ordinary = "https://www.busan.go.kr/activity/ordinary"
        transport = _Transport({child: {"status": 200}, ordinary: {"status": 200}})
        events = [
            {"id": "ordinary", "url": ordinary, "start_date": "2026-10-20", "end_date": "2026-10-21"},
            {"id": "child", "url": child, "start_date": "2026-10-20", "end_date": "2026-10-21",
             "is_experience": True, "family_friendly": True},
        ]
        check_links.check_events(
            events, send=transport.send, resolver=transport.resolver,
            now=_now(), monotonic=lambda: 0.0, sleep=lambda _: None, max_urls=1,
        )
        self.assertEqual([call[1] for call in transport.calls if call[0] == "GET"], [child])

    def test_removed_url_is_pruned_from_derived_cache(self):
        removed = "https://www.busan.go.kr/activity/removed"
        current = "https://www.busan.go.kr/activity/current"
        previous = {**_cache(removed), **_cache(current)}
        transport = _Transport({current: {"status": 200}})
        result = check_links.check_events(
            [{"id": "current", "url": current}],
            send=transport.send, resolver=transport.resolver,
            now=_now(), monotonic=lambda: 0.0, sleep=lambda _: None,
            previous=previous,
        )
        self.assertNotIn(_key(removed), result["checks"])
        self.assertIn(_key(current), result["checks"])

    def test_unseen_current_url_is_not_starved_by_expired_backlog(self):
        old_events = [
            {"id": f"old-{index}", "url": f"https://www.busan.go.kr/activity/old-{index}",
             "start_date": "2020-01-01", "end_date": "2020-01-02"}
            for index in range(40)
        ]
        unseen = {"id": "unseen", "url": "https://www.busan.go.kr/activity/unseen",
                  "start_date": "2026-10-20", "end_date": "2026-10-21"}
        transport = _Transport({unseen["url"]: {"status": 200}})
        check_links.check_events(
            [*old_events, unseen], send=transport.send, resolver=transport.resolver,
            now=_now(), monotonic=lambda: 0.0, sleep=lambda _: None, max_urls=1,
        )
        self.assertEqual([call[1] for call in transport.calls if call[0] == "GET"], [unseen["url"]])

    def test_same_bucket_unseen_url_rotates_ahead_of_recently_checked_backlog(self):
        urls = [f"https://www.busan.go.kr/activity/queue-{index}" for index in range(40)]
        unseen = "https://www.busan.go.kr/activity/queue-unseen"
        events = [
            {"id": f"queue-{index}", "url": url, "start_date": "2026-10-20", "end_date": "2026-10-21"}
            for index, url in enumerate(urls)
        ]
        events.append({"id": "queue-unseen", "url": unseen, "start_date": "2026-10-20", "end_date": "2026-10-21"})
        previous = {}
        for url in urls:
            previous.update(_cache(url, status="unverified", checked_at=_now().isoformat(), final_url=None,
                                   reason="일시적 서버 응답"))
        transport = _Transport({unseen: {"status": 200}})
        check_links.check_events(
            events, send=transport.send, resolver=transport.resolver,
            now=_now(), monotonic=lambda: 0.0, sleep=lambda _: None,
            previous=previous, max_urls=1,
        )
        self.assertEqual([call[1] for call in transport.calls if call[0] == "GET"], [unseen])

    def test_failed_first_url_does_not_starve_unseen_url_on_next_daily_run(self):
        first = "https://www.busan.go.kr/activity/daily-failed"
        unseen = "https://www.busan.go.kr/activity/daily-unseen"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            events_path = root / "events.json"
            cache_path = root / "link-checks.json"
            events_path.write_text(json.dumps([
                {"id": "failed", "url": first, "start_date": "2026-10-20", "end_date": "2026-10-21"},
                {"id": "unseen", "url": unseen, "start_date": "2026-10-20", "end_date": "2026-10-21"},
            ]), encoding="utf-8")
            transport = _Transport({first: {"status": 503}, unseen: {"status": 200}})
            check_links.run(
                events_path=events_path, cache_path=cache_path,
                send=transport.send, resolver=transport.resolver,
                now=_now(), monotonic=lambda: 0.0, sleep=lambda _: None, max_urls=1,
            )
            self.assertTrue([call[1] for call in transport.calls if call[0] == "GET"])
            self.assertTrue(all(call[1] == first for call in transport.calls if call[0] == "GET"))
            transport.calls.clear()
            check_links.run(
                events_path=events_path, cache_path=cache_path,
                send=transport.send, resolver=transport.resolver,
                now=_now() + timedelta(days=1), monotonic=lambda: 0.0, sleep=lambda _: None, max_urls=1,
            )
            self.assertEqual([call[1] for call in transport.calls if call[0] == "GET"], [unseen])

    def test_today_no_end_is_current_at_nine_am_kst(self):
        now = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
        today = "https://www.busan.go.kr/activity/today-no-end"
        tomorrow = "https://www.busan.go.kr/activity/tomorrow"
        transport = _Transport({today: {"status": 200}, tomorrow: {"status": 200}})
        check_links.check_events(
            [
                {"id": "tomorrow", "url": tomorrow, "start_date": "2026-10-16"},
                {"id": "today", "url": today, "start_date": "2026-10-15"},
            ], send=transport.send, resolver=transport.resolver,
            now=now, monotonic=lambda: 0.0, sleep=lambda _: None, max_urls=1,
        )
        self.assertEqual([call[1] for call in transport.calls if call[0] == "GET"], [today])

    def test_deferred_credential_query_proof_is_redacted_from_serialized_cache(self):
        url = "https://www.busan.go.kr/activity/private?token=secret-token"
        leaked_final = "https://www.busan.go.kr/activity/private?token=secret-token"
        previous = _cache(url, checked_at=(_now() - timedelta(days=8)).isoformat(), final_url=leaked_final)
        other = "https://www.busan.go.kr/activity/other"
        transport = _Transport({other: {"status": 200}})
        result = check_links.check_events(
            [{"id": "other", "url": other}, {"id": "private", "url": url}],
            send=transport.send, resolver=transport.resolver,
            now=_now(), monotonic=lambda: 0.0, sleep=lambda _: None,
            previous=previous, max_urls=1,
        )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("secret-token", serialized)
        self.assertIsNone(result["checks"][_key(url)]["link_url"])

    def test_invalid_events_input_fails_without_overwriting_existing_cache(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_path = root / "link-checks.json"
            original = '{"version": 1, "checks": {"existing": {"link_status": "verified"}}}\n'
            cache_path.write_text(original, encoding="utf-8")
            for payload in (None, "not-json", {"events": []}):
                events_path = root / f"events-{len(str(payload))}.json"
                if payload is None:
                    events_path.unlink(missing_ok=True)
                else:
                    events_path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
                with self.subTest(payload=payload), self.assertRaises((ValueError, OSError)):
                    check_links.run(events_path=events_path, cache_path=cache_path, max_urls=0)
                self.assertEqual(cache_path.read_text(encoding="utf-8"), original)

            malformed = root / "malformed-cli.json"
            malformed.write_text("not-json", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-m", "scripts.ops.check_links", "--events", str(malformed), "--cache", str(cache_path)],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(cache_path.read_text(encoding="utf-8"), original)

    def test_five_blocked_dns_hosts_exit_with_daemonized_resolver_cap(self):
        script = r'''
import json, threading, time
from scripts.common.links import check_url

blocked = threading.Event()
def resolver(host, port):
    blocked.wait(60)
    return ["93.184.216.34"]
def sender(*args, **kwargs):
    raise AssertionError("blocked DNS must prevent sending")
results = []
def worker(index):
    results.append(check_url(
        f"https://host-{index}.go.kr/activity", send=sender, resolver=resolver,
        now=None, monotonic=time.monotonic, deadline=time.monotonic() + 0.05,
    ))
threads = [threading.Thread(target=worker, args=(index,)) for index in range(5)]
for thread in threads: thread.start()
for thread in threads: thread.join(1)
print(json.dumps([item["link_status"] for item in results]))
'''
        completed = subprocess.run(
            [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=2, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout.strip()), ["unverified"] * 5)
class TestStaticPublication(unittest.TestCase):
    def _event(self, url):
        return {
            "id": "fixture:static", "name": "<script> & 행사", "description": "</script><script>alert(1)</script>",
            "start_date": "2026-10-15", "end_date": "2026-10-16", "status": "Open",
            "sido": "서울특별시", "sigungu": "중구", "url": url, "price": "free",
        }

    def test_jsonld_escapes_script_terminators_and_verified_proof_publishes_url(self):
        url = "https://www.busan.go.kr/activity"
        html = build_pages.render_page(
            self._event(url), checks=_cache(url, final_url=url), now=_now()
        )
        self.assertNotIn("</script><script>", html)
        self.assertIn("\\u003c/script\\u003e", html)
        self.assertIn(f'<a href="{url}" rel="noopener" data-link-status="verified">', html)
        self.assertIn(f'"url": "{url}"', html)

    def test_expired_verified_proof_has_reason_not_external_anchor_or_jsonld_url(self):
        url = "https://www.busan.go.kr/activity"
        stale = (_now() - timedelta(days=8)).isoformat()
        html = build_pages.render_page(
            self._event(url), checks=_cache(url, checked_at=stale, final_url=url), now=_now()
        )
        self.assertNotIn(f'<a href="{url}"', html)
        self.assertIn("검증 기록 만료", html)
        self.assertNotIn(f'"url": "{url}"', html)


class TestLinkChecker(unittest.TestCase):
    def _check(self, url, transport, **kwargs):
        result = check_links.check_events(
            [{"id": "fixture:1", "url": url}],
            send=transport.send,
            resolver=transport.resolver,
            now=_now(), monotonic=lambda: 0.0, sleep=lambda _: None,
            **kwargs,
        )
        self.assertEqual(result["version"], 1)
        return result["checks"][_key(url)]

    def test_blocked_schemes_credentials_ports_local_private_and_ipv6_never_send(self):
        urls = [
            "http://example.org/insecure", "javascript:alert(1)", "file:///etc/passwd",
            "https://user:pass@example.org/private", "https://example.org:8443/activity",
            "https://localhost/activity", "https://127.0.0.1/activity", "https://[::1]/activity",
        ]
        for url in urls:
            with self.subTest(url=url):
                transport = _Transport({})
                entry = self._check(url, transport)
                self.assertEqual(entry["link_status"], "blocked")
                self.assertFalse(any(call[0] in {"HEAD", "GET"} for call in transport.calls))

    def test_nat64_private_ipv4_mapping_is_blocked_before_send(self):
        url = "https://example.org/activity"
        transport = _Transport({})

        def nat64_resolver(host, port):
            transport.calls.append(("resolve", host, port))
            return ["64:ff9b::a9fe:a9fe"]

        result = check_links.check_events(
            [{"id": "fixture:nat64", "url": url}],
            send=transport.send,
            resolver=nat64_resolver,
            now=_now(), monotonic=lambda: 0.0, sleep=lambda _: None,
        )
        entry = result["checks"][_key(url)]
        self.assertEqual(entry["link_status"], "blocked")
        self.assertFalse(any(call[0] in {"HEAD", "GET"} for call in transport.calls))

    def test_one_private_dns_answer_blocks_without_retry_or_send(self):
        url = "https://www.busan.go.kr/activity"
        transport = _Transport({})
        resolve_calls = []

        def resolver(host, port):
            resolve_calls.append((host, port))
            return ["93.184.216.34", "10.0.0.7"]

        result = check_links.check_events(
            [{"id": "fixture:private-dns", "url": url}], send=transport.send,
            resolver=resolver, now=_now(), monotonic=lambda: 0.0, sleep=lambda _: None,
        )
        entry = result["checks"][_key(url)]
        self.assertEqual(entry["link_status"], "blocked")
        self.assertEqual(len(resolve_calls), 1)
        self.assertFalse(any(call[0] in {"HEAD", "GET"} for call in transport.calls))

    def test_redirect_escape_is_not_verified(self):
        url = "https://www.busan.go.kr/activity"
        transport = _Transport({url: {"status": 302, "final_url": "https://evil.example.net/phish"}})
        entry = self._check(url, transport)
        self.assertNotEqual(entry["link_status"], "verified")
        self.assertIsNone(entry["link_url"])

    def test_get_navigation_uses_one_method_and_bounds_redirects(self):
        url = "https://www.busan.go.kr/activity"
        transport = _Transport({(url, "GET"): {"status": 200, "final_url": url, "body": b"<html>ok</html>"}})
        entry = self._check(url, transport)
        self.assertEqual(entry["link_status"], "verified")
        methods = [call[0] for call in transport.calls if call[0] in {"HEAD", "GET"}]
        self.assertEqual(methods, ["GET"])
    def test_large_2xx_body_above_cap_still_verifies_when_sender_is_bounded(self):
        url = "https://www.busan.go.kr/activity/large"
        # Exercise the public single-URL API so this test isolates body-size semantics.
        transport = _Transport({(url, "GET"): {"status": 200, "final_url": url, "body": b"x" * 100_000}})
        entry = links.check_url(
            url, send=transport.send, resolver=transport.resolver,
            now=_now(), monotonic=lambda: 0.0,
        )
        self.assertEqual(entry["link_status"], "verified")

    def test_default_sender_bounds_reads_closes_tls_and_pins_resolved_ip(self):
        url = "https://www.busan.go.kr/activity/large-default"
        case = self
        header = b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n"
        raw = header + (b"x" * 100_000)

        class FakeTls:
            def __init__(self):
                self.offset = 0
                self.read_sizes = []
                self.body_read = 0
                self.closed = False

            def sendall(self, _request):
                return None

            def settimeout(self, _timeout):
                return None

            def recv(self, size):
                self.read_sizes.append(size)
                start = self.offset
                chunk = raw[start : start + size]
                self.offset += len(chunk)
                self.body_read += max(0, self.offset - len(header)) - max(0, start - len(header))
                return chunk

            def close(self):
                self.closed = True

        tls = FakeTls()

        class FakeSocketModule:
            @staticmethod
            def create_connection(address, timeout):
                case.assertEqual(address, ("93.184.216.34", 443))
                case.assertGreater(timeout, 0)
                return object()

        class FakeContext:
            def wrap_socket(self, sock, server_hostname):
                case.assertEqual(server_hostname, "www.busan.go.kr")
                return tls

        class FakeSslModule:
            @staticmethod
            def create_default_context():
                return FakeContext()

        with mock.patch.dict(sys.modules, {"socket": FakeSocketModule, "ssl": FakeSslModule}):
            entry = links.check_url(
                url,
                resolver=lambda host, port: ["93.184.216.34"],
                now=_now(), monotonic=lambda: 0.0,
            )
        self.assertEqual(entry["link_status"], "verified")
        self.assertTrue(tls.closed)
        self.assertTrue(tls.read_sizes)
        self.assertLessEqual(tls.body_read, 65_536)
        self.assertEqual(tls.read_sizes[0], min(4096, len(raw)))

    def test_head_success_cannot_hide_get_redirect_smuggling(self):
        url = "https://www.busan.go.kr/activity"
        transport = _Transport({
            (url, "HEAD"): {"status": 200, "final_url": url},
            (url, "GET"): {"status": 302, "final_url": "https://evil.example.net/phish"},
        })
        entry = self._check(url, transport)
        self.assertNotEqual(entry["link_status"], "verified")
        self.assertEqual([call[0] for call in transport.calls if call[0] in {"HEAD", "GET"}], ["GET"])

    def test_transient_is_unverified_but_404_and_410_are_dead(self):
        cases = [
            ("https://www.busan.go.kr/temporary", {"status": 503}, "unverified"),
            ("https://www.busan.go.kr/missing", {"status": 404}, "dead"),
            ("https://www.busan.go.kr/gone", {"status": 410}, "dead"),
        ]
        for url, response, expected in cases:
            with self.subTest(url=url):
                entry = self._check(url, _Transport({url: response}))
                self.assertEqual(entry["link_status"], expected)
    def test_unicode_path_is_percent_encoded_for_safe_navigation(self):
        url = "https://www.busan.go.kr/체험/봄"
        encoded = "https://www.busan.go.kr/%EC%B2%B4%ED%97%98/%EB%B4%84"
        transport = _Transport({(encoded, "GET"): {"status": 200, "final_url": encoded}})
        entry = self._check(url, transport)
        self.assertEqual(entry["link_status"], "verified")
        self.assertEqual(next(call[1] for call in transport.calls if call[0] == "GET"), "https://www.busan.go.kr/%EC%B2%B4%ED%97%98/%EB%B4%84")

    def test_elapsed_deadline_marks_later_urls_unverified_without_more_sends(self):
        urls = ["https://www.busan.go.kr/one", "https://www.busan.go.kr/two"]
        transport = _Transport({url: {"status": 200} for url in urls})
        ticks = iter([0.0, 999.0])
        clock = lambda: next(ticks, 999.0)
        result = check_links.check_events(
            [{"id": str(i), "url": url} for i, url in enumerate(urls)],
            send=transport.send, resolver=transport.resolver,
            now=_now(), monotonic=clock, sleep=lambda _: None, max_seconds=1,
        )
        self.assertEqual(result["checks"][_key(urls[1])]["link_status"], "unverified")
        self.assertEqual([call[1] for call in transport.calls if call[0] == "GET"], [])

    def test_url_budget_leaves_unvisited_entries_unchecked(self):
        urls = ["https://www.busan.go.kr/one", "https://www.busan.go.kr/two"]
        transport = _Transport({url: {"status": 200} for url in urls})
        result = check_links.check_events(
            [{"id": str(i), "url": url} for i, url in enumerate(urls)],
            send=transport.send, resolver=transport.resolver,
            now=_now(), monotonic=lambda: 0.0, sleep=lambda _: None, max_urls=1,
        )
        self.assertEqual(result["checks"][_key(urls[0])]["link_status"], "verified")
        self.assertEqual(len([call for call in transport.calls if call[0] == "GET"]), 1)


    def test_pinned_ip_is_passed_to_transport(self):
        url = "https://www.busan.go.kr/activity"
        transport = _Transport({url: {"status": 200}})
        entry = self._check(url, transport)
        self.assertEqual(entry["link_status"], "verified")
        request = next(call for call in transport.calls if call[0] == "GET")
        self.assertEqual(request[2], "93.184.216.34")


if __name__ == "__main__":
    unittest.main()
