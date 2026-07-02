<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-25 23:02 KST | Updated: 2026-06-25 23:02 KST -->

# tests/

## Purpose
unittest 스위트(58종). spec.md의 기계검증형 수용기준(AC)을 코드로 미러링 — 행위 보존 리팩터의 회귀 가드이자 DoD 게이트. 키/브라우저 부재 시 자동 폴백을 검증한다.

## Key Files

| File | Description |
|------|-------------|
| `test_pipeline.py` | 코어 53 테스트: 수집/upsert 멱등/시도정규화/SQLite FTS/필터/알람/매크로 게이팅/AI 플래너 |
| `test_runner.py` | Playwright 매크로 러너 E2E(5 테스트, playwright 미설치 시 skip) |
| `fixtures/` | 오프라인 테스트 픽스처 |

## For AI Agents

### Working In This Directory
- 테스트는 **관측 가능한 행위**(멱등성·게이팅·환각 제거·dedupe)를 검증 — 기본값/동어반복 단언 금지.
- 신규 기능은 spec.md에 AC를 먼저 추가하고 여기서 미러링.
- playwright 미설치로 skip되는 매크로 자동제출 경로는 CODE_REVIEW가 fake page 스파이로 CI 커버 권고.

### Testing Requirements
bash
python -m unittest discover -s tests       # 전체
python -m unittest discover -s tests -v     # 상세(현재 55 pass / 3 skip)


### Common Patterns
순수 함수는 입출력 단언, 부수효과는 dry-run/스파이로 검증. DoD: 40+ green + validate rc==0.

## Dependencies

### Internal
`scripts.**` 전 모듈.

### External
unittest(표준), playwright(옵션).

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
