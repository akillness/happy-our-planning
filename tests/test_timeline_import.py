"""F8.2 public Google Timeline parser/matcher contracts.

The tests use synthetic exports only and call the browser-facing importer API in
an external Node VM. They deliberately do not inspect implementation text or
make any network request.
"""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPORT_JS = ROOT / "web" / "public" / "timeline-import.js"
DIRECTIONS_JS = ROOT / "web" / "public" / "directions.js"

_NODE = r"""
const fs = require('fs');
const vm = require('vm');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const window = { addEventListener() {}, removeEventListener() {} };
const document = {
  readyState: 'complete',
  addEventListener() {},
  querySelectorAll() { return []; },
  getElementById() { return null; },
};
const NativeDate = Date;
class FixedDate extends NativeDate {
  constructor(...args) { super(...(args.length ? args : ['2026-10-24T00:00:00Z'])); }
  static now() { return NativeDate.parse('2026-10-24T00:00:00Z'); }
}
FixedDate.parse = NativeDate.parse;
FixedDate.UTC = NativeDate.UTC;
const context = {
  window, document, URL, URLSearchParams, Intl, Date: FixedDate, console,
  setTimeout, clearTimeout, TextEncoder, TextDecoder,
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context, {filename: process.argv[1]});
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context, {filename: process.argv[2]});
const api = window.NotchimaTimelineImport;
if (!api || typeof api.parse !== 'function' || typeof api.match !== 'function') {
  throw new Error('NotchimaTimelineImport public parse/match API is incomplete');
}
try {
  const result = api[request.method](...request.args);
  process.stdout.write(JSON.stringify({ok: true, result}));
} catch (error) {
  const detail = {code: error && error.code || null, message: String(error && error.message || error)};
  process.stdout.write(JSON.stringify({ok: false, error: detail}));
}
"""


def _call(method: str, *args):
    """Call only the public importer API; fail clearly when the module is absent."""
    if not IMPORT_JS.exists():
        raise AssertionError(f"missing public importer module: {IMPORT_JS}")
    completed = subprocess.run(
        ["node", "-e", _NODE, str(IMPORT_JS), str(DIRECTIONS_JS)],
        cwd=ROOT,
        input=json.dumps({"method": method, "args": args}, ensure_ascii=False),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr or completed.stdout)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(completed.stdout) from exc
    return payload


def _legacy_visit(*, start="2026-10-20T14:59:59Z", end="2026-10-20T15:30:00Z", lat=375000000, lng=1270000000):
    return {
        "placeVisit": {
            "location": {"latitudeE7": lat, "longitudeE7": lng},
            "duration": {"startTimestamp": start, "endTimestamp": end},
        }
    }


def _android_visit(*, latlng="37.5000000°, 127.0000000°", start="2026-10-20T15:00:00Z", end="2026-10-20T15:30:00Z"):
    return {
        "startTime": start,
        "endTime": end,
        "visit": {"topCandidate": {"placeLocation": {"latLng": latlng}}},
    }


def _legacy_ms_visit(*, start=1792508399000, end=1792510200000):
    return {
        "placeVisit": {
            "location": {"latitudeE7": 375000000, "longitudeE7": 1270000000},
            "duration": {"startTimestampMs": str(start), "endTimestampMs": str(end)},
        }
    }


def _error_code(result: dict) -> str | None:
    error = result.get("error") or {}
    return error.get("code") if isinstance(error, dict) else None


def _ios_visit(*, lat=37.5, lng=127.0, start="2026-10-20T16:00:00Z", end="2026-10-20T16:30:00Z"):
    return {
        "startTime": start,
        "endTime": end,
        "visit": {"topCandidate": {"placeLocation": f"geo:{lat},{lng}"}},
    }


def _event(event_id: str, *, lat=37.5, lng=127.0, start="2026-10-20", end="2026-10-20", precision=None):
    result = {
        "id": event_id,
        "name": event_id,
        "start_date": start,
        "end_date": end,
        "lat": lat,
        "lng": lng,
        "location": {"lat": lat, "lng": lng},
    }
    if precision:
        result["geo_precision"] = precision
    return result


