"""Focused map-first viewport and Leaflet geometry regression harness.

The harness serves ``web/public`` read-only and routes every ``data/*.json``
request to a deterministic in-memory fixture.  It deliberately launches the
real Leaflet 1.9.4 script from the page; no production test hook is required.

Run:
    PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
      python3 specs/verify/map_viewport_check.py
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import functools
import http.server
import json
import re
import os
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build import build_index
from run_ui_check import _run_metadata, serve


EVIDENCE = Path(__file__).resolve().parent / "evidence" / "map-viewport-check.json"
TILE_PNG = (ROOT / "assets" / "brand" / "notchima-mascot-256.png").read_bytes()
TILE_URL_RE = re.compile(r"^https://[a-c]\.tile\.openstreetmap\.org/")


def _report_metadata() -> dict:
    run_metadata = _run_metadata()
    return {
        "source_hashes": run_metadata["source_hashes"],
        "source_files_present": run_metadata["source_files_present"],
        "verifier_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "command": [sys.executable, *sys.argv],
        "cwd": str(Path.cwd()),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _route_tile_fixture(page) -> dict[str, int]:
    """Serve a valid 256px PNG for deterministic Leaflet math, not live-map proof."""
    stub_count = {"count": 0}
    def fulfill(route):
        stub_count["count"] += 1
        route.fulfill(status=200, content_type="image/png", body=TILE_PNG)
    page.route(TILE_URL_RE, fulfill)
    return stub_count


def _collect_browser_errors(page, allow_console=None) -> list[str]:
    """Collect runtime errors, ignoring only an explicitly identified console case."""
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
    def on_console(message):
        if message.type != "error":
            return
        location = message.location.get("url", "") if message.location else ""
        if allow_console and allow_console(message.text, location):
            return
        errors.append(f"console.error: {message.text} ({location})")
    page.on("console", on_console)
    return errors


def _event(event_id: str, name: str, lat: float, lng: float, *,
           description: str = "가족이 함께 참여하는 행사입니다.",
           end_date: str = "2026-10-31", price: object = "free",
           sido: str = "서울특별시", sigungu: str = "종로구",
           themes: list[str] | None = None,
           categories: list[str] | None = None) -> dict:
    event = {
        "id": event_id,
        "name": name,
        "description": description,
        "event_type": "Event",
        "themes": themes or ["체험"],
        "start_date": "2026-10-15",
        "end_date": end_date,
        "application_start": "2026-01-01",
        "application_end": "2026-12-31",
        "status": "Open",
        "location": {"sido": sido, "sigungu": sigungu, "lat": lat, "lng": lng},
        "age": "7-13",
        "age_bands": ["어린이"],
        "audience": ["가족"],
        "family_friendly": True,
        "family_evidence": "명시 문구: 가족",
        "price": price,
        "url": f"https://fixture/{event_id}",
        "source": "fixture",
        "is_experience": bool(categories),
        "experience_categories": categories or [],
        "experience_summary": "직접 참여하는 가족 체험" if categories else None,
        "link_status": "unverified",
        "link_checked_at": "2026-10-14T15:00:00+00:00",
        "link_url": None,
        "link_reason": "통제 fixture",
    }
    return build_index._flatten(
        event,
        link_checks={},
        now=dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc),
    )


def _fixture_payloads() -> dict:
    long_name = (
        "가장자리에서도 가려지지 않는 아주 긴 행사 제목과 말풍선 내용 검증용 가족 과학·탐구 체험"
    )
    events = [
        _event("map:single", "서울 단일 좌표 행사", 37.5665, 126.9780, categories=["과학·탐구"]),
        _event("map:group-a", "서울 동일좌표 가족 행사 A", 37.5700, 126.9760),
        _event("map:group-b", "서울 동일좌표 가족 행사 B", 37.5700, 126.9760),
        _event("map:long", long_name, 37.5400, 126.9500,
               description=("긴 상세 문장 " * 28) + "끝", categories=["만들기·예술"]),
        _event("map:south", "제주 가장자리 행사", 33.2100, 126.5100, sido="제주특별자치도", sigungu="서귀포시"),
        _event("map:north", "강원 가장자리 행사", 38.5000, 128.6000, sido="강원특별자치도", sigungu="고성군"),
        _event("map:east", "부산 동쪽 가장자리 행사", 35.1200, 129.1800, sido="부산광역시", sigungu="해운대구"),
        _event("map:west", "인천 서쪽 가장자리 행사", 37.9000, 124.6500, sido="인천광역시", sigungu="옹진군"),
        _event("map:seoul-2", "서울 추가 행사 2", 37.5200, 126.9000),
        _event("map:seoul-3", "서울 추가 행사 3", 37.6100, 127.0200),
        _event("map:daejeon", "대전 추가 행사", 36.3500, 127.3850, sido="대전광역시", sigungu="중구"),
        _event("map:gwangju", "광주 추가 행사", 35.1600, 126.8500, sido="광주광역시", sigungu="북구"),
        _event("map:daegu", "대구 추가 행사", 35.8700, 128.6000, sido="대구광역시", sigungu="중구"),
        _event("map:ulsan", "울산 추가 행사", 35.5400, 129.3100, sido="울산광역시", sigungu="남구"),
    ]
    for event in events:
        missing = {"price", "family_evidence", "link_status"}.difference(event)
        if missing:
            raise AssertionError(f"fixture missing contract fields: {sorted(missing)}")
    regions = {}
    for event in events:
        regions.setdefault(event["sido"], 0)
        regions[event["sido"]] += 1
    return {
        "events.json": events,
        "facets.json": {"theme": {"체험": len(events)}, "age_band": {"어린이": len(events)}},
        "regions.json": [
            {"sido": sido, "count": count, "slug": sido.replace("특별시", "").replace("광역시", ""), "centroid": [36.2, 127.8]}
            for sido, count in regions.items()
        ],
        "updated.json": {
            "generated_at": "2026-10-15T01:00:00+09:00",
            "total_active": len(events),
            "source_mode": "fixture",
            "collection_error_count": 0,
        },
    }


def _route_fixture(page, payloads: dict) -> dict[str, int]:
    tile_stub_count = _route_tile_fixture(page)
    def fulfill(route):
        filename = urlparse(route.request.url).path.rsplit("/", 1)[-1]
        payload = payloads.get(filename)
        if payload is None:
            route.continue_()
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    page.route("**/data/*.json", fulfill)
    return tile_stub_count




def _record(checks: list[dict], name: str, ok: bool, detail: object = "") -> None:
    checks.append({"check": name, "ok": bool(ok), "detail": detail})

def _write_failure_evidence(checks: list[dict], error: BaseException) -> None:
    summary = {
        "status": "error",
        "error": f"{type(error).__name__}: {error}",
        "total_checks": len(checks),
        "passed": sum(item["ok"] for item in checks),
        "data_context": "deterministic routed map fixture; no public data writes",
        "viewports": ["1440x900", "1280x800", "360x800", "390x844"],
        "checks": checks,
        "partial_checks": checks,
        **_report_metadata(),
    }
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _open_filters(page) -> bool:
    menu = page.locator("#filterMenu")
    if not menu.count():
        return False
    if not menu.evaluate("el => el.open"):
        page.locator("#filterToggle").click()
        page.wait_for_function("document.querySelector('#filterMenu')?.open === true")
    return True
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
    page.wait_for_function(
        """
        () => {
          const menu = document.querySelector('#filterMenu');
          const toggle = document.querySelector('#resultsToggle');
          const panel = document.querySelector('#resultsPanel');
          const rect = panel?.getBoundingClientRect();
          const style = panel ? getComputedStyle(panel) : null;
          return Boolean(panel && toggle && menu && !menu.open
            && panel.dataset.collapsed === 'false'
            && toggle.getAttribute('aria-expanded') === 'true'
            && rect && rect.width >= 200 && rect.height > 0
            && style && style.width !== '52px');
        }
        """
    )
    _wait_for_stable_map_fit(page)
    return panel.get_attribute("data-collapsed") == "false"
def _close_results_drawer_for_marker(page) -> None:
    """Close the mobile results overlay before any physical map-marker click."""
    width = page.evaluate("() => window.innerWidth")
    if width > 640:
        return
    panel = page.locator("#resultsPanel")
    toggle = page.locator("#resultsToggle")
    if panel.count() != 1 or toggle.count() != 1:
        raise AssertionError("expected mobile results drawer controls")
    if panel.get_attribute("data-collapsed") != "true":
        toggle.click()
        page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
    if panel.get_attribute("data-collapsed") != "true":
        raise AssertionError("results drawer must be collapsed before marker click")



def _wait_ready(page) -> None:
    page.wait_for_function("document.getElementById('count')?.textContent !== ''")
    try:
        page.wait_for_function("window.__notchimaMap && document.querySelector('#map.leaflet-container, #map .leaflet-container')", timeout=5000)
    except Exception:
        print("MAP_BOOTSTRAP", page.evaluate("() => ({map: Boolean(window.__notchimaMap), L: Boolean(window.L), mapClass: document.querySelector('#map')?.className, fallback: document.querySelector('#mapFallback')?.hidden, count: document.querySelector('#count')?.textContent, mapError: window.__mapInitError})"))
        raise


def _rect(page, selector: str) -> dict | None:
    box = page.locator(selector).bounding_box() if page.locator(selector).count() else None
    if not box:
        return None
    return {"left": box["x"], "top": box["y"], "right": box["x"] + box["width"],
            "bottom": box["y"] + box["height"], "width": box["width"], "height": box["height"]}



def _geometry(page, title: str) -> dict:
    return page.evaluate(
        """
        title => {
          const map = window.__notchimaMap;
          const root = document.querySelector('#map');
          const container = map?.getContainer();
          let found = null;
          const visit = layer => {
            if (found || !layer) return;
            if (typeof layer.getLatLng === 'function' && typeof layer.getElement === 'function') {
              const icon = layer.getElement();
              if (icon && icon.getAttribute('title') === title) found = layer;
            } else if (typeof layer.eachLayer === 'function') layer.eachLayer(visit);
          };
          map?.eachLayer(visit);
          if (!map || !root || !container || !found) return {error: 'marker-not-found', title};
          const icon = found.getElement();
          const iconRect = icon.getBoundingClientRect();
          const renderedSvg = icon.querySelector('svg');
          const renderedSvgRect = (renderedSvg || icon).getBoundingClientRect();
          const rootRect = root.getBoundingClientRect();
          const containerRect = container.getBoundingClientRect();
          const point = map.latLngToContainerPoint(found.getLatLng());
          const geo = {x: containerRect.left + point.x, y: containerRect.top + point.y};
          const tip = {x: renderedSvgRect.left + renderedSvgRect.width / 2, y: renderedSvgRect.bottom};
          const head = {x: renderedSvgRect.left + renderedSvgRect.width / 2, y: renderedSvgRect.top};
          const popup = found.getPopup();
          const popupLatLng = popup?.getLatLng?.();
          return {
            title,
            latlng: {lat: found.getLatLng().lat, lng: found.getLatLng().lng},
            popupLatlng: popupLatLng ? {lat: popupLatLng.lat, lng: popupLatLng.lng} : null,
            icon: {left: iconRect.left, top: iconRect.top, width: iconRect.width, height: iconRect.height},
            renderedSvg: {left: renderedSvgRect.left, top: renderedSvgRect.top, width: renderedSvgRect.width, height: renderedSvgRect.height},
            geo, tip, head,
            tipError: Math.hypot(tip.x - geo.x, tip.y - geo.y),
            mapRoot: {left: rootRect.left, top: rootRect.top, right: rootRect.right, bottom: rootRect.bottom},
          };
        }
        """,
        title,
    )


def _popup_no_space_state(page, title: str, overlays: list[str]) -> dict:
    """Read native marker identity and the app's overlay-reserved popup room."""
    return page.evaluate(
        """
        ({title, overlays}) => {
          const map = window.__notchimaMap;
          let found = null;
          const visit = layer => {
            if (found || !layer) return;
            if (typeof layer.getLatLng === 'function' && typeof layer.getElement === 'function') {
              if (layer.getElement()?.getAttribute('title') === title) found = layer;
            } else if (typeof layer.eachLayer === 'function') layer.eachLayer(visit);
          };
          map?.eachLayer(visit);
          const markerPopup = found?.getPopup?.() || null;
          const mapNode = document.querySelector('#map');
          const mapRect = mapNode?.getBoundingClientRect?.();
          const mapSize = map?.getSize?.() || {x: 0, y: 0};
          const width = mapRect?.width || mapSize.x || 0;
          const height = mapRect?.height || mapSize.y || 0;
          const mapLeft = mapRect?.left || 0;
          const mapTop = mapRect?.top || 0;
          const mapRight = mapLeft + width;
          const mapBottom = mapTop + height;
          let left = 24, top = 24, right = 24, bottom = 24;
          const selectors = [...new Set([
            '#filters', '#filterMenu', '#resultsPanel', '.results', '#searchDock', '.search-dock', '.filter-menu',
            '.brand-tile', '.map-tools', '.leaflet-control-zoom', '.leaflet-control-attribution',
            '#mapFallback', ...(overlays || []),
          ])];
          const occupied = [];
          for (const selector of selectors) {
            for (const node of document.querySelectorAll(selector)) {
              if (node.hidden) continue;
              const styles = getComputedStyle(node);
              if (styles.display === 'none' || styles.visibility === 'hidden') continue;
              const rect = node.getBoundingClientRect();
              if (!rect.width || !rect.height || rect.right <= mapLeft || rect.left >= mapRight
                  || rect.bottom <= mapTop || rect.top >= mapBottom) continue;
              const declaredEdge = styles.getPropertyValue('--map-edge').trim().toLowerCase();
              const edge = declaredEdge || (node.id === 'mapFallback' ? 'bottom' : '');
              if (!['top', 'right', 'bottom', 'left'].includes(edge)) continue;
              if (edge === 'top') top = Math.max(top, rect.bottom - mapTop + 8);
              if (edge === 'right') right = Math.max(right, mapLeft + width - rect.left + 8);
              if (edge === 'bottom') bottom = Math.max(bottom, mapTop + height - rect.top + 8);
              if (edge === 'left') left = Math.max(left, rect.right - mapLeft + 8);
              occupied.push({selector, edge, rect: {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height}});
            }
          }
          const popupOpen = Boolean(markerPopup?.isOpen?.() && map?._popup === markerPopup);
          const markerLatLng = found?.getLatLng?.();
          const popupLatLng = markerPopup?.getLatLng?.();
          const availableWidth = width - left - right;
          const availableHeight = height - top - bottom;
          const nativeMinWidth = Number(window.L?.Popup?.prototype?.options?.minWidth);
          const rawNoSpace = (Number.isFinite(nativeMinWidth) && nativeMinWidth > 0 && availableWidth < nativeMinWidth)
            || availableHeight <= 0;
          return {
            title,
            markerFound: Boolean(found),
            markerLatlng: markerLatLng ? {lat: markerLatLng.lat, lng: markerLatLng.lng} : null,
            popupLatlng: popupLatLng ? {lat: popupLatLng.lat, lng: popupLatLng.lng} : null,
            popupOpen,
            activePopupMatches: Boolean(map?._popup === markerPopup),
            availableWidth, availableHeight, nativeMinWidth, rawNoSpace,
            contentWidth: availableWidth - 30,
            contentHeight: availableHeight - 64,
            hasSpace: availableWidth - 30 >= 50 && availableHeight - 64 > 0,
            map: {left: mapLeft, top: mapTop, width, height},
            reserved: {left, top, right, bottom},
            occupied,
          };
        }
        """,
        {"title": title, "overlays": overlays},
    )


