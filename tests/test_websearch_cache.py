"""websearch 응답 캐시 (TTL·메모리 상한·라운드트립) 테스트."""
import datetime as dt
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from scripts.common.dates import KST
from scripts.ingest import websearch as ws


class TestCacheFresh(unittest.TestCase):
    def _now(self):
        return dt.datetime(2026, 10, 15, 12, 0, 0, tzinfo=KST)

    def test_fresh_within_ttl(self):
        ts = (self._now() - dt.timedelta(hours=5)).isoformat()
        self.assertTrue(ws._cache_fresh(ts, 20, now=self._now()))

    def test_stale_beyond_ttl(self):
        ts = (self._now() - dt.timedelta(hours=21)).isoformat()
        self.assertFalse(ws._cache_fresh(ts, 20, now=self._now()))

    def test_unparseable_is_stale(self):
        self.assertFalse(ws._cache_fresh("not-a-date", 20, now=self._now()))
        self.assertFalse(ws._cache_fresh("", 20, now=self._now()))


class TestCacheKeyAndTrim(unittest.TestCase):
    def test_key_deterministic_and_query_sensitive(self):
        self.assertEqual(ws._cache_key("exa", 10, "축제"), ws._cache_key("exa", 10, "축제"))
        self.assertNotEqual(ws._cache_key("exa", 10, "축제"), ws._cache_key("exa", 10, "공연"))
        self.assertNotEqual(ws._cache_key("exa", 10, "축제"), ws._cache_key("brave", 10, "축제"))

    def test_trim_caps_text_only(self):
        resp = {"results": [
            {"title": "t", "text": "x" * 5000, "score": 0.9},
            {"title": "u", "score": 0.5},
        ], "other": [1, 2]}
        out = ws._trim_response(resp, 2000)
        self.assertEqual(len(out["results"][0]["text"]), 2000)
        self.assertEqual(out["results"][0]["score"], 0.9)
        self.assertEqual(out["results"][1], {"title": "u", "score": 0.5})
        self.assertEqual(out["other"], [1, 2])


class TestCacheRoundtrip(unittest.TestCase):
    def test_save_load_roundtrip(self):
        with TemporaryDirectory() as tmp:
            with mock.patch.object(ws, "RAW_DIR", Path(tmp)):
                self.assertEqual(ws._load_cache(), {})
                ws._save_cache({"k": {"fetched_at": "2026-10-15T00:00:00+09:00",
                                      "query": "q", "response": {"results": []}}})
                loaded = ws._load_cache()
                self.assertEqual(loaded["k"]["query"], "q")

    def test_version_mismatch_ignored(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "websearch" / "_cache.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{"version": 999, "items": {"k": 1}}', encoding="utf-8")
            with mock.patch.object(ws, "RAW_DIR", Path(tmp)):
                self.assertEqual(ws._load_cache(), {})


if __name__ == "__main__":
    unittest.main()
