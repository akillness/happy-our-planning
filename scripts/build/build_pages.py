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
from datetime import datetime
import sys
from pathlib import Path

from scripts.common import links
from scripts.common.config import ROOT
from scripts.common.okf import _safe_id

OUT_DIR = ROOT / "web" / "public" / "events"
EVENTS_JSON = ROOT / "web" / "public" / "data" / "events.json"

# schema.org/Event status URL 매핑(OKF status enum → schema.org EventStatusType).
_STATUS_URL = {
    "Open": "https://schema.org/EventScheduled",
    "Scheduled": "https://schema.org/EventScheduled",
    "Closed": "https://schema.org/EventCancelled",
    "Postponed": "https://schema.org/EventPostponed",
}


def _is_free(price: object) -> bool:
    return price in ("free", 0, 0.0)


def event_jsonld(
    e: dict,
    checks: dict | None = None,
    now: object = None,
) -> dict:
    """행사 dict → schema.org/Event JSON-LD(검증된 링크만 외부 URL로 사용)."""
    place: dict = {"@type": "Place", "name": e.get("sido") or "대한민국"}
    address = " ".join(x for x in (e.get("sido"), e.get("sigungu")) if x)
    if address:
        place["address"] = address
    if e.get("lat") is not None and e.get("lng") is not None:
        place["geo"] = {
            "@type": "GeoCoordinates",
            "latitude": e["lat"],
            "longitude": e["lng"],
        }
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
    verified = links.verified_link(e, now=now, checks=checks)
    if verified:
        data["url"] = verified
    if e.get("status") in _STATUS_URL:
        data["eventStatus"] = _STATUS_URL[e["status"]]
    if e.get("price") is not None:
        offer: dict[str, str] = {
            "@type": "Offer",
            "price": "0" if _is_free(e["price"]) else str(e["price"]),
            "priceCurrency": "KRW",
        }
        if verified:
            offer["url"] = verified
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
    """행사 1건 → 상세 HTML 문자열(JSON-LD 임베드)."""
    esc = html.escape
    fields = links.link_fields(e.get("url"), checks=checks, now=now)
    verified = fields.get("link_url") if fields.get("link_status") == "verified" else None
    name = esc(e.get("name") or "행사")
    jsonld = _safe_jsonld(event_jsonld(e, checks=checks, now=now))
    when = esc((e.get("start_date") or "")[:10])
    if e.get("end_date"):
        when += " ~ " + esc((e["end_date"])[:10])
    where = esc(" ".join(x for x in (e.get("sido"), e.get("sigungu")) if x) or "-")
    desc = esc(e.get("description") or "")
    status = esc(fields.get("link_status") or "unchecked")
    reason = esc(fields.get("link_reason") or "검증 기록 없음")
    canonical = f'<link rel="canonical" href="{esc(verified)}">\n' if verified else ""
    source = (
        f'<p><a href="{esc(verified)}" rel="noopener" data-link-status="verified">연결 확인</a></p>\n'
        if verified
        else f'<p data-link-status="{status}">외부 링크: {status} - {reason}</p>\n'
    )
    return (
        "<!doctype html>\n"
        '<html lang="ko">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{name} - 놓치마</title>\n"
        f'<meta name="description" content="{desc[:150]}">\n'
        f"{canonical}"
        f'<script type="application/ld+json">\n{jsonld}\n</script>\n'
        "</head>\n<body>\n"
        f"<h1>{name}</h1>\n"
        f"<p>🗓 {when}</p>\n"
        f"<p>📍 {where}</p>\n"
        + (f"<p>{desc}</p>\n" if desc else "")
        + source
        + '<p><a href="../index.html">← 전체 행사 보기</a></p>\n'
        "</body>\n</html>\n"
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