def _popup_measure(page, title: str, overlays: list[str], *, allow_no_space_close: bool = False) -> dict:
    if allow_no_space_close:
        page.wait_for_function(
            """
            wanted => {
              const map = window.__notchimaMap;
              const popup = map?._popup;
              const sourceTitle = popup?._source?.options?.title || popup?._source?.getElement?.()?.getAttribute('title');
              return !popup?.isOpen?.() || sourceTitle === wanted;
            }
            """,
            arg=title,
            timeout=2000,
        )
        no_space = _popup_no_space_state(page, title, overlays)
        if not no_space.get("popupOpen"):
            if no_space.get("markerFound") and no_space.get("rawNoSpace"):
                return {"closedForNoSpace": True, "noSpaceEvidence": no_space,
                        "latlng": no_space.get("markerLatlng"), "popupLatlng": no_space.get("popupLatlng"),
                        "sameLatLng": no_space.get("markerLatlng") == no_space.get("popupLatlng")}
            raise AssertionError(f"popup closed without native no-space evidence: {json.dumps(no_space, ensure_ascii=False)}")
    try:
        _wait_for_current_popup_settle(page, title)
    except PlaywrightTimeoutError:
        if allow_no_space_close:
            no_space = _popup_no_space_state(page, title, overlays)
            if no_space.get("markerFound") and not no_space.get("popupOpen") and no_space.get("rawNoSpace"):
                return {"closedForNoSpace": True, "noSpaceEvidence": no_space,
                        "latlng": no_space.get("markerLatlng"), "popupLatlng": no_space.get("popupLatlng"),
                        "sameLatLng": no_space.get("markerLatlng") == no_space.get("popupLatlng")}
        raise
    _wait_for_stable_map_fit(page)
    return page.evaluate(
        """
        ({title, overlays}) => {
          const map = window.__notchimaMap;
          let found = null;
          const visit = layer => {
            if (found || !layer) return;
            if (typeof layer.getLatLng === 'function' && typeof layer.getElement === 'function') {
              if (layer.getElement()?.getAttribute('title') === title) found = layer;
            } else if (typeof layer.eachLayer === 'function') layer.eachLayer(visit);
          };
          map?.eachLayer(visit);
          const markerPopup = found?.getPopup?.() || null;
          const popup = markerPopup?.getElement?.() || null;
          if (!found || !markerPopup || !markerPopup.isOpen?.() || map?._popup !== markerPopup || !popup || !popup.isConnected || !map?.getContainer?.()?.contains(popup)) {
            return {error: 'identity-mismatch', title, markerFound: Boolean(found), popupIsOpen: Boolean(markerPopup?.isOpen?.()), activePopupMatches: Boolean(map?._popup === markerPopup), popupConnected: Boolean(popup?.isConnected)};
          }
          const tipContainer = popup.querySelector('.leaflet-popup-tip-container');
          const tip = popup.querySelector('.leaflet-popup-tip');
          const contentWrapper = popup.querySelector('.leaflet-popup-content-wrapper');
          const content = popup.querySelector('.leaflet-popup-content');
          const popupRect = popup.getBoundingClientRect();
          const tipRect = (tipContainer || tip)?.getBoundingClientRect();
          const contentWrapperRect = contentWrapper?.getBoundingClientRect();
          const contentRect = content?.getBoundingClientRect();
          const contentWithinWrapper = Boolean(contentRect && contentWrapperRect
            && contentRect.left >= contentWrapperRect.left - 1
            && contentRect.right <= contentWrapperRect.right + 1
            && contentRect.top >= contentWrapperRect.top - 1
            && contentRect.bottom <= contentWrapperRect.bottom + 1);
          const contentHorizontalFit = Boolean(content && content.scrollWidth <= content.clientWidth + 1);
          const contentContained = contentWithinWrapper && contentHorizontalFit;
          const iconRect = found.getElement().getBoundingClientRect();
          const renderedSvgRect = found.getElement().querySelector('svg')?.getBoundingClientRect() || iconRect;
          const viewport = {width: innerWidth, height: innerHeight};
          const box = r => r && ({left:r.left, top:r.top, right:r.right, bottom:r.bottom, width:r.width, height:r.height});
          const overlap = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
          const selectors = [...new Set([
            '#filters', '#filterMenu', '#resultsPanel', '.results', '#searchDock', '.search-dock', '.filter-menu',
            '.brand-tile', '.map-tools', '.leaflet-control-zoom', '.leaflet-control-attribution',
            '#mapFallback', ...(overlays || []),
          ])];
          const labels = new Map();
          const add = (node, selector) => {
            if (node && !labels.has(node)) labels.set(node, selector);
          };
          for (const selector of selectors) {
            document.querySelectorAll(selector).forEach(node => add(node, selector));
          }
          const occupiedOverlays = Array.from(labels, ([node, selector]) => {
            if (node.hidden) return null;
            const styles = getComputedStyle(node);
            if (styles.display === 'none' || styles.visibility === 'hidden') return null;
            const rect = node.getBoundingClientRect();
            if (!rect.width || !rect.height || rect.right <= 0 || rect.left >= viewport.width
                || rect.bottom <= 0 || rect.top >= viewport.height) return null;
            const declaredEdge = styles.getPropertyValue('--map-edge').trim().toLowerCase();
            const edge = declaredEdge || (node.id === 'mapFallback' ? 'bottom' : '');
            if (!['top', 'right', 'bottom', 'left'].includes(edge)) return null;
            return {selector, edge, rect: box(rect)};
          }).filter(Boolean);
          const markerLatLng = found.getLatLng();
          const popupLatLng = found.getPopup()?.getLatLng?.();
          const pointer = tipRect ? {x:(tipRect.left + tipRect.right) / 2, y:tipRect.bottom} : null;
          const head = {x:renderedSvgRect.left + renderedSvgRect.width / 2, y:renderedSvgRect.top};
          const pointOverlaps = point => point && occupiedOverlays.filter(({rect}) => point.x >= rect.left && point.x <= rect.right && point.y >= rect.top && point.y <= rect.bottom).map(({selector}) => selector);
          const connectorRect = pointer && head ? {
            left: Math.min(pointer.x, head.x) - 1, top: Math.min(pointer.y, head.y) - 1,
            right: Math.max(pointer.x, head.x) + 1, bottom: Math.max(pointer.y, head.y) + 1,
          } : null;
          const renderedSvgOverlaps = occupiedOverlays.filter(({rect}) => overlap(renderedSvgRect, rect)).map(({selector}) => selector);
          const connectorOverlaps = connectorRect ? occupiedOverlays.filter(({rect}) => overlap(connectorRect, rect)).map(({selector}) => selector) : [];
          const headOverlaps = pointOverlaps(head);
          const tipOverlaps = pointOverlaps(pointer);
          return {
            popup: box(popupRect),
            tip: box(tipRect), pointer, head,
            pointerError: pointer ? Math.hypot(pointer.x - head.x, pointer.y - head.y) : Infinity,
            inViewport: popupRect.left >= 0 && popupRect.top >= 0 && popupRect.right <= viewport.width && popupRect.bottom <= viewport.height,
            occupiedOverlays,
            renderedSvg: box(renderedSvgRect),
            renderedSvgOverlaps, connector: box(connectorRect), connectorOverlaps,
            headOverlaps, tipOverlaps,
            overlaps: occupiedOverlays.filter(({rect}) => overlap(popupRect, rect)).map(({selector}) => selector),
            content: box(contentRect),
            contentWrapper: box(contentWrapperRect),
            contentWithinWrapper,
            contentHorizontalFit,
            contentScrollWidth: content?.scrollWidth ?? null,
            contentClientWidth: content?.clientWidth ?? null,
            contentContained,
            latlng: {lat: markerLatLng.lat, lng: markerLatLng.lng},
            popupLatlng: popupLatLng ? {lat: popupLatLng.lat, lng: popupLatLng.lng} : null,
            sameLatLng: Boolean(popupLatLng && markerLatLng.lat === popupLatLng.lat && markerLatLng.lng === popupLatLng.lng),
            popupText: popup.innerText,
          };
        }
        """,
        {"title": title, "overlays": overlays},
    )
def _popup_overlay_clear(measure: dict) -> bool:
    return bool(measure.get("contentContained")) and all(not measure.get(key) for key in ("renderedSvgOverlaps", "connectorOverlaps", "headOverlaps", "tipOverlaps"))
def _popup_measure_contract_ok(measure: dict) -> bool:
    if measure.get("closedForNoSpace"):
        evidence = measure.get("noSpaceEvidence") or {}
        return bool(evidence.get("markerFound")
                    and not evidence.get("popupOpen")
                    and evidence.get("rawNoSpace")
                    and (evidence.get("availableWidth", 0) < evidence.get("nativeMinWidth", float("inf"))
                         or evidence.get("availableHeight", 0) <= 0))
    return (measure.get("pointerError", float("inf")) <= 2
            and measure.get("inViewport")
            and not measure.get("overlaps")
            and measure.get("sameLatLng")
            and _popup_overlay_clear(measure))



def _open_marker(page, title: str, *, edge: bool = False) -> None:
    _close_results_drawer_for_marker(page)
    markers = page.locator("#map .leaflet-marker-icon")
    index = markers.evaluate_all("(els, wanted) => els.findIndex(el => el.getAttribute('title') === wanted)", title)
    if index < 0:
        raise AssertionError(f"expected marker title {title!r}")
    filter_menu = page.locator("#filterMenu")
    if filter_menu.count() == 1:
        filter_open = filter_menu.evaluate("el => el.open")
        if filter_open:
            page.keyboard.press("Escape")
            page.wait_for_function(
                "document.querySelector('#filterMenu')?.open === false && document.activeElement === document.querySelector('#filterToggle')"
            )
        page.evaluate("() => { delete window.__notchimaFitProbe; }")
        _wait_for_stable_map_fit(page)
    page.evaluate("""
    ({title, edge}) => {
      const map = window.__notchimaMap;
      let target = null;
      map.eachLayer(layer => {
        if (!target && layer.getElement?.()?.getAttribute('title') === title && layer.getLatLng) target = layer;
      });
      if (!target) throw new Error(`marker not found: ${title}`);
      map.setView(target.getLatLng(), Math.max(map.getZoom(), 13), {animate:false});
      if (edge) map.panBy([Math.round(map.getSize().x * 0.32), 0], {animate:false});
    }
    """, {"title": title, "edge": edge})
    page.evaluate("() => { delete window.__notchimaPopupProbe; }")
    markers.nth(index).click()
    _wait_for_current_popup_settle(page, title)


