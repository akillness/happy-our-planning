<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-25 23:02 KST | Updated: 2026-06-25 23:02 KST -->

# scripts/build/

## Purpose
지식 DB(flat-file)에서 정적 UI가 소비하는 파생 산출물을 빌드한다: OKF 스키마 검증 게이트, JSON 인덱스(events/facets/regions/updated), SQLite FTS5 전문검색 인덱스, llm-wiki 인덱스 갱신.

## Key Files

| File | Description |
|------|-------------|
| `validate.py` | JSON Schema 검증 게이트 — rc≠0이면 빌드 중단(제약 C3) |
| `build_index.py` | `events.json`·`facets.json`·`regions.json`·`updated.json`(신선도) 빌드 |
| `build_sqlite.py` | `events.db` FTS5 한국어 전문검색 + sido/theme/status 교차필터(재생성 가능 파생물) |
| `wiki_index.py` | `knowledge/index.md`의 REGIONS/THEMES/SOURCES 마커 블록 갱신 |

## For AI Agents

### Working In This Directory
- `events.db`는 SSOT 아님 — 삭제 후 `run_pipeline` 재생성 시 동일 카운트여야 한다(제약 C2).
- 추천 점수 가중치(3/2/1)는 인라인 매직넘버 → CODE_REVIEW는 모듈 상수 추출 권고.

### Testing Requirements
`test_fts_korean_match`, `test_filter_sido_and_theme`(SQLite), facets/regions/updated 빌드 산출 확인.

### Common Patterns
검증 게이트 우선(fail-fast). 빌드는 멱등 — 입력 동일하면 산출 동일.

## Dependencies

### Internal
`knowledge/schema/event.schema.json`, `knowledge/events/**`.

### External
jsonschema, sqlite3(표준 lib, libSQL 호환 FTS5).

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
