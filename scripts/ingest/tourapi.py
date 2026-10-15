"""한국관광공사 TourAPI 4.0 searchFestival 어댑터. 지역축제 JSON → OKF Event."""
from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, unquote, urlsplit

from scripts.common.config import canonical_sido
from scripts.common.dates import KST, combine_kst
from scripts.common.http import HttpError, request_with_retry
from scripts.common.okf import content_hash
from scripts.ingest.base import SourceAdapter, now_kst


class CollectionError(RuntimeError):
    """Remote collection failed without retaining request credentials in its text."""

    def __init__(self, reason: str, status: int | None = None) -> None:
        self.reason = reason
        self.status = status
        suffix = f" (HTTP {status})" if isinstance(status, int) else ""
        super().__init__(f"TourAPI collection {reason}{suffix}")

def _normalize_api_key(value: str) -> str:
    if not isinstance(value, str):
        return value
    return unquote(value.strip())


def _kst_date(value: str, end: bool = False) -> str | None:
    """'20260718' → ISO 8601(KST)."""
    value = (value or "").strip()
    try:
        d = dt.datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return None
    return combine_kst(d, end)


def _num(value) -> float | None:
    try:
        f = float(value)
        return f if f else None
    except (TypeError, ValueError):
        return None


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
    json_method = getattr(response, "json", None)
    if callable(json_method):
        try:
            return json.dumps(json_method(), ensure_ascii=False)
        except (TypeError, ValueError):
            pass
    return ""


class _HrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value.strip())


