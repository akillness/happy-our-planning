"""신규 주기/보강/교차검증/메모리 모듈 회귀 가드 (오프라인)."""
import datetime as dt
import json
import unittest
from pathlib import Path

from scripts.ops import cross_check_links, prune_caches, refresh_cadence
from scripts.ingest import enrich_details


class TestRefreshCadence(unittest.TestCase):
    def test_daily_always(self):
        tiers = refresh_cadence.due_tiers(dt.date(2026, 10, 15))  # 수요일
        self.assertEqual(tiers, ["daily"])

    def test_weekly_monthly_yearly(self):
        self.assertIn("weekly", refresh_cadence.due_tiers(dt.date(2026, 10, 19)))  # 월요일
        self.assertIn("monthly", refresh_cadence.due_tiers(dt.date(2026, 11, 1)))
        self.assertEqual(
            refresh_cadence.due_tiers(dt.date(2026, 1, 1)),
            ["daily", "monthly", "yearly"] if dt.date(2026, 1, 1).weekday() != 0
            else ["daily", "weekly", "monthly", "yearly"],
        )


class TestEnrichReport(unittest.TestCase):
    def test_report_offline_shape(self):
        report = enrich_details.build_report()
        for key in ("websearch_hits", "thin_or_undated_candidates", "matchable"):
            self.assertIn(key, report)


class TestCrossCheck(unittest.TestCase):
    def test_report_shape(self):
        report = cross_check_links.build_report()
        for key in ("events_scanned", "unique_urls", "duplicate_groups", "orphan_cache_entries"):
            self.assertIn(key, report)
        self.assertIsInstance(report["plaintext_url_in_cache"], bool)


class TestMemoryAudit(unittest.TestCase):
    def test_legacy_free_shape(self):
        report = prune_caches.audit()
        self.assertIn("legacy_free", report)
        self.assertIn("websearch_cache_ok", report)
        self.assertLessEqual(report["websearch_cache_slots"], 300)


class TestMascotAssets(unittest.TestCase):
    def test_mascot_and_codex_menu_icons(self):
        root = Path(__file__).resolve().parents[1]
        mascot = root / "web" / "public" / "assets" / "notchima-mascot-256.png"
        self.assertTrue(mascot.exists(), "마스코트 정적 자산 필요")
        icons_dir = root / "web" / "public" / "assets" / "icons"
        html = (root / "web" / "public" / "index.html").read_text(encoding="utf-8")
        for name in ("menu-locate", "menu-language", "menu-filter", "menu-results", "menu-ai", "menu-reset"):
            self.assertTrue((icons_dir / f"{name}.webp").exists(), f"{name}.webp 필요")
            self.assertTrue((icons_dir / f"{name}.png").exists(), f"{name}.png 필요")
            self.assertIn(f"./assets/icons/{name}.webp", html)
        css = (root / "web" / "public" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 640px)", css)
        self.assertIn(".icon-btn .control-label", css)
        self.assertIn("prefers-reduced-motion", css)
        manifest = json.loads((root / "assets" / "design" / "family-assets.json").read_text(encoding="utf-8"))
        self.assertIn("private-codex", str(manifest.get("provider", "")))
        self.assertGreaterEqual(len(manifest.get("new_assets", [])), 9)


if __name__ == "__main__":
    unittest.main()
