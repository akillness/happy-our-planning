"""KOPIS 공연장 주소 좌표화 (시설검색+상세, 영속 캐시) 테스트. 네트워크 없음."""
import unittest
from unittest import mock

from scripts.ingest import kopis as kopis_mod
from scripts.ingest.kopis import (
    KopisAdapter,
    _geo_from_facility,
    _pick_facility,
    _venue_search_names,
)


def _search_rows(*names):
    return [{"fcltynm": n, "mt10id": f"FC{i:06d}"} for i, n in enumerate(names)]


class TestVenuePick(unittest.TestCase):
    def test_exact_match_only(self):
        rows = _search_rows("세종문화회관", "세종문화회관 별관")
        picked = _pick_facility(rows, "세종문화회관")
        self.assertEqual(picked["mt10id"], "FC000000")

    def test_whitespace_insensitive(self):
        rows = _search_rows("세종 문화회관")
        self.assertIsNotNone(_pick_facility(rows, "세종문화회관"))

    def test_no_match_returns_none(self):
        self.assertIsNone(_pick_facility(_search_rows("예술의전당"), "세종문화회관"))
        self.assertIsNone(_pick_facility([], "세종문화회관"))
        self.assertIsNone(_pick_facility(_search_rows("세종문화회관"), ""))

    def test_search_names_strip_parenthesis(self):
        self.assertEqual(
            _venue_search_names("예스24 라이브홀 (구. 악스코리아)"),
            ["예스24 라이브홀 (구. 악스코리아)", "예스24 라이브홀"],
        )
        self.assertEqual(_venue_search_names("세종문화회관"), ["세종문화회관"])
        self.assertEqual(_venue_search_names(""), [])


class TestVenueGeo(unittest.TestCase):
    def test_valid_detail(self):
        geo = _geo_from_facility({"la": "37.5725", "lo": "126.9756",
                                  "adres": "서울특별시 종로구 세종대로 175", "mt10id": "FC000020"})
        self.assertEqual(geo, {"lat": 37.5725, "lng": 126.9756,
                               "address": "서울특별시 종로구 세종대로 175", "mt10id": "FC000020"})

    def test_out_of_korea_rejected(self):
        self.assertIsNone(_geo_from_facility({"la": "0", "lo": "0", "adres": "x"}))
        self.assertIsNone(_geo_from_facility({"la": "37.5", "lo": "126.9", "adres": ""}))
        self.assertIsNone(_geo_from_facility({"la": "bad", "lo": "126.9", "adres": "x"}))


class TestAttachVenueGeo(unittest.TestCase):
    def _adapter(self):
        adapter = KopisAdapter.__new__(KopisAdapter)
        adapter.api_key = "k"
        adapter._auth_param = "service"
        adapter.sleep = lambda _: None
        from scripts.ingest.kopis import CollectionError  # noqa
        adapter.collection_metadata = {}
        return adapter

    def test_resolve_and_cache_with_injected_sender(self):
        adapter = self._adapter()
        search_resp = _search_rows("세종문화회관")
        detail_resp = [{"fcltynm": "세종문화회관", "mt10id": "FC000020",
                        "la": "37.5725", "lo": "126.9756", "adres": "서울특별시 종로구 세종대로 175"}]
        calls = []

        def fake_request(path, params, sender):
            calls.append(path)
            if path == "prfplc":
                return search_resp
            return detail_resp

        cache = {}
        with mock.patch.object(kopis_mod, "_load_venue_cache", return_value=cache), \
             mock.patch.object(kopis_mod, "_save_venue_cache",
                               side_effect=lambda c: cache.update(c)):
            rows = [{"fcltynm": "세종문화회관"}, {"fcltynm": "없는공연장"}]
            # _attach_venue_geo의 request 경로를 주입하기 위해 _venue_request를 패치
            def fake_venue_request(path, params, sender=None):
                return fake_request(path, params, sender)
            with mock.patch.object(KopisAdapter, "_venue_request",
                                   side_effect=lambda p, pr, s=None: fake_venue_request(p, pr, s)):
                stats = adapter._attach_venue_geo(rows)
        self.assertEqual(stats["resolved"], 1)
        self.assertEqual(stats["missed"], 1)
        self.assertEqual(rows[0]["_venue_geo"]["lat"], 37.5725)
        self.assertNotIn("_venue_geo", rows[1])
        self.assertTrue(cache["없는공연장"].get("miss"))

    def test_cached_venues_skip_network(self):
        adapter = self._adapter()
        cache = {"세종문화회관": {"lat": 37.5, "lng": 126.9, "address": "서울", "venue": "세종문화회관"}}
        with mock.patch.object(kopis_mod, "_load_venue_cache", return_value=dict(cache)), \
             mock.patch.object(kopis_mod, "_save_venue_cache"), \
             mock.patch.object(KopisAdapter, "_venue_request",
                               side_effect=AssertionError("network 호출 금지")):
            rows = [{"fcltynm": "세종문화회관"}]
            stats = adapter._attach_venue_geo(rows)
        self.assertEqual(stats["cached"], 1)
        self.assertEqual(rows[0]["_venue_geo"]["lng"], 126.9)


if __name__ == "__main__":
    unittest.main()
