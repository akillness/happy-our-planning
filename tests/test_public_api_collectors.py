"""Hermetic regression tests for the KOPIS and TourAPI public collectors."""
from __future__ import annotations

import copy
import io
import json
import logging
import os
import unittest
from dataclasses import dataclass
from datetime import datetime
from unittest import mock
import httpx

from scripts.ingest.kopis import CollectionError as KopisCollectionError
from scripts.ingest.kopis import KopisAdapter
from scripts.ingest.tourapi import CollectionError as TourCollectionError
from scripts.ingest.tourapi import TourApiAdapter


@dataclass
class Response:
    status_code: int = 200
    text: str = ""


class Sender:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, params, timeout):
        self.calls.append((url, dict(params), timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class RequestSender(Sender):
    def __init__(self, responses):
        super().__init__(responses)
        self.requests = []

    def __call__(self, url, params, timeout):
        self.requests.append(httpx.Request("GET", url, params=params))
        return super().__call__(url, params, timeout)


def kopis_xml(*records):
    body = []
    for record in records:
        fields = "".join(f"<{key}>{value}</{key}>" for key, value in record.items())
        body.append(f"<db>{fields}</db>")
    return f"<dbs>{''.join(body)}</dbs>"


def tour_json(items, total_count=None, result_code="0000"):
    body = {"items": {"item": items}}
    if total_count is not None:
        body["totalCount"] = total_count
    return json.dumps(
        {"response": {"header": {"resultCode": result_code}, "body": body}},
        ensure_ascii=False,
    )


class CollectorTestCase(unittest.TestCase):
    clock = lambda self: datetime.fromisoformat("2026-10-15T12:00:00+09:00")

    def live(self, adapter_type, env_name, sender, api_key="api-secret", **settings):
        with mock.patch.dict(os.environ, {env_name: api_key}, clear=False):
            adapter = adapter_type(offline=False, sender=sender, clock=self.clock, sleep=lambda _: None)
        adapter.cfg = copy.deepcopy(adapter.cfg)
        adapter.cfg["collection"] = settings
        return adapter

    def assert_secret_not_exposed(self, operation, error_type, secrets=("api-secret",)):
        logger = logging.getLogger("notchima.ingest")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        try:
            with self.assertRaises(error_type) as raised:
                operation()
        finally:
            logger.removeHandler(handler)
        for secret in secrets:
            self.assertNotIn(secret, str(raised.exception))
            self.assertNotIn(secret, stream.getvalue())


class TestKopisRemoteCollection(CollectorTestCase):
    def test_date_window_paging_multiple_records_and_deduplication(self):
        first = kopis_xml(
            {"mt20id": "K1", "prfnm": "첫 행사", "prfpdfrom": "2026.10.15"},
            {"mt20id": "K2", "prfnm": "둘째 행사", "prfpdfrom": "2026.10.16"},
            {"mt20id": "K1", "prfnm": "첫 행사", "prfpdfrom": "2026.10.15"},
        )
        second = kopis_xml(
            {"mt20id": "K2", "prfnm": "둘째 행사", "prfpdfrom": "2026.10.16"},
            {"mt20id": "K3", "prfnm": "셋째 행사", "prfpdfrom": "2026.10.17"},
        )
        sender = Sender([Response(text=first), Response(text=second)])
        adapter = self.live(KopisAdapter, "KOPIS_KEY", sender, window_days=3, page_size=3, max_pages=4)

        rows = adapter.fetch_native()

        self.assertEqual([row["mt20id"] for row in rows], ["K1", "K2", "K3"])
        self.assertEqual(len(sender.calls), 2)
        url, page1, _ = sender.calls[0]
        self.assertEqual(url, "https://www.kopis.or.kr/openApi/restful/pblprfr")
        self.assertEqual(page1["service"], "api-secret")
        self.assertEqual(page1["stdate"], "20261015")
        self.assertEqual(page1["eddate"], "20261017")
        self.assertEqual(page1["cpage"], 1)
        self.assertEqual(page1["rows"], 3)
        self.assertEqual(sender.calls[1][1]["cpage"], 2)
        self.assertEqual(adapter.collection_metadata["pages_fetched"], 2)
        self.assertTrue(adapter.complete)
        self.assertFalse(adapter.truncated)

    def test_valid_http_200_empty_response_is_complete_and_marked_empty(self):
        sender = Sender([Response(text="<dbs />")])
        adapter = self.live(KopisAdapter, "KOPIS_KEY", sender, page_size=10, max_pages=2)

        self.assertEqual(adapter.fetch_native(), [])
        self.assertTrue(adapter.collection_metadata["empty"])
        self.assertTrue(adapter.complete)
        self.assertFalse(adapter.truncated)

    def test_http_200_error_body_and_malformed_xml_raise_without_secret(self):
        error_xml = "<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
        for payload in (error_xml, "<dbs><db>"):
            sender = Sender([Response(text=payload)])
            adapter = self.live(KopisAdapter, "KOPIS_KEY", sender)
            self.assert_secret_not_exposed(adapter.fetch_native, KopisCollectionError)

    def test_page_cap_returns_bounded_rows_without_claiming_complete_snapshot(self):
        sender = Sender([
            Response(text=kopis_xml({"mt20id": "K1", "prfnm": "첫 행사", "prfpdfrom": "2026.10.15"})),
            Response(text=kopis_xml({"mt20id": "K2", "prfnm": "둘째 행사", "prfpdfrom": "2026.10.16"})),
        ])
        adapter = self.live(KopisAdapter, "KOPIS_KEY", sender, page_size=1, max_pages=2)

        rows = adapter.fetch_native()

        self.assertEqual(len(rows), 2)
        self.assertEqual(len(sender.calls), 2)
        self.assertTrue(adapter.truncated)
        self.assertFalse(adapter.complete)
        self.assertFalse(adapter.snapshot_complete)
        self.assertTrue(adapter.collection_metadata["truncated"])

    def test_second_page_failure_never_returns_first_page_as_success(self):
        sender = Sender([
            Response(text=kopis_xml({"mt20id": "K1", "prfnm": "첫 행사", "prfpdfrom": "2026.10.15"})),
            Response(status_code=503, text="temporarily unavailable"),
        ])
        adapter = self.live(KopisAdapter, "KOPIS_KEY", sender, page_size=1, max_pages=2, retries=0)

        with self.assertRaises(KopisCollectionError):
            adapter.fetch_native()
        self.assertEqual(len(sender.calls), 2)

    def test_remote_retry_uses_common_helper_and_then_succeeds(self):
        sender = Sender([
            Response(status_code=503, text="busy"),
            Response(text=kopis_xml({"mt20id": "K1", "prfnm": "첫 행사", "prfpdfrom": "2026.10.15"})),
        ])
        waits = []
        adapter = self.live(KopisAdapter, "KOPIS_KEY", sender, page_size=2, retries=1, backoff_seconds=0.25)
        adapter.sleep = waits.append

        rows = adapter.fetch_native()

        self.assertEqual([row["mt20id"] for row in rows], ["K1"])
        self.assertEqual(len(sender.calls), 2)
        self.assertEqual(waits, [0.25])


class TestTourApiRemoteCollection(CollectorTestCase):
    def festival(self, **overrides):
        row = {
            "contentid": "T1",
            "title": "관광 축제",
            "addr1": "서울특별시 중구 세종대로 1",
            "eventstartdate": "20261015",
            "eventenddate": "20261016",
            "homepage": "https://festival.example/events/t1",
        }
        row.update(overrides)
        return row

    def test_dict_and_list_items_page_until_total_count_and_map_homepage(self):
        sender = Sender([
            Response(text=tour_json(self.festival(), total_count=2)),
            Response(text=tour_json([self.festival(contentid="T2", title="다른 축제")], total_count=2)),
        ])
        adapter = self.live(TourApiAdapter, "TOURAPI_KEY", sender, window_days=2, page_size=1, max_pages=4)

        rows = adapter.collect()

        self.assertEqual([row["id"] for row in rows], ["tourapi:T1", "tourapi:T2"])
        self.assertEqual(rows[0]["url"], "https://festival.example/events/t1")
        self.assertIn("contentId=T1", rows[0]["source_url"])
        self.assertNotIn("cotid=", rows[0]["url"])
        self.assertEqual(adapter.collection_metadata["provider_total"], 2)
        self.assertTrue(adapter.complete)
        self.assertEqual(len(sender.calls), 2)
        url, params, _ = sender.calls[0]
        self.assertEqual(url, "https://apis.data.go.kr/B551011/KorService2/searchFestival2")
        self.assertEqual(params["serviceKey"], "api-secret")
        self.assertEqual(params["eventStartDate"], "20261015")
        self.assertEqual(params["numOfRows"], 1)
        self.assertEqual(params["pageNo"], 1)
        self.assertEqual(sender.calls[1][1]["pageNo"], 2)

    def test_http_200_empty_total_count_is_empty_complete_result(self):
        sender = Sender([Response(text=tour_json([], total_count=0))])
        adapter = self.live(TourApiAdapter, "TOURAPI_KEY", sender, page_size=10, max_pages=2)

        self.assertEqual(adapter.fetch_native(), [])
        self.assertEqual(adapter.collection_metadata["provider_total"], 0)
        self.assertTrue(adapter.collection_metadata["empty"])
        self.assertTrue(adapter.complete)

    def test_result_code_and_xml_error_body_raise_without_secret(self):
        bad_json = tour_json([], total_count=0, result_code="99")
        xml_error = "<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
        for payload in (bad_json, xml_error):
            sender = Sender([Response(text=payload)])
            adapter = self.live(TourApiAdapter, "TOURAPI_KEY", sender)
            self.assert_secret_not_exposed(adapter.fetch_native, TourCollectionError)

    def test_page_cap_is_bounded_and_marks_noncomplete_snapshot(self):
        sender = Sender([
            Response(text=tour_json([self.festival(contentid="T1")], total_count=3)),
            Response(text=tour_json([self.festival(contentid="T2")], total_count=3)),
        ])
        adapter = self.live(TourApiAdapter, "TOURAPI_KEY", sender, page_size=1, max_pages=2)

        rows = adapter.fetch_native()

        self.assertEqual([row["contentid"] for row in rows], ["T1", "T2"])
        self.assertTrue(adapter.truncated)
        self.assertFalse(adapter.complete)
        self.assertFalse(adapter.snapshot_complete)
        self.assertEqual(len(sender.calls), 2)

    def test_second_page_failure_raises_instead_of_returning_partial_results(self):
        sender = Sender([
            Response(text=tour_json([self.festival(contentid="T1")], total_count=2)),
            Response(status_code=503, text="temporarily unavailable"),
        ])
        adapter = self.live(TourApiAdapter, "TOURAPI_KEY", sender, page_size=1, max_pages=2, retries=0)

        with self.assertRaises(TourCollectionError):
            adapter.fetch_native()
        self.assertEqual(len(sender.calls), 2)

    def test_remote_retry_uses_common_helper_and_then_succeeds(self):
        sender = Sender([
            Response(status_code=503, text="busy"),
            Response(text=tour_json([self.festival()], total_count=1)),
        ])
        waits = []
        adapter = self.live(TourApiAdapter, "TOURAPI_KEY", sender, page_size=2, retries=1, backoff_seconds=0.25)
        adapter.sleep = waits.append

        rows = adapter.fetch_native()

        self.assertEqual([row["contentid"] for row in rows], ["T1"])
        self.assertEqual(len(sender.calls), 2)
        self.assertEqual(waits, [0.25])


class TestApiKeySerialization(CollectorTestCase):
    def test_preencoded_key_is_decoded_once_before_httpx_serialization(self):
        encoded_key = "abc%2Bdef%2F%3D%3D"
        cases = (
            (KopisAdapter, "KOPIS_KEY", "service", Response(text=kopis_xml({"mt20id": "K1", "prfnm": "행사", "prfpdfrom": "2026.10.15"}))),
            (TourApiAdapter, "TOURAPI_KEY", "serviceKey", Response(text=tour_json({"contentid": "T1", "title": "축제", "eventstartdate": "20261015"}, total_count=1))),
        )
        for adapter_type, env_name, auth_param, response in cases:
            with self.subTest(adapter=adapter_type.__name__):
                encoded_sender = RequestSender([response])
                raw_sender = RequestSender([response])
                encoded_adapter = self.live(adapter_type, env_name, encoded_sender, api_key=encoded_key, page_size=10)
                raw_adapter = self.live(adapter_type, env_name, raw_sender, api_key="abc+def/==", page_size=10)
                encoded_adapter.fetch_native()
                raw_adapter.fetch_native()
                encoded_query = encoded_sender.requests[0].url.query.decode("ascii")
                raw_query = raw_sender.requests[0].url.query.decode("ascii")
                self.assertEqual(encoded_query, raw_query)
                self.assertIn(f"{auth_param}=abc%2Bdef%2F%3D%3D", encoded_query)
                self.assertNotIn("%252B", encoded_query)
                self.assertNotIn("%252F", encoded_query)
                self.assertNotIn("api-secret", encoded_query)


    def test_preencoded_key_is_not_exposed_when_request_fails(self):
        encoded_key = "abc%2Bdef%2F%3D%3D"
        cases = (
            (KopisAdapter, "KOPIS_KEY"),
            (TourApiAdapter, "TOURAPI_KEY"),
        )
        for adapter_type, env_name in cases:
            with self.subTest(adapter=adapter_type.__name__):
                sender = RequestSender([Response(status_code=404, text="not found")])
                adapter = self.live(adapter_type, env_name, sender, api_key=encoded_key, retries=0)
                self.assert_secret_not_exposed(
                    adapter.fetch_native,
                    KopisCollectionError if adapter_type is KopisAdapter else TourCollectionError,
                    (encoded_key, "abc+def/=="),
                )

    def test_auth_param_declaration_controls_custom_query_field(self):
        cases = (
            (KopisAdapter, "KOPIS_KEY", "service", Response(text=kopis_xml({"mt20id": "K1", "prfnm": "행사", "prfpdfrom": "2026.10.15"}))),
            (TourApiAdapter, "TOURAPI_KEY", "serviceKey", Response(text=tour_json({"contentid": "T1", "title": "축제", "eventstartdate": "20261015"}, total_count=1))),
        )
        for adapter_type, env_name, default_param, response in cases:
            with self.subTest(adapter=adapter_type.__name__):
                sender = RequestSender([response])
                template = adapter_type(offline=False, sender=sender, clock=self.clock, sleep=lambda _: None)
                source_config = copy.deepcopy(template.cfg)
                source_config["auth"] = dict(source_config.get("auth") or {}, param="customAuth")
                with mock.patch("scripts.ingest.base.source_cfg", return_value=source_config), mock.patch.dict(
                    os.environ, {env_name: "api-secret"}, clear=False
                ):
                    adapter = adapter_type(offline=False, sender=sender, clock=self.clock, sleep=lambda _: None)
                adapter.cfg["collection"] = {"page_size": 10}
                adapter.fetch_native()
                query = sender.requests[0].url.query.decode("ascii")
                self.assertIn("customAuth=api-secret", query)
                self.assertNotIn(f"{default_param}=", query)


class TestKeylessFixtureCollection(unittest.TestCase):
    def test_keyless_collect_uses_fixtures_without_network_and_keeps_mapping(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("httpx.get", side_effect=AssertionError("network")):
            kopis = KopisAdapter()
            tour = TourApiAdapter()
            kopis_rows = kopis.collect()
            tour_rows = tour.collect()

        self.assertIn("kopis:PF200001", {row["id"] for row in kopis_rows})
        self.assertIn("tourapi:3001001", {row["id"] for row in tour_rows})
        self.assertEqual(next(row for row in kopis_rows if row["id"] == "kopis:PF200001")["event_type"], "MusicEvent")
        self.assertEqual(next(row for row in tour_rows if row["id"] == "tourapi:3001001")["event_type"], "Festival")


if __name__ == "__main__":
    unittest.main()
