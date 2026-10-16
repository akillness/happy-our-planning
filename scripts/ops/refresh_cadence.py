"""주기별 갱신 계층 판정 (오프라인 순수 함수 + 리포트 기록).

단일 일일 cron(C11) 안에서 동작한다. 스케줄을 늘리지 않고,
실행일(KST)이 월/주/일/연 경계에 해당하면 해당 tier 작업을 함께 수행한다.
네트워크를 호출하지 않는다.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.common.config import ROOT

KST = ZoneInfo("Asia/Seoul")
STATUS_PATH = ROOT / "knowledge" / "sources" / "refresh-cadence.json"


def due_tiers(today: dt.date | None = None) -> list[str]:
    """실행일에 수행할 tier 목록. daily는 항상 포함."""
    day = today or dt.datetime.now(KST).date()
    tiers = ["daily"]
    if day.weekday() == 0:  # 월요일
        tiers.append("weekly")
    if day.day == 1:
        tiers.append("monthly")
    if day.month == 1 and day.day == 1:
        tiers.append("yearly")
    return tiers


def write_status(tiers: list[str]) -> Path:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": dt.datetime.now(KST).isoformat(),
        "due_tiers": tiers,
        "schedule": "single daily cron 03:00 KST (C11)",
    }
    tmp = STATUS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATUS_PATH)
    return STATUS_PATH


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Notchima refresh cadence (offline)")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (KST, 기본 오늘)")
    args = parser.parse_args(argv)
    day = dt.date.fromisoformat(args.date) if args.date else None
    tiers = due_tiers(day)
    path = write_status(tiers)
    print(json.dumps({"due_tiers": tiers, "status": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
