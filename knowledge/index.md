# Happy Our Planning — 지식 DB (Knowledge Wiki)

llm-wiki 패턴을 따르는 행사 지식 베이스. 검색/지도/추천의 단일 진실원천(SSOT).
각 행사는 `events/<YYYY>/<MM>/<sido>/<id>.md` 에 schema.org/Event(OKF) frontmatter로 저장된다.

- **Schema**: `schema/event.schema.json`
- **Graphify output**: `../graphify-out/` (행사↔지역↔테마↔주최 그래프)
- **갱신 주기**: 매일 03:00 KST (GitHub Actions)

## Regions
<!-- REGIONS:START -->
- 서울특별시 — 158건
- 경기도 — 36건
- 부산광역시 — 35건
- 대구광역시 — 19건
- 경상북도 — 17건
- 강원특별자치도 — 13건
- 인천광역시 — 12건
- 전남광주통합특별시 — 11건
- 경상남도 — 10건
- 충청남도 — 9건
- 대전광역시 — 8건
- 충청북도 — 5건
- 제주특별자치도 — 4건
- 울산광역시 — 4건
- 전북특별자치도 — 3건
- 세종특별자치시 — 1건
<!-- REGIONS:END -->

## Themes
<!-- THEMES:START -->
- 공연 — 335건
- 축제 — 8건
- 공모전 — 2건
- 교육 — 1건
- 체험 — 1건
<!-- THEMES:END -->

## Sources
<!-- SOURCES:START -->
- kopis — 334건 (최근 갱신 2026-10-16)
- websearch — 7건 (최근 갱신 2026-10-15)
- tourapi — 3건 (최근 갱신 2026-10-15)
- example — 1건 (최근 갱신 2026-06-20)
<!-- SOURCES:END -->

## Reports
- `reports/` — 주간 트렌드·큐레이션·dedupe 후보 (AI 생성)
