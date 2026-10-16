"""검색내용으로 일정 상세 보충 (오프라인, 네트워크 호출 없음).

- 대상: knowledge flat-file 중 description이 비었거나 40자 미만인 행사,
  또는 end_date가 없고 검색 hit에서 문맥 날짜를 추출할 수 있는 행사.
- 근거: raw/websearch/sample-* fixture + raw/websearch/_cache.json(로컬, 미커밋)의
  공통 hit(title/url/snippet/start_date/end_date)를 같은 URL 우선,
  없으면 정규화 이름+시도 일치로 매칭한다.
- 기본은 리포트만(dry-run). --apply 시에만 flat-file을 갱신하고
  x_enriched_from / x_enriched_at provenance를 남긴다. content_hash는 재계산한다.
- 원문 훼손 금지: description이 이미 충분하면 덮어쓰지 않고, snippet은
  최대 500자로 잘라 [검색 보충] 접두와 함께 추가한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.common.config import ROOT
from scripts.common.okf import content_hash, iter_events, parse_markdown, to_markdown

KST = ZoneInfo("Asia/Seoul")
RAW_WS = ROOT / "raw" / "websearch"
MIN_DESC = 40
SNIPPET_CAP = 500


def _norm_name(value: str) -> str:
    cleaned = re.sub(r"\s+", "", str(value or "").casefold())
    return re.sub(r"[^\w가-힣]", "", cleaned)[:60]


def _load_hits() -> list[dict]:
    hits: list[dict] = []
    if not RAW_WS.exists():
        return hits
    for path in sorted(RAW_WS.glob("sample-*")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = data if isinstance(data, list) else data.get("results", [])
        if isinstance(rows, list):
            hits.extend([r for r in rows if isinstance(r, dict)])
    cache_path = RAW_WS / "_cache.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        for slot in (cache.get("items") or {}).values():
            resp = (slot or {}).get("response") or {}
            for r in resp.get("results", []) or []:
                if isinstance(r, dict) and r.get("url"):
                    hits.append({
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": (r.get("text") or r.get("summary") or "")[:SNIPPET_CAP],
                    })
    except (OSError, ValueError):
        pass
    return hits


def build_report() -> dict:
    hits = _load_hits()
    by_url = {str(h.get("url", "")).strip(): h for h in hits if h.get("url")}
    by_name: dict[tuple[str, str], dict] = {}
    for h in hits:
        key = (_norm_name(h.get("title", "")), str(h.get("sido", "") or ""))
        if key[0] and key not in by_name:
            by_name[key] = h
    candidates = 0
    matchable = 0
    for _path, fm, _body in iter_events():
        if fm.get("status") == "archived":
            continue
        desc = str(fm.get("description") or "")
        thin = (not desc.strip()) or len(desc.strip()) < MIN_DESC
        missing_end = not fm.get("end_date")
        if not (thin or missing_end):
            continue
        candidates += 1
        url = str(fm.get("url") or "")
        hit = by_url.get(url.strip())
        if hit is None:
            key = (_norm_name(fm.get("name", "")), str((fm.get("location") or {}).get("sido", "") or ""))
            # sido가 비어 있어도 이름만으로 매칭 시도
            for (n, _s), h in by_name.items():
                if n and n == key[0]:
                    hit = h
                    break
        if hit and str(hit.get("snippet") or "").strip():
            matchable += 1
    return {
        "websearch_hits": len(hits),
        "thin_or_undated_candidates": candidates,
        "matchable": matchable,
        "policy": f"dry-run report only (MIN_DESC={MIN_DESC})",
    }


def apply_enrichment() -> dict:
    hits = _load_hits()
    by_url = {str(h.get("url", "")).strip(): h for h in hits if h.get("url")}
    updated = 0
    skipped = 0
    for path, fm, body in iter_events():
        if fm.get("status") == "archived":
            continue
        desc = str(fm.get("description") or "")
        thin = (not desc.strip()) or len(desc.strip()) < MIN_DESC
        missing_end = not fm.get("end_date")
        if not (thin or missing_end):
            continue
        url = str(fm.get("url") or "")
        hit = by_url.get(url.strip())
        if hit is None:
            skipped += 1
            continue
        snippet = str(hit.get("snippet") or "").strip()[:SNIPPET_CAP]
        if not snippet:
            skipped += 1
            continue
        changed = False
        if thin:
            base = desc.strip()
            addition = f"[검색 보충] {snippet}"
            fm["description"] = f"{base}\n{addition}".strip()[:2000] if base else addition
            changed = True
        if missing_end and hit.get("end_date"):
            fm["end_date"] = hit["end_date"]
            changed = True
        if changed:
            fm["x_enriched_from"] = "websearch"
            fm["x_enriched_at"] = dt.datetime.now(KST).isoformat()
            fm["content_hash"] = content_hash(fm)
            path.write_text(to_markdown(fm, body), encoding="utf-8")
            updated += 1
        else:
            skipped += 1
    return {"updated": updated, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Notchima detail enrichment (offline)")
    parser.add_argument("--apply", action="store_true", help="flat-file에 실제 보충 기록")
    args = parser.parse_args(argv)
    if args.apply:
        result = apply_enrichment()
    else:
        result = build_report()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
