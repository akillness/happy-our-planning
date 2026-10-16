"""링크 교차검증 리포트 (오프라인, 네트워크 호출 없음).

검사 항목:
1. scheme: HTTPS가 아닌 원본 URL 목록
2. duplicates: 같은 URL을 공유하는 행사 id 그룹 (상호 교차확인용)
3. source_mismatch: url과 source_url이 다른 레코드
4. cache_consistency: link-checks.json 캐시 키가 현재 공개 집합에 없는 잔류 항목 수
   + 원본 URL 평문이 캐시에 저장되어 있는지(정책 위반) 확인
산출: web/public/data/link-crosscheck.json (파생물, 커밋되어 투명 공개)
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from urllib.parse import urlsplit

from scripts.common.config import ROOT
from scripts.common.links import CACHE_PATH, _url_key
from scripts.common.okf import iter_events

OUT_PATH = ROOT / "web" / "public" / "data" / "link-crosscheck.json"


def build_report() -> dict:
    seen: dict[str, list[str]] = collections.defaultdict(list)
    non_https: list[dict] = []
    source_url_non_https: list[dict] = []
    api_detail_pairs = 0
    total = 0
    for _path, fm, _body in iter_events():
        if fm.get("status") == "archived":
            continue
        total += 1
        url = str(fm.get("url") or "")
        src_url = str(fm.get("source_url") or "")
        if url:
            seen[url].append(str(fm.get("id")))
            if urlsplit(url).scheme.lower() != "https":
                non_https.append({"id": fm.get("id"), "url": url[:120]})
        if src_url:
            if urlsplit(src_url).scheme.lower() != "https":
                source_url_non_https.append({"id": fm.get("id")})
            if src_url != url:
                # KOPIS: url=상세페이지, source_url=원천 API — 출처 분리형으로 정상
                api_detail_pairs += 1
    duplicates = [
        {"url": url[:120], "ids": ids, "count": len(ids)}
        for url, ids in seen.items() if len(ids) > 1
    ]
    # 캐시 정합성 (원본 URL 평문 저장 금지 정책 확인)
    orphan = 0
    plaintext_url_stored = False
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        checks = cache.get("checks") if isinstance(cache, dict) else None
        checks = checks if isinstance(checks, dict) else {}
        live_keys = {_url_key(u) for u in seen}
        orphan = sum(1 for k in checks if k not in live_keys)
        blob = json.dumps(checks, ensure_ascii=False)
        # 해시(64hex) 키만 허용 — http:// 또는 www. 평문이 값/키에 있으면 위반
        plaintext_url_stored = ("http://" in blob or "https://" in blob)
    except (OSError, ValueError):
        checks = {}
    report = {
        "events_scanned": total,
        "unique_urls": len(seen),
        "non_https": non_https[:20],
        "non_https_count": len(non_https),
        "duplicates": duplicates[:20],
        "duplicate_groups": len(duplicates),
        "source_url_non_https_count": len(source_url_non_https),
        "api_detail_separated_pairs": api_detail_pairs,
        "cache_entries": len(checks),
        "orphan_cache_entries": orphan,
        "plaintext_url_in_cache": plaintext_url_stored,
    }
    return report


def write_report() -> Path:
    report = build_report()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(OUT_PATH)
    return OUT_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Notchima offline link cross-check")
    parser.add_argument("--write", action="store_true", default=True)
    args = parser.parse_args(argv)
    path = write_report() if args.write else None
    report = build_report()
    if path:
        report["report_path"] = str(path)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
