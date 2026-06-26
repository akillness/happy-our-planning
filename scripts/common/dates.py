"""KST(UTC+9) 시간대 상수 + 날짜→KST ISO 결합 헬퍼.

어댑터마다 흩어져 있던 `dt.timezone(dt.timedelta(hours=9))` 리터럴과
`_kst_date` 공통 꼬리(시각 결합 → isoformat)를 한 곳으로 모은다.
"""
from __future__ import annotations

import datetime as dt

KST = dt.timezone(dt.timedelta(hours=9))


def now_kst() -> str:
    """현재 KST 시각 ISO 8601(초 단위)."""
    return dt.datetime.now(KST).isoformat(timespec="seconds")


def combine_kst(day: dt.date, end: bool = False) -> str:
    """date → KST ISO 8601. end=True면 23:59:59, 아니면 00:00:00."""
    t = dt.time(23, 59, 59) if end else dt.time(0, 0, 0)
    return dt.datetime.combine(day, t, KST).isoformat()
