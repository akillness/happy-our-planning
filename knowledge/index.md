# Happy Our Planning — 지식 DB (Knowledge Wiki)

llm-wiki 패턴을 따르는 행사 지식 베이스. 검색/지도/추천의 단일 진실원천(SSOT).
각 행사는 `events/<YYYY>/<MM>/<sido>/<id>.md` 에 schema.org/Event(OKF) frontmatter로 저장된다.

- **Schema**: `schema/event.schema.json`
- **Graphify output**: `../graphify-out/` (행사↔지역↔테마↔주최 그래프)
- **갱신 주기**: 매일 03:00 KST (GitHub Actions)

## Regions
<!-- REGIONS:START -->
- 서울특별시 — 205건
- 경기도 — 49건
- 부산광역시 — 46건
- 대구광역시 — 21건
- 경상북도 — 19건
- 전남광주통합특별시 — 16건
- 인천광역시 — 15건
- 강원특별자치도 — 14건
- 경상남도 — 12건
- 대전광역시 — 11건
- 충청남도 — 9건
- 울산광역시 — 8건
- 충청북도 — 6건
- 제주특별자치도 — 4건
- 전북특별자치도 — 4건
- 세종특별자치시 — 1건
<!-- REGIONS:END -->

## Themes
<!-- THEMES:START -->
- 공연 — 426건
- 축제 — 11건
- 공모전 — 2건
- 교육 — 2건
- 체험 — 1건
<!-- THEMES:END -->

## Sources
<!-- SOURCES:START -->
- kopis — 425건 (최근 갱신 2026-10-16)
- websearch — 11건 (최근 갱신 2026-10-17)
- tourapi — 3건 (최근 갱신 2026-10-15)
- example — 1건 (최근 갱신 2026-06-20)
<!-- SOURCES:END -->

## Reports
- `reports/` — 주간 트렌드·큐레이션·dedupe 후보 (AI 생성)
