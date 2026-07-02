<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-25 23:02 KST | Updated: 2026-06-25 23:02 KST -->

# scripts/notify/

## Purpose
관심 필터(지역/테마)에 맞는 알람을 계산하고 전송한다. 마감 D-1 임박, (확장 계획) 신규 등록 행사 알림. 순수 계산(`compute_notifications`)과 전송(`dispatch`)을 분리하고, `knowledge/notify/sent.json`으로 중복 억제.

## Key Files

| File | Description |
|------|-------------|
| `dispatch.py` | `compute_notifications`(순수) + `dispatch`(Telegram 전송, 무토큰 dry-run) + dedupe |

## For AI Agents

### Working In This Directory
- 채널 토큰 없으면 dry-run 무오류여야 한다(제약 C1). 토큰 주입 시 실제 1건 도달.
- 2회차 실행 중복 0(`sent.json` 억제).
- FOMO 방어 트리거 3종 모두 활성: `deadline-D{1,3}`(마감 임박) · `new-event`(`fetched_at` 24h 이내 신규) · `application-open`(신청 시작일 당일 + status=Open). 각 트리거는 `{sub|event|kind}` 해시로 독립 dedupe.

### Testing Requirements
`test_deadline_d1_and_filter_match`, `test_filter_mismatch_no_notif`, `test_dedupe_suppresses_second_run`, `test_new_event_notification`, `test_application_open_today`, `test_application_open_requires_open_status`, `test_dedupe_across_trigger_types`.

### Common Patterns
계산은 멱등·순수, 전송만 부수효과. 채널 추상(Telegram→Web Push 확장 여지).

## Dependencies

### Internal
빌드 산출 `events.json`, `knowledge/notify/sent.json`.

### External
Telegram Bot API(무료), 향후 Web Push.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
