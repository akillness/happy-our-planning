<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-25 23:02 KST | Updated: 2026-06-25 23:02 KST -->

# scripts/common/

## Purpose
패키지 전역 공유 유틸. 프로젝트 루트 경로 해결, `config/*.yaml` 로더, OKF 레코드 헬퍼. 모든 모듈이 의존하는 기반 레이어.

## Key Files

| File | Description |
|------|-------------|
| `config.py` | `ROOT` 경로, `config/*.yaml`(sources/regions/themes/age-bands/search/macro-sites) 로드 |
| `okf.py` | OKF 레코드 빌드/직렬화 헬퍼(JSON-LD frontmatter), content_hash |

## For AI Agents

### Working In This Directory
- CODE_REVIEW 권고: 여기에 `http.py`(`post_json` retry/backoff) 공통 헬퍼와 `OkfEvent(TypedDict)` 도입 — 변경 시 광범위 영향, 58 테스트가 회귀 가드.
- 경로는 항상 `ROOT` 기준 상대해결, 하드코딩 금지.

### Testing Requirements
공유 헬퍼 변경은 전체 스위트로 검증(`python -m unittest discover -s tests`).

### Common Patterns
설정은 한 번 로드해 재사용. 순수 헬퍼 위주, IO 최소화.

## Dependencies

### Internal
없음(최하위 레이어).

### External
pyyaml.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
