"""KOPIS(공연예술통합전산망) 어댑터. 공연/전시 목록 XML → OKF Event."""
from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any

from scripts.common.config import canonical_sido
from scripts.common.dates import KST, combine_kst
from scripts.common.http import HttpError, request_with_retry
from scripts.common.okf import content_hash
from scripts.ingest.base import SourceAdapter, log, now_kst

_GENRE_THEME = {
    "연극": ("공연", "Event"),
    "뮤지컬": ("공연", "MusicEvent"),
    "서양음악(클래식)": ("공연", "MusicEvent"),
    "한국음악(국악)": ("공연", "MusicEvent"),
    "대중음악": ("공연", "MusicEvent"),
    "무용": ("공연", "Event"),
    "대중무용": ("공연", "Event"),
    "서커스/마술": ("공연", "Event"),
    "복합": ("공연", "Event"),
    "전시": ("전시", "ExhibitionEvent"),
}

_STATE = {"공연중": "Scheduled", "공연예정": "Scheduled", "공연완료": "archived"}


def _normalize_api_key(value: str) -> str:
    if not isinstance(value, str):
        return value
    return urllib.parse.unquote(value.strip())

class CollectionError(RuntimeError):
    """Remote collection failed without retaining request credentials in its text."""

    def __init__(self, reason: str, status: int | None = None) -> None:
        self.reason = reason
        self.status = status
        suffix = f" (HTTP {status})" if isinstance(status, int) else ""
        super().__init__(f"KOPIS collection {reason}{suffix}")


# 공연장(fcltynm) → 실제 주소 좌표. 목록 API는 공연장명만 주므로 시설검색
# (/prfplc?shprfnmfct=) + 시설상세(/prfplc/{mt10id}의 adres/la/lo)로 보강한다.
# 결과는 knowledge/_geocache.json에 영속(부정 캐시 포함) — CI 재실행 시 신규만 조회.
_VENUE_CACHE_FILENAME = "_geocache.json"
_VENUE_NEW_LIMIT = 250  # 1회 수집당 신규 공연장 조회 상한(쿼터 보호)

# 상세(/pblprfr/{id}) 조회 영속 캐시. 성공분만 저장(실패는 캐시하지 않음 — 다음
# 실행에서 재시도될 기회를 보존, venue 캐시의 부정 캐시와 다른 성격).
_DETAIL_CACHE_FILENAME = "_detailcache.json"
_DETAIL_MAX_CALLS_DEFAULT = 0   # 설정 미지정 시 비활성(기존 테스트/도구 호환). sources.yaml이 운영값(40)을 명시.
_DETAIL_CACHE_TTL_HOURS_DEFAULT = 24.0


def _venue_cache_path():
    from pathlib import Path

    from scripts.common.config import ROOT

    return ROOT / "knowledge" / _VENUE_CACHE_FILENAME


def _load_venue_cache() -> dict:
    import json as _json

    try:
        payload = _json.loads(_venue_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_venue_cache(cache: dict) -> None:
    import json as _json

    try:
        _venue_cache_path().write_text(
            _json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        pass


def _norm_venue(name: object) -> str:
    import re as _re

    return _re.sub(r"\s+", "", str(name or "")).lower()


def _venue_search_names(name: str) -> list[str]:
    """전체 이름 → 괄호 메모 제거형 순으로 최대 2회 검색."""
    full = (name or "").strip()
    if not full:
        return []
    out = [full]
    short = full.split("(")[0].strip()
    if short and short != full:
        out.append(short)
    return out


def _pick_facility(rows: list[dict], name: str) -> dict | None:
    """시설검색 결과에서 공연장명 정확 일치만 채택(오매칭 방지)."""
    want = _norm_venue(name)
    if not want:
        return None
    for row in rows:
        if _norm_venue(row.get("fcltynm")) == want:
            return row
    return None


def _geo_from_facility(detail: dict) -> dict | None:
    """시설상세 → {lat, lng, address, mt10id}. 한국 영역 밖이면 None."""
    try:
        lat = float(str(detail.get("la") or "").strip())
        lng = float(str(detail.get("lo") or "").strip())
    except (TypeError, ValueError):
        return None
    if not (33.0 <= lat <= 39.0 and 124.0 <= lng <= 132.0):
        return None
    address = str(detail.get("adres") or "").strip()
    if not address:
        return None
    return {"lat": lat, "lng": lng, "address": address,
            "mt10id": str(detail.get("mt10id") or "").strip() or None}


def _kst_date(value: str, end: bool = False) -> str | None:
    """'2026.07.18' → ISO 8601(KST). end=True면 23:59:59."""
    value = (value or "").replace("-", ".").strip()
    try:
        d = dt.datetime.strptime(value, "%Y.%m.%d")
    except ValueError:
        return None
    return combine_kst(d, end)


def _clock_date(clock: Callable[[], Any] | None) -> dt.date:
    value = clock() if clock else dt.datetime.now(KST)
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(KST)
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value).date()
        except ValueError:
            return dt.date.fromisoformat(value[:10])
    raise ValueError("unsupported clock value")


def _clock_datetime(clock: Callable[[], Any] | None) -> dt.datetime:
    """상세 캐시 신선도 비교용 tz-aware datetime(날짜만 있으면 00:00 KST)."""
    value = clock() if clock else dt.datetime.now(KST)
    if isinstance(value, dt.datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=KST)
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time(0, 0, 0), KST)
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value)
        except ValueError:
            return dt.datetime.now(KST)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=KST)
    return dt.datetime.now(KST)


