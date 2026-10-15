"""Regression tests for source-grounded event-date extraction in web search hits."""

import unittest

from scripts.ingest import websearch


class _FakeLiveWebSearchAdapter(websearch.WebSearchAdapter):
    """Exercise the live collection path without making network requests."""

    def __init__(self, provider, response):
        super().__init__(provider=provider, offline=False)
        self._response = response

    def _expand_queries(self):
        return ["전국 어린이 행사 2026"]

    def _call_provider(self, httpx, query, num):
        return self._response


def _provider_response(provider, result):
    if provider == "exa":
        return {"results": [result]}
    if provider == "brave":
        return {"web": {"results": [result]}}
    if provider == "tavily":
        return {"results": [result]}
    raise AssertionError(f"unsupported provider fixture: {provider}")


def _result(provider, *, title, url, text, score=0.9, **fields):
    common = {"title": title, "url": url, "score": score}
    if provider == "exa":
        common["text"] = text
        common["publishedDate"] = "2026-01-15T00:00:00Z"
    elif provider == "brave":
        common["description"] = text
        common["page_age"] = "2026-01-15"
    elif provider == "tavily":
        common["content"] = text
        common["published_date"] = "2026-01-15"
    common.update(fields)
    return common


class TestSearchEventMetadata(unittest.TestCase):
    providers = ("exa", "brave", "tavily")

    def test_live_fake_provider_results_extract_dates_from_event_text(self):
        cases = {
            "exa": (
                "서울 어린이 과학체험 행사",
                "https://science.seoul.go.kr/events/2026",
                "서울 어린이 과학체험 행사기간: 2026년 11월 1일~11월 3일",
            ),
            "brave": (
                "부산광역시 가족 교육 워크숍",
                "https://www.busan.go.kr/edu/2026",
                "부산광역시 가족 교육 워크숍 개최기간 2026.12.10 - 2026.12.12",
            ),
            "tavily": (
                "경기도 자연 교육 프로그램",
                "https://www.gg.go.kr/program/2026",
                "경기도 자연 교육 프로그램 교육기간: 2026-10-05 ~ 2026-10-07",
            ),
        }

        for provider in self.providers:
            with self.subTest(provider=provider):
                title, url, text = cases[provider]
                response = _provider_response(
                    provider,
                    _result(provider, title=title, url=url, text=text),
                )
                rows = _FakeLiveWebSearchAdapter(provider, response).collect()

                self.assertEqual(len(rows), 1)
                event = rows[0]
                self.assertEqual(event["name"], title)
                self.assertEqual(event["source"], "websearch")
                self.assertEqual(event["source_url"], url)
                if provider == "tavily":
                    self.assertEqual(event["start_date"], "2026-10-05T00:00:00+09:00")
                    self.assertEqual(event["end_date"], "2026-10-07T23:59:59+09:00")
                else:
                    expected_start, expected_end = {
                        "exa": (
                            "2026-11-01T00:00:00+09:00",
                            "2026-11-03T23:59:59+09:00",
                        ),
                        "brave": (
                            "2026-12-10T00:00:00+09:00",
                            "2026-12-12T23:59:59+09:00",
                        ),
                    }[provider]
                    self.assertEqual(event["start_date"], expected_start)
                    self.assertEqual(event["end_date"], expected_end)

    def test_event_period_and_application_start_are_separately_extracted(self):
        text = (
            "서울특별시 어린이 여름 축제 행사기간: 2026년 8월 1일부터 "
            "8월 3일까지. 신청은 7월 1일부터 가능합니다."
        )
        for provider in self.providers:
            with self.subTest(provider=provider):
                title = "서울특별시 어린이 여름 축제"
                url = f"https://culture.seoul.go.kr/{provider}/summer-2026"
                response = _provider_response(
                    provider,
                    _result(provider, title=title, url=url, text=text),
                )
                rows = _FakeLiveWebSearchAdapter(provider, response).collect()

                self.assertEqual(len(rows), 1)
                event = rows[0]
                self.assertEqual(event["start_date"], "2026-08-01T00:00:00+09:00")
                self.assertEqual(event["end_date"], "2026-08-03T23:59:59+09:00")
                self.assertEqual(event["application_start"], "2026-07-01T00:00:00+09:00")
                self.assertNotIn("application_end", event)

    def test_event_dates_without_an_explicit_year_are_rejected(self):
        text = "서울특별시 어린이 행사기간: 8월 1일~8월 3일"
        for provider in self.providers:
            with self.subTest(provider=provider):
                response = _provider_response(
                    provider,
                    _result(
                        provider,
                        title="서울특별시 어린이 행사",
                        url=f"https://culture.seoul.go.kr/{provider}/no-year",
                        text=text,
                    ),
                )
                rows = _FakeLiveWebSearchAdapter(provider, response).collect()
                self.assertEqual(rows, [])


    def test_live_fake_provider_preserves_structured_event_and_application_dates(self):
        fields = {
            "start_date": "2026-09-01",
            "end_date": "2026-09-03",
            "application_start": "2026-08-01",
            "application_end": "2026-08-20",
        }
        for provider in self.providers:
            with self.subTest(provider=provider):
                title = f"{provider} 서울 공식 어린이 행사"
                url = f"https://events.seoul.go.kr/{provider}/2026"
                response = _provider_response(
                    provider,
                    _result(
                        provider,
                        title=title,
                        url=url,
                        text="공식 행사 안내입니다.",
                        **fields,
                    ),
                )
                rows = _FakeLiveWebSearchAdapter(provider, response).collect()

                self.assertEqual(len(rows), 1)
                event = rows[0]
                self.assertEqual(event["start_date"], "2026-09-01T00:00:00+09:00")
                self.assertEqual(event["end_date"], "2026-09-03T23:59:59+09:00")
                self.assertEqual(event["application_start"], "2026-08-01T00:00:00+09:00")
                self.assertEqual(event["application_end"], "2026-08-20T23:59:59+09:00")
                self.assertEqual(event["status"], "Open")

    def test_publication_and_application_metadata_never_becomes_event_start(self):
        cases = {
            "publication-only": lambda provider: _result(
                provider,
                title="서울 행사 소개 기사",
                url=f"https://news.seoul.go.kr/{provider}/article",
                text="서울특별시에서 열리는 행사 소식입니다.",
                **{
                    "publishedDate": "2026-11-01T00:00:00Z",
                    "page_age": "2026-11-01",
                    "published_date": "2026-11-01",
                },
            ),
            "deadline-only": lambda provider: _result(
                provider,
                title="부산 지원 프로그램 모집",
                url=f"https://www.busan.go.kr/{provider}/apply",
                text="부산광역시 프로그램 신청마감: 2026.11.30",
                application_end="2026-11-30",
            ),
            "application-window-only": lambda provider: _result(
                provider,
                title="경기도 교육 접수 안내",
                url=f"https://www.gg.go.kr/{provider}/apply",
                text="경기도 교육 접수기간: 2026-11-01~2026-11-20",
                application_start="2026-11-01",
                application_end="2026-11-20",
            ),
        }

        for case_name, make_result in cases.items():
            for provider in self.providers:
                with self.subTest(case=case_name, provider=provider):
                    response = _provider_response(provider, make_result(provider))
                    rows = _FakeLiveWebSearchAdapter(provider, response).collect()
                    self.assertEqual(rows, [])

    def test_ambiguous_multiple_schedules_and_invalid_calendar_dates_are_rejected(self):
        cases = {
            "ambiguous-program-dates": (
                "서울 어린이 프로그램",
                "https://culture.seoul.go.kr/ambiguous",
                "서울특별시 1차 2026.11.01, 2차 2026.11.15, 3차 2026.12.01 진행",
            ),
            "invalid-calendar-date": (
                "부산 겨울 행사",
                "https://www.busan.go.kr/invalid",
                "부산광역시 행사기간: 2026년 2월 30일~2026년 3월 2일",
            ),
            "malformed-yearless-range": (
                "서울 겨울 행사",
                "https://culture.seoul.go.kr/malformed-yearless",
                "서울특별시 행사기간: 2025년 2월 29일~3월 1일",
            ),
            "incomplete-range-endpoint": (
                "서울 가을 행사",
                "https://culture.seoul.go.kr/incomplete",
                "서울특별시 행사기간: 2026년 11월 1일~",
            ),
            "reversed-same-day-time-range": (
                "서울 야간 행사",
                "https://culture.seoul.go.kr/reversed",
                "서울특별시 행사기간: 2026-11-01T18:00:00+09:00~2026-11-01T09:00:00+09:00",
            ),
        }
        for case_name, (title, url, text) in cases.items():
            for provider in self.providers:
                with self.subTest(case=case_name, provider=provider):
                    response = _provider_response(
                        provider,
                        _result(provider, title=title, url=url, text=text),
                    )
                    rows = _FakeLiveWebSearchAdapter(provider, response).collect()
                    self.assertEqual(rows, [])

    def test_explicit_application_fields_are_not_hybrid_filled_from_context(self):
        text = (
            "서울특별시 행사기간: 2026년 8월 1일~8월 3일. "
            "접수기간: 2026년 10월 1일~10월 20일"
        )
        cases = {
            "explicit-start": {"application_start": "2026-09-15"},
            "explicit-end": {"application_end": "2026-09-20"},
        }
        for case_name, explicit in cases.items():
            for provider in self.providers:
                with self.subTest(case=case_name, provider=provider):
                    response = _provider_response(
                        provider,
                        _result(
                            provider,
                            title="서울특별시 어린이 행사",
                            url=f"https://culture.seoul.go.kr/{provider}/{case_name}",
                            text=text,
                            **explicit,
                        ),
                    )
                    rows = _FakeLiveWebSearchAdapter(provider, response).collect()

                    self.assertEqual(len(rows), 1)
                    event = rows[0]
                    self.assertEqual(event["start_date"], "2026-08-01T00:00:00+09:00")
                    self.assertEqual(event["end_date"], "2026-08-03T23:59:59+09:00")
                    if case_name == "explicit-start":
                        self.assertEqual(event["application_start"], "2026-09-15T00:00:00+09:00")
                        self.assertNotIn("application_end", event)
                    else:
                        self.assertEqual(event["application_end"], "2026-09-20T23:59:59+09:00")
                        self.assertNotIn("application_start", event)

    def test_invalid_explicit_application_date_groups_reject_hit(self):
        text = "서울특별시 행사기간: 2026년 8월 1일~8월 3일"
        cases = {
            "invalid-calendar": {
                "application_start": "2026-02-30",
                "application_end": "2026-03-01",
            },
            "reversed-range": {
                "application_start": "2026-09-20",
                "application_end": "2026-09-10",
            },
        }
        for case_name, explicit in cases.items():
            for provider in self.providers:
                with self.subTest(case=case_name, provider=provider):
                    response = _provider_response(
                        provider,
                        _result(
                            provider,
                            title="서울특별시 어린이 행사",
                            url=f"https://culture.seoul.go.kr/{provider}/{case_name}",
                            text=text,
                            **explicit,
                        ),
                    )
                    rows = _FakeLiveWebSearchAdapter(provider, response).collect()
                    self.assertEqual(rows, [])

    def test_region_and_confidence_gates_still_reject_date_bearing_hits(self):
        event_text = "행사기간: 2026년 11월 1일~11월 3일"
        cases = {
            "unknown-region": _result(
                "{provider}",
                title="어린이 과학체험 행사",
                url="https://science.example.org/2026",
                text=event_text,
                score=0.95,
            ),
            "low-confidence": _result(
                "{provider}",
                title="서울 어린이 과학체험 행사",
                url="https://science.example.org/2026",
                text=event_text,
                score=0.20,
            ),
        }
        for case_name, template in cases.items():
            for provider in self.providers:
                with self.subTest(case=case_name, provider=provider):
                    if case_name == "low-confidence" and provider == "brave":
                        continue
                    result = dict(template)
                    if provider == "exa":
                        result["text"] = event_text
                        result.pop("content", None)
                        result.pop("description", None)
                        result["publishedDate"] = "2026-01-15T00:00:00Z"
                    elif provider == "brave":
                        result["description"] = event_text
                        result.pop("text", None)
                        result.pop("content", None)
                        result["page_age"] = "2026-01-15"
                    else:
                        result["content"] = event_text
                        result.pop("text", None)
                        result.pop("description", None)
                        result["published_date"] = "2026-01-15"
                    result["title"] = template["title"].replace("{provider}", provider)
                    response = _provider_response(provider, result)
                    rows = _FakeLiveWebSearchAdapter(provider, response).collect()
                    self.assertEqual(rows, [])



if __name__ == "__main__":
    unittest.main()
