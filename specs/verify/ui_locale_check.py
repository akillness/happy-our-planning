"""Spec 1.6 map-shell, localization, ended-state, and accessibility verifier.

This is a browser gate authoring surface.  It serves ``web/public`` read-only,
routes one deterministic catalog through the established tile/data helpers, and
uses real Leaflet plus real pointer/keyboard events.  It must not be treated as
public-data or deployment proof.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build import build_index, build_pages
from map_viewport_check import _wait_for_current_popup_settle, _wait_for_stable_map_fit
from run_ui_check import (
    EVID,
    FIXED_CLOCK_SCRIPT,
    PUBLIC,
    _initial_ui_fixture_payloads,
    _install_data_fixture,
    _new_page,
    _run_metadata,
    serve,
)

EVIDENCE = EVID / "ui-locale-check.json"
VIEWPORTS = [(1440, 900), (1280, 800), (1024, 540), (740, 360), (667, 375), (640, 360), (568, 320), (360, 800), (390, 844)]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PRE_UI_RED = {
    "command": "python3 /tmp/notchima_ui_red_probe.py",
    "launcher": CHROME,
    "viewport": "1440x900",
    "observed": {
        "selector": "filters",
        "rect": {"left": 440, "top": 16, "right": 1000, "bottom": 126, "width": 560, "height": 110},
        "centerError": 0,
        "bottomGap": 774,
        "languageToggle": False,
    },
    "exit_code": 1,
}


def _single_catalog_payloads() -> dict:
    """Build one catalog containing active, ended, future-closed, and paginatable rows."""
    payloads = _initial_ui_fixture_payloads()
    rows = payloads["events.json"]
    rows[0].update({
        "link_status": "verified",
        "link_checked_at": "2026-10-14T15:00:00+00:00",
        "link_url": "https://www.busan.go.kr/active",
        "link_reason": None,
    })
    ended = dict(rows[0])
    ended.update({
        "id": "ui-fixture:ended",
        "name": "서울 지난 가족 음악축제",
        "description": "원문 행사 설명을 보존하는 종료 행사",
        "start_date": "2026-10-01",
        "end_date": "2026-10-14",
        "status": "Closed",
        "url": "https://fixture/ended",
        "link_url": "https://www.busan.go.kr/ended",
        "address": "서울특별시 종로구 가족로 1", "lat": 37.575, "lng": 126.955,
    })
    rows.append(ended)
    start_only = dict(rows[0])
    start_only.update({
        "id": "ui-fixture:start-only-ended",
        "name": "서울 시작일 지난 가족 음악축제",
        "description": "종료일 없는 과거 시작일 행사",
        "start_date": "2026-10-01",
        "end_date": None,
        "status": "Closed",
        "url": "https://fixture/start-only-ended",
        "link_url": "https://www.busan.go.kr/start-only-ended", "lat": 37.585, "lng": 126.965,
    })
    rows.append(start_only)
    future_closed = dict(rows[0])
    future_closed.update({
        "id": "ui-fixture:future-closed",
        "name": "서울 미래 가족 음악축제",
        "description": "미래 행사지만 신청만 마감된 원문 행사",
        "start_date": "2026-11-01",
        "end_date": "2026-11-30",
        "application_start": "2026-01-01",
        "application_end": "2026-10-14",
        "status": "Open",
        "display_status": "마감",
        "link_url": "https://www.busan.go.kr/future-closed", "lat": 37.595, "lng": 126.975,
    })
    rows.append(future_closed)
    for index in range(12):
        item = dict(rows[0])
        item.update({
            "id": f"ui-fixture:page-{index}",
            "name": f"서울 카탈로그 행사 {index + 1}",
            "description": "페이지 상태 보존용 가족 행사",
            "url": f"https://fixture/page-{index}",
            "lat": 37.50 + index * 0.01, "lng": 126.90 + index * 0.01,
        })
        rows.append(item)
    payloads["facets.json"] = {"theme": {"축제": len(rows)}, "age_band": {"어린이": len(rows)}}
    payloads["regions.json"] = [{
        "sido": "서울특별시", "count": len(rows), "slug": "seoul", "centroid": [37.56, 126.94],
    }]
    payloads["updated.json"].update({"total_active": len(rows), "source_mode": "fixture", "collection_error_count": 0})
    return payloads


def _record(checks: list[dict], name: str, ok: bool, detail: object = "") -> None:
    checks.append({"check": name, "ok": bool(ok), "detail": detail})


def _rect(page, selector: str) -> dict | None:
    locator = page.locator(selector)
    if not locator.count():
        return None
    box = locator.first.bounding_box()
    if not box:
        return None
    return {
        "left": box["x"], "top": box["y"], "right": box["x"] + box["width"],
        "bottom": box["y"] + box["height"], "width": box["width"], "height": box["height"],
    }
def _bbox(locator, include_layout: bool = False) -> dict | None:
    box = locator.bounding_box()
    if not box:
        return None
    result = {
        "left": box["x"], "top": box["y"], "right": box["x"] + box["width"],
        "bottom": box["y"] + box["height"], "width": box["width"], "height": box["height"],
    }
    if include_layout:
        result.update(locator.evaluate("el => ({layoutWidth: el.offsetWidth, layoutHeight: el.offsetHeight})"))
    return result
def _reachable(locator, width: int, height: int) -> tuple[bool, dict | None]:
    box = _bbox(locator)
    if not locator.is_visible() or not box:
        return False, box
    return (box["width"] > 0 and box["height"] > 0
            and box["left"] >= -2 and box["top"] >= -2
            and box["right"] <= width + 2 and box["bottom"] <= height + 2), box
def _wait_for_pending_route(page, pending: dict[str, object], key: str, attempts: int = 200) -> None:
    for _ in range(attempts):
        if key in pending:
            return
        page.wait_for_timeout(25)
    raise AssertionError(f"{key} failure route was not captured during bounded Playwright pump")


def _contrast(rgb: dict | list | tuple, background: tuple[int, int, int]) -> float:
    values = rgb if isinstance(rgb, (list, tuple)) else [rgb.get(key, 0) for key in ("r", "g", "b")]
    def channel(value: float) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    fg = sum(channel(float(value)) * weight for value, weight in zip(values, (0.2126, 0.7152, 0.0722)))
    bg = sum(channel(float(value)) * weight for value, weight in zip(background, (0.2126, 0.7152, 0.0722)))
    return (max(fg, bg) + 0.05) / (min(fg, bg) + 0.05)

def _is_red(rgb: object) -> bool:
    values = rgb if isinstance(rgb, (list, tuple)) else []
    return len(values) >= 3 and float(values[0]) > float(values[1]) * 1.25 and float(values[0]) > float(values[2]) * 1.1

def _wait_ready(page) -> None:
    page.wait_for_function("document.getElementById('count')?.textContent !== ''")
    page.wait_for_function("window.__notchimaMap && document.querySelector('#map.leaflet-container, #map .leaflet-container')", timeout=10000)

def _pager_state(page) -> dict:
    return page.evaluate("""
    () => {
      const text = document.querySelector('#pageInfo')?.textContent?.trim() || '';
      const pageMatch = text.match(/Page\\s+(\\d+)\\s+of\\s+(\\d+)/i)
        || text.match(/(\\d+)\\s*\\/\\s*(\\d+)\\s*페이지/);
      const rangeMatch = text.match(/\\((\\d+)[–-](\\d+)\\s+of\\s+(\\d+)/i)
        || text.match(/전체\\s*(\\d+)건\\s*중\\s*(\\d+)[–-](\\d+)번째/);
      const cards = [...document.querySelectorAll('#list .card')];
      return {
        text,
        current: pageMatch ? Number(pageMatch[1]) : null,
        total: pageMatch ? Number(pageMatch[2]) : null,
        from: rangeMatch ? Number(rangeMatch[rangeMatch[0].startsWith('전체') ? 2 : 1]) : null,
        to: rangeMatch ? Number(rangeMatch[rangeMatch[0].startsWith('전체') ? 3 : 2]) : null,
        resultCount: Number((document.querySelector('#count')?.textContent || '').trim()) || 0,
        rowCount: cards.length,
        titles: cards.map(card => card.querySelector('.card-title')?.textContent?.trim() || ''),
        hrefs: cards.map(card => card.querySelector("a[href^='https://']")?.href || ''),
      };
    }
    """)


def _wait_for_stable_pager(page) -> None:
    page.wait_for_function("""
    () => {
      const panel = document.querySelector('#resultsPanel');
      const toggle = document.querySelector('#resultsToggle');
      const menu = document.querySelector('#filterMenu');
      const scroll = document.querySelector('#resultsScroll');
      const rect = panel?.getBoundingClientRect();
      const scrollRect = scroll?.getBoundingClientRect();
      const text = document.querySelector('#pageInfo')?.textContent?.trim() || '';
      const pageMatch = text.match(/Page\\s+(\\d+)\\s+of\\s+(\\d+)/i)
        || text.match(/(\\d+)\\s*\\/\\s*(\\d+)\\s*페이지/);
      const titles = [...document.querySelectorAll('#list .card-title')].map(el => el.textContent.trim());
      if (!panel || !toggle || !menu || !scroll || menu.open || panel.dataset.collapsed !== 'false'
          || toggle.getAttribute('aria-expanded') !== 'true' || !rect || rect.width < 200 || rect.height <= 0
          || !scrollRect || scrollRect.width <= 0 || scrollRect.height <= 0 || !pageMatch) return false;
      const signature = JSON.stringify({page: Number(pageMatch[1]), total: Number(pageMatch[2]), count: titles.length, titles});
      const same = window.__notchimaLocalePagerSignature === signature;
      window.__notchimaLocalePagerSignature = signature;
      return same;
    }
    """, timeout=5000)


def _open_results_drawer(page) -> bool:
    toggle = page.locator("#resultsToggle")
    panel = page.locator("#resultsPanel")
    menu = page.locator("#filterMenu")
    if toggle.count() != 1 or panel.count() != 1:
        return False
    if menu.count() == 1 and menu.evaluate("el => el.open"):
        page.keyboard.press("Escape")
        page.wait_for_function("document.querySelector('#filterMenu')?.open === false")
    if panel.get_attribute("data-collapsed") == "true":
        toggle.click()
    page.wait_for_function("""
    () => {
      const panel = document.querySelector('#resultsPanel');
      const toggle = document.querySelector('#resultsToggle');
      const menu = document.querySelector('#filterMenu');
      const rect = panel?.getBoundingClientRect();
      return Boolean(panel && toggle && menu && !menu.open && panel.dataset.collapsed === 'false'
        && toggle.getAttribute('aria-expanded') === 'true' && rect && rect.width >= 200 && rect.height > 0);
    }
    """, timeout=5000)
    _wait_for_stable_pager(page)
    return panel.get_attribute("data-collapsed") == "false"


def _filter_snapshot(page) -> dict:
    state = _pager_state(page)
    state["page"] = {"current": state["current"], "total": state["total"], "text": state["text"]}
    state.update(page.evaluate("""
    () => {
      const params = new URL(location.href).searchParams;
      return {
        url: location.href,
        form: {
          q: document.querySelector('#q')?.value || '',
          from: document.querySelector('#from')?.value || '',
          to: document.querySelector('#to')?.value || '',
          sido: document.querySelector('#sido')?.value || '',
          applicable: Boolean(document.querySelector('#applyable')?.checked),
          family: Boolean(document.querySelector('#kids')?.checked),
        },
        query: params.get('q') || '',
        nextDisabled: Boolean(document.querySelector('#pageNext')?.disabled),
      };
    }
    """))
    return state

MAP_ANCHOR = 'state.map = window.L.map("map"'


def _install_map_observer(page) -> None:
    app_source = (PUBLIC / "app.js").read_text(encoding="utf-8")
    if app_source.count(MAP_ANCHOR) != 1:
        raise AssertionError(f"map instrumentation anchor count != 1: {app_source.count(MAP_ANCHOR)}")
    instrumented = app_source.replace(MAP_ANCHOR, f'window.__notchimaMap = {MAP_ANCHOR}', 1)
    page.route("**/app.js", lambda route: route.fulfill(status=200, content_type="application/javascript", body=instrumented))


def _launch_options() -> dict:
    options = {"headless": True}
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if executable:
        options["executable_path"] = executable
    return options


def _run(checks: list[dict]) -> int:
    catalog = _single_catalog_payloads()
    with serve(PUBLIC) as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch(**_launch_options())
        context = browser.new_context()
        context.add_init_script(FIXED_CLOCK_SCRIPT)
        pages = []
        try:
            for width, height in VIEWPORTS:
                page = _new_page(context, width, height)
                pages.append(page)
                _install_map_observer(page)
                _install_data_fixture(page, catalog)
                page.goto(base, wait_until="networkidle")
                _wait_ready(page)
                shell = page.evaluate("""
                () => {
                  const box = selector => { const el = document.querySelector(selector); if (!el) return null; const r = el.getBoundingClientRect(); return {left:r.left, top:r.top, right:r.right, bottom:r.bottom, width:r.width, height:r.height}; };
                  const doc = document.scrollingElement;
                  const panel = document.querySelector('#resultsPanel');
                  const scroll = document.querySelector('#resultsScroll');
                  return {
                    map: box('#map'), search: box('#searchDock'), results: box('#resultsPanel'),
                    doc: {width: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth,
                          height: doc.scrollHeight, clientHeight: doc.clientHeight},
                    collapsed: panel?.dataset.collapsed, expanded: document.querySelector('#resultsToggle')?.getAttribute('aria-expanded'),
                    resultsOverflow: scroll ? getComputedStyle(scroll).overflowY : '',
                  };
                }
                """)
                viewport = f"{width}x{height}"
                map_box = shell["map"]
                map_ok = bool(map_box and abs(map_box["left"]) <= 1 and abs(map_box["top"]) <= 1
                              and abs(map_box["right"] - width) <= 1 and abs(map_box["bottom"] - height) <= 1)
                _record(checks, f"{viewport} 지도 전체 뷰포트", map_ok, shell)
                dock = shell["search"]
                dock_ok = bool(dock and abs((dock["left"] + dock["width"] / 2) - width / 2) <= 2
                              and 12 <= height - dock["bottom"] <= 24)
                _record(checks, f"{viewport} 하단 중앙 검색 dock 12–24px", dock_ok, shell)
                results = shell["results"]
                results_ok = bool(results and 12 <= width - results["right"] <= 24 and shell["resultsOverflow"] in ("auto", "scroll"))
                _record(checks, f"{viewport} 우측 결과·내부 스크롤", results_ok, shell)
                _record(checks, f"{viewport} 문서 스크롤·가로 넘침 없음",
                        shell["doc"]["width"] <= width + 1 and shell["doc"]["height"] <= height + 1, shell["doc"])
                hooks = page.evaluate("""() => ["searchDock", "filterMenu", "filterToggle", "resultsPanel", "resultsToggle", "resultsScroll", "languageToggle"].every(id => Boolean(document.getElementById(id)))""")
                _record(checks, f"{viewport} 명시적 UI DOM hooks", hooks)
                filter_toggle = page.locator("#filterToggle")
                results_toggle = page.locator("#resultsToggle")
                results_panel = page.locator("#resultsPanel")
                compact_filter = width <= 640 or height <= 560
                if compact_filter and results_panel.get_attribute("data-collapsed") != "true":
                    results_toggle.click()
                    page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
                filter_toggle.click()
                page.wait_for_function("document.querySelector('#filterMenu')?.open === true")
                page.wait_for_function("""
                compact => {
                  const drawer = document.querySelector('#filterMenu .filter-drawer');
                  const panel = document.querySelector('#resultsPanel');
                  const toggle = document.querySelector('#resultsToggle');
                  const body = document.querySelector('#resultsScroll');
                  if (!drawer || !panel || !toggle || !body) return false;
                  const drawerRect = drawer.getBoundingClientRect();
                  const panelRect = panel.getBoundingClientRect();
                  const bodyRect = body.getBoundingClientRect();
                  const drawerStyle = getComputedStyle(drawer);
                  const bodyStyle = getComputedStyle(body);
                  const drawerVisible = drawerStyle.display !== 'none' && drawerStyle.visibility !== 'hidden' && drawerRect.width > 0 && drawerRect.height > 0;
                  const bodyVisible = bodyStyle.display !== 'none' && bodyStyle.visibility !== 'hidden' && bodyRect.width > 0 && bodyRect.height > 0;
                  return drawerVisible && (compact
                    ? panel.dataset.collapsed === 'true' && toggle.getAttribute('aria-expanded') === 'false' && !bodyVisible
                    : bodyVisible && panelRect.width >= 300);
                }
                """, arg=compact_filter)
                filter_box = _bbox(page.locator("#filterMenu"))
                filter_content_box = _bbox(page.locator("#filterMenu .filter-drawer"))
                results_box = _bbox(results_panel)
                overlap = bool(filter_box and results_box and filter_box["left"] < results_box["right"] and filter_box["right"] > results_box["left"] and filter_box["top"] < results_box["bottom"] and filter_box["bottom"] > results_box["top"])
                content_overlap = bool(filter_content_box and results_box and filter_content_box["left"] < results_box["right"] and filter_content_box["right"] > results_box["left"] and filter_content_box["top"] < results_box["bottom"] and filter_content_box["bottom"] > results_box["top"])
                full_results_panel = bool(results_box and results_box["width"] >= 300)
                filter_geometry_ok = bool(filter_box and filter_content_box and ((full_results_panel and not compact_filter and not overlap and not content_overlap) if not compact_filter else results_panel.get_attribute("data-collapsed") == "true"))
                _record(checks, f"{viewport} 필터·결과 실제 패널 비겹침/모바일 상호배타", filter_geometry_ok, {"filter": filter_box, "filterContent": filter_content_box, "results": results_box, "fullResultsPanel": full_results_panel, "resultsCollapsed": results_panel.get_attribute("data-collapsed"), "resultsAriaExpanded": results_toggle.get_attribute("aria-expanded"), "overlap": overlap, "contentOverlap": content_overlap, "mode": "exclusive" if compact_filter else "coexist"})
                filter_toggle.press("Escape")
                page.wait_for_function("document.querySelector('#filterMenu')?.open === false")
                if width in (1440, 390) or height <= 375:
                    page.evaluate("() => { window.__typedSearchMarker = 'preserved'; }")
                    before_lang = page.evaluate("() => document.documentElement.lang")
                    page.locator("#q").fill("가족")
                    page.locator("#q").press("Enter")
                    page.wait_for_function("new URL(location.href).searchParams.get('q') === '가족'")
                    typed_enter = page.evaluate("expectedLang => window.__typedSearchMarker === 'preserved' && document.documentElement.lang === expectedLang && new URL(location.href).searchParams.get('q') === '가족'", before_lang)
                    _record(checks, f"{width}x{height} 검색 Enter 문서·언어·q 보존", typed_enter, page.url)
                if height > 375 and height <= 560:
                    if results_panel.get_attribute("data-collapsed") == "true":
                        results_toggle.click()
                        page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'false'")
                    _wait_for_stable_pager(page)
                    compact_pager_controls = page.locator("#pager button")
                    compact_pager_controls.first.scroll_into_view_if_needed()
                    compact_pager_boxes = compact_pager_controls.evaluate_all("els => els.filter(el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return !el.hidden && s.display !== 'none' && r.width > 0 && r.height > 0; }).map(el => { const r = el.getBoundingClientRect(); return {width:r.width, height:r.height, layoutWidth:el.offsetWidth, layoutHeight:el.offsetHeight, top:r.top, bottom:r.bottom}; })")
                    compact_pager_targets_ok = compact_pager_controls.count() == 2 and len(compact_pager_boxes) == 2 and all(item["layoutWidth"] >= 44 and item["layoutHeight"] >= 44 and item["width"] >= 43.999 and item["height"] >= 43.999 and item["top"] >= -2 and item["bottom"] <= height + 2 for item in compact_pager_boxes)
                    _record(checks, f"{viewport} compact disclosure pager 물리적 open·44px target", compact_pager_targets_ok, {"pager": compact_pager_boxes, "count": compact_pager_controls.count(), "resultsCollapsed": results_panel.get_attribute("data-collapsed")})
                if height <= 375:
                    compact = page.evaluate("""() => {
                      const box = selector => {
                        const el = document.querySelector(selector);
                        if (!el || el.hidden || getComputedStyle(el).display === 'none') return null;
                        const r = el.getBoundingClientRect();
                        return {left:r.left, top:r.top, right:r.right, bottom:r.bottom, width:r.width, height:r.height};
                      };
                      const overlaps = (a, b) => a && b && a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
                      const boxes = {brand: box('.brand-tile'), tools: box('.map-tools'), search: box('#searchDock'), results: box('#resultsPanel'), map: box('#map'), attribution: box('.leaflet-control-attribution')};
                      return {
                        ...boxes,
                        overlaps: {
                          toolsBrand: overlaps(boxes.tools, boxes.brand),
                          toolsSearch: overlaps(boxes.tools, boxes.search),
                          toolsResultsCollapsed: overlaps(boxes.tools, boxes.results),
                          brandSearch: overlaps(boxes.brand, boxes.search),
                        },
                        corridor: boxes.search && boxes.tools ? boxes.search.top - boxes.tools.bottom : null,
                      };
                    }""")
                    overlap_free = all(value is False for value in compact["overlaps"].values())
                    attribution = compact["attribution"]
                    attribution_ok = bool(attribution and attribution["left"] >= -1 and attribution["right"] <= width + 1 and attribution["top"] >= -1 and attribution["bottom"] <= height + 1 and attribution["bottom"] <= compact["search"]["top"] + 1)
                    compact_geometry_ok = bool(compact["brand"] and compact["tools"] and compact["search"] and compact["map"] and overlap_free and compact["corridor"] >= 56 and compact["map"]["width"] >= 56 and attribution_ok)
                    _record(checks, f"{viewport} 단축 높이 도구·브랜드·검색·저작권 비겹침·지도 56px corridor", compact_geometry_ok, compact)
                    if width in (568, 640):
                        _record(checks, f"{viewport} 결과 접힘 도구·결과 패널 실제 rect 비겹침", compact["overlaps"]["toolsResultsCollapsed"] is False, {"tools": compact["tools"], "results": compact["results"], "overlap": compact["overlaps"]["toolsResultsCollapsed"]})
                    toggle = page.locator("#resultsToggle")
                    toggle.scroll_into_view_if_needed()
                    toggle_box = _bbox(page.locator("#resultsToggle"), include_layout=True)
                    toggle_ready = bool(toggle_box and toggle_box["layoutWidth"] >= 44 and toggle_box["layoutHeight"] >= 44 and toggle_box["width"] >= 43.999 and toggle_box["height"] >= 43.999 and 0 <= toggle_box["top"] <= height and toggle_box["bottom"] <= height)
                    initial_collapsed = shell["collapsed"] == "true" and shell["expanded"] == "false"
                    _record(checks, f"{viewport} 결과 접힘 44px 토글·자연 스크롤 도달", initial_collapsed and toggle_ready, {"collapsed": initial_collapsed, "toggle": toggle_box})
                    page.locator("#filterToggle").click()
                    page.wait_for_function("document.querySelector('#filterMenu')?.open === true")
                    filter_results_exclusive = page.evaluate("() => document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
                    page.locator("#filterToggle").press("Escape")
                    page.wait_for_function("document.querySelector('#filterMenu')?.open === false")
                    if toggle_ready:
                        toggle.click()
                        page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'false'")
                    results_filter_exclusive = page.evaluate("() => document.querySelector('#resultsPanel')?.dataset.collapsed === 'false' && document.querySelector('#filterMenu')?.open === false")
                    if width in (568, 640):
                        tools_results_expanded = page.evaluate("""
                        () => {
                          const box = selector => { const el = document.querySelector(selector); if (!el || el.hidden || getComputedStyle(el).display === 'none') return null; const r = el.getBoundingClientRect(); return {left:r.left, top:r.top, right:r.right, bottom:r.bottom, width:r.width, height:r.height}; };
                          const overlaps = (a, b) => a && b && a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
                          const tools = box('.map-tools');
                          const results = box('#resultsPanel');
                          return {tools, results, overlap: overlaps(tools, results)};
                        }
                        """)
                        _record(checks, f"{viewport} 결과 펼침 도구·결과 패널 비겹침", tools_results_expanded["overlap"] is False, tools_results_expanded)
                    pager_controls = page.locator("#pager button")
                    if pager_controls.count():
                        pager_controls.first.scroll_into_view_if_needed()
                    pager_boxes = pager_controls.evaluate_all("els => els.filter(el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return !el.hidden && s.display !== 'none' && r.width > 0 && r.height > 0; }).map(el => { const r = el.getBoundingClientRect(); return {width:r.width, height:r.height, layoutWidth:el.offsetWidth, layoutHeight:el.offsetHeight, top:r.top, bottom:r.bottom}; })")
                    pager_targets_ok = pager_controls.count() == 2 and len(pager_boxes) == 2 and all(item["layoutWidth"] >= 44 and item["layoutHeight"] >= 44 and item["width"] >= 43.999 and item["height"] >= 43.999 and item["top"] >= -2 and item["bottom"] <= height + 2 for item in pager_boxes)
                    result_action = page.locator("#resultsScroll .card-actions .button-quiet").last
                    result_action.scroll_into_view_if_needed()
                    results_scroll_box = _bbox(page.locator("#resultsScroll"))
                    result_action_box = _bbox(result_action, include_layout=True)
                    result_scroll_top = page.locator("#resultsScroll").evaluate("el => el.scrollTop")
                    result_top = result_action_box["top"] if result_action_box else None
                    result_bottom = result_action_box["bottom"] if result_action_box else None
                    result_left = result_action_box["left"] if result_action_box else None
                    result_right = result_action_box["right"] if result_action_box else None
                    scroll_right = results_scroll_box["right"] if results_scroll_box else None
                    scroll_bottom = results_scroll_box["bottom"] if results_scroll_box else None
                    result_action_clickable = page.evaluate("el => { const r = el.getBoundingClientRect(); const hit = document.elementFromPoint(r.left + Math.min(8, r.width / 2), r.top + Math.min(8, r.height / 2)); return Boolean(hit && (hit === el || el.contains(hit)) && getComputedStyle(el).pointerEvents !== 'none'); }", result_action.element_handle()) if result_action_box else False
                    result_action_ok = bool(results_scroll_box and result_action_box and results_scroll_box["height"] >= 72 and result_action_box["layoutWidth"] >= 44 and result_action_box["layoutHeight"] >= 44 and result_action_box["width"] >= 43.999 and result_action_box["height"] >= 43.999 and result_left >= results_scroll_box["left"] - 2 and result_right <= scroll_right + 2 and result_top >= results_scroll_box["top"] - 2 and result_bottom <= scroll_bottom + 2 and result_left >= -2 and result_right <= width + 2 and result_top >= -2 and result_bottom <= height + 2 and result_scroll_top > 0 and result_action_clickable)
                    if result_action_ok:
                        result_action.click()
                        page.wait_for_function("document.querySelector('#detail[open]') !== null")
                    physical_detail_open = page.locator("#detail[open]").count() == 1
                    if physical_detail_open:
                        page.keyboard.press("Escape")
                        page.wait_for_function("document.querySelector('#detail[open]') === null")
                    _record(checks, f"{viewport} 필터·결과 상호배타·pager·결과 action 완전가시·실클릭", filter_results_exclusive and results_filter_exclusive and pager_targets_ok and result_action_ok and physical_detail_open, {"filterWhileResultsCollapsed": filter_results_exclusive, "resultsExpandedFilterClosed": results_filter_exclusive, "pager": pager_boxes, "pagerTargetsOk": pager_targets_ok, "resultsScroll": results_scroll_box, "action": result_action_box, "scrollTop": result_scroll_top, "clickable": result_action_clickable, "detailOpen": physical_detail_open})
                visual = page.evaluate("""
                () => {
                  const selectors = ['#searchDock .search-label input', '#filterMenu', '#resultsPanel', '.leaflet-control-attribution'];
                  const channels = value => (value.match(/\\d+(?:\\.\\d+)?/g) || []).slice(0, 3).map(Number);
                  return selectors.map(selector => {
                    const el = document.querySelector(selector);
                    if (!el) return {selector, missing: true};
                    const style = getComputedStyle(el);
                    const bg = channels(style.backgroundColor);
                    const alphaMatch = style.backgroundColor.match(/rgba?\\([^)]*,\\s*([\\d.]+)\\)/);
                    const alpha = Number(alphaMatch?.[1] || 1);
                    return {selector, background: style.backgroundColor, bg, alpha, opacity: style.opacity, text: channels(style.color)};
                  });
                }
                """)
                visual_ok = bool(visual) and all(
                    not item.get("missing")
                    and item["alpha"] >= 0.88
                    and item["opacity"] == "1"
                    and _contrast(item["text"], tuple(round(item["bg"][i] * item["alpha"] + 255 * (1 - item["alpha"])) for i in range(3))) >= 4.5
                    and _contrast(item["text"], tuple(round(item["bg"][i] * item["alpha"]) for i in range(3))) >= 4.5
                    for item in visual
                )
                _record(checks, f"{viewport} cream surface alpha·양쪽 배경 대비", visual_ok, visual)
                if width <= 390:
                    if page.locator("#resultsPanel").evaluate("el => el.dataset.collapsed !== 'true'"):
                        page.locator("#resultsToggle").click()
                        page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
                    page.locator("#filterToggle").click()
                    page.wait_for_function("document.querySelector('#filterMenu')?.open === true")
                    filter_targets = page.locator("#filterToggle, #resultsToggle, #languageToggle, #reset, #locateBtn").evaluate_all(
                        "els => els.filter(el => !el.hidden && getComputedStyle(el).display !== 'none').map(el => { const r = el.getBoundingClientRect(); return {id: el.id, width:r.width, height:r.height, layoutWidth:el.offsetWidth, layoutHeight:el.offsetHeight}; })"
                    )
                    filter_target_ids = {item["id"] for item in filter_targets}
                    required_filter_target_ids = {"filterToggle", "resultsToggle", "languageToggle", "reset", "locateBtn"}
                    filter_targets_ok = required_filter_target_ids.issubset(filter_target_ids) and all(item["layoutWidth"] >= 44 and item["layoutHeight"] >= 44 and item["width"] >= 43.999 and item["height"] >= 43.999 for item in filter_targets)
                    _record(checks, f"{viewport} 필터 open·Reset 포함 44px 타깃", filter_targets_ok, filter_targets)
                    page.locator("#filterToggle").press("Escape")
                    page.wait_for_function("document.querySelector('#filterMenu')?.open === false")
                    if page.locator("#resultsPanel").evaluate("el => el.dataset.collapsed !== 'true'"):
                        page.locator("#resultsToggle").click()
                        page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
                    targets = filter_targets
                else:
                    targets = page.locator("#filterToggle, #resultsToggle, #languageToggle, #reset, #pagePrev, #pageNext, #locateBtn").evaluate_all(
                        "els => els.filter(el => !el.hidden && getComputedStyle(el).display !== 'none').map(el => { const r = el.getBoundingClientRect(); return {id:el.id, width:r.width, height:r.height, layoutWidth:el.offsetWidth, layoutHeight:el.offsetHeight}; })"
                    )
                    _record(checks, f"{viewport} 주요 조작 44px 타깃", bool(targets) and all(item["layoutWidth"] >= 44 and item["layoutHeight"] >= 44 and item["width"] >= 43.999 and item["height"] >= 43.999 for item in targets), targets)
                marker_targets = page.locator(".leaflet-marker-icon").evaluate_all(
                    "els => els.filter(el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return !el.hidden && s.display !== 'none' && r.width > 0 && r.height > 0; }).map(el => { const r = el.getBoundingClientRect(); return {width:r.width, height:r.height, layoutWidth:el.offsetWidth, layoutHeight:el.offsetHeight, pointer:getComputedStyle(el).pointerEvents}; })"
                )
                _record(checks, f"{viewport} visible Leaflet marker 44px pointer target", bool(marker_targets) and all(item["layoutWidth"] >= 44 and item["layoutHeight"] >= 44 and item["width"] >= 43.999 and item["height"] >= 43.999 and item["pointer"] != "none" for item in marker_targets), marker_targets)
                if width <= 390:
                    retained = page.evaluate("""
                    () => {
                      const labels = Array.from(document.querySelectorAll('#searchDock label[for]'));
                      return {
                        labels: labels.length > 0 && labels.every(label => label.textContent.trim().length > 0),
                        errors: document.querySelector('#loadError')?.getAttribute('role') === 'alert' && Boolean(document.querySelector('#retry')),
                        attribution: Boolean(document.querySelector('.leaflet-control-attribution')?.textContent.trim()),
                        locateStatus: document.querySelector('#locateHint')?.getAttribute('role') === 'status',
                      };
                    }
                    """)
                    _record(checks, f"{viewport} 필드 라벨·오류 안내·Leaflet 저작권 보존", all(retained.values()), retained)
                    mobile_ok = shell["collapsed"] == "true" and shell["expanded"] == "false" and bool(shell["results"] and shell["results"]["left"] >= 56)
                    _record(checks, f"{viewport} 결과 기본 접힘·우측 drawer·지도 56px", mobile_ok, shell)
                    page.locator("#filterToggle").focus()
                    focus_ring = page.evaluate("""() => {
                      const summary = document.querySelector('#filterToggle');
                      if (!summary || document.activeElement !== summary) return false;
                      const style = getComputedStyle(summary);
                      return (parseFloat(style.outlineWidth) >= 2 && style.outlineStyle !== 'none' && parseFloat(style.outlineOffset) <= 0)
                        || style.boxShadow.includes('inset');
                    }""")
                    _record(checks, f"{viewport} 필터 요약 키보드 초점 표시", focus_ring)
                    page.locator("#filterToggle").press("Enter")
                    page.wait_for_function("document.querySelector('#filterMenu')?.open === true")
                    filter_open = page.locator("#filterMenu").evaluate("el => el.open")
                    page.locator("#filterToggle").press("Escape")
                    page.wait_for_function("document.querySelector('#filterMenu')?.open === false")
                    filter_closed_focus = page.evaluate("() => document.activeElement?.id === 'filterToggle'")
                    _record(checks, f"{viewport} 조건 팝업 키보드 open/close·초점 복원", filter_open and filter_closed_focus)
                    page.locator("#resultsToggle").click()
                    page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'false'")
                    drawer_open = page.locator("#resultsPanel").evaluate("el => el.dataset.collapsed === 'false'")
                    if width <= 390:
                        mobile_filter_closed = page.locator("#filterMenu").evaluate("el => el.open === false")
                        mobile_page_next = page.locator("#pageNext")
                        mobile_multiple_pages = mobile_page_next.count() == 1 and not mobile_page_next.is_disabled()
                        if mobile_page_next.count():
                            mobile_page_next.scroll_into_view_if_needed()
                        result_targets = page.locator("#resultsToggle, #languageToggle, #pagePrev, #pageNext, #locateBtn").evaluate_all(
                            "els => els.filter(el => !el.hidden && getComputedStyle(el).display !== 'none').map(el => { const r = el.getBoundingClientRect(); return {id: el.id, width:r.width, height:r.height, layoutWidth:el.offsetWidth, layoutHeight:el.offsetHeight}; })"
                        )
                        result_target_ids = {item["id"] for item in result_targets}
                        required_result_target_ids = {"resultsToggle", "languageToggle", "pagePrev", "pageNext", "locateBtn"}
                        result_targets_ok = required_result_target_ids.issubset(result_target_ids) and all(item["layoutWidth"] >= 44 and item["layoutHeight"] >= 44 and item["width"] >= 43.999 and item["height"] >= 43.999 for item in result_targets)
                        targets = result_targets
                        _record(checks, f"{viewport} drawer·필터 배타·다중 페이지·pager 44px 타깃", drawer_open and mobile_filter_closed and mobile_multiple_pages and filter_targets_ok and result_targets_ok, {"drawerOpen": drawer_open, "filterClosed": mobile_filter_closed, "multiplePages": mobile_multiple_pages, "filterTargets": filter_targets, "resultTargets": result_targets})
                    drawer_occlusion_ok = page.evaluate("""() => {
                      const drawer = document.querySelector('#resultsPanel');
                      if (!drawer) return false;
                      const d = drawer.getBoundingClientRect();
                      const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
                      return ['.brand-tile', '.map-tools', '.map-tools button'].every(selector => {
                        const element = document.querySelector(selector);
                        return !element || !overlaps(d, element.getBoundingClientRect());
                      });
                    }""")
                    _record(checks, f"{viewport} 확장 결과 drawer 지도 chrome 비가림", drawer_occlusion_ok)
                    page.locator("#filterToggle").click()
                    page.wait_for_function("document.querySelector('#filterMenu')?.open === true")
                    page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
                    exclusive = page.locator("#resultsPanel").evaluate("el => el.dataset.collapsed === 'true'")
                    _record(checks, f"{viewport} 모바일 조건·결과 상호배타 확장", drawer_open and exclusive)
                    page.locator("#filterToggle").click()
                reduced_page = page
                cdp = reduced_page.context.new_cdp_session(reduced_page)
                cdp.send("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}, {"name": "prefers-reduced-transparency", "value": "reduce"}]})
                reduced_media = reduced_page.evaluate("() => window.matchMedia('(prefers-reduced-transparency: reduce)').matches")
                reduced_page.wait_for_function("""
                () => ['#searchDock .search-label input', '#filterMenu', '#resultsPanel', '.leaflet-control-attribution'].every(selector => {
                  const el = document.querySelector(selector);
                  if (!el) return false;
                  const style = getComputedStyle(el);
                  const channels = style.backgroundColor.match(/\\d+(?:\\.\\d+)?/g) || [];
                  const alpha = channels.length > 3 ? Number(channels[3]) : 1;
                  return alpha >= 1 && (style.backdropFilter === 'none' || style.backdropFilter === '') && style.opacity === '1';
                })
                """, timeout=5000)
                reduced = reduced_page.evaluate("""
                () => ['#searchDock .search-label input', '#filterMenu', '#resultsPanel', '.leaflet-control-attribution'].map(selector => {
                  const el = document.querySelector(selector);
                  if (!el) return {selector, missing: true};
                  const s = getComputedStyle(el);
                  const channels = s.backgroundColor.match(/\\d+(?:\\.\\d+)?/g) || [];
                  return {selector, background: s.backgroundColor, alpha: channels.length > 3 ? Number(channels[3]) : 1, blur: s.backdropFilter, opacity: s.opacity};
                })
                """)
                reduced_ok = bool(reduced_media) and bool(reduced) and all(not item.get("missing") and item["alpha"] >= 1 and item["blur"] in ("none", "") and item["opacity"] == "1" for item in reduced)
                _record(checks, f"{viewport} reduced-transparency 불투명·blur 해제", reduced_ok, {"mediaMatches": reduced_media, "surfaces": reduced})
                cdp.detach()
            failure_page = _new_page(context, 640, 360)
            failure_pending: dict[str, object] = {}
            def defer_failure_locale(route):
                failure_pending["route"] = route
            failure_page.route("**/locales.json", defer_failure_locale)
            failure_page.goto(base + "?lang=en", wait_until="domcontentloaded")
            _wait_for_pending_route(failure_page, failure_pending, "route")
            failure_filter = failure_page.locator("#filterToggle")
            failure_filter_reachable, failure_filter_box = _reachable(failure_filter, 640, 360)
            if not failure_filter_reachable:
                raise AssertionError(f"catalog failure filter toggle not physically reachable: {failure_filter_box}")
            failure_filter.click()
            failure_page.wait_for_function("document.querySelector('#filterMenu')?.open === true")
            failure_pending.pop("route").fulfill(status=503, content_type="application/json", body="{}")
            failure_page.wait_for_function("document.querySelector('#loadError:not([hidden])') !== null")
            failure_page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'false'")
            failure_error_reachable, failure_error_box = _reachable(failure_page.locator("#loadError"), 640, 360)
            failure_retry_reachable, failure_retry_box = _reachable(failure_page.locator("#retry"), 640, 360)
            failure_state = failure_page.evaluate("""
            () => {
              const text = document.body.innerText || "";
              const dynamic = Array.from(document.querySelectorAll("[data-i18n]"))
                .map(node => node.textContent.trim())
                .filter(value => /^[A-Za-z][\\w-]*\\.[A-Za-z][\\w.-]*$/.test(value));
              return {
                lang: document.documentElement.lang,
                loadedFalse: window.NotchimaI18n?.isLoaded?.() === false,
                errorRole: document.querySelector("#loadError")?.getAttribute("role") || "",
                errorText: document.querySelector("#loadError")?.innerText || "",
                freshness: document.querySelector("#freshness")?.textContent || "",
                resultHint: document.querySelector("#resultHint")?.textContent || "",
                resultsTitle: document.querySelector("#resultsTitle")?.textContent || "",
                searchLabel: document.querySelector("#searchDock .search-label")?.textContent || "",
                dynamicKeys: dynamic,
                filterOpen: document.querySelector('#filterMenu')?.open === true,
                bodyHasKorean: /[가-힣]/.test(text),
              };
            }
            """)
            failure_state.update({"errorReachable": failure_error_reachable, "retryReachable": failure_retry_reachable,
                                  "errorBox": failure_error_box, "retryBox": failure_retry_box,
                                  "filterBox": failure_filter_box})
            failure_ok = (failure_state["lang"] == "en" and failure_state["loadedFalse"]
                          and failure_state["errorRole"] == "alert"
                          and "불러오지 못했어요" in failure_state["errorText"]
                          and "Could not load language data" in failure_state["errorText"]
                          and "Please reload" in failure_state["errorText"]
                          and failure_state["freshness"] == "번역 설정을 불러오지 못했어요."
                          and failure_state["resultHint"] == "잠시 후 다시 시도해 주세요."
                          and bool(failure_state["resultsTitle"] and any("가" <= char <= "힣" for char in failure_state["resultsTitle"]))
                          and bool(failure_state["searchLabel"] and any("가" <= char <= "힣" for char in failure_state["searchLabel"]))
                          and failure_state["bodyHasKorean"] and not failure_state["dynamicKeys"]
                          and failure_state["filterOpen"] is False
                          and failure_state["errorReachable"] and failure_state["retryReachable"])
            _record(checks, "locales.json 503 lang=en도 KO baked UI·명확한 한영 오류·키 누출 없음", failure_ok, failure_state)
            static_failure_page = _new_page(context, 360, 320)
            static_failure_html = build_pages.render_page(catalog["events.json"][0], checks={}, now=dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc))
            static_failure_page.route("**/events/ui-fixture-locale-failure.html*", lambda route: route.fulfill(status=200, content_type="text/html", body=static_failure_html))
            static_failure_page.route("**/locales.json", lambda route: route.fulfill(status=503, content_type="application/json", body="{}"))
            static_failure_page.goto(base + "events/ui-fixture-locale-failure.html?lang=en", wait_until="domcontentloaded")
            static_failure_page.wait_for_selector("h1")
            static_failure_state = static_failure_page.evaluate("""
            () => {
              const text = document.body.innerText || "";
              const values = Array.from(document.querySelectorAll("[data-i18n], [data-status-key]"))
                .map(node => node.textContent.trim());
              return {
                lang: document.documentElement.lang,
                loadedFalse: window.NotchimaI18n?.isLoaded?.() === false,
                heading: document.querySelector("h1")?.textContent || "",
                schedule: document.querySelector('[data-i18n="detail.schedule"]')?.textContent || "",
                bodyHasKorean: /[가-힣]/.test(text),
                dynamicKeys: values.filter(value => /^[A-Za-z][\\w-]*\\.[A-Za-z][\\w.-]*$/.test(value)),
              };
            }
            """)
            static_failure_ok = (static_failure_state["lang"] == "en" and static_failure_state["loadedFalse"]
                                 and static_failure_state["heading"] and static_failure_state["schedule"] == "일정: "
                                 and static_failure_state["bodyHasKorean"] and not static_failure_state["dynamicKeys"])
            _record(checks, "정적 상세 locales.json 503도 원문·KO baked 라벨 보존", static_failure_ok, static_failure_state)
            sparse_page = _new_page(context, 360, 320)
            sparse_event = catalog["events.json"][0]
            sparse_html = build_pages.render_page(sparse_event, checks={}, now=dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc))
            sparse_status_key = sparse_html.split('data-status-key="', 1)[1].split('"', 1)[0]
            sparse_display_status = build_index.derive_status(sparse_event, "2026-10-15")
            sparse_catalog = json.loads((PUBLIC / "locales.json").read_text(encoding="utf-8"))
            for sparse_language in ("ko", "en"):
                for sparse_key in ("detail.region", "detail.venue", sparse_status_key):
                    sparse_catalog[sparse_language].pop(sparse_key, None)
            sparse_page.route("**/events/ui-fixture-sparse.html*", lambda route: route.fulfill(status=200, content_type="text/html", body=sparse_html))
            sparse_page.route("**/locales.json", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(sparse_catalog, ensure_ascii=False)))
            sparse_states = {}
            for sparse_language in ("ko", "en"):
                sparse_page.goto(base + f"events/ui-fixture-sparse.html?lang={sparse_language}", wait_until="domcontentloaded")
                sparse_page.wait_for_selector("h1")
                sparse_page.wait_for_function("window.NotchimaI18n?.isLoaded?.() === true")
                sparse_states[sparse_language] = sparse_page.evaluate("""
                () => {
                  const text = document.body.innerText || "";
                  const values = Array.from(document.querySelectorAll("[data-i18n], [data-status-key], [data-i18n-template]"))
                    .map(node => node.textContent.trim());
                  return {
                    lang: document.documentElement.lang,
                    loaded: window.NotchimaI18n?.isLoaded?.() === true,
                    title: document.querySelector("h1")?.textContent || "",
                    region: document.querySelector('[data-i18n-template="detail.region"]')?.textContent || "",
                    venue: document.querySelector('[data-i18n-template="detail.venue"]')?.textContent || "",
                    address: document.querySelector(".detail-address")?.textContent || "",
                    status: document.querySelector(".status-value")?.textContent || "",
                    statusKey: document.querySelector("[data-status-key]")?.getAttribute("data-status-key") || "",
                    back: document.querySelector('a[href="index.html"]')?.textContent.trim() || "",
                    dynamicKeys: values.filter(value => /^[A-Za-z][\\w-]*\\.[A-Za-z][\\w.-]*$/.test(value)),
                    bodyHasKorean: /[가-힣]/.test(text),
                  };
                }
                """)
            sparse_address_value = str(sparse_event.get("address") or "정보 없음")
            sparse_venue_value = str(sparse_event.get("venue") or "정보 없음")
            sparse_address_labels = {language: sparse_catalog[language]["detail.address"] for language in ("ko", "en")}
            sparse_expected_back = {language: sparse_catalog[language]["staticPage.backToAll"] for language in ("ko", "en")}
            sparse_ok = all(
                state["lang"] == sparse_language and state["loaded"] and state["title"] == sparse_event["name"]
                and state["statusKey"] == sparse_status_key
                and state["region"] == f"지역: {sparse_event['sido']}"
                and state["venue"] == f"장소: {sparse_venue_value}"
                and state["address"] == f"{sparse_address_labels[sparse_language]}{sparse_address_value}"
                and state["status"] == sparse_display_status
                and state["back"] == sparse_expected_back[sparse_language]
                and sparse_address_labels[sparse_language] and sparse_expected_back[sparse_language]
                and state["bodyHasKorean"] and not state["dynamicKeys"]
                for sparse_language, state in sparse_states.items()
            )
            _record(checks, "정적 상세 부분 locales 누락을 KO·EN에서 원문·baked 라벨·상태와 유효 번역으로 보존", sparse_ok, sparse_states)
            sparse_page.close()
            static_failure_page.close()
            failure_page.close()
            locale = pages[-1]
            locale.goto(base + "?lang=ko", wait_until="networkidle")
            _wait_ready(locale)
            ko = locale.evaluate("""
            () => ({lang: document.documentElement.lang, api: Boolean(window.NotchimaI18n), title: document.querySelector('#resultsTitle')?.textContent,
                    name: document.querySelector('#list .card-title')?.textContent || '', location: document.querySelector('#list .card-location')?.textContent || '',
                    originalHref: document.querySelector("#list .card:first-child a[href^='https://']")?.href || ''})
            """)
            ko_event = next((event for event in catalog["events.json"] if event.get("name") == ko["name"]), None)
            ko_expected_location = " ".join(str(value) for value in ((ko_event or {}).get("sido"), (ko_event or {}).get("sigungu")) if value)
            ko_expected_href = (ko_event or {}).get("link_url") or ""
            ko_initial_ok = (ko["lang"] == "ko" and ko["api"] and ko_event is not None and ko["name"] == ko_event.get("name") and ko["location"] == ko_expected_location and ko["originalHref"] == ko_expected_href)
            _record(checks, "KO 초기 html/API·동일 fixture 원문 행사값", ko_initial_ok, {"rendered": ko, "expected": {"name": (ko_event or {}).get("name"), "location": ko_expected_location, "originalHref": ko_expected_href}})
            ko_markers = locale.locator(".leaflet-marker-icon").evaluate_all("els => els.filter(el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return !el.hidden && s.display !== 'none' && r.width > 0 && r.height > 0; }).map(el => { const r = el.getBoundingClientRect(); return {width:r.width, height:r.height, pointer:getComputedStyle(el).pointerEvents}; })")
            _record(checks, "KO visible Leaflet marker pointer target", bool(ko_markers) and all(item["width"] >= 44 and item["height"] >= 44 and item["pointer"] != "none" for item in ko_markers), ko_markers)
            locale.locator("#q").fill("가족")
            try:
                locale.wait_for_function("new URL(location.href).searchParams.get('q') === '가족'", timeout=5000)
            except PlaywrightTimeoutError as exc:
                q_timeout_state = locale.evaluate("""
                () => ({q: document.querySelector('#q')?.value || '', count: document.querySelectorAll('#list .card').length,
                        page: document.querySelector('#pageInfo')?.textContent || '', titles: [...document.querySelectorAll('#list .card-title')].map(el => el.textContent.trim())})
                """)
                raise AssertionError(f"q filter URL timeout: {q_timeout_state}") from exc
            locale.locator("#filterToggle").click()
            locale.select_option("#sido", "서울특별시")
            locale.locator("#filterToggle").click()
            locale.wait_for_function("new URL(location.href).searchParams.get('sido') === '서울특별시'")
            locale.wait_for_function("document.querySelector('#filterMenu')?.open === false")
            locale_drawer_open = _open_results_drawer(locale)
            _wait_for_stable_pager(locale)
            page_before_locale = _pager_state(locale)
            seoul_events = [event for event in catalog["events.json"] if event.get("sido") == "서울특별시"]
            canonical_seoul_names = {event["name"] for event in seoul_events}
            next_page = locale.locator("#pageNext")
            page_can_advance = locale_drawer_open and next_page.count() == 1 and not next_page.is_disabled()
            if page_can_advance:
                next_page.click()
                _wait_for_stable_pager(locale)
            page_after_locale = _pager_state(locale)
            canonical_seoul_hrefs = {event["name"]: str(event.get("link_url") or "") for event in seoul_events}
            page2_before_toggle = _pager_state(locale)
            page2_after_toggle = page2_before_toggle
            page2_toggle_ok = False
            if page_can_advance and page2_before_toggle["current"] == 2:
                if locale.locator("#resultsPanel").get_attribute("data-collapsed") != "false":
                    _open_results_drawer(locale)
                _wait_for_stable_pager(locale)
                page2_before_toggle = _pager_state(locale)
                results_toggle = locale.locator("#resultsToggle")
                results_toggle.click()
                locale.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
                results_toggle.click()
                locale.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'false'")
                _wait_for_stable_pager(locale)
                page2_after_toggle = _pager_state(locale)
                expected_page2_hrefs = [canonical_seoul_hrefs.get(title, "") for title in page2_before_toggle["titles"]]
                page2_toggle_ok = (
                    page2_after_toggle["current"] == page2_before_toggle["current"]
                    and page2_after_toggle["total"] == page2_before_toggle["total"]
                    and page2_after_toggle["from"] == page2_before_toggle["from"]
                    and page2_after_toggle["to"] == page2_before_toggle["to"]
                    and page2_after_toggle["resultCount"] == page2_before_toggle["resultCount"] == len(seoul_events)
                    and page2_after_toggle["rowCount"] == page2_before_toggle["rowCount"] == len(page2_before_toggle["titles"]) == len(page2_before_toggle["hrefs"])
                    and page2_after_toggle["titles"] == page2_before_toggle["titles"]
                    and page2_after_toggle["hrefs"] == page2_before_toggle["hrefs"] == expected_page2_hrefs
                    and set(page2_after_toggle["titles"]).issubset(canonical_seoul_names)
                    and all(page2_after_toggle["hrefs"])
                )
            _record(checks, "2쪽 결과 접힘→펼침이 같은 semantic page·range·canonical title/href를 보존", page2_toggle_ok, {"before": page2_before_toggle, "after": page2_after_toggle, "canonicalHrefs": canonical_seoul_hrefs})
            page_transition_ok = (
                page_can_advance
                and page_before_locale["current"] == 1 and page_before_locale["total"] >= 2
                and page_before_locale["from"] == 1 and page_before_locale["to"] == page_before_locale["rowCount"]
                and page_before_locale["resultCount"] == len(seoul_events)
                and page_before_locale["rowCount"] == len(page_before_locale["titles"]) > 0
                and page_before_locale["rowCount"] == page_after_locale["rowCount"]
                and len(set(page_before_locale["titles"])) == page_before_locale["rowCount"]
                and set(page_before_locale["titles"]).issubset(canonical_seoul_names)
                and page_after_locale["current"] == page_before_locale["current"] + 1
                and page_after_locale["total"] == page_before_locale["total"]
                and page_after_locale["from"] == page_before_locale["to"] + 1
                and page_after_locale["to"] == page_after_locale["from"] + page_after_locale["rowCount"] - 1
                and page_after_locale["resultCount"] == len(seoul_events)
                and page_after_locale["rowCount"] == len(page_after_locale["titles"]) > 0
                and len(set(page_after_locale["titles"])) == page_after_locale["rowCount"]
                and set(page_after_locale["titles"]).issubset(canonical_seoul_names)
                and set(page_before_locale["titles"]).isdisjoint(page_after_locale["titles"])
            )
            _record(checks, "페이지네이션은 semantic 현재·전체 쪽과 canonical 행 identity/count/range를 보존", page_transition_ok, {"before": page_before_locale, "after": page_after_locale})
            _record(checks, "페이지네이션을 실제로 2쪽으로 이동", page_transition_ok, {"drawerOpen": locale_drawer_open, "before": page_before_locale, "after": page_after_locale})
            _open_results_drawer(locale)
            _wait_for_stable_pager(locale)
            locale.evaluate("() => window.__notchimaMap.setView([37.55, 126.95], 9, {animate:false})")
            locale.wait_for_function("Math.abs(window.__notchimaMap.getZoom() - 9) < 0.01")
            _wait_for_stable_pager(locale)
            before = locale.evaluate("""
            () => ({query: location.search, center: window.__notchimaMap.getCenter(), zoom: window.__notchimaMap.getZoom(),
                    name: document.querySelector('#list .card-title')?.textContent || '', locationText: document.querySelector('#list .card-location')?.textContent || '', href: document.querySelector("#list .card:first-child a[href^='https://']")?.href || ''})
            """)
            before["pager"] = _pager_state(locale)
            locale.locator("#languageToggle").focus()
            locale.locator("#languageToggle").press("Enter")
            locale.wait_for_function("document.documentElement.lang === 'en'")
            _wait_for_stable_pager(locale)
            after = locale.evaluate("""
            () => ({lang: document.documentElement.lang, query: location.search, center: window.__notchimaMap.getCenter(), zoom: window.__notchimaMap.getZoom(),
                    title: document.querySelector('#resultsTitle')?.textContent || '', name: document.querySelector('#list .card-title')?.textContent || '', locationText: document.querySelector('#list .card-location')?.textContent || '', href: document.querySelector("#list .card:first-child a[href^='https://']")?.href || ''})
            """)
            after["pager"] = _pager_state(locale)
            rendered_event = next((event for event in catalog["events.json"] if event.get("name") == before["name"]), None)
            raw_sigungu = str((rendered_event or {}).get("sigungu") or "")
            translated_sido = "Seoul" if (rendered_event or {}).get("sido") == "서울특별시" else str((rendered_event or {}).get("sido") or "")
            expected_en_location = " ".join(value for value in (translated_sido, raw_sigungu) if value)
            preserved = (rendered_event is not None and after["lang"] == "en" and parse_qs(urlparse(locale.url).query).get("lang", [None])[0] == "en"
                         and parse_qs(urlparse(locale.url).query).get("sido", [None])[0] == "서울특별시"
                         and before["pager"]["current"] == after["pager"]["current"]
                         and before["pager"]["total"] == after["pager"]["total"]
                         and before["pager"]["titles"] == after["pager"]["titles"]
                         and before["pager"]["rowCount"] == after["pager"]["rowCount"] == len(before["pager"]["titles"])
                         and abs(before["center"]["lat"] - after["center"]["lat"]) < 1e-6
                         and abs(before["center"]["lng"] - after["center"]["lng"]) < 1e-6
                         and abs(before["zoom"] - after["zoom"]) < 1e-6
                         and before["name"] == after["name"] and before["locationText"].endswith(raw_sigungu) and after["locationText"] == expected_en_location
                         and before["href"] == after["href"] and after["title"] != ko["title"])
            _record(checks, "KO→EN 토글이 semantic 페이지·행 identity/count·필터·지도·원문·href 보존", preserved, {"before": before, "after": after})
            _open_results_drawer(locale)
            locale.locator("#q").fill("서울")
            try:
                locale.wait_for_function("new URL(location.href).searchParams.get('q') === '서울' && document.querySelectorAll('#list .card').length > 0", timeout=5000)
            except PlaywrightTimeoutError as exc:
                q_timeout_state = _filter_snapshot(locale)
                raise AssertionError(f"EN q filter URL/rows timeout: {q_timeout_state}") from exc
            _wait_for_stable_pager(locale)
            en_filter_state = _pager_state(locale)
            en_filter_rows = locale.locator("#list .card").count()
            en_filter_ok = (parse_qs(urlparse(locale.url).query).get("lang", [None])[0] == "en"
                            and parse_qs(urlparse(locale.url).query).get("q", [None])[0] == "서울"
                            and en_filter_state["current"] == 1 and en_filter_state["total"] >= 1
                            and en_filter_state["from"] == 1 and en_filter_state["to"] == en_filter_state["rowCount"]
                            and en_filter_state["resultCount"] == len(seoul_events)
                            and en_filter_rows == en_filter_state["rowCount"] == len(en_filter_state["titles"]) > 0
                            and set(en_filter_state["titles"]).issubset(canonical_seoul_names))
            _record(checks, "EN 토글 뒤 q 필터 변경이 URL·semantic 페이지·canonical 행 identity/count/range에 반영", en_filter_ok, {"url": locale.url, "state": en_filter_state})
            en_card = locale.locator("#list .card").first
            en_rendered_name = en_card.locator(".card-title").inner_text().strip()
            en_catalog_matches = [event for event in catalog["events.json"] if event.get("name") == en_rendered_name]
            en_event = en_catalog_matches[0] if len(en_catalog_matches) == 1 else None
            en_rendered_href = en_card.locator("a[href^='https://']").first.get_attribute("href") if en_card.locator("a[href^='https://']").count() else ""
            en_card.locator(".card-open").click()
            locale.wait_for_function("document.querySelector('#detail[open]') !== null")
            en_detail_title = locale.locator("#detailTitle").inner_text().strip()
            en_notice = locale.locator("#detail .source-language-notice").inner_text()
            _record(checks, "EN 상세는 현재 렌더 카드의 canonical 원문 이름·언어 고지를 보존", bool(en_notice) and "source language" in en_notice and en_event is not None and en_rendered_name == en_event["name"] and en_detail_title == en_event["name"], {"notice": en_notice, "renderedCard": {"name": en_rendered_name, "href": en_rendered_href}, "canonical": (en_event or {}).get("name"), "title": en_detail_title})
            locale.keyboard.press("Escape")
            en_markers = locale.locator(".leaflet-marker-icon").evaluate_all("els => els.filter(el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return !el.hidden && s.display !== 'none' && r.width > 0 && r.height > 0; }).map(el => { const r = el.getBoundingClientRect(); return {width:r.width, height:r.height, pointer:getComputedStyle(el).pointerEvents}; })")
            _record(checks, "EN visible Leaflet marker pointer target", bool(en_markers) and all(item["width"] >= 44 and item["height"] >= 44 and item["pointer"] != "none" for item in en_markers), en_markers)
            locale.reload(wait_until="networkidle")
            _wait_ready(locale)
            _record(checks, "EN URL·저장 언어 reload 복원", locale.evaluate("() => document.documentElement.lang === 'en' && window.NotchimaI18n.getLanguage() === 'en'"))
            english_title = locale.locator("#resultsTitle").inner_text()
            locale.goto(base, wait_until="networkidle")
            _wait_ready(locale)
            stored_language = locale.evaluate("() => ({lang: document.documentElement.lang, saved: window.NotchimaI18n.getLanguage(), title: document.querySelector('#resultsTitle')?.textContent || '', hasLangParam: new URL(location.href).searchParams.has('lang')})")
            _record(checks, "저장 언어가 lang 없는 clean URL에서도 EN·번역 UI 복원", stored_language["lang"] == "en" and stored_language["saved"] == "en" and stored_language["title"] == english_title and not stored_language["hasLangParam"], stored_language)
            locale.goto(base + "?lang=en", wait_until="networkidle")
            _wait_ready(locale)
            locale.locator("#languageToggle").click()
            locale.wait_for_function("document.documentElement.lang === 'ko'")
            _record(checks, "EN→KO pointer 토글", locale.evaluate("() => document.documentElement.lang === 'ko' && new URL(location.href).searchParams.get('lang') === 'ko'"))
            def locate_query_card(query: str, target: str):
                if not _open_results_drawer(locale):
                    raise AssertionError(f"{query!r} ended/start/future query cannot open results drawer: {_filter_snapshot(locale)}")
                locale.locator("#q").fill(query)
                try:
                    locale.wait_for_function("expected => new URL(location.href).searchParams.get('q') === expected && document.querySelectorAll('#list .card').length > 0", arg=query, timeout=5000)
                except PlaywrightTimeoutError as exc:
                    snapshot = _filter_snapshot(locale)
                    raise AssertionError(
                        f"{query!r} query wait timeout: URL={snapshot['url']!r}; form={snapshot['form']!r}; "
                        f"q={snapshot['query']!r}; count={snapshot['resultCount']}; page={snapshot['page']!r}; "
                        f"visible_titles={snapshot['titles']!r}"
                    ) from exc
                _wait_for_stable_pager(locale)
                snapshot = _filter_snapshot(locale)
                max_pages = snapshot["total"] if isinstance(snapshot["total"], int) and snapshot["total"] > 0 else 1
                max_pages = min(max_pages, 25)
                for _ in range(max_pages):
                    if target in snapshot["titles"]:
                        return locale.locator("#list .card").filter(has_text=target)
                    if (snapshot["nextDisabled"] or not isinstance(snapshot["current"], int)
                            or not isinstance(snapshot["total"], int) or snapshot["current"] >= snapshot["total"]):
                        break
                    previous_page = snapshot["current"]
                    locale.locator("#pageNext").click()
                    try:
                        _wait_for_stable_pager(locale)
                    except PlaywrightTimeoutError as exc:
                        transition = _filter_snapshot(locale)
                        raise AssertionError(
                            f"{query!r} page transition timeout: URL={transition['url']!r}; form={transition['form']!r}; "
                            f"q={transition['query']!r}; count={transition['resultCount']}; page={transition['page']!r}; "
                            f"visible_titles={transition['titles']!r}; previous_page={previous_page}"
                        ) from exc
                    snapshot = _filter_snapshot(locale)
                raise AssertionError(
                    f"{query!r} target {target!r} not found after real paging: URL={snapshot['url']!r}; "
                    f"form={snapshot['form']!r}; q={snapshot['query']!r}; count={snapshot['resultCount']}; "
                    f"page={snapshot['page']!r}; visible_titles={snapshot['titles']!r}"
                )

            def popup_failure_state(stage: str) -> dict:
                return locale.evaluate("""
                stage => {
                  const map = window.__notchimaMap;
                  const popup = map?._popup;
                  const popupElement = popup?.getElement?.() || document.querySelector('.leaflet-popup');
                  const source = popup?._source;
                  const marker = source?.getElement?.() || [...document.querySelectorAll('.leaflet-marker-icon')].find(el => el.getAttribute('title') === source?.options?.title);
                  const rect = el => {
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return {left:r.left, top:r.top, right:r.right, bottom:r.bottom, width:r.width, height:r.height};
                  };
                  const center = map?.getCenter?.();
                  const latlng = popup?._latlng || source?.getLatLng?.();
                  const options = popup?.options || {};
                  return {
                    stage,
                    url: location.href,
                    viewport: {width: window.innerWidth, height: window.innerHeight},
                    map: {
                      rect: rect(document.querySelector('#map')),
                      center: center ? {lat:center.lat, lng:center.lng} : null,
                      zoom: map?.getZoom?.() ?? null,
                      animatingZoom: Boolean(map?._animatingZoom),
                      panInProgress: Boolean(map?._panAnim?._inProgress),
                      dragging: Boolean(map?.dragging?.moving?.()),
                    },
                    filter: {
                      menuOpen: Boolean(document.querySelector('#filterMenu')?.open),
                      drawerCollapsed: document.querySelector('#resultsPanel')?.dataset.collapsed || '',
                      drawerRect: rect(document.querySelector('#resultsPanel')),
                      resultsRect: rect(document.querySelector('#resultsScroll')),
                    },
                    popupNative: {
                      count: document.querySelectorAll('.leaflet-popup').length,
                      text: popupElement?.innerText || '',
                      options: {
                        autoPan: options.autoPan ?? null,
                        autoPanPadding: options.autoPanPadding ?? null,
                        minWidth: options.minWidth ?? null,
                        maxWidth: options.maxWidth ?? null,
                        maxHeight: options.maxHeight ?? null,
                      },
                      latlng: latlng ? {lat:latlng.lat, lng:latlng.lng} : null,
                      rect: rect(popupElement),
                      headRect: rect(popupElement?.querySelector('.leaflet-popup-content-wrapper') || popupElement),
                      contentRect: rect(popupElement?.querySelector('.leaflet-popup-content')),
                      tipRect: rect(popupElement?.querySelector('.leaflet-popup-tip')),
                      closeRect: rect(popupElement?.querySelector('.leaflet-popup-close-button')),
                    },
                    marker: {title: marker?.getAttribute('title') || '', rect: rect(marker)},
                    sourcePopupSettleTrace: window.__notchimaPopupProbe || null,
                    nativeTrace: (window.__notchimaLocaleNativeTrace || []).slice(-32),
                  };
                }
                """, stage)

            def close_native_popup(popup, stage: str) -> None:
                close_button = popup.locator(".leaflet-popup-close-button")
                if close_button.count() != 1:
                    state = popup_failure_state(stage)
                    raise AssertionError(f"{stage} native popup close control missing: {state}")
                try:
                    close_button.click()
                except PlaywrightTimeoutError as exc:
                    state = popup_failure_state(stage)
                    raise AssertionError(f"{stage} native popup close click timeout: {state}") from exc
                try:
                    locale.wait_for_function("() => document.querySelector('.leaflet-popup') === null", timeout=5000)
                except PlaywrightTimeoutError as exc:
                    state = popup_failure_state(stage)
                    raise AssertionError(f"{stage} native popup close timeout: {state}") from exc

            def wait_native_popup(title: str, stage: str) -> None:
                try:
                    locale.wait_for_function("title => { const p = document.querySelector('.leaflet-popup'); return p && p.innerText.includes(title); }", arg=title, timeout=5000)
                    locale.evaluate("() => { window.__notchimaPopupProbe = null; }")
                    _wait_for_current_popup_settle(locale, title)
                except PlaywrightTimeoutError as exc:
                    state = popup_failure_state(stage)
                    raise AssertionError(f"{stage} native popup wait timeout for {title!r}: {state}") from exc

            def wait_native_marker(title: str, stage: str) -> None:
                try:
                    locale.wait_for_function("title => [...document.querySelectorAll('.leaflet-marker-icon')].some(el => el.getAttribute('title') === title)", arg=title, timeout=5000)
                except PlaywrightTimeoutError as exc:
                    state = locale.evaluate("() => ({url: location.href, zoom: window.__notchimaMap?.getZoom?.() ?? null, drawer: document.querySelector('#resultsPanel')?.dataset.collapsed || '', markers: [...document.querySelectorAll('.leaflet-marker-icon')].map(el => el.getAttribute('title') || '')})")
                    raise AssertionError(f"{stage} native marker wait timeout for {title!r}: {state}") from exc


            ended_title = "서울 지난 가족 음악축제"
            ended = locate_query_card("지난 가족", ended_title)
            ended_date = ended.locator(".card-date").first
            ended_text = ended.inner_text() if ended.count() else ""
            ended_style = ended_date.evaluate("el => getComputedStyle(el).textDecorationLine") if ended_date.count() else ""
            ended_color = ended_date.evaluate("el => getComputedStyle(el).color.match(/\\d+(?:\\.\\d+)?/g).slice(0, 3).map(Number)") if ended_date.count() else []
            ended_badge = ended.locator(".badge.ended").all_inner_texts()
            _record(checks, "종료 카드 날짜 취소선·붉은 종료 텍스트", ended.count() == 1 and "종료" in ended_text and "line-through" in ended_style and _is_red(ended_color) and ended_badge, {"text": ended_text, "decoration": ended_style, "color": ended_color, "badges": ended_badge})
            if locale.locator("#resultsPanel").get_attribute("data-collapsed") != "true":
                locale.locator("#resultsToggle").click()
                locale.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
            def click_native_date_marker(title: str, stage: str) -> None:
                wait_native_marker(title, stage)
                locale.evaluate("""
                () => {
                  const map = window.__notchimaMap;
                  if (!map) return false;
                  if (!map.__notchimaLocaleTraceBound) {
                    for (const type of ['zoomstart', 'zoomend', 'dragstart', 'moveend']) {
                      map.on(type, () => {
                        const trace = window.__notchimaLocaleNativeTrace;
                        if (!Array.isArray(trace)) return;
                        const center = map.getCenter?.();
                        trace.push({type, zoom: map.getZoom?.() ?? null, center: center ? {lat: center.lat, lng: center.lng} : null});
                        if (trace.length > 128) trace.splice(0, trace.length - 128);
                      });
                    }
                    map.__notchimaLocaleTraceBound = true;
                  }
                  delete window.__notchimaFitProbe;
                  window.__notchimaLocaleNativeTrace = [];
                  return true;
                }
                """)
                _wait_for_stable_map_fit(locale)
                iteration_trace: list[dict] = []
                def record_marker_snapshot(label: str, state: dict) -> dict:
                    snapshot = {"label": label, **state}
                    iteration_trace.append(snapshot)
                    if len(iteration_trace) > 32:
                        del iteration_trace[:-32]
                    return state
                def native_trace() -> list[dict]:
                    return locale.evaluate("() => (window.__notchimaLocaleNativeTrace || []).slice(-128)")
                marker = locale.locator(f".leaflet-marker-icon[title='{title}']")

                def measure_marker() -> dict:
                    return locale.evaluate("""
                    title => {
                      const map = window.__notchimaMap;
                      const root = document.querySelector('#map');
                      const marker = [...document.querySelectorAll('.leaflet-marker-icon')].find(el => el.getAttribute('title') === title);
                      const rect = marker?.getBoundingClientRect();
                      const mapRect = root?.getBoundingClientRect();
                      const x = rect ? rect.left + rect.width / 2 : null;
                      const y = rect ? rect.top + rect.height / 2 : null;
                      const hit = x !== null && y !== null ? document.elementFromPoint(x, y) : null;
                      const owner = hit?.closest?.('.leaflet-marker-icon');
                      const maxZoom = map?.getMaxZoom?.() ?? map?.options?.maxZoom ?? null;
                      const center = map?.getCenter?.();
                      return {zoom: map?.getZoom?.() ?? null, center: center ? {lat: center.lat, lng: center.lng} : null, maxZoom: Number.isFinite(maxZoom) ? maxZoom : null,
                              rect: rect ? {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom} : null,
                              mapRect: mapRect ? {left: mapRect.left, top: mapRect.top, right: mapRect.right, bottom: mapRect.bottom} : null,
                              x, y, hitTitle: owner?.getAttribute('title') || '', hitClass: owner?.className || ''};
                    }
                    """, title)
                def wait_stable_fit(stage_label: str) -> None:
                    try:
                        _wait_for_stable_map_fit(locale)
                    except PlaywrightTimeoutError as exc:
                        try:
                            live_measure = measure_marker()
                        except Exception as measure_exc:
                            live_measure = {"measurementError": repr(measure_exc)}
                        live_state = locale.evaluate("""
                        () => {
                          const map = window.__notchimaMap;
                          const panAnim = map?._panAnim;
                          const pane = map?._mapPane?._leaflet_pos;
                          const center = map?.getCenter?.();
                          return {
                            currentDateNow: Date.now(),
                            newDateNow: new Date().getTime(),
                            dateElapsedMs: new Date().getTime() - 1791993600000,
                            performanceNow: performance.now(),
                            visibilityState: document.visibilityState,
                            hidden: document.hidden,
                            hasFocus: document.hasFocus(),
                            zoom: map?.getZoom?.() ?? null,
                            center: center ? {lat: center.lat, lng: center.lng} : null,
                            panAnim: panAnim ? {_inProgress: Boolean(panAnim._inProgress), _duration: panAnim._duration ?? null, _startTime: panAnim._startTime ?? null, _animId: panAnim._animId ?? null, elapsedMs: panAnim._startTime != null ? performance.now() - panAnim._startTime : null} : null,
                            dragging: map?.dragging ? {moving: Boolean(map.dragging.moving?.()), enabled: Boolean(map.dragging.enabled?.())} : null,
                            panePos: pane ? {x: pane.x ?? null, y: pane.y ?? null} : null,
                          };
                        }
                        """)
                        raise AssertionError(f"{stage_label} stable map fit timeout: measure={live_measure}; iterations={iteration_trace}; nativeTrace={native_trace()}; liveState={live_state}") from exc


                def center_marker(state: dict) -> dict:
                    rect = state.get("rect")
                    map_rect = state.get("mapRect")
                    if not rect or not map_rect:
                        raise AssertionError(f"{stage} native marker cannot be measured for map drag: {state}")
                    marker_x = (rect["left"] + rect["right"]) / 2
                    marker_y = (rect["top"] + rect["bottom"]) / 2
                    map_x = (map_rect["left"] + map_rect["right"]) / 2
                    map_y = (map_rect["top"] + map_rect["bottom"]) / 2
                    margin = 64
                    if map_rect["left"] + margin <= marker_x <= map_rect["right"] - margin and map_rect["top"] + margin <= marker_y <= map_rect["bottom"] - margin:
                        return state
                    start_x = map_rect["left"] + (map_rect["right"] - map_rect["left"]) * 0.2
                    start_y = map_rect["top"] + (map_rect["bottom"] - map_rect["top"]) * 0.75
                    locale.mouse.move(start_x, start_y)
                    locale.mouse.down()
                    locale.mouse.move(start_x + map_x - marker_x, start_y + map_y - marker_y, steps=6)
                    locale.wait_for_timeout(80)
                    locale.mouse.up()
                    locale.evaluate("() => { delete window.__notchimaFitProbe; }")
                    wait_stable_fit(f"{stage} drag")
                    try:
                        locale.wait_for_function("() => { const m = window.__notchimaMap; return m && !m._animatingZoom && !m._panAnim?._inProgress && !m.dragging?.moving?.(); }", timeout=3000)
                    except PlaywrightTimeoutError as exc:
                        raise AssertionError(f"{stage} native map drag did not settle while centering marker: {state}; iterations={iteration_trace}; trace={native_trace()}") from exc
                    return measure_marker()

                marker_hit = record_marker_snapshot("beforeCenter", measure_marker())
                if marker_hit["maxZoom"] is None or marker_hit["zoom"] is None:
                    raise AssertionError(f"{stage} native marker has no bounded map zoom: {marker_hit}; trace={native_trace()}")
                zoom_in = locale.locator(".leaflet-control-zoom-in")
                if zoom_in.count() != 1:
                    raise AssertionError(f"native map zoom-in control missing for {stage} marker: {marker_hit}; trace={native_trace()}")
                for _ in range(12):
                    marker_hit = center_marker(marker_hit)
                    record_marker_snapshot("afterCenter", marker_hit)
                    if marker_hit["hitTitle"] == title:
                        break
                    if marker_hit["zoom"] >= marker_hit["maxZoom"]:
                        break
                    marker_hit = record_marker_snapshot("beforeZoom", measure_marker())
                    previous_zoom = marker_hit["zoom"]
                    zoom_in.click()
                    try:
                        locale.wait_for_function("previous => { const m = window.__notchimaMap; return m && m.getZoom() > previous && !m._animatingZoom && !m._panAnim?._inProgress; }", arg=previous_zoom, timeout=3000)
                    except PlaywrightTimeoutError as exc:
                        raise AssertionError(f"{stage} native zoom-control click did not advance marker zoom: {marker_hit}; iterations={iteration_trace}; trace={native_trace()}") from exc
                    locale.evaluate("() => { delete window.__notchimaFitProbe; }")
                    wait_stable_fit(f"{stage} zoom")
                    marker_hit = record_marker_snapshot("afterZoom", measure_marker())
                if marker_hit["hitTitle"] != title:
                    raise AssertionError(f"{stage} native marker remains pointer-blocked after bounded zoom-control clicks: {marker_hit}; iterations={iteration_trace}; trace={native_trace()}")
                try:
                    marker.click()
                except PlaywrightTimeoutError as exc:
                    live_measure = measure_marker()
                    raise AssertionError(f"{stage} native marker click timeout: measure={live_measure}; iterations={iteration_trace}; trace={native_trace()}") from exc
                wait_native_popup(title, stage)
                locale.evaluate("iterations => { window.__notchimaLocaleLastIterations = iterations; }", iteration_trace)

            click_native_date_marker("서울 지난 가족 음악축제", "ended")
            popup = locale.locator(".leaflet-popup")
            popup_text = popup.inner_text()
            popup_date = popup.locator(".map-popup-meta .event-date").first
            popup_style = popup_date.evaluate("el => getComputedStyle(el).textDecorationLine") if popup_date.count() else ""
            popup_color = popup_date.evaluate("el => getComputedStyle(el).color.match(/\\d+(?:\\.\\d+)?/g).slice(0, 3).map(Number)") if popup_date.count() else []
            _record(checks, "종료 지도 native popup 날짜·붉은 종료 텍스트", "종료" in popup_text and "line-through" in popup_style and _is_red(popup_color) and popup.locator(".badge.ended").count() == 1, {"text": popup_text, "decoration": popup_style, "color": popup_color, "nativeTrace": locale.evaluate("() => (window.__notchimaLocaleNativeTrace || []).slice(-128)"), "markerIterations": locale.evaluate("() => (window.__notchimaLocaleLastIterations || []).slice(-32)")})
            close_native_popup(popup, "ended")
            if locale.locator("#resultsPanel").get_attribute("data-collapsed") == "true":
                locale.locator("#resultsToggle").click()
                locale.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'false'")
            ended.locator(".card-open").click()
            locale.wait_for_function("(title) => { const d = document.querySelector('#detail[open]'); return d && document.querySelector('#detailTitle')?.textContent.trim() === title; }", arg="서울 지난 가족 음악축제")
            detail_text = locale.locator("#detail").inner_text()
            detail_date = locale.locator("#detail .event-date").first
            detail_style = detail_date.evaluate("el => getComputedStyle(el).textDecorationLine") if detail_date.count() else ""
            detail_color = detail_date.evaluate("el => getComputedStyle(el).color.match(/\\d+(?:\\.\\d+)?/g).slice(0, 3).map(Number)") if detail_date.count() else []
            _record(checks, "종료 native 상세 날짜·붉은 종료 텍스트", "종료" in detail_text and "line-through" in detail_style and _is_red(detail_color) and locale.locator("#detail .badge.ended").count() == 1, {"text": detail_text, "decoration": detail_style, "color": detail_color})
            locale.keyboard.press("Escape")
            locale.wait_for_function("document.querySelector('#detail[open]') === null")
            start_only_card = locate_query_card("시작일 지난", "서울 시작일 지난 가족 음악축제")
            start_only_date = start_only_card.locator(".card-date").first
            start_only_text = start_only_card.inner_text() if start_only_card.count() else ""
            start_only_style = start_only_date.evaluate("el => getComputedStyle(el).textDecorationLine") if start_only_date.count() else ""
            start_only_color = start_only_date.evaluate("el => getComputedStyle(el).color.match(/\\d+(?:\\.\\d+)?/g).slice(0, 3).map(Number)") if start_only_date.count() else []
            _record(checks, "종료일 없는 과거 시작일 카드도 종료 취소선·붉은 텍스트", start_only_card.count() == 1 and "종료" in start_only_text and "line-through" in start_only_style and _is_red(start_only_color) and start_only_card.locator(".badge.ended").count() == 1, {"text": start_only_text, "decoration": start_only_style, "color": start_only_color})
            if locale.locator("#resultsPanel").get_attribute("data-collapsed") != "true":
                locale.locator("#resultsToggle").click()
                locale.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
            click_native_date_marker("서울 시작일 지난 가족 음악축제", "start-only")
            start_only_popup = locale.locator(".leaflet-popup")
            start_only_popup_text = start_only_popup.inner_text()
            start_only_popup_date = start_only_popup.locator(".map-popup-meta .event-date").first
            start_only_popup_style = start_only_popup_date.evaluate("el => getComputedStyle(el).textDecorationLine") if start_only_popup_date.count() else ""
            _record(checks, "종료일 없는 과거 시작일 native popup도 종료", "종료" in start_only_popup_text and "line-through" in start_only_popup_style and start_only_popup.locator(".badge.ended").count() == 1, {"text": start_only_popup_text, "decoration": start_only_popup_style})
            close_native_popup(start_only_popup, "start-only")
            if locale.locator("#resultsPanel").get_attribute("data-collapsed") == "true":
                locale.locator("#resultsToggle").click()
                locale.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'false'")
            start_only_card.locator(".card-open").click()
            locale.wait_for_function("(title) => { const d = document.querySelector('#detail[open]'); return d && document.querySelector('#detailTitle')?.textContent.trim() === title; }", arg="서울 시작일 지난 가족 음악축제")
            start_only_detail_text = locale.locator("#detail").inner_text()
            start_only_detail_date = locale.locator("#detail .event-date").first
            start_only_detail_style = start_only_detail_date.evaluate("el => getComputedStyle(el).textDecorationLine") if start_only_detail_date.count() else ""
            _record(checks, "종료일 없는 과거 시작일 native 상세도 종료", "종료" in start_only_detail_text and "line-through" in start_only_detail_style and locale.locator("#detail .badge.ended").count() == 1, {"text": start_only_detail_text, "decoration": start_only_detail_style})
            locale.keyboard.press("Escape")
            locale.wait_for_function("document.querySelector('#detail[open]') === null")
            future = locate_query_card("미래 가족", "서울 미래 가족 음악축제")
            future_date = future.locator(".card-date").first
            future_text = future.inner_text() if future.count() else ""
            future_style = future_date.evaluate("el => getComputedStyle(el).textDecorationLine") if future_date.count() else ""
            future_badges = future.locator(".badge").all_inner_texts()
            _record(checks, "신청마감 미래 행사 카드는 신청마감 배지·종료 취소선 금지", future.count() == 1 and "마감" in future_badges and "종료" not in future_text and future_style == "none" and not future.locator(".badge.ended").count(), {"text": future_text, "decoration": future_style, "badges": future_badges})
            if locale.locator("#resultsPanel").get_attribute("data-collapsed") != "true":
                locale.locator("#resultsToggle").click()
                locale.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
            click_native_date_marker("서울 미래 가족 음악축제", "future")
            future_popup = locale.locator(".leaflet-popup")
            future_popup_text = future_popup.inner_text()
            future_popup_date = future_popup.locator(".map-popup-meta .event-date").first
            future_popup_style = future_popup_date.evaluate("el => getComputedStyle(el).textDecorationLine") if future_popup_date.count() else ""
            _record(checks, "신청마감 미래 지도 popup은 신청마감 배지·종료 취소선 금지", "종료" not in future_popup_text and future_popup_style == "none" and future_popup.locator(".badge.closed").count() == 1 and future_popup.locator(".badge.ended").count() == 0, {"text": future_popup_text, "decoration": future_popup_style})
            close_native_popup(future_popup, "future")
            if locale.locator("#resultsPanel").get_attribute("data-collapsed") == "true":
                locale.locator("#resultsToggle").click()
                locale.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'false'")
            future.locator(".card-open").click()
            locale.wait_for_function("(title) => { const d = document.querySelector('#detail[open]'); return d && document.querySelector('#detailTitle')?.textContent.trim() === title; }", arg="서울 미래 가족 음악축제")
            future_detail = locale.locator("#detail").inner_text()
            future_detail_date = locale.locator("#detail .event-date").first
            future_detail_style = future_detail_date.evaluate("el => getComputedStyle(el).textDecorationLine") if future_detail_date.count() else ""
            future_detail_closed = locale.locator("#detail .badge.closed").count()
            future_detail_ended = locale.locator("#detail .badge.ended").count()
            _record(checks, "신청마감 미래 native 상세는 종료 아님", future_detail_closed == 1 and future_detail_ended == 0 and future_detail_style == "none" and "신청 마감" in future_detail, {"text": future_detail, "decoration": future_detail_style, "closed": future_detail_closed, "ended": future_detail_ended})
            locale.keyboard.press("Escape")
            locale.wait_for_function("document.querySelector('#detail[open]') === null")
            static_page = _new_page(locale.context, 360, 800)
            ended_event = next(event for event in catalog["events.json"] if event["id"] == "ui-fixture:ended")
            future_event = next(event for event in catalog["events.json"] if event["id"] == "ui-fixture:future-closed")
            start_only_event = next(event for event in catalog["events.json"] if event["id"] == "ui-fixture:start-only-ended")
            start_only_static_html = build_pages.render_page(start_only_event, checks={}, now=dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc))
            static_page.route("**/events/ui-fixture-start-only-ended.html*", lambda route: route.fulfill(status=200, content_type="text/html", body=start_only_static_html))
            static_page.goto(base + "events/ui-fixture-start-only-ended.html?lang=ko", wait_until="networkidle")
            start_only_static_text = static_page.locator("body").inner_text()
            start_only_static_date = static_page.locator("body p").filter(has_text="2026-10-01").first
            start_only_static_style = start_only_static_date.evaluate("el => getComputedStyle(el).textDecorationLine") if start_only_static_date.count() else ""
            start_only_static_color = start_only_static_date.evaluate("el => getComputedStyle(el).color.match(/\\d+(?:\\.\\d+)?/g).slice(0, 3).map(Number)") if start_only_static_date.count() else []
            _record(checks, "종료일 없는 과거 시작일 정적 상세도 종료 안내", "종료" in start_only_static_text and "line-through" in start_only_static_style and _is_red(start_only_static_color) and static_page.locator(".event-status .status-value[data-status-key='status.ended']").count() == 1, {"text": start_only_static_text, "decoration": start_only_static_style, "color": start_only_static_color})
            static_ended_event = dict(ended_event)
            static_ended_event["url"] = "https://www.kopis.or.kr/por/db/pblprfr/pblprfrView.do?mt20id=PF303302"
            static_ended_event["offers"] = {"url": "https://www.ticketlink.co.kr/product/66233"}
            static_info_url = static_ended_event["url"]
            static_booking_url = static_ended_event["offers"]["url"]
            ended_checks = {
                hashlib.sha256(static_info_url.encode("utf-8")).hexdigest(): {
                    "link_status": "verified",
                    "link_checked_at": "2026-10-14T15:00:00+00:00",
                    "link_url": static_info_url,
                    "link_reason": None,
                },
                hashlib.sha256(static_booking_url.encode("utf-8")).hexdigest(): {
                    "link_status": "verified",
                    "link_checked_at": "2026-10-14T15:00:00+00:00",
                    "link_url": static_booking_url,
                    "link_reason": None,
                },
            }
            static_html = build_pages.render_page(static_ended_event, checks=ended_checks, now=dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc))
            static_page.route("**/events/ui-fixture-ended.html*", lambda route: route.fulfill(status=200, content_type="text/html", body=static_html))
            static_page.goto(base + "events/ui-fixture-ended.html", wait_until="networkidle")
            static_text = static_page.locator("body").inner_text()
            static_date = static_page.locator("body p").filter(has_text="2026-10-01").first
            static_style = static_date.evaluate("el => getComputedStyle(el).textDecorationLine") if static_date.count() else ""
            static_color = static_date.evaluate("el => getComputedStyle(el).color.match(/\\d+(?:\\.\\d+)?/g).slice(0, 3).map(Number)") if static_date.count() else []
            _record(checks, "종료 정적 상세 날짜·붉은 종료 안내", "종료" in static_text and "line-through" in static_style and _is_red(static_color), {"text": static_text, "decoration": static_style, "color": static_color})
            static_toggle = static_page.locator("#languageToggle")
            toggle_box = _bbox(static_toggle, include_layout=True)
            static_page.evaluate("el => el.scrollIntoView({block: 'end'})", static_page.locator(".event-actions").element_handle())
            action = static_page.locator(".event-actions a").last
            action.scroll_into_view_if_needed()
            action_box = _bbox(action, include_layout=True)
            action_clickable = static_page.evaluate("el => { const r = el.getBoundingClientRect(); const top = document.elementFromPoint(r.left + Math.min(8, r.width / 2), r.top + Math.min(8, r.height / 2)); return !!top && (top === el || el.contains(top)) && getComputedStyle(el).pointerEvents !== 'none'; }", action.element_handle()) if action_box else False
            action_touch_ok = bool(action_box and action_box["layoutWidth"] >= 44 and action_box["layoutHeight"] >= 44 and action_box["width"] >= 43.999 and action_box["height"] >= 43.999)
            _record(checks, "360x800 정적 상세 마지막 액션 스크롤·44px 레이아웃·포인터 가능", bool(action_touch_ok and action_box["top"] >= 0 and action_box["bottom"] <= 800 and action_clickable), {"box": action_box, "clickable": action_clickable, "touch": action_touch_ok})
            toggle_ok = bool(toggle_box and toggle_box["layoutWidth"] >= 44 and toggle_box["layoutHeight"] >= 44 and toggle_box["width"] >= 43.999 and toggle_box["height"] >= 43.999)
            static_toggle.click()
            static_page.wait_for_function("document.documentElement.lang === 'en'")
            static_en_text = static_page.locator(".event-actions").inner_text()
            static_info_anchor = static_page.locator('.event-actions a[data-link-status="verified"]').filter(has_text="Official info")
            static_booking_anchor = static_page.locator('.event-actions a[data-link-status="verified"]').filter(has_text="Go to booking site")
            static_source_text = static_page.locator('p[data-i18n-template="detail.source"]').inner_text()
            static_actions_ok = ("Official info" in static_en_text and "Go to booking site" in static_en_text and static_info_anchor.count() == 1 and static_booking_anchor.count() == 1 and static_info_anchor.get_attribute("href") == static_info_url and static_booking_anchor.get_attribute("href") == static_booking_url and static_info_url in static_source_text)
            _record(checks, "360x800 정적 상세 EN 공식 정보·예매 라벨·원문 링크·44px 언어 토글", toggle_ok and static_actions_ok, {"toggle": toggle_box, "text": static_en_text, "source": static_source_text, "infoHref": static_info_anchor.get_attribute("href") if static_info_anchor.count() else None, "bookingHref": static_booking_anchor.get_attribute("href") if static_booking_anchor.count() else None})
            static_address = static_page.locator(".detail-address").inner_text()
            static_schedule = static_page.locator("[data-i18n='detail.schedule']").inner_text()
            static_notice = static_page.locator("[data-i18n='detail.sourceLanguageNotice']").inner_text()
            original_address = str(ended_event.get("address") or ended_event.get("location") or "").strip()
            static_adapter_ok = static_address.startswith("Address:") and original_address in static_address and static_schedule == "Schedule: " and "source language" in static_notice
            _record(checks, "360x800 정적 상세 EN 원문 주소·일정·언어 고지 보존", static_adapter_ok, {"address": static_address, "schedule": static_schedule, "notice": static_notice})
            kopis_source_url = static_info_url
            static_long_event = dict(static_ended_event)
            static_long_event["url"] = kopis_source_url
            static_long_checks = {
                hashlib.sha256(kopis_source_url.encode("utf-8")).hexdigest(): {
                    "link_status": "verified",
                    "link_checked_at": "2026-10-14T15:00:00+00:00",
                    "link_url": kopis_source_url,
                    "link_reason": None,
                },
                hashlib.sha256(static_booking_url.encode("utf-8")).hexdigest(): {
                    "link_status": "verified",
                    "link_checked_at": "2026-10-14T15:00:00+00:00",
                    "link_url": static_booking_url,
                    "link_reason": None,
                },
            }
            static_long_html = build_pages.render_page(static_long_event, checks=static_long_checks, now=dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc))
            static_page.route("**/events/ui-fixture-long-source.html*", lambda route: route.fulfill(status=200, content_type="text/html", body=static_long_html))
            static_page.goto(base + "events/ui-fixture-long-source.html?lang=en", wait_until="networkidle")
            static_source = static_page.locator('p[data-i18n-template="detail.source"]')
            static_source_text = static_source.inner_text() if static_source.count() else ""
            static_overflow = static_page.evaluate("""
                () => {
                    const source = document.querySelector('p[data-i18n-template="detail.source"]');
                    const body = document.body;
                    if (!source || !body) return {sensitivity: false, restored: false};
                    const authored = source.style.overflowWrap;
                    const before = {scrollWidth: source.scrollWidth, clientWidth: source.clientWidth, right: source.getBoundingClientRect().right};
                    source.style.overflowWrap = "normal";
                    const unwrapped = {scrollWidth: source.scrollWidth, clientWidth: source.clientWidth, right: source.getBoundingClientRect().right};
                    const sensitivity = unwrapped.scrollWidth > unwrapped.clientWidth || unwrapped.right > window.innerWidth + 0.5;
                    source.style.overflowWrap = authored;
                    const restored = {scrollWidth: source.scrollWidth, clientWidth: source.clientWidth, right: source.getBoundingClientRect().right};
                    return {bodyScrollWidth: body.scrollWidth, bodyClientWidth: body.clientWidth, viewport: window.innerWidth, before, unwrapped, sensitivity, restored};
                }
            """)
            static_geometry_ok = (static_overflow.get("bodyScrollWidth", 10**9) <= static_overflow.get("bodyClientWidth", 0) and static_overflow.get("sensitivity") and static_overflow.get("restored", {}).get("scrollWidth", 10**9) <= static_overflow.get("restored", {}).get("clientWidth", 0) and static_overflow.get("restored", {}).get("right", 10**9) <= static_overflow.get("viewport", 0) + 0.5 and static_overflow.get("restored", {}).get("scrollWidth", 10**9) <= static_overflow.get("restored", {}).get("clientWidth", 0))
            _record(checks, "360x800 정적 상세 긴 KOPIS 원문 URL 요소 줄바꿈·민감도·가로 overflow 없음", static_source.count() == 1 and kopis_source_url in static_source_text and static_geometry_ok, {"source": static_source_text, "overflow": static_overflow})
            future_static_html = build_pages.render_page(future_event, checks={}, now=dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc))
            static_page.route("**/events/ui-fixture-future-closed.html", lambda route: route.fulfill(status=200, content_type="text/html", body=future_static_html))
            static_page.goto(base + "events/ui-fixture-future-closed.html", wait_until="networkidle")
            future_static_text = static_page.locator("body").inner_text()
            future_static_date = static_page.locator("body p").filter(has_text="2026-11-01").first
            future_static_style = future_static_date.evaluate("el => getComputedStyle(el).textDecorationLine") if future_static_date.count() else ""
            future_static_closed = static_page.locator(".event-status .status-value[data-status-key='status.applicationClosed']")
            future_static_ended = static_page.locator(".event-status .status-value[data-status-key='status.ended']")
            future_static_status = future_static_closed.inner_text() if future_static_closed.count() else ""
            _record(checks, "신청마감 미래 정적 상세는 종료 아님", future_static_closed.count() == 1 and future_static_ended.count() == 0 and "Application closed" in future_static_status and future_static_style == "none", {"text": future_static_text, "status": future_static_status, "decoration": future_static_style})
            static_page.close()
        finally:
            for page in pages:
                page.close()
            context.close()
            browser.close()
    return 0 if all(item["ok"] for item in checks) else 1


def main() -> int:
    checks: list[dict] = []
    metadata = _run_metadata()
    running = {"status": "running", "spec_version": "1.6.0", "viewports": [f"{w}x{h}" for w, h in VIEWPORTS],
               "data_context": "single deterministic routed catalog; Leaflet tile fixture; not public-data QA",
               "pre_ui_edit_red": PRE_UI_RED, **metadata, "checks": []}
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(running, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(running, ensure_ascii=False, indent=2))
    try:
        status = _run(checks)
    except BaseException as error:
        summary = {"status": "error", "spec_version": "1.6.0", "error": f"{type(error).__name__}: {error}",
                   "partial_results": {"total_checks": len(checks), "passed": sum(item["ok"] for item in checks)},
                   "viewports": [f"{w}x{h}" for w, h in VIEWPORTS],
                   "data_context": "single deterministic routed catalog; Leaflet tile fixture; not public-data QA",
                   "pre_ui_edit_red": PRE_UI_RED, **metadata, "checks": checks}
        EVIDENCE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1
    summary = {"status": "passed" if status == 0 else "failed", "spec_version": "1.6.0",
               "total_checks": len(checks), "passed": sum(item["ok"] for item in checks),
               "viewports": [f"{w}x{h}" for w, h in VIEWPORTS],
               "data_context": "single deterministic routed catalog; Leaflet tile fixture; not public-data QA",
               "pre_ui_edit_red": PRE_UI_RED, **metadata, "checks": checks}
    EVIDENCE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
