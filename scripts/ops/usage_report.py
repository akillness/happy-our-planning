"""무료티어 사용률 추정 리포트 (S4-T3 · B11 · C7).

월 0원 제약(C7)을 지키려면 일별 cron 수집이 각 소스의 무료 한도 안에 머무는지
주기적으로 확인해야 한다. 이 리포트는 최근 수집 스냅샷(`knowledge/sources/*.md`)의
collected 카운트로 호출량을 보수적으로 추정하고, 일·월 한도 대비 여유율과
경고 상태(ok/warn/over)를 산출한다.

순수 계산(`estimate_source`/`build_report`)과 IO(`load_runs`/`main`)를 분리해
파일 없이도 테스트된다. 한도 초과 추정 시 rc==1로 CI/cron이 배치 지연·규칙 강등을
결정할 수 있게 한다.

사용:
    python -m scripts.ops.usage_report          # 텍스트 리포트
    python -m scripts.ops.usage_report --json   # 기계판독 JSON
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import yaml

from scripts.common import config
from scripts.common.config import ROOT

SOURCES_DIR = ROOT / "knowledge" / "sources"
PAGE_SIZE = 100          # 페이징 API(rows/numOfRows)의 페이지 크기
CRON_RUNS_PER_DAY = 1    # ingest.yml cron: 0 18 * * * (일 1회)
DAYS_PER_MONTH = 30
WARN_RATIO = 0.8         # 일 한도의 80% 초과 시 경고


def _num(text: object) -> int | None:
    """'월 1,000 검색' 같은 자유 문자열에서 첫 정수를 추출(콤마 제거)."""
    if text is None:
        return None
    m = re.search(r"[\d,]+", str(text))
    return int(m.group(0).replace(",", "")) if m else None


def estimate_source(source: dict, collected: int, search_cfg: dict | None = None) -> dict:
    """소스 1개의 1회 수집 호출량 → 일·월 추정 + 한도 대비 상태.

    - 페이징 공공 API(kopis/tourapi): 호출 ≈ ceil(collected / PAGE_SIZE)(최소 1).
    - 웹검색(websearch): 호출 ≈ search.yaml의 질의 개수(제공자 월 크레딧과 비교).
    """
    key = source.get("key", "?")
    daily_limit = source.get("rate_limit_per_day")

    monthly_free = None
    if key == "websearch":
        cfg = search_cfg or {}
        queries = cfg.get("queries") or []
        calls_per_run = max(len(queries), 1)
        prov = (source.get("provider")
                or cfg.get("default_provider"))
        provider_cfg = (cfg.get("providers") or {}).get(prov, {})
        monthly_free = _num(provider_cfg.get("free_tier"))
    else:
        calls_per_run = max(1, math.ceil(collected / PAGE_SIZE)) if collected else 1

    daily = calls_per_run * CRON_RUNS_PER_DAY
    monthly = daily * DAYS_PER_MONTH

    daily_ratio = (daily / daily_limit) if daily_limit else None
    monthly_ratio = (monthly / monthly_free) if monthly_free else None
    ratios = [r for r in (daily_ratio, monthly_ratio) if r is not None]
    worst = max(ratios) if ratios else 0.0

    if worst > 1.0:
        status = "over"
    elif worst >= WARN_RATIO:
        status = "warn"
    else:
        status = "ok"

    return {
        "source": key,
        "collected": collected,
        "calls_per_run": calls_per_run,
        "daily_estimate": daily,
        "monthly_estimate": monthly,
        "daily_limit": daily_limit,
        "monthly_free": monthly_free,
        "daily_ratio": round(daily_ratio, 3) if daily_ratio is not None else None,
        "monthly_ratio": round(monthly_ratio, 3) if monthly_ratio is not None else None,
        "status": status,
    }


def build_report(sources: list[dict], collected_by_source: dict[str, int],
                 search_cfg: dict | None = None) -> dict:
    """소스 카탈로그 + 최근 collected 카운트 → 사용률 리포트(정렬 결정적)."""
    rows = []
    for s in sources:
        if not s.get("enabled", True):
            continue
        key = s.get("key", "?")
        rows.append(estimate_source(s, collected_by_source.get(key, 0), search_cfg))
    rows.sort(key=lambda r: r["source"])
    over = [r["source"] for r in rows if r["status"] == "over"]
    warn = [r["source"] for r in rows if r["status"] == "warn"]
    return {
        "rows": rows,
        "over": over,
        "warn": warn,
        "within_free_tier": not over,
    }


def load_runs(sources_dir: Path | None = None) -> dict[str, int]:
    """knowledge/sources/<src>-<date>.md 중 소스별 최신 스냅샷의 collected 합산."""
    d = sources_dir or SOURCES_DIR
    latest: dict[str, tuple[str, int]] = {}
    if not d.exists():
        return {}
    for path in sorted(d.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        fm = yaml.safe_load(text[3:end]) if end != -1 else None
        if not isinstance(fm, dict):
            continue
        src = str(fm.get("source") or "")
        date = str(fm.get("date") or "")
        collected = int(fm.get("collected") or 0)
        if src and (src not in latest or date >= latest[src][0]):
            latest[src] = (date, collected)
    return {src: cnt for src, (_, cnt) in latest.items()}


def _render(report: dict) -> str:
    lines = ["무료티어 사용률 추정 (cron 일1회 · 30일 기준)", ""]
    lines.append(f"{'source':<12}{'calls/run':>10}{'daily':>8}{'limit':>8}"
                 f"{'month':>8}{'free':>9}  status")
    for r in report["rows"]:
        lines.append(
            f"{r['source']:<12}{r['calls_per_run']:>10}{r['daily_estimate']:>8}"
            f"{str(r['daily_limit'] or '-'):>8}{r['monthly_estimate']:>8}"
            f"{str(r['monthly_free'] or '-'):>9}  {r['status']}")
    lines.append("")
    if report["over"]:
        lines.append("⚠ 한도 초과 추정: " + ", ".join(report["over"])
                     + " — 배치 지연/규칙 강등 권고(C7)")
    elif report["warn"]:
        lines.append("⚠ 한도 임박(>80%): " + ", ".join(report["warn"]))
    else:
        lines.append("✓ 전 소스 무료티어 한도 내(C7 충족)")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    sources = config.sources()
    search_cfg = config.search_config()
    collected = load_runs()
    report = build_report(sources, collected, search_cfg)
    if "--json" in argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_render(report))
    return 0 if report["within_free_tier"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
