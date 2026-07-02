<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-25 23:02 KST | Updated: 2026-06-25 23:02 KST -->

# scripts/

## Purpose
놓치마의 전 백엔드 로직. 공공 API·웹검색 수집부터 OKF 정규화, JSON/SQLite 인덱스 빌드, 규칙·AI 추천, 알람 디스패치, 신청 매크로까지 모든 적재·검증·운영을 구동하는 Python 패키지. 엔트리포인트는 `run_pipeline.py` 오케스트레이터.

## Key Files

| File | Description |
|------|-------------|
| `run_pipeline.py` | 5단계 오케스트레이터: 수집·정규화·적재 → OKF 검증 → 인덱스 빌드 → SQLite → wiki 갱신 |
| `__init__.py` | 패키지 마커 |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `common/` | 공유 설정 로더·OKF 헬퍼·ROOT 경로 (see `common/AGENTS.md`) |
| `ingest/` | 소스 어댑터: kopis·tourapi·websearch fetch (see `ingest/AGENTS.md`) |
| `normalize/` | OKF 변환·지오코딩·멱등 upsert (see `normalize/AGENTS.md`) |
| `build/` | 검증·JSON 인덱스·SQLite FTS5·wiki 인덱스 빌드 (see `build/AGENTS.md`) |
| `recommend/` | 규칙 랭킹 + Gemini 주간 플래너 (see `recommend/AGENTS.md`) |
| `notify/` | 마감/신규 알람 계산 + 전송(Telegram) (see `notify/AGENTS.md`) |
| `macro/` | 신청 매크로 잡 계획 + Playwright 러너 (see `macro/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- 모듈은 `python -m scripts.<pkg>.<mod>` 형태로 호출(절대 임포트). `from scripts.common.config import ROOT`로 경로 해결.
- 모든 신규 외부 호출은 무료티어 + 키 부재 시 오프라인 폴백 필수.
- CODE_REVIEW_2026 권고: `print()` → `logging` 전환, 공통 `http.post_json` retry/backoff 헬퍼, OKF에 `TypedDict` 도입이 미착수 개선항목.

### Testing Requirements
`python -m unittest discover -s tests`. 행위 보존 리팩터는 기존 58 테스트가 회귀 가드.

### Common Patterns
- 순수 계산(`compute_notifications`, `score_event`)과 부수효과(`dispatch`, fetch)를 분리.
- 키/네트워크 부재 → fixture/규칙기반 자동 강등.

## Dependencies

### Internal
`run_pipeline` → `normalize.to_okf` → `build.{validate,build_index,build_sqlite,wiki_index}`. 추천/알람/매크로는 빌드 산출물(`web/public/data/*.json`, `knowledge/**`) 소비.

### External
httpx(원격 fetch), pyyaml(config), jsonschema(검증), playwright(매크로 옵션).

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
