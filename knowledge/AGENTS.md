<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-25 23:02 KST | Updated: 2026-06-25 23:02 KST -->

# knowledge/

## Purpose
**지식 DB의 단일 진실원천(SSOT)** — llm-wiki 패턴의 flat-file 데이터베이스. 각 행사는 `events/<YYYY>/<MM>/<sido>/<id>.md`에 schema.org/Event(OKF) JSON-LD frontmatter로 저장된다. `web/public/data/events.db`(SQLite)는 여기서 재생성되는 파생물이지 진실원천이 아니다.

## Key Files

| File | Description |
|------|-------------|
| `index.md` | wiki 루트 인덱스 — REGIONS/THEMES/SOURCES 마커 블록(자동 갱신) + 갱신주기·그래프 링크 |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `events/` | 행사 OKF Markdown 레코드(`<YYYY>/<MM>/<sido>/<id>.md`) |
| `schema/` | `event.schema.json` — OKF 검증 계약 |
| `sources/` | 소스별 수집 스냅샷/메타(`<source>-<date>.md`) |
| `notify/` | `sent.json` 등 알람 중복억제 상태 |

## For AI Agents

### Working In This Directory
- **이곳이 진실원천** — `events.db`나 `web/public/data/*.json`을 SSOT로 취급 금지(파생물).
- 행사 레코드는 `schema/event.schema.json` 정합 필수, 위반 시 `validate.py` rc≠0로 빌드 중단(C3).
- `index.md`의 `<!-- REGIONS:START/END -->` 등 마커 블록은 `wiki_index.build()`가 자동 갱신 — 손으로 마커 안을 고치지 말 것.

### Testing Requirements
`run_pipeline` 5단계 후 `index.md` 인덱스 갱신 확인(AC-F6.1). 스키마 검증 게이트.

### Common Patterns
git 안의 Markdown = DB. 증분 upsert(content_hash), archived 정책으로 만료 관리.

## Dependencies

### Internal
`scripts.normalize.upsert`(쓰기), `scripts.build.*`(읽기/파생 생성).

### External
graphify(행사↔지역↔테마↔주최 그래프, `../graphify-out/`).

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
