"""보안 링크 정책과 빌드 시점 검증 결과의 순수 공개 판정.

원본 행사 ``url``은 flat-file에 그대로 남고, 이 모듈은 검증 캐시가 최근
7일 이내일 때만 최종 HTTPS 주소를 공개한다. 네트워크 검사는
``scripts.ops.check_links``가 담당하며 이 모듈의 공개 판정 함수는 네트워크를
호출하지 않는다.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import SplitResult, parse_qsl, quote, urljoin, urlsplit, urlunsplit

from scripts.common.config import ROOT, search_config

CACHE_PATH = ROOT / "web" / "public" / "data" / "link-checks.json"
LINK_STATUSES = {"unchecked", "verified", "unverified", "blocked", "dead"}
DEFAULT_TTL = timedelta(days=7)

_POLICY_REASONS = {
    "empty": "원본 URL 없음",
    "invalid": "유효하지 않은 URL",
    "scheme": "HTTPS만 허용",
    "credentials": "자격증명 URL 차단",
    "port": "비표준 포트 차단",
    "host": "호스트 없음",
    "placeholder": "예시·테스트·로컬 호스트 차단",
    "trust": "신뢰 도메인 아님",
    "private": "사설·예약 IP 차단",
    "dns": "DNS 확인 실패",
    "api": "공개 페이지가 아닌 API 주소 차단",
    "credential_query": "자격증명 쿼리 매개변수 차단",
}

# Query parameter names (normalized: lowercased, separators stripped) that
# indicate an embedded credential/secret rather than a public identifier.
# Public event identifiers (id, contentId, eventId, mt20id, ...) never match
# this set, so they stay valid; only secret-shaped keys are rejected before
# any DNS/send/cache step touches the URL.
_CREDENTIAL_QUERY_KEYS = frozenset({
    "servicekey", "apikey", "apisecret", "accesstoken", "accesskey",
    "secretkey", "clientsecret", "authtoken", "authkey", "auth",
    "authorization", "token", "secret", "password", "passwd", "privatekey",
    "sessiontoken", "refreshtoken", "idtoken", "signature", "sig", "key",
})
_MAX_QUERY_PARAMS_SCANNED = 64


def _normalised_query_key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _has_credential_query(query: str) -> bool:
    if not query:
        return False
    # max_num_fields bounds parse_qsl's own cost: it counts separators and
    # raises *before* unquoting/decoding any field once the count is over
    # the limit, so a pathological query string can never turn this into
    # unbounded parsing work. Slicing an already-fully-parsed list (the
    # previous approach) does not have this property: parse_qsl would
    # still decode every field up front, so a credential-shaped key placed
    # past the scan window - after enough padding params - would parse
    # successfully but never actually be scanned. Any ValueError (field
    # count over the limit, or any other parse failure) is treated as
    # unsafe: a query too large/odd to confidently scan is rejected rather
    # than silently admitting whatever its unscanned tail might hide.
    try:
        pairs = parse_qsl(query, keep_blank_values=True, max_num_fields=_MAX_QUERY_PARAMS_SCANNED)
    except ValueError:
        return True
    return any(_normalised_query_key(key) in _CREDENTIAL_QUERY_KEYS for key, _ in pairs)


class LinkPolicyError(ValueError):
    """URL 정책 위반(네트워크 요청 전에 발생)."""

    def __init__(self, reason: str, *, blocked: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.blocked = blocked


class LinkDnsError(OSError):
    """DNS 해석 실패(차단이 아닌 검증 불확실성)."""



class LinkDeadlineExceeded(TimeoutError):
    """The checker wall-clock budget expired before another operation."""

class LinkResponse:
    """검사 transport가 반환하는 최소 응답 계약."""

    def __init__(
        self,
        status_code: int,
        headers: Mapping[str, str] | None = None,
        body: bytes | str = b"",
    ) -> None:
        self.status_code = int(status_code)
        self.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        self.body = body.encode("utf-8", "replace") if isinstance(body, str) else bytes(body)


# Tests and callers may use this alias as a concise injected transport type.
LinkSender = Callable[[str, str, str, str, float], LinkResponse]
Resolver = Callable[[str, int], Iterable[Any]]


def _utc_now(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(now: datetime | None = None) -> str:
    return _utc_now(now).isoformat().replace("+00:00", "Z")


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _policy() -> dict[str, Any]:
    cfg = search_config()
    value = cfg.get("link_policy", {}) if isinstance(cfg, dict) else {}
    return value if isinstance(value, dict) else {}

def _proof_ttl() -> timedelta:
    try:
        days = float(_policy().get("proof_ttl_days", 7))
    except (TypeError, ValueError):
        days = 7
    return timedelta(days=max(0, days))


def _normalise_domain_list(domains: Iterable[str] | None) -> tuple[str, ...]:
    result: list[str] = []
    for item in domains or []:
        if not isinstance(item, str):
            continue
        domain = item.strip().lower().rstrip(".")
        if domain and domain not in result:
            result.append(domain)
    return tuple(result)


def _booking_trusted_domains() -> tuple[str, ...]:
    """Curated, evidence-based booking-vendor hosts (``role="booking"`` only).

    Separate from ``trusted_domains`` (go.kr/or.kr/ac.kr, the unchanged info
    allowlist): real ticket vendors are commercial domains and must never
    widen what an info ``url`` can verify against. Populated from
    ``config/search.yaml``'s ``booking_trusted_domains`` - exact hosts only,
    observed from real KOPIS relates/relateurl responses, never a registrable
    suffix/wildcard.
    """
    cfg = search_config()
    value = cfg.get("booking_trusted_domains", []) if isinstance(cfg, dict) else []
    return _normalise_domain_list(value)


def _trusted_domains(
    trusted_domains: Iterable[str] | None = None, *, role: str = "info"
) -> tuple[str, ...]:
    if trusted_domains is not None:
        return _normalise_domain_list(trusted_domains)
    cfg = search_config()
    base = _normalise_domain_list(cfg.get("trusted_domains", []) if isinstance(cfg, dict) else [])
    if role != "booking":
        return base
    merged = list(base)
    for domain in _booking_trusted_domains():
        if domain not in merged:
            merged.append(domain)
    return tuple(merged)


def _host_allowed(
    host: str, trusted_domains: Iterable[str] | None = None, *, role: str = "info"
) -> bool:
    return any(
        host == domain or host.endswith("." + domain)
        for domain in _trusted_domains(trusted_domains, role=role)
    )


def _placeholder_host(host: str) -> bool:
    labels = set(host.rstrip(".").split("."))
    return bool(labels & {"example", "test", "localhost", "local"}) or host.endswith(".local")


def _safe_ip(value: object) -> bool:
    try:
        parsed = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return False
    # is_global excludes most private, loopback, link-local, multicast,
    # unspecified and reserved ranges. Python treats 192.0.0.0/24's special
    # addresses as global on some versions, so keep that range explicit.
    if not parsed.is_global:
        return False
    special = (
        ipaddress.ip_network("192.0.0.0/24"),
        ipaddress.ip_network("192.0.2.0/24"),
        ipaddress.ip_network("198.18.0.0/15"),
        ipaddress.ip_network("198.51.100.0/24"),
        ipaddress.ip_network("203.0.113.0/24"),
        ipaddress.ip_network("2001:db8::/32"),
        # RFC 6052 NAT64 well-known prefix: Python's is_global does not see
        # through it, so an embedded private/link-local/loopback IPv4 (e.g.
        # ::ffff:169.254.169.254-style metadata or 127.0.0.1) would otherwise
        # pass as a global destination and get the live TLS connection pinned
        # to it.
        ipaddress.ip_network("64:ff9b::/96"),
        # Deprecated "IPv4-compatible IPv6" format (RFC 4291 2.5.5.1, ``::/96``
        # minus ``::``/``::1`` which are already excluded elsewhere): the same
        # embedding problem as NAT64 above - ``::192.168.1.1`` or
        # ``::169.254.169.254`` also reports is_global=True.
        ipaddress.ip_network("::/96"),
    )
    return not any(parsed in network for network in special)


def _booking_http_upgrade_eligible(host: str) -> bool:
    """Exact-host, booking-role-only HTTP->HTTPS upgrade.

    Several evidenced KOPIS relateurl vendors (ticketlink.co.kr,
    ticket.interpark.com, playticket.co.kr, yywc.moonhwain.net) are only ever
    observed as plain ``http://`` from the source API. This upgrades the
    *scheme* alone on the *same* host/path/query - never a host change, never
    applied to an unlisted host, and never applied outside ``role="booking"``.
    Exact membership only (no suffix broadening): a curated host matches
    itself, never a sibling subdomain that happens to share a parent domain.
    """
    return host in _booking_trusted_domains()


def _normalise_url(
    url: object, *, trusted_domains: Iterable[str] | None = None, role: str = "info"
) -> str:
    if not isinstance(url, str) or not url.strip():
        raise LinkPolicyError(_POLICY_REASONS["empty"])
    if url != url.strip() or any(ord(char) < 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise LinkPolicyError(_POLICY_REASONS["invalid"])
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        raise LinkPolicyError(_POLICY_REASONS["invalid"]) from None
    scheme = parsed.scheme.lower()
    if scheme == "http" and role == "booking" and host and _booking_http_upgrade_eligible(host.lower().rstrip(".")):
        scheme = "https"
    if scheme != "https":
        raise LinkPolicyError(_POLICY_REASONS["scheme"])
    if parsed.username is not None or parsed.password is not None:
        raise LinkPolicyError(_POLICY_REASONS["credentials"])
    if _has_credential_query(parsed.query):
        raise LinkPolicyError(_POLICY_REASONS["credential_query"])
    if port not in (None, 443):
        raise LinkPolicyError(_POLICY_REASONS["port"])
    if not host:
        raise LinkPolicyError(_POLICY_REASONS["host"])
    try:
        ascii_host = host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        raise LinkPolicyError(_POLICY_REASONS["invalid"]) from None
    if not ascii_host or _placeholder_host(ascii_host):
        raise LinkPolicyError(_POLICY_REASONS["placeholder"])
    if ascii_host == "apis.data.go.kr" and "detailcommon2" in (parsed.path or "").lower():
        raise LinkPolicyError(_POLICY_REASONS["api"])
    if not _host_allowed(ascii_host, trusted_domains, role=role):
        raise LinkPolicyError(_POLICY_REASONS["trust"])
    try:
        literal = ipaddress.ip_address(ascii_host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise LinkPolicyError(_POLICY_REASONS["private"])
    # Rebuild the netloc so credentials and non-canonical host spellings cannot
    # accidentally survive into a published URL. Percent-encoding preserves
    # existing escapes while making Unicode request targets ASCII-safe.
    netloc = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    path = quote(parsed.path or "/", safe="/%:@-._~!$&'()*+,;=")
    query = quote(parsed.query, safe="/%?:@-._~!$&'()*+,;=")
    fragment = quote(parsed.fragment, safe="/%?:@-._~!$&'()*+,;=")
    rebuilt = SplitResult("https", netloc, path, query, fragment)
    return urlunsplit(rebuilt)


def validate_url(
    url: object, *, trusted_domains: Iterable[str] | None = None, role: str = "info"
) -> tuple[str | None, str | None]:
    """Return a sanitized HTTPS URL and a short policy reason, without I/O.

    ``role="info"`` (default) validates against the unchanged
    ``trusted_domains`` allowlist only. ``role="booking"`` additionally
    allows the curated ``booking_trusted_domains`` vendor hosts (and, only
    for those exact hosts, a same-host HTTP->HTTPS scheme upgrade) - it never
    widens what an info-role caller accepts, and an explicit
    ``trusted_domains`` override (tests) bypasses role-based domain
    resolution entirely while still gating the booking-only HTTP upgrade on
    the real role argument.
    """
    try:
        return _normalise_url(url, trusted_domains=trusted_domains, role=role), None
    except LinkPolicyError as exc:
        return None, exc.reason


def _url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _cache_mapping(checks: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(checks, Mapping):
        return {}
    nested = checks.get("checks")
    return nested if isinstance(nested, Mapping) else checks


def _safe_reason(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = " ".join(value.split())
    return cleaned[:120] or fallback


def _optional_reason(value: object) -> str | None:
    """Like ``_safe_reason`` but preserves "no reason recorded" as ``None``
    instead of substituting a filler string - only meaningful once a link is
    already ``verified``, where a reason is a bonus detail, not evidence."""
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:120] or None


def _fresh(checked_at: object, now: datetime, ttl: timedelta = DEFAULT_TTL) -> bool:
    parsed = _parse_iso(checked_at)
    if parsed is None:
        return False
    age = now - parsed
    return timedelta(0) <= age <= ttl


def _unchecked(reason: str = "검증 기록 없음", checked_at: object = None) -> dict[str, Any]:
    return {
        "link_status": "unchecked",
        "link_checked_at": checked_at if isinstance(checked_at, str) else None,
        "link_url": None,
        "link_reason": _safe_reason(reason, "검증 기록 없음"),
    }


def link_fields(
    url: object,
    checks: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    *,
    role: str = "info",
) -> dict[str, Any]:
    """Purely derive publication fields from one exact original URL and cache.

    ``role`` re-derives trust (and, for ``"booking"``, the same-host HTTP->
    HTTPS upgrade) on *every* call - a cache entry's stored status is never
    trusted blindly across roles. A URL verified only under ``role="booking"``
    (a curated vendor host) always re-validates as ``"blocked"`` when read
    with ``role="info"`` (the unchanged go.kr/or.kr/ac.kr allowlist), even
    though both reads share the exact same ``sha256(url)`` cache entry.
    """
    sanitized, policy_reason = validate_url(url, role=role)
    if policy_reason:
        return {
            "link_status": "blocked",
            "link_checked_at": None,
            "link_url": None,
            "link_reason": policy_reason,
        }
    assert isinstance(url, str) and sanitized is not None
    entry = _cache_mapping(checks).get(_url_key(url))
    if not isinstance(entry, Mapping):
        return _unchecked()
    checked_at = entry.get("link_checked_at")
    current = _utc_now(now)
    if not _fresh(checked_at, current, _proof_ttl()):
        return _unchecked("검증 기록 만료", checked_at)
    status = entry.get("link_status")
    if status not in LINK_STATUSES:
        return _unchecked("검증 기록 형식 오류", checked_at)
    if status != "verified":
        reason = _safe_reason(entry.get("link_reason"), "검증 결과 확인 필요")
        return {
            "link_status": status,
            "link_checked_at": checked_at if isinstance(checked_at, str) else None,
            "link_url": None,
            "link_reason": reason,
        }
    final_url, final_reason = validate_url(entry.get("link_url"), role=role)
    if final_reason or not final_url:
        return {
            "link_status": "blocked",
            "link_checked_at": checked_at if isinstance(checked_at, str) else None,
            "link_url": None,
            "link_reason": "검증 URL 정책 불일치",
        }
    return {
        "link_status": "verified",
        "link_checked_at": checked_at if isinstance(checked_at, str) else None,
        "link_url": final_url,
        "link_reason": _optional_reason(entry.get("link_reason")),
    }


def load_link_checks(path: Path | str | None = None) -> dict[str, Any]:
    """Load the derived cache; a missing/corrupt cache is an offline miss."""
    target = Path(path) if path else CACHE_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, Mapping):
        return {}
    checks = raw.get("checks")
    return dict(checks) if isinstance(checks, Mapping) else dict(raw)


def verified_link(
    event: Mapping[str, Any],
    now: datetime | None = None,
    checks: Mapping[str, Any] | None = None,
) -> str | None:
    """Return the canonical info link only when its proof is fresh+verified
    under ``role="info"`` - the unchanged go.kr/or.kr/ac.kr allowlist."""
    cache = checks if checks is not None else load_link_checks()
    fields = link_fields(event.get("url") if isinstance(event, Mapping) else None, cache, now)
    value = fields.get("link_url")
    return value if fields.get("link_status") == "verified" and isinstance(value, str) else None


def _event_offers_url(event: Mapping[str, Any] | None) -> str | None:
    """Provider-declared booking URL only - never inferred from ``url``."""
    if not isinstance(event, Mapping):
        return None
    offers = event.get("offers")
    if not isinstance(offers, Mapping):
        return None
    url = offers.get("url")
    return url if isinstance(url, str) and url else None


def booking_link_fields(
    event: Mapping[str, Any] | None,
    checks: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Independently derive ``booking_link_*`` from ``offers.url`` alone.

    Same cache, same ``link_fields`` policy function as the info ``url``,
    verified under ``role="booking"`` (curated vendor hosts) - never falls
    back to the event's own info ``url`` when no provider booking
    destination is declared.
    """
    return link_fields(_event_offers_url(event), checks, now, role="booking")


