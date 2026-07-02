<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-25 23:02 KST | Updated: 2026-06-25 23:02 KST -->

# scripts/macro/

## Purpose
신청기간 내 신청을 보조하는 매크로. `apply.plan_job(event, profile)`이 사이트 프로필로 입력 스텝을 렌더하고, Playwright 러너(`runner.py`)가 mock/실 폼에서 소비한다. 약관상 자동화 금지 사이트는 반자동(semi)으로 강등.

## Key Files

| File | Description |
|------|-------------|
| `apply.py` | `plan_job` — 사이트 프로필 기반 스텝 렌더, 토큰 치환, 약관 게이팅(automation_allowed) |
| `runner.py` | Playwright 헤드리스 러너 — 스텝 입력→자동제출/일시정지, 결과 캡처 |

## For AI Agents

### Working In This Directory
- **제약 C5 절대 준수**: `automation_allowed=False` 사이트는 `mode='semi'`로 강등, 자동 submit 제거 + 사용자 최종제출 pause. 위반 시 스펙 드리프트.
- 미등록 사이트는 manual. 사이트 프로필은 `config/macro-sites.yaml`.
- CODE_REVIEW: 러너의 네트워크 지연·팝업 예외 처리 강화 필요. fake page 스파이로 submit 제거 불변을 chromium 없이 CI 검증 권고.

### Testing Requirements
`test_auto_site_has_submit`, `test_tos_blocked_site_is_semi_no_autosubmit`, `test_template_substitution`, `test_unknown_site_manual`, `tests/test_runner.py`(E2E, playwright 설치 시).

### Common Patterns
계획(`plan_job`, 순수)과 실행(`run_job`, 브라우저)을 분리. 안전 기본값은 비자동.

## Dependencies

### Internal
`config/macro-sites.yaml`, 사용자 프로필.

### External
playwright(chromium, 옵션 — 미설치 시 해당 테스트 skip).

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
