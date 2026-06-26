"""소스 어댑터 기반 클래스.

각 공공 API 어댑터는 (1) native 레코드 수집(fetch_native)과
(2) OKF 매핑(map_to_okf)을 구현한다. 키가 없거나 offline 모드면
raw/<key>/ 의 fixture(sample-*) 로 대체해 네트워크 없이도 파이프라인이 돈다.
"""
from __future__ import annotations

import json
import logging
import os
import xml.etree.ElementTree as ET

from scripts.common.config import ROOT, source as source_cfg
from scripts.common.dates import now_kst  # noqa: F401 (어댑터 재노출)

log = logging.getLogger("notchima.ingest")

RAW_DIR = ROOT / "raw"


class SourceAdapter:
    key: str = ""
    fmt: str = "json"  # json | xml

    def __init__(self, offline: bool | None = None):
        self.cfg = source_cfg(self.key) or {}
        self.fmt = self.cfg.get("format", self.fmt)
        secret_env = (self.cfg.get("auth") or {}).get("secret_env")
        self.api_key = os.environ.get(secret_env or "", "")
        # 키가 없으면 자동으로 offline(fixture) 모드
        self.offline = (not self.api_key) if offline is None else offline

    # ---- 수집(step1) ---------------------------------------------------------
    def fetch_native(self) -> list[dict]:
        if self.offline:
            return self._load_fixtures()
        return self._fetch_remote()

    def _load_fixtures(self) -> list[dict]:
        d = RAW_DIR / self.key
        records: list[dict] = []
        if not d.exists():
            return records
        for f in sorted(d.glob("sample-*")):
            text = f.read_text(encoding="utf-8")
            if self.fmt == "xml" or f.suffix == ".xml":
                records.extend(self._parse_xml(text))
            else:
                records.extend(self._parse_json(text))
        return records

    def _fetch_remote(self) -> list[dict]:  # pragma: no cover - 네트워크 경로
        raise NotImplementedError("원격 수집은 어댑터에서 구현")

    # ---- 파서(소스 포맷별, 어댑터에서 오버라이드) -----------------------------
    def _parse_xml(self, text: str) -> list[dict]:
        root = ET.fromstring(text)
        out = []
        for item in root.iter("db"):
            out.append({c.tag: (c.text or "").strip() for c in item})
        return out

    def _parse_json(self, text: str) -> list[dict]:
        return json.loads(text)

    # ---- 매핑(step2, 어댑터에서 구현) ----------------------------------------
    def map_to_okf(self, native: dict) -> dict | None:
        raise NotImplementedError

    def collect(self) -> list[dict]:
        """native 수집 + OKF 매핑까지. None(매핑 실패)은 제외."""
        out = []
        for n in self.fetch_native():
            try:
                okf = self.map_to_okf(n)
            except Exception as exc:  # 한 레코드 실패가 전체를 막지 않음
                log.warning("[%s] map error: %s", self.key, exc)
                continue
            if okf:
                out.append(okf)
        return out
