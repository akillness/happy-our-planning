"""OKF 레코드를 지식 DB(Markdown 파일)에 증분 적재.

- content_hash 동일 → skip(파일 미변경, git diff 0)
- 변경 → 본문 보존하며 파일 갱신, 경로가 바뀌면 옛 파일 제거
- 소스에서 사라진 항목 → 삭제하지 않고 status=archived + x_lastSeen
"""
from __future__ import annotations

from scripts.common.okf import (
    content_hash,
    event_path,
    iter_events,
    to_markdown,
)
from scripts.ingest.base import now_kst


def _index_existing() -> dict[str, tuple]:
    idx = {}
    for path, fm, body in iter_events():
        eid = fm.get("id")
        if eid:
            idx[eid] = (path, fm, body)
    return idx


def upsert(events: list[dict], processed_sources: set[str] | None = None) -> dict:
    existing = _index_existing()
    stats = {"created": 0, "updated": 0, "skipped": 0, "archived": 0}
    seen: set[str] = set()

    for ev in events:
        eid = ev["id"]
        seen.add(eid)
        new_path = event_path(ev)
        if eid in existing:
            old_path, old_fm, old_body = existing[eid]
            if old_fm.get("content_hash") == ev.get("content_hash") and old_fm.get("status") != "archived":
                stats["skipped"] += 1
                continue
            new_path.parent.mkdir(parents=True, exist_ok=True)
            new_path.write_text(to_markdown(ev, old_body), encoding="utf-8")
            if old_path != new_path and old_path.exists():
                old_path.unlink()
            stats["updated"] += 1
        else:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            new_path.write_text(to_markdown(ev), encoding="utf-8")
            stats["created"] += 1

    # archived 정책: 처리한 소스의 기존 항목 중 이번에 안 보인 것
    if processed_sources:
        for eid, (path, fm, body) in existing.items():
            if eid in seen:
                continue
            if fm.get("source") not in processed_sources:
                continue
            if fm.get("status") == "archived":
                continue
            fm["status"] = "archived"
            fm["x_lastSeen"] = now_kst()
            fm["content_hash"] = content_hash(fm)
            path.write_text(to_markdown(fm, body), encoding="utf-8")
            stats["archived"] += 1

    return stats


if __name__ == "__main__":
    import sys
    print("upsert는 보통 to_okf 오케스트레이터가 호출합니다.", file=sys.stderr)
