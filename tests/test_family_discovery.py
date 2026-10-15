"""Focused regression tests for conservative family discovery contracts.

Run: python -m unittest tests.test_family_discovery -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.build import build_index


ROOT = Path(__file__).resolve().parents[1]
EVENTS_JSON = ROOT / "web" / "public" / "data" / "events.json"


class TestDeriveFamilyFlags(unittest.TestCase):
    @staticmethod
    def classify(**overrides):
        fields = {
            "age": None,
            "age_bands": [],
            "audience": [],
            "name": None,
            "description": None,
            "themes": [],
        }
        fields.update(overrides)
        return build_index.derive_family_flags(**fields)

    def test_explicit_family_or_child_evidence_is_true_and_explained(self):
        cases = [
            {"name": "어린이 과학 체험"},
            {"audience": ["가족"]},
            {"description": "초등학생 대상 체험 프로그램"},
            {"themes": ["키즈"]},
            {"age": "7-13"},
            {"age": "13세 이하"},
        ]
        for case in cases:
            with self.subTest(case=case):
                friendly, evidence = self.classify(**case)
                self.assertIs(friendly, True)
                self.assertTrue(evidence.strip(), "true family classification must carry evidence")

    def test_adult_only_universal_and_missing_information_are_not_family(self):
        cases = [
            {"audience": ["성인 전용"]},
            {"age": "전연령"},
            {},
        ]
        for case in cases:
            with self.subTest(case=case):
                friendly, evidence = self.classify(**case)
                self.assertIs(friendly, False)
                self.assertTrue(evidence.strip())
        self.assertEqual(self.classify(age="전연령")[1], "전연령만 표기")
        self.assertEqual(self.classify()[1], "근거 부족")

    def test_minimum_age_ratings_and_teen_range_do_not_promote_family(self):
        for age in ("만 7세 이상", "만 13세 이상", "13-18"):
            with self.subTest(age=age):
                friendly, evidence = self.classify(age=age)
                self.assertIs(friendly, False)
                self.assertTrue(evidence.strip())

    def test_prohibition_phrases_override_positive_words(self):
        phrases = ("노키즈존", "no kids", "어린이 입장 불가", "미취학 아동 관람 불가")
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                friendly, evidence = self.classify(
                    name=f"가족 프로그램 {phrase}", audience=["가족"],
                )
                self.assertIs(friendly, False)
                self.assertIn("명시 제한", evidence)

    def test_contextual_child_restrictions_override_family_words(self):
        cases = [
            {"name": "유아 동반 입장 불가 공연장"},
            {"name": "미취학아동 동반입장 금지 안내"},
            {"name": "가족 모임 공간 (미성년자 출입 금지)"},
            {"description": "미취학 아동 학부모 대상 안전교육 설명회 (보호자만 참석)"},
            {"name": "아동 보호자 전용 세미나"},
        ]
        for case in cases:
            with self.subTest(case=case):
                friendly, evidence = self.classify(**case)
                self.assertIs(friendly, False)
                self.assertTrue(evidence.strip())

    def test_restriction_containing_negation_stays_conservative_false(self):
        """A restriction-bearing negation is not promoted without source verification."""
        friendly, evidence = self.classify(name="노키즈존 아닙니다. 어린이 환영")
        self.assertIs(friendly, False)
        self.assertTrue(evidence.strip())

    def test_genuine_child_prohibition_wins_over_negated_phrase(self):
        friendly, evidence = self.classify(
            description="노키즈존 아닙니다. 어린이 환영. 미취학 아동 관람 불가",
        )
        self.assertIs(friendly, False)
        self.assertTrue(evidence.strip())


class TestFamilyOutputs(unittest.TestCase):
    def test_flatten_propagates_derived_family_fields(self):
        flat = build_index._flatten({
            "id": "fixture:family",
            "name": "어린이 숲 체험",
            "description": "초등학생 대상 자연 체험",
            "age": "7-13",
            "age_bands": ["어린이"],
            "audience": ["어린이"],
            "themes": ["체험"],
            "location": {"sido": "서울특별시", "sigungu": "종로구"},
        })
        self.assertIs(flat["family_friendly"], True)
        self.assertTrue(flat["family_evidence"].strip())
        self.assertEqual(flat["age_bands"], ["어린이"])
        self.assertEqual(flat["sido"], "서울특별시")

    def test_canonical_family_fixture_shapes_flatten_true_with_evidence(self):
        # Keep four representative source shapes local so daily ingest refreshes cannot
        # turn a classification regression into a fixture-count failure.
        canonical = [
            {
                "id": "example:demo-application", "name": "서울 어린이 여름 음악축제",
                "description": "초등학생 대상 오케스트라 체험", "age": "7-13",
                "age_bands": ["어린이"], "audience": ["어린이", "가족"],
                "themes": ["공연", "축제"],
            },
            {
                "id": "kopis:PF200002", "name": "부산 어린이 인형극",
                "age_bands": [], "audience": [], "themes": ["공연"],
            },
            {
                "id": "websearch:b254ade704ec",
                "name": "서울 한강 여름 무료 가족 축제",
                "description": "무료 가족 축제 신청 안내", "age_bands": [],
                "audience": [], "themes": ["축제"],
            },
            {
                "id": "websearch:bb38c44890b9", "name": "경기도 어린이 과학 체험전",
                "description": "어린이 과학 체험 전시", "age_bands": [],
                "audience": [], "themes": ["체험"],
            },
        ]
        for fixture in canonical:
            with self.subTest(event_id=fixture["id"]):
                flat = build_index._flatten(fixture)
                self.assertIs(flat["family_friendly"], True)
                self.assertTrue(flat["family_evidence"].strip(), fixture["id"])

    def test_generated_output_propagates_family_fields_and_evidence(self):
        records = [
            {
                "id": "fixture:family", "name": "어린이 숲 체험",
                "description": "초등학생 대상 자연 체험", "age": "7-13",
                "age_bands": ["어린이"], "audience": ["어린이"],
                "themes": ["체험"], "location": {"sido": "서울특별시"},
                "start_date": "2099-01-01", "status": "Scheduled",
            },
            {
                "id": "fixture:universal", "name": "누구나 참여 행사",
                "age": "전연령", "themes": ["공연"],
                "location": {"sido": "서울특별시"},
                "start_date": "2099-01-02", "status": "Scheduled",
            },
            {
                "id": "fixture:adult", "name": "성인 전용 강연",
                "audience": ["성인 전용"], "themes": ["교육"],
                "location": {"sido": "서울특별시"},
                "start_date": "2099-01-03", "status": "Scheduled",
            },
            {
                "id": "fixture:missing", "name": "정보 부족 행사",
                "themes": ["축제"], "location": {"sido": "서울특별시"},
                "start_date": "2099-01-04", "status": "Scheduled",
            },
        ]
        rows = [(None, record, "") for record in records]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(build_index, "OUT_DIR", Path(tmp)), \
                    mock.patch.object(build_index, "iter_events", return_value=iter(rows)):
                build_index.build()
            events = json.loads((Path(tmp) / "events.json").read_text(encoding="utf-8"))

        by_id = {event["id"]: event for event in events}
        self.assertEqual(set(by_id), {record["id"] for record in records})
        for event in events:
            self.assertIn("family_friendly", event, event["id"])
            self.assertIn("family_evidence", event, event["id"])
        self.assertIs(by_id["fixture:family"]["family_friendly"], True)
        self.assertTrue(by_id["fixture:family"]["family_evidence"].strip())
        self.assertIs(by_id["fixture:universal"]["family_friendly"], False)
        self.assertEqual(by_id["fixture:universal"]["family_evidence"], "전연령만 표기")
        self.assertIs(by_id["fixture:adult"]["family_friendly"], False)
        self.assertIs(by_id["fixture:missing"]["family_friendly"], False)
        self.assertEqual(by_id["fixture:missing"]["family_evidence"], "근거 부족")


if __name__ == "__main__":
    unittest.main()
