"""신청 매크로 — 잡 모델·스텝 렌더링·안전 게이팅 (docs/06).

이 모듈은 브라우저 실행 전 단계(잡 계획·약관 게이팅)를 담당한다.
실제 폼 입력은 Playwright 러너(scripts/macro/runner.py)가 plan_job() 결과를 소비해 수행한다.

안전 보장:
- automation_allowed=False 사이트는 mode='semi'(반자동)로 강등되고
  자동 submit 액션이 제거되며 pause(사용자 최종제출) 단계가 보장된다.
"""
from __future__ import annotations

import json
import re
import sys
from functools import lru_cache

import yaml

from scripts.common.config import ROOT

PROFILES_PATH = ROOT / "config" / "macro-sites.yaml"
_TOKEN = re.compile(r"\{([a-zA-Z0-9_.]+)\}")


@lru_cache(maxsize=None)
def _profiles() -> list[dict]:
    with PROFILES_PATH.open(encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("sites", [])


def match_site(url: str) -> dict | None:
    for p in _profiles():
        if p.get("match_url") and p["match_url"] in (url or ""):
            return p
    return None


def _resolve(token: str, ctx: dict) -> str:
    cur: object = ctx
    for part in token.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return ""
    return "" if cur is None else str(cur)


def render_steps(steps: list[dict], ctx: dict) -> list[dict]:
    out = []
    for step in steps:
        rendered = {}
        for k, v in step.items():
            rendered[k] = _TOKEN.sub(lambda m: _resolve(m.group(1), ctx), v) if isinstance(v, str) else v
        out.append(rendered)
    return out


def plan_job(event: dict, profile_data: dict) -> dict:
    """행사 url로 사이트 프로필을 찾아 실행 잡을 계획한다."""
    site = match_site(event.get("url", ""))
    if not site:
        return {"event_id": event.get("id"), "error": "매칭되는 사이트 프로필 없음", "mode": "manual"}

    ctx = {"event": event, "profile": profile_data}
    steps = render_steps(site.get("steps", []), ctx)
    auto = bool(site.get("automation_allowed"))

    if not auto:
        # 자동제출 제거 + 사용자 최종제출 pause 보장
        steps = [s for s in steps if s.get("action") != "submit"]
        if not any(s.get("action") == "pause" for s in steps):
            steps.append({"action": "pause", "reason": "약관상 사용자가 최종제출"})

    return {
        "event_id": event.get("id"),
        "site_key": site["key"],
        "mode": "auto" if auto else "semi",
        "automation_allowed": auto,
        "auth": site.get("auth", {}),
        "steps": steps,
        "success_signal": site.get("success_signal"),
        "state": "queued",  # queued→filled→submitted→result
    }


def is_auto_submit(job: dict) -> bool:
    """이 잡이 사용자 개입 없이 자동 제출하는가? (안전 검증용)"""
    return job.get("mode") == "auto" and any(s.get("action") == "submit" for s in job.get("steps", []))


def main(argv: list[str]) -> int:
    event = {"id": "demo", "url": argv[0] if argv else "https://mock.local/apply/demo",
             "session": "10:00"}
    profile_data = {"name": "홍길동", "phone": "010-0000-0000"}
    job = plan_job(event, profile_data)
    print(json.dumps(job, ensure_ascii=False, indent=2))
    print(f"\n자동제출 여부: {is_auto_submit(job)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
