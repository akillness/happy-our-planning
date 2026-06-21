"""T4 — UI 5필터·반응형·URL 복원 검증 (Playwright E2E 증거 생성).

web/public 를 임시 HTTP 서버로 띄우고 5축 필터를 조작 → URL 동기화 → 새로고침 복원 →
360px 스냅샷을 캡처한다. 산출 증거는 specs/verify/evidence/ 에 저장되고, 요약은 stdout(JSON).

실행: python3 specs/verify/run_ui_check.py
의존: playwright + chromium(무료·선택). 미설치 시 RuntimeError.
"""
from __future__ import annotations

import contextlib
import functools
import http.server
import json
import socket
import threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "web" / "public"
EVID = Path(__file__).resolve().parent / "evidence"


@contextlib.contextmanager
def serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.socket.getsockname()[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        httpd.shutdown()


def main() -> int:
    from playwright.sync_api import sync_playwright

    EVID.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []

    def record(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    with serve(PUBLIC) as base, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(base, wait_until="networkidle")
        page.wait_for_function("document.getElementById('count').textContent !== ''")

        total = int(page.locator("#count").inner_text())
        record("초기 로드 + 카운트", total > 0, f"활성 {total}건")

        # 5축 필터 조작: 키워드 / 지역 / 테마 / 나이대 / 기간 + 신청가능
        page.fill("#q", "축제")
        page.select_option("#sido", "서울특별시")
        page.select_option("#theme", "축제")
        page.select_option("#age", "어린이")
        page.fill("#from", "2026-01-01")
        page.fill("#to", "2026-12-31")
        page.check("#applyable")
        page.wait_for_timeout(200)
        filtered = int(page.locator("#count").inner_text())

        qs = parse_qs(urlparse(page.url).query)
        want = {"q": "축제", "sido": "서울특별시", "theme": "축제", "age": "어린이",
                "from": "2026-01-01", "to": "2026-12-31", "applyable": "1"}
        url_ok = all(qs.get(k, [None])[0] == v for k, v in want.items())
        record("5축 필터 → URL 동기화", url_ok, f"{urlparse(page.url).query}")
        record("필터 적용 결과 카운트", filtered <= total, f"{filtered}/{total}")

        shared_url = page.url

        # 새로고침/공유 링크 복원: 동일 URL 신규 페이지 로드 → 입력값·카운트 복원
        page2 = browser.new_page(viewport={"width": 1280, "height": 900})
        page2.goto(shared_url, wait_until="networkidle")
        page2.wait_for_function("document.getElementById('count').textContent !== ''")
        page2.wait_for_timeout(200)
        restored = {
            "q": page2.input_value("#q"),
            "sido": page2.input_value("#sido"),
            "theme": page2.input_value("#theme"),
            "age": page2.input_value("#age"),
            "from": page2.input_value("#from"),
            "to": page2.input_value("#to"),
            "applyable": page2.is_checked("#applyable"),
        }
        restore_ok = (restored["q"] == "축제" and restored["sido"] == "서울특별시"
                      and restored["theme"] == "축제" and restored["age"] == "어린이"
                      and restored["from"] == "2026-01-01" and restored["to"] == "2026-12-31"
                      and restored["applyable"] is True)
        recount = int(page2.locator("#count").inner_text())
        record("URL 복원(새 세션 입력값)", restore_ok, json.dumps(restored, ensure_ascii=False))
        record("URL 복원(카운트 일치)", recount == filtered, f"{recount}=={filtered}")

        # 반응형 360px 스냅샷
        page2.set_viewport_size({"width": 360, "height": 780})
        page2.wait_for_timeout(200)
        shot360 = EVID / "ui-360px.png"
        page2.screenshot(path=str(shot360), full_page=True)
        record("360px 모바일 스냅샷", shot360.exists(), str(shot360.relative_to(ROOT)))

        # reset 동작 확인
        page2.set_viewport_size({"width": 1280, "height": 900})
        page2.click("#reset")
        page2.wait_for_timeout(200)
        reset_count = int(page2.locator("#count").inner_text())
        record("reset → 전체 복귀", reset_count == total, f"{reset_count}=={total}")

        shot_desktop = EVID / "ui-desktop-filtered.png"
        page.screenshot(path=str(shot_desktop))
        record("데스크톱 필터 스냅샷", shot_desktop.exists(), str(shot_desktop.relative_to(ROOT)))

        browser.close()

    summary = {"total_checks": len(checks), "passed": sum(c["ok"] for c in checks), "checks": checks}
    (EVID / "ui-check.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] == summary["total_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
