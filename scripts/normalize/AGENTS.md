<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-25 23:02 KST | Updated: 2026-06-25 23:02 KST -->

# scripts/normalize/

## Purpose
수집 원본을 `schema.org/Event`(OKF) 정규 레코드로 변환하고, 좌표를 보강하며, 지식 DB에 멱등(idempotent) upsert한다. 검색·지도·추천의 1급 필터 축(지역/기간/나이/테마/키워드)을 정규 속성으로 보장.

## Key Files

| File | Description |
|------|-------------|
| `to_okf.py` | 소스 레코드 → OKF JSON-LD frontmatter Markdown 변환·실행 진입(`run`) |
| `geocode.py` | 주소/지역 → lat/lng(33–39N·124–132E) 보강, geocache |
| `upsert.py` | `content_hash` 증분 upsert(생성→skip 멱등, 변경 시만 update), archived 정책 |

## For AI Agents

### Working In This Directory
- 필수 키: `id, name, start_date, url, location.sido, source, fetched_at, content_hash`.
- upsert는 멱등이어야 한다(동일 입력 재실행 시 skip). 내용 변경 시에만 `content_hash` 갱신.
- 시/도는 17개 enum + 별칭/부분일치 정규화.

### Testing Requirements
`test_create_then_skip_idempotent`, `test_update_on_change`, `test_canonical_sido_alias_and_partial`.

### Common Patterns
변환은 순수 함수, upsert만 파일 IO. 좌표 결측은 archived가 아니라 결측 허용(품질 메트릭으로 추적).

## Dependencies

### Internal
`scripts.common.okf`(스키마 헬퍼), `scripts.build.validate`(후속 게이트).

### External
없음(좌표 캐시는 로컬). 원격 지오코딩은 VWorld(무료) 옵션.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