def _homepage(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    parser = _HrefParser()
    try:
        parser.feed(raw)
    except (ValueError, TypeError):
        parser.hrefs = []
    candidates = parser.hrefs or [raw]
    for candidate in candidates:
        parts = urlsplit(candidate.strip())
        if parts.scheme != "https" or not parts.hostname:
            continue
        if parts.username or parts.password:
            continue
        return candidate.strip()
    return None


def _parse_page(text: str) -> tuple[list[dict], int | None]:
    if not text.strip():
        raise CollectionError("empty_payload")
    if text.lstrip().startswith("<") or "openapi_serviceresponse" in text.lower():
        raise CollectionError("provider_error")
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise CollectionError("invalid_payload") from None
    if not isinstance(data, dict):
        raise CollectionError("invalid_payload")
    response = data.get("response")
    if not isinstance(response, dict):
        raise CollectionError("invalid_payload")
    header = response.get("header")
    if not isinstance(header, dict):
        raise CollectionError("invalid_payload")
    code = str(header.get("resultCode", "")).strip()
    if code != "0000":
        raise CollectionError("provider_error")
    body = response.get("body")
    if not isinstance(body, dict):
        raise CollectionError("invalid_payload")
    total_raw = body.get("totalCount")
    total: int | None = None
    if total_raw not in (None, ""):
        try:
            total = int(total_raw)
        except (TypeError, ValueError):
            raise CollectionError("invalid_total") from None
        if total < 0:
            raise CollectionError("invalid_total")
    items_block = body.get("items")
    if items_block is None:
        return [], total
    if not isinstance(items_block, dict):
        raise CollectionError("invalid_items")
    items = items_block.get("item", [])
    if items is None:
        return [], total
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise CollectionError("invalid_items")
    return items, total


class TourApiAdapter(SourceAdapter):
    key = "tourapi"
    fmt = "json"

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
        self._auth_param = str(auth.get("param") or "serviceKey")
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
            self._request_url, params=self._request_params, timeout=self._timeout
        )

    def _request(self, url: str, params: dict, sender: Callable[[str, dict, float], Any] | None) -> Any:
        self._request_url = url
        self._request_params = params
        try:
            import httpx

            retry_exceptions: tuple[type[BaseException], ...] = (
                httpx.TransportError,
                TimeoutError,
                OSError,
            )
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
        endpoint = str((self.cfg.get("endpoints") or {}).get("festival", "/searchFestival2"))
        path = endpoint.split("?", 1)[0].rstrip("/")
        url = f"{str(self.cfg.get('base_url', '')).rstrip('/')}/{path.lstrip('/')}"
        self.collection_metadata = {
            "window_start": start.strftime("%Y%m%d"),
            "window_end": end.strftime("%Y%m%d"),
            "page_size": page_size,
            "max_pages": max_pages,
            "pages_fetched": 0,
            "truncated": False,
            "complete": False,
        }
        rows: list[dict] = []
        seen_ids: set[str] = set()
        page_signatures: set[str] = set()
        provider_total: int | None = None
        for page in range(1, max_pages + 1):
            params = {
                self._auth_param: self.api_key,
                "eventStartDate": start.strftime("%Y%m%d"),
                "numOfRows": page_size,
                "pageNo": page,
                "MobileOS": "ETC",
                "MobileApp": "hop",
                "_type": "json",
            }
            response = self._request(url, params, sender)
            page_rows, page_total = _parse_page(_text(response))
            if page_total is not None:
                if provider_total is not None and page_total != provider_total:
                    raise CollectionError("inconsistent_total")
                provider_total = page_total
                self.collection_metadata["provider_total"] = page_total
            signature = json.dumps(page_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if signature in page_signatures:
                raise CollectionError("repeating_page")
            page_signatures.add(signature)
            self.collection_metadata["pages_fetched"] = page
            for row in page_rows:
                row_id = str(row.get("contentid") or "").strip()
                if row_id and row_id in seen_ids:
                    continue
                if row_id:
                    seen_ids.add(row_id)
                rows.append(row)
            collected = len(rows)
            if provider_total is not None and collected >= provider_total:
                self.complete = True
                break
            if len(page_rows) < page_size:
                if provider_total is not None and collected < provider_total:
                    raise CollectionError("partial_page")
                self.complete = True
                break
            if page == max_pages:
                self.truncated = provider_total is None or collected < provider_total
                self.collection_metadata["truncated"] = self.truncated
        self.collection_metadata["empty"] = not rows
        self.complete = self.complete or not self.truncated
        self.collection_metadata["complete"] = self.complete
        return rows

    def _now(self) -> str:
        return _clock_iso(self.clock)

    def _parse_json(self, text: str) -> list[dict]:
        data = json.loads(text)
        items = (
            data.get("response", {})
            .get("body", {})
            .get("items", {})
            .get("item", [])
        )
        if isinstance(items, dict):  # 단건 응답
            items = [items]
        return items

    def map_to_okf(self, n: dict) -> dict | None:
        cid = n.get("contentid")
        name = n.get("title")
        start = _kst_date(n.get("eventstartdate", ""))
        if not cid or not name or not start:
            return None
        addr = (n.get("addr1") or "").strip()
        sido = canonical_sido(addr.split()[0]) if addr else None
        lat, lng = _num(n.get("mapy")), _num(n.get("mapx"))
        source_url = (
            f"{self.cfg.get('base_url', '').rstrip('/')}/detailCommon2?"
            f"{urlencode({'contentId': str(cid)})}"
        )
        homepage = next(
            (
                _homepage(n.get(key))
                for key in ("homepage", "homepage1", "homepage2")
                if _homepage(n.get(key))
            ),
            None,
        )
        nonpublishable_url = homepage is None
        event = {
            "id": f"tourapi:{cid}",
            "name": name,
            "event_type": "Festival",
            "themes": ["축제"],
            "start_date": start,
            "status": "Scheduled",
            "attendance_mode": "Offline",
            "location": {
                "name": n.get("title"),
                "sido": sido,
                "address": addr or None,
            },
            "url": homepage or source_url,
            "source": self.key,
            "source_url": source_url,
            "fetched_at": self._now(),
        }
        if nonpublishable_url:
            event["x_nonpublishable_url"] = True
        end = _kst_date(n.get("eventenddate", ""), end=True)
        if end:
            event["end_date"] = end
        if lat and lng:
            event["location"]["lat"] = lat
            event["location"]["lng"] = lng
        if n.get("firstimage"):
            event["image"] = n["firstimage"]
        event["content_hash"] = content_hash(event)
        return event


if __name__ == "__main__":
    rows = TourApiAdapter().collect()
    print(f"tourapi: {len(rows)} OKF 레코드")
    for r in rows[:3]:
        print(" -", r["id"], r["name"], r["location"]["sido"])