def _wait_for_stable_map_fit(page) -> None:
    page.wait_for_function(
        """
        () => {
          const map = window.__notchimaMap;
          if (!map?.getCenter || !map.getZoom || !map.getSize) return false;
          const center = map.getCenter();
          const size = map.getSize();
          const panePos = map._mapPane?._leaflet_pos || {x: 0, y: 0};
          const panelRect = document.querySelector('#resultsPanel')?.getBoundingClientRect();
          const panelGeometry = panelRect ? [panelRect.left, panelRect.top, panelRect.right, panelRect.bottom, panelRect.width, panelRect.height] : [];
          const animating = Boolean(map._animatingZoom || map._panAnim?._inProgress || map.dragging?.moving?.());
          const signature = [center.lat, center.lng, map.getZoom(), size.x, size.y, panePos.x, panePos.y, window.innerWidth, window.innerHeight, ...panelGeometry].join('|');
          const prior = window.__notchimaFitProbe;
          if (animating || !prior || prior.signature !== signature) {
            window.__notchimaFitProbe = {signature, stable: 0};
            return false;
          }
          window.__notchimaFitProbe.stable += 1;
          return window.__notchimaFitProbe.stable >= 3;
        }
        """,
        polling="raf",
    )


def _wait_for_current_popup_settle(page, title: str) -> None:
    """Wait for the requested live popup and native Leaflet autopan to settle."""
    try:
        page.wait_for_function(
            """
            (wanted) => {
              const map = window.__notchimaMap;
              const popup = map?._popup;
              const popupElement = popup?.getElement?.();
              const sourceTitle = popup?._source?.options?.title
                || popup?._source?.getElement?.()?.getAttribute('title');
              const sourceMatches = sourceTitle === wanted;
              if (!map?.getCenter || !map.getZoom || !map.getSize || !popup?.isOpen?.()
                  || !popupElement || !sourceMatches) return false;
              const center = map.getCenter();
              const size = map.getSize();
              const panePos = map._mapPane?._leaflet_pos || {x: 0, y: 0};
              const rect = popupElement.getBoundingClientRect();
              const idle = !map._animatingZoom && !map._panAnim?._inProgress && !map.dragging?.moving?.();
              const signature = [center.lat, center.lng, map.getZoom(), size.x, size.y,
                panePos.x, panePos.y, rect.left, rect.top, rect.right, rect.bottom].join('|');
              const prior = window.__notchimaPopupProbe;
              if (!idle || !prior || prior.signature !== signature) {
                window.__notchimaPopupProbe = {signature, stable: 0};
                return false;
              }
              window.__notchimaPopupProbe.stable += 1;
              return window.__notchimaPopupProbe.stable >= 12;
            }
            """,
            arg=title,
            polling="raf",
        )
    except PlaywrightTimeoutError as exc:
        state = page.evaluate(
            """
            wanted => {
              const map = window.__notchimaMap;
              const popup = map?._popup;
              const popupElement = popup?.getElement?.();
              const source = popup?._source;
              const center = map?.getCenter?.();
              const size = map?.getSize?.();
              const rect = popupElement?.getBoundingClientRect?.();
              return {
                wanted,
                mapPresent: Boolean(map),
                center: center ? {lat: center.lat, lng: center.lng} : null,
                zoom: map?.getZoom?.() ?? null,
                size: size ? {x: size.x, y: size.y} : null,
                popupPresent: Boolean(popup),
                popupOpen: Boolean(popup?.isOpen?.()),
                popupSourceTitle: source?.options?.title || source?.getElement?.()?.getAttribute('title') || null,
                popupElement: Boolean(popupElement),
                popupConnected: Boolean(popupElement?.isConnected),
                popupRect: rect ? {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom} : null,
                activeProbe: window.__notchimaPopupProbe || null,
                animatingZoom: Boolean(map?._animatingZoom),
                panInProgress: Boolean(map?._panAnim?._inProgress),
                dragging: Boolean(map?.dragging?.moving?.()),
                markerTitles: Array.from(document.querySelectorAll('#map .leaflet-marker-icon')).map(el => el.getAttribute('title')),
              };
            }
            """,
            title,
        )
        raise AssertionError(f"popup settle timeout for {title}: {json.dumps(state, ensure_ascii=False)}") from exc


def _fit_bounds_open_marker(page, title: str) -> None:
    """Reset through the app's disclosure, wait for its own fit, then physically click a marker."""
    _close_results_drawer_for_marker(page)
    menu = page.locator("#filterMenu")
    if menu.count() != 1:
        raise AssertionError("expected native filter disclosure")
    if not menu.evaluate("el => el.open"):
        page.locator("#filterToggle").click()
        page.wait_for_function("document.querySelector('#filterMenu')?.open === true")
    page.evaluate("() => { delete window.__notchimaFitProbe; }")
    page.locator("#reset").click()
    page.wait_for_function("document.querySelector('#count')?.textContent !== ''")
    page.keyboard.press("Escape")
    page.wait_for_function(
        "document.querySelector('#filterMenu')?.open === false && document.activeElement === document.querySelector('#filterToggle')"
    )
    _wait_for_stable_map_fit(page)
    marker = page.locator(f'#map .leaflet-marker-icon[title="{title}"]')
    if marker.count() != 1:
        raise AssertionError(f"expected one marker title {title!r}")
    page.evaluate("() => { delete window.__notchimaPopupProbe; }")
    marker.click()
    _wait_for_current_popup_settle(page, title)


def _map_drag_target(page) -> dict:
    return page.evaluate(
        """
        () => {
          const root = document.querySelector('#map');
          if (!root) return {error: 'map-root-missing'};
          const blocked = [
            '#filters', '#filterMenu', '#resultsPanel', '.results', '.search-dock', '.filter-menu',
            '.brand-tile', '.map-tools', '.leaflet-control', '#mapFallback', '.leaflet-popup',
            '.leaflet-marker-icon',
          ];
          const box = root.getBoundingClientRect();
          const describe = elements => elements.slice(0, 8).map(element => ({
            tag: element.tagName,
            id: element.id || '',
            className: typeof element.className === 'string' ? element.className : '',
            pointerEvents: getComputedStyle(element).pointerEvents,
            rect: (() => { const r = element.getBoundingClientRect(); return {left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height}; })(),
          }));
          const describeHit = element => element ? {
            tag: element.tagName,
            id: element.id || '',
            className: typeof element.className === 'string' ? element.className : '',
            pointerEvents: getComputedStyle(element).pointerEvents,
            rect: (() => { const r = element.getBoundingClientRect(); return {left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height}; })(),
            ancestors: describe(Array.from((() => { const a = []; let n = element; while (n && a.length < 8) { a.push(n); n = n.parentElement; } return a; })())),
          } : null;
          const xs = [0.05, 0.15, 0.3, 0.5, 0.7, 0.85, 0.95];
          const ys = [0.08, 0.18, 0.3, 0.5, 0.7, 0.82, 0.92];
          for (const yRatio of ys) {
            for (const xRatio of xs) {
              const x = box.left + box.width * xRatio;
              const y = box.top + box.height * yRatio;
              const elements = document.elementsFromPoint(x, y);
              const blockedHit = elements.some(element => blocked.some(selector => element.matches?.(selector) || element.closest?.(selector)));
              const mapHit = elements.some(element => element.matches?.('.leaflet-tile, .leaflet-map-pane, .leaflet-container'));
              if (!blockedHit && mapHit && root.contains(document.elementFromPoint(x, y))) {
                return {
                  x, y, mapContains: true, blocked: false, stack: describe(elements),
                  hit: describeHit(document.elementFromPoint(x, y)),
                  closestControls: elements.map(element => element.closest?.('.leaflet-control, .leaflet-popup, .leaflet-marker-icon, .brand-tile, .map-tools, #filters, #resultsPanel')?.className || element.closest?.('.leaflet-control, .leaflet-popup, .leaflet-marker-icon, .brand-tile, .map-tools, #filters, #resultsPanel')?.id || '').filter(Boolean),
                };
              }
            }
          }
          return {error: 'no-unobstructed-map-point', mapRect: {left: box.left, top: box.top, right: box.right, bottom: box.bottom}};
        }
        """
    )


def _install_tile_failure_probe(page) -> None:
    """Capture native map/tile state without changing application behavior."""
    page.evaluate(
        """
        () => {
          const map = window.__notchimaMap;
          if (!map) return;
          const probe = {
            active: true,
            nativeEventCounts: {},
            tileEvents: {tileload: [], tileerror: [], tileunload: []},
            rafSamples: [],
          };
          window.__notchimaTileFailureProbe = probe;
          const push = (bucket, value) => {
            bucket.push(value);
            if (bucket.length > 50) bucket.shift();
          };
          const eventNames = [
            "click", "dragstart", "drag", "dragend", "zoomstart", "zoom", "zoomend",
            "movestart", "move", "moveend",
          ];
          for (const name of eventNames) {
            probe.nativeEventCounts[name] = 0;
            map.on(name, () => { probe.nativeEventCounts[name] += 1; });
          }
          let tileLayer = null;
          map.eachLayer(layer => {
            if (!tileLayer && typeof layer.getTileUrl === "function") tileLayer = layer;
          });
          if (tileLayer) {
            for (const name of Object.keys(probe.tileEvents)) {
              tileLayer.on(name, event => push(probe.tileEvents[name], {
                url: event?.tile?.src || event?.url || null,
                coords: event?.coords ? {
                  x: event.coords.x, y: event.coords.y, z: event.coords.z,
                } : null,
              }));
            }
          }
          const sample = () => {
            if (!probe.active) return;
            const center = map.getCenter?.();
            const pane = map._mapPane?._leaflet_pos || {x: 0, y: 0};
            push(probe.rafSamples, {
              center: center ? {lat: center.lat, lng: center.lng} : null,
              zoom: map.getZoom?.() ?? null,
              pane: {x: pane.x ?? 0, y: pane.y ?? 0},
              animatingZoom: Boolean(map._animatingZoom),
              panInProgress: Boolean(map._panAnim?._inProgress),
              dragging: Boolean(map.dragging?.moving?.()),
              fallbackHidden: document.querySelector("#mapFallback")?.hidden ?? null,
            });
            window.requestAnimationFrame(sample);
          };
          sample();
        }
        """
    )


def _tile_failure_diagnostics(page, *, expected_zoom=None, expected_center=None,
                              tile_stub_count=None, tile_abort_count=None) -> dict:
    """Return actionable state for a bounded tile-failure wait timeout."""
    try:
        detail = page.evaluate(
            """
            ({expectedZoom, expectedCenter}) => {
              const map = window.__notchimaMap;
              const center = map?.getCenter?.();
              const pane = map?._mapPane?._leaflet_pos || {x: 0, y: 0};
              const probe = window.__notchimaTileFailureProbe || {};
              const hook = window.__notchimaTileFailureKeys;
              let failedTileKeys = null;
              if (Array.isArray(hook)) failedTileKeys = hook.slice();
              else if (hook && typeof hook[Symbol.iterator] === "function") failedTileKeys = Array.from(hook);
              return {
                expectedZoom: expectedZoom ?? null,
                expectedCenter: expectedCenter ?? null,
                actualZoom: map?.getZoom?.() ?? null,
                actualCenter: center ? {lat: center.lat, lng: center.lng} : null,
                pane: {x: pane.x ?? 0, y: pane.y ?? 0},
                animatingZoom: Boolean(map?._animatingZoom),
                panInProgress: Boolean(map?._panAnim?._inProgress),
                dragging: Boolean(map?.dragging?.moving?.()),
                fallbackHidden: document.querySelector("#mapFallback")?.hidden ?? null,
                nativeEventCounts: probe.nativeEventCounts || {},
                tileEvents: probe.tileEvents || {tileload: [], tileerror: [], tileunload: []},
                failedTileKeys,
                rafSamples: probe.rafSamples || [],
              };
            }
            """,
            {"expectedZoom": expected_zoom, "expectedCenter": expected_center},
        )
    except Exception as error:
        detail = {"diagnosticError": str(error)}
    if tile_stub_count is not None:
        detail["fixtureStubbedTiles"] = tile_stub_count
    if tile_abort_count is not None:
        detail["abortedTiles"] = tile_abort_count
    return detail