class TestTimelineImportParser(unittest.TestCase):
    def test_parses_legacy_android_and_ios_visits_with_public_shape(self):
        cases = (
            (
                "legacy",
                {"timelineObjects": [_legacy_visit()]},
                37.5,
                127.0,
                "2026-10-20T14:59:59Z",
            ),
            (
                "android",
                {"semanticSegments": [_android_visit()]},
                37.5,
                127.0,
                "2026-10-20T15:00:00Z",
            ),
            (
                "ios",
                [_ios_visit()],
                37.5,
                127.0,
                "2026-10-20T16:00:00Z",
            ),
        )
        for format_name, payload, expected_lat, expected_lng, expected_start in cases:
            with self.subTest(format=format_name):
                result = _call("parse", json.dumps(payload, ensure_ascii=False))
                self.assertTrue(result["ok"], result)
                parsed = result["result"]
                self.assertEqual(parsed["format"], format_name)
                self.assertEqual(len(parsed["visits"]), 1)
                visit = parsed["visits"][0]
                self.assertAlmostEqual(visit["lat"], expected_lat, places=7)
                self.assertAlmostEqual(visit["lng"], expected_lng, places=7)
                self.assertEqual(visit["start"], expected_start)
                self.assertEqual(visit["end"].endswith("Z"), True)
                self.assertIsInstance(parsed["skipped"], int)
    def test_supports_legacy_millisecond_timestamps_and_skips_future_visits(self):
        payload = {"timelineObjects": [_legacy_ms_visit(), _legacy_visit(start="2026-10-25T15:00:00Z", end="2026-10-25T15:30:00Z")]}
        result = _call("parse", json.dumps(payload, ensure_ascii=False))
        self.assertTrue(result["ok"], result)
        parsed = result["result"]
        self.assertEqual(parsed["format"], "legacy")
        self.assertEqual(parsed["skipped"], 1)
        self.assertEqual(parsed["visits"], [{"lat": 37.5, "lng": 127, "start": "2026-10-20T14:59:59.000Z", "end": "2026-10-20T15:30:00.000Z"}])

    def test_invalid_json_reports_typed_error(self):
        result = _call("parse", "{not-json")
        self.assertFalse(result["ok"], result)
        self.assertEqual(_error_code(result), "invalid")

    def test_rejects_raw_points_bad_times_bad_coordinates_and_unknown_shapes(self):
        payload = {
            "timelineObjects": [
                _legacy_visit(),  # one valid control row
                {"activitySegment": {"startLocation": {"latitudeE7": 375000000, "longitudeE7": 1270000000}}},
                {"timelinePath": [{"point": "37.5,127.0"}]},
                {"searchQuery": {"query": "private place"}},
                _legacy_visit(start="", end="2026-10-20T15:30:00Z"),
                _legacy_visit(start="2026-10-20T16:00:00Z", end="2026-10-20T15:30:00Z"),
                _legacy_visit(lat=910000000, lng=1270000000),
                _legacy_visit(lat=375000000, lng=1810000000),
            ]
        }
        result = _call("parse", json.dumps(payload, ensure_ascii=False))
        self.assertTrue(result["ok"], result)
        parsed = result["result"]
        self.assertEqual(len(parsed["visits"]), 1)
        self.assertGreaterEqual(parsed["skipped"], 6)
        self.assertNotIn("private place", json.dumps(parsed, ensure_ascii=False))

        unknown = _call("parse", json.dumps({"unrelated": [{"lat": 37.5, "lng": 127.0}]}))
        self.assertFalse(unknown["ok"], unknown)
        self.assertEqual(_error_code(unknown), "unsupported")

    def test_enforces_size_and_record_limits_before_matching(self):
        oversized = json.dumps({"timelineObjects": []}) + " " * (20 * 1024 * 1024)
        result = _call("parse", oversized)
        self.assertFalse(result["ok"], result)
        self.assertEqual(_error_code(result), "tooLarge")

        row = _legacy_visit()
        too_many = json.dumps({"timelineObjects": [row] * 100001}, ensure_ascii=False)
        result = _call("parse", too_many)
        self.assertFalse(result["ok"], result)
        self.assertEqual(_error_code(result), "tooMany")


