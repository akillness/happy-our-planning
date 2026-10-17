"""F7 directions and static-detail contracts.

These tests exercise the public JavaScript API through a real Node VM and the
static-page generator through its public render function.  They deliberately do
not mock external Maps success: URL construction is the contract, navigation is
owned by the browser verifier.
"""
from __future__ import annotations

import json
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.build import build_pages

ROOT = Path(__file__).resolve().parents[1]
DIRECTIONS_JS = ROOT / "web" / "public" / "directions.js"
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def _event(**overrides) -> dict:
    event = {
        "id": "fixture:direction-1",
        "name": "가족 과학 체험",
        "description": "좌표와 주소를 함께 가진 행사",
        "start_date": "2026-10-20",
        "end_date": "2026-10-20",
        "location": {
            "name": "서울시립과학관",
            "address": "서울특별시 노원구 한글비석로 160",
            "lat": 37.6421,
            "lng": 127.0767,
        },
    }
    event.update(overrides)
    return event


def _directions_call(event: dict, origin: dict | None = None) -> dict:
    """Evaluate the browser-facing API without importing private helpers."""
    if not DIRECTIONS_JS.exists():
        raise AssertionError(f"missing public directions module: {DIRECTIONS_JS}")
    script = r"""
const fs = require('fs');
const vm = require('vm');
const event = JSON.parse(process.argv[1]);
const origin = process.argv[2] === 'null' ? null : JSON.parse(process.argv[2]);
const window = {};
const document = { readyState: 'complete', addEventListener() {}, querySelectorAll() { return []; } };
window.addEventListener = () => {};
const context = { window, document, URL, encodeURIComponent, console };
vm.runInNewContext(fs.readFileSync(process.argv[3], 'utf8'), context, { filename: process.argv[3] });
const api = window.NotchimaDirections;
if (!api || typeof api.destination !== 'function' || typeof api.links !== 'function') {
  throw new Error('NotchimaDirections public API is incomplete');
}
const result = { destination: api.destination(event), links: api.links(event, origin) };
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", script, json.dumps(event, ensure_ascii=False), json.dumps(origin, ensure_ascii=False) if origin is not None else "null", str(DIRECTIONS_JS)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


class TestDirectionsPublicApi(unittest.TestCase):
    def test_coordinates_are_preferred_and_same_day_origin_is_transit(self):
        result = _directions_call(_event(), _event(id="fixture:origin", location={"name": "이전 장소", "lat": 37.5, "lng": 126.9}))
        destination = result["destination"]
        links = result["links"]
        self.assertEqual(destination, "37.6421,127.0767")
        self.assertIn("google", links)
        google = links["google"]
        self.assertIn("travelmode=transit", google)
        self.assertIn("origin=37.5%2C126.9", google)
        self.assertIn("destination=37.6421%2C127.0767", google)
        self.assertNotIn("waypoints", google)
        for key in ("google", "naver", "kakao"):
            self.assertTrue(links[key].startswith("https://"), key)

    def test_first_visit_has_no_origin_and_address_fallback_is_safe(self):
        event = _event(location={"name": "한글 장소 & 안내", "address": "서울특별시 노원구 <광장>"}, lat=None, lng=None)
        result = _directions_call(event)
        self.assertEqual(result["destination"], "서울특별시 노원구 <광장>")
        google = result["links"]["google"]
        self.assertIn("travelmode=transit", google)
        self.assertNotIn("origin=", google)
        self.assertNotIn("waypoints", google)
        self.assertNotIn("<script", google.lower())

    def test_missing_destination_returns_null_links_without_dropping_event(self):
        result = _directions_call(_event(location={"name": "", "address": ""}, lat=None, lng=None))
        self.assertIsNone(result["destination"])
        self.assertEqual(result["links"], {"google": None, "naver": None, "kakao": None})


class TestStaticDirectionsGeneration(unittest.TestCase):
    def test_static_detail_contains_direction_container_and_escaped_event_data(self):
        payload = _event(
            name='XSS <img src=x onerror="alert(1)">',
            description='문자열 </script><script>alert("x")</script>',
        )
        html = build_pages.render_page(payload, checks={}, now=NOW)
        self.assertIn('class="directions-navigation"', html)
        self.assertIn('class="directions-host"', html)
        self.assertIn('data-directions-event=', html)
        self.assertIn('<script src="../directions.js"></script>', html)
        self.assertNotIn('<img src=x onerror="alert(1)">', html)
        self.assertNotIn("</script><script>alert", html)

    def test_static_detail_keeps_missing_location_as_record_with_guidance(self):
        html = build_pages.render_page(_event(location={"name": "", "address": "", "lat": None, "lng": None}), checks={}, now=NOW)
        self.assertIn('class="directions-host"', html)
        self.assertIn('data-directions-event=', html)
        self.assertIn("directions.js", html)


if __name__ == "__main__":
    unittest.main()