def _run_case(page, viewport: tuple[int, int], checks: list[dict], payloads: dict) -> None:
    width, height = viewport
    page.set_viewport_size({"width": width, "height": height})
    tile_stub_count = _route_fixture(page, payloads)
    page.goto(page.url.split("?", 1)[0], wait_until="networkidle")
    _wait_ready(page)
    _record(checks, f"{width}x{height} OSM 타일 결정적 fixture stub 사용", tile_stub_count["count"] > 0, tile_stub_count)

    map_panel = _rect(page, ".map-panel")
    map_root = _rect(page, "#map")
    map_box = map_panel or map_root
    full_map = bool(map_box and abs(map_box["left"]) <= 1 and abs(map_box["top"]) <= 1
                    and abs(map_box["right"] - width) <= 1 and abs(map_box["bottom"] - height) <= 1)
    _record(checks, f"{width}x{height} 지도 뷰포트 네 변", full_map, {"panel": map_panel, "map": map_root})
    root_transform = page.evaluate("() => getComputedStyle(document.querySelector('#map.leaflet-container, #map .leaflet-container')).transform")
    _record(checks, f"{width}x{height} Leaflet root CSS transform 없음",
            root_transform in ("none", "matrix(1, 0, 0, 1, 0, 0)"), root_transform)

    search = _rect(page, "#q")
    search_dock = _rect(page, "#searchDock") or _rect(page, ".search-dock")
    centered = bool(search and abs(search["left"] + search["width"] / 2 - width / 2) <= 2)
    dock_centered = bool(search_dock and abs(search_dock["left"] + search_dock["width"] / 2 - width / 2) <= 2)
    dock_bottom_gap = (height - search_dock["bottom"]) if search_dock else float("inf")
    dock_positioned = bool(search_dock and 8 <= dock_bottom_gap <= 28 and search_dock["width"] > 0)
    _record(checks, f"{width}x{height} 검색 도크 하단 중앙", centered and dock_centered and dock_positioned,
            {"search": search, "dock": search_dock, "bottomGap": dock_bottom_gap})

    doc = page.evaluate("() => ({w: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, h: document.scrollingElement.scrollHeight, ch: document.scrollingElement.clientHeight})")
    no_scroll = doc["w"] <= width + 1 and doc["h"] <= height + 1
    _record(checks, f"{width}x{height} 문서 스크롤·가로 넘침 없음", no_scroll, doc)

    if width <= 640:
        drawer_open = _open_results_drawer(page)
        panel = _rect(page, "#resultsPanel")
        drawer_geometry_ok = bool(panel and panel["width"] >= 200 and panel["height"] > 0)
        _record(checks, f"{width}x{height} 모바일 결과 드로어 확장·반응형 폭 정착", drawer_open and drawer_geometry_ok,
                {"collapsed": page.locator("#resultsPanel").get_attribute("data-collapsed") if page.locator("#resultsPanel").count() else None,
                 "ariaExpanded": page.locator("#resultsToggle").get_attribute("aria-expanded") if page.locator("#resultsToggle").count() else None,
                 "panel": panel, "minimumWidth": 200})
    panel = _rect(page, "#resultsPanel") or _rect(page, ".results")
    body = _rect(page, "#resultsScroll") or _rect(page, ".results-body")
    panel_inside = bool(panel and panel["left"] >= -1 and panel["right"] <= width + 1
                        and panel["top"] >= -1 and panel["bottom"] <= height + 1)
    right_gap = width - panel["right"] if panel else float("inf")
    panel_inside = panel_inside and bool(panel and 8 <= right_gap <= 28 and panel["width"] > 0)
    _record(checks, f"{width}x{height} 결과 패널 우측 드로어 뷰포트 고정", panel_inside,
            {"panel": panel, "body": body, "rightGap": right_gap})

    body_scrollable = False
    if body:
        body_scrollable = page.evaluate(
            """sel => { const el = document.querySelector(sel); return Boolean(el && el.scrollHeight > el.clientHeight); }""",
            "#resultsScroll" if page.locator("#resultsScroll").count() else ".results-body",
        )
    _record(checks, f"{width}x{height} 결과 내부 스크롤", body_scrollable, body)

    if width > 640:
        menu = page.locator("#filterMenu")
        _open_filters(page)
        filter_results_geometry = page.evaluate(
            """
            () => {
              const filterMenu = document.querySelector('#filterMenu');
              const filterContent = document.querySelector('#filterMenu .filter-drawer');
              const results = document.querySelector('#resultsPanel');
              const resultsBody = document.querySelector('#resultsScroll, #resultsPanel .results-body');
              const rectData = (rect) => rect ? {
                left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom,
                width: rect.width, height: rect.height,
              } : null;
              const filterRect = filterMenu?.getBoundingClientRect();
              const filterContentRect = filterContent?.getBoundingClientRect();
              const resultsRect = results?.getBoundingClientRect();
              const resultsBodyRect = resultsBody?.getBoundingClientRect();
              const resultsBodyStyle = resultsBody ? getComputedStyle(resultsBody) : null;
              const resultsExpanded = Boolean(
                resultsRect && resultsRect.width > 0 && resultsRect.height > 0
                && resultsBodyRect && resultsBodyRect.width > 0 && resultsBodyRect.height > 0
                && resultsBodyStyle && resultsBodyStyle.display !== 'none'
                && resultsBodyStyle.visibility !== 'hidden'
              );
              if (!filterRect || !filterContentRect || !resultsRect) {
                return {
                  filterOpen: Boolean(filterMenu?.open),
                  resultsExpanded,
                  filterMenu: rectData(filterMenu?.getBoundingClientRect()),
                  filterContent: rectData(filterContentRect),
                  results: rectData(resultsRect),
                  resultsBody: rectData(resultsBodyRect),
                  overlapArea: null,
                  horizontalGap: null,
                  contentOverlapArea: null,
                  contentHorizontalGap: null,
                };
              }
              const overlapWidth = Math.max(
                0,
                Math.min(filterRect.right, resultsRect.right)
                  - Math.max(filterRect.left, resultsRect.left),
              );
              const overlapHeight = Math.max(
                0,
                Math.min(filterRect.bottom, resultsRect.bottom)
                  - Math.max(filterRect.top, resultsRect.top),
              );
              const horizontalGap = filterRect.right <= resultsRect.left
                ? resultsRect.left - filterRect.right
                : resultsRect.right <= filterRect.left
                  ? filterRect.left - resultsRect.right
                  : 0;
              const contentOverlapWidth = Math.max(
                0,
                Math.min(filterContentRect.right, resultsRect.right)
                  - Math.max(filterContentRect.left, resultsRect.left),
              );
              const contentOverlapHeight = Math.max(
                0,
                Math.min(filterContentRect.bottom, resultsRect.bottom)
                  - Math.max(filterContentRect.top, resultsRect.top),
              );
              const contentHorizontalGap = filterContentRect.right <= resultsRect.left
                ? resultsRect.left - filterContentRect.right
                : resultsRect.right <= filterContentRect.left
                  ? filterContentRect.left - resultsRect.right
                  : 0;
              return {
                filterOpen: Boolean(filterMenu?.open),
                resultsExpanded,
                filterMenu: rectData(filterMenu?.getBoundingClientRect()),
                filterContent: rectData(filterContentRect),
                results: rectData(resultsRect),
                resultsBody: rectData(resultsBodyRect),
                overlapArea: overlapWidth * overlapHeight,
                horizontalGap,
                contentOverlapArea: contentOverlapWidth * contentOverlapHeight,
                contentHorizontalGap,
              };
            }
            """
        )
        filter_results_geometry_ok = bool(
            filter_results_geometry.get("filterOpen")
            and filter_results_geometry.get("resultsExpanded")
            and filter_results_geometry.get("overlapArea") == 0
            and float(filter_results_geometry.get("horizontalGap") or 0) > 0
            and filter_results_geometry.get("contentOverlapArea") == 0
            and float(filter_results_geometry.get("contentHorizontalGap") or 0) > 0
        )
        _record(
            checks,
            f"{width}x{height} 열린 필터 패널과 결과 패널 비중첩",
            filter_results_geometry_ok,
            filter_results_geometry,
        )
        free_only = page.locator("#freeOnly")
        free_only.check()
        page.wait_for_function("document.querySelector('#freeOnly')?.checked === true")
        page.locator("#reset").click()
        page.wait_for_function("document.querySelector('#filterMenu')?.open === true && document.querySelector('#freeOnly')?.checked === false")
        page.evaluate("() => { delete window.__notchimaFitProbe; }")
        _wait_for_stable_map_fit(page)
        menu_open_after_reset = menu.evaluate("el => el.open")
        page.evaluate(
            """
            () => {
              const map = window.__notchimaMap;
              const center = map.getCenter();
              const zoom = map.getZoom();
              const point = map.project(center, zoom);
              const names = ['dragstart', 'drag', 'dragend', 'movestart', 'move', 'moveend'];
              const events = Object.fromEntries(names.map(name => [name, 0]));
              const handlers = Object.fromEntries(names.map(name => [name, () => { events[name] += 1; }]));
              names.forEach(name => map.on(name, handlers[name]));
              window.__notchimaDragProbe = {events, before: {lat: center.lat, lng: center.lng, zoom, x: point.x, y: point.y}, handlers};
            }
            """
        )
        drag_target = _map_drag_target(page)
        if drag_target.get("error"):
            raise AssertionError(f"physical drag target unavailable: {drag_target}")
        start_x = drag_target["x"]
        start_y = drag_target["y"]
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        for step in range(1, 7):
            page.mouse.move(start_x + 120 * step / 6, start_y + 24 * step / 6)
        page.mouse.up()
        _wait_for_stable_map_fit(page)
        drag_result = page.evaluate(
            """
            () => {
              const map = window.__notchimaMap;
              const probe = window.__notchimaDragProbe;
              const center = map.getCenter();
              const point = map.project(center, map.getZoom());
              const events = {...probe.events};
              for (const name of Object.keys(probe.handlers)) map.off(name, probe.handlers[name]);
              delete window.__notchimaDragProbe;
              return {
                events,
                before: probe.before,
                after: {lat: center.lat, lng: center.lng, zoom: map.getZoom(), x: point.x, y: point.y},
                projectedCenterDelta: Math.hypot(point.x - probe.before.x, point.y - probe.before.y),
                menuOpenAfterDrag: Boolean(document.querySelector('#filterMenu')?.open),
              };
            }
            """
        )
        drag_result["startTarget"] = drag_target
        drag_events = drag_result["events"]
        drag_ok = (menu_open_after_reset
                   and not drag_result["menuOpenAfterDrag"]
                   and drag_events["dragstart"] > 0
                   and drag_events["dragend"] > 0
                   and drag_events["moveend"] > 0
                   and drag_result["projectedCenterDelta"] > 5)
        _record(checks, f"{width}x{height} 필터 Reset 후 첫 물리 드래그가 disclosure를 닫고 지도 중심을 이동", drag_ok,
                {"menuOpenAfterReset": menu_open_after_reset, **drag_result})

    if page.locator("#sortNew").count() and page.locator("#pageNext").count():
        if width <= 640:
            _open_filters(page)
            page.keyboard.press("Escape")
            page.wait_for_function("document.querySelector('#filterMenu')?.open === false")
            _record(checks, f"{width}x{height} 모바일 필터 닫힘 후 결과 드로어 확장", _open_results_drawer(page),
                    {"filterOpen": page.locator("#filterMenu").evaluate("el => el.open") if page.locator("#filterMenu").count() else None})
        query = page.locator("#q")
        if query.count() == 1:
            page.evaluate("() => { const el = document.querySelector('#resultsScroll, .results-body'); if (el) el.scrollTop = 88; }")
            query.fill("서울")
            page.wait_for_function("new URL(location.href).searchParams.get('q') === '서울'")
            query_scroll = page.evaluate("() => document.querySelector('#resultsScroll, .results-body')?.scrollTop ?? -1")
            _record(checks, f"{width}x{height} 필터 변경이 결과 내부 스크롤을 0으로 초기화", query_scroll == 0, {"scrollTop": query_scroll})
            query.fill("")
            page.wait_for_function("new URL(location.href).searchParams.get('q') !== '서울'")
        else:
            _record(checks, f"{width}x{height} 필터 변경이 결과 내부 스크롤을 0으로 초기화", False, "#q missing")
        _open_marker(page, "서울 단일 좌표 행사")
        if width <= 640:
            if not _open_results_drawer(page):
                raise AssertionError("results drawer must be open before mobile sort/page controls")
            _wait_for_current_popup_settle(page, "서울 단일 좌표 행사")
        baseline = page.evaluate("""
        () => {
          const map = window.__notchimaMap;
          window.__probeMarker = Array.from(document.querySelectorAll('#map .leaflet-marker-icon')).find(el => el.getAttribute('title') === '서울 단일 좌표 행사');
          const scroll = document.querySelector('#resultsScroll, .results-body');
          if (scroll) scroll.scrollTop = 77;
          return {center: [map.getCenter().lat, map.getCenter().lng], titles: Array.from(document.querySelectorAll('#map .leaflet-marker-icon')).map(el => el.getAttribute('title')).sort(), popup: Boolean(map._popup?.isOpen?.() && map._popup?.getElement?.()), scrollTop: scroll?.scrollTop ?? -1};
        }
        """)
        page.locator("#sortNew").click()
        page.wait_for_function("document.querySelector('#sortNew')?.getAttribute('aria-pressed') === 'true'")
        _wait_for_stable_map_fit(page)
        after_sort = page.evaluate("""
        () => {
          const map = window.__notchimaMap;
          const marker = Array.from(document.querySelectorAll('#map .leaflet-marker-icon')).find(el => el.getAttribute('title') === '서울 단일 좌표 행사');
          return {center: [map.getCenter().lat, map.getCenter().lng], titles: Array.from(document.querySelectorAll('#map .leaflet-marker-icon')).map(el => el.getAttribute('title')).sort(), popup: Boolean(map._popup?.isOpen?.() && map._popup?.getElement?.()), sameMarker: marker === window.__probeMarker, scrollTop: document.querySelector('#resultsScroll, .results-body')?.scrollTop ?? -1};
        }
        """)
        sort_ok = (all(abs(a - b) <= 1e-9 for a, b in zip(baseline["center"], after_sort["center"]))
                   and baseline["titles"] == after_sort["titles"] and after_sort["popup"] and after_sort["sameMarker"] and after_sort["scrollTop"] == 0)
        _record(checks, f"{width}x{height} 정렬이 지도 중심·마커·팝업 보존", sort_ok, {"before": baseline, "after": after_sort})
        page_next = page.locator("#pageNext")
        if not page_next.is_disabled():
            before_page = after_sort
            page.evaluate("() => { const el = document.querySelector('#resultsScroll, .results-body'); if (el) el.scrollTop = 66; }")
            page_next.click()
            page.wait_for_function("document.querySelector('#pageInfo')?.textContent.includes('2 /')")
            page.wait_for_function("""
            () => {
              const body = document.querySelector('#resultsScroll, .results-body');
              const first = document.querySelector('#list')?.firstElementChild;
              const heading = first?.querySelector('h1,h2,h3,h4,[role="heading"]') || first;
              if (!body || !heading) return false;
              const bodyRect = body.getBoundingClientRect();
              const headingRect = heading.getBoundingClientRect();
              return headingRect.top >= bodyRect.top - 1 && headingRect.top <= bodyRect.bottom + 1;
            }
            """)
            _wait_for_stable_map_fit(page)
            after_page = page.evaluate("""
            () => {
              const map = window.__notchimaMap;
              const marker = Array.from(document.querySelectorAll('#map .leaflet-marker-icon')).find(el => el.getAttribute('title') === '서울 단일 좌표 행사');
              const body = document.querySelector('#resultsScroll, .results-body');
              const first = document.querySelector('#list')?.firstElementChild;
              const heading = first?.querySelector('h1,h2,h3,h4,[role="heading"]') || first;
              const firstResultVisible = (() => {
                if (!body || !heading) return false;
                const bodyRect = body.getBoundingClientRect();
                const headingRect = heading.getBoundingClientRect();
                return headingRect.top >= bodyRect.top - 1 && headingRect.top <= bodyRect.bottom + 1;
              })();
              return {center: [map.getCenter().lat, map.getCenter().lng], titles: Array.from(document.querySelectorAll('#map .leaflet-marker-icon')).map(el => el.getAttribute('title')).sort(), popup: Boolean(map._popup?.isOpen?.() && map._popup?.getElement?.()), sameMarker: marker === window.__probeMarker, firstResultVisible, scrollTop: document.querySelector('#resultsScroll, .results-body')?.scrollTop ?? -1};
            }
            """)
            page_ok = (all(abs(a - b) <= 1e-9 for a, b in zip(before_page["center"], after_page["center"]))
                       and before_page["titles"] == after_page["titles"] and after_page["popup"] and after_page["sameMarker"] and after_page["firstResultVisible"])
            _record(checks, f"{width}x{height} 페이징이 지도 중심·마커·팝업 보존", page_ok, {"before": before_page, "after": after_page})
        else:
            _record(checks, f"{width}x{height} 페이징이 지도 중심·마커·팝업 보존", False, "#pageNext disabled")
    else:
        _record(checks, f"{width}x{height} 정렬이 지도 중심·마커·팝업 보존", False, "sort/page controls missing")
        _record(checks, f"{width}x{height} 페이징이 지도 중심·마커·팝업 보존", False, "sort/page controls missing")
    if width <= 640 and page.locator("#resultsPanel").get_attribute("data-collapsed") == "false":
        page.locator("#resultsToggle").click()
        page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")

    page.evaluate("() => window.__notchimaMap.closePopup()")
    before_locate_count = int(page.locator("#count").inner_text())
    page.evaluate("""
    () => Object.defineProperty(navigator, 'geolocation', {configurable:true, value: {
      getCurrentPosition(success) { success({coords: {latitude: 37.5665, longitude: 126.9780, accuracy: 5}}); }
    }})
    """)
    page.locator("#locateBtn").click()
    located = page.evaluate("""
    () => {
      const map = window.__notchimaMap;
      const center = map.getCenter();
      const expected = window.L.latLng(37.5665, 126.9780);
      const actualPoint = map.project(center, 14);
      const expectedPoint = map.project(expected, 14);
      return {
        lat: center.lat,
        lng: center.lng,
        zoom: map.getZoom(),
        pixelError: Math.hypot(actualPoint.x - expectedPoint.x, actualPoint.y - expectedPoint.y),
        count: Number(document.querySelector('#count')?.textContent || -1),
        hint: document.querySelector('#locateHint')?.textContent || '',
      };
    }
    """)
    locate_ok = (located["pixelError"] <= 2 and located["zoom"] == 14
                 and located["count"] == before_locate_count and "거리순" in located["hint"])
    _open_marker(page, "서울 단일 좌표 행사")
    geometry = _geometry(page, "서울 단일 좌표 행사")
    geometry_ok = geometry.get("tipError", float("inf")) <= 2 and geometry.get("popupLatlng") == geometry.get("latlng")
    _record(checks, f"{width}x{height} 단일 핀 지리 tip 정합", geometry_ok, geometry)

    popup = _popup_measure(page, "서울 단일 좌표 행사", ["#searchDock", "#filterMenu", "#resultsPanel", ".results"])
    popup_ok = popup.get("pointerError", float("inf")) <= 2 and popup.get("inViewport") and not popup.get("overlaps") and popup.get("sameLatLng") and _popup_overlay_clear(popup)
    _record(checks, f"{width}x{height} 단일 팝업 pointer 정합·가림 없음", popup_ok, popup)

    # Pan, zoom, and resize are performed on the captured real Leaflet map.
    page.evaluate("() => { const map = window.__notchimaMap; map.closePopup(); map.panBy([70, 25], {animate:false}); map.setZoom(map.getZoom() + 1, {animate:false}); }")
    page.wait_for_function("window.__notchimaMap && document.querySelector('#map .leaflet-marker-icon')")
    page.set_viewport_size({"width": width - 1, "height": height - 1})
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate("() => window.__notchimaMap.invalidateSize({pan:false})")
    _open_marker(page, "서울 단일 좌표 행사")
    transformed = _geometry(page, "서울 단일 좌표 행사")
    transformed_popup = _popup_measure(page, "서울 단일 좌표 행사", ["#searchDock", "#filterMenu", "#resultsPanel", ".results"])
    transformed_ok = transformed.get("tipError", float("inf")) <= 2 and transformed_popup.get("pointerError", float("inf")) <= 2 and _popup_overlay_clear(transformed_popup)
    _record(checks, f"{width}x{height} pan·zoom·resize 후 정합", transformed_ok, {"geometry": transformed, "popup": transformed_popup})
    page.evaluate("() => window.__notchimaMap.closePopup()")
    page.emulate_media(reduced_motion="reduce")
    _open_marker(page, "서울 단일 좌표 행사")
    reduced_popup = _popup_measure(page, "서울 단일 좌표 행사", ["#searchDock", "#filterMenu", "#resultsPanel", ".results"])
    reduced_media = page.evaluate("() => matchMedia('(prefers-reduced-motion: reduce)').matches")
    _record(checks, f"{width}x{height} reduced-motion 팝업 정합·가림 없음",
            reduced_media and reduced_popup.get("pointerError", float("inf")) <= 2
            and reduced_popup.get("inViewport") and not reduced_popup.get("overlaps") and _popup_overlay_clear(reduced_popup), reduced_popup)
    page.emulate_media(reduced_motion=None)
    page.evaluate("() => window.__notchimaMap.closePopup()")

    page.evaluate("() => window.__notchimaMap.closePopup()")
    _open_marker(page, "2개 행사")
    grouped = page.locator(".map-popup-list")
    _record(checks, f"{width}x{height} 동일좌표 그룹 팝업", grouped.count() == 1 and "2개 행사" in grouped.inner_text(), grouped.inner_text() if grouped.count() else "missing")
    grouped_popup = _popup_measure(page, "2개 행사", ["#searchDock", "#filterMenu", "#resultsPanel", ".results"])
    _record(checks, f"{width}x{height} 그룹 핀 popup pointer 정합·가림 없음",
            grouped_popup.get("pointerError", float("inf")) <= 2
            and grouped_popup.get("inViewport") and not grouped_popup.get("overlaps") and _popup_overlay_clear(grouped_popup), grouped_popup)

    page.evaluate("() => window.__notchimaMap.closePopup()")
    long_title = _fixture_payloads()["events.json"][3]["name"]
    _open_marker(page, long_title, edge=True)
    long_geometry = _geometry(page, long_title)
    long_popup = _popup_measure(page, long_title, ["#searchDock", "#filterMenu", "#resultsPanel", ".results"])
    long_ok = (long_geometry.get("tipError", float("inf")) <= 2
               and long_geometry.get("popupLatlng") == long_geometry.get("latlng")
               and long_popup.get("pointerError", float("inf")) <= 2
               and long_popup.get("sameLatLng")
               and _popup_overlay_clear(long_popup)
               and long_popup.get("inViewport") and not long_popup.get("overlaps"))
    _record(checks, f"{width}x{height} 긴 내용·가장자리 tip/pointer 정합·가림 없음", long_ok,
            {"geometry": long_geometry, "popup": long_popup})
    page.evaluate("() => window.__notchimaMap.closePopup()")
    for edge_title, edge_label in (
        ("제주 가장자리 행사", "남쪽"),
        ("강원 가장자리 행사", "북쪽"),
        ("인천 서쪽 가장자리 행사", "서쪽"),
        ("부산 동쪽 가장자리 행사", "동쪽"),
    ):
        _fit_bounds_open_marker(page, edge_title)
        edge_geometry = _geometry(page, edge_title)
        edge_popup = _popup_measure(page, edge_title, ["#searchDock", "#filterMenu", "#resultsPanel", ".results"])
        occupied_overlay_selectors = {item.get("selector") for item in edge_popup.get("occupiedOverlays", [])}
        required_overlay_selectors = {
            "#searchDock", "#resultsPanel", ".brand-tile", ".map-tools",
            ".leaflet-control-zoom", ".leaflet-control-attribution",
        }
        edge_ok = (edge_geometry.get("tipError", float("inf")) <= 2
                   and edge_geometry.get("popupLatlng") == edge_geometry.get("latlng")
                   and edge_popup.get("pointerError", float("inf")) <= 2
                   and edge_popup.get("sameLatLng")
                   and _popup_overlay_clear(edge_popup)
                   and required_overlay_selectors.issubset(occupied_overlay_selectors)
                   and edge_popup.get("inViewport") and not edge_popup.get("overlaps"))
        _record(checks, f"{width}x{height} 앱 autofit {edge_label} 가장자리 물리 클릭 tip/pointer 정합·동일좌표·가림 없음", edge_ok,
                {"geometry": edge_geometry, "popup": edge_popup})
        page.evaluate("() => window.__notchimaMap.closePopup()")

    if width <= 640:
        mobile_panel = page.locator("#resultsPanel")
        mobile_toggle = page.locator("#resultsToggle")
        initial_collapsed = mobile_panel.get_attribute("data-collapsed") == "true" if mobile_panel.count() == 1 else False
        initial_map = _rect(page, "#map")
        _record(checks, f"{width}x{height} 모바일 결과 우측 드로어 기본 접힘·지도 폭 보존",
                initial_collapsed and bool(initial_map and initial_map["width"] >= 56),
                {"collapsed": initial_collapsed, "map": initial_map})
        if mobile_toggle.count() == 1:
            _open_filters(page)
            mobile_toggle.click()
            drawer_open = _open_results_drawer(page)
            mutually_exclusive = page.evaluate(
                "() => document.querySelector('#resultsPanel')?.dataset.collapsed === 'false' && document.querySelector('#resultsToggle')?.getAttribute('aria-expanded') === 'true' && !document.querySelector('#filterMenu')?.open"
            )
            panel_box = _rect(page, "#resultsPanel")
            _record(checks, f"{width}x{height} 모바일 조건·결과 팝업 상호배타 확장", drawer_open and mutually_exclusive and bool(panel_box and panel_box["width"] >= 200),
                    {"filterOpen": page.locator("#filterMenu").evaluate("el => el.open"),
                     "resultsCollapsed": mobile_panel.get_attribute("data-collapsed"),
                     "ariaExpanded": mobile_toggle.get_attribute("aria-expanded"), "panel": panel_box})
        else:
            _record(checks, f"{width}x{height} 모바일 조건·결과 팝업 상호배타 확장", False, "#resultsToggle missing")

    pre_probe_panel_state = None
    restored_panel_state = None
    if width <= 640:
        details_present = _open_filters(page)
        _record(checks, f"{width}x{height} 네이티브 필터 disclosure", details_present, {"count": page.locator("#filterMenu").count()})
        if details_present:
            search = page.locator("#q")
            search.fill("서울")
            page.wait_for_function("new URL(location.href).searchParams.get('q') === '서울' && document.querySelector('#q')?.value === '서울'")
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            page.evaluate("() => { delete window.__notchimaFitProbe; }")
            _wait_for_stable_map_fit(page)
            page.evaluate("""
            () => {
              const m = window.__notchimaMap;
              window.__mapGestureCounts = {click: 0, dragstart: 0, zoomstart: 0, movestart: 0};
              for (const name of Object.keys(window.__mapGestureCounts)) m.on(name, () => window.__mapGestureCounts[name]++);
            }
            """)
            filter_drawer = page.locator("#filterMenu .filter-drawer")
            if filter_drawer.count() != 1 or not filter_drawer.is_visible():
                raise AssertionError("expected visible native filter drawer before physical gesture probe")
            drawer_box = filter_drawer.bounding_box()
            if not drawer_box or drawer_box["width"] <= 12 or drawer_box["height"] <= 12:
                raise AssertionError(f"filter drawer has no physical gesture surface: {drawer_box}")
            gesture_point = page.evaluate(
                """
                () => {
                  const drawer = document.querySelector('#filterMenu .filter-drawer');
                  if (!drawer) return null;
                  const box = drawer.getBoundingClientRect();
                  const ratios = [0.2, 0.35, 0.5, 0.65, 0.8];
                  for (const yRatio of ratios) for (const xRatio of ratios) {
                    const x = box.left + box.width * xRatio;
                    const y = box.top + box.height * yRatio;
                    const top = document.elementFromPoint(x, y);
                    if (!top || !drawer.contains(top)) continue;
                    if (top.closest('button,a,input,select,textarea,summary,label')) continue;
                    return {x, y, top: top.tagName.toLowerCase(), box: {x: box.x, y: box.y, width: box.width, height: box.height}};
                  }
                  return {x: box.left + box.width / 2, y: box.top + box.height / 2,
                          top: document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)?.tagName?.toLowerCase() || null,
                          box: {x: box.x, y: box.y, width: box.width, height: box.height}};
                }
                """
            )
            if not gesture_point:
                raise AssertionError("filter drawer gesture point unavailable")
            gesture_x = gesture_point["x"]
            gesture_y = gesture_point["y"]
            page.mouse.move(gesture_x, gesture_y)
            page.mouse.click(gesture_x, gesture_y)
            page.mouse.move(gesture_x, gesture_y)
            page.mouse.wheel(0, 240)
            _wait_for_stable_map_fit(page)
            gesture_counts = page.evaluate("() => ({...window.__mapGestureCounts})")
            _record(checks, f"{width}x{height} 안정된 필터 overlay click·wheel이 지도 gesture를 만들지 않음",
                    all(gesture_counts[name] == 0 for name in ("click", "dragstart", "zoomstart", "movestart")),
                    {"counts": gesture_counts, "gesturePoint": gesture_point})
            page.keyboard.press("Escape")
            page.wait_for_function(
                "document.querySelector('#filterMenu')?.open === false && document.activeElement === document.querySelector('#filterToggle')"
            )
            _wait_for_stable_map_fit(page)
            closed = page.evaluate("() => !document.querySelector('#filterMenu').open && document.activeElement === document.querySelector('#filterToggle')")
            _record(checks, f"{width}x{height} Escape 필터 닫기·summary 초점 복귀", closed)
        toggle = page.locator("#resultsToggle")
        if toggle.count() == 1:
            if page.locator("#resultsPanel").get_attribute("data-collapsed") != "true":
                toggle.click()
                page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
            toggle.click()
            drawer_open = _open_results_drawer(page)
            expanded = (drawer_open
                        and page.locator("#resultsPanel").get_attribute("data-collapsed") == "false"
                        and toggle.get_attribute("aria-expanded") == "true")
            _record(checks, f"{width}x{height} 결과 패널 접기·펼치기", expanded)
            pre_probe_panel_state = page.evaluate(
                """
                () => {
                  const panel = document.querySelector('#resultsPanel');
                  const rect = panel?.getBoundingClientRect();
                  const style = panel ? getComputedStyle(panel) : null;
                  return {
                    styleAttribute: panel?.getAttribute('style') ?? null,
                    dataCollapsed: panel?.dataset.collapsed ?? null,
                    rect: rect ? {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height} : null,
                    computed: style ? {width: style.width, top: style.top, height: style.height} : null,
                    media: {
                      maxWidth640: matchMedia('(max-width: 640px)').matches,
                      maxHeight560: matchMedia('(max-height: 560px)').matches,
                      screen: matchMedia('screen').matches,
                      print: matchMedia('print').matches,
                    },
                    panelCount: document.querySelectorAll('#resultsPanel').length,
                    filterOpen: Boolean(document.querySelector('#filterMenu')?.open),
                    viewport: {width: window.innerWidth, height: window.innerHeight},
                  };
                }
                """
            )
            page.evaluate("() => window.__notchimaMap.closePopup()")
            _wait_for_stable_map_fit(page)
            page.wait_for_function("document.querySelector('#filterMenu')?.open === false")
            _open_marker(page, "서울 단일 좌표 행사")
            sheet_open = _popup_measure(page, "서울 단일 좌표 행사", ["#filterMenu", "#resultsPanel", ".results"])
            if page.locator("#resultsPanel").get_attribute("data-collapsed") != "true":
                toggle.click()
                page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
            collapsed_label = toggle.inner_text().strip() or (toggle.get_attribute("aria-label") or "")
            _record(checks, f"{width}x{height} 접힌 결과 토글에 펼치기 라벨 표시",
                    "펼치기" in collapsed_label and toggle.is_visible(), collapsed_label)
            if not _open_results_drawer(page):
                raise AssertionError("results drawer must be open before drawer Escape and popup resize checks")
            drawer_action = page.locator("#resultsPanel button:not(#resultsToggle):visible, #resultsPanel a:visible").first
            drawer_action.focus()
            focused_in_drawer = page.evaluate("() => { const panel = document.querySelector('#resultsPanel'); const active = document.activeElement; return Boolean(panel && active && panel.contains(active) && active.id !== 'resultsToggle'); }")
            page.keyboard.press("Escape")
            page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
            escape_collapsed = (focused_in_drawer
                                and page.locator("#resultsPanel").get_attribute("data-collapsed") == "true"
                                and toggle.get_attribute("aria-expanded") == "false"
                                and page.evaluate("() => document.activeElement === document.querySelector('#resultsToggle')"))
            _record(checks, f"{width}x{height} 결과 액션에서 Escape 드로어 닫기·토글 초점 복귀", escape_collapsed)
            drawer_open = _open_results_drawer(page)
            sheet_resized = _popup_measure(page, "서울 단일 좌표 행사", ["#filterMenu", "#resultsPanel", ".results"], allow_no_space_close=True)
            drawer_state = page.evaluate("""
            () => {
              const panel = document.querySelector('#resultsPanel');
              const toggle = document.querySelector('#resultsToggle');
              const rect = panel?.getBoundingClientRect();
              return {filterOpen: Boolean(document.querySelector('#filterMenu')?.open), dataCollapsed: panel?.dataset.collapsed ?? null,
                      ariaExpanded: toggle?.getAttribute('aria-expanded') ?? null,
                      rect: rect ? {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height} : null};
            }
            """)
            sheet_ok = (drawer_open
                        and not drawer_state["filterOpen"]
                        and drawer_state["dataCollapsed"] == "false"
                        and drawer_state["ariaExpanded"] == "true"
                        and drawer_state["rect"] and drawer_state["rect"]["width"] >= 200
                        and _popup_measure_contract_ok(sheet_open)
                        and _popup_measure_contract_ok(sheet_resized))
            _record(checks, f"{width}x{height} sheet 크기 변경 전후 팝업 정합·가림 없음", sheet_ok,
                    {"open": sheet_open, "resized": sheet_resized})
            page.evaluate(
                """
                state => {
                  const panel = document.querySelector('#resultsPanel');
                  if (!panel) return;
                  if (state.styleAttribute === null) panel.removeAttribute('style');
                  else panel.setAttribute('style', state.styleAttribute);
                }
                """,
                pre_probe_panel_state,
            )
            page.set_viewport_size(pre_probe_panel_state["viewport"])
            _wait_for_stable_map_fit(page)
            restored_panel_state = page.evaluate(
                """
                () => {
                  const panel = document.querySelector('#resultsPanel');
                  const rect = panel?.getBoundingClientRect();
                  return {
                    styleAttribute: panel?.getAttribute('style') ?? null,
                    dataCollapsed: panel?.dataset.collapsed ?? null,
                    rect: rect ? {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height} : null,
                    viewport: {width: window.innerWidth, height: window.innerHeight},
                  };
                }
                """
            )
            if (restored_panel_state["styleAttribute"] != pre_probe_panel_state["styleAttribute"] or restored_panel_state["viewport"] != pre_probe_panel_state["viewport"]):
                raise AssertionError(f"results panel inline/viewport state was not restored: {restored_panel_state} != {pre_probe_panel_state}")
            page.evaluate("() => window.__notchimaMap.closePopup()")
            page.wait_for_function("document.querySelector('#filterMenu')?.open === false")
            toggle.click()
            page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
            filter_query = page.locator("#q")
            filter_query.fill("서울")
            page.wait_for_function("new URL(location.href).searchParams.get('q') === '서울'")
            page.wait_for_function("document.querySelector('#filterMenu')?.open === false")
            drawer_open = _open_results_drawer(page)
            page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'false'")
            autofit = page.evaluate("""
            () => {
              const map = window.__notchimaMap;
              const rect = document.querySelector('#map')?.getBoundingClientRect();
              const size = map?.getSize?.();
              return {rect, size: size ? {x: size.x, y: size.y} : null};
            }
            """)
            autofit_ok = bool(autofit.get("rect")) and bool(autofit.get("size")) and (
                abs(autofit["rect"]["width"] - width) <= 2
                and abs(autofit["rect"]["height"] - height) <= 2
                and abs(autofit["size"]["x"] - width) <= 2
                and abs(autofit["size"]["y"] - height) <= 2
            )
            _record(checks, f"{width}x{height} 결과 접기→필터→펼치기 map autofit", autofit_ok, autofit)
            filter_query.fill("")
            page.wait_for_function("new URL(location.href).searchParams.get('q') !== '서울'")
            page.evaluate("() => window.__notchimaMap.closePopup()")
            page.wait_for_function("document.querySelector('#filterMenu')?.open === false")
            toggle.click()
            page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
            page.set_viewport_size({"width": 1280, "height": 800})
            desktop_body = _rect(page, "#resultsScroll") or _rect(page, ".results-body")
            desktop_toggle_visible = page.evaluate("() => { const el = document.querySelector('#resultsToggle'); return Boolean(el && getComputedStyle(el).display !== 'none' && !el.hidden); }")
            _record(checks, f"{width}x{height} 접힌 모바일→데스크톱 결과 본문 가시·토글 숨김",
                    bool(desktop_body and desktop_body["width"] > 0 and desktop_body["height"] > 0) and not desktop_toggle_visible,
                    {"body": desktop_body, "toggleVisible": desktop_toggle_visible})
            page.set_viewport_size({"width": width, "height": height})
            _wait_for_stable_map_fit(page)
        else:
            _record(checks, f"{width}x{height} 결과 패널 접기·펼치기", False, "#resultsToggle missing")

    before44_panel_state = page.evaluate(
        """
        () => {
          const panel = document.querySelector('#resultsPanel');
          const rect = panel?.getBoundingClientRect();
          const style = panel ? getComputedStyle(panel) : null;
          return {
            styleAttribute: panel?.getAttribute('style') ?? null,
            dataCollapsed: panel?.dataset.collapsed ?? null,
            rect: rect ? {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height} : null,
            computed: style ? {width: style.width, top: style.top, height: style.height} : null,
            media: {
              maxWidth640: matchMedia('(max-width: 640px)').matches,
              maxHeight560: matchMedia('(max-height: 560px)').matches,
              screen: matchMedia('screen').matches,
              print: matchMedia('print').matches,
            },
            panelCount: document.querySelectorAll('#resultsPanel').length,
            filterOpen: Boolean(document.querySelector('#filterMenu')?.open),
            viewport: {width: window.innerWidth, height: window.innerHeight},
          };
        }
        """
    )
    target_ids = ["filterToggle", "resultsToggle", "reset", "sortRel", "sortNew", "pagePrev", "pageNext"]
    desktop_target_ids = ["filterToggle", "reset", "sortRel", "sortNew", "pagePrev", "pageNext"]
    def measure_targets(ids: list[str]) -> list[dict]:
        return page.evaluate(
            """
            ids => ids.map(id => {
              const el = document.getElementById(id);
              const style = el ? getComputedStyle(el) : null;
              const rect = el?.getBoundingClientRect();
              const visible = Boolean(el && !el.hidden && style?.display !== 'none' && style?.visibility !== 'hidden');
              return {id, present: Boolean(el), visible, w: rect?.width ?? 0, h: rect?.height ?? 0};
            })
            """,
            ids,
        )
    if width <= 640:
        if not _open_filters(page):
            raise AssertionError("filter disclosure must open before filter target measurement")
        page.wait_for_function("document.querySelector('#filterMenu')?.open === true && document.querySelector('#filterMenu .filter-drawer')?.getBoundingClientRect().height > 0")
        target_measurements = measure_targets(["filterToggle", "reset"])
        page.keyboard.press("Escape")
        page.wait_for_function(
            "document.querySelector('#filterMenu')?.open === false && document.activeElement === document.querySelector('#filterToggle')"
        )
        _wait_for_stable_map_fit(page)
        if not _open_results_drawer(page):
            raise AssertionError("results drawer must open before result target measurement")
        target_measurements.extend(measure_targets(["resultsToggle", "sortRel", "sortNew", "pagePrev", "pageNext"]))
    else:
        results_toggle_hidden = page.evaluate(
            "() => { const el = document.querySelector('#resultsToggle'); const style = el && getComputedStyle(el); return Boolean(el && (el.hidden || style?.display === 'none' || style?.visibility === 'hidden')); }"
        )
        target_measurements = measure_targets(desktop_target_ids)
    expected_target_ids = target_ids if width <= 640 else desktop_target_ids
    targets_ok = (
        len(target_measurements) == len(expected_target_ids)
        and {item["id"] for item in target_measurements} == set(expected_target_ids)
        and all(item["present"] and item["visible"] and item["w"] >= 44 and item["h"] >= 44 for item in target_measurements)
        and (width <= 640 or results_toggle_hidden)
    )
    target_detail = (
        {"targets": target_measurements, "before44": before44_panel_state, "preProbe": pre_probe_panel_state, "afterRestore": restored_panel_state}
        if width <= 640
        else {"targets": target_measurements, "resultsToggleHidden": results_toggle_hidden}
    )
    _record(checks, f"{width}x{height} 주요 타깃 44px", targets_ok, target_detail)
    marker_targets = page.locator("#map .leaflet-marker-icon").evaluate_all(
        "els => els.map(el => ({title: el.getAttribute('title'), width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height}))"
    )
    marker_targets_ok = bool(marker_targets) and all(item["width"] >= 44 and item["height"] >= 44 for item in marker_targets)
    _record(checks, f"{width}x{height} 실제 지도 핀 44px hit target", marker_targets_ok, marker_targets)


