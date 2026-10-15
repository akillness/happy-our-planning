"""웹 검색 API(브라우저 검색) 기반 "행사 발견" 어댑터.

공공 API가 다루지 않는 신규/모집형 행사를 Exa·Brave·Tavily 같은 무료 티어
검색 API로 발견해 OKF Event 후보로 적재한다. 키가 없으면 raw/websearch/ 의
픽스처(sample-*)로 동작한다(네트워크 없이 파이프라인 유지).

제공자별 응답 스키마가 달라서 parse_<provider>(resp) → 공통 hit(dict) 로
정규화한 뒤, map_to_okf(hit) 한 곳에서 OKF로 매핑한다.

공통 hit 스키마:
    {title, url, snippet, published, score,
     sido?, start_date?, end_date?, application_start?, application_end?,
     theme?, price?, provider?, query?}
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
from urllib.parse import urlsplit

from scripts.common.config import (
    canonical_sido,
    extract_sido,
    search_config,
    search_provider,
)
from scripts.common.dates import KST, combine_kst
from scripts.common.okf import content_hash
from scripts.ingest.base import SourceAdapter, now_kst

# 표준 테마 → schema.org event_type (event.schema.json enum)
_THEME_EVENT_TYPE = {
    "축제": "Festival",
    "전시": "ExhibitionEvent",
    "공연": "MusicEvent",
    "교육": "EducationEvent",
    "체험": "Event",
    "공모전": "Event",
    "채용·취업": "BusinessEvent",
    "복지·지원": "Event",
    "스포츠": "SportsEvent",
    "봉사": "Event",
}

# 본문 키워드 → 표준 테마 추정(명시 theme 없을 때)
_KEYWORD_THEME = [
    (("축제", "페스티벌", "문화제"), "축제"),
    (("공모전", "공모", "콘테스트", "대회"), "공모전"),
    (("전시", "특별전", "미술", "박물관"), "전시"),
    (("공연", "콘서트", "뮤지컬", "연극", "클래식"), "공연"),
    (("교육", "강좌", "워크숍", "세미나", "강연"), "교육"),
    (("체험", "원데이", "투어", "캠프"), "체험"),
    (("채용", "일자리", "취업"), "채용·취업"),
    (("봉사", "자원봉사"), "봉사"),
]


def _to_kst_datetime(value: str | None, end: bool = False) -> str | None:
    """'2026-08-01' 또는 ISO datetime → KST date-time ISO. end=True면 23:59:59."""
    if not value:
        return None
    value = value.strip()
    try:
        if "T" in value:
            d = dt.datetime.fromisoformat(value)
            if d.tzinfo is None:
                d = d.replace(tzinfo=KST)
            return d.isoformat()
        day = dt.date.fromisoformat(value[:10])
    except ValueError:
        return None
    return combine_kst(day, end)
def _metadata_ranges_valid(hit: dict) -> bool:
    for start_field, end_field in (("start_date", "end_date"),
                                   ("application_start", "application_end")):
        start_raw = hit.get(start_field)
        end_raw = hit.get(end_field)
        try:
            start = _to_kst_datetime(start_raw) if start_raw else None
            end = _to_kst_datetime(end_raw, end=True) if end_raw else None
            start_dt = dt.datetime.fromisoformat(start) if start else None
            end_dt = dt.datetime.fromisoformat(end) if end else None
        except (AttributeError, TypeError, ValueError):
            return False
        if (start_raw and not start) or (end_raw and not end):
            return False
        if start_dt and end_dt and end_dt < start_dt:
            return False
    return True


def _date_matches(value: str | None, expected: str) -> bool:
    try:
        converted = _to_kst_datetime(value) if value else None
    except (AttributeError, TypeError, ValueError):
        return False
    return bool(converted and converted[:10] == expected)

_MAX_DATE_TEXT = 2400
_EVENT_CONTEXT = re.compile(
    r"행사\s*(?:기간|일|일정)|개최\s*(?:기간|일|일정)|"
    r"교육\s*(?:기간|일|일정)|체험\s*(?:기간|일|일정)|"
    r"전시\s*(?:기간|일|일정)|공연\s*(?:기간|일|일정)|"
    r"운영\s*기간|진행\s*기간|개막(?:일|식)?|개최|진행"
)
_APPLICATION_CONTEXT = re.compile(
    r"신청|접수|모집|지원|등록|서류|마감|발표|선발|합격"
)
_PUBLICATION_CONTEXT = re.compile(
    r"발행|게시|작성|등록\s*일|공고\s*일|게시일|수정|업데이트|갱신|기사"
)
_APPLICATION_END_CONTEXT = re.compile(r"마감|종료|끝|까지")
_KO_DATE = re.compile(
    r"(?P<year>20\d{2})\s*년\s*(?P<month>\d{1,2})\s*월\s*"
    r"(?P<day>\d{1,2})\s*일?"
)
_NUMERIC_DATE = re.compile(
    r"(?P<year>20\d{2})[./-](?P<month>\d{1,2})[./-](?P<day>\d{1,2})"
)
_YEAR_ONLY = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_KO_PARTIAL_DATE = re.compile(
    r"(?P<month>\d{1,2})\s*월\s*(?P<day>\d{1,2})\s*일?"
)
_NUMERIC_PARTIAL_DATE = re.compile(
    r"(?P<month>\d{1,2})[./-](?P<day>\d{1,2})"
)
_RANGE_SEPARATOR = re.compile(r"^\s*(?:~|～|∼|〜|–|—|부터|-)\s*$")
_ISO_INSTANT = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?"
_ISO_INSTANT_RANGE = re.compile(
    rf"(?P<start>{_ISO_INSTANT})\s*(?:~|～|∼|〜|–|—|-)\s*"
    rf"(?P<end>{_ISO_INSTANT})"
)


def _date_tokens(text: str) -> list[dict]:
    """Return bounded, validated calendar tokens without publication metadata."""
    matches: list[dict] = []
    patterns = (
        (_KO_DATE, True),
        (_NUMERIC_DATE, True),
        (_KO_PARTIAL_DATE, False),
        (_NUMERIC_PARTIAL_DATE, False),
    )
    for pattern, has_year in patterns:
        for match in pattern.finditer(text[:_MAX_DATE_TEXT]):
            try:
                year = int(match.group("year")) if has_year else None
                month = int(match.group("month"))
                day = int(match.group("day"))
                value = dt.date(year or 2000, month, day)
            except (TypeError, ValueError):
                continue
            matches.append({
                "start": match.start(),
                "end": match.end(),
                "year": year,
                "month": month,
                "day": day,
                "value": value,
            })
    # Full tokens contain a partial-token match; retain only the outer token.
    matches.sort(key=lambda item: (item["start"], -(item["end"] - item["start"])))
    tokens: list[dict] = []
    for token in matches:
        if any(token["start"] < prev["end"] and prev["start"] < token["end"]
               for prev in tokens):
            continue
        tokens.append(token)
    return sorted(tokens, key=lambda item: item["start"])


def _date_context(text: str, token: dict) -> str | None:
    """Return the nearest event/application/publication field label."""
    before = text[max(0, token["start"] - 80):token["start"]]
    after = text[token["end"]:min(len(text), token["end"] + 80)]
    candidates: list[tuple[int, int, str]] = []
    for pattern, kind in ((_EVENT_CONTEXT, "event"),
                          (_APPLICATION_CONTEXT, "application"),
                          (_PUBLICATION_CONTEXT, "publication")):
        priority = 0 if kind in ("publication", "application") else 1
        for match in pattern.finditer(before):
            candidates.append((len(before) - match.end(), priority, kind))
        for match in pattern.finditer(after):
            candidates.append((match.start(), priority, kind))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def _contextual_years(text: str, tokens: list[dict]) -> set[int]:
    years = {
        token["year"] for token in tokens
        if token["year"] and _date_context(text, token) in ("event", "application")
    }
    for match in _YEAR_ONLY.finditer(text):
        if any(token["start"] <= match.start() < token["end"] for token in tokens):
            continue
        token = {"start": match.start(), "end": match.end()}
        if _date_context(text, token) in ("event", "application"):
            years.add(int(match.group(1)))
    return years


def _resolve_token_date(token: dict, years: set[int], preferred_year: int | None = None) -> dt.date | None:
    year = token["year"] or preferred_year
    if year is None and len(years) == 1:
        year = next(iter(years))
    if year is None:
        return None
    try:
        return dt.date(year, token["month"], token["day"])
    except ValueError:
        return None


def _context_date_ranges(
    text: str, tokens: list[dict], kind: str, years: set[int]
) -> list[tuple[str, str | None, tuple[int, int]]]:
    ranges: list[tuple[str, str | None, tuple[int, int]]] = []
    for first, second in zip(tokens, tokens[1:]):
        gap = text[first["end"]:second["start"]]
        if not _RANGE_SEPARATOR.fullmatch(gap):
            continue
        if _date_context(text, first) != kind:
            continue
        start_day = _resolve_token_date(first, years)
        if not start_day:
            continue
        end_day = _resolve_token_date(second, years, start_day.year)
        if not end_day or end_day < start_day:
            continue
        # A missing end year is accepted only for an unambiguous same-year range.
        if second["year"] is None and end_day.year != start_day.year:
            continue
        ranges.append((start_day.isoformat(), end_day.isoformat(),
                       (first["start"], second["end"])))
    return ranges


def _has_invalid_contextual_date(text: str, kind: str) -> bool:
    full_spans = [
        (match.start(), match.end())
        for pattern in (_KO_DATE, _NUMERIC_DATE)
        for match in pattern.finditer(text)
    ]
    patterns = (
        (_KO_DATE, True),
        (_NUMERIC_DATE, True),
        (_KO_PARTIAL_DATE, False),
        (_NUMERIC_PARTIAL_DATE, False),
    )
    for pattern, has_year in patterns:
        for match in pattern.finditer(text):
            if not has_year and any(
                start < match.end() and match.start() < end
                for start, end in full_spans
            ):
                continue
            year = int(match.group("year")) if has_year else 2000
            try:
                dt.date(year, int(match.group("month")), int(match.group("day")))
            except ValueError:
                token = {"start": match.start(), "end": match.end()}
                if _date_context(text, token) == kind:
                    return True
    return False
def _has_unresolved_context_range(
    text: str, tokens: list[dict], kind: str, years: set[int]
) -> bool:
    range_prefix = re.compile(r"^\s*(?:~|～|∼|〜|–|—|-)\s*")
    for index, first in enumerate(tokens):
        if _date_context(text, first) != kind:
            continue
        tail = text[first["end"]:first["end"] + 48]
        separator = range_prefix.match(tail)
        if not separator:
            continue
        second = None
        for candidate in tokens[index + 1:]:
            if candidate["start"] < first["end"] + separator.end():
                continue
            gap = text[first["end"]:candidate["start"]]
            if _RANGE_SEPARATOR.fullmatch(gap):
                second = candidate
            break
        if second is None:
            return True
        start_day = _resolve_token_date(first, years)
        end_day = _resolve_token_date(second, years,
                                      start_day.year if start_day else None)
        if not start_day or not end_day or end_day < start_day:
            return True
        if second["year"] is None and end_day.year != start_day.year:
            return True
    return False


def _extract_context_dates(text: str, kind: str) -> tuple[str, str | None] | None:
    bounded = (text or "")[:_MAX_DATE_TEXT]
    if _has_invalid_contextual_date(bounded, kind):
        return None
    tokens = _date_tokens(bounded)
    if not tokens:
        return None
    for match in _ISO_INSTANT_RANGE.finditer(bounded):
        token = {"start": match.start(), "end": match.end()}
        if _date_context(bounded, token) != kind:
            continue
        try:
            start_instant = dt.datetime.fromisoformat(
                match.group("start").replace("Z", "+00:00")
            )
            end_instant = dt.datetime.fromisoformat(
                match.group("end").replace("Z", "+00:00")
            )
        except ValueError:
            return None
        if end_instant < start_instant:
            return None
    years = _contextual_years(bounded, tokens)
    if _has_unresolved_context_range(bounded, tokens, kind, years):
        return None
    ranges = _context_date_ranges(bounded, tokens, kind, years)
    all_ranges = []
    for context_kind in ("event", "application"):
        all_ranges.extend(_context_date_ranges(bounded, tokens, context_kind, years))
    unique_ranges = {(start, end) for start, end, _ in ranges}
    if len(unique_ranges) > 1:
        return None

    covered: set[int] = set()
    for index, token in enumerate(tokens):
        if any(span_start <= token["start"] < span_end
               for _, _, (span_start, span_end) in all_ranges):
            covered.add(index)
    selected_range: tuple[str, str | None] | None = None
    if unique_ranges:
        selected_range = next(iter(unique_ranges))

    singles = []
    for index, token in enumerate(tokens):
        if index in covered or _date_context(bounded, token) != kind:
            continue
        value = _resolve_token_date(token, years)
        if value:
            singles.append(value.isoformat())
    unique_singles = set(singles)
    if selected_range:
        if unique_singles - set(selected_range):
            return None
        return selected_range
    if len(unique_singles) != 1:
        return None
    return next(iter(unique_singles)), None


def _extract_event_dates(text: str) -> tuple[str, str | None] | None:
    """Extract one contextual event date/range; reject ambiguous article dates."""
    return _extract_context_dates(text, "event")


def _extract_application_dates(text: str) -> tuple[str, str | None] | None:
    """Extract application dates without promoting them to an event period."""
    return _extract_context_dates(text, "application")


def _application_is_end(text: str, target_date: str | None = None) -> bool:
    bounded = (text or "")[:_MAX_DATE_TEXT]
    tokens = _date_tokens(bounded)
    years = _contextual_years(bounded, tokens)
    for token in tokens:
        if _date_context(bounded, token) != "application":
            continue
        if target_date:
            value = _resolve_token_date(token, years)
            if not value or value.isoformat() != target_date:
                continue
        before = bounded[max(0, token["start"] - 80):token["start"]]
        app_labels = list(_APPLICATION_CONTEXT.finditer(before))
        nearest_label = before[app_labels[-1].start():] if app_labels else ""
        after = bounded[token["end"]:token["end"] + 24]
        if (_APPLICATION_END_CONTEXT.search(nearest_label)
                or _APPLICATION_END_CONTEXT.search(after)):
            return True
    return False


def _enrich_event_metadata(hit: dict, raw: dict | None = None) -> dict:
    """Preserve explicit provider metadata, then add contextual event/application dates."""
    enriched = dict(hit)
    structured_application = any(
        (hit.get(field) or (raw or {}).get(field) or (raw or {}).get(alias))
        for field, alias in (("application_start", "applicationStart"),
                             ("application_end", "applicationEnd"))
    )
    if raw:
        aliases = {
            "start_date": ("start_date", "startDate"),
            "end_date": ("end_date", "endDate"),
            "application_start": ("application_start", "applicationStart"),
            "application_end": ("application_end", "applicationEnd"),
            "sido": ("sido",),
            "theme": ("theme",),
            "price": ("price",),
        }
        for field, keys in aliases.items():
            if enriched.get(field):
                continue
            for key in keys:
                value = raw.get(key)
                if value not in (None, ""):
                    enriched[field] = value
                    break

    text = f"{enriched.get('title', '')}\n{enriched.get('snippet', '')}"
    extracted = _extract_event_dates(text)
    if extracted:
        extracted_start, extracted_end = extracted
        explicit_start = enriched.get("start_date")
        explicit_end = enriched.get("end_date")
        start_matches = (not explicit_start
                         or _date_matches(explicit_start, extracted_start))
        end_matches = (not extracted_end or not explicit_end
                       or _date_matches(explicit_end, extracted_end))
        if start_matches and end_matches:
            if not explicit_start:
                enriched["start_date"] = extracted_start
            if extracted_end and not explicit_end:
                enriched["end_date"] = extracted_end

    if not structured_application:
        application = _extract_application_dates(text)
        if application:
            application_start, application_end = application
            if application_end:
                enriched.setdefault("application_start", application_start)
                enriched.setdefault("application_end", application_end)
            elif _application_is_end(text, application_start):
                enriched.setdefault("application_end", application_start)
            else:
                enriched.setdefault("application_start", application_start)
    return enriched



def _guess_theme(hit: dict) -> str | None:
    if hit.get("theme"):
        return hit["theme"]
    text = f"{hit.get('title','')} {hit.get('snippet','')}"
    for keys, theme in _KEYWORD_THEME:
        if any(k in text for k in keys):
            return theme
    return None


def _domain(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


# ---- 제공자별 응답 → 공통 hit -------------------------------------------------

def parse_exa(resp: dict) -> list[dict]:
    out = []
    for r in resp.get("results", []) or []:
        out.append(_enrich_event_metadata({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "snippet": r.get("text") or r.get("summary") or "",
            "published": (r.get("publishedDate") or "")[:10] or None,
            "score": float(r.get("score", 0.0) or 0.0),
            "provider": "exa",
        }, r))
    return out


def parse_brave(resp: dict) -> list[dict]:
    out = []
    for r in (resp.get("web", {}) or {}).get("results", []) or []:
        out.append(_enrich_event_metadata({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "snippet": r.get("description") or "",
            # page_age(ISO) 우선, 없으면 age(상대표현) — ISO만 날짜로 신뢰.
            "published": (r.get("page_age") or r.get("age") or "")[:10] or None,
            # Brave는 score를 주지 않음 → 랭크 역수로 근사(상위일수록 높게)
            "score": None,
            "provider": "brave",
        }, r))
    # 순위 기반 점수 부여(0.9 → 0.5 선형)
    n = len(out)
    for i, h in enumerate(out):
        h["score"] = round(0.9 - (0.4 * i / max(n - 1, 1)), 3) if n else 0.0
    return out


def parse_tavily(resp: dict) -> list[dict]:
    out = []
    for r in resp.get("results", []) or []:
        out.append(_enrich_event_metadata({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "snippet": r.get("content") or "",
            "published": (r.get("published_date") or "")[:10] or None,
            "score": float(r.get("score", 0.0) or 0.0),
            "provider": "tavily",
        }, r))
    return out


_PARSERS = {"exa": parse_exa, "brave": parse_brave, "tavily": parse_tavily}


class WebSearchAdapter(SourceAdapter):
    key = "websearch"
    fmt = "json"

    def __init__(self, provider: str | None = None, offline: bool | None = None):
        self.provider = provider or (self.__class__.cfg_provider())
        prov_cfg = search_provider(self.provider)
        self.prov_cfg = prov_cfg
        self.api_key = os.environ.get(prov_cfg.get("secret_env", ""), "")
        # base SourceAdapter는 sources.yaml 인증을 보지만 websearch는 search.yaml 사용
        from scripts.common.config import source as source_cfg
        self.cfg = source_cfg(self.key) or {}
        self.offline = (not self.api_key) if offline is None else offline
        self.collection_mode = "fixture" if self.offline else "live"
        self.collection_errors: list[BaseException] = []
        self.snapshot_complete = False
        self.complete = False
        self.truncated = False
        self.scfg = search_config()

    @classmethod
    def cfg_provider(cls) -> str:
        from scripts.common.config import source as source_cfg
        return (source_cfg("websearch") or {}).get("provider") or \
            search_config().get("default_provider", "exa")

    # ---- 수집 ---------------------------------------------------------------
    def _parse_json(self, text: str) -> list[dict]:
        import json
        data = json.loads(text)
        # 픽스처는 이미 공통 hit 리스트
        if isinstance(data, list):
            return data
        # 제공자 원시 응답이면 파서로 정규화
        return _PARSERS.get(self.provider, parse_exa)(data)

    def _fetch_remote(self) -> list[dict]:  # pragma: no cover - 네트워크 경로
        import httpx

        hits: list[dict] = []
        num = int(self.prov_cfg.get("num_results", 10))
        for query in self._expand_queries():
            try:
                resp = self._call_provider(httpx, query, num)
            except Exception as exc:
                self.collection_errors.append(exc)
                print(f"[websearch:{self.provider}] query failed: {type(exc).__name__}")
                continue
            for h in _PARSERS.get(self.provider, parse_exa)(resp):
                h.setdefault("query", query)
                hits.append(h)
        return hits

    def _expand_queries(self) -> list[str]:  # pragma: no cover - 네트워크 경로
        year = dt.date.today().year
        out = []
        for tmpl in self.scfg.get("queries", []):
            out.append(tmpl.replace("{region}", "전국").replace("{theme}", "")
                       .replace("{year}", str(year)).strip())
        return out or [f"전국 {year} 무료 행사 신청"]

    def _call_provider(self, httpx, query: str, num: int) -> dict:  # pragma: no cover
        url = self.prov_cfg.get("base_url", "")
        if self.provider == "exa":
            r = httpx.post(url, headers={"x-api-key": self.api_key},
                           json={"query": query, "numResults": num, "contents": {"text": True}},
                           timeout=20)
        elif self.provider == "brave":
            r = httpx.get(url, headers={"X-Subscription-Token": self.api_key},
                          params={"q": query, "count": num}, timeout=20)
        elif self.provider == "tavily":
            r = httpx.post(url, json={"api_key": self.api_key, "query": query,
                                      "max_results": num}, timeout=20)
        else:
            raise ValueError(f"미지원 제공자: {self.provider}")
        r.raise_for_status()
        return r.json()

    # ---- 매핑 ---------------------------------------------------------------
    def _confidence(self, hit: dict) -> float:
        base = float(hit.get("score") or 0.0)
        dom = _domain(hit.get("url", ""))
        if any(dom == t or dom.endswith("." + t) for t in self.scfg.get("trusted_domains", [])):
            base = min(1.0, base + 0.1)
        return round(base, 3)

    def map_to_okf(self, hit: dict) -> dict | None:
        hit = _enrich_event_metadata(hit)
        if not _metadata_ranges_valid(hit):
            return None
        name = (hit.get("title") or "").strip()
        url = (hit.get("url") or "").strip()
        if not name or not url:
            return None

        confidence = self._confidence(hit)
        if confidence < float(self.scfg.get("min_confidence", 0.45)):
            return None

        start = _to_kst_datetime(hit.get("start_date"))
        if not start:
            return None  # 날짜 없는 일반 글은 행사 후보로 적재하지 않음

        sido = canonical_sido(hit.get("sido")) or extract_sido(
            f"{name} {hit.get('snippet','')}")
        if not sido:
            return None

        theme = _guess_theme(hit)
        event_type = _THEME_EVENT_TYPE.get(theme or "", "Event")
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]

        event = {
            "id": f"websearch:{digest}",
            "name": name,
            "description": (hit.get("snippet") or "").strip() or None,
            "event_type": event_type,
            "themes": [theme] if theme else [],
            "start_date": start,
            "status": "Scheduled",
            "attendance_mode": "Offline",
            "location": {"sido": sido},
            "url": url,
            "source": self.key,
            "source_url": url,
            "fetched_at": now_kst(),
            "x_provider": hit.get("provider") or self.provider,
            "x_query": hit.get("query"),
            "x_confidence": confidence,
            "x_verification": "web-discovered",  # 미검증 발견 후보(공식확인 전)
        }
        end = _to_kst_datetime(hit.get("end_date"), end=True)
        if end:
            event["end_date"] = end
        for fld in ("application_start", "application_end"):
            v = _to_kst_datetime(hit.get(fld), end=fld == "application_end")
            if v:
                event[fld] = v
        if hit.get("price") is not None:
            event["price"] = hit["price"]
        # 신청기간이 명시되면 신청가능 상태로
        if event.get("application_start") and event.get("application_end"):
            event["status"] = "Open"
        event = {k: v for k, v in event.items() if v is not None}
        event["content_hash"] = content_hash(event)
        return event


if __name__ == "__main__":
    rows = WebSearchAdapter(offline=True).collect()
    print(f"websearch: {len(rows)} OKF 후보 ({WebSearchAdapter.cfg_provider()})")
    for r in rows[:5]:
        print(" -", r["id"], r["name"], r["location"]["sido"], r.get("x_confidence"))
