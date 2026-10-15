"""정적 sitemap.xml 빌드 (SEO).

web/public/sitemap.xml — 인덱스 + 행사별 정적 상세(events/*.html)를 나열한다.
입력: web/public/data/events.json (이미 retention 필터된 공개 후보).
출력은 멱등(정렬된 URL 집합, lastmod는 행사 시작일 또는 빌드일).
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.common.config import ROOT
from scripts.common.okf import _safe_id

OUT = ROOT / "web" / "public" / "sitemap.xml"
EVENTS_JSON = ROOT / "web" / "public" / "data" / "events.json"
SITE = "https://happy-our-planning.vercel.app"

NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
ET.register_namespace("", NS)


def _lastmod(event: dict, today: str) -> str:
    start = str(event.get("start_date") or "")[:10]
    if len(start) == 10 and start <= "9999-12-31":
        try:
            y, m, d = start.split("-")
            int(y)
            int(m)
            int(d)
            return start
        except ValueError:
            pass
    return today


def build(out_dir: Path | str | None = None, events: list[dict] | None = None,
          today: str | None = None, site: str = SITE) -> dict:
    out = Path(out_dir) if out_dir else OUT
    if out.is_dir():
        out = out / "sitemap.xml"
    if events is None:
        events = json.loads(EVENTS_JSON.read_text(encoding="utf-8")) if EVENTS_JSON.exists() else []
    if today is None:
        from scripts.common.dates import now_kst
        today = now_kst()[:10]

    urls: dict[str, str] = {f"{site}/": today}
    for event in events:
        eid = event.get("id")
        if not eid:
            continue
        urls[f"{site}/events/{_safe_id(eid)}.html"] = _lastmod(event, today)

    urlset = ET.Element(f"{{{NS}}}urlset")
    for loc in sorted(urls):
        url_el = ET.SubElement(urlset, f"{{{NS}}}url")
        loc_el = ET.SubElement(url_el, f"{{{NS}}}loc")
        loc_el.text = loc
        last_el = ET.SubElement(url_el, f"{{{NS}}}lastmod")
        last_el.text = urls[loc]
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = ET.tostring(urlset, encoding="utf-8", xml_declaration=True)
    out.write_bytes(payload)
    print(f"sitemap: {len(urls)}개 URL")
    return {"path": str(out), "urls": len(urls)}


def main(argv: list[str]) -> int:
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