def _clock_iso(clock: Callable[[], Any] | None) -> str:
    value = clock() if clock else now_kst()
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=KST)
        return value.astimezone(KST).isoformat(timespec="seconds")
    if isinstance(value, dt.date):
        return combine_kst(value)
    if isinstance(value, str):
        return value
    return now_kst()


def _text(response: Any) -> str:
    value = getattr(response, "text", "")
    if callable(value):
        value = value()
    if isinstance(value, str):
        return value
    content = getattr(response, "content", b"")
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _validated_root(text: str) -> ET.Element:
    """Parse+validate a KOPIS XML payload, shared by list and detail endpoints."""
    if not text.strip():
        raise CollectionError("empty_payload")
    if "openapi_serviceresponse" in text.lower():
        raise CollectionError("provider_error")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise CollectionError("invalid_payload") from None
    root_name = _local(root.tag)
    if root_name in {"error", "openapi_serviceresponse", "responseerror"}:
        raise CollectionError("provider_error")
    for node in root.iter():
        name = _local(node.tag)
        value = (node.text or "").strip().lower()
        if name in {"error", "errormessage", "errormsg"} and value:
            raise CollectionError("provider_error")
        if name in {"resultcode", "returnreasoncode"} and value not in {"", "0", "00", "0000", "ok"}:
            raise CollectionError("provider_error")
    return root


def _parse_remote_xml(text: str) -> list[dict]:
    root = _validated_root(text)
    rows: list[dict] = []
    for item in root.iter():
        if _local(item.tag) != "db":
            continue
        rows.append({_local(child.tag): (child.text or "").strip() for child in item})
    return rows


# 상세(/pblprfr/{id}) 응답에서 끌어와 목록 행에 병합하는 필드. map_to_okf가 읽는
# 키와 1:1 대응(원문 그대로 보존 — 가공/추론 없음).
_DETAIL_MERGE_FIELDS = (
    "sty", "prfage", "pcseguidance", "prfruntime",
    "dtguidance", "prfcast", "prfcrew",
    "entrpsnm", "entrpsnmh", "entrpsnmp", "entrpsnma", "entrpsnms",
    "relatenm", "relateurl",
)


