"""sitemap.xml 빌드 테스트."""
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.build import build_sitemap


class TestSitemap(unittest.TestCase):
    def _events(self):
        return [
            {"id": "kopis:PF1", "name": "가", "start_date": "2026-11-01T00:00:00+09:00"},
            {"id": "tourapi:2", "name": "나", "start_date": None},
            {"id": "", "name": "무시"},
        ]

    def test_urls_sorted_and_lastmod(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "sitemap.xml"
            result = build_sitemap.build(out_dir=tmp, events=self._events(),
                                         today="2026-10-15", site="https://x.test")
            self.assertEqual(result["urls"], 3)
            root = ET.parse(out).getroot()
            ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
            locs = [u.find(f"{ns}loc").text for u in root.findall(f"{ns}url")]
            self.assertEqual(locs, sorted(locs))
            self.assertIn("https://x.test/", locs)
            self.assertIn("https://x.test/events/kopis_PF1.html", locs)
            mods = {u.find(f"{ns}loc").text: u.find(f"{ns}lastmod").text
                    for u in root.findall(f"{ns}url")}
            self.assertEqual(mods["https://x.test/events/kopis_PF1.html"], "2026-11-01")
            self.assertEqual(mods["https://x.test/events/tourapi_2.html"], "2026-10-15")


if __name__ == "__main__":
    unittest.main()