def verified_booking_link(
    event: Mapping[str, Any],
    now: datetime | None = None,
    checks: Mapping[str, Any] | None = None,
) -> str | None:
    """Return the provider's booking URL only when its own proof is
    fresh+verified under ``role="booking"`` - independent of (and never
    borrowed from) the info link's verification."""
    fields = booking_link_fields(event, checks, now)
    value = fields.get("link_url")
    return value if fields.get("link_status") == "verified" and isinstance(value, str) else None


__all__ = [
    "CACHE_PATH",
    "DEFAULT_TTL",
    "LINK_STATUSES",
    "LinkResponse",
    "LinkSender",
    "Resolver",
    "booking_link_fields",
    "check_url",
    "link_fields",
    "load_link_checks",
    "validate_url",
    "verified_booking_link",
    "verified_link",
]


# Network-checking implementation is kept here as a transport-neutral function;
# scripts.ops.check_links owns orchestration, cache writes and its CLI.
def _resolve_default(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise LinkDnsError from exc
    result: list[str] = []
    for info in infos:
        try:
            address = info[4][0]
        except (IndexError, TypeError):
            continue
        if isinstance(address, str) and address not in result:
            result.append(address)
    if not result:
        raise LinkDnsError
    return result
# Bounded concurrency, not a thread pool: a plain daemon Thread is never
# tracked by interpreter shutdown (unlike concurrent.futures.ThreadPoolExecutor,
# whose non-daemon workers are joined by an atexit hook - a wedged
# getaddrinfo() there would hang CLI exit even past our own deadline). The
# semaphore instead caps how many such lookups may be in flight at once; a
# permit is only released by the worker that holds it when its own call
# actually returns, so N already-stuck lookups cannot be "forgotten" by a
# caller that merely gave up waiting. A 5th+ caller while all permits are
# held degrades to a transient DNS failure (unverified, never "dead") bounded
# by the same remaining-deadline math as everything else, and thread count
# can never exceed this fixed cap.
_DNS_CONCURRENCY = 4
_dns_semaphore = threading.Semaphore(_DNS_CONCURRENCY)


def _resolved_addresses(
    host: str,
    resolver: Resolver | None,
    *,
    timeout: float,
    deadline: float | None,
    clock: Callable[[], float],
) -> list[str]:
    worker = resolver or _resolve_default

    def remaining_wait() -> float:
        wait = max(0.01, timeout)
        if deadline is not None:
            remaining = deadline - clock()
            if remaining <= 0:
                raise LinkDeadlineExceeded
            wait = min(wait, remaining)
        return wait

    if not _dns_semaphore.acquire(timeout=remaining_wait()):
        raise LinkDnsError
    done = threading.Event()
    holder: dict[str, Any] = {}

    def resolve_worker() -> None:
        try:
            holder["payload"] = worker(host, 443)
            holder["kind"] = "ok"
        except Exception as exc:  # worker is daemonized; caller maps to safe status
            holder["payload"] = exc
            holder["kind"] = "error"
        finally:
            done.set()
            _dns_semaphore.release()

    threading.Thread(target=resolve_worker, daemon=True).start()
    if not done.wait(timeout=remaining_wait()):
        raise LinkDnsError
    if holder.get("kind") == "error":
        payload = holder.get("payload")
        if isinstance(payload, LinkPolicyError):
            raise payload
        raise LinkDnsError from None
    payload = holder.get("payload")
    result: list[str] = []
    for item in payload or []:
        address: object = item
        if isinstance(item, tuple) and len(item) >= 1:
            address = item[0] if isinstance(item[0], str) else item[-1]
            if isinstance(address, tuple):
                address = address[0]
        if isinstance(address, str) and address not in result:
            result.append(address)
    if not result:
        raise LinkDnsError
    if any(not _safe_ip(address) for address in result):
        raise LinkPolicyError(_POLICY_REASONS["private"])
    return result


def _coerce_response(value: Any) -> LinkResponse:
    if isinstance(value, LinkResponse):
        return value
    status = getattr(value, "status_code", None)
    headers = getattr(value, "headers", None)
    body = getattr(value, "body", getattr(value, "content", b""))
    if isinstance(value, Mapping):
        status = value.get("status_code", value.get("status", status))
        headers = value.get("headers", headers)
        body = value.get("body", value.get("content", body))
    if status is None:
        raise OSError
    return LinkResponse(int(status), headers if isinstance(headers, Mapping) else {}, body or b"")


def _send_with_retry(
    send: LinkSender,
    url: str,
    method: str,
    ip: str,
    host: str,
    timeout: float,
    sleep: Callable[[float], None] | None,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> LinkResponse:
    """Reuse common retry policy while preserving manual redirect responses."""
    from scripts.common.http import HttpError, request_with_retry

    holder: dict[str, LinkResponse] = {}

    def remaining_timeout() -> float:
        remaining = max(0.01, timeout)
        if deadline is not None:
            remaining = min(remaining, deadline - clock())
            if remaining <= 0:
                raise LinkDeadlineExceeded
        return remaining

    def attempt() -> LinkResponse:
        response = _coerce_response(send(url, method, ip, host, remaining_timeout()))
        holder["response"] = response
        # request_with_retry intentionally raises on 3xx; make redirects visible
        # to this caller while still routing transient failures through the helper.
        if 300 <= response.status_code < 400:
            return LinkResponse(200, response.headers, response.body)
        return response

    def bounded_sleep(seconds: float) -> None:
        if deadline is not None and clock() + seconds >= deadline:
            raise LinkDeadlineExceeded
        (sleep or time.sleep)(seconds)

    try:
        request_with_retry(
            attempt,
            retries=1,
            backoff=0.5,
            retry_exceptions=(OSError, TimeoutError),
            sleep=bounded_sleep,
        )
    except HttpError:
        pass
    return holder.get("response", LinkResponse(599))


def _default_send(url: str, method: str, ip: str, host: str, timeout: float) -> LinkResponse:
    # Imported lazily so pure publication imports never touch socket/SSL setup.
    import socket as _socket
    import ssl

    # Headers get their own fixed, generous bound (unrelated to the
    # configured response-body sample size below): this just stops a
    # malicious/broken header stream from growing the buffer forever while
    # we hunt for the terminator.
    _MAX_HEADER_BYTES = 65536
    max_body_bytes = int(_policy().get("max_response_bytes", 65536) or 65536)
    parsed = urlsplit(url)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    context = ssl.create_default_context()
    operation_deadline = time.monotonic() + max(0.01, timeout)
    sock = _socket.create_connection((ip, 443), timeout=max(0.01, timeout))
    tls = context.wrap_socket(sock, server_hostname=host)
    try:
        request = (
            f"{method} {target} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "User-Agent: Notchima-LinkChecker/1\r\n"
            "Accept: */*\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii", "strict")
        tls.sendall(request)
        data = bytearray()
        while b"\r\n\r\n" not in data and len(data) <= _MAX_HEADER_BYTES:
            remaining = operation_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            tls.settimeout(remaining)
            chunk = tls.recv(min(4096, _MAX_HEADER_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        marker = data.find(b"\r\n\r\n")
        if marker < 0 or marker > _MAX_HEADER_BYTES:
            raise OSError
        head = bytes(data[:marker]).split(b"\r\n")
        if not head:
            raise OSError
        try:
            status = int(head[0].split(None, 2)[1])
        except (IndexError, ValueError):
            raise OSError from None
        headers: dict[str, str] = {}
        for line in head[1:]:
            if b":" not in line:
                continue
            key, value = line.split(b":", 1)
            headers[key.decode("latin1").strip().lower()] = value.decode("latin1").strip()
        # Read at most max_body_bytes of the body, then stop - a bounded
        # sample of a large/valid page, not a size cap on what counts as
        # reachable. A real 2xx page bigger than the sample is still
        # verified; this only bounds how much of it this checker buffers.
        body = bytearray(data[marker + 4 :]) if method == "GET" else bytearray()
        if len(body) > max_body_bytes:
            del body[max_body_bytes:]
        while method == "GET" and len(body) < max_body_bytes:
            remaining = operation_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            tls.settimeout(remaining)
            chunk = tls.recv(min(4096, max_body_bytes - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        return LinkResponse(status, headers, bytes(body))
    finally:
        try:
            tls.close()
        except OSError:
            pass


def _result(status: str, checked_at: str, reason: str, link_url: str | None = None) -> dict[str, Any]:
    return {
        "link_status": status,
        "link_checked_at": checked_at,
        "link_url": link_url if status == "verified" else None,
        "link_reason": _safe_reason(reason, "검증 결과 확인 필요"),
    }


def check_url(
    url: object,
    *,
    send: LinkSender | None = None,
    resolver: Resolver | None = None,
    now: datetime | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    deadline: float | None = None,
    role: str = "info",
) -> dict[str, Any]:
    """Check one URL with bounded GET redirects and pinned public IP.

    ``role`` gates trust (and the booking-only same-host HTTP->HTTPS
    upgrade) and is re-applied on *every* redirect hop below, not just the
    first request - a booking-role check can never be smuggled onto an
    unlisted host via a redirect, since each hop's target is re-validated
    against the same role-scoped allowlist before it is ever resolved/sent.
    """
    checked_at = _iso(now)
    sender = send or _default_send
    clock = monotonic or time.monotonic
    policy = _policy()
    max_redirects = int(policy.get("max_redirects", 3) or 3)
    timeout = float(policy.get("request_timeout_seconds", 4) or 4)
    trusted = _trusted_domains(role=role)
    current, reason = validate_url(url, trusted_domains=trusted, role=role)
    if reason or not current:
        return _result("blocked", checked_at, reason or _POLICY_REASONS["invalid"])
    seen: set[str] = set()
    for hop in range(max_redirects + 1):
        if deadline is not None and clock() >= deadline:
            return _result("unverified", checked_at, "검사 시간 초과")
        current, reason = validate_url(current, trusted_domains=trusted, role=role)
        if reason or not current:
            return _result("blocked", checked_at, reason or "리디렉션 대상 정책 위반")
        if current in seen:
            return _result("unverified", checked_at, "리디렉션 반복")
        seen.add(current)
        host = urlsplit(current).hostname or ""
        try:
            addresses = _resolved_addresses(
                host,
                resolver,
                timeout=timeout,
                deadline=deadline,
                clock=clock,
            )
            response = _send_with_retry(
                sender,
                current,
                "GET",
                addresses[0],
                host,
                timeout,
                sleep,
                deadline=deadline,
                clock=clock,
            )
        except LinkPolicyError as exc:
            return _result("blocked", checked_at, exc.reason)
        except LinkDeadlineExceeded:
            return _result("unverified", checked_at, "검사 시간 초과")
        except LinkDnsError:
            return _result("unverified", checked_at, _POLICY_REASONS["dns"])
        except (OSError, TimeoutError, UnicodeError, ValueError):
            return _result("unverified", checked_at, "일시적 연결 실패")
        status = response.status_code
        if 300 <= status < 400:
            location = response.headers.get("location")
            if not location or hop >= max_redirects:
                return _result("unverified", checked_at, "리디렉션 한도 초과")
            try:
                current = urljoin(current, location)
            except (TypeError, ValueError):
                return _result("blocked", checked_at, "리디렉션 대상 정책 위반")
            continue
        if 200 <= status < 300:
            # A reachable 2xx is verified regardless of full page size - the
            # transport (the default sender below, or an injected one) is
            # responsible for its own bounded read; this layer no longer
            # second-guesses body length, which used to fail a real,
            # reachable page shut purely for being a normal full HTML
            # document larger than the read sample.
            return _result("verified", checked_at, "연결 확인", current)
        if status in {404, 410}:
            return _result("dead", checked_at, "페이지 없음")
        if status == 429 or 500 <= status < 600:
            return _result("unverified", checked_at, "일시적 서버 응답")
        return _result("unverified", checked_at, "접근 확인 필요")
    return _result("unverified", checked_at, "리디렉션 한도 초과")
