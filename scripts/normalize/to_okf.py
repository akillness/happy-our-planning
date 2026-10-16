"""정규화 오케스트레이터: 활성 소스 → 어댑터 수집/매핑 → upsert → 수집 로그.

사용:
    python -m scripts.normalize.to_okf            # 활성 소스 전체
    python -m scripts.normalize.to_okf kopis      # 특정 소스만
"""
from __future__ import annotations

import datetime as dt
import json
import sys

from scripts.common.config import ROOT, sources
from scripts.common.okf import iter_events
from scripts.ingest.base import now_kst
from scripts.ingest.kopis import KopisAdapter
from scripts.ingest.tourapi import TourApiAdapter
from scripts.ingest.websearch import WebSearchAdapter
from scripts.normalize.upsert import upsert
from scripts.normalize import geocode

ADAPTERS = {
    "kopis": KopisAdapter,
    "tourapi": TourApiAdapter,
    "websearch": WebSearchAdapter,
}

SOURCES_LOG_DIR = ROOT / "knowledge" / "sources"
COLLECTION_STATUS_PATH = SOURCES_LOG_DIR / "collection-status.json"


def _safe_error(error: BaseException | object | None) -> str | None:
    """Return a short error label that cannot include request credentials."""
    if error is None:
        return None
    error_type = type(error).__name__ if isinstance(error, BaseException) else "CollectionError"
    status = getattr(error, "status", None)
    if isinstance(status, int):
        return f"{error_type} (HTTP {status})"
    return error_type


