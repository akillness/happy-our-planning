<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-25 23:02 KST | Updated: 2026-06-25 23:02 KST -->

# config/

## Purpose
통제 어휘(controlled vocabulary)와 소스/매크로 설정 YAML. 정규화·필터·랭킹·매크로가 참조하는 단일 어휘 출처. 유료 엔드포인트는 절대 포함하지 않는다(제약 C1).

## Key Files

| File | Description |
|------|-------------|
| `sources.yaml` | 수집 소스 카탈로그(KOPIS·TourAPI·웹검색) + 무료티어 한도 주석 |
| `regions.yaml` | 17개 시/도 + 시군구 정규 어휘·별칭 |
| `themes.yaml` | 테마 정규화 매핑(축제/공연/전시/교육/공모/지원) + event_type enum |
| `age-bands.yaml` | 나이대 밴드 매핑(어린이/청소년/청년/…) |
| `search.yaml` | 검색 API(Exa/Brave/Tavily) 설정 + 무료티어 한도 |
| `macro-sites.yaml` | 사이트별 매크로 프로필 + `automation_allowed` 약관 플래그 |

## For AI Agents

### Working In This Directory
- 키/소스 추가 시 무료티어 한도 주석을 반드시 유지(drift guard).
- `config/*.yaml`에 유료 엔드포인트가 등장하면 제약 C1 위반.
- 약관 자동화 금지 사이트는 `macro-sites.yaml`에서 `automation_allowed: false`로 명시(C5).

### Testing Requirements
어휘 변경은 정규화 테스트(`test_canonical_sido_alias_and_partial`)와 매크로 게이팅 테스트에 반영.

### Common Patterns
코드가 아닌 데이터로 정책을 표현 — 어휘 변경에 코드 수정 불필요.

## Dependencies

### Internal
`scripts.common.config`가 로드, 전 모듈이 참조.

### External
없음.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
