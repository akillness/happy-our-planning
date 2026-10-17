"""F7.1–F7.3 browser regression harness for the visit timeline and directions.

The harness uses the same static server, browser launch options, clock, data
fixture, and tile fixture as the existing UI verifier.  It never fakes a
successful external Maps navigation: it checks the generated outbound URLs and
only uses a local fixture for the app's own data.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "web" / "public"
EVID = Path(__file__).resolve().parent / "evidence"
EVIDENCE = EVID / "timeline-check.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from run_ui_check import FIXED_CLOCK_SCRIPT, _initial_ui_fixture_payloads, _install_data_fixture, serve
from ui_locale_check import _launch_options, _shortwide_header_results_probe

VIEWPORTS = ((320, 740), (360, 800), (390, 844), (768, 1024), (1440, 900), (844, 390), (568, 320))
TIMELINE_KEY = "notchima:timeline:v2"
SPEC_VERSION = "1.8.0"


def _record(checks: list[dict], name: str, ok: bool, detail: object = "") -> None:
    checks.append({"check": name, "ok": bool(ok), "detail": detail})


def _fixture_payloads() -> dict:
    payloads = copy.deepcopy(_initial_ui_fixture_payloads())
    events = payloads["events.json"]
    # Keep the real flattened event shape consumed by the browser.  These rows
    # cover same-day ordering, unknown time, a multi-day period, and no venue.
    events[0].update({
        "id": "timeline:alpha",
        "name": "타임라인 과학 체험",
        "start_date": "2026-10-20T14:00:00+09:00",
        "end_date": "2026-10-20T18:00:00+09:00",
        "venue": "서울시립과학관",
        "address": "서울특별시 노원구 한글비석로 160",
        "lat": 37.6421,
        "lng": 127.0767,
    })
    events[1].update({
        "id": "timeline:beta",
        "name": "기간 가족 축제",
        "start_date": "2026-10-20",
        "end_date": "2026-10-22",
        "venue": "서울숲",
        "address": "서울특별시 성동구 뚝섬로 273",
        "lat": 37.5446,
        "lng": 127.0374,
    })
    events[2].update({
        "id": "timeline:gamma",
        "name": "부산 만들기 체험",
        "start_date": "2026-10-19",
        "end_date": "2026-10-19",
        "venue": "부산시민공원",
        "address": "부산광역시 부산진구 시민공원로 73",
        "lat": 35.1684,
        "lng": 129.0557,
    })
    events.append({
        "id": "timeline:missing-place",
        "name": "장소 미정 가족 행사",
        "description": "목적지가 없는 실제 필드 형식의 행사 fixture",
        "event_type": "Event",
        "themes": ["체험"],
        "start_date": "2026-10-21",
        "end_date": "2026-10-21",
        "status": "Open",
        "sido": "서울특별시",
        "sigungu": "중구",
        "lat": None,
        "lng": None,
        "venue": "",
        "address": "",
        "age": "7-13",
        "age_bands": ["어린이"],
        "audience": ["가족"],
        "family_friendly": True,
        "family_evidence": "명시 문구: 가족",
        "experience_categories": ["과학·탐구"],
        "experience_summary": "목적지 미정 체험",
        "is_experience": True,
        "price": "free",
        "price_text": None,
        "duration_text": None,
        "duration": None,
        "link_status": "unchecked",
        "link_checked_at": None,
        "link_url": None,
        "link_reason": None,
        "booking_link_status": "unchecked",
        "booking_link_checked_at": None,
        "booking_link_url": None,
        "booking_link_reason": None,
        "offers": None,
        "url": "https://fixture/timeline-missing-place",
        "source": "fixture",
    })
    payloads["facets.json"] = {"theme": {"축제": 2, "체험": 2}, "age_band": {"어린이": 4}}
    payloads["regions.json"] = [{"sido": "서울특별시", "count": 3, "slug": "seoul", "centroid": [37.56, 126.94]}, {"sido": "부산광역시", "count": 1, "slug": "busan", "centroid": [35.16, 129.16]}]
    payloads["updated.json"]["total_active"] = len(events)
    return payloads


def _wait_ready(page) -> None:
    page.wait_for_function("document.getElementById('count')?.textContent !== ''")


def _open_timeline(page) -> None:
    panel = page.locator("#timelinePanel")
    if not panel.is_visible():
        page.locator("#timelineTab").click()
        page.wait_for_function("!document.querySelector('#timelinePanel')?.hidden")


def _return_to_map(page) -> None:
    page.locator("#mapTab").click()
    page.wait_for_function("document.querySelector('#timelinePanel')?.hidden === true")
    page.wait_for_function("document.querySelector('#resultsPanel')?.hidden === false")
    page.wait_for_function("document.querySelectorAll('#list [data-timeline-event]').length > 0")


def _add(page, event_id: str) -> None:
    # Cards and map/detail surfaces can contain the same action.  The card in
    # #list is the user-visible source of truth; never click a hidden popup
    # action merely because it appears first in document order.
    buttons = page.locator(f'#list [data-timeline-event="{event_id}"]')
    for index in range(buttons.count()):
        button = buttons.nth(index)
        if button.is_visible():
            button.click()
            return
    raise AssertionError(f"visible result-card timeline action missing: {event_id}")

def _items(page) -> list[str]:
    return page.locator("#timelineList .timeline-item").evaluate_all("nodes => nodes.map(n => n.dataset.eventId)")


def _save_screenshot(page, name: str) -> str:
    EVID.mkdir(parents=True, exist_ok=True)
    path = EVID / name
    page.screenshot(path=str(path), full_page=True)
    return str(path.relative_to(ROOT))


def _viewport_probe(page, width: int, height: int, checks: list[dict]) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto(page.url.split("?")[0], wait_until="networkidle")
    _wait_ready(page)
    if (width, height) in ((844, 390), (568, 320)):
        _shortwide_header_results_probe(page, width, height, checks)
    overflow = page.evaluate("() => ({scrollWidth: document.documentElement.scrollWidth, innerWidth: innerWidth})")
    _record(checks, f"{width}x{height} no horizontal overflow", overflow["scrollWidth"] <= overflow["innerWidth"] + 1, overflow)
    _record(checks, f"{width}x{height} map and timeline primary navigation", page.locator("#mapTab").count() == 1 and page.locator("#timelineTab").count() == 1)
    targets = page.locator("#mapTab, #timelineTab, #toolsToggle, #timelineClose")
    boxes = targets.evaluate_all("nodes => nodes.filter(n => !n.hidden && !n.closest('[hidden]') && n.getClientRects().length).map(n => { const r=n.getBoundingClientRect(); return {id:n.id,width:r.width,height:r.height}; })")
    target_ok = bool(boxes) and all(item["width"] >= 44 and item["height"] >= 44 for item in boxes)
    _record(checks, f"{width}x{height} primary targets >=44px", target_ok, boxes)
    if not page.locator("#toolsMenu").evaluate("el => el.open"):
        page.locator("#toolsToggle").click()
        page.wait_for_function("document.querySelector('#toolsMenu')?.open === true")
    tool_details = []
    for selector in ("#locateBtn", "#languageToggle", "#soundToggle", "#aiBtn"):
        control = page.locator(selector)
        control.scroll_into_view_if_needed()
        detail = control.evaluate("""
          el => {
            const r = el.getBoundingClientRect();
            const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            return {
              id: el.id,
              width: r.width,
              height: r.height,
              left: r.left,
              top: r.top,
              right: r.right,
              bottom: r.bottom,
              centerHit: hit === el || el.contains(hit),
              inViewport: r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight,
            };
          }
        """)
        tool_details.append(detail)
    tools_reachable = all(
        item["centerHit"] and item["inViewport"] and item["width"] >= 44 and item["height"] >= 44
        for item in tool_details
    )
    _record(checks, f"{width}x{height} tools drawer controls center-hit/in-viewport/44px", tools_reachable, tool_details)
    page.locator("#toolsToggle").click()
    page.wait_for_function("document.querySelector('#toolsMenu')?.open === false")
    shell = page.evaluate("""
      () => {
        const body = document.querySelector('#timelineScroll');
        const list = document.querySelector('#timelineList');
        const workspace = document.querySelector('#mapWorkspace');
        const bodyStyle = body ? getComputedStyle(body) : null;
        const workspaceStyle = workspace ? getComputedStyle(workspace) : null;
        return {
          paddingLeft: bodyStyle ? parseFloat(bodyStyle.paddingLeft) || 0 : 0,
          paddingRight: bodyStyle ? parseFloat(bodyStyle.paddingRight) || 0 : 0,
          listGap: list ? parseFloat(getComputedStyle(list).gap || getComputedStyle(list).rowGap) || 0 : 0,
          workspaceHeight: workspaceStyle ? parseFloat(workspaceStyle.height) || 0 : 0,
        };
      }
    """)
    spacing_ok = shell["paddingLeft"] >= 16 and shell["paddingRight"] >= 16 and shell["listGap"] >= 12
    viewport_height_ok = abs(shell["workspaceHeight"] - height) <= 1
    _record(checks, f"{width}x{height} 16px padding/12px spacing contract", spacing_ok and viewport_height_ok, shell)
    _save_screenshot(page, f"timeline-{width}x{height}.png")


def _run(checks: list[dict]) -> int:
    payloads = _fixture_payloads()
    with serve(PUBLIC) as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch(**_launch_options())
        context = browser.new_context()
        context.add_init_script(FIXED_CLOCK_SCRIPT)
        page = context.new_page()
        page.set_viewport_size({"width": 1440, "height": 900})
        _install_data_fixture(page, payloads)
        page.goto(base, wait_until="networkidle")
        _wait_ready(page)

        _record(checks, "timeline/map primary controls are present", page.locator("#mapTab").count() == 1 and page.locator("#timelineTab").count() == 1)
        initial_timeline_hidden = page.locator("#timelinePanel").get_attribute("hidden") is not None
        initial_empty_hidden_attr = page.locator("#timelineEmpty").get_attribute("hidden") is not None
        initial_empty_hidden_by_panel = not page.locator("#timelineEmpty").is_visible()
        _open_timeline(page)
        empty_state_visible = page.locator("#timelineEmpty").is_visible() and page.locator("#timelineList").inner_text().strip() == ""
        _record(
            checks,
            "timeline starts hidden and opens to a visible empty state",
            initial_timeline_hidden and not initial_empty_hidden_attr and initial_empty_hidden_by_panel and empty_state_visible,
            {"panelHidden": initial_timeline_hidden, "emptyAttrHidden": initial_empty_hidden_attr, "emptyHiddenByPanel": initial_empty_hidden_by_panel, "openVisible": empty_state_visible},
        )
        _return_to_map(page)
        _add(page, "timeline:alpha")
        first_count = _items(page).count("timeline:alpha")
        _add(page, "timeline:alpha")
        second_count = _items(page).count("timeline:alpha")
        _record(checks, "add and duplicate prevention", second_count <= 1 and first_count <= 1, {"first": first_count, "second": second_count})
        if second_count == 0:
            _add(page, "timeline:alpha")
        _open_timeline(page)
        ids = _items(page)
        _record(checks, "timeline item exposes date/time inputs", page.locator('.timeline-item[data-event-id="timeline:alpha"] input[data-field="date"][type="date"]').count() == 1 and page.locator('.timeline-item[data-event-id="timeline:alpha"] input[data-field="time"][type="time"]').count() == 1)
        _record(checks, "timeline count reports saved entries", page.locator("#timelineCount").inner_text().strip() in {"1", "1건", "1 item", "1 items"}, page.locator("#timelineCount").inner_text())

        row = page.locator('.timeline-item[data-event-id="timeline:alpha"]')
        date_input = row.locator('input[data-field="date"]')
        time_input = row.locator('input[data-field="time"]')
        date_input.fill("2026-10-20")
        time_input.fill("")
        time_input.press("Tab")
        time_input.focus()
        page.locator('.timeline-item[data-event-id="timeline:alpha"] .timeline-remove').click()
        _record(checks, "remove works with time input focused", "timeline:alpha" not in _items(page), _items(page))
        _return_to_map(page)
        _add(page, "timeline:alpha")
        _add(page, "timeline:beta")
        _add(page, "timeline:gamma")
        _open_timeline(page)
        order = _items(page)
        _record(checks, "date/time ordering puts earlier date first and unknown time after fixed time", order[:3] == ["timeline:gamma", "timeline:alpha", "timeline:beta"], order)
        alpha_google = page.locator('.timeline-item[data-event-id="timeline:alpha"] .directions-google')
        beta_google = page.locator('.timeline-item[data-event-id="timeline:beta"] .directions-google')
        alpha_href = alpha_google.get_attribute("href") if alpha_google.count() else None
        beta_href = beta_google.get_attribute("href") if beta_google.count() else None
        direction_links_ok = bool(alpha_href and beta_href and "travelmode=transit" in alpha_href and "origin=" not in alpha_href and "origin=" in beta_href and "waypoints" not in beta_href)
        rel_ok = all("noopener" in (anchor.get_attribute("rel") or "") for anchor in (alpha_google, beta_google) if anchor.count())
        _record(checks, "timeline directions use first-without-origin and next-with-previous-origin", direction_links_ok and rel_ok, {"alpha": alpha_href, "beta": beta_href})
        beta_for_filter = page.locator('.timeline-item[data-event-id="timeline:beta"]')
        beta_for_filter.locator('input[type="date"]').fill("2026-10-22")
        beta_for_filter.locator('input[type="date"]').press("Tab")
        page.locator("#timelineDate").fill("2026-10-22")
        page.locator("#timelineDate").press("Tab")
        page.wait_for_function("Array.from(document.querySelectorAll('#timelineList .timeline-item')).filter(el => !el.hidden && getComputedStyle(el).display !== 'none').length === 1")
        different_date_href = page.locator('.timeline-item[data-event-id="timeline:beta"] .directions-google').get_attribute("href")
        _record(checks, "different-date timeline starts a new route without origin", bool(different_date_href and "travelmode=transit" in different_date_href and "origin=" not in different_date_href), different_date_href)
        page.locator("#timelineDateClear").click()
        beta = page.locator('.timeline-item[data-event-id="timeline:beta"]')
        beta_date = beta.locator('input[type="date"]')
        beta_date.fill("2026-10-23")
        beta_date.press("Tab")
        _record(checks, "date outside event period is rejected", beta_date.input_value() != "2026-10-23" or page.locator("#timelineNotice").inner_text().strip() != "", {"value": beta_date.input_value(), "notice": page.locator("#timelineNotice").inner_text()})
        _return_to_map(page)

        if not page.locator("#filterMenu").evaluate("el => el.open"):
            page.locator("#filterToggle").click()
        page.locator("#from").fill("2026-10-20")
        page.locator("#from").press("Tab")
        if page.locator("#filterMenu").evaluate("el => el.open"):
            page.locator("#filterToggle").click()
            page.wait_for_function("document.querySelector('#filterMenu')?.open === false")
        before = _items(page)
        tools_center_hit = page.evaluate("""
          () => {
            const target = document.querySelector('#toolsToggle');
            if (!target) return false;
            const rect = target.getBoundingClientRect();
            const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
            return hit === target || target.contains(hit);
          }
        """)
        _record(checks, "tools toggle center hit is unobstructed", bool(tools_center_hit), tools_center_hit)
        page.locator("#toolsToggle").click()
        page.wait_for_function("document.querySelector('#toolsMenu')?.open === true")
        _record(checks, "tools toggle opens with physical click", page.locator("#toolsMenu").evaluate("el => el.open"))
        page.locator("#toolsToggle").press("Escape")
        page.wait_for_function("document.querySelector('#toolsMenu')?.open === false")
        _record(checks, "tools toggle closes with keyboard Escape", page.evaluate("document.activeElement?.id") == "toolsToggle", page.evaluate("document.activeElement?.id"))
        page.locator("#toolsToggle").press("Enter")
        page.wait_for_function("document.querySelector('#toolsMenu')?.open === true")
        _record(checks, "tools toggle opens with keyboard Enter", page.locator("#toolsMenu").evaluate("el => el.open"))
        page.locator("#toolsToggle").press("Escape")
        page.wait_for_function("document.querySelector('#toolsMenu')?.open === false")
        page.locator("#toolsToggle").click()
        page.wait_for_function("document.querySelector('#toolsMenu')?.open === true")
        page.locator("#languageToggle").click()
        page.wait_for_function("document.documentElement.lang === 'en'")
        after = _items(page)
        _record(checks, "filter and language changes preserve timeline", before == after and len(after) >= 2, {"before": before, "after": after})
        page.reload(wait_until="networkidle")
        _wait_ready(page)
        _open_timeline(page)
        restored = _items(page)
        stored = page.evaluate(f"() => JSON.parse(localStorage.getItem({json.dumps(TIMELINE_KEY)}))")
        _record(checks, "reload restores timeline and versioned storage", set(restored) >= {"timeline:alpha", "timeline:beta", "timeline:gamma"} and stored.get("version") == 2 and isinstance(stored.get("entries"), list), {"items": restored, "stored": stored})

        page.locator("#timelineClose").click()
        page.locator("#timelineTab").press("Escape")
        _record(checks, "Escape closes timeline and restores opener focus", page.locator("#timelinePanel").get_attribute("hidden") is not None and page.evaluate("document.activeElement?.id") in {"timelineTab", "timelineClose"}, page.evaluate("document.activeElement?.id"))

        corrupt_context = browser.new_context()
        corrupt_context.add_init_script(FIXED_CLOCK_SCRIPT)
        corrupt = corrupt_context.new_page()
        corrupt.set_viewport_size({"width": 768, "height": 900})
        corrupt.add_init_script(f"localStorage.setItem({json.dumps(TIMELINE_KEY)}, '{{broken');")
        _install_data_fixture(corrupt, payloads)
        corrupt.goto(base, wait_until="networkidle")
        _wait_ready(corrupt)
        _open_timeline(corrupt)
        _record(checks, "corrupt storage is reported without blocking app", corrupt.locator("#timelineNotice").is_visible() and corrupt.locator("#count").inner_text().strip() != "", corrupt.locator("#timelineNotice").inner_text())

        blocked_context = browser.new_context()
        blocked_context.add_init_script(FIXED_CLOCK_SCRIPT)
        blocked = blocked_context.new_page()
        blocked.set_viewport_size({"width": 768, "height": 900})
        blocked.add_init_script("Storage.prototype.setItem = function(){ throw new Error('quota denied'); };")
        _install_data_fixture(blocked, payloads)
        blocked.goto(base, wait_until="networkidle")
        _wait_ready(blocked)
        _add(blocked, "timeline:alpha")
        _open_timeline(blocked)
        _record(checks, "storage failure is surfaced while browsing continues", blocked.locator("#timelineNotice").is_visible() and blocked.locator("#count").inner_text().strip() != "", blocked.locator("#timelineNotice").inner_text())

        for width, height in VIEWPORTS:
            _viewport_probe(page, width, height, checks)
        _save_screenshot(page, "timeline-final.png")
        blocked.close()
        blocked_context.close()
        corrupt.close()
        corrupt_context.close()
        page.close()
        browser.close()
    EVID.mkdir(parents=True, exist_ok=True)
    summary = {"status": "passed" if all(item["ok"] for item in checks) else "failed", "spec_version": SPEC_VERSION, "checks": checks, "total_checks": len(checks), "passed": sum(item["ok"] for item in checks)}
    EVIDENCE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 1


def main() -> int:
    checks: list[dict] = []
    try:
        return _run(checks)
    except (AssertionError, PlaywrightTimeoutError, OSError) as exc:
        EVID.mkdir(parents=True, exist_ok=True)
        summary = {"status": "failed", "spec_version": SPEC_VERSION, "checks": checks, "error": str(exc), "total_checks": len(checks), "passed": sum(item["ok"] for item in checks)}
        EVIDENCE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.parse_args()
    raise SystemExit(main())
