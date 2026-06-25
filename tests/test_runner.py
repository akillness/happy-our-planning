"""신청 매크로 Playwright 러너 테스트 (T1 · AC-F3-runner).

Playwright/chromium은 무료·선택 의존. 미설치 환경에서는 E2E 테스트가 skip 되고
dry-run 테스트만 항상 실행되어 계획 경로(C1 무키·오프라인)를 회귀한다.

실행: python -m unittest tests.test_runner
"""
from __future__ import annotations

import unittest
from pathlib import Path

from scripts.macro import apply as macro
from scripts.macro import runner

FIXTURE = Path(__file__).parent / "fixtures" / "mock_apply_form.html"


def _chromium_runnable() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


_RUNNABLE = _chromium_runnable()


class TestRunner(unittest.TestCase):
    def _job(self, url: str) -> dict:
        """plan_job 산출 잡의 goto 타깃을 로컬 픽스처로 치환한다."""
        job = macro.plan_job({"id": "t", "url": url},
                             {"name": "홍길동", "phone": "010-1234-5678"})
        for step in job["steps"]:
            if step.get("action") == "goto":
                step["url"] = FIXTURE.as_uri()
        return job

    @unittest.skipUnless(_RUNNABLE, "playwright chromium 미설치 — 선택 의존")
    def test_auto_site_fills_and_submits_mock(self):
        job = self._job("https://mock.local/apply/x")
        res = runner.run_job(job, headless=True)
        self.assertEqual(res["mode"], "auto")
        self.assertTrue(res["submitted"])
        self.assertIn("신청완료", res["result_text"])

    @unittest.skipUnless(_RUNNABLE, "playwright chromium 미설치 — 선택 의존")
    def test_semi_site_pauses_before_submit(self):
        job = self._job("https://apply.example-city.go.kr/e")
        res = runner.run_job(job, headless=True)
        self.assertEqual(res["mode"], "semi")
        self.assertFalse(res["submitted"])
        self.assertEqual(res["paused_reason"], "본인인증/최종제출은 사용자가 수행")
        self.assertTrue(any(s["action"] == "pause" for s in job["steps"]))

    @unittest.skipUnless(_RUNNABLE, "playwright chromium 미설치 — 선택 의존")
    def test_guard_strips_submit_when_not_auto(self):
        # 반자동 잡에 submit이 잘못 끼어 있어도 러너가 실행하지 않는다(C5 이중 방어).
        job = self._job("https://apply.example-city.go.kr/e")
        job["steps"].append({"action": "submit", "selector": "#submit"})
        res = runner.run_job(job, headless=True)
        self.assertFalse(res["submitted"])

    def test_dry_run_no_browser(self):
        # Playwright 유무와 무관하게 항상 통과(무키·오프라인 계획 경로).
        job = self._job("https://mock.local/apply/x")
        res = runner.run_job(job, dry_run=True)
        self.assertFalse(res["submitted"])
        self.assertEqual(res["mode"], "auto")
        self.assertIn("스텝", res["result_text"])

    def test_dry_run_semi_mode_reports_no_submit_step(self):
        job = self._job("https://apply.example-city.go.kr/e")
        res = runner.run_job(job, dry_run=True)
        self.assertEqual(res["mode"], "semi")
        self.assertFalse(res["submitted"])

class _FakeLocator:
    def __init__(self, text: str = ""):
        self._text = text
        self.waited = False

    def wait_for(self, timeout=None):
        self.waited = True

    def inner_text(self):
        return self._text


class _FakePage:
    """Playwright sync page 표면을 흉내내는 스파이(chromium 불필요)."""

    def __init__(self, signal_text: str = "신청완료"):
        self.actions: list[tuple] = []
        self._signal_text = signal_text

    def goto(self, url):
        self.actions.append(("goto", url))

    def fill(self, selector, value=""):
        self.actions.append(("fill", selector, value))

    def check(self, selector):
        self.actions.append(("check", selector))

    def click(self, selector):
        self.actions.append(("click", selector))

    def locator(self, selector):
        return _FakeLocator(self._signal_text)


class TestRunnerUnit(unittest.TestCase):
    """chromium 없이 항상 실행되는 안전 불변(C5) 회귀."""

    def test_filtered_steps_strips_submit_when_not_auto(self):
        job = {"mode": "semi", "steps": [
            {"action": "fill", "selector": "#n"},
            {"action": "submit", "selector": "#s"},
        ]}
        steps = runner._filtered_steps(job)
        self.assertNotIn("submit", [s["action"] for s in steps])

    def test_filtered_steps_keeps_submit_when_auto(self):
        job = {"mode": "auto", "steps": [
            {"action": "submit", "selector": "#s"},
        ]}
        steps = runner._filtered_steps(job)
        self.assertIn("submit", [s["action"] for s in steps])

    def test_drive_auto_submits_and_captures_signal(self):
        page = _FakePage(signal_text="신청완료")
        result = {"submitted": False, "result_text": ""}
        steps = [
            {"action": "fill", "selector": "#n", "value": "홍길동"},
            {"action": "submit", "selector": "#s"},
        ]
        runner._drive(page, steps, success={"selector": "#ok"}, result=result)
        self.assertTrue(result["submitted"])
        self.assertEqual(result["result_text"], "신청완료")
        self.assertIn(("click", "#s"), page.actions)

    def test_drive_pause_stops_before_submit(self):
        page = _FakePage()
        result = {"submitted": False, "result_text": ""}
        steps = [
            {"action": "fill", "selector": "#n", "value": "x"},
            {"action": "pause", "reason": "본인인증 필요"},
            {"action": "submit", "selector": "#s"},  # pause 뒤이므로 도달 안 함
        ]
        runner._drive(page, steps, success={}, result=result)
        self.assertFalse(result["submitted"])
        self.assertEqual(result["paused_reason"], "본인인증 필요")
        self.assertNotIn(("click", "#s"), page.actions)

    def test_drive_ignores_unsupported_action(self):
        page = _FakePage()
        result = {"submitted": False, "result_text": ""}
        runner._drive(page, [{"action": "evil_eval"}], success={}, result=result)
        self.assertEqual(page.actions, [])  # 화이트리스트 밖 액션은 무시



if __name__ == "__main__":
    unittest.main()
