"""명시적 opt-in 빌드용 외부 링크 reachability checker.

기본 파이프라인은 이 모듈을 호출하지 않는다. 실행 시에도 URL 정책을 먼저
검사하고, 공용 IP를 모두 확인한 뒤 선택한 IP에 TLS 연결을 고정한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Iterable, Mapping

from scripts.common import links
from scripts.common.config import ROOT, search_config

EVENTS_PATH = ROOT / "web" / "public" / "data" / "events.json"
CACHE_PATH = links.CACHE_PATH


def _utc_now(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(now: datetime | None = None) -> str:
    return _utc_now(now).isoformat().replace("+00:00", "Z")


def _policy_value(name: str, default: int) -> int:
    cfg = search_config()
    policy = cfg.get("link_policy", {}) if isinstance(cfg, Mapping) else {}
    try:
        return int(policy.get(name, default))
    except (TypeError, ValueError):
        return default


def _entry(url: str, fields: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """Build one output cache record, re-validating any ``verified`` claim.

    This is the single choke point every write path (a fresh ``check_url``
    result, a reused-fresh proof, or a deferred raw prior entry carried
    forward unchanged) funnels through, so a stale/tampered/clock-skewed
    prior record can never republish an unsafe or now-policy-invalid
    ``link_url`` as clickable just because its stored status says
    "verified". The SHA-256 key already binds this result to the exact
    input; this is the second half - don't trust what the cache *claims*
    about that url without rechecking it against today's policy and clock.
    """
    status = fields.get("link_status", "unchecked")
    checked_at = fields.get("link_checked_at")
    reason = fields.get("link_reason", "검증 결과 확인 필요")
    link_url: str | None = None
    if status == "verified":
        parsed_checked = links._parse_iso(checked_at)
        valid_url, policy_reason = links.validate_url(fields.get("link_url"))
        if parsed_checked is None or parsed_checked > now or policy_reason or not valid_url:
            status, reason = "blocked", "검증 URL 정책 불일치"
        else:
            link_url = valid_url
    return {
        "link_status": status,
        "link_checked_at": checked_at if isinstance(checked_at, str) else None,
        "link_url": link_url,
        "link_reason": reason,
    }


_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)
_FAR_PAST = datetime.min.replace(tzinfo=timezone.utc)
_KST = timezone(timedelta(hours=9))


def _effective_date(event: Mapping[str, Any]) -> datetime | None:
    # end_date (or start_date for still-open/undated-end records) is the
    # relevant "how soon does this stop mattering" anchor for the queue.
    return links._parse_iso(event.get("end_date")) or links._parse_iso(event.get("start_date"))


def _expired(effective: datetime | None, now: datetime) -> bool:
    """KST calendar-day-inclusive: an event is not expired until the day
    after its effective KST date. A record published as (e.g.) a bare
    midnight-KST ``start_date`` for "today" must stay current for the rest
    of that KST day, not look already-past the instant ``now`` ticks past
    00:00 KST.
    """
    if effective is None:
        return False
    return now.astimezone(_KST).date() > effective.astimezone(_KST).date()


def _priority_key(
    event: Mapping[str, Any], now: datetime, checked_at: object
) -> tuple[int, int, datetime, datetime]:
    """Lower sorts first: this run's budget hits these URLs before the rest.

    Upcoming child-experience candidates (``is_experience`` and
    ``family_friendly``) lead; other current/future events are next;
    already-expired records (kept on display but no longer time-critical)
    are checked last.

    Within a bucket, a URL this queue has never actually checked goes
    first, then the one checked longest ago, then soonest-ending first.
    Sorting a bucket by date/experience status alone would let the exact
    same ~40 URLs win every scheduled run forever once that bucket is
    larger than the budget; ordering by recency-of-check instead rotates
    the budget across the whole eligible set over successive runs.
    """
    effective = _effective_date(event)
    if _expired(effective, now):
        bucket = 2
    elif bool(event.get("is_experience")) and bool(event.get("family_friendly")):
        bucket = 0
    else:
        bucket = 1
    checked = links._parse_iso(checked_at)
    rotation = (1, checked) if checked is not None else (0, _FAR_PAST)
    return (bucket, rotation[0], rotation[1], effective or _FAR_FUTURE)


def check_events(
    events: Iterable[Mapping[str, Any]],
    *,
    send: links.LinkSender | None = None,
    resolver: links.Resolver | None = None,
    now: datetime | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    max_urls: int | None = None,
    max_seconds: int | None = None,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Check unique event URLs under count and wall-clock budgets.

    ``send`` and ``resolver`` are injected for deterministic tests. The returned
    object is directly serializable as ``link-checks.json``.

    ``previous`` is this run's starting point - normally the on-disk cache,
    which ``run()`` loads and passes through. A URL whose prior proof is
    still within TTL and ``verified`` is republished as-is without spending
    any of this run's network budget. A URL this run defers (budget or
    wall-clock exhausted first) keeps its exact prior entry instead of being
    overwritten by a placeholder, so a large backlog of older URLs can never
    permanently erase another, still-valid proof just by outnumbering it
    across scheduled runs; a URL deferred with no prior entry still gets the
    unchecked/unverified placeholder, same as before ``previous`` existed.
    Pruning needs no extra step: the output is built strictly from this
    run's current event set, so a URL no longer published is never carried
    forward.
    """
    clock = monotonic or globals()["monotonic"]
    current = _utc_now(now)
    url_limit = max_urls if max_urls is not None else _policy_value("max_urls", 40)
    time_limit = max_seconds if max_seconds is not None else _policy_value("max_runtime_seconds", 120)
    deadline = clock() + max(0, time_limit)
    prior_checks = links._cache_mapping(previous)

    ordered: list[tuple[str, Mapping[str, Any]]] = []
    seen: set[str] = set()
    for event in events:
        if not isinstance(event, Mapping):
            continue
        url = event.get("url")
        if not isinstance(url, str) or not url or url in seen:
            continue
        seen.add(url)
        ordered.append((url, event))

    def _sort_key(item: tuple[str, Mapping[str, Any]]) -> tuple[int, int, datetime, datetime]:
        prior = prior_checks.get(links._url_key(item[0]))
        checked_at = prior.get("link_checked_at") if isinstance(prior, Mapping) else None
        return _priority_key(item[1], current, checked_at)

    # Stable sort: ties (e.g. two records with no date, both never checked)
    # keep their original relative order, only the priority bucket/rotation
    # state/date actually reorders work.
    ordered.sort(key=_sort_key)

    checks: dict[str, Any] = {}
    count = 0
    for url, _event in ordered:
        key = links._url_key(url)
        fresh = links.link_fields(url, previous, current)
        if fresh.get("link_status") == "verified":
            checks[key] = _entry(url, fresh, current)
            continue
        if count >= max(0, url_limit):
            deferred, fallback = True, {
                "link_status": "unchecked",
                "link_checked_at": None,
                "link_url": None,
                "link_reason": "검사 한도 초과",
            }
        elif clock() >= deadline:
            deferred, fallback = True, {
                "link_status": "unverified",
                "link_checked_at": _iso(current),
                "link_url": None,
                "link_reason": "검사 시간 초과",
            }
        else:
            deferred, fallback = False, None
        if deferred:
            assert fallback is not None  # deferred is only True alongside a non-None fallback dict (see branches above)
            raw_prior = prior_checks.get(key)
            checks[key] = _entry(url, raw_prior if isinstance(raw_prior, Mapping) else fallback, current)
            count += 1
            continue
        fields = links.check_url(
            url,
            send=send,
            resolver=resolver,
            now=current,
            monotonic=clock,
            sleep=sleep,
            deadline=deadline,
        )
        checks[key] = _entry(url, fields, current)
        count += 1
    return {"version": 1, "generated_at": _iso(current), "checks": checks}


