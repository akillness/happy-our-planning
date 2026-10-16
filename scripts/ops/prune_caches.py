"""레거시 없는 메모리 관리: 캐시 상한 감사 + 파생물 구분 (오프라인).

- websearch _cache.json: 최대 300 슬롯 유지 (ingest/websearch._save_cache와 동일 상한)
- link-checks.json: 현재 공개 집합에 없는 해시 증명 prune은 checker가 수행.
  여기서는 크기/키 형식만 감사한다.
- events.db: 파생물(재생성 가능). 저장소에 커밋되어 있으면 LEGACY 위반으로 보고.
- __pycache__/.mypy_cache: 로컬 부산물. 크기와 존재만 보고하고 삭제는 --prune 시에만.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from scripts.common.config import ROOT

WS_CACHE = ROOT / "raw" / "websearch" / "_cache.json"
LINK_CACHE = ROOT / "web" / "public" / "data" / "link-checks.json"
EVENTS_DB = ROOT / "web" / "public" / "data" / "events.db"


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def audit() -> dict:
    ws_slots = 0
    try:
        payload = json.loads(WS_CACHE.read_text(encoding="utf-8"))
        items = payload.get("items") if isinstance(payload, dict) else None
        ws_slots = len(items) if isinstance(items, dict) else 0
    except (OSError, ValueError):
        ws_slots = 0
    link_entries = 0
    try:
        payload = json.loads(LINK_CACHE.read_text(encoding="utf-8"))
        checks = payload.get("checks") if isinstance(payload, dict) else None
        link_entries = len(checks) if isinstance(checks, dict) else 0
    except (OSError, ValueError):
        link_entries = 0
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--", "web/public/data/events.db", ".mypy_cache"],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
        ).stdout.strip().splitlines()
        tracked = [t for t in tracked if t.strip()]
    except (OSError, ValueError):
        tracked = []
    return {
        "websearch_cache_slots": ws_slots,
        "websearch_cache_limit": 300,
        "websearch_cache_ok": ws_slots <= 300,
        "link_cache_entries": link_entries,
        "pycache_bytes": _dir_size(ROOT / "scripts" / "__pycache__"),
        "mypy_cache_bytes": _dir_size(ROOT / ".mypy_cache"),
        "events_db_bytes": EVENTS_DB.stat().st_size if EVENTS_DB.exists() else 0,
        "events_db_tracked_legacy": bool([t for t in tracked if "events.db" in t]),
        "tracked_legacy_files": tracked,
        "legacy_free": not any("events.db" in t for t in tracked),
    }


def prune() -> dict:
    report = audit()
    # websearch 캐시 상한 강제
    try:
        payload = json.loads(WS_CACHE.read_text(encoding="utf-8"))
        items = payload.get("items") if isinstance(payload, dict) else None
        if isinstance(items, dict) and len(items) > 300:
            ordered = sorted(items.items(), key=lambda kv: str((kv[1] or {}).get("fetched_at") or ""))
            payload["items"] = dict(ordered[-300:])
            WS_CACHE.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            report["websearch_pruned"] = True
    except (OSError, ValueError):
        pass
    for stale in [ROOT / "scripts" / "__pycache__"]:
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)
            report["pycache_pruned"] = True
    report.update(audit())
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Notchima memory/legacy audit (offline)")
    parser.add_argument("--prune", action="store_true")
    args = parser.parse_args(argv)
    report = prune() if args.prune else audit()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("legacy_free", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
