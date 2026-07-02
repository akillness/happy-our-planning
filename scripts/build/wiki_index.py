"""knowledge/index.md 의 REGIONS/THEMES/SOURCES 블록을 집계로 갱신(llm-wiki).

각 블록은 `<!-- X:START -->` ~ `<!-- X:END -->` 사이를 자동 생성으로 교체한다.
"""
from __future__ import annotations

import re
from collections import Counter

from scripts.common.config import ROOT
from scripts.common.okf import iter_events

INDEX = ROOT / "knowledge" / "index.md"


def _replace_block(text: str, name: str, lines: list[str]) -> str:
    start, end = f"<!-- {name}:START -->", f"<!-- {name}:END -->"
    body = "\n".join(lines) if lines else "- (없음)"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    return pattern.sub(f"{start}\n{body}\n{end}", text)


def build() -> None:
    regions: Counter = Counter()
    themes: Counter = Counter()
    sources: Counter = Counter()
    last_seen: dict[str, str] = {}  # 소스별 최신 fetched_at(YYYY-MM-DD)
    active = 0
    for _p, fm, _b in iter_events():
        if fm.get("status") == "archived":
            continue
        active += 1
        loc = fm.get("location") or {}
        if loc.get("sido"):
            regions[loc["sido"]] += 1
        for t in fm.get("themes") or []:
            themes[t] += 1
        if fm.get("source"):
            src = fm["source"]
            sources[src] += 1
            fetched = (fm.get("fetched_at") or "")[:10]
            if fetched and fetched > last_seen.get(src, ""):
                last_seen[src] = fetched

    region_lines = [f"- {k} — {v}건" for k, v in sorted(regions.items(), key=lambda x: -x[1])]
    theme_lines = [f"- {k} — {v}건" for k, v in sorted(themes.items(), key=lambda x: -x[1])]
    # 최근 갱신은 소스별 실제 fetched_at에서 파생 → 데이터 무변경 시 index.md 재생성 멱등.
    source_lines = [
        f"- {k} — {v}건" + (f" (최근 갱신 {last_seen[k]})" if last_seen.get(k) else "")
        for k, v in sorted(sources.items(), key=lambda x: -x[1])
    ]

    text = INDEX.read_text(encoding="utf-8")
    text = _replace_block(text, "REGIONS", region_lines)
    text = _replace_block(text, "THEMES", theme_lines)
    text = _replace_block(text, "SOURCES", source_lines)
    INDEX.write_text(text, encoding="utf-8")
    print(f"wiki_index: active={active} regions={len(regions)} themes={len(themes)} sources={len(sources)}")


if __name__ == "__main__":
    build()
