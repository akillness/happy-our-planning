<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-25 23:02 KST | Updated: 2026-06-25 23:02 KST -->

# scripts/ingest/

## Purpose
소스별 수집 어댑터. 각 어댑터는 `fetch()`로 원본을 가져와(키 있으면 원격, 없으면 `raw/**/sample-*` 픽스처) `normalize`로 넘긴다. 공통 fetch 골격은 `base.py`.

## Key Files

| File | Description |
|------|-------------|
| `base.py` | 공통 fetch 추상(원격/오프라인 분기), 어댑터 베이스 |
| `kopis.py` | KOPIS(공연예술통합전산망) 어댑터 |
| `tourapi.py` | TourAPI(축제·관광) 어댑터 |
| `websearch.py` | 웹검색 발견 레이어(Exa·Brave·Tavily), 신뢰도/날짜/지역 가드로 OKF 후보 적재 (최대 모듈, 285 LOC) |

## For AI Agents

### Working In This Directory
- 신규 소스 추가 시 `config/sources.yaml`에 무료티어 한도 주석과 함께 등록.
- 웹검색 후보는 `🔎 발견` 배지로 노출되며 신뢰도 가드를 통과해야 적재.
- 키 부재 시 반드시 픽스처로 rc==0 (제약 C1).

### Testing Requirements
`tests/test_pipeline.py`의 수집 관련 테스트. 시/도 정규화는 `test_canonical_sido_alias_and_partial`.

### Common Patterns
fetch → 어댑터별 `to_okf` 매핑 위임 → upsert. 소스별 격리(스키마 변경 시 해당 소스만 skip).

## Dependencies

### Internal
`scripts.common.config`(소스 설정), `scripts.normalize.to_okf`·`upsert`.

### External
httpx, 공공 API·웹검색 API(무료티어).

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
