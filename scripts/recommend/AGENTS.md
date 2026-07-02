<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-25 23:02 KST | Updated: 2026-06-25 23:02 KST -->

# scripts/recommend/

## Purpose
프로필 기반 개인화 추천. 규칙 랭킹(`rank.py`)으로 후보를 점수화하고 주간 플랜을 구성하며, Gemini(`ai_planner.py`)가 `responseSchema` 강제 구조화 플랜을 생성한다. LLM 실패/무키 시 규칙 폴백.

## Key Files

| File | Description |
|------|-------------|
| `rank.py` | 규칙 기반 점수화(`score_event`: 지역/테마/나이 가중) + `plan_week` 주간 플랜 |
| `ai_planner.py` | Google AI Studio(Gemini) 플래너 — responseSchema JSON 강제, 환각 id 제거, 규칙 폴백, 캐시 |

## For AI Agents

### Working In This Directory
- AI 출력은 반드시 `responseSchema` 강제 + 환각 event_id 제거 + 규칙 폴백(제약 C6). 무가드 출력 금지.
- `free_only` 준수·1일 상한 준수. LLM이 생성한 날짜는 사용자 `available_dates` 범위 검증(가드레일).
- `ai_planner.plan()`은 단발 httpx.post — CODE_REVIEW는 공통 retry/backoff 헬퍼 권고.

### Testing Requirements
`test_build_request_has_schema_and_ids`, `test_parse_plan_valid`/`rejects_bad_shape`, `test_constrain_drops_hallucinated_ids`, `test_plan_falls_back_without_key`, `test_free_only_excludes_paid`, `test_plan_respects_max_per_day`.

### Common Patterns
규칙 랭킹이 항상 가용한 기준선, LLM은 그 위 향상 레이어. 점수→`(float, reasons)` 반환(향후 `@dataclass ScoredEvent` 권고).

## Dependencies

### Internal
빌드 산출 `events.json`, `config/{themes,age-bands}.yaml`.

### External
Gemini(Google AI Studio 무료등급), httpx.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
