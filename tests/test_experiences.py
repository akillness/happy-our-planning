"""Source-grounded children experience classification contracts."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.build import build_index
from scripts.common import config


class TestExperienceDerivation(unittest.TestCase):
    def _derive(self, **overrides):
        fields = {
            "age": "7-13",
            "age_bands": ["어린이"],
            "audience": ["가족"],
            "name": "가족 프로그램",
            "description": "",
            "themes": ["교육"],
            "event_type": "Event",
        }
        fields.update(overrides)
        return build_index.derive_experiences(**fields)

    def test_hands_on_source_evidence_derives_experience_category_and_summary(self):
        cases = [
            ({"name": "어린이 과학 실험실", "description": "현미경으로 관찰하고 직접 실험하는 과학 체험"}, "과학·탐구"),
            ({"name": "숲속 자연 탐사", "description": "숲을 걸으며 식물을 직접 관찰하고 자연물을 탐색"}, "자연·생태"),
            ({"name": "도예 만들기", "description": "흙을 직접 빚어 나만의 그릇을 만드는 공방"}, "만들기·예술"),
            ({"name": "농장 수확과 요리", "description": "농작물을 직접 수확해 음식을 만드는 농촌 체험"}, "농촌·먹거리"),
        ]
        for fields, category in cases:
            with self.subTest(name=fields["name"]):
                categories, summary, is_experience = self._derive(**fields)
                self.assertTrue(is_experience)
                self.assertIn(category, categories)
                self.assertIsInstance(summary, str)
                self.assertTrue(summary)

    def test_viewing_music_and_puppets_are_not_experiences_without_participation(self):
        for name, description, event_type in [
            ("어린이 음악회", "오케스트라 공연을 관람하는 음악회", "MusicEvent"),
            ("어린이 인형극", "인형극을 관람하는 공연", "Event"),
        ]:
            with self.subTest(name=name):
                categories, summary, is_experience = self._derive(
                    name=name, description=description, themes=["공연"], event_type=event_type,
                )
                self.assertFalse(is_experience)
                self.assertEqual(categories, [])
                self.assertIsNone(summary)
 
    def test_music_viewing_and_generic_orchestras_do_not_become_experiences(self):
        cases = [
            {
                "name": "코리안챔버오케스트라 정기연주회",
                "description": "오케스트라 정기연주회를 관람하는 공연",
                "themes": ["공연"],
                "event_type": "MusicEvent",
            },
            {
                "name": "과학 실험과 오케스트라 관람",
                "description": "과학 실험을 직접 해 보고 오케스트라 공연을 관람하는 행사",
                "themes": ["교육", "공연"],
                "event_type": "Event",
            },
        ]
        for fields in cases:
            with self.subTest(name=fields["name"]):
                categories, summary, is_experience = self._derive(**fields)
                if fields["name"].startswith("코리안"):
                    self.assertFalse(is_experience)
                    self.assertEqual(categories, [])
                    self.assertIsNone(summary)
                else:
                    self.assertTrue(is_experience)
                    self.assertEqual(categories, ["과학·탐구"])
                    self.assertTrue(summary)

    def test_instrument_playing_is_an_art_experience(self):
        categories, summary, is_experience = self._derive(
            name="악기 직접 연주 체험",
            description="어린이가 악기를 직접 연주해 보는 참여형 체험",
            themes=["공연", "교육"],
            event_type="MusicEvent",
        )
        self.assertTrue(is_experience)
        self.assertEqual(categories, ["만들기·예술"])
        self.assertTrue(summary)

    def test_nonpublishable_source_url_never_enables_link_publication(self):
        event = {
            "id": "fixture:nonpublishable-source",
            "location": {"sido": "서울특별시"},
            "age": "7-13",
            "age_bands": ["어린이"],
            "audience": ["가족"],
            "name": "과학 탐구",
            "description": "아이들이 직접 실험하고 관찰하는 과학 체험",
            "themes": ["교육"],
            "event_type": "Event",
            "url": "https://fixture.example/activity",
            "x_nonpublishable_url": True,
        }
        checks = {
            "https://fixture.example/activity": {
                "link_status": "verified",
                "link_checked_at": "2026-10-15T00:00:00Z",
                "link_url": "https://fixture.example/activity",
                "link_reason": None,
            }
        }
        flattened = build_index._flatten(event, link_checks=checks)
        self.assertEqual(flattened.get("link_status"), "blocked")
        self.assertIsNone(flattened.get("link_checked_at"))
        self.assertIsNone(flattened.get("link_url"))
        self.assertTrue(flattened.get("link_reason"))


    def test_explicit_instrument_participation_is_experience(self):
        categories, summary, is_experience = self._derive(
            name="어린이 오케스트라 체험",
            description="아이들이 악기를 직접 연주하고 오케스트라에 참여하는 워크숍",
            themes=["공연", "교육"],
            event_type="MusicEvent",
        )
        self.assertTrue(is_experience)
        self.assertIn("만들기·예술", categories)
        self.assertTrue(summary)

    def test_generic_theme_does_not_invent_a_program(self):
        categories, summary, is_experience = self._derive(
            name="가족 교육 프로그램",
            description="가족을 위한 교육 행사 안내",
            themes=["교육"],
        )
        self.assertFalse(is_experience)
        self.assertEqual(categories, [])
        self.assertIsNone(summary)

    def test_child_restrictions_remain_excluded_even_when_activity_is_hands_on(self):
        event = {
            "age": "성인 전용",
            "age_bands": ["청년"],
            "audience": ["성인"],
            "name": "성인 전용 공방",
            "description": "성인만 참여하는 직접 만들기 프로그램",
            "themes": ["교육"],
            "event_type": "Event",
        }
        categories, summary, is_experience = build_index.derive_experiences(**event)
        self.assertIsInstance(categories, list)
        self.assertTrue(is_experience is False or "만들기·예술" in categories)
        self.assertTrue(summary is None or isinstance(summary, str))

        flattened = build_index._flatten({
            "id": "fixture:adult-craft",
            "location": {"sido": "서울특별시"},
            **event,
        })
        self.assertFalse(flattened["family_friendly"])
        self.assertIn("명시 제한", flattened["family_evidence"])
        self.assertEqual(flattened["experience_categories"], categories)
        self.assertEqual(flattened["is_experience"], is_experience)

    def test_flatten_exposes_source_grounded_experience_fields(self):
        flattened = build_index._flatten({
            "id": "fixture:science",
            "location": {"sido": "서울특별시"},
            "age": "7-13",
            "age_bands": ["어린이"],
            "audience": ["가족"],
            "name": "과학 탐구",
            "description": "아이들이 직접 실험하고 관찰하는 과학 체험",
            "themes": ["교육"],
            "event_type": "Event",
        })
        self.assertTrue(flattened["is_experience"])
        self.assertIn("과학·탐구", flattened["experience_categories"])
        self.assertTrue(flattened["experience_summary"])

    def test_flatten_preserves_price_without_using_price_to_infer_experience(self):
        for price in ("free", 0, 1000, None):
            with self.subTest(price=price):
                flattened = build_index._flatten({
                    "id": f"fixture:price-{price}",
                    "location": {"sido": "서울특별시"},
                    "age": "7-13",
                    "age_bands": ["어린이"],
                    "audience": ["가족"],
                    "name": "과학 실험 체험",
                    "description": "아이들이 직접 실험하고 관찰하는 과학 체험",
                    "themes": ["교육"],
                    "event_type": "Event",
                    "price": price,
                })
                self.assertEqual(flattened["price"], price)
                self.assertTrue(flattened["is_experience"])

        generic = build_index._flatten({
            "id": "fixture:free-generic",
            "location": {"sido": "서울특별시"},
            "age": "7-13",
            "age_bands": ["어린이"],
            "audience": ["가족"],
            "name": "무료 가족 프로그램",
            "description": "가족을 위한 교육 행사 안내",
            "themes": ["교육"],
            "event_type": "Event",
            "price": "free",
        })
        self.assertFalse(generic["is_experience"])

    def _build_publication(self, retention_days: int):
        today = dt.datetime(2026, 10, 15, 1, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
        old_end = (today.date() - dt.timedelta(days=retention_days + 1)).isoformat()
        boundary = (today.date() - dt.timedelta(days=retention_days)).isoformat()
        rows = []
        for event_id, end_date in (("old", old_end), ("boundary", boundary)):
            rows.append((None, {
                "id": event_id, "name": event_id, "description": "행사",
                "event_type": "Event", "themes": [], "age_bands": ["어린이"], "audience": [],
                "start_date": end_date, "end_date": end_date, "status": "Scheduled", "location": {},
                "source": "fixture", "fetched_at": today.isoformat(),
            }, ""))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "search.yaml").write_text(
                json.dumps({"publication_policy": {"retention_days": retention_days}}),
                encoding="utf-8",
            )
            (root / "config" / "regions.yaml").write_text("regions: []\n", encoding="utf-8")
            knowledge = root / "knowledge" / "sources"
            knowledge.mkdir(parents=True)
            sentinel = knowledge / "history.md"
            sentinel.write_text("keep", encoding="utf-8")
            output = root / "web" / "public" / "data"
            old_root, old_out, old_config, old_config_dir = (
                build_index.ROOT, build_index.OUT_DIR, config.ROOT, config.CONFIG_DIR
            )
            try:
                build_index.ROOT, build_index.OUT_DIR = root, output
                config.ROOT, config.CONFIG_DIR = root, root / "config"
                config._load.cache_clear()
                with mock.patch.object(build_index, "iter_events", return_value=rows), \
                     mock.patch.object(build_index, "all_regions", return_value=[]), \
                     mock.patch.object(build_index, "load_link_checks", return_value={}), \
                     mock.patch.object(build_index, "now_kst", return_value=today.isoformat()):
                    build_index.build()
                events = json.loads((output / "events.json").read_text(encoding="utf-8"))
                updated = json.loads((output / "updated.json").read_text(encoding="utf-8"))
                return events, updated, sentinel.read_text(encoding="utf-8")
            finally:
                config._load.cache_clear()
                build_index.ROOT, build_index.OUT_DIR = old_root, old_out
                config.ROOT, config.CONFIG_DIR = old_config, old_config_dir
                config._load.cache_clear()

    def test_publication_retention_excludes_strictly_older_and_keeps_boundary(self):
        events, updated, knowledge = self._build_publication(90)
        self.assertEqual([event["id"] for event in events], ["boundary"])
        self.assertEqual(updated["publication"], {
            "retained": 1, "excluded": 1, "retention_days": 90,
        })
        self.assertEqual(knowledge, "keep")

    def test_publication_retention_reads_config_override(self):
        events, updated, _ = self._build_publication(30)
        self.assertEqual([event["id"] for event in events], ["boundary"])
        self.assertEqual(updated["publication"]["retention_days"], 30)


if __name__ == "__main__":
    unittest.main()
