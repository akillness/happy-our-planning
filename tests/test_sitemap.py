"""sitemap.xml 빌드 테스트."""
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.build import build_sitemap


CANONICAL_ORIGIN = "https://happy-our-planning.vercel.app"


class _MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "canonical":
            self.values["canonical"] = attrs.get("href")
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key in {"og:url", "og:image"}:
                self.values[key] = attrs.get("content")


class TestSitemap(unittest.TestCase):
    def _events(self):
        return [
            {"id": "kopis:PF1", "name": "가", "start_date": "2026-11-01T00:00:00+09:00"},
            {"id": "tourapi:2", "name": "나", "start_date": None},
            {"id": "", "name": "무시"},
        ]

    def _locs(self, out):
        root = ET.parse(out).getroot()
        ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
        return [u.find(f"{ns}loc").text for u in root.findall(f"{ns}url")]

    def test_default_origin_preserves_retained_event_count_and_safe_paths(self):
        events = [
            {"id": "kopis:PF/1?x", "start_date": "2026-11-01T00:00:00+09:00"},
            {"id": "tourapi:2", "start_date": None},
            {"id": "", "start_date": "2026-11-02"},
            {"name": "missing id", "start_date": "2026-11-03"},
        ]
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "sitemap.xml"
            result = build_sitemap.build(out_dir=tmp, events=events, today="2026-10-15")
            locs = self._locs(out)

        self.assertEqual(result["urls"], 3)
        self.assertEqual(set(locs), {
            f"{CANONICAL_ORIGIN}/",
            f"{CANONICAL_ORIGIN}/events/kopis_PF_1_x.html",
            f"{CANONICAL_ORIGIN}/events/tourapi_2.html",
        })
        self.assertTrue(all(url.startswith(f"{CANONICAL_ORIGIN}/") for url in locs))
        self.assertNotIn("https://notchima.vercel.app/", locs)

    def test_static_metadata_and_robots_share_canonical_origin(self):
        public = build_sitemap.ROOT / "web" / "public"
        parser = _MetadataParser()
        parser.feed((public / "index.html").read_text(encoding="utf-8"))
        robots = (public / "robots.txt").read_text(encoding="utf-8").splitlines()
        sitemap_line = next(line for line in robots if line.startswith("Sitemap:"))

        self.assertEqual(parser.values["canonical"], f"{CANONICAL_ORIGIN}/")
        self.assertEqual(parser.values["og:url"], f"{CANONICAL_ORIGIN}/")
        self.assertEqual(parser.values["og:image"],
                         f"{CANONICAL_ORIGIN}/assets/family-outing.webp")
        self.assertEqual(sitemap_line, f"Sitemap: {CANONICAL_ORIGIN}/sitemap.xml")

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
