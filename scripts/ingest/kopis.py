"""KOPIS(공연예술통합전산망) 어댑터. 공연/전시 목록 XML → OKF Event."""
from __future__ import annotations

import datetime as dt
import json
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any

from scripts.common.config import canonical_sido
from scripts.common.dates import KST, combine_kst
from scripts.common.http import HttpError, request_with_retry
from scripts.common.okf import content_hash
from scripts.ingest.base import SourceAdapter, now_kst

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


def _parse_remote_xml(text: str) -> list[dict]:
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
    rows: list[dict] = []
    for item in root.iter():
        if _local(item.tag) != "db":
            continue
        rows.append({_local(child.tag): (child.text or "").strip() for child in item})
    return rows


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
        if sender is None and self.sender is None and self.client is None and self.api_key:
            self._attach_venue_geo(rows)
        return rows

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
        event["content_hash"] = content_hash(event)
        return event


if __name__ == "__main__":
    rows = KopisAdapter().collect()
    print(f"kopis: {len(rows)} OKF 레코드")
    for r in rows[:3]:
        print(" -", r["id"], r["name"], r["location"]["sido"])
