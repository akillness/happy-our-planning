"""T4 — UI 5필터·반응형·URL 복원 검증 (Playwright E2E 증거 생성).

web/public 를 임시 HTTP 서버로 띄우고 5축 필터를 조작 → URL 동기화 → 새로고침 복원 →
360px 스냅샷을 캡처한다. 산출 증거는 specs/verify/evidence/ 에 저장되고, 요약은 stdout(JSON).

실행: python3 specs/verify/run_ui_check.py
의존: playwright + chromium(무료·선택). 미설치 시 RuntimeError.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import functools
import hashlib
import http.server
import json
import os
import re
import socket
import threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build import build_index, build_pages

PUBLIC = ROOT / "web" / "public"
EVID = Path(__file__).resolve().parent / "evidence"
UI_EVIDENCE = EVID / "ui-check.json"
_SOURCE_FILES = {
    "spec.md": ROOT / "specs" / "spec.md",
    "seed.yaml": ROOT / "specs" / "seed.yaml",
    "index.html": PUBLIC / "index.html",
    "app.js": PUBLIC / "app.js",
    "styles.css": PUBLIC / "styles.css",
    "locales.json": PUBLIC / "locales.json",
    "i18n.js": PUBLIC / "i18n.js",
}


def _run_metadata() -> dict:
    present = {name: path.exists() for name, path in _SOURCE_FILES.items()}
    return {
        "spec_version": "1.6.0",
        "source_files_present": present,
        "source_hashes": {
            name: hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            for name, path in _SOURCE_FILES.items()
        },
    }

def _new_page(context, width: int, height: int):
    page = context.new_page()
    page.set_viewport_size({"width": width, "height": height})
    return page

def _physically_reachable(locator, width: int, height: int) -> tuple[bool, dict | None]:
    if not locator.is_visible():
        return False, None
    box = locator.bounding_box()
    if not box:
        return False, None
    reachable = (box["width"] > 0 and box["height"] > 0
                 and box["x"] >= -2 and box["y"] >= -2
                 and box["x"] + box["width"] <= width + 2
                 and box["y"] + box["height"] <= height + 2)
    return reachable, box
def _wait_for_pending_route(page, pending: dict[str, object], key: str, attempts: int = 200) -> None:
    for _ in range(attempts):
        if key in pending:
            return
        page.wait_for_timeout(25)
    raise AssertionError(f"{key} failure route was not captured during bounded Playwright pump")

def _write_ui_evidence(summary: dict) -> None:
    EVID.mkdir(parents=True, exist_ok=True)
    UI_EVIDENCE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


TILE_PNG = (ROOT / "assets" / "brand" / "notchima-mascot-256.png").read_bytes()
FIXED_CLOCK_SCRIPT = """
(() => {
  const NativeDate = Date;
  const fixed = NativeDate.parse("2026-10-14T16:00:00Z");
  const started = performance.now();
  const advancingNow = () => fixed + Math.max(0, performance.now() - started);
  class FixedDate extends NativeDate {
    constructor(...args) { super(...(args.length ? args : [advancingNow()])); }
    static now() { return advancingNow(); }
  }
  FixedDate.parse = NativeDate.parse;
  FixedDate.UTC = NativeDate.UTC;
  window.Date = FixedDate;
})();
"""


def _install_tile_fixture(page) -> dict:
    """Serve a valid 256px PNG for deterministic Leaflet math, not live-map proof."""
    tile_requests = {"count": 0}
    tile_pattern = re.compile(r"^https://[a-c]\.tile\.openstreetmap\.org/")

    def fulfill_tile(route):
        tile_requests["count"] += 1
        route.fulfill(status=200, content_type="image/png", body=TILE_PNG)

    page.route(tile_pattern, fulfill_tile)
    return tile_requests


def _initial_ui_fixture_payloads() -> dict:
    """Return fixed-date data for legacy UI checks; never writes into web/public."""
    events = [
        {
            "id": "ui-fixture:seoul-festival", "name": "서울 어린이 여름 음악축제",
            "description": "가족이 함께 보는 무료 음악축제", "event_type": "Event",
            "themes": ["축제"], "start_date": "2026-10-15", "end_date": "2026-10-16",
            "application_start": "2026-01-01", "application_end": "2026-12-31",
            "status": "Open", "sido": "서울특별시", "sigungu": "종로구",
            "lat": 37.57, "lng": 126.976, "age": "7-13", "age_bands": ["어린이"],
            "audience": ["가족"], "family_friendly": True,
            "family_evidence": "명시 문구: 가족", "price": "free",
            "url": "https://fixture/music", "source": "fixture",
        },
        {
            "id": "ui-fixture:seoul-paid-festival", "name": "서울 유료 어린이 축제",
            "description": "유료 가족 축제", "event_type": "Event", "themes": ["축제"],
            "start_date": "2026-10-15", "end_date": "2026-10-16", "status": "Open",
            "sido": "서울특별시", "sigungu": "마포구", "lat": 37.55, "lng": 126.91,
            "age": "7-13", "age_bands": ["어린이"], "audience": ["가족"],
            "family_friendly": True, "family_evidence": "명시 문구: 가족", "price": 1000,
            "url": "https://fixture/paid", "source": "fixture",
        },
        {
            "id": "ui-fixture:busan-craft", "name": "부산 가족 만들기 체험",
            "description": "가족 공예 체험", "event_type": "Event", "themes": ["체험"],
            "start_date": "2026-10-15", "end_date": "2026-10-16", "status": "Open",
            "sido": "부산광역시", "sigungu": "해운대구", "lat": 35.16, "lng": 129.16,
            "age": "7-13", "age_bands": ["어린이"], "audience": ["가족"],
            "family_friendly": True, "family_evidence": "명시 문구: 가족", "price": "free",
            "url": "https://fixture/craft", "source": "fixture",
        },
    ]
    flattened = []
    for event in events:
        location = {
            "sido": event.pop("sido"), "sigungu": event.pop("sigungu"),
            "lat": event.pop("lat"), "lng": event.pop("lng"),
        }
        event["location"] = location
        flattened.append(build_index._flatten(event, link_checks={}, now=dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc)))
    events = flattened
    # Applyable fixtures require an independently verified, fresh booking proof and window;
    # the two positive rows intentionally use timestamps at or before the frozen fixture clock.
    for index, booking_url in ((0, "https://www.busan.go.kr/booking-active"), (1, "https://www.busan.go.kr/booking-paid")):
        events[index].update({
            "application_start": "2026-01-01",
            "application_end": "2026-12-31",
            "booking_link_status": "verified",
            "booking_link_checked_at": "2026-10-14T15:00:00+00:00",
            "booking_link_url": booking_url,
            "booking_link_reason": None,
        })
    required = {"price", "age", "experience_summary", "family_evidence", "link_status", "link_checked_at", "link_url", "link_reason"}
    for event in events:
        missing = required.difference(event)
        if missing:
            raise AssertionError(f"UI fixture contract missing fields: {sorted(missing)}")
    if not any(event.get("price") is not None for event in events):
        raise AssertionError("UI fixture contract requires a representative price")
    return {
        "events.json": events,
        "facets.json": {"theme": {"축제": 2, "체험": 1}, "age_band": {"어린이": 3}},
        "regions.json": [
            {"sido": "서울특별시", "count": 2, "slug": "seoul", "centroid": [37.56, 126.94]},
            {"sido": "부산광역시", "count": 1, "slug": "busan", "centroid": [35.16, 129.16]},
        ],
        "updated.json": {
            "generated_at": "2026-10-15T01:00:00+09:00", "total_active": 3,
            "source_mode": "fixture", "collection_error_count": 0,
        },
    }


def _install_data_fixture(page, payloads: dict) -> dict:
    tile_requests = _install_tile_fixture(page)
    def fulfill(route):
        filename = urlparse(route.request.url).path.rsplit("/", 1)[-1]
        payload = payloads.get(filename)
        if payload is None:
            route.continue_()
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(payload, ensure_ascii=False))

    page.route("**/data/*.json", fulfill)
    return tile_requests


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


def _run_ui_check(checks: list[dict]) -> int:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

    EVID.mkdir(parents=True, exist_ok=True)

    def record(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    def count(page) -> int:
        return int(page.locator("#count").inner_text())
    def reset_scroll(page) -> None:
        page.evaluate("() => { window.scrollTo(0, 0); if (document.activeElement) document.activeElement.blur(); }")
        page.wait_for_function("window.scrollY === 0")
    def open_filter_disclosure(page) -> bool:
        menu = page.locator("#filterMenu")
        if not menu.count():
            return False
        if not menu.evaluate("el => el.open"):
            page.locator("#filterToggle").click()
            page.wait_for_function("document.querySelector('#filterMenu')?.open === true")
        return True
    def prepare_filter_action(page) -> None:
        menu = page.locator("#filterMenu")
        if not menu.count():
            raise AssertionError("native filter disclosure missing")
        if not menu.evaluate("el => el.open"):
            if not open_filter_disclosure(page):
                raise AssertionError("native filter disclosure could not be opened")

    def select_filter(page, selector: str, value: str) -> None:
        prepare_filter_action(page)
        page.select_option(selector, value)

    def warm_map(page) -> None:
        page.locator("#map").scroll_into_view_if_needed()
        page.wait_for_function("""
        () => {
          const fallback = document.querySelector('#mapFallback');
          if (fallback && !fallback.hidden) return false;
          const tiles = Array.from(document.querySelectorAll('#map .leaflet-tile'));
          return tiles.length > 0 && tiles.every(tile => tile.complete && tile.naturalWidth > 0);
        }
        """)
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


    launch_options = {"headless": True}
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if executable:
        launch_options["executable_path"] = executable

    with serve(PUBLIC) as base, sync_playwright() as p:
        browser = p.chromium.launch(**launch_options)
        context = browser.new_context()
        context.add_init_script(FIXED_CLOCK_SCRIPT)
        page = _new_page(context, 1280, 900)
        initial_payloads = _initial_ui_fixture_payloads()
        tile_requests = _install_data_fixture(page, initial_payloads)
        page.goto(base, wait_until="networkidle")
        page.wait_for_function("document.getElementById('count').textContent !== ''")

        total = count(page)
        record("초기 로드 + 가족 기본 카운트", total > 0, f"활성 가족 행사 {total}건")
        desktop_default = EVID / "ui-desktop-family.png"
        warm_map(page)
        record("일반 지도 페이지가 실제 OSM 타일 fixture를 사용함",
               tile_requests["count"] > 0, json.dumps(tile_requests, ensure_ascii=False))
        reset_scroll(page)
        page.screenshot(path=str(desktop_default), full_page=True)
        record("데스크톱 가족 기본 스냅샷", desktop_default.exists(), str(desktop_default.relative_to(ROOT)))
        hero_page = _new_page(context, 1440, 900)
        _install_data_fixture(hero_page, initial_payloads)
        hero_page.goto(base, wait_until="networkidle")
        hero_page.wait_for_function("document.getElementById('count').textContent !== ''")
        shell = hero_page.evaluate("""
        () => {
          const box = selector => { const el = document.querySelector(selector); if (!el) return null; const r = el.getBoundingClientRect(); return {left:r.left, top:r.top, right:r.right, bottom:r.bottom, width:r.width, height:r.height}; };
          return {map: box('#map'), panel: box('.map-panel'), search: box('#q'), transform: getComputedStyle(document.querySelector('.leaflet-container') || document.body).transform};
        }
        """)
        map_box = shell.get("panel") or shell.get("map")
        map_ok = bool(map_box and abs(map_box["left"]) <= 1 and abs(map_box["top"]) <= 1
                      and abs(map_box["right"] - 1440) <= 1 and abs(map_box["bottom"] - 900) <= 1)
        search_box = shell.get("search")
        search_ok = bool(search_box and abs(search_box["left"] + search_box["width"] / 2 - 720) <= 2)
        record("1440px 지도 우선 셸이 뷰포트를 채움", map_ok, json.dumps(shell, ensure_ascii=False))
        record("1440px 검색 입력이 뷰포트 가로 중앙", search_ok, json.dumps(shell, ensure_ascii=False))
        record("Leaflet 루트 transform 덮어쓰기 없음", shell.get("transform") in ("none", "matrix(1, 0, 0, 1, 0, 0)"), shell.get("transform"))
        hero_page.close()
        sample_card = page.locator("#list .card").filter(has_text="서울 어린이 여름 음악축제")
        sample_external_links = sample_card.locator("a[href^='http']")
        sample_external_hrefs = sample_external_links.evaluate_all("els => els.map(el => el.href)")
        record("출처·미승인 외부 앵커 없이 신선한 booking href만 보존", sample_external_hrefs == ["https://www.busan.go.kr/booking-active"], json.dumps(sample_external_hrefs, ensure_ascii=False))
        static_page = _new_page(context, 1280, 900)
        static_event = {
            "id": "fixture:static-detail", "name": "통제된 출처 없는 정적 행사",
            "description": "검증 증거가 없는 정적 상세 fixture", "event_type": "Event",
            "start_date": "2026-10-15", "end_date": "2026-10-16", "status": "Open",
            "sido": "서울특별시", "sigungu": "중구", "url": "https://example.org/no-proof",
            "price": "free",
        }
        static_html = build_pages.render_page(
            static_event, checks={}, now=dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc)
        )
        static_page.route(
            "**/events/fixture-static-detail.html",
            lambda route: route.fulfill(status=200, content_type="text/html", body=static_html),
        )
        static_response = static_page.goto(base + "events/fixture-static-detail.html", wait_until="networkidle")
        static_info_anchor = static_page.locator(".info-link a[href^='https://']")
        static_info_status = static_page.locator(".info-link .link-status")
        static_booking_anchor = static_page.locator(".booking-link a[href^='https://']")
        static_booking_status = static_page.locator(".booking-link .link-status")
        static_title = static_page.locator("h1").inner_text()
        record("정적 fixture 응답·의미 있는 제목", static_response is not None
               and static_response.status == 200 and static_title == "통제된 출처 없는 정적 행사", static_title)
        static_info_text = static_info_status.inner_text() if static_info_status.count() else ""
        static_booking_text = static_booking_status.inner_text() if static_booking_status.count() else ""
        static_info_key = static_info_status.get_attribute("data-i18n") if static_info_status.count() else ""
        static_booking_key = static_booking_status.get_attribute("data-i18n") if static_booking_status.count() else ""
        record("정적 상세 blocked info·booking은 앵커 없이 blocked 상태 표시", static_info_anchor.count() == 0
               and static_info_key == "link.status.blocked"
               and static_info_text == "연결 차단"
               and static_booking_anchor.count() == 0
               and static_booking_key == "link.status.blocked"
               and static_booking_text == "연결 차단",
               json.dumps({"infoKey": static_info_key, "bookingKey": static_booking_key,
                           "infoStatus": static_info_text, "bookingStatus": static_booking_text,
                           "infoExternal": static_info_anchor.count(), "bookingExternal": static_booking_anchor.count()}, ensure_ascii=False))
        static_page.close()
        filter_disclosure = open_filter_disclosure(page)
        record("네이티브 필터 disclosure 열기", filter_disclosure,
               "#filterMenu/#filterToggle" if filter_disclosure else "missing native filter disclosure")

        # 5축 필터 조작: 키워드 / 지역 / 테마 / 나이대 / 기간 + 신청가능
        prepare_filter_action(page)
        page.fill("#q", "축제")
        select_filter(page, "#sido", "서울특별시")
        select_filter(page, "#theme", "축제")
        select_filter(page, "#age", "어린이")
        prepare_filter_action(page)
        page.fill("#from", "2026-01-01")
        prepare_filter_action(page)
        page.fill("#to", "2026-12-31")
        prepare_filter_action(page)
        page.check("#applyable")
        page.wait_for_function("new URL(location.href).searchParams.get('applyable') === '1'")
        filtered = count(page)

        qs = parse_qs(urlparse(page.url).query)
        want = {"q": "축제", "sido": "서울특별시", "theme": "축제", "age": "어린이",
                "from": "2026-01-01", "to": "2026-12-31", "applyable": "1"}
        url_ok = all(qs.get(k, [None])[0] == v for k, v in want.items())
        record("5축 필터 → URL 동기화", url_ok, f"{urlparse(page.url).query}")
        expected_filtered_ids = {"ui-fixture:seoul-festival", "ui-fixture:seoul-paid-festival"}
        expected_filtered_names = {event["name"] for event in initial_payloads["events.json"] if event["id"] in expected_filtered_ids}
        filtered_names = set(page.locator("#list .card-title button").all_inner_texts())
        filtered_ok = filtered == len(expected_filtered_ids) and filtered_names == expected_filtered_names
        record("필터 적용 결과 카운트·fixture 식별자", filtered_ok,
               json.dumps({"count": filtered, "expectedCount": len(expected_filtered_ids), "names": sorted(filtered_names), "expectedNames": sorted(expected_filtered_names)}, ensure_ascii=False))

        shared_url = page.url

        # 새로고침/공유 링크 복원: 동일 URL 신규 페이지 로드 → 입력값·카운트 복원
        page2 = _new_page(context, 1280, 900)
        _install_data_fixture(page2, initial_payloads)
        page2.goto(shared_url, wait_until="networkidle")
        page2.wait_for_function("document.getElementById('count').textContent !== ''")
        open_filter_disclosure(page2)
        page2.wait_for_function("new URL(location.href).searchParams.get('q') === '축제'")
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
        recount = count(page2)
        record("URL 복원(새 세션 입력값)", restore_ok, json.dumps(restored, ensure_ascii=False))
        record("URL 복원(카운트 일치)", recount == filtered, f"{recount}=={filtered}")

        stale_page = _new_page(context, 1280, 900)
        _install_data_fixture(stale_page, initial_payloads)
        stale_url = (base + "?sido=%EC%97%86%EB%8A%94%EC%A7%80%EC%97%AD"
                     "&theme=%EC%97%86%EB%8A%94%ED%85%8C%EB%A7%88&age=%EC%97%86%EB%8A%94%EB%82%98%EC%9D%B4&kids=0")
        stale_page.goto(stale_url, wait_until="networkidle")
        stale_page.wait_for_function("document.getElementById('count').textContent !== ''")
        stale_values = [stale_page.input_value(selector) for selector in ("#sido", "#theme", "#age")]
        stale_labels = [stale_page.locator(selector).evaluate("el => el.selectedOptions[0]?.textContent || ''")
                        for selector in ("#sido", "#theme", "#age")]
        stale_ok = (stale_values == ["없는지역", "없는테마", "없는나이"]
                    and all("현재 목록 없음" in label for label in stale_labels)
                    and count(stale_page) == 0)
        record("오래된 URL 필터값 보존·무결과", stale_ok,
               json.dumps({"values": stale_values, "labels": stale_labels, "count": count(stale_page)}, ensure_ascii=False))
        stale_page.close()
        # Preserve the legacy mobile evidence for the 5-axis state.
        page2.set_viewport_size({"width": 360, "height": 780})
        page2.wait_for_function("document.querySelector('#map.leaflet-container, #map .leaflet-container')")
        shot360 = EVID / "ui-360px.png"
        warm_map(page2)
        reset_scroll(page2)
        page2.screenshot(path=str(shot360), full_page=True)
        record("360px 모바일 스냅샷", shot360.exists(), str(shot360.relative_to(ROOT)))

        # Reset must return to the family default, not to the all-events scope.
        page2.set_viewport_size({"width": 1280, "height": 900})
        open_filter_disclosure(page2)
        prepare_filter_action(page2)
        page2.click("#reset")
        page2.wait_for_function("([selector, previous]) => Number(document.querySelector(selector).textContent) === previous",
                                arg=["#count", total])
        reset_count = count(page2)
        record("reset → 전체 복귀", reset_count == total, f"{reset_count}=={total}")
        record("reset → 가족/무료 토글 초기화", page2.is_checked("#kids") and not page2.is_checked("#freeOnly"))
        select_filter(page2, "#sido", "서울특별시")
        page2.wait_for_function("new URL(location.href).searchParams.get('sido') === '서울특별시'")
        combined_before = count(page2)
        combined_before_cards = page2.locator("#list .card")
        record("가족·서울 → 정확한 fixture 범위", combined_before == 2,
               f"count={combined_before} expected=2")

        # free_only must remove paid/unknown-price rows and synchronize its URL state.
        free_before = combined_before
        prepare_filter_action(page2)
        page2.check("#freeOnly")
        free_count = count(page2)
        free_cards = page2.locator("#list .card")
        card_names = set(page2.locator("#list .card-title button").all_inner_texts())
        all_free = all(card.locator(".card-price").inner_text() == "무료" for card in free_cards.all())
        free_qs = parse_qs(urlparse(page2.url).query)
        expected_combined_ids = {"ui-fixture:seoul-festival"}
        expected_combined_names = {event["name"] for event in initial_payloads["events.json"] if event["id"] in expected_combined_ids}
        cards_consistent = (free_count == len(expected_combined_ids)
                            and card_names == expected_combined_names
                            and all(card.locator(".card-location").inner_text().startswith("서울특별시")
                                    and card.locator(".card-price").inner_text() == "무료"
                                    for card in free_cards.all()))
        record("무료만 토글 → 실제 필터링", free_count < free_before and all_free, f"{free_count}/{free_before}")
        record("무료만 → URL 동기화", free_qs.get("free_only", [None])[0] == "1", urlparse(page2.url).query)
        record("가족·서울·무료 → 정확한 fixture 카드/카운트", cards_consistent,
               f"{free_count} cards={free_cards.count()} names={sorted(card_names)} expected={sorted(expected_combined_names)}")
        map_container_count = page2.locator(".leaflet-container").count()
        marker_count = page2.locator(".leaflet-marker-icon").count()
        map_consistent = (map_container_count == 1 and marker_count == len(expected_combined_ids))
        record("가족·서울·무료 → 정확한 fixture 지도", map_consistent,
               f"containers={map_container_count} markers={marker_count} expected={len(expected_combined_ids)}")

        page2.click("#aiBtn")
        page2.wait_for_function("document.querySelector('#aiDialog[open]') !== null")
        rec_names = page2.locator("#aiDialog .recommendations li .rec-open").all_inner_texts()
        rec_text = page2.locator("#aiDialog").inner_text()
        recommendations_consistent = all(name in expected_combined_names for name in rec_names) and len(rec_names) <= free_count
        if not rec_names:
            recommendations_consistent = recommendations_consistent and "추천할 진행 중 행사가 없습니다" in rec_text
        record("가족·서울·무료 → 추천 범위 일치", recommendations_consistent, rec_text)
        page2.locator("#aiDialog .close").press("Enter")
        open_filter_disclosure(page2)

        # A no-result query exposes the empty state and reset recovers the family default.
        prepare_filter_action(page2)
        page2.fill("#q", "이 조건은 반드시 결과가 없어야 합니다")
        page2.wait_for_function("document.getElementById('count').textContent === '0'")
        no_results_ok = page2.locator("#empty").is_visible() and count(page2) == 0
        record("무결과 상태 표시", no_results_ok, page2.locator("#empty").inner_text())
        open_filter_disclosure(page2)
        prepare_filter_action(page2)
        page2.click("#reset")
        page2.wait_for_function("([selector, previous]) => Number(document.querySelector(selector).textContent) === previous",
                                arg=["#count", total])
        record("무결과 → reset 복구", count(page2) == total and not page2.locator("#empty").is_visible())

        # kids=0 is shareable and must restore the broader, non-family scope.
        kids_page = _new_page(context, 1280, 900)
        _install_data_fixture(kids_page, initial_payloads)
        kids_page.goto(base + "?kids=0", wait_until="networkidle")
        kids_page.wait_for_function("document.getElementById('count').textContent !== ''")
        kids_count = count(kids_page)
        kids_qs = parse_qs(urlparse(kids_page.url).query)
        record("kids=0 → 가족 범위 해제", not kids_page.is_checked("#kids") and kids_count >= total,
               f"{kids_count}>={total}")
        record("kids=0 → URL 복원", kids_qs.get("kids", [None])[0] == "0")
        kids_page.close()
        # Deterministic route fixture proves recommendation eligibility rather than accepting
        # an empty recommendation list from expired live data.
        fixture_events = [
            {
                "id": "fixture:active-seoul-free", "name": "서울 10월 가족 체험",
                "description": "오늘 참여할 수 있는 가족 체험", "event_type": "Event",
                "themes": ["체험"], "start_date": "2026-10-15", "end_date": "2026-10-16",
                "status": "Open", "sido": "서울특별시", "sigungu": "종로구",
                "lat": 37.57, "lng": 126.976, "age": "7-13", "age_bands": ["어린이", "어린이"],
                "audience": ["가족"], "family_friendly": True,
                "family_evidence": "명시 문구: 가족", "price": "free", "url": "https://fixture/active",
            },
            {
                "id": "fixture:paid-seoul-family", "name": "서울 유료 가족 체험",
                "description": "유료 가족 체험", "event_type": "Event", "themes": ["체험"],
                "start_date": "2026-10-15", "end_date": "2026-10-16", "status": "Open",
                "sido": "서울특별시", "sigungu": "마포구", "lat": 37.55, "lng": 126.91,
                "age": "7-13", "age_bands": ["어린이"], "audience": ["가족"],
                "family_friendly": True, "family_evidence": "명시 문구: 가족",
                "price": 1000, "url": "https://fixture/paid",
            },
            {
                "id": "fixture:active-busan-free", "name": "부산 무료 가족 체험",
                "description": "부산 가족 체험", "event_type": "Event", "themes": ["체험"],
                "start_date": "2026-10-15", "end_date": "2026-10-16", "status": "Open",
                "sido": "부산광역시", "sigungu": "해운대구", "lat": 35.16, "lng": 129.16,
                "age": "7-13", "age_bands": ["어린이"], "audience": ["가족"],
                "family_friendly": True, "family_evidence": "명시 문구: 가족",
                "price": "free", "url": "https://fixture/busan",
            },
            {
                "id": "fixture:adult-seoul-free", "name": "서울 성인 무료 강연",
                "description": "성인 전용 강연", "event_type": "Event", "themes": ["교육"],
                "start_date": "2026-10-15", "end_date": "2026-10-16", "status": "Open",
                "sido": "서울특별시", "sigungu": "중구", "lat": 37.56, "lng": 126.98,
                "age": "성인 전용", "age_bands": [], "audience": ["성인 전용"],
                "family_friendly": False, "family_evidence": "명시 제한: 성인 전용",
                "price": "free", "url": "https://fixture/adult",
            },
            {
                "id": "fixture:expired-start-only", "name": "서울 10월 지난 과학 체험",
                "description": "지난 날짜의 시작일-only 가족 체험", "event_type": "Event",
                "themes": ["체험"], "start_date": "2026-10-14", "end_date": None,
                "status": "Open", "sido": "서울특별시", "sigungu": "강서구",
                "lat": 37.55, "lng": 126.85, "age": "7-13", "age_bands": ["어린이"],
                "audience": ["가족"], "family_friendly": True,
                "family_evidence": "명시 문구: 가족", "price": "free", "url": "https://fixture/expired",
            },
        ]
        fixture_events[0].update({
            "is_experience": True, "experience_categories": ["과학·탐구"],
            "experience_summary": "직접 실험하고 관찰하는 과학 체험",
            "link_status": "verified", "link_checked_at": "2026-10-14T15:00:00+00:00",
            "link_url": "https://www.busan.go.kr/active", "link_reason": None,
            "booking_link_status": "verified", "booking_link_checked_at": "2026-10-14T15:00:00+00:00",
            "booking_link_url": "https://booking.example/active", "booking_link_reason": None,
        })
        fixture_events[1].update({
            "is_experience": True, "experience_categories": ["만들기·예술", "신체·놀이"],
            "experience_summary": "직접 만들고 놀며 참여하는 공예 체험",
            "link_status": "verified", "link_checked_at": "2026-10-14T15:00:00+00:00",
            "link_url": "https://[::ffff:127.0.0.1]/", "link_reason": None,
            "booking_link_status": "verified", "booking_link_checked_at": "2026-10-14T15:00:00+00:00",
            "booking_link_url": "https://[::ffff:127.0.0.1]/booking", "booking_link_reason": None,
        })
        fixture_events[2].update({
            "booking_link_status": "verified", "booking_link_checked_at": "2026-10-01T00:00:00+00:00",
            "booking_link_url": "https://booking.example/stale", "booking_link_reason": "검증 기록 만료",
        })
        fixture_events[4].update({
            "is_experience": True, "experience_categories": ["과학·탐구"],
            "experience_summary": "지난 날짜의 체험",
            "link_status": "unverified", "link_checked_at": "2026-10-01T00:00:00+00:00",
            "link_url": None, "link_reason": "검증 만료",
        })
        fixture_payloads_updated = {
            "source_mode": "offline", "last_success_at": "2026-10-15T01:00:00+09:00",
            "collection_started_at": "2026-10-15T01:00:00+09:00", "collection_error_count": 1,
            "collection_errors": ["live source unavailable"],
        }
        fixture_payloads = {
            "events.json": fixture_events,
            "facets.json": {"theme": {"체험": 5}, "age_band": {"어린이": 99}},
            "regions.json": [
                {"sido": "서울특별시", "count": 4, "slug": "seoul", "centroid": [37.56, 126.94]},
                {"sido": "부산광역시", "count": 1, "slug": "busan", "centroid": [35.16, 129.16]},
            ],
            "updated.json": {"generated_at": "2026-10-15T01:00:00+09:00", "total_active": 5, **fixture_payloads_updated},
        }

        def fulfill_fixture(route):
            filename = urlparse(route.request.url).path.rsplit("/", 1)[-1]
            payload = fixture_payloads.get(filename)
            if payload is None:
                route.continue_()
                return
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(payload, ensure_ascii=False))

        preload_page = _new_page(context, 390, 844)
        _install_tile_fixture(preload_page)
        preload_attempts = {"events.json": 0}
        preload_pending: dict[str, object] = {}
        preload_errors = []
        preload_page.on("pageerror", lambda error: preload_errors.append({
            "kind": "pageerror", "text": str(error), "location": None
        }))
        preload_page.on("console", lambda message: preload_errors.append({
            "kind": "console", "type": message.type, "text": message.text,
            "location": message.location
        }) if message.type == "error" else None)

        def fulfill_preload(route):
            filename = urlparse(route.request.url).path.rsplit("/", 1)[-1]
            if filename == "events.json":
                preload_attempts[filename] += 1
                if preload_attempts[filename] == 1:
                    preload_pending[filename] = route
                    return
            payload = initial_payloads.get(filename)
            if payload is None:
                route.continue_()
                return
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(payload, ensure_ascii=False))

        preload_page.route("**/data/*.json", fulfill_preload)
        preload_url = base + "?sido=%EC%84%9C%EC%9A%B8%ED%8A%B9%EB%B3%84%EC%8B%9C&free_only=1"
        preload_page.goto(preload_url, wait_until="domcontentloaded")
        _wait_for_pending_route(preload_page, preload_pending, "events.json")
        pending_filter_toggle = preload_page.locator("#filterToggle")
        pending_filter_reachable, pending_filter_box = _physically_reachable(pending_filter_toggle, 390, 844)
        if not pending_filter_reachable:
            raise AssertionError(f"filter toggle not physically reachable before deferred failure: {pending_filter_box}")
        pending_filter_toggle.click()
        preload_page.wait_for_function("document.querySelector('#filterMenu')?.open === true")
        preload_pending.pop("events.json").fulfill(status=503, content_type="application/json",
                                                   body=json.dumps({"error": "fixture first load failure"}))
        preload_page.wait_for_function("document.getElementById('loadError')?.hidden === false")
        failure_error_reachable, failure_error_box = _physically_reachable(preload_page.locator("#loadError"), 390, 844)
        failure_retry_reachable, failure_retry_box = _physically_reachable(preload_page.locator("#retry"), 390, 844)
        failure_filter_closed = preload_page.evaluate("() => document.querySelector('#filterMenu')?.open === false")
        preload_controls = ["q", "sido", "theme", "age", "activity", "from", "to",
                            "kids", "freeOnly", "applyable", "reset", "aiBtn",
                            "sortRel", "sortNew", "sortDist", "pagePrev", "pageNext"]
        preload_initial = preload_page.evaluate("""
        ids => ({
          disabled: Object.fromEntries(ids.map(id => [id, Boolean(document.getElementById(id)?.disabled)])),
          query: window.location.search,
          map: document.querySelectorAll('#map.leaflet-container, #map .leaflet-container').length,
          errorVisible: document.querySelector('#loadError')?.hidden === false,
          retryVisible: document.querySelector('#retry')?.hidden === false,
        })
        """, preload_controls)
        preload_all_disabled = all(preload_initial["disabled"].get(id) is True for id in preload_controls)
        preload_url_ok = ("sido=%EC%84%9C%EC%9A%B8%ED%8A%B9%EB%B3%84%EC%8B%9C" in preload_initial["query"]
                          and "free_only=1" in preload_initial["query"])
        record("초기 events 실패 시 q·데이터 의존 입력/액션 잠금·URL 보존·지도 유지·대기 중 필터 닫힘",
               preload_initial["errorVisible"] and preload_initial["retryVisible"]
               and failure_filter_closed and failure_error_reachable and failure_retry_reachable
               and preload_all_disabled and preload_url_ok and preload_initial["map"] == 1,
               json.dumps({**preload_initial, "filterClosed": failure_filter_closed,
                           "errorReachable": failure_error_reachable, "retryReachable": failure_retry_reachable,
                           "errorBox": failure_error_box, "retryBox": failure_retry_box}, ensure_ascii=False))
        preload_toggle = preload_page.locator("#resultsToggle")
        preload_page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'false'")
        error_reachable, error_box = _physically_reachable(preload_page.locator("#loadError"), 390, 844)
        retry_reachable, retry_box = _physically_reachable(preload_page.locator("#retry"), 390, 844)
        opened_error_ui = preload_page.evaluate("""
        () => ({collapsed: document.querySelector('#resultsPanel')?.dataset.collapsed,
                expanded: document.querySelector('#resultsToggle')?.getAttribute('aria-expanded'),
                errorVisible: document.querySelector('#loadError')?.hidden === false,
                retryVisible: document.querySelector('#retry')?.hidden === false,
                errorText: document.querySelector('#loadError')?.innerText || ''})
        """)
        preload_toggle.click()
        preload_page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'true'")
        collapsed_initial_error = preload_page.evaluate("""
        () => ({collapsed: document.querySelector('#resultsPanel')?.dataset.collapsed,
                expanded: document.querySelector('#resultsToggle')?.getAttribute('aria-expanded')})
        """)
        preload_toggle.click()
        preload_page.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'false'")
        expanded_initial_error = preload_page.evaluate("""
        () => ({collapsed: document.querySelector('#resultsPanel')?.dataset.collapsed,
                expanded: document.querySelector('#resultsToggle')?.getAttribute('aria-expanded')})
        """)
        record("초기 오류 모바일 결과 drawer 자동 확장·오류 재시도 도달성·접기·재펼치기",
               opened_error_ui["collapsed"] == "false"
               and opened_error_ui["expanded"] == "true"
               and opened_error_ui["errorVisible"] and opened_error_ui["retryVisible"]
               and bool(opened_error_ui["errorText"])
               and error_reachable and retry_reachable
               and collapsed_initial_error == {"collapsed": "true", "expanded": "false"}
               and expanded_initial_error == {"collapsed": "false", "expanded": "true"},
               json.dumps({"opened": opened_error_ui, "errorBox": error_box, "retryBox": retry_box,
                           "errorReachable": error_reachable, "retryReachable": retry_reachable,
                           "collapsed": collapsed_initial_error, "expanded": expanded_initial_error}, ensure_ascii=False))
        preload_page.click("#retry")
        preload_page.wait_for_function("document.getElementById('loadError')?.hidden === true && document.getElementById('count')?.textContent === '1'")
        preload_after_retry = preload_page.evaluate("""
        ids => ({
          disabled: Object.fromEntries(ids.map(id => [id, Boolean(document.getElementById(id)?.disabled)])),
          count: document.getElementById('count')?.textContent || '',
          errorHidden: document.getElementById('loadError')?.hidden === true,
          query: window.location.search,
        })
        """, preload_controls)
        expected_retry_disabled = {control_id: control_id in {"sortDist", "pagePrev", "pageNext"}
                                  for control_id in preload_controls}
        retry_controls_ok = preload_after_retry["disabled"] == expected_retry_disabled
        expected_retry_errors = [error for error in preload_errors
                                 if error.get("kind") == "console"
                                 and re.search(r"\b503\b", error.get("text", ""))
                                 and ("/data/events.json" in str(error.get("location", {}).get("url", ""))
                                      or re.search(r"/data/events\.json(?:[?#\s\"']|$)", error.get("text", ""))) ]
        unexpected_retry_errors = [error for error in preload_errors if error not in expected_retry_errors]
        retry_error_ok = (len(expected_retry_errors) == 1 and not unexpected_retry_errors)
        record("Retry 성공 시 필터·액션 복구·카운트·URL 보존·브라우저 오류 없음",
               preload_attempts["events.json"] == 2 and preload_after_retry["count"] == "1"
               and preload_after_retry["errorHidden"] and retry_controls_ok
               and preload_url_ok and retry_error_ok,
               json.dumps({"attempts": preload_attempts, "after": preload_after_retry,
                           "expectedDisabled": expected_retry_disabled,
                           "browserErrors": preload_errors,
                           "expectedInitial503": expected_retry_errors,
                           "unexpectedErrors": unexpected_retry_errors}, ensure_ascii=False))
        preload_page.close()
        missing_map_page = _new_page(context, 1280, 800)
        _install_data_fixture(missing_map_page, initial_payloads)
        missing_map_page.route("**/leaflet.js", lambda route: route.fulfill(
            status=200, content_type="application/javascript", body="window.L = undefined;"
        ))
        missing_map_page.goto(base, wait_until="networkidle")
        missing_map_page.wait_for_function("document.getElementById('count').textContent !== ''")
        missing_map_page.wait_for_function("document.getElementById('mapFallback')?.hidden === false")
        missing_map_page.click("#locateBtn")
        missing_map_hint = missing_map_page.locator("#locateHint").inner_text()
        missing_map_fallback = missing_map_page.locator("#mapFallback").is_visible()
        record("지도 초기화 실패 시 Locate가 위치를 가장하지 않음",
               missing_map_fallback and missing_map_hint == "지도를 사용할 수 없어 현재 위치를 표시할 수 없습니다.",
               json.dumps({"fallback": missing_map_fallback, "hint": missing_map_hint}, ensure_ascii=False))
        missing_map_page.close()


        fixture_page = _new_page(context, 1280, 900)
        _install_tile_fixture(fixture_page)
        fixture_page.route("**/data/*.json", fulfill_fixture)
        fixture_url = base + "?sido=%EC%84%9C%EC%9A%B8%ED%8A%B9%EB%B3%84%EC%8B%9C&free_only=1"
        fixture_page.goto(fixture_url, wait_until="networkidle")
        fixture_page.wait_for_function("document.getElementById('count').textContent !== ''")
        open_filter_disclosure(fixture_page)
        supplied_age_label = fixture_page.locator("#age option[value='어린이']").inner_text()
        record("라우트 fixture age count 중복·facet 오염 방지", supplied_age_label == "어린이 (4)",
               json.dumps({"label": supplied_age_label,
                           "ageOptions": fixture_page.locator("#age option").all_inner_texts(),
                           "fixtureEventCount": len(fixture_payloads["events.json"]),
                           "facetAge": fixture_payloads["facets.json"].get("age_band")}, ensure_ascii=False))
        fixture_cards = fixture_page.locator("#list .card")
        fixture_names = fixture_cards.locator(".card-title button").all_inner_texts()
        expected_name = "서울 10월 가족 체험"
        expired_name = "서울 10월 지난 과학 체험"
        excluded_names = {"서울 유료 가족 체험", "부산 무료 가족 체험", "서울 성인 무료 강연"}
        fixture_cards_ok = (count(fixture_page) == 2
                            and set(fixture_names) == {expected_name, expired_name}
                            and not excluded_names.intersection(fixture_names))
        fixture_container_count = fixture_page.locator(".leaflet-container").count()
        fixture_marker_count = fixture_page.locator(".leaflet-marker-icon").count()
        fixture_map_ok = fixture_container_count == 1 and fixture_marker_count == 2
        fixture_page.click("#aiBtn")
        fixture_page.wait_for_function("document.querySelector('#aiDialog[open]') !== null")
        fixture_recs = fixture_page.locator("#aiDialog .recommendations li .rec-open").all_inner_texts()
        fixture_recommendation_ok = fixture_recs == [expected_name] and expired_name not in fixture_recs
        expired_fixture_card = fixture_cards.filter(has_text=expired_name)
        expired_status_badge = expired_fixture_card.locator(".badge.ended")
        expired_status_text = expired_status_badge.inner_text() if expired_status_badge.count() else ""
        expired_recommendation_ok = (
            expired_fixture_card.count() == 1
            and expired_status_badge.count() == 1
            and expired_status_text == "종료"
            and fixture_recs == [expected_name]
            and expired_name not in fixture_recs
        )
        record("만료된 시작일-only 행사 추천 제외", expired_recommendation_ok,
               json.dumps({"card": expired_fixture_card.count(), "status": expired_status_text,
                           "recommendations": fixture_recs}, ensure_ascii=False))
        record("결정적 가족·서울·무료 카드 필터", fixture_cards_ok, json.dumps(fixture_names, ensure_ascii=False))
        record("결정적 가족 결과 지도 2건", fixture_map_ok,
               f"containers={fixture_container_count} markers={fixture_marker_count}")
        record("결정적 활성 행사만 추천", fixture_recommendation_ok,
               json.dumps(fixture_recs, ensure_ascii=False))
        fixture_page.locator("#aiDialog .close").press("Enter")
        verified_card = fixture_page.locator("#list .card").filter(has_text=expected_name)
        verified_info_anchor = verified_card.locator("a[href='https://www.busan.go.kr/active']")
        verified_booking_anchor = verified_card.locator("a[href='https://booking.example/active']")
        verified_info_rel = verified_info_anchor.get_attribute("rel") if verified_info_anchor.count() else ""
        verified_booking_rel = verified_booking_anchor.get_attribute("rel") if verified_booking_anchor.count() else ""
        record("신선한 verified info·booking만 각각 외부 clickout", verified_info_anchor.count() == 1
               and verified_booking_anchor.count() == 1
               and "noopener" in (verified_info_rel or "")
               and "noopener" in (verified_booking_rel or ""),
               json.dumps({"info": verified_info_anchor.count(), "booking": verified_booking_anchor.count()}, ensure_ascii=False))

        expired_card = fixture_page.locator("#list .card").filter(has_text=expired_name)
        expired_anchor = expired_card.locator("a[href^='https://']")
        expired_card.locator(".card-open").click()
        fixture_page.wait_for_function("document.querySelector('#detail[open]') !== null")
        detail_external = fixture_page.locator("#detail a[href^='https://']")
        detail_text = fixture_page.locator("#detail").inner_text()
        record("만료·미검증 출처는 카드/상세 외부 앵커 금지", expired_anchor.count() == 0
               and detail_external.count() == 0
               and ("연결 미확인" in detail_text or "확인 만료" in detail_text), detail_text)
        fixture_page.keyboard.press("Escape")
        open_filter_disclosure(fixture_page)
        prepare_filter_action(fixture_page)
        fixture_page.click("#reset")
        fixture_page.wait_for_function("document.getElementById('count').textContent === '4'")
        reset_names = fixture_page.locator("#list .card .card-title button").all_inner_texts()
        experience_first_ok = (
            reset_names[:2] == ["서울 10월 가족 체험", "서울 유료 가족 체험"]
            and "부산 무료 가족 체험" in reset_names
            and "서울 10월 지난 과학 체험" in reset_names
        )
        record("기본 결과 경험 우선·비경험/종료 행사 보존", experience_first_ok,
               json.dumps({"order": reset_names}, ensure_ascii=False))
        stale_booking_card = fixture_page.locator("#list .card").filter(has_text="부산 무료 가족 체험")
        stale_booking_anchor = stale_booking_card.locator("a[href='https://booking.example/stale']")
        stale_booking_card.locator(".card-open").click()
        fixture_page.wait_for_function("document.querySelector('#detail[open]') !== null")
        stale_booking_detail_anchor = fixture_page.locator("#detail a[href='https://booking.example/stale']")
        stale_booking_detail_text = fixture_page.locator("#detail").inner_text()
        record("신선도 만료 booking은 활성 행사에서도 외부 앵커 금지", stale_booking_anchor.count() == 0
               and stale_booking_detail_anchor.count() == 0
               and ("확인 만료" in stale_booking_detail_text or "검증 기록 만료" in stale_booking_detail_text), stale_booking_detail_text)
        fixture_page.keyboard.press("Escape")
        mapped_card = fixture_page.locator("#list .card").filter(has_text="서울 유료 가족 체험")
        mapped_anchor = mapped_card.locator("a[href^='https://']")
        mapped_card.locator(".card-open").click()
        fixture_page.wait_for_function("document.querySelector('#detail[open]') !== null")
        mapped_detail_anchor = fixture_page.locator("#detail a[href^='https://']")
        mapped_detail_text = fixture_page.locator("#detail").inner_text()
        record("매핑된 사설 IPv4는 카드·상세 외부 앵커 금지",
               mapped_anchor.count() == 0 and mapped_detail_anchor.count() == 0
               and "연결 차단" in mapped_detail_text, mapped_detail_text)
        fixture_page.keyboard.press("Escape")
        fixture_payloads["events.json"][1]["link_url"] = "https://[64:ff9b::7f00:1]/"
        fixture_page.reload(wait_until="networkidle")
        fixture_page.wait_for_function("document.getElementById('count').textContent === '4'")
        nat64_card = fixture_page.locator("#list .card").filter(has_text="서울 유료 가족 체험")
        nat64_anchor = nat64_card.locator("a[href^='https://']")
        nat64_card.locator(".card-open").click()
        fixture_page.wait_for_function("document.querySelector('#detail[open]') !== null")
        nat64_detail_anchor = fixture_page.locator("#detail a[href^='https://']")
        record("NAT64 사설 목적지는 카드·상세 외부 앵커 금지",
               nat64_anchor.count() == 0 and nat64_detail_anchor.count() == 0,
               str(nat64_detail_anchor.count()))
        fixture_page.keyboard.press("Escape")

        activity_control = fixture_page.locator("#activity")
        activity_present = activity_control.count() == 1
        record("activity 필터 컨트롤 노출", activity_present)
        if activity_present:
            select_filter(fixture_page, "#activity", "과학·탐구")
            fixture_page.wait_for_function("document.getElementById('count').textContent === '2'")
            activity_names = fixture_page.locator("#list .card .card-title button").all_inner_texts()
            activity_qs = parse_qs(urlparse(fixture_page.url).query)
            activity_container_count = fixture_page.locator(".leaflet-container").count()
            activity_markers = fixture_page.locator(".leaflet-marker-icon").count()
            activity_ok = (activity_container_count == 1 and activity_markers == 2
                           and activity_names == [expected_name, expired_name]
                           and activity_qs.get("activity", [None])[0] == "과학·탐구")
            record("activity → 카드·지도·공유 URL 동기화", activity_ok,
                   json.dumps({"names": activity_names, "query": activity_qs,
                               "containers": activity_container_count, "markers": activity_markers}, ensure_ascii=False))
            ai_button = fixture_page.locator("#aiBtn")
            ai_button.focus()
            ai_button.press("Enter")
            fixture_page.wait_for_function("document.querySelector('#aiDialog[open]') !== null")
            activity_recs = fixture_page.locator("#aiDialog .recommendations li .rec-open").all_inner_texts()
            record("activity → 추천 범위 동기화", activity_recs == [expected_name],
                   json.dumps(activity_recs, ensure_ascii=False))
            recommendation = fixture_page.locator("#aiDialog .recommendations li .rec-open").first
            recommendation.focus()
            recommendation.press("Enter")
            fixture_page.wait_for_function("document.querySelector('#detail[open]') !== null && document.querySelector('#aiDialog[open]') === null")
            detail_focus = fixture_page.evaluate("""
            () => {
              const active = document.activeElement;
              const ai = document.querySelector('#aiDialog');
              return {detailOpen: Boolean(document.querySelector('#detail[open]')),
                      aiOpen: Boolean(document.querySelector('#aiDialog[open]')),
                      activeInsideHiddenAI: Boolean(ai && ai.contains(active))};
            }
            """)
            fixture_page.keyboard.press("Escape")
            fixture_page.wait_for_function("document.querySelector('#detail[open]') === null && document.activeElement === document.querySelector('#aiBtn')")
            ai_return = fixture_page.evaluate("""
            () => ({detailOpen: Boolean(document.querySelector('#detail[open]')),
                    aiOpen: Boolean(document.querySelector('#aiDialog[open]')),
                    activeId: document.activeElement?.id || '',
                    activeInsideHiddenAI: Boolean(document.querySelector('#aiDialog')?.contains(document.activeElement))})
            """)
            record("AI 버튼→추천→상세 키보드 경로·Escape aiBtn 초점 복귀",
                   detail_focus == {"detailOpen": True, "aiOpen": False, "activeInsideHiddenAI": False}
                   and ai_return == {"detailOpen": False, "aiOpen": False, "activeId": "aiBtn", "activeInsideHiddenAI": False},
                   json.dumps({"detail": detail_focus, "return": ai_return}, ensure_ascii=False))
            open_filter_disclosure(fixture_page)
            prepare_filter_action(fixture_page)
            fixture_page.click("#reset")
            fixture_page.wait_for_function("document.getElementById('count').textContent === '4'")
            record("activity → reset 복귀", fixture_page.input_value("#activity") in ("", "all"))

            def activity_chip(category, expected_count, expected_names):
                button = fixture_page.locator(f"#activityQuick button[data-activity='{category}']")
                names_before = fixture_page.locator("#list .card .card-title button").all_inner_texts()
                button.focus()
                button.press("Enter")
                keyboard_chip_focus = fixture_page.evaluate(
                    "el => document.activeElement === el && el.getAttribute('aria-pressed') === 'true'",
                    button.element_handle(),
                ) if button.count() == 1 else False
                record(f"{category} 칩 키보드 Enter 활성화·초점 유지", keyboard_chip_focus)
                fixture_page.wait_for_function(
                    "([selector, expected]) => Number(document.querySelector(selector).textContent) === expected",
                    arg=["#count", expected_count],
                )
                query = parse_qs(urlparse(fixture_page.url).query)
                names = fixture_page.locator("#list .card .card-title button").all_inner_texts()
                cards_match = (names == expected_names
                               and all(category in text for text in fixture_page.locator("#list .card").all_inner_texts()))
                active_button = fixture_page.locator(f"#activityQuick button[data-activity='{category}'][aria-pressed='true']")
                active_label = active_button.inner_text() if active_button.count() == 1 else ""
                active_count_label = active_button.locator("strong").inner_text() if active_button.count() == 1 else ""
                active_count_contrast = active_button.locator("strong").evaluate("""
                el => {
                  const rgb = value => (value.match(/\\d+(?:\\.\\d+)?/g) || []).slice(0, 3).map(Number);
                  const luminance = value => rgb(value).map(channel => {
                    const normalized = channel / 255;
                    return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
                  }).reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
                  const foreground = luminance(getComputedStyle(el).color);
                  const background = luminance(getComputedStyle(el.closest("button")).backgroundColor);
                  return (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05);
                }
                """) if active_button.count() == 1 else 0
                sync_ok = (active_button.count() == 1 and fixture_page.input_value("#activity") == category
                           and query.get("activity", [None])[0] == category
                           and active_button.get_attribute("aria-pressed") == "true"
                           and category in active_label
                           and active_count_label == f"{expected_count}건"
                           and active_count_contrast >= 4.5
                           and count(fixture_page) == expected_count and cards_match)
                record(f"{category} 칩 → select·URL·결과 동기화", sync_ok,
                       json.dumps({"before": names_before, "after": names, "query": query,
                                   "label": active_label, "countLabel": active_count_label,
                                   "contrast": active_count_contrast}, ensure_ascii=False))
                open_filter_disclosure(fixture_page)
                prepare_filter_action(fixture_page)
                fixture_page.click("#reset")
                fixture_page.wait_for_function("document.getElementById('count').textContent === '4'")

            chip_expectations = {
                "과학·탐구": (2, ["서울 10월 가족 체험", "서울 10월 지난 과학 체험"]),
                "만들기·예술": (1, ["서울 유료 가족 체험"]),
            }
            chip_render_ok = True
            chip_contrast_ok = True
            chip_details = {}
            for category, (expected_count, _) in chip_expectations.items():
                button = fixture_page.locator(f"#activityQuick button[data-activity='{category}']")
                button.scroll_into_view_if_needed()
                image = button.locator("img")
                image_loaded = False
                if image.count():
                    image_handle = image.element_handle()
                    try:
                        image_loaded = bool(fixture_page.wait_for_function(
                            "img => img.complete && img.naturalWidth > 0", arg=image_handle
                        ))
                    except PlaywrightTimeoutError:
                        image_loaded = False
                expected_asset = {"과학·탐구": "family-science-spot.webp", "만들기·예술": "family-craft-spot.webp"}[category]
                image_ok = (button.count() == 1 and image.count() == 1 and image_loaded
                            and button.get_attribute("aria-pressed") == "false"
                            and image.get_attribute("alt") == ""
                            and (image.get_attribute("src") or "").endswith(expected_asset))
                chip_render_ok = chip_render_ok and image_ok
                contrast = button.locator("strong").evaluate("""
                el => {
                  const rgb = value => (value.match(/\\d+(?:\\.\\d+)?/g) || []).slice(0, 3).map(Number);
                  const luminance = value => rgb(value).map(channel => {
                    const normalized = channel / 255;
                    return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
                  }).reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
                  const foreground = getComputedStyle(el).color;
                  const background = getComputedStyle(el.closest("button")).backgroundColor;
                  const light = Math.max(luminance(foreground), luminance(background));
                  const dark = Math.min(luminance(foreground), luminance(background));
                  return {color: foreground, background, ratio: (light + 0.05) / (dark + 0.05)};
                }
                """)
                chip_contrast_ok = chip_contrast_ok and contrast["ratio"] >= 4.5
                chip_details[category] = {"button": button.count(), "images": image.count(),
                                          "loaded": image_loaded, "contrast": contrast,
                                          "alt": image.get_attribute("alt") if image.count() else None,
                                          "src": image.get_attribute("src") if image.count() else None}
            text_only = fixture_page.locator("#activityQuick button[data-activity='신체·놀이']")
            text_only_image = text_only.locator("img")
            text_only_ok = (text_only.count() == 1 and text_only_image.count() == 0
                            and "신체·놀이" in text_only.inner_text() and "1건" in text_only.inner_text())
            record("과학·만들기 칩 장식 이미지·접근성 이름", chip_render_ok,
                   json.dumps(chip_details, ensure_ascii=False))
            record("과학·만들기 미선택 count 대비 4.5 이상", chip_contrast_ok,
                   json.dumps({category: details["contrast"] for category, details in chip_details.items()}, ensure_ascii=False))
            record("출처 기반 무그림 범주는 텍스트 칩 유지", text_only_ok,
                   text_only.inner_text() if text_only.count() else "missing")
            for category, (expected_count, expected_names) in chip_expectations.items():
                activity_chip(category, expected_count, expected_names)
        source_status = fixture_page.locator("#sourceStatus")
        source_text = source_status.inner_text() if source_status.count() else ""
        record("수집 mode/failure 상태 노출", source_status.count() == 1
               and "수집 모드" in source_text and "오류" in source_text, source_text)
        expired_card = fixture_page.locator("#list .card").filter(has_text=expired_name)
        status_badges = expired_card.locator(".badge.closed, .badge.ended")
        status_badge_details = status_badges.evaluate_all("""
        els => {
          const rgb = value => (value.match(/\\d+(?:\\.\\d+)?/g) || []).slice(0, 3).map(Number);
          const luminance = value => rgb(value).map(channel => {
            const normalized = channel / 255;
            return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
          }).reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
          return els.map(el => {
            const style = getComputedStyle(el);
            const foreground = style.color;
            const background = style.backgroundColor;
            const light = Math.max(luminance(foreground), luminance(background));
            const dark = Math.min(luminance(foreground), luminance(background));
            return {text: el.textContent.trim(), color: foreground, background,
                    ratio: (light + 0.05) / (dark + 0.05)};
          });
        }
        """)
        status_badge_contrast_ok = bool(status_badge_details) and all(
            detail["text"] and detail["ratio"] >= 4.5 for detail in status_badge_details
        )
        record("종료·마감 배지 렌더 대비 4.5 이상", status_badge_contrast_ok,
               json.dumps(status_badge_details, ensure_ascii=False))
        # The activity reset clears all filters; restore the intended Seoul/free scope
        # before asserting the one active event inside the KST date window.
        select_filter(fixture_page, "#sido", "서울특별시")
        prepare_filter_action(fixture_page)
        fixture_page.check("#freeOnly")
        prepare_filter_action(fixture_page)
        fixture_page.fill("#from", "2026-10-15")
        prepare_filter_action(fixture_page)
        fixture_page.fill("#to", "2026-10-16")
        fixture_page.wait_for_function("document.getElementById('count').textContent === '1'")
        boundary_names = fixture_page.locator("#list .card .card-title button").all_inner_texts()
        boundary_container_count = fixture_page.locator(".leaflet-container").count()
        boundary_markers = fixture_page.locator(".leaflet-marker-icon").count()
        boundary_map_ok = boundary_container_count == 1 and boundary_markers == 1
        record("KST 날짜 경계 → 10월 15일 행사만 카드", boundary_names == [expected_name],
               json.dumps(boundary_names, ensure_ascii=False))
        record("KST 날짜 경계 → 지도 1건", boundary_map_ok,
               f"containers={boundary_container_count} markers={boundary_markers}")
        fixture_page.close()
        fixture_payloads["facets.json"]["age_band"] = {}
        missing_age_page = _new_page(context, 1280, 900)
        _install_tile_fixture(missing_age_page)
        missing_age_page.route("**/data/*.json", fulfill_fixture)
        missing_age_page.goto(base, wait_until="networkidle")
        missing_age_page.wait_for_function("document.getElementById('count').textContent !== ''")
        open_filter_disclosure(missing_age_page)
        missing_age_option = missing_age_page.locator("#age option[value='어린이']")
        missing_age_text = (missing_age_option.text_content() or "").strip()
        expected_age_count = sum("어린이" in set(event.get("age_bands") or []) for event in fixture_events)
        record("라우트 fixture facets 누락 age count", missing_age_text == f"어린이 ({expected_age_count})",
               json.dumps({"label": missing_age_text, "expected": expected_age_count,
                           "fixtureEvents": len(fixture_events)}, ensure_ascii=False))
        missing_age_page.close()

        # Keyboard detail open/close must put focus back on the invoking card.
        first_card = page2.locator("#list .card-open").first
        first_card.focus()
        first_card.press("Enter")
        page2.wait_for_function("document.querySelector('#detail[open]') !== null")
        close_focused = page2.locator("#detail .close").evaluate("el => document.activeElement === el")
        page2.keyboard.press("Escape")
        page2.wait_for_function("document.querySelector('#detail[open]') === null")
        focus_restored = page2.locator("#list .card-open").first.evaluate("el => document.activeElement === el")
        record("키보드 상세 열기 → 닫기 포커스", close_focused and focus_restored)

        # Map-first mobile shell: the new contract starts with a collapsed right drawer.
        page2.set_viewport_size({"width": 360, "height": 800})
        page2.goto(base, wait_until="networkidle")
        page2.wait_for_function("document.getElementById('count').textContent !== ''")
        open_filter_disclosure(page2)
        page2.wait_for_function("document.querySelector('#map.leaflet-container, #map .leaflet-container')")
        mobile_shell = page2.evaluate("""
        () => {
          const rect = selector => { const el = document.querySelector(selector); if (!el) return null; const r = el.getBoundingClientRect(); return {left:r.left, top:r.top, right:r.right, bottom:r.bottom, width:r.width, height:r.height}; };
          const scrolling = document.scrollingElement;
          return {map: rect('#map'), panel: rect('#resultsPanel') || rect('.results'), search: rect('#searchDock') || rect('.search-dock'),
                  width: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth,
                  height: scrolling.scrollHeight, clientHeight: scrolling.clientHeight,
                  collapsed: document.querySelector('#resultsPanel')?.dataset.collapsed,
                  expanded: document.querySelector('#resultsToggle')?.getAttribute('aria-expanded')};
        }
        """)
        map_box = mobile_shell.get("map")
        mobile_map_ok = bool(map_box and abs(map_box["left"]) <= 1 and abs(map_box["top"]) <= 1
                             and abs(map_box["right"] - 360) <= 1 and abs(map_box["bottom"] - 800) <= 1)
        record("360px 지도 배경이 뷰포트를 채움", mobile_map_ok, json.dumps(mobile_shell, ensure_ascii=False))
        mobile_panel = mobile_shell.get("panel")
        mobile_panel_ok = bool(mobile_panel and mobile_panel["left"] >= 56
                               and mobile_panel["right"] <= 361
                               and mobile_shell.get("collapsed") == "true"
                               and mobile_shell.get("expanded") == "false")
        record("360px 우측 결과 drawer 기본 접힘·지도 56px", mobile_panel_ok, json.dumps(mobile_shell, ensure_ascii=False))
        record("360px 문서 스크롤·가로 넘침 없음", mobile_shell["width"] <= 361
               and mobile_shell["height"] <= mobile_shell["clientHeight"] + 1, json.dumps(mobile_shell, ensure_ascii=False))
        mobile_shot = EVID / "ui-mobile-family.png"
        page2.screenshot(path=str(mobile_shot), full_page=False)
        record("360px 가족 기본 스냅샷", mobile_shot.exists(), str(mobile_shot.relative_to(ROOT)))
        filter_target_boxes = page2.locator("#filterToggle, #resultsToggle, #reset, #aiBtn, #activityQuick .region-chip, #regionQuick .region-chip, #subFacets .subfacet-chip").evaluate_all(
            "els => els.filter(el => !el.hidden && getComputedStyle(el).display !== 'none').map(el => { const r = el.getBoundingClientRect(); return {selector: el.className || el.id, width: r.width, height: r.height}; })"
        )
        filter_targets_ok = bool(filter_target_boxes) and all(box["width"] >= 44 and box["height"] >= 44 for box in filter_target_boxes)
        page2.locator("#filterToggle").press("Escape")
        page2.wait_for_function("document.querySelector('#filterMenu')?.open === false")
        page2.locator("#resultsToggle").click()
        page2.wait_for_function("document.querySelector('#resultsPanel')?.dataset.collapsed === 'false'")
        page2.locator("#resultsScroll .card-actions .button").first.scroll_into_view_if_needed()
        result_action_boxes = page2.locator("#resultsScroll .card-actions .button").evaluate_all(
            "els => els.map(el => { const r = el.getBoundingClientRect(); return {selector: el.className || el.id, width: r.width, height: r.height}; })"
        )
        result_actions_ok = bool(result_action_boxes) and all(box["width"] >= 44 and box["height"] >= 44 for box in result_action_boxes)
        targets_ok = filter_targets_ok and result_actions_ok
        record("360px 주요 터치 타깃 44px", targets_ok,
               json.dumps({"filterTargets": filter_target_boxes, "resultActions": result_action_boxes,
                           "filterTargetsOk": filter_targets_ok, "resultActionsOk": result_actions_ok}, ensure_ascii=False))
        chip_page = _new_page(context, 360, 780)
        _install_tile_fixture(chip_page)
        chip_page.route("**/data/*.json", fulfill_fixture)
        chip_page.goto(base, wait_until="networkidle")
        chip_page.wait_for_function("document.getElementById('count').textContent !== ''")
        chip_target_boxes = chip_page.locator("#activityQuick .region-chip").evaluate_all(
            "els => els.map(el => { const r = el.getBoundingClientRect(); return {width: r.width, height: r.height}; })"
        )
        record("360px 활동 칩 터치 타깃 44px", bool(chip_target_boxes)
               and all(box["width"] >= 44 and box["height"] >= 44 for box in chip_target_boxes),
               json.dumps(chip_target_boxes, ensure_ascii=False))
        chip_page.close()
        reset_scroll(page)

        shot_desktop = EVID / "ui-desktop-filtered.png"
        page.bring_to_front()
        page.screenshot(path=str(shot_desktop))
        record("데스크톱 필터 스냅샷", shot_desktop.exists(), str(shot_desktop.relative_to(ROOT)))

        context.close()
        browser.close()

    passed = sum(c["ok"] for c in checks)
    summary = {"status": "passed" if passed == len(checks) else "failed",
               "total_checks": len(checks), "passed": passed,
               "data_context": "deterministic routed fixture (fixed KST 2026-10-15), not production events.json",
               "viewports": ["1440x900", "1280x800", "360x800", "390x844"],
               **_run_metadata(), "checks": checks}
    _write_ui_evidence(summary)
    return 0 if summary["status"] == "passed" else 1


def main() -> int:
    checks: list[dict] = []
    metadata = _run_metadata()
    _write_ui_evidence({"status": "running", "total_checks": 0, "passed": 0,
                        "data_context": "deterministic routed fixture (fixed KST 2026-10-15), not production events.json",
                        "viewports": ["1440x900", "1280x800", "360x800", "390x844"],
                        **metadata, "checks": []})
    try:
        return _run_ui_check(checks)
    except BaseException as error:
        passed = sum(item["ok"] for item in checks)
        summary = {"status": "error", "error": f"{type(error).__name__}: {error}",
                   "partial_results": {"total_checks": len(checks), "passed": passed},
                   "total_checks": len(checks), "passed": passed,
                   "data_context": "deterministic routed fixture (fixed KST 2026-10-15), not production events.json",
                   "viewports": ["1440x900", "1280x800", "360x800", "390x844"],
                   **metadata, "checks": checks}
        _write_ui_evidence(summary)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
