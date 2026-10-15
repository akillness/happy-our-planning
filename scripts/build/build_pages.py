"""지식 DB(또는 events.json) → 행사별 정적 상세 HTML 빌드 (S2-T1 · B2).

리스트/지도에서 진입하는 SEO 친화 상세 페이지를 빌드타임에 사전 생성한다.
각 페이지에 schema.org/Event JSON-LD를 임베드해 검색엔진 리치결과를 노린다.

flat-file SSOT 불변: 이 HTML은 events에서 파생(언제든 재생성). 입력이 같으면
출력은 바이트 동일(멱등) — 타임스탬프 등 휘발성 값을 페이지에 넣지 않는다.

산출: web/public/events/<safe-id>.html

사용:
    python -m scripts.build.build_pages
"""
from __future__ import annotations

import html
import json
import math
import re
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

from scripts.build.build_index import derive_status
from scripts.common import links
from scripts.common.config import ROOT
from scripts.common.okf import _safe_id

OUT_DIR = ROOT / "web" / "public" / "events"
EVENTS_JSON = ROOT / "web" / "public" / "data" / "events.json"
_KST = timezone(timedelta(hours=9))

# schema.org/Event status URL 매핑(OKF status enum → schema.org EventStatusType).
_STATUS_URL = {
    "Open": "https://schema.org/EventScheduled",
    "Scheduled": "https://schema.org/EventScheduled",
    "Closed": "https://schema.org/EventCancelled",
    "Postponed": "https://schema.org/EventPostponed",
}


_DURATION_PATTERN = re.compile(r"^PT(?=\d)(?:\d+H)?(?:\d+M)?(?:\d+S)?$")


def _is_free(price: object) -> bool:
    return not isinstance(price, bool) and price in ("free", 0, 0.0)


def _valid_price(price: object) -> bool:
    if isinstance(price, bool):
        return False
    if price == "free":
        return True
    if isinstance(price, (int, float)):
        return math.isfinite(float(price)) and price >= 0
    return False


def _valid_duration(value: object) -> bool:
    return isinstance(value, str) and bool(_DURATION_PATTERN.fullmatch(value))


def _text(value: object) -> str:
    if value is None:
        return "정보 없음"
    if isinstance(value, str):
        return value if value else "정보 없음"
    text = str(value).strip()
    return text or "정보 없음"


def _price_text(event: dict) -> str:
    raw = event.get("price_text")
    if isinstance(raw, str) and raw.strip():
        return raw
    price = event.get("price")
    if _is_free(price):
        return "무료"
    if isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(float(price)):
        return f"{price:,.0f}원"
    return "정보 없음"


def _today(now: datetime | None) -> str:
    current = now.astimezone(_KST) if now is not None else datetime.now(_KST)
    return current.date().isoformat()


def _link_cache(checks: dict | None) -> dict:
    return checks if checks is not None else links.load_link_checks()


def event_jsonld(
    e: dict,
    checks: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """행사 dict → schema.org/Event JSON-LD with role-separated link proofs."""
    raw_location = e.get("location")
    location = raw_location if isinstance(raw_location, dict) else {}
    venue = e.get("venue") or location.get("name") or e.get("name") or e.get("sido") or "대한민국"
    address = e.get("address") or location.get("address") or " ".join(
        x for x in (e.get("sido"), e.get("sigungu")) if x
    )
    place: dict = {"@type": "Place", "name": venue}
    if address:
        place["address"] = address
    lat = e.get("lat") if e.get("lat") is not None else location.get("lat")
    lng = e.get("lng") if e.get("lng") is not None else location.get("lng")
    if lat is not None and lng is not None:
        place["geo"] = {
            "@type": "GeoCoordinates",
            "latitude": lat,
            "longitude": lng,
        }
    cache = _link_cache(checks)
    info_fields = links.link_fields(e.get("url"), checks=cache, now=now, role="info")
    booking_fields = links.booking_link_fields(e, checks=cache, now=now)
    info_url = info_fields.get("link_url") if info_fields.get("link_status") == "verified" else None
    booking_url = booking_fields.get("link_url") if booking_fields.get("link_status") == "verified" else None
    data: dict = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": e.get("name") or "",
        "startDate": e.get("start_date") or "",
        "location": place,
    }
    if e.get("end_date"):
        data["endDate"] = e["end_date"]
    if e.get("description"):
        data["description"] = e["description"]
    if info_url:
        data["url"] = info_url
    if e.get("status") in _STATUS_URL:
        data["eventStatus"] = _STATUS_URL[e["status"]]
    if _valid_duration(e.get("duration")):
        data["duration"] = e["duration"]
    raw_offers = e.get("offers")
    if _valid_price(e.get("price")) or isinstance(raw_offers, dict):
        offer: dict[str, object] = {"@type": "Offer"}
        if _valid_price(e.get("price")):
            offer["price"] = "0" if _is_free(e["price"]) else e["price"]
            offer["priceCurrency"] = "KRW"
        if booking_url:
            offer["url"] = booking_url
        data["offers"] = offer
    if e.get("organizer"):
        data["organizer"] = {"@type": "Organization", "name": e["organizer"]}
    return data

