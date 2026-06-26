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
        out.append({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "snippet": r.get("text") or r.get("summary") or "",
            "published": (r.get("publishedDate") or "")[:10] or None,
            "score": float(r.get("score", 0.0) or 0.0),
            "provider": "exa",
        })
    return out


def parse_brave(resp: dict) -> list[dict]:
    out = []
    for r in (resp.get("web", {}) or {}).get("results", []) or []:
        out.append({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "snippet": r.get("description") or "",
            "published": (r.get("age") or r.get("page_age") or "")[:10] or None,
            # Brave는 score를 주지 않음 → 랭크 역수로 근사(상위일수록 높게)
            "score": None,
            "provider": "brave",
        })
    # 순위 기반 점수 부여(0.9 → 0.5 선형)
    n = len(out)
    for i, h in enumerate(out):
        h["score"] = round(0.9 - (0.4 * i / max(n - 1, 1)), 3) if n else 0.0
    return out


def parse_tavily(resp: dict) -> list[dict]:
    out = []
    for r in resp.get("results", []) or []:
        out.append({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "snippet": r.get("content") or "",
            "published": (r.get("published_date") or "")[:10] or None,
            "score": float(r.get("score", 0.0) or 0.0),
            "provider": "tavily",
        })
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
                print(f"[websearch:{self.provider}] '{query}' 실패: {exc}")
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
        if any(dom.endswith(t) for t in self.scfg.get("trusted_domains", [])):
            base = min(1.0, base + 0.1)
        return round(base, 3)

    def map_to_okf(self, hit: dict) -> dict | None:
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