def viewport_red_probe() -> int:
    """Run the first red gate against real data before any UI source edits."""
    launch_options = {"headless": True}
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if executable:
        launch_options["executable_path"] = executable
    with serve(ROOT / "web" / "public") as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base, wait_until="networkidle")
        observed = _rect(page, "#map")
        expected = {"left": 0, "top": 0, "right": 1440, "bottom": 900}
        ok = bool(observed and all(abs(observed[key] - expected[key]) <= 1 for key in expected))
        summary = {
            "status": "passed" if ok else "failed",
            "total_checks": 1,
            "passed": int(ok),
            "failed": int(not ok),
            "checks": [{
                "check": "1440x900 #map 뷰포트 네 변 1px",
                "ok": ok,
                "detail": {"observed": observed, "expected": expected},
            }],
            **_report_metadata(),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        browser.close()
    return 0 if ok else 1

def _main_impl(checks: list[dict]) -> int:
    payloads = _fixture_payloads()
    expected_marker_count = len({(event["lat"], event["lng"]) for event in payloads["events.json"]})
    if expected_marker_count <= 0:
        raise AssertionError("fixture must contain at least one unique marker coordinate")
    launch_options = {"headless": True}
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if executable:
        launch_options["executable_path"] = executable

    with serve(ROOT / "web" / "public") as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        app_source = (ROOT / "web" / "public" / "app.js").read_text(encoding="utf-8")
        map_anchor = 'state.map = window.L.map("map"'
        if app_source.count(map_anchor) != 1:
            raise AssertionError(f"map instrumentation anchor count != 1: {app_source.count(map_anchor)}")
        instrumented_app = app_source.replace(
            map_anchor,
            'window.__notchimaMap = state.map = window.L.map("map"',
            1,
        )
        instrumented_anchor = 'window.__notchimaMap = state.map = window.L.map("map"'
        if instrumented_app.count(instrumented_anchor) != 1:
            raise AssertionError(f"map instrumentation replacement count != 1: {instrumented_app.count(instrumented_anchor)}")
        source_hashes = {
            "app.js": hashlib.sha256(app_source.encode("utf-8")).hexdigest(),
            "styles.css": hashlib.sha256((ROOT / "web" / "public" / "styles.css").read_bytes()).hexdigest(),
        }
        instrumented_app_hash = hashlib.sha256(instrumented_app.encode("utf-8")).hexdigest()
        served_hashes: dict[str, str] = {}

        def capture_asset_response(response):
            filename = urlparse(response.url).path.rsplit("/", 1)[-1]
            if filename not in source_hashes:
                return
            try:
                served_hashes[filename] = hashlib.sha256(response.body()).hexdigest()
            except Exception as error:
                served_hashes[filename] = f"error:{type(error).__name__}"

        page.on("response", capture_asset_response)
        ordinary_errors = _collect_browser_errors(page)

        def fulfill_app(route):
            route.fulfill(status=200, content_type="application/javascript", body=instrumented_app)

        retry_page = browser.new_page(viewport={"width": 1280, "height": 800})
        def allow_expected_retry_error(text, location):
            return "/data/events.json" in location and "503" in text
        retry_errors = _collect_browser_errors(retry_page, allow_expected_retry_error)
        _route_tile_fixture(retry_page)
        retry_page.route("**/app.js", fulfill_app)
        retry_failures = []
        retry_attempts = {"events": 0}
        retry_successes = {"events": 0}


        def fulfill_retry_data(route):
            filename = urlparse(route.request.url).path.rsplit("/", 1)[-1]
            payload = payloads.get(filename)
            if filename == "events.json" and retry_attempts["events"] == 0:
                retry_attempts["events"] += 1
                retry_failures.append({"url": route.request.url, "status": 503})
                route.fulfill(status=503, content_type="application/json", body='{"error":"fixture first load failure"}')
                return
            if payload is None:
                route.continue_()
                return
            if filename == "events.json":
                retry_successes["events"] += 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

        retry_page.route("**/data/*.json", fulfill_retry_data)
        retry_page.goto(base, wait_until="networkidle")
        retry_page.wait_for_selector("#loadError", state="visible")
        retry_page.click("#retry")
        retry_page.wait_for_function(
            "expected => document.getElementById('count')?.textContent === String(expected) && document.getElementById('loadError')?.hidden",
            arg=len(payloads["events.json"]),
        )
        expected_retry_url = base + "data/events.json"
        retry_ok = (retry_attempts["events"] == 1
                    and retry_successes["events"] == 1
                    and retry_failures == [{"url": expected_retry_url, "status": 503}]
                    and not retry_errors
                    and int(retry_page.locator("#count").inner_text()) == len(payloads["events.json"])
                    )
        _record(checks, "초기 events 실패 → Retry 실제 재로드 성공", retry_ok,
                {"eventsAttempts": retry_attempts["events"], "successfulEvents": retry_successes["events"], "failures": retry_failures,
                 "browserErrors": retry_errors, "count": retry_page.locator("#count").inner_text()})
        retry_page.close()

        page.route("**/app.js", fulfill_app)
        _route_fixture(page, payloads)
        page.goto(base, wait_until="networkidle")
        for viewport in ((1440, 900), (1280, 800), (360, 800), (390, 844)):
            _run_case(page, viewport, checks, payloads)
        provenance = {
            "sourceInputSHA256": source_hashes,
            "instrumentedServedAppSHA256": instrumented_app_hash,
            "servedResponseSHA256": served_hashes,
        }
        provenance_ok = (
            served_hashes.get("app.js") == instrumented_app_hash
            and served_hashes.get("styles.css") == source_hashes["styles.css"]
        )
        _record(checks, "instrumented app and CSS response provenance", provenance_ok, provenance)
        _record(checks, "일반 fixture pageerror·console.error 없음", not ordinary_errors, ordinary_errors)
        nojs_context = browser.new_context(java_script_enabled=False)
        nojs_page = nojs_context.new_page()
        nojs_page.set_viewport_size({"width": 360, "height": 800})
        nojs_page.goto(base, wait_until="networkidle")
        nojs_panel = nojs_page.locator("noscript .state-panel")
        nojs_ok = nojs_panel.count() == 1 and nojs_panel.is_visible() and "자바스크립트를 켜면" in nojs_panel.inner_text() and nojs_panel.locator("a[href='./data/events.json']").count() == 1
        _record(checks, "JavaScript 비활성 시 정적 안내·events.json 링크", nojs_ok, nojs_panel.inner_text() if nojs_panel.count() else "missing")
        nojs_context.close()

        blocked_page = browser.new_page(viewport={"width": 390, "height": 844})
        blocked_leaflet_url = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        def allow_blocked_leaflet_error(text, location):
            return (
                text.startswith("Map initialization failed") and "L is not defined" in text
            ) or (
                location == blocked_leaflet_url
                and "Failed to load resource" in text
                and "ERR_FAILED" in text
            )
        blocked_page_errors = _collect_browser_errors(blocked_page, allow_blocked_leaflet_error)
        blocked_page.route("**/leaflet.js", lambda route: route.abort())
        tile_failure_page = browser.new_page(viewport={"width": 1280, "height": 800})
        tile_failure_errors = _collect_browser_errors(
            tile_failure_page,
            lambda text, location: "tile.openstreetmap.org/" in location and "Failed to load resource" in text,
        )
        tile_stub_count = _route_fixture(tile_failure_page, payloads)
        tile_abort_count = {"count": 0}
        def abort_tile(route):
            tile_abort_count["count"] += 1
            route.abort()
        tile_failure_page.route("**/app.js", fulfill_app)
        tile_failure_page.route(TILE_URL_RE, abort_tile)
        tile_failure_page.goto(base, wait_until="networkidle")
        tile_failure_page.wait_for_function("document.getElementById('count')?.textContent !== ''")
        tile_abort_ok = tile_abort_count["count"] > 0
        _record(checks, "OSM 타일 abort route 실제 매칭", tile_abort_ok, {"abortedTiles": tile_abort_count["count"]})
        if tile_abort_ok:
            tile_failure_page.wait_for_function("document.getElementById('mapFallback')?.hidden === false", timeout=5000)
            _install_tile_failure_probe(tile_failure_page)
        post_abort = {"ok": False, "skipped": not tile_abort_ok}
        if tile_abort_ok:
            before = tile_failure_page.evaluate("""
            () => {
              const map = window.__notchimaMap;
              const center = map.getCenter();
              return {lat: center.lat, lng: center.lng, zoom: map.getZoom()};
            }
            """)
            try:
                tile_failure_page.locator(".leaflet-control-zoom-in").click()
                tile_failure_page.wait_for_function(
                    "expected => window.__notchimaMap?.getZoom() === expected", arg=before["zoom"] + 1, timeout=2000
                )
                zoomed = tile_failure_page.evaluate("() => window.__notchimaMap.getZoom()") == before["zoom"] + 1
                _wait_for_stable_map_fit(tile_failure_page)
                drag_events_before = tile_failure_page.evaluate("() => ({...(window.__notchimaTileFailureProbe?.nativeEventCounts || {})})")
                drag_target = _map_drag_target(tile_failure_page)
                if drag_target.get("error"):
                    post_abort["error"] = f"physical drag target unavailable: {drag_target}"
                else:
                    start_x = drag_target["x"]
                    start_y = drag_target["y"]
                    post_abort["dragTarget"] = {**drag_target, "start": {"x": start_x, "y": start_y}, "end": {"x": start_x + 80, "y": start_y + 18}}
                    tile_failure_page.mouse.move(start_x, start_y)
                    tile_failure_page.mouse.down()
                    tile_failure_page.mouse.move(start_x + 80, start_y + 18, steps=4)
                    tile_failure_page.mouse.up()
                    tile_failure_page.wait_for_function(
                        "origin => { const c = window.__notchimaMap?.getCenter(); return Boolean(c && (Math.abs(c.lat - origin.lat) > 1e-8 || Math.abs(c.lng - origin.lng) > 1e-8)); }",
                        arg={"lat": before["lat"], "lng": before["lng"]},
                        timeout=2000,
                    )
                    after = tile_failure_page.evaluate("""
                    () => { const c = window.__notchimaMap.getCenter(); return {lat: c.lat, lng: c.lng, zoom: window.__notchimaMap.getZoom()}; }
                    """)
                    moved = abs(after["lat"] - before["lat"]) > 1e-8 or abs(after["lng"] - before["lng"]) > 1e-8
                    drag_events_after = tile_failure_page.evaluate("() => ({...(window.__notchimaTileFailureProbe?.nativeEventCounts || {})})")
                    native_drag = (drag_events_after.get("dragstart", 0) > drag_events_before.get("dragstart", 0)
                                   and drag_events_after.get("dragend", 0) > drag_events_before.get("dragend", 0))
                    post_abort["nativeDrag"] = native_drag
                    post_abort["nativeEventsBefore"] = drag_events_before
                    post_abort["nativeEventsAfter"] = drag_events_after
                    post_abort.update({"ok": zoomed and moved and native_drag, "zoomed": zoomed, "moved": moved, "before": before, "after": after})
            except PlaywrightTimeoutError as error:
                post_abort["error"] = f"bounded post-abort stability timeout: {error}"
                post_abort["diagnostics"] = _tile_failure_diagnostics(
                    tile_failure_page,
                    expected_zoom=before["zoom"] + 1,
                    expected_center={"lat": before["lat"], "lng": before["lng"]},
                    tile_stub_count=tile_stub_count["count"],
                    tile_abort_count=tile_abort_count["count"],
                )
        _record(checks, "OSM 타일 실패 후 bounded zoom·물리 이동 안정성", post_abort.get("ok", False), post_abort)
        fallback_after_abort = tile_failure_page.locator("#mapFallback").is_visible()
        tile_recovery = {"ok": False, "skipped": False}
        if tile_abort_ok:
            try:
                tile_failure_page.unroute(TILE_URL_RE, abort_tile)
                healthy_tiles_before = tile_stub_count["count"]
                recovery_before = tile_failure_page.evaluate("""
                () => {
                  const map = window.__notchimaMap;
                  const center = map.getCenter();
                  return {lat: center.lat, lng: center.lng, zoom: map.getZoom()};
                }
                """)
                tile_failure_page.locator(".leaflet-control-zoom-in").click()
                tile_failure_page.wait_for_function(
                    "expected => window.__notchimaMap?.getZoom() === expected",
                    arg=recovery_before["zoom"] + 1,
                    timeout=2000,
                )
                recovery_zoomed = tile_failure_page.evaluate("() => window.__notchimaMap.getZoom()") == recovery_before["zoom"] + 1
                _wait_for_stable_map_fit(tile_failure_page)
                recovery_drag_events_before = tile_failure_page.evaluate("() => ({...(window.__notchimaTileFailureProbe?.nativeEventCounts || {})})")
                recovery_drag_target = _map_drag_target(tile_failure_page)
                if recovery_drag_target.get("error"):
                    tile_recovery["error"] = f"healthy physical drag target unavailable: {recovery_drag_target}"
                else:
                    recovery_start_x = recovery_drag_target["x"]
                    recovery_start_y = recovery_drag_target["y"]
                    tile_recovery["dragTarget"] = {**recovery_drag_target, "start": {"x": recovery_start_x, "y": recovery_start_y}, "end": {"x": recovery_start_x - 72, "y": recovery_start_y + 16}}
                    tile_failure_page.mouse.move(recovery_start_x, recovery_start_y)
                    tile_failure_page.mouse.down()
                    tile_failure_page.mouse.move(recovery_start_x - 72, recovery_start_y + 16, steps=4)
                    tile_failure_page.mouse.up()
                    tile_failure_page.wait_for_function(
                        "origin => { const c = window.__notchimaMap?.getCenter(); return Boolean(c && (Math.abs(c.lat - origin.lat) > 1e-8 || Math.abs(c.lng - origin.lng) > 1e-8)); }",
                        arg={"lat": recovery_before["lat"], "lng": recovery_before["lng"]},
                        timeout=2000,
                    )
                    tile_failure_page.wait_for_function(
                        "() => document.getElementById('mapFallback')?.hidden === true && !window.__notchimaMap?._animatingZoom && !window.__notchimaMap?._panAnim?._inProgress && !window.__notchimaMap?.dragging?.moving?.()",
                        timeout=5000,
                    )
                    recovery_after = tile_failure_page.evaluate("""
                    () => {
                      const map = window.__notchimaMap;
                      const center = map.getCenter();
                      const first = document.querySelector('#list > li');
                      return {
                        lat: center.lat,
                        lng: center.lng,
                        zoom: map.getZoom(),
                        idle: !map._animatingZoom && !map._panAnim?._inProgress && !map.dragging?.moving?.(),
                        fallbackHidden: document.querySelector('#mapFallback')?.hidden === true,
                        count: Number(document.querySelector('#count')?.textContent || -1),
                        listItems: document.querySelectorAll('#list > li').length,
                        firstListVisible: Boolean(first && getComputedStyle(first).display !== 'none' && first.getBoundingClientRect().width > 0 && first.getBoundingClientRect().height > 0),
                        markers: document.querySelectorAll('#map .leaflet-marker-icon').length,
                        zoomControls: document.querySelectorAll('.leaflet-control-zoom').length,
                      };
                    }
                    """)
                    healthy_tiles_after = tile_stub_count["count"]
                    moved_after_recovery = abs(recovery_after["lat"] - recovery_before["lat"]) > 1e-8 or abs(recovery_after["lng"] - recovery_before["lng"]) > 1e-8
                    recovery_stable = recovery_after["idle"] and recovery_after["fallbackHidden"]
                    controls_stable = recovery_after["zoomControls"] == 1
                    markers_stable = recovery_after["markers"] == expected_marker_count
                    list_stable = (recovery_after["count"] == len(payloads["events.json"])
                                   and recovery_after["listItems"] > 0 and recovery_after["firstListVisible"])
                    recovery_drag_events_after = tile_failure_page.evaluate("() => ({...(window.__notchimaTileFailureProbe?.nativeEventCounts || {})})")
                    recovery_native_drag = (recovery_drag_events_after.get("dragstart", 0) > recovery_drag_events_before.get("dragstart", 0)
                                            and recovery_drag_events_after.get("dragend", 0) > recovery_drag_events_before.get("dragend", 0))
                    tile_recovery.update({
                        "ok": (healthy_tiles_after > healthy_tiles_before
                               and recovery_zoomed and moved_after_recovery and recovery_native_drag and recovery_stable
                               and controls_stable and markers_stable and list_stable),
                        "healthyTilesBefore": healthy_tiles_before,
                        "healthyTilesAfter": healthy_tiles_after,
                        "healthyTilesRequested": healthy_tiles_after > healthy_tiles_before,
                        "recoveryBefore": recovery_before,
                        "recoveryAfter": recovery_after,
                        "zoomed": recovery_zoomed,
                        "moved": moved_after_recovery,
                        "nativeDrag": recovery_native_drag,
                        "nativeEventsBefore": recovery_drag_events_before,
                        "nativeEventsAfter": recovery_drag_events_after,
                        "stable": recovery_stable,
                        "controlsStable": controls_stable,
                        "markersStable": markers_stable,
                        "listStable": list_stable,
                    })
            except PlaywrightTimeoutError as error:
                tile_recovery["error"] = f"healthy tile recovery timeout: {error}"
                tile_recovery["diagnostics"] = _tile_failure_diagnostics(
                    tile_failure_page,
                    expected_zoom=recovery_before["zoom"] + 1,
                    expected_center={"lat": recovery_before["lat"], "lng": recovery_before["lng"]},
                    tile_stub_count=tile_stub_count["count"],
                    tile_abort_count=tile_abort_count["count"],
                )
            except Exception as error:
                tile_recovery["error"] = f"healthy tile recovery setup failed: {error}"
        else:
            tile_recovery["error"] = "abort route did not match; healthy recovery gate cannot pass"
        _record(checks, "OSM 타일 abort route 제거 후 healthy fixture·fallback 복구", tile_recovery.get("ok", False), tile_recovery)
        tile_failure_ok = (
            tile_abort_count["count"] > 0
            and post_abort.get("ok")
            and tile_recovery.get("ok")
            and fallback_after_abort
            and int(tile_failure_page.locator("#count").inner_text()) == len(payloads["events.json"])
            and tile_failure_page.locator(".leaflet-marker-icon").count() == expected_marker_count
            and tile_failure_page.locator(".leaflet-control-zoom").count() == 1
        )
        _record(checks, "OSM 타일 실패 → fallback·핀·지도 컨트롤 유지", tile_failure_ok, {
            "fallbackAfterAbort": fallback_after_abort,
            "fallbackAfterRecovery": tile_failure_page.locator("#mapFallback").is_visible(),
            "count": tile_failure_page.locator("#count").inner_text(),
            "markers": tile_failure_page.locator(".leaflet-marker-icon").count(),
            "expectedUniqueMarkers": expected_marker_count,
            "payloadEventCount": len(payloads["events.json"]),
            "fixtureStubbedTiles": tile_stub_count["count"],
            "zoomControls": tile_failure_page.locator(".leaflet-control-zoom").count(),
            "abortedTiles": tile_abort_count["count"],
            "browserErrors": tile_failure_errors,
        })
        tile_failure_page.close()
        blocked_page.route("**/app.js", fulfill_app)
        _route_fixture(blocked_page, payloads)
        blocked_page.goto(base, wait_until="networkidle")
        blocked_page.wait_for_function("document.getElementById('count')?.textContent !== ''")
        blocked_page.wait_for_function("document.getElementById('mapFallback')?.hidden === false", timeout=5000)
        blocked_ok = (not blocked_page_errors
                      and blocked_page.locator("#mapFallback").is_visible()
                      and int(blocked_page.locator("#count").inner_text()) == len(payloads["events.json"]))
        _record(checks, "Leaflet CDN 차단 시 목록 유지·지도 fallback 표시", blocked_ok,
                {"fallback": blocked_page.locator("#mapFallback").is_visible(),
                 "count": blocked_page.locator("#count").inner_text(),
                 "browserErrors": blocked_page_errors})
        blocked_page.close()

        browser.close()

    total_checks = len(checks)
    passed_checks = sum(item["ok"] for item in checks)
    all_checks_passed = total_checks > 0 and passed_checks == total_checks and all(item["ok"] for item in checks)
    summary = {
        "status": "passed" if all_checks_passed else "failed",
        "total_checks": total_checks,
        "passed": passed_checks,
        "failed": total_checks - passed_checks,
        "data_context": "deterministic routed map fixture; no public data writes",
        "viewports": ["1440x900", "1280x800", "360x800", "390x844"],
        "checks": checks,
        **_report_metadata(),
    }
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] == summary["total_checks"] else 1


def main() -> int:
    checks: list[dict] = []
    try:
        return _main_impl(checks)
    except Exception as error:
        _write_failure_evidence(checks, error)
        return 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--viewport-red", action="store_true")
    args = parser.parse_args()
    raise SystemExit(viewport_red_probe() if args.viewport_red else main())
