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


def _install_data_fixture(page, payloads: dict) -> None:
    def fulfill(route):
        filename = urlparse(route.request.url).path.rsplit("/", 1)[-1]
        payload = payloads.get(filename)
        if payload is None:
            route.continue_()
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(payload, ensure_ascii=False))

    page.route("**/data/*.json", fulfill)


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
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

    EVID.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []

    def record(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    def count(page) -> int:
        return int(page.locator("#count").inner_text())
    def reset_scroll(page) -> None:
        page.evaluate("() => { window.scrollTo(0, 0); if (document.activeElement) document.activeElement.blur(); }")
        page.wait_for_function("window.scrollY === 0")
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
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        initial_payloads = _initial_ui_fixture_payloads()
        _install_data_fixture(page, initial_payloads)
        page.goto(base, wait_until="networkidle")
        page.wait_for_function("document.getElementById('count').textContent !== ''")

        total = count(page)
        record("초기 로드 + 가족 기본 카운트", total > 0, f"활성 가족 행사 {total}건")
        desktop_default = EVID / "ui-desktop-family.png"
        warm_map(page)
        reset_scroll(page)
        page.screenshot(path=str(desktop_default), full_page=True)
        record("데스크톱 가족 기본 스냅샷", desktop_default.exists(), str(desktop_default.relative_to(ROOT)))
        hero_page = browser.new_page(viewport={"width": 1440, "height": 900})
        _install_data_fixture(hero_page, initial_payloads)
        hero_page.goto(base, wait_until="networkidle")
        hero_page.wait_for_function("document.getElementById('count').textContent !== ''")
        hero_page.wait_for_function(
            "() => { const i = document.querySelector('.hero-art img'); return i && i.complete && i.naturalWidth > 0; }"
        )
        hero_img = hero_page.locator(".hero-art img")
        hero_box = hero_img.bounding_box()
        hero_src = hero_img.evaluate("img => img.currentSrc")
        hero_text = hero_page.locator(".hero-art").inner_text()
        hero_ok = (hero_src.endswith("family-outing.webp")
                   and hero_img.evaluate("img => img.naturalWidth > 0")
                   and hero_box is not None and hero_box["height"] <= 180)
        record("1440px 히어로 canonical 경로·렌더링·높이", hero_ok,
               json.dumps({"src": hero_src, "box": hero_box}, ensure_ascii=False))
        record("히어로 아트 DOM UI 텍스트 없음", hero_text.strip() == "", hero_text)
        filters_box = hero_page.locator("#filters").bounding_box()
        activity_box = hero_page.locator(".quick-experiences").bounding_box()
        record("필터가 활동 바로가기보다 먼저", bool(filters_box and activity_box and filters_box["y"] < activity_box["y"]),
               json.dumps({"filters": filters_box, "activity": activity_box}, ensure_ascii=False))
        hero_page.close()
        sample_card = page.locator("#list .card").filter(has_text="서울 어린이 여름 음악축제")
        sample_links = sample_card.locator("a[href^='http']")
        sample_hrefs = sample_links.evaluate_all("els => els.map(el => el.href)")
        record("출처 링크는 검증 증거 없이는 외부 앵커 금지", sample_links.count() == 0, json.dumps(sample_hrefs, ensure_ascii=False))
        static_page = browser.new_page(viewport={"width": 1280, "height": 900})
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
        static_external = static_page.locator("a[href^='https://']")
        static_title = static_page.locator("h1").inner_text()
        static_status = static_page.locator("[data-link-status]").get_attribute("data-link-status")
        record("정적 fixture 응답·의미 있는 제목", static_response is not None
               and static_response.status == 200 and static_title == "통제된 출처 없는 정적 행사", static_title)
        record("정적 상세도 fresh proof 없는 외부 앵커 금지", static_external.count() == 0
               and static_status == "blocked", json.dumps({"status": static_status, "external": static_external.evaluate_all("els => els.map(el => el.href)" )}))
        static_page.close()

        # 5축 필터 조작: 키워드 / 지역 / 테마 / 나이대 / 기간 + 신청가능
        page.fill("#q", "축제")
        page.select_option("#sido", "서울특별시")
        page.select_option("#theme", "축제")
        page.select_option("#age", "어린이")
        page.fill("#from", "2026-01-01")
        page.fill("#to", "2026-12-31")
        page.check("#applyable")
        page.wait_for_timeout(200)
        filtered = count(page)

        qs = parse_qs(urlparse(page.url).query)
        want = {"q": "축제", "sido": "서울특별시", "theme": "축제", "age": "어린이",
                "from": "2026-01-01", "to": "2026-12-31", "applyable": "1"}
        url_ok = all(qs.get(k, [None])[0] == v for k, v in want.items())
        record("5축 필터 → URL 동기화", url_ok, f"{urlparse(page.url).query}")
        record("필터 적용 결과 카운트", filtered <= total, f"{filtered}/{total}")

        shared_url = page.url

        # 새로고침/공유 링크 복원: 동일 URL 신규 페이지 로드 → 입력값·카운트 복원
        page2 = browser.new_page(viewport={"width": 1280, "height": 900})
        _install_data_fixture(page2, initial_payloads)
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
        recount = count(page2)
        record("URL 복원(새 세션 입력값)", restore_ok, json.dumps(restored, ensure_ascii=False))
        record("URL 복원(카운트 일치)", recount == filtered, f"{recount}=={filtered}")

        stale_page = browser.new_page(viewport={"width": 1280, "height": 900})
        _install_data_fixture(stale_page, initial_payloads)
        stale_url = (base + "?sido=%EC%97%86%EB%8A%94%EC%A7%80%EC%97%AD"
                     "&theme=%EC%97%86%EB%8A%94%ED%85%8C%EB%A7%88&age=%EC%97%86%EB%8A%94%EB%82%98%EC%9D%B4&kids=0")
        stale_page.goto(stale_url, wait_until="networkidle")
        stale_page.wait_for_function("document.getElementById('count').textContent !== ''")
        stale_values = [stale_page.input_value(selector) for selector in ("#sido", "#theme", "#age")]
        stale_labels = [stale_page.locator(f"{selector} option:checked").inner_text()
                        for selector in ("#sido", "#theme", "#age")]
        stale_ok = (stale_values == ["없는지역", "없는테마", "없는나이"]
                    and all("현재 목록 없음" in label for label in stale_labels)
                    and count(stale_page) == 0)
        record("오래된 URL 필터값 보존·무결과", stale_ok,
               json.dumps({"values": stale_values, "labels": stale_labels, "count": count(stale_page)}, ensure_ascii=False))
        stale_page.close()
        # Preserve the legacy mobile evidence for the 5-axis state.
        page2.set_viewport_size({"width": 360, "height": 780})
        page2.wait_for_timeout(200)
        shot360 = EVID / "ui-360px.png"
        warm_map(page2)
        reset_scroll(page2)
        page2.screenshot(path=str(shot360), full_page=True)
        record("360px 모바일 스냅샷", shot360.exists(), str(shot360.relative_to(ROOT)))

        # Reset must return to the family default, not to the all-events scope.
        page2.set_viewport_size({"width": 1280, "height": 900})
        page2.click("#reset")
        page2.wait_for_function("([selector, previous]) => Number(document.querySelector(selector).textContent) === previous",
                                arg=["#count", total])
        reset_count = count(page2)
        record("reset → 전체 복귀", reset_count == total, f"{reset_count}=={total}")
        record("reset → 가족/무료 토글 초기화", page2.is_checked("#kids") and not page2.is_checked("#freeOnly"))

        # free_only must remove paid/unknown-price rows and synchronize its URL state.
        free_before = count(page2)
        page2.check("#freeOnly")
        free_count = count(page2)
        free_cards = page2.locator("#list .card")
        all_free = all(card.locator(".card-price").inner_text() == "무료" for card in free_cards.all())
        free_qs = parse_qs(urlparse(page2.url).query)
        record("무료만 토글 → 실제 필터링", free_count < free_before and all_free, f"{free_count}/{free_before}")
        record("무료만 → URL 동기화", free_qs.get("free_only", [None])[0] == "1", urlparse(page2.url).query)

        # Combined family + region + free state: count, cards, map markers, and recommendations agree.
        page2.select_option("#sido", "서울특별시")
        combined_count = count(page2)
        combined_cards = page2.locator("#list .card")
        card_names = set(combined_cards.locator(".card-title button").all_inner_texts())
        cards_consistent = combined_count == combined_cards.count() and all(
            card.locator(".card-location").inner_text().startswith("서울특별시")
            and card.locator(".card-price").inner_text() == "무료"
            for card in combined_cards.all()
        )
        map_ready = page2.locator(".leaflet-container").count() == 1
        marker_count = page2.locator(".leaflet-marker-icon").count()
        map_consistent = marker_count == combined_count if map_ready else page2.locator("#mapFallback").is_visible()
        record("가족·서울·무료 → 카드/카운트 일치", cards_consistent, f"{combined_count} cards={combined_cards.count()}")
        record("가족·서울·무료 → 지도 일치", map_consistent, f"markers={marker_count} cards={combined_count}")
        page2.click("#aiBtn")
        page2.wait_for_function("document.querySelector('#aiDialog[open]') !== null")
        rec_names = page2.locator("#aiDialog .recommendations li .rec-open").all_inner_texts()
        rec_text = page2.locator("#aiDialog").inner_text()
        recommendations_consistent = all(name in card_names for name in rec_names) and len(rec_names) <= combined_count
        if not rec_names:
            recommendations_consistent = recommendations_consistent and "추천할 진행 중 행사가 없습니다" in rec_text
        record("가족·서울·무료 → 추천 범위 일치", recommendations_consistent, rec_text)
        page2.locator("#aiDialog .close").press("Enter")

        # A no-result query exposes the empty state and reset recovers the family default.
        page2.fill("#q", "이 조건은 반드시 결과가 없어야 합니다")
        page2.wait_for_function("document.getElementById('count').textContent === '0'")
        no_results_ok = page2.locator("#empty").is_visible() and count(page2) == 0
        record("무결과 상태 표시", no_results_ok, page2.locator("#empty").inner_text())
        page2.click("#reset")
        page2.wait_for_function("([selector, previous]) => Number(document.querySelector(selector).textContent) === previous",
                                arg=["#count", total])
        record("무결과 → reset 복구", count(page2) == total and not page2.locator("#empty").is_visible())

        # kids=0 is shareable and must restore the broader, non-family scope.
        kids_page = browser.new_page(viewport={"width": 1280, "height": 900})
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
        })
        fixture_events[1].update({
            "is_experience": True, "experience_categories": ["만들기·예술", "신체·놀이"],
            "experience_summary": "직접 만들고 놀며 참여하는 공예 체험",
            "link_status": "verified", "link_checked_at": "2026-10-14T15:00:00+00:00",
            "link_url": "https://[::ffff:127.0.0.1]/", "link_reason": None,
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

        fixture_page = browser.new_page(viewport={"width": 1280, "height": 900})
        fixture_page.add_init_script("""
        (() => {
          const NativeDate = Date;
          const fixed = NativeDate.parse("2026-10-14T16:00:00Z");
          class FixedDate extends NativeDate {
            constructor(...args) { super(args.length ? args[0] : fixed); }
            static now() { return fixed; }
          }
          FixedDate.parse = NativeDate.parse;
          FixedDate.UTC = NativeDate.UTC;
          window.Date = FixedDate;
        })();
        """)
        fixture_page.route("**/data/*.json", fulfill_fixture)
        fixture_url = base + "?sido=%EC%84%9C%EC%9A%B8%ED%8A%B9%EB%B3%84%EC%8B%9C&free_only=1"
        fixture_page.goto(fixture_url, wait_until="networkidle")
        fixture_page.wait_for_function("document.getElementById('count').textContent !== ''")
        supplied_age_label = fixture_page.locator("#age option[value='어린이']").inner_text()
        record("라우트 fixture age count 중복·facet 오염 방지", supplied_age_label == "어린이 (4)", supplied_age_label)
        fixture_cards = fixture_page.locator("#list .card")
        fixture_names = fixture_cards.locator(".card-title button").all_inner_texts()
        expected_name = "서울 10월 가족 체험"
        expired_name = "서울 10월 지난 과학 체험"
        excluded_names = {"서울 유료 가족 체험", "부산 무료 가족 체험", "서울 성인 무료 강연"}
        fixture_cards_ok = (count(fixture_page) == 2
                            and set(fixture_names) == {expected_name, expired_name}
                            and not excluded_names.intersection(fixture_names))
        fixture_marker_count = fixture_page.locator(".leaflet-marker-icon").count()
        fixture_map_ok = (fixture_marker_count == 2
                          if fixture_page.locator(".leaflet-container").count() == 1
                          else fixture_page.locator("#mapFallback").is_visible())
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
        record("결정적 가족 결과 지도 2건", fixture_map_ok, f"markers={fixture_marker_count}")
        record("결정적 활성 행사만 추천", fixture_recommendation_ok,
               json.dumps(fixture_recs, ensure_ascii=False))
        fixture_page.locator("#aiDialog .close").press("Enter")
        verified_card = fixture_page.locator("#list .card").filter(has_text=expected_name)
        verified_anchor = verified_card.locator("a[href^='https://']")
        verified_href = verified_anchor.get_attribute("href") if verified_anchor.count() else ""
        verified_rel = verified_anchor.get_attribute("rel") if verified_anchor.count() else ""
        record("신선한 verified 출처만 외부 clickout", verified_anchor.count() == 1
               and verified_href == "https://www.busan.go.kr/active"
               and "noopener" in (verified_rel or ""), verified_href)

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
        fixture_page.click("#reset")
        fixture_page.wait_for_function("document.getElementById('count').textContent === '4'")
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
            activity_control.select_option("과학·탐구")
            fixture_page.wait_for_function("document.getElementById('count').textContent === '2'")
            activity_names = fixture_page.locator("#list .card .card-title button").all_inner_texts()
            activity_qs = parse_qs(urlparse(fixture_page.url).query)
            activity_markers = fixture_page.locator(".leaflet-marker-icon").count()
            activity_ok = (activity_names == [expected_name, expired_name]
                           and activity_qs.get("activity", [None])[0] == "과학·탐구"
                           and (activity_markers == 2 or fixture_page.locator("#mapFallback").is_visible()))
            record("activity → 카드·지도·공유 URL 동기화", activity_ok,
                   json.dumps({"names": activity_names, "query": activity_qs}, ensure_ascii=False))
            fixture_page.click("#aiBtn")
            fixture_page.wait_for_function("document.querySelector('#aiDialog[open]') !== null")
            activity_recs = fixture_page.locator("#aiDialog .recommendations li .rec-open").all_inner_texts()
            record("activity → 추천 범위 동기화", activity_recs == [expected_name],
                   json.dumps(activity_recs, ensure_ascii=False))
            fixture_page.locator("#aiDialog .close").press("Enter")
            fixture_page.click("#reset")
            fixture_page.wait_for_function("document.getElementById('count').textContent === '4'")
            record("activity → reset 복귀", fixture_page.input_value("#activity") in ("", "all"))

            def activity_chip(category, expected_count, expected_names):
                button = fixture_page.get_by_role("button", name=re.compile(rf"^{re.escape(category)}\s+\d+건$"))
                names_before = fixture_page.locator("#list .card .card-title button").all_inner_texts()
                button.click()
                fixture_page.wait_for_function(
                    "([selector, expected]) => Number(document.querySelector(selector).textContent) === expected",
                    arg=["#count", expected_count],
                )
                query = parse_qs(urlparse(fixture_page.url).query)
                names = fixture_page.locator("#list .card .card-title button").all_inner_texts()
                cards_match = (names == expected_names
                               and all(category in text for text in fixture_page.locator("#list .card").all_inner_texts()))
                count_color = button.locator("strong").evaluate("el => getComputedStyle(el).color") if button.count() else ""
                sync_ok = (button.count() == 1 and fixture_page.input_value("#activity") == category
                           and query.get("activity", [None])[0] == category
                           and button.get_attribute("aria-pressed") == "true"
                           and count_color == "rgb(255, 255, 255)"
                           and count(fixture_page) == expected_count and cards_match)
                record(f"{category} 칩 → select·URL·결과 동기화", sync_ok,
                       json.dumps({"before": names_before, "after": names, "query": query}, ensure_ascii=False))
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
                button = fixture_page.get_by_role("button", name=re.compile(rf"^{re.escape(category)}\s+{expected_count}건$"))
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
            text_only = fixture_page.get_by_role("button", name=re.compile(r"^신체·놀이\s+1건$"))
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
        fixture_page.select_option("#sido", "서울특별시")
        fixture_page.check("#freeOnly")
        fixture_page.wait_for_function("document.getElementById('count').textContent === '2'")
        fixture_page.fill("#from", "2026-10-15")
        fixture_page.fill("#to", "2026-10-16")
        fixture_page.wait_for_function("document.getElementById('count').textContent === '1'")
        boundary_names = fixture_page.locator("#list .card .card-title button").all_inner_texts()
        boundary_markers = fixture_page.locator(".leaflet-marker-icon").count()
        boundary_map_ok = (boundary_markers == 1
                           if fixture_page.locator(".leaflet-container").count() == 1
                           else fixture_page.locator("#mapFallback").is_visible())
        record("KST 날짜 경계 → 10월 15일 행사만 카드", boundary_names == [expected_name],
               json.dumps(boundary_names, ensure_ascii=False))
        record("KST 날짜 경계 → 지도 1건", boundary_map_ok, f"markers={boundary_markers}")
        fixture_page.close()
        fixture_payloads["facets.json"]["age_band"] = {}
        missing_age_page = browser.new_page(viewport={"width": 1280, "height": 900})
        missing_age_page.add_init_script("""
        (() => {
          const NativeDate = Date;
          const fixed = NativeDate.parse("2026-10-14T16:00:00Z");
          class FixedDate extends NativeDate {
            constructor(...args) { super(args.length ? args[0] : fixed); }
            static now() { return fixed; }
          }
          FixedDate.parse = NativeDate.parse;
          FixedDate.UTC = NativeDate.UTC;
          window.Date = FixedDate;
        })();
        """)
        missing_age_page.route("**/data/*.json", fulfill_fixture)
        missing_age_page.goto(base, wait_until="networkidle")
        missing_age_page.wait_for_function("document.getElementById('count').textContent !== ''")
        missing_age_label = missing_age_page.locator("#age option[value='어린이']").inner_text()
        record("라우트 fixture facets 누락 age count", missing_age_label == "어린이 (4)", missing_age_label)
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

        # Mobile family evidence and measurable hit-area/overflow checks.
        page2.set_viewport_size({"width": 360, "height": 780})
        page2.goto(base, wait_until="networkidle")
        page2.wait_for_function("document.getElementById('count').textContent !== ''")
        warm_map(page2)
        page2.wait_for_function("() => { const i = document.querySelector('.hero-art img'); return i && i.complete && i.naturalWidth > 0; }")
        reset_scroll(page2)
        marker_visibility = page2.evaluate("""
        () => {
          const map = document.querySelector('#map')?.getBoundingClientRect();
          const markers = Array.from(document.querySelectorAll('#map .leaflet-marker-icon')).map(el => {
            const r = el.getBoundingClientRect();
            return {left: r.left, top: r.top, right: r.right, bottom: r.bottom};
          });
          return {map: map && {left: map.left, top: map.top, right: map.right, bottom: map.bottom}, markers};
        }
        """)
        map_box = marker_visibility.get("map")
        marker_targets_in_map = bool(map_box and marker_visibility["markers"])
        if marker_targets_in_map:
            marker_targets_in_map = all(
                box["left"] >= map_box["left"] and box["right"] <= map_box["right"]
                and box["top"] >= map_box["top"] and box["bottom"] <= map_box["bottom"]
                for box in marker_visibility["markers"]
            )
        record("360px 리사이즈 후 지도 마커 가시영역", marker_targets_in_map,
               json.dumps(marker_visibility, ensure_ascii=False))
        mobile_shot = EVID / "ui-mobile-family.png"
        page2.screenshot(path=str(mobile_shot), full_page=True)
        record("360px 가족 기본 스냅샷", mobile_shot.exists(), str(mobile_shot.relative_to(ROOT)))
        no_overflow = page2.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        record("360px 가로 오버플로 없음", no_overflow,
               page2.evaluate("() => `${document.documentElement.scrollWidth}/${document.documentElement.clientWidth}`"))
        result_box = page2.locator(".results").bounding_box()
        map_box = page2.locator(".map-panel").bounding_box()
        record("360px 결과가 지도보다 먼저 표시", bool(result_box and map_box and result_box["y"] < map_box["y"]),
               json.dumps({"results": result_box, "map": map_box}, ensure_ascii=False))
        target_boxes = page2.locator(".check-pill, #reset, #aiBtn, .region-chip, #list .card-actions .button").evaluate_all(
            "els => els.map(el => { const r = el.getBoundingClientRect(); return {selector: el.className || el.id, width: r.width, height: r.height}; })"
        )
        targets_ok = bool(target_boxes) and all(box["width"] >= 44 and box["height"] >= 44 for box in target_boxes)
        record("360px 주요 터치 타깃 44px", targets_ok, json.dumps(target_boxes, ensure_ascii=False))

        chip_page = browser.new_page(viewport={"width": 360, "height": 780})
        chip_page.add_init_script("""
        (() => {
          const NativeDate = Date;
          const fixed = NativeDate.parse("2026-10-14T16:00:00Z");
          class FixedDate extends NativeDate {
            constructor(...args) { super(args.length ? args[0] : fixed); }
            static now() { return fixed; }
          }
          FixedDate.parse = NativeDate.parse;
          FixedDate.UTC = NativeDate.UTC;
          window.Date = FixedDate;
        })();
        """)
        chip_page.route("**/data/*.json", fulfill_fixture)
        chip_page.goto(base, wait_until="networkidle")
        chip_page.wait_for_function("document.getElementById('count').textContent !== ''")
        chip_target_boxes = chip_page.locator(".quick-experiences .region-chip").evaluate_all(
            "els => els.map(el => { const r = el.getBoundingClientRect(); return {width: r.width, height: r.height}; })"
        )
        record("360px 활동 칩 터치 타깃 44px", bool(chip_target_boxes)
               and all(box["width"] >= 44 and box["height"] >= 44 for box in chip_target_boxes),
               json.dumps(chip_target_boxes, ensure_ascii=False))
        chip_page.close()
        marker_boxes = page2.locator(".leaflet-marker-icon").evaluate_all(
            "els => els.map(el => { const r = el.getBoundingClientRect(); return {width: r.width, height: r.height}; })"
        )
        markers_ok = all(box["width"] >= 20 and box["height"] >= 20 for box in marker_boxes)
        record("지도 마커 실용적 터치 크기", markers_ok if marker_boxes else page2.locator("#mapFallback").is_visible(),
               json.dumps(marker_boxes, ensure_ascii=False))
        mobile_src = page2.locator(".hero-art img").evaluate("img => img.currentSrc")
        record(
            "모바일 WebP 이미지 로드",
            mobile_src.endswith("family-outing-mobile.webp") and page2.locator(".hero-art img").evaluate("img => img.naturalWidth > 0"),
            mobile_src,
        )
        reset_scroll(page)

        shot_desktop = EVID / "ui-desktop-filtered.png"
        page.bring_to_front()
        page.screenshot(path=str(shot_desktop))
        record("데스크톱 필터 스냅샷", shot_desktop.exists(), str(shot_desktop.relative_to(ROOT)))

        browser.close()

    summary = {"total_checks": len(checks), "passed": sum(c["ok"] for c in checks),
               "data_context": "deterministic routed fixture (fixed KST 2026-10-15), not production events.json",
               "checks": checks}
    (EVID / "ui-check.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] == summary["total_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
