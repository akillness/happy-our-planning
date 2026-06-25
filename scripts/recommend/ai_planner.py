"""Google AI Studio(Gemini) 기반 주간 플래너 (docs/08 2단계).

규칙 기반 랭킹(scripts.recommend.rank)으로 후보를 추린 뒤, Gemini에
**구조화 JSON 스키마(responseSchema)** 를 강제해 주간 동선/이유를 생성한다.
키가 없거나 네트워크/검증 실패 시 rule-based 폴백(rank.plan_week)으로 강등한다.

키: GOOGLE_AI_STUDIO_KEY(우선) 또는 GEMINI_KEY. 모델: GEMINI_MODEL(기본 gemini-2.0-flash).
키는 서버/Worker(ai-proxy)에서만 사용 — 클라이언트 노출 금지.

순수 함수(build_request / parse_plan / validate_plan)는 네트워크 없이 테스트된다.

사용:
    python -m scripts.recommend.ai_planner                # 데모 프로필
    python -m scripts.recommend.ai_planner profile.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from scripts.common.config import ROOT
from scripts.recommend import rank

API_HOST = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-2.0-flash"

# 강제 출력 스키마(OpenAPI subset that Gemini responseSchema accepts)
_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "week_of": {"type": "string"},
        "days": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "event_id": {"type": "string"},
                                "time": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["event_id", "reason"],
                        },
                    },
                },
                "required": ["date", "items"],
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["week_of", "days"],
}


def _api_key() -> str:
    return os.environ.get("GOOGLE_AI_STUDIO_KEY") or os.environ.get("GEMINI_KEY") or ""


def _model() -> str:
    return os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL


def _slim(c: dict) -> dict:
    """후보 OKF를 프롬프트용 축약 필드로."""
    return {
        "event_id": c.get("id"),
        "name": c.get("name"),
        "sido": c.get("sido"),
        "themes": c.get("themes") or [],
        "start_date": (c.get("start_date") or "")[:10],
        "end_date": (c.get("end_date") or "")[:10] or None,
        "price": c.get("price"),
        "status": c.get("status"),
        "reasons": c.get("_reasons") or [],
    }


def build_request(profile: dict, candidates: list[dict]) -> dict:
    """Gemini generateContent 요청 바디(JSON 강제)."""
    prefs = profile.get("prefs", {})
    instruction = (
        "너는 대한민국 행사 큐레이터다. 사용자 프로필과 후보 행사 목록만으로 "
        "주간 일정을 설계하라. 규칙: (1) 하루 최대 "
        f"{prefs.get('max_per_day', 2)}개, (2) 같은 날 동선 최소화(같은 시/도 우선), "
        "(3) available_dates 안에서만 배치, (4) free_only=true면 유료 제외, "
        "(5) reason은 한국어 한 문장. event_id는 반드시 후보 목록의 값만 사용."
    )
    payload = {
        "profile": profile,
        "candidates": [_slim(c) for c in candidates],
    }
    return {
        "contents": [{
            "role": "user",
            "parts": [{"text": instruction + "\n\n입력:\n" + json.dumps(
                payload, ensure_ascii=False)}],
        }],
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json",
            "responseSchema": _PLAN_SCHEMA,
        },
    }


def parse_plan(resp: dict) -> dict:
    """generateContent 응답 → plan dict. 텍스트 파트의 JSON을 파싱."""
    candidates = resp.get("candidates") or []
    if not candidates:
        raise ValueError("응답에 candidates 없음")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise ValueError("응답 텍스트 비어있음")
    plan = json.loads(text)
    validate_plan(plan)
    return plan


def validate_plan(plan: dict) -> None:
    """플랜 형태 최소 검증(스키마 위반 시 예외)."""
    if not isinstance(plan, dict):
        raise ValueError("plan은 object여야 함")
    if not isinstance(plan.get("week_of"), str):
        raise ValueError("week_of(str) 필요")
    days = plan.get("days")
    if not isinstance(days, list):
        raise ValueError("days(array) 필요")
    for day in days:
        if not isinstance(day.get("date"), str):
            raise ValueError("day.date(str) 필요")
        items = day.get("items")
        if not isinstance(items, list):
            raise ValueError("day.items(array) 필요")
        for it in items:
            if not it.get("event_id"):
                raise ValueError("item.event_id 필요")


def _constrain_to_candidates(plan: dict, candidates: list[dict], profile: dict | None = None) -> dict:
    """LLM이 후보 밖 event_id를 만들거나 가용일이 아닌 날짜에 일정을 배치하면 제거(환각 가드)."""
    valid = {c.get("id") for c in candidates}
    avail = set(profile.get("available_dates") or []) if profile else set()
    for day in plan.get("days", []):
        day_date = day.get("date")
        if avail and day_date not in avail:
            day["items"] = []
        else:
            day["items"] = [it for it in day.get("items", []) if it.get("event_id") in valid]
    plan["engine"] = f"gemini:{_model()}"
    return plan



def plan(profile: dict, events: list[dict], top_n: int = 8) -> dict:
    """후보 랭킹 → Gemini 플랜(실패 시 규칙 기반 폴백)."""
    candidates = rank.recommend(profile, events, top_n=top_n)
    key = _api_key()
    if not key or not candidates:
        return rank.plan_week(profile, candidates)
    try:
        import httpx
    except ImportError:
        return rank.plan_week(profile, candidates)
    from scripts.common import http as _http
    url = f"{API_HOST}/v1beta/models/{_model()}:generateContent?key={key}"
    try:  # pragma: no cover - 네트워크 경로
        r = _http.request_with_retry(
            lambda: httpx.post(url, json=build_request(profile, candidates), timeout=30),
            retry_exceptions=(httpx.TransportError,),
        )
        result = parse_plan(r.json())
        return _constrain_to_candidates(result, candidates, profile)
    except Exception as exc:  # pragma: no cover - 폴백 보장
        fb = rank.plan_week(profile, candidates)
        fb["notes"] = f"Gemini 실패({exc}) → 규칙 기반 폴백. " + fb.get("notes", "")
        return fb


def main(argv: list[str]) -> int:
    events_json = ROOT / "web" / "public" / "data" / "events.json"
    if not events_json.exists():
        print("events.json 없음 — 먼저 scripts.build.build_index 실행", file=sys.stderr)
        return 1
    profile = (json.loads(Path(argv[0]).read_text(encoding="utf-8"))
               if argv else rank.DEMO_PROFILE)
    events = json.loads(events_json.read_text(encoding="utf-8"))
    result = plan(profile, events)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
