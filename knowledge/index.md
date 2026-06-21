# Happy Our Planning — 지식 DB (Knowledge Wiki)

llm-wiki 패턴을 따르는 행사 지식 베이스. 검색/지도/추천의 단일 진실원천(SSOT).
각 행사는 `events/<YYYY>/<MM>/<sido>/<id>.md` 에 schema.org/Event(OKF) frontmatter로 저장된다.

- **Schema**: `schema/event.schema.json`
- **Graphify output**: `../graphify-out/` (행사↔지역↔테마↔주최 그래프)
- **갱신 주기**: 매일 03:00 KST (GitHub Actions)

## Regions
<!-- REGIONS:START -->
- 서울특별시 — 4건
- 부산광역시 — 2건
- 강원특별자치도 — 1건
- 충청남도 — 1건
- 경기도 — 1건
- 제주특별자치도 — 1건
<!-- REGIONS:END -->

## Themes
<!-- THEMES:START -->
- 축제 — 5건
- 공연 — 4건
- 공모전 — 1건
- 교육 — 1건
- 체험 — 1건
<!-- THEMES:END -->

## Sources
<!-- SOURCES:START -->
- tourapi — 3건 (최근 갱신 2026-06-21)
- websearch — 3건 (최근 갱신 2026-06-21)
- kopis — 3건 (최근 갱신 2026-06-21)
- example — 1건 (최근 갱신 2026-06-21)
<!-- SOURCES:END -->

## Reports
- `reports/` — 주간 트렌드·큐레이션·dedupe 후보 (AI 생성)