def _compose_detail_description(n: dict) -> str | None:
    """sty(줄거리)가 비어 있는 상세 응답에서 원문 상세 필드만을 조합한 사실 요약.

    목록 전용 행(상세 필드가 전혀 없는 행)에서는 None을 반환해 목록 필드를 상세처럼
    위장하지 않는다(AC-F2.12). 또한 체험/가족 분류 어휘를 오염시키지 않는 중립
    필드 라벨만 사용한다.
    """
    detail_keys = (
        "dtguidance", "prfcast", "prfage", "prfruntime", "pcseguidance",
        "relatenm", "entrpsnm", "entrpsnmh", "entrpsnmp", "entrpsnms",
    )
    if not any(str(n.get(k) or "").strip() for k in detail_keys):
        return None
    parts: list[str] = []
    if n.get("genrenm"):
        parts.append(f"장르: {str(n['genrenm']).strip()}")
    if n.get("fcltynm"):
        parts.append(f"공연장: {str(n['fcltynm']).strip()}")
    if n.get("dtguidance"):
        parts.append(f"공연시간 안내: {str(n['dtguidance']).strip()}")
    if n.get("prfage"):
        parts.append(f"관람연령: {str(n['prfage']).strip()}")
    if n.get("prfruntime"):
        parts.append(f"런타임: {str(n['prfruntime']).strip()}")
    if n.get("pcseguidance"):
        parts.append(f"티켓가격: {str(n['pcseguidance']).strip()}")
    cast = str(n.get("prfcast") or "").strip()
    if cast and cast not in {"해당없음", "-"}:
        parts.append(f"출연진: {cast}")
    if n.get("relatenm"):
        parts.append(f"예매처: {str(n['relatenm']).strip()}")
    organizer = (
        n.get("entrpsnmh") or n.get("entrpsnm") or n.get("entrpsnmp") or n.get("entrpsnms")
    )
    if organizer and str(organizer).strip() not in {"해당없음", "-"}:
        parts.append(f"주최·주관: {str(organizer).strip()}")
    return " · ".join(parts) if parts else None


def _flatten_detail_item(item: ET.Element) -> dict:
    """<db> 1건 → 평탄 dict. relates/relate(복수 가능)는 첫 건의 relatenm/relateurl만 채택.

    실 API 응답(2026-10-15 bounded probe 확인)에서 relate가 2건 이상인 레코드가
    존재한다(예: NHN티켓링크 + 네이버N예약) — 문서 순서상 첫 항목을 단일 예약
    제공자로 채택한다(발명 없음, 임의 선택 명시).
    """
    row: dict[str, str] = {}
    for child in item:
        tag = _local(child.tag)
        if tag == "relates":
            relate = next(iter(child), None)
            if relate is not None:
                for sub in relate:
                    row[_local(sub.tag)] = (sub.text or "").strip()
            continue
        row[tag] = (child.text or "").strip()
    return row


def _parse_detail_xml(text: str) -> dict:
    """상세 응답 → 평탄 dict(없으면 빈 dict). 목록 파서와 동일 검증 공유."""
    root = _validated_root(text)
    for item in root.iter():
        if _local(item.tag) == "db":
            return _flatten_detail_item(item)
    return {}


# "90분" / "1시간 30분" / "2시간" 처럼 숫자+시간·분 단위만으로 이뤄진 경우에만
# ISO 8601 duration을 파생한다. "약 90분(인터미션 포함)" 등 수식어·부가설명이
# 붙으면 모호하다고 보고 duration_text(원문)만 유지한다(발명 금지).
_DURATION_RE = re.compile(r"^(?:(\d+)\s*시간)?\s*(?:(\d+)\s*분)?$")


# pcseguidance 원문 예(실 API, 2026-10-15 bounded probe): "전석무료", "전석 30,000원",
# "전석 33,000원", "R석 143,000원,S석 132,000원"(복수 등급). canonical price는 무료
# 단일판정 또는 단일 금액일 때만 파생 — 복수 등급/모호 문구는 None(발명 금지,
# 임의 등급 선택·0원 대체 금지). 원문은 price_text로 항상 보존.
_PRICE_FREE_RE = re.compile(r"^(?:전석\s*)?무료$")
_PRICE_SINGLE_RE = re.compile(r"^(?:전석\s*)?([\d,]+)\s*원$")


