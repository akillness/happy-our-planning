"""정규화 오케스트레이터: 활성 소스 → 어댑터 수집/매핑 → upsert → 수집 로그.

사용:
    python -m scripts.normalize.to_okf            # 활성 소스 전체
    python -m scripts.normalize.to_okf kopis      # 특정 소스만
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from scripts.common.config import ROOT, sources
from scripts.ingest.base import now_kst
from scripts.ingest.kopis import KopisAdapter
from scripts.ingest.tourapi import TourApiAdapter
from scripts.ingest.websearch import WebSearchAdapter
from scripts.normalize.upsert import upsert
from scripts.normalize import geocode

ADAPTERS = {
    "kopis": KopisAdapter,
    "tourapi": TourApiAdapter,
    "websearch": WebSearchAdapter,
}

SOURCES_LOG_DIR = ROOT / "knowledge" / "sources"


def _write_log(key: str, count: int, stats: dict, error: str | None = None) -> None:
    SOURCES_LOG_DIR.mkdir(parents=True, exist_ok=True)
    date = dt.date.today().isoformat()
    path = SOURCES_LOG_DIR / f"{key}-{date}.md"
    body = (
        f"---\nsource: {key}\ndate: {date}\nfetched: {now_kst()}\n"
        f"collected: {count}\ncreated: {stats.get('created',0)}\n"
        f"updated: {stats.get('updated',0)}\nskipped: {stats.get('skipped',0)}\n"
        f"archived: {stats.get('archived',0)}\nerror: {error or ''}\n---\n\n"
        f"# {key} 수집 로그 {date}\n\n"
        f"- 수집(OKF): {count}\n- 신규: {stats.get('created',0)}\n"
        f"- 갱신: {stats.get('updated',0)}\n- 변경없음: {stats.get('skipped',0)}\n"
        f"- 아카이브: {stats.get('archived',0)}\n"
    )
    path.write_text(body, encoding="utf-8")


def run(only: list[str] | None = None, *, use_network: bool = False) -> dict:
    enabled = [s["key"] for s in sources() if s.get("enabled")]
    targets = [k for k in enabled if (not only or k in only)]
    summary = {}
    for key in targets:
        adapter_cls = ADAPTERS.get(key)
        if not adapter_cls:
            print(f"[{key}] 어댑터 없음 — skip")
            continue
        try:
            events = adapter_cls().collect()
            events = geocode.enrich(events, use_network=use_network)
            stats = upsert(events, processed_sources={key})
            stats = upsert(events, processed_sources={key})
            _write_log(key, len(events), stats)
            summary[key] = {"collected": len(events), **stats}
            print(f"[{key}] {len(events)}건 → {stats}")
        except Exception as exc:  # 소스 격리
            _write_log(key, 0, {}, error=str(exc))
            summary[key] = {"error": str(exc)}
            print(f"[{key}] 실패: {exc}")
    return summary


if __name__ == "__main__":
    run(sys.argv[1:] or None)
