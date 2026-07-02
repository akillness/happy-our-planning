"""설정/통제 어휘 로더 (config/*.yaml).

지역 별칭 정규화, 소스 카테고리 → 표준 테마 매핑, 나이대 밴드 파싱을 제공한다.
모든 적재/정규화 스크립트가 공유하는 단일 진입점.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    path = CONFIG_DIR / name
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---- regions -----------------------------------------------------------------

@lru_cache(maxsize=None)
def _regions_index() -> tuple[dict[str, dict], dict[str, str]]:
    data = _load("regions.yaml")
    by_name: dict[str, dict] = {}
    for r in data.get("regions", []):
        by_name[r["name"]] = r
    aliases: dict[str, str] = dict(data.get("aliases", {}))
    return by_name, aliases


def canonical_sido(name: str | None) -> str | None:
    """소스 표기 → 표준 시/도명. 이미 표준이면 그대로, 별칭이면 변환."""
    if not name:
        return None
    name = name.strip()
    by_name, aliases = _regions_index()
    if name in by_name:
        return name
    if name in aliases:
        return aliases[name]
    # 부분 일치: 표준명이 입력으로 시작하면(예: "서울" → "서울특별시") 단일 후보만 채택
    by_full = [c for c in by_name if c.startswith(name)]
    if len(by_full) == 1:
        return by_full[0]
    # 짧은 표기(예: "서울시" → "서울특별시")는 2자 코어로 매칭하되,
    # 같은 2자 접두를 공유하는 형제 시/도(충청남/북·경상남/북)는 보조 글자(남/북)로 판별
    by_core = [c for c in by_name if name.startswith(c[:2])]
    if len(by_core) == 1:
        return by_core[0]
    if len(by_core) > 1:
        for c in by_core:
            marker = c[2:3]  # 남/북 등 형제 구분 글자
            if marker and marker in name:
                return c
    return name  # 미지/모호한 값은 보존(검증 단계에서 노출)


def sido_slug(name: str | None) -> str:
    by_name, _ = _regions_index()
    canon = canonical_sido(name)
    r = by_name.get(canon or "")
    return r["slug"] if r else "unknown"


def all_regions() -> list[dict]:
    return _load("regions.yaml").get("regions", [])


def extract_sido(text: str | None) -> str | None:
    """자유 텍스트(검색 제목/요약)에서 시/도를 1개 추출. 표준명 우선, 그다음 별칭/접두."""
    if not text:
        return None
    by_name, aliases = _regions_index()
    for canon in by_name:  # 표준 전체명이 본문에 그대로 있으면 최우선
        if canon in text:
            return canon
    for alias, canon in aliases.items():
        if alias in text:
            return canon
    return None


# ---- themes ------------------------------------------------------------------

def map_theme(source: str, code: str | None) -> str | None:
    """소스별 카테고리 코드 → 표준 테마. 미매핑이면 None."""
    if not code:
        return None
    mapping = _load("themes.yaml").get("mapping", {})
    return mapping.get(source, {}).get(str(code))


def all_themes() -> list[str]:
    return _load("themes.yaml").get("themes", [])


# ---- age bands ---------------------------------------------------------------

@lru_cache(maxsize=None)
def _age_cfg() -> dict:
    return _load("age-bands.yaml")


def age_bands(age: str | None) -> list[str]:
    """typicalAgeRange 문자열을 밴드 key 리스트로. 예 '7-13'->['어린이'], '19-'->청년이상."""
    cfg = _age_cfg()
    bands = cfg.get("bands", [])
    parse = cfg.get("parse", {})
    all_tokens = set(parse.get("all_tokens", []))

    if not age:
        return []
    token = age.strip()
    if token in all_tokens:
        return [b["key"] for b in bands if b["key"] != "전연령"] or ["전연령"]

    m = re.match(parse.get("range_regex", r"^(\d+)?\s*-\s*(\d+)?$"), token)
    if not m:
        # 단일 숫자("12")도 허용
        if token.isdigit():
            lo = hi = int(token)
        else:
            return []
    else:
        lo = int(m.group(1)) if m.group(1) else 0
        hi = int(m.group(2)) if m.group(2) else 200

    result = []
    for b in bands:
        if b["key"] == "전연령":
            continue
        if lo <= b["max"] and hi >= b["min"]:  # 구간 겹침
            result.append(b["key"])
    return result


def all_age_bands() -> list[dict]:
    return _age_cfg().get("bands", [])


# ---- sources -----------------------------------------------------------------

def sources() -> list[dict]:
    return _load("sources.yaml").get("sources", [])


def source(key: str) -> dict | None:
    for s in sources():
        if s.get("key") == key:
            return s
    return None


# ---- web search (discovery) --------------------------------------------------

def search_config() -> dict:
    """config/search.yaml — 웹 검색 제공자/질의/신뢰도 가드."""
    return _load("search.yaml")


def search_provider(name: str | None = None) -> dict:
    cfg = search_config()
    name = name or cfg.get("default_provider")
    return (cfg.get("providers", {}) or {}).get(name or "", {})
