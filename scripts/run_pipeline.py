"""전체 파이프라인 실행: 수집/정규화/적재 → 검증 → 인덱스 빌드 → wiki 갱신.

사용:
    python -m scripts.run_pipeline           # 활성 소스 전체
    python -m scripts.run_pipeline kopis     # 특정 소스만
"""
from __future__ import annotations

import logging
import subprocess
import sys

from scripts.build import build_index, build_pages, build_sqlite, wiki_index
from scripts.common.config import ROOT
from scripts.normalize import to_okf

log = logging.getLogger("notchima.pipeline")


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("== 1. 수집·정규화·적재 ==")
    to_okf.run(argv or None)

    log.info("== 2. OKF 스키마 검증 ==")
    rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "build" / "validate.py")])
    if rc != 0:
        log.error("검증 실패 — 빌드 중단")
        return rc

    log.info("== 3. 인덱스 빌드 ==")
    build_index.build()

    log.info("== 3b. 행사별 정적 상세 HTML(JSON-LD) 빌드 ==")
    build_pages.build()

    log.info("== 4. SQLite(libSQL) 쿼리 인덱스 빌드 ==")
    try:
        build_sqlite.build()
    except RuntimeError as exc:
        log.warning("SQLite 건너뜀: %s", exc)

    log.info("== 5. 지식 wiki 갱신 ==")
    wiki_index.build()
    log.info("완료. 정적 사이트: web/public/ (data/*.json, events.db 갱신됨)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