def _safe_jsonld(value: dict) -> str:
    """Serialize JSON-LD without allowing HTML script termination."""
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def render_page(
    e: dict,
    checks: dict | None = None,
    now: datetime | None = None,
) -> str:
    """행사 1건 → 언어 어댑터를 사용하는 정적 상세 HTML."""
    esc = html.escape
    cache = _link_cache(checks)
    info_fields = links.link_fields(e.get("url"), checks=cache, now=now, role="info")
    booking_fields = links.booking_link_fields(e, checks=cache, now=now)
    info_url = info_fields.get("link_url") if info_fields.get("link_status") == "verified" else None
    booking_url = booking_fields.get("link_url") if booking_fields.get("link_status") == "verified" else None
    name = esc(_text(e.get("name")))
    jsonld = _safe_jsonld(event_jsonld(e, checks=cache, now=now))
    today = _today(now)
    date_text = _text(e.get("start_date"))
    if e.get("end_date"):
        date_text += " ~ " + _text(e.get("end_date"))
    effective_end = e.get("end_date") or e.get("start_date")
    ended = bool(effective_end and str(effective_end)[:10] < today)
    date_value = esc(date_text)
    date_class = "event-date ended" if ended else "event-date"
    if ended:
        date_value = f'<del>{date_value}</del> <span class="status-badge ended" data-i18n="status.ended">종료</span>'
    raw_location = e.get("location")
    location = raw_location if isinstance(raw_location, dict) else {}
    where = _text(e.get("sido") or e.get("sigungu") or location.get("sido") or location.get("sigungu"))
    venue = _text(e.get("venue") or location.get("name"))
    address = _text(e.get("address") or location.get("address"))
    age = _text(e.get("age"))
    duration = _text(e.get("duration_text"))
    price = _price_text(e)
    organizer = _text(e.get("organizer"))
    application_end = _text(e.get("application_end"))
    status_value = derive_status(e, today)
    status_keys = {
        "신청전": "status.notYetOpen", "오픈": "status.open", "마감임박": "status.closingSoon",
        "마감": "status.applicationClosed", "종료": "status.ended", "예정": "status.upcoming",
        "진행중": "status.ongoing", "정보 없음": "status.unknown",
    }
    status_key = status_keys.get(status_value, "status.unknown")
    status_html = (
        '<span data-i18n="status.prefix">상태: </span>'
        f'<span class="status-value" data-status-key="{esc(status_key)}">{esc(status_value)}</span>'
    )

    def link_status_key(fields: dict) -> str:
        status = fields.get("link_status")
        if status == "verified":
            return "link.status.verified"
        if fields.get("link_reason") == "검증 기록 만료":
            return "link.status.expired"
        if status in {"blocked", "dead", "unverified"}:
            return f"link.status.{status}"
        return "link.status.unverified"

    info_status_key = link_status_key(info_fields)
    booking_status_key = link_status_key(booking_fields)
    if info_url:
        info_html = (
            f'<a href="{esc(info_url)}" rel="noopener" data-link-status="verified">'
            '<span data-i18n="link.infoLabel">공식 정보 보기</span></a> '
            '<span class="link-proof" data-i18n="link.status.verified">연결 확인</span>'
        )
    else:
        info_reason = esc(info_fields.get("link_reason") or "검증 기록 없음")
        info_html = (
            f'<span class="link-status" data-i18n="{esc(info_status_key)}">연결 미확인</span>'
            f' <span class="link-reason">{info_reason}</span>'
        )
    if booking_url:
        booking_html = (
            f'<a href="{esc(booking_url)}" rel="noopener" data-link-status="verified">'
            '<span data-i18n="link.bookingLabel">예매 사이트 바로가기</span></a> '
            '<span class="link-proof" data-i18n="link.status.verified">연결 확인</span>'
        )
    else:
        booking_reason = esc(booking_fields.get("link_reason") or "검증 기록 없음")
        booking_html = (
            f'<span class="link-status" data-i18n="{esc(booking_status_key)}">연결 미확인</span>'
            f' <span class="booking-reason">{booking_reason}</span>'
        )
    raw_url = e.get("url")
    source_url = esc(raw_url) if isinstance(raw_url, str) else "정보 없음"
    desc = esc(_text(e.get("description")))
    canonical = f'<link rel="canonical" href="{esc(info_url)}">\n' if info_url else ""
    return (
        "<!doctype html>\n"
        '<html lang="ko">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<base href="../">\n'
        f"<title>{name} - 놓치마</title>\n"
        f'<meta name="description" content="{desc[:150]}">\n'
        '<link rel="stylesheet" href="styles.css">\n'
        '<style>html,body{height:auto;min-height:100%;overflow:auto}.event-detail{max-width:720px;margin:2rem auto;padding:1.25rem;line-height:1.6}.language-toggle{min-width:44px;min-height:44px}.event-date.ended{color:#b42318;text-decoration:line-through}.status-badge.ended{color:#b42318;font-weight:700;text-decoration:none}.event-actions{display:flex;gap:.75rem;flex-wrap:wrap}.event-actions>p[data-i18n-template="detail.source"]{min-width:0;max-width:100%;overflow-wrap:anywhere}.event-actions a{display:inline-block;padding:.5rem .75rem;min-height:44px}.booking-disclaimer{font-weight:700}</style>\n'
        f"{canonical}"
        f'<script type="application/ld+json">\n{jsonld}\n</script>\n'
        '</head>\n<body>\n'
        '<main class="event-detail">\n'
        '<button id="languageToggle" type="button" class="language-toggle" aria-label="English"><span class="control-label">English</span></button>\n'
        f"<h1>{name}</h1>\n"
        f'<p class="{date_class}">🗓 <span data-i18n="detail.schedule">일정: </span>{date_value}</p>\n'
        f'<p class="event-status">{status_html}</p>\n'
        f'<p data-i18n-template="detail.region" data-i18n-value="{esc(where)}">지역: {esc(where)}</p>\n'
        f'<p data-i18n-template="detail.venue" data-i18n-value="{esc(venue)}">장소: {esc(venue)}</p>\n'
        f'<p class="detail-address"><span data-i18n="detail.address">주소: </span>{esc(address)}</p>\n'
        f'<p data-i18n-template="detail.age" data-i18n-value="{esc(age)}">대상 연령: {esc(age)}</p>\n'
        f'<p data-i18n-template="detail.price" data-i18n-value="{esc(price)}">비용: {esc(price)}</p>\n'
        f'<p data-i18n-template="detail.duration" data-i18n-value="{esc(duration)}">소요시간: {esc(duration)}</p>\n'
        f'<p data-i18n-template="detail.organizer" data-i18n-value="{esc(organizer)}">주최: {esc(organizer)}</p>\n'
        f'<p data-i18n-template="detail.applicationDeadline" data-i18n-value="{esc(application_end)}">신청 마감: {esc(application_end)}</p>\n'
        f'<p class="description">{desc}</p>\n'
        '<p data-i18n="detail.sourceLanguageNotice">제목·설명·장소·주소는 원문(한국어) 그대로이며 공식 번역이 없습니다.</p>\n'
        '<div class="event-actions">\n'
        f'<p data-i18n-template="detail.source" data-i18n-value="{source_url}">출처: {source_url}</p>\n'
        f'<p class="info-link">{info_html}</p>\n'
        f'<p class="booking-link"><span class="booking-disclaimer" data-i18n="link.bookingDisclaimer">예매처에서 확인</span> {booking_html}</p>\n'
        '</div>\n'
        '<p><a href="index.html" data-i18n="staticPage.backToAll">← 전체 행사 보기</a></p>\n'
        '</main>\n'
        '</body>\n'
        '<script src="../i18n.js"></script>\n'
        '</html>\n'
    )


def build(out_dir: Path | str | None = None, events: list[dict] | None = None) -> dict:
    """events(또는 events.json) → 행사별 상세 HTML. 멱등(동일 입력 동일 산출)."""
    out = Path(out_dir) if out_dir else OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    if events is None:
        events = (json.loads(EVENTS_JSON.read_text(encoding="utf-8"))
                  if EVENTS_JSON.exists() else [])

    checks = links.load_link_checks()
    written = set()
    for e in events:
        eid = e.get("id")
        if not eid:
            continue
        page = out / f"{_safe_id(eid)}.html"
        page.write_text(render_page(e, checks=checks), encoding="utf-8")
        written.add(page.name)

    # 더 이상 존재하지 않는 행사의 묵은 페이지 제거(파생물 정합).
    for stale in out.glob("*.html"):
        if stale.name not in written:
            stale.unlink()

    print(f"pages: events/{len(written)}개 상세 HTML(JSON-LD)")
    return {"path": str(out), "pages": len(written)}


def main(argv: list[str]) -> int:
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