class TestTimelineImportMatcher(unittest.TestCase):
    def test_matches_inclusive_period_and_150m_real_coordinates_only(self):
        events = [
            _event("same-place-a"),
            _event("same-place-b"),  # same real coordinates: both candidates are retained
            _event("near-inside", lat=37.5012),  # about 133m north
            _event("near-outside", lat=37.5015),  # about 167m north
            _event("centroid", precision="sido-centroid"),
            _event("no-coordinates", lat=None, lng=None),
            _event("period-boundary", start="2026-10-21", end="2026-10-22"),
        ]
        visits = [
            {"lat": 37.5, "lng": 127.0, "start": "2026-10-20T00:00:00+09:00", "end": "2026-10-20T00:30:00+09:00"},
            {"lat": 37.5, "lng": 127.0, "start": "2026-10-20T09:00:00+09:00", "end": "2026-10-20T09:30:00+09:00"},
            {"lat": 37.5, "lng": 127.0, "start": "2026-10-20T09:00:00+09:00", "end": "2026-10-20T09:30:00+09:00"},  # exact duplicate
            {"lat": 37.5, "lng": 127.0, "start": "2026-10-21T00:00:00+09:00", "end": "2026-10-21T00:10:00+09:00"},  # inclusive start
            {"lat": 37.5, "lng": 127.0, "start": "2026-10-22T23:59:59+09:00", "end": "2026-10-23T00:10:00+09:00"},  # inclusive end
            {"lat": 37.5, "lng": 127.0, "start": "2026-10-19T23:59:59+09:00", "end": "2026-10-20T00:10:00+09:00"},
        ]
        result = _call("match", visits, events)
        self.assertTrue(result["ok"], result)
        candidates = result["result"]
        self.assertTrue(candidates, "at least one in-period real-coordinate candidate is required")
        self.assertTrue(all(set(candidate) == {"event_id", "date", "time", "visited", "source"} for candidate in candidates))
        self.assertTrue(all(candidate["visited"] is True and candidate["source"] == "google-export" for candidate in candidates))
        ids = {candidate["event_id"] for candidate in candidates}
        self.assertIn("same-place-a", ids)
        self.assertIn("same-place-b", ids)
        self.assertIn("near-inside", ids)
        self.assertNotIn("near-outside", ids)
        self.assertNotIn("centroid", ids)
        self.assertNotIn("no-coordinates", ids)
        self.assertIn("period-boundary", ids)
        boundary_dates = {(c["date"], c["time"]) for c in candidates if c["event_id"] == "period-boundary"}
        self.assertEqual(boundary_dates, {("2026-10-21", "00:00"), ("2026-10-22", "23:59")})
        unique_keys = {(candidate["event_id"], candidate["date"], candidate["time"]) for candidate in candidates}
        self.assertEqual(len(unique_keys), len(candidates), "re-importing an identical visit must not duplicate a candidate")
        same_event_times = {(c["date"], c["time"]) for c in candidates if c["event_id"] == "same-place-a"}
        self.assertEqual(len(same_event_times), 2, "distinct real visit times remain distinct")

    def test_converts_utc_instants_to_kst_date_and_time_at_midnight_boundary(self):
        events = [_event("kst-before", start="2026-10-20", end="2026-10-20"), _event("kst-after", start="2026-10-21", end="2026-10-21")]
        visits = [
            {"lat": 37.5, "lng": 127.0, "start": "2026-10-20T14:59:59Z", "end": "2026-10-20T15:00:01Z"},
            {"lat": 37.5, "lng": 127.0, "start": "2026-10-20T15:00:00Z", "end": "2026-10-20T15:00:01Z"},
        ]
        result = _call("match", visits, events)
        self.assertTrue(result["ok"], result)
        by_id = {(row["event_id"], row["date"], row["time"]) for row in result["result"]}
        self.assertIn(("kst-before", "2026-10-20", "23:59"), by_id)
        self.assertIn(("kst-after", "2026-10-21", "00:00"), by_id)


if __name__ == "__main__":
    unittest.main()
