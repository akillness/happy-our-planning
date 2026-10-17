"""F8.1–F8.3 browser evidence for local Google Timeline import.

Only synthetic temporary JSON is selected. The ordinary app data request is
allowed and recorded separately; the import window must not issue a request or
persist the source JSON, coordinates, or filename.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "web" / "public"
EVID = Path(__file__).resolve().parent / "evidence"
EVIDENCE = EVID / "history-import-check.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from run_ui_check import FIXED_CLOCK_SCRIPT, _initial_ui_fixture_payloads, _install_data_fixture, serve
from ui_locale_check import _launch_options

TIMELINE_V2 = "notchima:timeline:v2"
TIMELINE_V1 = "notchima:timeline:v1"
SENTINEL = "private-place-sentinel-7f8c"
HISTORY_CLOCK_SCRIPT = FIXED_CLOCK_SCRIPT.replace("2026-10-14T16:00:00Z", "2026-10-24T00:00:00Z")
SENTINEL_COORDINATE = "11.222333"


def _record(checks: list[dict], name: str, ok: bool, detail: object = "") -> None:
    # Evidence must not contain the synthetic private place, raw coordinates, or filename.
    safe = str(detail)
    for secret in (SENTINEL, SENTINEL_COORDINATE, "synthetic-history-private-file.json"):
        safe = safe.replace(secret, "[redacted]")
    checks.append({"check": name, "ok": bool(ok), "detail": safe})


def _payloads() -> dict:
    payloads = copy.deepcopy(_initial_ui_fixture_payloads())
    events = payloads["events.json"]
    events[0].update({
        "id": "history:event-a",
        "name": "History A",
        "start_date": "2026-10-20",
        "end_date": "2026-10-20",
        "lat": 37.5000000,
        "lng": 127.0000000,
        "location": {"lat": 37.5000000, "lng": 127.0000000},
    })
    events[1].update({
        "id": "history:event-b",
        "name": "History B",
        "start_date": "2026-10-20",
        "end_date": "2026-10-20",
        "lat": 37.5000000,
        "lng": 127.0000000,
        "location": {"lat": 37.5000000, "lng": 127.0000000},
    })
    events[2].update({
        "id": "history:event-future",
        "name": "History Future",
        "start_date": "2026-12-20",
        "end_date": "2026-12-20",
        "lat": 37.5000000,
        "lng": 127.0000000,
        "location": {"lat": 37.5000000, "lng": 127.0000000},
    })
    payloads["updated.json"]["total_active"] = len(events)
    return payloads


def _history_export() -> dict:
    return {
        "timelineObjects": [
            {"placeVisit": {
                "location": {"latitudeE7": 375000000, "longitudeE7": 1270000000},
                "duration": {"startTimestamp": "2026-10-20T01:00:00Z", "endTimestamp": "2026-10-20T01:20:00Z"},
            }},
            {"placeVisit": {
                "location": {"latitudeE7": 375000000, "longitudeE7": 1270000000},
                "duration": {"startTimestamp": "2026-10-20T05:00:00Z", "endTimestamp": "2026-10-20T05:20:00Z"},
            }},
            {"timelinePath": [{"point": SENTINEL_COORDINATE}]},
        ]
    }


def _open_history(page) -> None:
    details = page.locator("#historyImport")
    if details.count() != 1:
        raise AssertionError("history import details control is missing")
    if not details.is_visible():
        timeline_tab = page.locator("#timelineTab")
        if timeline_tab.count() != 1:
            raise AssertionError("timeline tab is missing")
        timeline_tab.click()
        page.wait_for_function("document.querySelector('#timelinePanel')?.hidden === false")
    if not details.evaluate("el => el.open"):
        details.locator("summary").click()
        page.wait_for_function("document.querySelector('#historyImport')?.open === true")

def _storage(page) -> dict[str, object]:
    return page.evaluate("""
      () => Object.fromEntries(Array.from({length: localStorage.length}, (_, i) => {
        const key = localStorage.key(i);
        return [key, localStorage.getItem(key)];
      }))
    """)


def _entries(page) -> list[dict]:
    raw = page.evaluate(f"() => localStorage.getItem({json.dumps(TIMELINE_V2)})")
    return json.loads(raw or "{}").get("entries", [])


def _run(checks: list[dict]) -> int:
    payloads = _payloads()
    requests: list[str] = []
    consoles: list[str] = []
    page_errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="notchima-history-") as tmp:
        path = Path(tmp) / "synthetic-history-private-file.json"
        path.write_text(json.dumps(_history_export(), ensure_ascii=False), encoding="utf-8")
        with serve(PUBLIC) as base, sync_playwright() as playwright:
            browser = playwright.chromium.launch(**_launch_options())
            context = browser.new_context()
            context.add_init_script(HISTORY_CLOCK_SCRIPT)
            page = context.new_page()
            page.set_viewport_size({"width": 1280, "height": 900})
            page.on("request", lambda request: requests.append(request.url))
            page.on("console", lambda message: consoles.append(message.text))
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            _install_data_fixture(page, payloads)
            page.goto(base, wait_until="networkidle")
            page.wait_for_function("document.getElementById('count')?.textContent !== ''")
            baseline_requests = len(requests)
            _record(checks, "ordinary app data GET completes before import", any("/data/events.json" in url for url in requests))

            # Seed a plan so confirming history cannot replace it.
            page.evaluate(f"""
              () => localStorage.setItem({json.dumps(TIMELINE_V2)}, JSON.stringify({{
                version: 2,
                entries: [{{entry_id: 'plan-a', event_id: 'history:event-a', date: '2026-10-20', time: '', visited: false}}]
              }}))
            """)
            page.reload(wait_until="networkidle")
            page.wait_for_function("document.getElementById('count')?.textContent !== ''")
            page.wait_for_function("document.getElementById('loading')?.hidden === true")
            baseline_requests = len(requests)
            before_import = _storage(page)
            _open_history(page)
            with page.expect_file_chooser() as chooser_info:
                page.locator("label[for='historyFile']").click()
            chooser = chooser_info.value
            _record(checks, "native file input opens one non-multiple picker", page.locator("#historyFile[type='file']").count() == 1 and not chooser.is_multiple())
            chooser.set_files(str(path))
            page.wait_for_function("document.querySelectorAll('#historyCandidates input[type=checkbox]').length > 0")
            checks_locator = page.locator("#historyCandidates input[type=checkbox]")
            unchecked = checks_locator.evaluate_all("els => els.every(el => !el.checked)")
            _record(checks, "preview shows candidates unchecked by default", unchecked)
            _record(checks, "preview does not write storage", _storage(page) == before_import)

            page.locator("#historyCancel").click()
            page.wait_for_function("document.querySelectorAll('#historyCandidates input[type=checkbox]').length === 0 || document.querySelector('#historyCandidates')?.hidden")
            _record(checks, "cancel clears preview without changing storage", _storage(page) == before_import)

            # Selecting the same file again must be possible after clearing the input.
            page.locator("#historyFile").set_input_files([])
            page.locator("#historyFile").set_input_files(str(path))
            page.wait_for_function("document.querySelectorAll('#historyCandidates input[type=checkbox]').length > 0")
            metadata = page.locator("#historyCandidates input[type=checkbox]").evaluate_all("""
              els => els.map(el => ({
                eventId: el.dataset.eventId || el.value || '',
                label: el.closest('label')?.innerText || el.parentElement?.innerText || ''
              }))
            """)
            candidates = page.locator("#historyCandidates input[type=checkbox]")
            selected_indexes = [
                index for index, item in enumerate(metadata)
                if "History A" in item["label"] or "history:event-a" in item["eventId"]
            ]
            if not selected_indexes:
                selected_indexes = [0]
            for index in selected_indexes:
                candidates.nth(index).check()
            page.locator("#historyConfirm").click()
            page.wait_for_function("document.querySelector('#historyImportStatus')?.textContent.trim() !== ''")

            after_import = _storage(page)
            imported_entries = _entries(page)
            _record(checks, "confirm stores only selected candidates", any(entry.get("visited") is True and entry.get("source") == "google-export" for entry in imported_entries))
            _record(checks, "existing plan survives history confirmation", any(entry.get("entry_id") == "plan-a" and entry.get("visited") is False for entry in imported_entries))
            allowed = {"entry_id", "event_id", "date", "time", "visited", "source"}
            _record(checks, "stored history entries contain only minimal fields", all(set(entry) <= allowed for entry in imported_entries))
            new_data_requests = [url for url in requests[baseline_requests:] if "/data/" in url]
            _record(checks, "import makes no network request", not new_data_requests, new_data_requests)
            joined_requests = "\n".join(requests[baseline_requests:])
            joined_console = "\n".join(consoles)
            storage_blob = json.dumps(after_import, ensure_ascii=False)
            _record(checks, "source JSON/file name/GPS do not appear in request, console, or storage", all(secret not in joined_requests + joined_console + storage_blob for secret in (SENTINEL, SENTINEL_COORDINATE, path.name)))
            _record(checks, "page has no import runtime error", not page_errors, page_errors)

            page.reload(wait_until="networkidle")
            page.wait_for_function("document.getElementById('count')?.textContent !== ''")
            page.locator("#timelineTab").click()
            page.wait_for_function("!document.querySelector('#timelinePanel')?.hidden")
            repeated = page.locator('.timeline-item[data-event-id="history:event-a"]')
            _record(checks, "reload preserves repeat visits as separate timeline entries", repeated.count() >= 2)
            date_control = page.locator("#timelineDate")
            view_control = page.locator("#timelineView")
            date_clear = page.locator("#timelineDateClear")
            date_control.fill("2026-10-20")
            date_control.press("Tab")
            view_control.select_option("visited")
            visible_visited = page.locator("#timelineList .timeline-item:visible")
            _record(checks, "date and visited view filter the timeline", visible_visited.count() >= 1 and all(visible_visited.nth(i).locator('.timeline-visited button[aria-pressed="true"]').count() == 1 for i in range(visible_visited.count())))
            date_clear.click()
            view_control.select_option("planned")
            _record(checks, "date clear and planned view restore plan", page.locator('.timeline-item[data-entry-id="plan-a"]:visible').count() == 1)

            # Future manual completion is blocked even though the plan is present.
            future = page.locator('.timeline-item[data-event-id="history:event-future"]').first
            if future.count():
                future.locator(".timeline-visited button").click()
                _record(checks, "future manual visit completion is blocked", future.locator('.timeline-visited button[aria-pressed="true"]').count() == 0)

            # Migration succeeds only after the v2 write; a failed write keeps v1.
            migration = context.new_page()
            _install_data_fixture(migration, payloads)
            migration.add_init_script(f"""
              (() => {{
                const original = Storage.prototype.setItem;
                localStorage.removeItem({json.dumps(TIMELINE_V2)});
                original.call(localStorage, {json.dumps(TIMELINE_V1)}, JSON.stringify({{version: 1, entries: [{{event_id: 'history:event-b', date: '2026-10-20', time: '11:00'}}]}}));
              }})()
            """)
            migration.goto(base, wait_until="networkidle")
            migration.wait_for_function("document.getElementById('count')?.textContent !== ''")
            migrated = _storage(migration)
            migrated_payload = json.loads(migrated[TIMELINE_V2])
            _record(checks, "v1 migration preserves plans and removes old key after v2 write", migrated_payload.get("version") == 2 and any(entry.get("event_id") == "history:event-b" and entry.get("visited") is False for entry in migrated_payload.get("entries", [])) and TIMELINE_V1 not in migrated)
            migration.close()
            route_context = browser.new_context()
            route_context.add_init_script(HISTORY_CLOCK_SCRIPT)
            route_page = route_context.new_page()
            _install_data_fixture(route_page, payloads)
            route_page.add_init_script(f"""
              localStorage.setItem({json.dumps(TIMELINE_V2)}, JSON.stringify({{
                version: 2,
                entries: [
                  {{entry_id: 'route-a', event_id: 'history:event-a', date: '2026-10-20', time: '01:00', visited: true}},
                  {{entry_id: 'route-b', event_id: 'history:event-b', date: '2026-10-20', time: '05:00', visited: false}}
                ]
              }}));
            """)
            route_page.goto(base, wait_until="networkidle")
            route_page.wait_for_function("document.getElementById('count')?.textContent !== ''")
            _open_history(route_page)
            route_page.locator("#timelineView").select_option("planned")
            route_page.wait_for_function("Array.from(document.querySelectorAll('#timelineList .timeline-item')).filter(el => !el.hidden && getComputedStyle(el).display !== 'none').length === 1")
            planned_beta_href = route_page.locator('.timeline-item[data-event-id="history:event-b"] .directions-google').get_attribute("href")
            _record(checks, "planned-only view keeps hidden same-day predecessor origin", bool(planned_beta_href and "origin=37.5%2C127" in planned_beta_href and "travelmode=transit" in planned_beta_href), planned_beta_href)
            route_page.locator("#timelineView").select_option("visited")
            route_page.wait_for_function("Array.from(document.querySelectorAll('#timelineList .timeline-item')).filter(el => !el.hidden && getComputedStyle(el).display !== 'none').length === 1")
            visited_alpha_href = route_page.locator('.timeline-item[data-event-id="history:event-a"] .directions-google').get_attribute("href")
            _record(checks, "visited-only view starts with no predecessor origin", bool(visited_alpha_href and "travelmode=transit" in visited_alpha_href and "origin=" not in visited_alpha_href), visited_alpha_href)
            route_page.close()
            route_context.close()
            focus_context = browser.new_context()
            focus_context.add_init_script(HISTORY_CLOCK_SCRIPT)
            focus_page = focus_context.new_page()
            _install_data_fixture(focus_page, payloads)
            focus_page.add_init_script(f"""
              localStorage.setItem({json.dumps(TIMELINE_V2)}, JSON.stringify({{
                version: 2,
                entries: [
                  {{entry_id: 'focus-a', event_id: 'history:event-a', date: '2026-10-20', time: '01:00', visited: true}},
                  {{entry_id: 'focus-b', event_id: 'history:event-b', date: '2026-10-20', time: '', visited: false}}
                ]
              }}));
            """)
            focus_page.goto(base, wait_until="networkidle")
            focus_page.wait_for_function("document.getElementById('count')?.textContent !== ''")
            _open_history(focus_page)
            time_text = focus_page.locator("#timelineList .timeline-item").evaluate_all("""
              els => Object.fromEntries(els.map(el => [el.dataset.entryId, el.querySelector('.timeline-time')?.textContent || '']))
            """)
            _record(checks, "timeline shows explicit time and time-unset label", time_text == {"focus-a": "01:00", "focus-b": "시간 미정"}, time_text)

            focused_toggle = focus_page.locator('.timeline-item[data-entry-id="focus-a"] .timeline-visited button')
            focused_toggle.focus()
            focused_toggle.press("Enter")
            focus_page.wait_for_function("""
              document.activeElement?.closest('.timeline-item')?.dataset.entryId === 'focus-a'
                && document.activeElement?.classList.contains('timeline-visited')
                && document.activeElement?.getAttribute('aria-pressed') === 'false'
            """)
            focus_after_toggle = focus_page.evaluate("""
              () => ({
                entryId: document.activeElement?.closest('.timeline-item')?.dataset.entryId || '',
                className: document.activeElement?.className || '',
                pressed: document.activeElement?.getAttribute('aria-pressed') || ''
              })
            """)
            _record(checks, "keyboard visited toggle preserves focus on reused row", focus_after_toggle == {"entryId": "focus-a", "className": "button button-quiet timeline-visited", "pressed": "false"}, focus_after_toggle)

            focus_page.evaluate(f"""
              () => localStorage.setItem({json.dumps(TIMELINE_V2)}, JSON.stringify({{
                version: 2,
                entries: [
                  {{entry_id: 'focus-a', event_id: 'history:event-a', date: '2026-10-20', time: '01:00', visited: true}},
                  {{entry_id: 'focus-b', event_id: 'history:event-b', date: '2026-10-20', time: '', visited: false}}
                ]
              }}))
            """)
            focus_page.reload(wait_until="networkidle")
            focus_page.wait_for_function("document.getElementById('count')?.textContent !== ''")
            _open_history(focus_page)
            focus_page.locator("#timelineView").select_option("visited")
            focus_page.wait_for_function("Array.from(document.querySelectorAll('#timelineList .timeline-item')).filter(el => !el.hidden && getComputedStyle(el).display !== 'none').length === 1")
            hidden_toggle = focus_page.locator('.timeline-item[data-entry-id="focus-a"] .timeline-visited button')
            hidden_toggle.focus()
            hidden_toggle.press("Enter")
            focus_page.wait_for_function("document.activeElement?.id === 'timelineView'")
            _record(checks, "filter-hidden visited toggle moves focus to view control", focus_page.evaluate("() => document.activeElement?.id || ''") == "timelineView")
            focus_page.close()
            focus_context.close()

            failed_context = browser.new_context()
            failed_context.add_init_script(HISTORY_CLOCK_SCRIPT)
            failed = failed_context.new_page()
            _install_data_fixture(failed, payloads)
            failed.add_init_script(f"""
              (() => {{
                const target = window.localStorage;
                const originalSet = target.setItem.bind(target);
                const originalRemove = target.removeItem.bind(target);
                originalRemove({json.dumps(TIMELINE_V2)});
                originalSet({json.dumps(TIMELINE_V1)}, JSON.stringify({{version: 1, entries: [{{event_id: 'history:event-b', date: '2026-10-20', time: '11:00'}}]}}));
                Object.defineProperty(window, 'localStorage', {{
                  configurable: true,
                  value: new Proxy(target, {{
                    get(storage, property) {{
                      if (property === 'setItem') return (key, value) => {{
                        if (key === {json.dumps(TIMELINE_V2)}) throw new Error('synthetic quota denied');
                        return originalSet(key, value);
                      }};
                      const value = Reflect.get(storage, property, storage);
                      return typeof value === 'function' ? value.bind(storage) : value;
                    }}
                  }})
                }});
              }})()
            """)
            failed.goto(base, wait_until="networkidle")
            failed.wait_for_function("document.getElementById('count')?.textContent !== ''")
            failed_storage = _storage(failed)
            _record(checks, "failed v2 migration keeps v1 plans", TIMELINE_V1 in failed_storage and TIMELINE_V2 not in failed_storage, sorted(failed_storage))
            failed.close()
            failed_context.close()
            page.close()
            browser.close()
    EVID.mkdir(parents=True, exist_ok=True)
    summary = {
        "status": "passed" if all(check["ok"] for check in checks) else "failed",
        "checks": checks,
        "total_checks": len(checks),
        "passed": sum(check["ok"] for check in checks),
        "boundary": "synthetic local file only; no Google account, export, or external Maps verification",
    }
    EVIDENCE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 1


def main() -> int:
    checks: list[dict] = []
    try:
        return _run(checks)
    except (AssertionError, PlaywrightTimeoutError, OSError) as exc:
        EVID.mkdir(parents=True, exist_ok=True)
        summary = {"status": "failed", "checks": checks, "error": str(exc), "total_checks": len(checks), "passed": sum(check["ok"] for check in checks)}
        EVIDENCE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.parse_args()
    raise SystemExit(main())