def _load_previous_status() -> dict[str, dict]:
    try:
        payload = json.loads(COLLECTION_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    previous = payload.get("sources") if isinstance(payload, dict) else None
    return previous if isinstance(previous, dict) else {}


def _write_collection_status(statuses: dict[str, dict]) -> None:
    SOURCES_LOG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": now_kst(), "sources": statuses}
    temporary = COLLECTION_STATUS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(COLLECTION_STATUS_PATH)


def _write_log(key: str, count: int, stats: dict, error: str | None = None) -> None:
    SOURCES_LOG_DIR.mkdir(parents=True, exist_ok=True)
    date = dt.date.today().isoformat()
    path = SOURCES_LOG_DIR / f"{key}-{date}.md"
    body = (
        f"---\nsource: {key}\ndate: {date}\nfetched: {now_kst()}\n"
        f"collected: {count}\ncreated: {stats.get('created',0)}\n"
        f"updated: {stats.get('updated',0)}\nskipped: {stats.get('skipped',0)}\n"
        f"archived: {stats.get('archived',0)}\nerror: {error or ''}\n---\n\n"
        f"# {key} 수집 로그 {date}\n\n"
        f"- 수집(OKF): {count}\n- 신규: {stats.get('created',0)}\n"
        f"- 갱신: {stats.get('updated',0)}\n- 변경없음: {stats.get('skipped',0)}\n"
        f"- 아카이브: {stats.get('archived',0)}\n"
    )
    path.write_text(body, encoding="utf-8")


def _mode(adapter: object) -> str:
    value = getattr(adapter, "collection_mode", None)
    if value in {"live", "fixture"}:
        return value
    return "fixture" if getattr(adapter, "offline", False) else "live"


_SAMPLE_FIXTURE_IDS = frozenset({
    "example:demo-application",
    "kopis:PF200001", "kopis:PF200002", "kopis:PF200003",
    "tourapi:3001001", "tourapi:3001002", "tourapi:3001003",
    "websearch:625e949e3cb8", "websearch:27ce5dd1aab3",
    "websearch:b254ade704ec", "websearch:bb38c44890b9",
})


def _source_has_active_events(key: str) -> bool:
    return any(
        fm.get("source") == key and fm.get("status") != "archived"
        for _path, fm, _body in iter_events()
    )


def _has_real_active_events() -> bool:
    return any(
        fm.get("status") != "archived" and fm.get("id") not in _SAMPLE_FIXTURE_IDS
        for _path, fm, _body in iter_events()
    )


def _collection_flags(adapter: object) -> tuple[bool, bool, bool]:
    return (
        bool(getattr(adapter, "snapshot_complete", False)),
        bool(getattr(adapter, "complete", False)),
        bool(getattr(adapter, "truncated", False)),
    )


# adapter.collection_metadata["detail"](예: KopisAdapter._attach_details)에서
# 끌어와 run log/상태에 반영하는 정수 카운터만의 화이트리스트. URL/키/기타
# 임의 메타데이터는 절대 통과시키지 않는다(공개 로그 안전).
_DETAIL_METRIC_FIELDS = ("candidates", "cached", "fetched", "failed", "capped", "stale")


def _detail_metrics(adapter: object) -> dict[str, int]:
    """상세 조회 통계(정수만) — adapter.collection_metadata["detail"]에서 추출.

    키/URL/기타 필드는 전달하지 않는다. 필드가 없거나 int가 아니면 생략(발명 금지).
    """
    metadata = getattr(adapter, "collection_metadata", None)
    detail = metadata.get("detail") if isinstance(metadata, dict) else None
    if not isinstance(detail, dict):
        return {}
    return {
        field: detail[field]
        for field in _DETAIL_METRIC_FIELDS
        if isinstance(detail.get(field), int)
    }


def run(only: list[str] | None = None, *, use_network: bool = False) -> dict:
    enabled = [s["key"] for s in sources() if s.get("enabled")]
    targets = [k for k in enabled if (not only or k in only)]
    previous = _load_previous_status()
    statuses = {
        key: previous[key]
        for key in enabled
        if isinstance(previous.get(key), dict)
    }
    summary: dict = {}
    failed_live = False
    failed = False

    for key in targets:
        attempted_at = now_kst()
        adapter = None
        adapter_mode = "live"
        snapshot_complete = False
        complete = False
        truncated = False
        raw_prior = previous.get(key)
        prior = raw_prior if isinstance(raw_prior, dict) else {}
        adapter_cls = ADAPTERS.get(key)
        if not adapter_cls:
            error = "AdapterMissing"
            statuses[key] = {
                "mode": "error",
                "attempted_at": attempted_at,
                "last_success_at": prior.get("last_success_at"),
                "error": error,
                "collected": 0,
                "complete": False,
                "truncated": False,
            }
            summary[key] = {"error": error}
            failed = failed_live = True
            _write_log(key, 0, {}, error=error)
            print(f"[{key}] 실패: {error}")
            continue

        try:
            adapter = adapter_cls()
            adapter_mode = _mode(adapter)
            events = adapter.collect()
            snapshot_complete, complete, truncated = _collection_flags(adapter)
            collection_errors = list(getattr(adapter, "collection_errors", ()))
            if collection_errors and adapter_mode == "live":
                error = _safe_error(collection_errors[0]) or "CollectionError"
                statuses[key] = {
                    "mode": "error",
                    "attempted_at": attempted_at,
                    "last_success_at": prior.get("last_success_at"),
                    "error": error,
                    "collected": len(events),
                    "complete": complete,
                    "truncated": truncated,
                }
                summary[key] = {"collected": len(events), "error": error}
                failed = failed_live = True
                _write_log(key, len(events), {}, error=error)
                print(f"[{key}] 실패: {error} (기존 행사 보존)")
                continue
            if adapter_mode == "live" and not events and _source_has_active_events(key):
                error = "EmptyCollection"
                statuses[key] = {
                    "mode": "error",
                    "attempted_at": attempted_at,
                    "last_success_at": prior.get("last_success_at"),
                    "error": error,
                    "collected": 0,
                    "complete": complete,
                    "truncated": truncated,
                }
                summary[key] = {"error": error}
                failed = failed_live = True
                _write_log(key, 0, {}, error=error)
                print(f"[{key}] 실패: {error} (기존 행사 보존)")
                continue

            events = geocode.enrich(events, use_network=use_network)
            is_sample_batch = bool(events) and all(
                isinstance(ev, dict) and ev.get("id") in _SAMPLE_FIXTURE_IDS
                for ev in events
            )
            if adapter_mode == "fixture" and is_sample_batch and _has_real_active_events():
                stats = {"created": 0, "updated": 0, "skipped": len(events), "archived": 0}
            else:
                stats = upsert(events, processed_sources={key} if snapshot_complete else None)
            # 상세 조회 실패/상한은 목록 레코드를 전혀 막지 않는다(부분 실패와
            # 전체 소스 실패를 구분) — 있으면 정수 카운터만 run log/상태에 반영.
            detail_metrics = _detail_metrics(adapter)
            _write_log(key, len(events), stats)
            summary[key] = {"collected": len(events), **stats}
            statuses[key] = {
                "mode": adapter_mode,
                "attempted_at": attempted_at,
                "last_success_at": now_kst() if adapter_mode == "live" else prior.get("last_success_at"),
                "error": None,
                "collected": len(events),
                "complete": complete,
                "truncated": truncated,
            }
            if detail_metrics:
                summary[key]["detail"] = detail_metrics
                statuses[key]["detail"] = detail_metrics
            print(f"[{key}] {len(events)}건 → {stats} ({adapter_mode})")
        except Exception as exc:  # 소스 격리
            error = _safe_error(exc) or "CollectionError"
            mode = adapter_mode if adapter is not None else "live"
            statuses[key] = {
                "mode": "error",
                "attempted_at": attempted_at,
                "last_success_at": prior.get("last_success_at"),
                "error": error,
                "collected": 0,
                "complete": complete,
                "truncated": truncated,
            }
            summary[key] = {"error": error}
            failed = True
            if mode == "live":
                failed_live = True
            _write_log(key, 0, {}, error=error)
            print(f"[{key}] 실패: {error} (기존 행사 보존)")

    _write_collection_status(statuses)
    summary["sources"] = statuses
    summary["failed_live"] = failed_live
    summary["failed"] = failed
    return summary


if __name__ == "__main__":
    run(sys.argv[1:] or None)
