"""신청 매크로 — Playwright 실행 러너 (docs/06 · F3.4).

`apply.plan_job()`가 산출한 잡(steps[])을 소비해 실제 브라우저에서 폼을 채우고
(자동 사이트에 한해) 제출까지 수행한다. 계획/게이팅은 apply.py, 실행은 본 모듈이 담당한다.

안전 불변(C5):
- `apply.is_auto_submit(job)`가 False면(=반자동/약관 게이트) submit 액션을 실행하지 않고
  pause 단계에서 멈춘다. 잡이 잘못 전달돼도 러너가 한 번 더 submit을 제거한다(이중 방어).

의존성: Playwright는 무료·선택 의존이다. 미설치 시 run_job()은 RuntimeError를 던지고,
테스트는 chromium 미설치 환경에서 skip 된다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.macro import apply as _apply

# 잡 상태 천이: queued → filled → (submitted) → result
_SUPPORTED = {"goto", "fill", "check", "click", "submit", "pause", "assert_text"}


def _filtered_steps(job: dict) -> list[dict]:
    """잡의 스텝을 안전 불변(C5)에 맞게 정제한다.

    `is_auto_submit(job)`가 False면(반자동/약관 게이트) submit 액션을 제거한다.
    잡이 잘못 전달돼도 러너가 한 번 더 submit을 막는 이중 방어.
    """
    steps = list(job.get("steps", []))
    if not _apply.is_auto_submit(job):
        steps = [s for s in steps if s.get("action") != "submit"]
    return steps


def _new_result(job: dict) -> dict:
    return {
        "site": job.get("site_key"),
        "mode": job.get("mode", "manual"),
        "submitted": False,
        "result_text": "",
        "screenshot": None,
    }


def _drive(page, steps: list[dict], *, success: dict, result: dict) -> dict:
    """page-like 객체에 스텝을 디스패치한다(브라우저 추상화 경계).

    page는 Playwright sync page와 동일한 표면(goto/fill/check/click/locator)을
    가지면 되며, 테스트는 가짜 page로 submit/pause 불변을 chromium 없이 검증한다.
    """
    for step in steps:
        action = step.get("action")
        if action not in _SUPPORTED:
            continue
        if action == "goto":
            page.goto(step["url"])
        elif action == "fill":
            page.fill(step["selector"], step.get("value", ""))
        elif action == "check":
            page.check(step["selector"])
        elif action == "click":
            page.click(step["selector"])
        elif action == "assert_text":
            loc = page.locator(step["selector"])
            loc.wait_for(timeout=5000)
            result["result_text"] = loc.inner_text()
        elif action == "pause":
            # 반자동: 최종 제출은 사용자가 수행. 자동 실행은 여기서 멈춘다.
            result["paused_reason"] = step.get("reason", "사용자 최종제출 대기")
            break
        elif action == "submit":
            page.click(step["selector"])
            result["submitted"] = True

    # 성공 신호 캡처(제출했고 success_signal selector가 있으면).
    if result["submitted"] and success.get("selector"):
        loc = page.locator(success["selector"])
        try:
            loc.wait_for(timeout=5000)
            result["result_text"] = loc.inner_text()
        except Exception:  # pragma: no cover - 신호 미출현
            result["result_text"] = ""
    return result


def run_job(job: dict, *, headless: bool = True, dry_run: bool = False,
            screenshot_dir: str | Path | None = None) -> dict:
    """잡을 실행하고 증거(JSON)를 반환한다.

    반환: {site, mode, submitted, result_text, screenshot, paused_reason?}
    """
    steps = _filtered_steps(job)
    result = _new_result(job)

    if dry_run:
        # 브라우저를 띄우지 않고 계획만 검증(무키·오프라인 통과 경로).
        result["result_text"] = f"dry-run: {len(steps)}개 스텝 계획됨"
        return result

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - 의존성 미설치 경로
        raise RuntimeError("playwright 미설치 — `pip install playwright && playwright install chromium`") from exc

    success = job.get("success_signal") or {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            _drive(page, steps, success=success, result=result)
            if screenshot_dir is not None:
                out = Path(screenshot_dir)
                out.mkdir(parents=True, exist_ok=True)
                shot = out / f"{job.get('event_id', 'job')}.png"
                page.screenshot(path=str(shot))
                result["screenshot"] = str(shot)
        finally:
            browser.close()

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="신청 매크로 Playwright 러너")
    parser.add_argument("--job", required=True, help="plan_job() 산출 JSON 파일")
    parser.add_argument("--headless", action="store_true", help="헤드리스 모드")
    parser.add_argument("--dry-run", action="store_true", help="브라우저 미실행, 계획만 검증")
    parser.add_argument("--screenshot-dir", default=None)
    args = parser.parse_args(argv)

    job = json.loads(Path(args.job).read_text(encoding="utf-8"))
    result = run_job(job, headless=args.headless, dry_run=args.dry_run,
                     screenshot_dir=args.screenshot_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