def _parse_canonical_price(text: str | None) -> str | int | None:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    if _PRICE_FREE_RE.fullmatch(cleaned):
        return "free"
    match = _PRICE_SINGLE_RE.fullmatch(cleaned)
    if match:
        try:
            return int(match.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def _parse_duration_iso(text: str | None) -> str | None:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    match = _DURATION_RE.fullmatch(cleaned)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    if not hours and not minutes:
        return None
    out = "PT"
    if hours:
        out += f"{hours}H"
    if minutes:
        out += f"{minutes}M"
    return out


def _detail_cache_path():
    from scripts.common.config import ROOT

    return ROOT / "knowledge" / _DETAIL_CACHE_FILENAME


def _load_detail_cache() -> dict:
    try:
        payload = json.loads(_detail_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_detail_cache(cache: dict) -> None:
    try:
        _detail_cache_path().write_text(
            json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        # 조용히 삼키지 않는다 — 저장 실패 시 다음 실행의 캐시 재사용이 깨지므로
        # (상한 내 신규 호출만 늘어날 뿐 수집은 계속됨) 가시화한다. 비밀/경로는
        # 로그에 남기지 않는다(타입명만).
        log.warning("[kopis] detail cache 저장 실패: %s", type(exc).__name__)


def _cache_entry_fresh(entry: Any, *, now: dt.datetime, ttl_hours: float) -> bool:
    if not isinstance(entry, dict):
        return False
    fetched_at = entry.get("fetched_at")
    if not isinstance(fetched_at, str):
        return False
    try:
        fetched = dt.datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=KST)
    return (now - fetched) <= dt.timedelta(hours=max(0.0, ttl_hours))


def _row_ended(row: dict) -> bool:
    return (row.get("prfstate") or "").strip() == "공연완료"


def _row_family_priority(row: dict) -> bool:
    """현재 목록 응답만으로 가족/아동 우선도를 추정하는 가벼운 신호.

    KOPIS 목록 API는 실제 아동공연 플래그(상세의 `child` 필드)를 주지 않으므로
    (2026-10-15 bounded probe로 확인됨) 상세 호출 이전에는 제목만으로 판별 가능.
    build_index.derive_family_flags와 같은 어휘를 재사용해 별도 2번째 분류
    정책을 만들지 않는다 — 상세 병합 후 최종 family_friendly 판정은 그 함수가
    전담(여기서는 상한 내 호출 순서를 정하는 용도로만 사용).
    """
    from scripts.build.build_index import derive_family_flags

    family, _ = derive_family_flags(
        age=None, age_bands=None, audience=None,
        name=row.get("prfnm"), description=None, themes=None,
    )
    return family


def _detail_priority_key(row: dict) -> tuple[int, int]:
    return (1 if _row_ended(row) else 0, 0 if _row_family_priority(row) else 1)


class KopisAdapter(SourceAdapter):
    key = "kopis"
    fmt = "xml"

    def __init__(
        self,
        offline: bool | None = None,
        *,
        sender: Callable[[str, dict, float], Any] | None = None,
        client: Any | None = None,
        clock: Callable[[], Any] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(offline)
        self.api_key = _normalize_api_key(self.api_key)
        auth = self.cfg.get("auth") or {}
        self._auth_param = str(auth.get("param") or "service")
        self.sender = sender
        self.client = client
        self.clock = clock
        self.sleep = sleep or time.sleep
        self.snapshot_complete = False
        self.complete = False
        self.truncated = False
        self.collection_metadata: dict[str, Any] = {}

    def _setting(self, name: str, default: Any) -> Any:
        scoped = self.cfg.get("collection") or {}
        return scoped.get(name, self.cfg.get(name, default))

    def _transport(self, sender: Callable[[str, dict, float], Any] | None = None) -> Callable[[], Any]:
        selected = sender or self.sender
        if selected is not None:
            return lambda: selected(self._request_url, self._request_params, self._timeout)
        client = self.client
        if client is not None:
            return lambda: client.get(
                self._request_url, params=self._request_params, timeout=self._timeout
            )
        import httpx

        return lambda: httpx.get(
            self._request_url, params=self._request_params, timeout=self._timeout,
            follow_redirects=True,  # www.kopis.or.kr → kopis.or.kr 301 대응
        )

    def _request(self, url: str, params: dict, sender: Callable[[str, dict, float], Any] | None) -> Any:
        self._request_url = url
        self._request_params = params
        retry_exceptions: tuple[type[BaseException], ...]
        try:
            import httpx

            retry_exceptions = (httpx.TransportError, TimeoutError, OSError)
        except ImportError:  # pragma: no cover - httpx is a declared dependency
            retry_exceptions = (TimeoutError, OSError)
        try:
            return request_with_retry(
                self._transport(sender),
                retries=self._retries,
                backoff=self._backoff,
                retry_exceptions=retry_exceptions,
                sleep=self.sleep,
            )
        except HttpError as exc:
            raise CollectionError("http_error", exc.status) from None
        except Exception as exc:
            raise CollectionError(f"transport_{type(exc).__name__}") from None

    def _fetch_remote(
        self,
        sender: Callable[[str, dict, float], Any] | None = None,
        *,
        clock: Callable[[], Any] | None = None,
    ) -> list[dict]:
        if not self.api_key and sender is None and self.sender is None and self.client is None:
            raise CollectionError("missing_credentials")
        clock = clock or self.clock
        self.clock = clock
        start = _clock_date(clock)
        window_days = max(1, min(int(self._setting("window_days", 90)), 366))
        end = start + dt.timedelta(days=window_days - 1)
        page_size = max(1, min(int(self._setting("page_size", 100)), 100))
        quota = max(1, int(self.cfg.get("rate_limit_per_day", 3) or 3))
        max_pages = max(1, min(int(self._setting("max_pages", 3)), 100, quota))
        self._timeout = float(self._setting("request_timeout_seconds", 20))
        self._retries = max(0, min(int(self._setting("retries", 2)), 5))
        self._backoff = max(0.0, float(self._setting("backoff_seconds", 0.5)))
        endpoint = str((self.cfg.get("endpoints") or {}).get("list", "/pblprfr"))
        path = endpoint.split("?", 1)[0].rstrip("/")
        url = f"{str(self.cfg.get('base_url', '')).rstrip('/')}/{path.lstrip('/')}"
        self.collection_metadata = {
            "window_start": start.strftime("%Y%m%d"),
            "window_end": end.strftime("%Y%m%d"),
            "page_size": page_size,
            "max_pages": max_pages,
            "pages_fetched": 0,
            "provider_total": None,
            "truncated": False,
            "complete": False,
        }
        rows: list[dict] = []
        seen_ids: set[str] = set()
        page_signatures: set[str] = set()
        for page in range(1, max_pages + 1):
            params = {
                self._auth_param: self.api_key,
                "stdate": start.strftime("%Y%m%d"),
                "eddate": end.strftime("%Y%m%d"),
                "cpage": page,
                "rows": page_size,
            }
            try:
                response = self._request(url, params, sender)
            except CollectionError as exc:
                if exc.status != 400:
                    raise
                # KOPIS 간헐적 400 스로틀 대응: 동일 페이지 1회만 재시도.
                self.sleep(2.0)
                response = self._request(url, params, sender)
            page_rows = _parse_remote_xml(_text(response))
            signature = json.dumps(page_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if signature in page_signatures:
                raise CollectionError("repeating_page")
            page_signatures.add(signature)
            self.collection_metadata["pages_fetched"] = page
            for row in page_rows:
                row_id = str(row.get("mt20id") or "").strip()
                if row_id and row_id in seen_ids:
                    continue
                if row_id:
                    seen_ids.add(row_id)
                rows.append(row)
            if len(page_rows) < page_size:
                self.complete = True
                break
            if page == max_pages:
                self.truncated = True
                self.collection_metadata["truncated"] = True
        self.complete = self.complete or not self.truncated
        self.collection_metadata["complete"] = self.complete
        self.collection_metadata["empty"] = not rows
        self._attach_details(rows, sender)
        if sender is None and self.sender is None and self.client is None and self.api_key:
            self._attach_venue_geo(rows)
        return rows

    def _attach_details(
        self,
        rows: list[dict],
        sender: Callable[[str, dict, float], Any] | None = None,
    ) -> dict:
        """목록 행에 상세(/pblprfr/{id})를 병합. 실행당 신규 호출 상한 + 만료 캐시 재사용.

        AC-F2.12: 상세 조회는 기존 무료 API 예산 안에서 실행별 상한(기본 0=비활성,
        운영 설정 sources.yaml에서 40)과 만료 캐시 재사용을 적용하며 원격 실패를
        숨기지 않는다 — 실패한 행은 목록에서 얻은 필드만 그대로 유지한다(발명 금지).
        """
        max_calls = max(0, int(self._setting("detail_max_calls_per_run", _DETAIL_MAX_CALLS_DEFAULT)))
        stats = {"candidates": 0, "cached": 0, "fetched": 0, "failed": 0, "capped": 0, "stale": 0}
        self.collection_metadata["detail"] = stats
        if max_calls <= 0 or not rows:
            return stats

        persist = sender is None and self.sender is None and self.client is None
        cache = _load_detail_cache() if persist else {}
        ttl_hours = float(self._setting("detail_cache_ttl_hours", _DETAIL_CACHE_TTL_HOURS_DEFAULT))
        now = _clock_datetime(self.clock)

        endpoint = str((self.cfg.get("endpoints") or {}).get("detail", "/pblprfr/{id}"))
        base = str(self.cfg.get("base_url", "")).rstrip("/")

        candidates = [row for row in rows if str(row.get("mt20id") or "").strip()]
        candidates.sort(key=_detail_priority_key)
        stats["candidates"] = len(candidates)

        fresh_calls = 0
        for row in candidates:
            mt20id = str(row["mt20id"]).strip()
            cached = cache.get(mt20id)
            stale_entry = cached if isinstance(cached, dict) else None
            if stale_entry is not None and _cache_entry_fresh(stale_entry, now=now, ttl_hours=ttl_hours):
                self._merge_detail_fields(row, stale_entry)
                stats["cached"] += 1
                continue
            if fresh_calls >= max_calls:
                # 신규 호출 상한 도달. upsert가 frontmatter 전체를 교체하므로,
                # 과거 성공분을 버리면 알던 offers/price_text/description이
                # 통째로 사라진다 — 만료됐더라도 있던 캐시를 그대로 서빙해 보존.
                # 신선한 fetched_at으로 갱신하지 않음(다음 실행에도 여전히 만료로
                # 보여 재시도 기회가 남는다 — stale을 fresh로 위장하지 않는다).
                if stale_entry:
                    self._merge_detail_fields(row, stale_entry)
                    stats["stale"] += 1
                stats["capped"] += 1
                continue
            fresh_calls += 1
            path = endpoint.split("?", 1)[0].replace("{id}", urllib.parse.quote(mt20id, safe=""))
            url = f"{base}/{path.lstrip('/')}"
            params = {self._auth_param: self.api_key}
            try:
                response = self._request(url, params, sender)
                detail = _parse_detail_xml(_text(response))
            except CollectionError:
                detail = None
            if not detail:
                # 원격 갱신 실패. 신선한 provider 결과가 없을 때만 과거 성공분으로
                # 대체한다 — 성공 시엔 provider의 최신 상태(필드 제거 포함)가
                # 항상 이긴다(여기 도달하지 않음, 발명 금지는 그대로 유지).
                if stale_entry:
                    self._merge_detail_fields(row, stale_entry)
                    stats["stale"] += 1
                stats["failed"] += 1
                continue
            entry = {**{k: v for k, v in detail.items() if k in _DETAIL_MERGE_FIELDS and v},
                     "fetched_at": now.isoformat()}
            cache[mt20id] = entry
            self._merge_detail_fields(row, entry)
            stats["fetched"] += 1
        if persist:
            _save_detail_cache(cache)
        return stats

    @staticmethod
    def _merge_detail_fields(row: dict, detail: dict) -> None:
        for field in _DETAIL_MERGE_FIELDS:
            value = detail.get(field)
            if value:
                row[field] = value

    def _venue_request(self, path: str, params: dict,
                       sender: Callable[[str, dict, float], Any] | None = None) -> list[dict]:
        base = str(self.cfg.get("base_url", "")).rstrip("/") or "https://www.kopis.or.kr/openApi/restful"
        url = f"{base}/{path.lstrip('/')}"
        try:
            response = self._request(url, params, sender)
        except CollectionError:
            return []
        try:
            return _parse_remote_xml(_text(response))
        except CollectionError:
            return []

    def _attach_venue_geo(self, rows: list[dict],
                          sender: Callable[[str, dict, float], Any] | None = None) -> dict:
        """공연장명 → 시설검색+상세로 실제 좌표 부착. 결과는 영속 캐시(부정 포함).

        반환: {"resolved": n, "missed": m, "cached": c} 통계.
        """
        cache = _load_venue_cache()
        names: list[str] = []
        for row in rows:
            venue = str(row.get("fcltynm") or "").strip()
            if venue and venue not in names:
                names.append(venue)
        stats = {"resolved": 0, "missed": 0, "cached": 0, "capped": 0}
        fresh = 0  # 이번 실행의 신규 조회(쿼터 보호 대상)
        for venue in names:
            hit = cache.get(venue)
            if isinstance(hit, dict) and not hit.get("miss"):
                stats["cached"] += 1
                continue
            if isinstance(hit, dict) and hit.get("miss"):
                stats["missed"] += 1
                continue
            if fresh >= _VENUE_NEW_LIMIT:
                stats["capped"] += 1
                continue
            fresh += 1
            geo = None
            for query in _venue_search_names(venue):
                found = self._venue_request("prfplc", {
                    self._auth_param: self.api_key,
                    "cpage": 1, "rows": 10, "shprfnmfct": query,
                }, sender)
                picked = _pick_facility(found, venue)
                if picked and picked.get("mt10id"):
                    detail = self._venue_request(f"prfplc/{str(picked['mt10id']).strip()}", {
                        self._auth_param: self.api_key,
                    }, sender)
                    if detail:
                        geo = _geo_from_facility(detail[0])
                    break
            if geo:
                cache[venue] = {**geo, "venue": venue}
                stats["resolved"] += 1
            else:
                cache[venue] = {"miss": True}
                stats["missed"] += 1
        _save_venue_cache(cache)
        for row in rows:
            venue = str(row.get("fcltynm") or "").strip()
            hit = cache.get(venue) if venue else None
            if isinstance(hit, dict) and not hit.get("miss") and hit.get("lat") is not None:
                row["_venue_geo"] = hit
        self.collection_metadata["venue_geo"] = stats
        return stats

    def _now(self) -> str:
        return _clock_iso(self.clock)

    def map_to_okf(self, n: dict) -> dict | None:
        mt20id = n.get("mt20id")
        name = n.get("prfnm")
        if not mt20id or not name:
            return None
        genre = (n.get("genrenm") or "").strip()
        theme, event_type = _GENRE_THEME.get(genre, ("공연", "Event"))
        start = _kst_date(n.get("prfpdfrom", ""))
        end = _kst_date(n.get("prfpdto", ""), end=True)
        if not start:
            return None
        event = {
            "id": f"kopis:{mt20id}",
            "name": name,
            "event_type": event_type,
            "themes": [theme],
            "start_date": start,
            "status": _STATE.get((n.get("prfstate") or "").strip(), "Scheduled"),
            "attendance_mode": "Offline",
            "location": {
                "name": n.get("fcltynm") or None,
                "sido": canonical_sido(n.get("area")),
            },
            "url": f"https://www.kopis.or.kr/por/db/pblprfr/pblprfrView.do?mt20id={mt20id}",
            "source": self.key,
            "source_url": f"{self.cfg.get('base_url','')}/pblprfr/{mt20id}",
            "fetched_at": self._now(),
        }
        venue_geo = n.get("_venue_geo") if isinstance(n.get("_venue_geo"), dict) else None
        if venue_geo:
            event["location"]["address"] = venue_geo.get("address")
            event["location"]["lat"] = venue_geo.get("lat")
            event["location"]["lng"] = venue_geo.get("lng")
            event["location"]["geo_precision"] = "venue"
        if end:
            event["end_date"] = end
        if n.get("poster"):
            event["image"] = n["poster"]
        # 상세(/pblprfr/{id}) 병합 필드 — 원문 그대로 보존, 발명 없음(AC-F2.11/.12).
        if n.get("sty"):
            event["description"] = n["sty"]
        else:
            detail_desc = _compose_detail_description(n)
            if detail_desc:
                event["description"] = detail_desc
        if n.get("prfage"):
            event["age"] = n["prfage"]
        if n.get("pcseguidance"):
            event["price_text"] = n["pcseguidance"]
            canonical_price = _parse_canonical_price(n["pcseguidance"])
            if canonical_price is not None:
                event["price"] = canonical_price
        if n.get("prfruntime"):
            event["duration_text"] = n["prfruntime"]
            iso_duration = _parse_duration_iso(n["prfruntime"])
            if iso_duration:
                event["duration"] = iso_duration
        organizer = (
            n.get("entrpsnmh") or n.get("entrpsnm") or n.get("entrpsnmp") or n.get("entrpsnms")
        )
        if organizer:
            event["organizer"] = organizer
        if n.get("relateurl"):
            # 제공자가 명시한 예약 URL만 offers.url — 정보 URL(위 "url")에서 추론 금지.
            event["offers"] = {"url": n["relateurl"]}
        event["content_hash"] = content_hash(event)
        return event


if __name__ == "__main__":
    rows = KopisAdapter().collect()
    print(f"kopis: {len(rows)} OKF 레코드")
    for r in rows[:3]:
        print(" -", r["id"], r["name"], r["location"]["sido"])
