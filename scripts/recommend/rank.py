"""규칙 기반 추천/랭킹 + 폴백 주간 플래너 (docs/08 1단계).

LLM 없이도 동작하는 설명가능 추천. LLM 플래너 실패 시 폴백으로도 사용.
사용:
    python -m scripts.recommend.rank                 # 기본 데모 프로필
    python -m scripts.recommend.rank profile.json    # 프로필 파일
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

from scripts.common.config import ROOT

EVENTS_JSON = ROOT / "web" / "public" / "data" / "events.json"

# 추천 점수 가중치(튜닝 단일 지점). score_event가 이 상수만 조합한다.
W_REGION = 3       # 프로필 지역(시/도) 일치
W_THEME = 2        # 테마 일치 1건당
W_AGE = 2          # 나이대 일치
W_FREE = 1         # 무료
W_OPEN = 2         # 신청가능(status=Open)
W_NEAR = 2         # 도보·근거리(<=5km)
W_NEARISH = 1      # 근거리(<=30km)
W_AVAIL = 2        # 가용일 내 개최
PAID_EXCLUDE = -1.0  # free_only인데 유료 → 후보 제외 신호

DEMO_PROFILE = {
    "regions": ["서울특별시"],
    "age_band": "어린이",
    "themes": ["공연", "교육"],
    "available_dates": ["2026-07-18", "2026-07-19", "2026-07-20"],
    "prefs": {"free_only": True, "max_per_day": 2, "near": {"lat": 37.57, "lng": 126.97}},
}


@dataclass
class ScoredEvent:
    """점수화된 행사 후보 — 점수와 설명가능 근거를 행사에 결속한다.

    `to_record()`는 UI/플래너/ai_planner가 소비하는 평면 dict(`_score`/`_reasons`
    부가)을 산출 — 기존 출력 계약을 보존한다.
    """

    event: dict
    score: float
    reasons: list[str] = field(default_factory=list)

    def to_record(self) -> dict:
        return {**self.event, "_score": round(self.score, 2), "_reasons": self.reasons}



def _haversine(a: dict, lat: float, lng: float) -> float | None:
    if a.get("lat") is None or a.get("lng") is None:
        return None
    r = 6371.0
    p1, p2 = math.radians(a["lat"]), math.radians(lat)
    dphi = math.radians(lat - a["lat"])
    dlmb = math.radians(lng - a["lng"])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _is_free(price: object) -> bool:
    return price == "free" or price == 0 or price == 0.0


def _date(s: str | None) -> str:
    return (s or "")[:10]


def _window(e: dict) -> tuple[str, str]:
    """행사 개최 구간 (시작, 종료). 종료 결측 시 시작일로 대체."""
    s = _date(e.get("start_date"))
    return s, _date(e.get("end_date")) or s


def score_event(e: dict, profile: dict) -> tuple[float, list[str]]:
    score, reasons = 0.0, []
    prefs = profile.get("prefs", {})

    if e.get("sido") in (profile.get("regions") or []):
        score += W_REGION
        reasons.append(f"지역일치({e['sido']})")

    matched = set(e.get("themes") or []) & set(profile.get("themes") or [])
    if matched:
        score += W_THEME * len(matched)
        reasons.append("테마일치(" + ",".join(sorted(matched)) + ")")

    if profile.get("age_band") and profile["age_band"] in (e.get("age_bands") or []):
        score += W_AGE
        reasons.append(f"나이대일치({profile['age_band']})")

    if _is_free(e.get("price")):
        score += W_FREE
        reasons.append("무료")
    elif prefs.get("free_only"):
        return PAID_EXCLUDE, ["유료 제외"]

    if e.get("status") == "Open":
        score += W_OPEN
        reasons.append("신청가능")

    near = prefs.get("near")
    if near and e.get("lat") and e.get("lng"):
        d = _haversine(e, near["lat"], near["lng"])
        if d is not None:
            if d <= 5:
                score += W_NEAR
                reasons.append(f"도보·근거리({d:.1f}km)")
            elif d <= 30:
                score += W_NEARISH
                reasons.append(f"근거리({d:.0f}km)")

    avail = {_date(x) for x in profile.get("available_dates") or []}
    if avail:
        s, en = _window(e)
        if any(s <= d <= en for d in avail):
            score += W_AVAIL
            reasons.append("가용일 내 개최")

    return score, reasons


def recommend(profile: dict, events: list[dict], top_n: int = 10) -> list[dict]:
    scored: list[ScoredEvent] = []
    for e in events:
        s, reasons = score_event(e, profile)
        if s <= 0:
            continue
        scored.append(ScoredEvent(event=e, score=s, reasons=reasons))
    scored.sort(key=lambda x: -x.score)
    return [se.to_record() for se in scored[:top_n]]


def plan_week(profile: dict, candidates: list[dict]) -> dict:
    """폴백 주간 플랜: 가용일별로 점수 상위 N개 배치(하루 max_per_day)."""
    max_per_day = profile.get("prefs", {}).get("max_per_day", 2)
    days = []
    used: set[str] = set()
    for d in profile.get("available_dates") or []:
        d = _date(d)
        items = []
        for c in candidates:
            if c["id"] in used:
                continue
            s, en = _window(c)
            if s <= d <= en:
                items.append({
                    "event_id": c["id"],
                    "name": c["name"],
                    "reason": "·".join(c["_reasons"][:3]),
                })
                used.add(c["id"])
            if len(items) >= max_per_day:
                break
        days.append({"date": d, "items": items})
    return {
        "week_of": _date((profile.get("available_dates") or ["-"])[0]),
        "engine": "rule-based-fallback",
        "days": days,
        "notes": "LLM 미사용 폴백 플랜(규칙 기반). 키 설정 시 ai-proxy가 동선/이유를 보강.",
    }


def main(argv: list[str]) -> int:
    profile = json.loads(Path(argv[0]).read_text(encoding="utf-8")) if argv else DEMO_PROFILE
    if not EVENTS_JSON.exists():
        print("events.json 없음 — 먼저 scripts.build.build_index 실행", file=sys.stderr)
        return 1
    events = json.loads(EVENTS_JSON.read_text(encoding="utf-8"))
    cands = recommend(profile, events)
    print(json.dumps({"candidates": [
        {"id": c["id"], "name": c["name"], "score": c["_score"], "reasons": c["_reasons"]}
        for c in cands
    ], "plan": plan_week(profile, cands)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
