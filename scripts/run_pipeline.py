"""전체 파이프라인 실행: 수집/정규화/적재 → 검증 → 인덱스 빌드 → wiki 갱신.

사용:
    python -m scripts.run_pipeline           # 활성 소스 전체
    python -m scripts.run_pipeline kopis     # 특정 소스만
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

from scripts.build import build_index, build_pages, build_sitemap, build_sqlite, wiki_index
from scripts.common.config import ROOT
from scripts.common.env import load_dotenv
from scripts.normalize import to_okf

log = logging.getLogger("notchima.pipeline")


def _load_public_events() -> list[dict]:
    """Load the just-built, retention-filtered publication candidate set."""
    path = build_index.OUT_DIR / "events.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("공개 행사 인덱스 로드 실패") from exc
    if not isinstance(raw, list) or any(not isinstance(event, dict) for event in raw):
        raise RuntimeError("공개 행사 인덱스 형식 오류")
    return raw



def _run_link_checks() -> int:
    """Run the bounded network checker only when the caller explicitly opts in."""
    if os.environ.get("NOTCHIMA_CHECK_LINKS") != "1":
        return 0
    log.info("== 1b. 외부 링크 도달성 확인 (명시적 opt-in) ==")
    return subprocess.call([
        sys.executable,
        "-m",
        "scripts.ops.check_links",
    ])


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx/httpcore INFO 로그는 요청 URL(쿼리 내 키 포함)을 그대로 찍으므로 억제.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    log.info("== 1. 수집·정규화·적재 ==")
    dotenv_loaded = load_dotenv()
    if dotenv_loaded:
        log.info(".env에서 %d건 로드 (%s)", len(dotenv_loaded), ", ".join(dotenv_loaded))
    collection = to_okf.run(argv or None)
    if collection.get("failed"):
        log.error("수집 실패 — 기존 행사 보존, 빌드 중단")
        return 1

    log.info("== 2. OKF 스키마 검증 ==")
    rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "build" / "validate.py")])
    if rc != 0:
        log.error("검증 실패 — 빌드 중단")
        return rc

    log.info("== 3. 현재 공개 후보 인덱스 빌드 ==")
    build_index.build()

    link_rc = _run_link_checks()
    if link_rc != 0:
        log.error("링크 확인 실패 — 빌드 중단")
        return link_rc
    if os.environ.get("NOTCHIMA_CHECK_LINKS") == "1":
        log.info("== 3b. 링크 결과 반영 인덱스 재빌드 ==")
        build_index.build()

    log.info("== 3c. 공개 후보 JSON 로드 및 SQLite 인덱스 빌드 ==")
    try:
        public_events = _load_public_events()
        sqlite_result = build_sqlite.build(events=public_events)
    except Exception as exc:
        log.error("SQLite 빌드 실패 (%s)", type(exc).__name__)
        return 1
    sqlite_count = sqlite_result.get("events") if isinstance(sqlite_result, dict) else None
    if sqlite_count != len(public_events):
        log.error("SQLite 행사 수 불일치 — 공개 후보=%d, DB=%s", len(public_events), sqlite_count)
        return 1

    log.info("== 3d. 행사별 정적 상세 HTML(JSON-LD) 빌드 ==")
    build_pages.build(events=public_events)

    log.info("== 3e. sitemap.xml 빌드 ==")
    build_sitemap.build(events=public_events)

    log.info("== 4. 지식 wiki 갱신 ==")
    wiki_index.build()
    log.info("완료. 정적 사이트: web/public/ (data/*.json, events.db 갱신됨)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
