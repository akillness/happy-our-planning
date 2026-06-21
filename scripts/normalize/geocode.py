"""지오코딩 보강 (T5 · docs/03·05).

좌표(lat/lng)가 없는 행사에 좌표를 채운다. 우선순위:
  1) 주소/장소명 캐시(knowledge/sources/geocache.json) — 정확 좌표 재사용(무네트워크).
  2) VWorld 지오코딩(선택, VWORLD_KEY) — 결과는 캐시에 적재.
  3) 시/도 centroid 폴백(config/regions.yaml) — 결측을 0으로 줄이는 근사 좌표.

무키·오프라인(C1): 키 없으면 1·3단계만으로 동작한다. 좌표를 채우면 content_hash를
재계산해 적재 idempotency(C2)를 유지한다(centroid는 결정적 → 재실행 시 skip).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from scripts.common.config import ROOT, all_regions, canonical_sido
from scripts.common.okf import content_hash

GEOCACHE_PATH = ROOT / "knowledge" / "sources" / "geocache.json"


@lru_cache(maxsize=None)
def _centroids() -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for r in all_regions():
        if r.get("lat") is not None and r.get("lng") is not None:
            out[r["name"]] = (r["lat"], r["lng"])
    return out


def _load_cache() -> dict[str, dict]:
    if GEOCACHE_PATH.exists():
        return json.loads(GEOCACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict[str, dict]) -> None:
    GEOCACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    GEOCACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
                             encoding="utf-8")


def sido_centroid(sido: str | None) -> tuple[float | None, float | None]:
    canon = canonical_sido(sido)
    return _centroids().get(canon or "", (None, None))


def vworld_geocode(address: str | None, *, key: str | None = None) -> tuple[float, float] | None:
    """VWorld 주소→좌표(선택). 키/주소 없으면 None(무오류). EPSG:4326 (lng, lat)."""
    key = key or os.environ.get("VWORLD_KEY")
    if not key or not address:
        return None
    try:  # pragma: no cover - 네트워크 경로
        import httpx
        resp = httpx.get(
            "https://api.vworld.kr/req/address",
            params={"service": "address", "request": "getcoord", "version": "2.0",
                    "crs": "epsg:4326", "type": "ROAD", "address": address,
                    "format": "json", "key": key},
            timeout=10,
        )
        data = resp.json()
        point = data["response"]["result"]["point"]
        return (float(point["y"]), float(point["x"]))  # (lat, lng)
    except Exception:
        return None


def geocode_event(event: dict, *, cache: dict | None = None, use_network: bool = False) -> dict:
    """단일 행사의 좌표를 보강한다. 좌표가 이미 있으면 그대로 둔다."""
    loc = event.get("location") or {}
    if loc.get("lat") is not None and loc.get("lng") is not None:
        return event

    cache = _load_cache() if cache is None else cache
    addr_keys = [loc.get("address"), event.get("venue"), event.get("name")]

    # 1) 캐시
    for k in addr_keys:
        if k and k in cache:
            loc["lat"], loc["lng"] = cache[k]["lat"], cache[k]["lng"]
            loc["geo_precision"] = "address-cache"
            event["location"] = loc
            event["content_hash"] = content_hash(event)
            return event

    # 2) VWorld(선택)
    if use_network:
        addr = loc.get("address") or event.get("venue")
        hit = vworld_geocode(addr)
        if hit:
            loc["lat"], loc["lng"] = hit
            loc["geo_precision"] = "vworld"
            event["location"] = loc
            if addr:
                cache[addr] = {"lat": hit[0], "lng": hit[1]}
                _save_cache(cache)
            event["content_hash"] = content_hash(event)
            return event

    # 3) 시/도 centroid 폴백
    lat, lng = sido_centroid(loc.get("sido"))
    if lat is not None:
        loc["lat"], loc["lng"] = lat, lng
        loc["geo_precision"] = "sido-centroid"
        event["location"] = loc
        event["content_hash"] = content_hash(event)
    return event


def enrich(events: list[dict], *, use_network: bool = False) -> list[dict]:
    """수집된 행사 목록에 좌표를 보강한다(to_okf 오케스트레이터가 호출)."""
    cache = _load_cache()
    return [geocode_event(e, cache=cache, use_network=use_network) for e in events]


def coverage(events: list[dict]) -> dict:
    """좌표 결측률·시/도 분포 측정(검증용)."""
    total = len(events)

    def _has(e):
        loc = e.get("location") or {}
        return (loc.get("lat") is not None or e.get("lat") is not None)

    with_geo = sum(1 for e in events if _has(e))
    sidos = {canonical_sido((e.get("location") or {}).get("sido") or e.get("sido"))
             for e in events}
    sidos.discard(None)
    return {
        "total": total,
        "with_geo": with_geo,
        "missing_rate": round((total - with_geo) / total, 4) if total else 0.0,
        "sido_count": len(sidos),
        "centroid_table_size": len(_centroids()),
    }
