"""지식 DB(Markdown) → 정적 프런트엔드용 JSON 빌드.

산출(web/public/data/):
  events.json   검색/지도/리스트용 평탄화 배열(archived 제외)
  facets.json   필터 카운트(지역/테마/나이밴드/상태/유형)
  regions.json  시/도별 건수 + 대표 좌표(코로플레스/핀 보조)
  updated.json  생성시각·총계·소스별 신선도
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter, defaultdict


from scripts.common.config import ROOT, age_bands, all_regions, search_config
from scripts.common.dates import now_kst
from scripts.common.links import booking_link_fields, link_fields, load_link_checks
from scripts.common.okf import iter_events

OUT_DIR = ROOT / "web" / "public" / "data"

_EXPERIENCE_CATEGORIES = (
    "자연·생태",
    "과학·탐구",
    "만들기·예술",
    "농촌·먹거리",
    "신체·놀이",
    "기타 체험",
)
_EXPERIENCE_RULES = {
    "자연·생태": ("자연", "생태", "숲", "식물", "곤충", "동물", "텃밭"),
    "과학·탐구": ("과학", "탐구", "실험", "로봇", "천문", "공학", "메이커"),
    "만들기·예술": ("만들기", "만들", "공예", "미술", "그리기", "도예", "목공", "제작", "악기", "오케스트라"),
    "농촌·먹거리": ("농촌", "농장", "수확", "먹거리", "요리", "음식", "베이킹", "농사"),
    "신체·놀이": ("놀이", "게임", "스포츠", "운동", "체육", "야외활동", "캠프"),
}
_ACTIVITY_TOKENS = (
    "체험", "워크숍", "workshop", "실습", "실험", "관찰", "만들", "제작", "공예",
    "참여", "직접", "탐구", "놀이", "게임", "요리", "수확", "그리기", "조작",
)
_STRONG_ACTIVITY_TOKENS = (
    "워크숍", "workshop", "실습", "실험", "관찰", "만들", "제작", "공예", "참여",
    "직접", "탐구", "놀이", "게임", "요리", "수확", "그리기", "조작",
)


def derive_family_flags(
    *,
    age: str | None,
    age_bands: list[str] | None,
    audience: list[str] | None,
    name: str | None,
    description: str | None,
    themes: list[str] | None,
) -> tuple[bool, str]:
    """Return a conservative family flag and a short Korean evidence string.

    This helper only inspects supplied event text/fields. Missing or broad
    all-ages information is not promoted to family relevance.
    """
    age_text = age.strip() if isinstance(age, str) else ""
    age_key = age_text.casefold()
    universal_age = {"all", "전체", "전연령", "누구나", "all ages", "all-age", "all-ages"}
    raw_bands = age_bands if isinstance(age_bands, (list, tuple, set)) else []
    bands = {band.strip() for band in raw_bands if isinstance(band, str) and band.strip()}
    raw_audience = audience if isinstance(audience, (list, tuple, set)) else []
    raw_themes = themes if isinstance(themes, (list, tuple, set)) else []
    fields = [age_text, name if isinstance(name, str) else "", description if isinstance(description, str) else "", *raw_audience, *raw_themes]
    haystack = " ".join(value for value in fields if isinstance(value, str)).casefold()

    def _has_literal(pattern: str) -> bool:
        return pattern.casefold() in haystack

    # A child/minor token followed by a nearby restriction is a conservative
    # exclusion. Do not attempt double-negation NLP: any such wording stays out
    # of the default family result.
    child_restriction = re.compile(
        r"(?:노\s*키즈\s*(?:존)?|no\s*-?\s*kids?|미취학\s*아동|미성년자|어린이|아동|유아|"
        r"아이(?![가-힣])|키즈|child(?:ren)?|minor)[^.,;:/\n]{0,12}"
        r"(?:불가|금지|제한|사절|입장\s*불)"
    )
    if child_restriction.search(haystack):
        return False, "명시 제한: 아동 접근 제한"

    for pattern in ("노키즈존", "노 키즈 존", "no kids", "no-kids"):
        if _has_literal(pattern):
            return False, "명시 제한: 노키즈"
    for match in re.finditer(r"노\s*키즈(?!\s*존|[가-힣])", haystack):
        return False, "명시 제한: 노키즈"

    adult_patterns = (
        "성인 전용", "성인만", "성인 대상", "성인 프로그램", "adult only", "adults only",
        "adult-only", "adults-only", "19세 이상", "만 19세", "19+",
        "청소년 이상", "청소년/성인", "청소년·성인", "청소년 및 성인",
    )
    for pattern in adult_patterns:
        if _has_literal(pattern):
            return False, f"명시 제한: {pattern}"

    child_attendance = re.compile(
        r"(?:어린이|아동|유아|미취학\s*아동|아이(?![가-힣])|가족|키즈|child(?:ren)?|minor)"
        r"[^.,;:/\n]{0,16}(?:환영|동반\s*가능|참여\s*(?:가능|환영)?|참석\s*(?:가능|환영)?|"
        r"입장\s*(?:가능|환영)?|함께)"
    )
    parent_only = re.compile(
        r"(?:학부모|보호자|부모|양육자)[^.,;:/\n]{0,20}"
        r"(?:전용|만\s*(?:참석|참여|입장|이용)|대상)"
    )
    if any(parent_only.finditer(haystack)) and not any(child_attendance.finditer(haystack)):
        return False, "명시 제한: 보호자 전용"

    family_signals = (
        ("어린이", "어린이"),
        ("아동", "아동"),
        ("영유아", "영유아"),
        ("유아", "유아"),
        ("초등", "초등"),
        ("미취학", "미취학"),
        ("키즈", "키즈"),
        ("kids", "kids"),
        ("가족", "가족"),
    )
    for token, label in family_signals:
        if token.casefold() in haystack:
            return True, f"명시 문구: {label}"

    numeric_age = re.fullmatch(r"(\d+)?\s*[-~〜]\s*(\d+)?\s*세?", age_text)
    if age_key not in universal_age and numeric_age:
        lo = int(numeric_age.group(1) or 0)
        hi = int(numeric_age.group(2) or 200)
        if lo >= 19:
            return False, "명시 제한: 성인 연령대"
        if lo >= 14 and hi <= 18:
            return False, "명시 제한: 청소년 연령대"
        if hi <= 13:
            return True, f"대상 연령: {age_text}"

    exact_age = re.fullmatch(r"(?:만\s*)?(\d+)\s*세?", age_text)
    if age_key not in universal_age and exact_age and int(exact_age.group(1)) <= 13:
        return True, f"대상 연령: {age_text}"
    upper_bound = re.fullmatch(r"(?:만\s*)?(\d+)\s*세?\s*(?:이하|미만)", age_text)
    if age_key not in universal_age and upper_bound and int(upper_bound.group(1)) <= 13:
        return True, f"대상 연령: {age_text}"

    child_bands = {"영유아", "어린이"}
    if not age_text and bands and bands.issubset(child_bands):
        return True, "대상 연령대: 어린이"
    restricted_bands = {"청소년", "청년", "중장년", "노년"}
    if not age_text and bands and bands.issubset(restricted_bands):
        return False, "명시 제한: 청소년·성인 연령대"

    all_bands = {"영유아", "어린이", "청소년", "청년", "중장년", "노년"}
    if age_key in universal_age or all_bands.issubset(bands):
        return False, "전연령만 표기"
    return False, "근거 부족"
def _source_text(name: str | None, description: str | None, themes: list[str] | None) -> str:
    values = [name, description, *(themes or [])]
    return " ".join(value.strip() for value in values if isinstance(value, str) and value.strip())


def _source_snippet(text: str, needles: tuple[str, ...]) -> str | None:
    for needle in needles:
        match = re.search(re.escape(needle), text, flags=re.IGNORECASE)
        if match:
            start = max(0, match.start() - 12)
            end = min(len(text), match.end() + 24)
            return " ".join(text[start:end].split())[:80]
    return None


def derive_experiences(
    *,
    age: str | None = None,
    age_bands: list[str] | None = None,
    audience: list[str] | None = None,
    name: str | None = None,
    description: str | None = None,
    themes: list[str] | None = None,
    event_type: str | None = None,
) -> tuple[list[str], str | None, bool]:
    """Derive hands-on experience facets from source text only.

    Family safety exclusions are delegated to the existing family predicate. The
    classifier never infers facilities, preparation, age, price, or safety.
    """
    _family, family_evidence = derive_family_flags(
        age=age,
        age_bands=age_bands,
        audience=audience,
        name=name,
        description=description,
        themes=themes,
    )
    if family_evidence.startswith("명시 제한:"):
        return [], None, False

    text = _source_text(name, description, themes)
    lowered = text.casefold()
    if not text:
        return [], None, False
    view_words = ("공연", "인형극", "오케스트라", "음악", "연주회", "콘서트", "발표회", "뮤지컬", "전시", "관람", "감상")
    view_only = any(word.casefold() in lowered for word in view_words) or event_type == "MusicEvent"
    activity_hits = tuple(token for token in _ACTIVITY_TOKENS if token.casefold() in lowered)
    strong_hits = tuple(token for token in _STRONG_ACTIVITY_TOKENS if token.casefold() in lowered)
    if not activity_hits:
        return [], None, False

    categories: list[str] = []
    evidence: list[str] = []
    for category in _EXPERIENCE_CATEGORIES[:-1]:
        category_terms = _EXPERIENCE_RULES[category]
        if not any(term.casefold() in lowered for term in category_terms):
            continue
        # Use category-local evidence; a different category's activity must
        # not authorize a viewing-only category such as orchestra.
        activity_pattern = "|".join(re.escape(token) for token in _ACTIVITY_TOKENS)
        category_terms_for_activity = category_terms
        if category == "만들기·예술":
            category_terms_for_activity = tuple(term for term in category_terms if term not in ("악기", "오케스트라"))
        category_pattern = "|".join(re.escape(term) for term in category_terms_for_activity)
        category_activity = re.search(
            rf"(?:{category_pattern})[^.,;:/\n]{{0,8}}(?:{activity_pattern})|"
            rf"(?:{activity_pattern})[^.,;:/\n]{{0,8}}(?:{category_pattern})",
            lowered,
        ) if category_pattern else None
        explicit_science_activity = category == "과학·탐구" and re.search(r"과학[^.,;:/\n]{0,8}(?:체험|실험)", lowered)
        explicit_music_activity = category == "만들기·예술" and re.search(r"(?:악기|오케스트라)[^.,;:/\n]{0,8}(?:체험|직접|참여|워크숍)", lowered)
        if view_only and not explicit_science_activity and not explicit_music_activity and not category_activity:
            continue
        snippet = _source_snippet(text, category_terms + activity_hits)
        if snippet:
            categories.append(category)
            evidence.append(snippet)

    if not categories and (not view_only or strong_hits):
        snippet = _source_snippet(text, activity_hits)
        if snippet:
            categories.append("기타 체험")
            evidence.append(snippet)
    summary = " · ".join(dict.fromkeys(evidence)) or None
    return categories, summary, bool(categories)


def _flatten(fm: dict, *, link_checks: dict | None = None, now: dt.datetime | None = None) -> dict:
    loc = fm.get("location") or {}
    age = fm.get("age")
    bands = fm.get("age_bands") or age_bands(age)
    audience = fm.get("audience") or []
    themes = fm.get("themes") or []
    family_friendly, family_evidence = derive_family_flags(
        age=age,
        age_bands=bands,
        audience=audience,
        name=fm.get("name"),
        description=fm.get("description"),
        themes=themes,
    )
    experience_categories, experience_summary, is_experience = derive_experiences(
        age=age,
        age_bands=bands,
        audience=audience,
        name=fm.get("name"),
        description=fm.get("description"),
        themes=themes,
        event_type=fm.get("event_type"),
    )
    publication_link_fields = link_fields(fm.get("url"), checks=link_checks, now=now)
    if fm.get("x_nonpublishable_url"):
        publication_link_fields = {
            "link_status": "blocked",
            "link_checked_at": None,
            "link_url": None,
            "link_reason": "공식 홈페이지 없음",
        }
    # 예약(booking) 링크는 원문이 명시한 offers.url에서만 독립적으로 파생한다
    # (role="booking", 동일 단일 캐시) - 정보 url의 검증 결과를 빌려오지 않는다.
    offers = fm.get("offers") if isinstance(fm.get("offers"), dict) else None
    booking_fields = booking_link_fields(fm, checks=link_checks, now=now)
    booking_publication_fields = {
        "booking_link_status": booking_fields["link_status"],
        "booking_link_checked_at": booking_fields["link_checked_at"],
        "booking_link_url": booking_fields["link_url"],
        "booking_link_reason": booking_fields["link_reason"],
    }
    return {
        "id": fm.get("id"),
        "name": fm.get("name"),
        "description": fm.get("description"),
        "event_type": fm.get("event_type"),
        "themes": themes,
        "start_date": fm.get("start_date"),
        "end_date": fm.get("end_date"),
        "application_start": fm.get("application_start"),
        "application_end": fm.get("application_end"),
        "status": fm.get("status"),
        "sido": loc.get("sido"),
        "sigungu": loc.get("sigungu"),
        "venue": loc.get("name"),
        "address": loc.get("address"),
        "geo_precision": loc.get("geo_precision"),
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "age": age,
        "age_bands": bands,
        "audience": audience,
        "family_friendly": family_friendly,
        "family_evidence": family_evidence,
        "experience_categories": experience_categories,
        "experience_summary": experience_summary,
        "is_experience": is_experience,
        "price": fm.get("price"),
        "price_text": fm.get("price_text"),
        "duration_text": fm.get("duration_text"),
        "duration": fm.get("duration"),
        **publication_link_fields,
        "offers": offers,
        **booking_publication_fields,
        "organizer": fm.get("organizer"),
        "url": fm.get("url"),
        "image": fm.get("image"),
        "source": fm.get("source"),
        "verification": fm.get("x_verification"),
        "confidence": fm.get("x_confidence"),
    }




# 사용자 노출용 4상태 라벨(신청전/오픈/마감임박/마감). schema.org status(Open/Closed/
# Scheduled)와 신청기간을 결합해 FOMO 방어 배지/CTA 분기의 근거를 빌드타임에 파생한다.
_IMMINENT_DAYS = 3


def _date10(s: str | None) -> str:
    return (s or "")[:10]

_DEFAULT_RETENTION_DAYS = 90


def _retention_days() -> int:
    cfg = search_config()
    policy = cfg.get("publication_policy", {}) if isinstance(cfg, dict) else {}
    value = policy.get("retention_days") if isinstance(policy, dict) else None
    if value is None or isinstance(value, bool):
        return _DEFAULT_RETENTION_DAYS
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_RETENTION_DAYS
    return parsed if parsed >= 0 else _DEFAULT_RETENTION_DAYS


def _is_publishable(fm: dict, *, today: str, days: int) -> bool:
    if not isinstance(fm, dict) or fm.get("status") == "archived":
        return False
    effective_end = _date10(fm.get("end_date") or fm.get("start_date"))
    if not effective_end:
        return True
    try:
        cutoff = (dt.date.fromisoformat(_date10(today)) - dt.timedelta(days=days)).isoformat()
        return effective_end >= cutoff
    except ValueError:
        return True


def derive_status(e: dict, today: str) -> str:
    """행사 상태와 명시된 신청기간을 사실대로 결합한 표시 상태."""
    end = _date10(e.get("end_date") or e.get("start_date"))
    start = _date10(e.get("start_date"))
    if end and today > end:
        return "종료"

    status = e.get("status") or ""
    astart = _date10(e.get("application_start"))
    aend = _date10(e.get("application_end"))
    has_application_window = bool(astart or aend)
    if has_application_window:
        if status == "Closed" or (aend and today > aend):
            return "마감"
        if astart and today < astart:
            return "신청전"
        open_now = ((not astart or astart <= today)
                    and (not aend or today <= aend))
        if open_now:
            if aend:
                try:
                    days = (dt.date.fromisoformat(aend) - dt.date.fromisoformat(today)).days
                except ValueError:
                    days = None
                if days is not None and 0 <= days <= _IMMINENT_DAYS:
                    return "마감임박"
            return "오픈"

    # Open/Scheduled from a provider is not application evidence.  Without an
    # explicit application period, report only the performance schedule.
    if start and today < start:
        return "예정"
    if start and (not end or today <= end):
        return "진행중"
    if end and today <= end:
        return "진행중"
    return "정보 없음"
def _load_collection_status() -> dict:
    path = ROOT / "knowledge" / "sources" / "collection-status.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build() -> dict:

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    build_now = now_kst()
    today = build_now[:10]
    configured_retention_days = _retention_days()
    link_now = dt.datetime.fromisoformat(build_now)
    link_checks = load_link_checks()
    collection_status = _load_collection_status()
    events: list[dict] = []
    archived = 0
    excluded = 0
    by_source_count: Counter = Counter()
    oldest_fetch: dict[str, str] = {}

    for _path, fm, _body in iter_events():
        if fm.get("status") == "archived":
            archived += 1
            continue
        if not _is_publishable(fm, today=today, days=configured_retention_days):
            excluded += 1
            continue
        src = fm.get("source") or "unknown"
        by_source_count[src] += 1
        fetched = fm.get("fetched_at")
        if fetched and (src not in oldest_fetch or fetched < oldest_fetch[src]):
            oldest_fetch[src] = fetched
        events.append(_flatten(fm, link_checks=link_checks, now=link_now))

    # 표시 상태 라벨 파생(FOMO 방어 배지/CTA 분기 근거).
    today = build_now[:10]
    for event in events:
        event["display_status"] = derive_status(event, today)

    # 진행 중/예정 우선, 그 안에서 손-활동 경험 우선. 비경험 행사는 보존한다.
    def _sort_key(event: dict) -> tuple[int, int, str]:
        end = _date10(event.get("end_date")) or _date10(event.get("start_date"))
        ended = 1 if end and end < today else 0
        return ended, 0 if event.get("is_experience") else 1, event.get("start_date") or "9999"

    events.sort(key=_sort_key)



    facets: dict[str, Counter] = {
        "sido": Counter(),
        "theme": Counter(),
        "age_band": Counter(),
        "status": Counter(),
        "event_type": Counter(),
        "experience_category": Counter(),
        "experience": Counter(),
    }
    region_count: Counter = Counter()
    region_coord: dict[str, list] = defaultdict(lambda: [0.0, 0.0, 0])
    for e in events:
        if e["sido"]:
            facets["sido"][e["sido"]] += 1
            region_count[e["sido"]] += 1
            if e["lat"] and e["lng"]:
                acc = region_coord[e["sido"]]
                acc[0] += e["lat"]; acc[1] += e["lng"]; acc[2] += 1
        for t in e["themes"]:
            facets["theme"][t] += 1
        for b in e["age_bands"]:
            facets["age_band"][b] += 1
        if e["status"]:
            facets["status"][e["status"]] += 1
        if e["event_type"]:
            facets["event_type"][e["event_type"]] += 1
        for category in e["experience_categories"]:
            facets["experience_category"][category] += 1
        facets["experience"]["true" if e["is_experience"] else "false"] += 1

    facets_out = {k: dict(v) for k, v in facets.items()}

    regions_out: list[dict] = []
    region_meta = {r["name"]: r for r in all_regions()}
    for name, cnt in sorted(region_count.items(), key=lambda x: -x[1]):
        racc = region_coord.get(name)
        centroid = ([racc[0] / racc[2], racc[1] / racc[2]] if racc and racc[2] else None)
        regions_out.append({
            "sido": name,
            "slug": region_meta.get(name, {}).get("slug", "unknown"),
            "count": cnt,
            "centroid": centroid,
        })

    source_map = collection_status.get("sources")
    if not isinstance(source_map, dict):
        source_map = {}
    source_entries = [entry for entry in source_map.values() if isinstance(entry, dict)]
    modes = {entry.get("mode") for entry in source_entries if entry.get("mode")}
    if "live" in modes:
        source_mode = "live" if modes.issubset({"live"}) else "mixed"
    else:
        source_mode = "offline"
    successes = sorted(
        str(entry.get("last_success_at"))
        for entry in source_entries
        if isinstance(entry.get("last_success_at"), str) and entry.get("last_success_at")
    )
    collection_errors = [
        entry["error"].strip()
        for entry in source_entries
        if isinstance(entry.get("error"), str) and entry["error"].strip()
    ]
    updated = {
        "generated_at": build_now,
        "total_active": len(events),
        "archived": archived,
        "publication": {
            "retained": len(events),
            "excluded": excluded,
            "retention_days": configured_retention_days,
        },
        "freshness": oldest_fetch,
        "sources": source_map,
        "source_mode": source_mode,
        "last_success_at": successes[-1] if successes else None,
        "collection_started_at": collection_status.get("generated_at"),
        "collection_error_count": len(collection_errors),
        "collection_errors": collection_errors,
    }

    (OUT_DIR / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "facets.json").write_text(json.dumps(facets_out, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "regions.json").write_text(json.dumps(regions_out, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "updated.json").write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"build: events={len(events)} archived={archived} sido={len(regions_out)} themes={len(facets_out['theme'])}")
    return {"events": len(events), "archived": archived, "regions": len(regions_out)}


if __name__ == "__main__":
    build()