def write_cache(cache: Mapping[str, Any], path: Path | str | None = None) -> Path:
    """Atomically write a derived cache without exposing request details."""
    target = Path(path) if path else CACHE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(target)
    return target


class EventsLoadError(ValueError):
    """Events source is missing/unreadable/malformed - never treated as 0 events.

    Silently coercing a read failure to an empty list would make a missing
    file, a truncated write from a concurrent build, or a corrupted
    events.json indistinguishable from "there happen to be no events right
    now" - and then overwrite the on-disk proof cache with an empty result,
    destroying every valid existing proof. Callers must fail loudly instead
    and leave the existing cache untouched.
    """


def _load_events(path: Path) -> list[Mapping[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EventsLoadError(f"events source unreadable: {path}") from exc
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise EventsLoadError(f"events source is not valid JSON: {path}") from exc
    if not isinstance(raw, list):
        raise EventsLoadError(f"events source is not a JSON list: {path}")
    if not all(isinstance(item, Mapping) for item in raw):
        raise EventsLoadError(f"events source has a non-object record: {path}")
    return raw


def run(
    *,
    events_path: Path | str | None = None,
    cache_path: Path | str | None = None,
    send: links.LinkSender | None = None,
    resolver: links.Resolver | None = None,
    now: datetime | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    max_urls: int | None = None,
    max_seconds: int | None = None,
) -> dict[str, Any]:
    source = Path(events_path) if events_path else EVENTS_PATH
    # Load (and validate) events *before* touching the cache at all: if this
    # raises, write_cache is never reached and the existing on-disk proofs
    # are left exactly as they were.
    events = _load_events(source)
    previous = links.load_link_checks(cache_path)
    cache = check_events(
        events,
        send=send,
        resolver=resolver,
        now=now,
        monotonic=monotonic,
        sleep=sleep,
        max_urls=max_urls,
        max_seconds=max_seconds,
        previous=previous,
    )
    write_cache(cache, cache_path)
    return cache


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Notchima bounded HTTPS link checker")
    parser.add_argument("--events", type=Path, default=EVENTS_PATH)
    parser.add_argument("--cache", type=Path, default=CACHE_PATH)
    parser.add_argument("--max-urls", type=int, default=None)
    parser.add_argument("--max-seconds", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        cache = run(
            events_path=args.events,
            cache_path=args.cache,
            max_urls=args.max_urls,
            max_seconds=args.max_seconds,
        )
    except EventsLoadError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    counts: dict[str, int] = {}
    for item in cache["checks"].values():
        status = item.get("link_status", "unchecked")
        counts[status] = counts.get(status, 0) + 1
    print(json.dumps({"checked": len(cache["checks"]), "statuses": counts}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
