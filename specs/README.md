# `specs/` — spec-stack 동결 번들 (Write → Freeze → Run, Verified)

놓치마(Notchima) 인프라의 **단일 진실원천 계획 번들**. `jeo team $spec-stack $survey`의 산출물.
survey(발견) → spec(쓰기) → seed(동결) → tasks(실행) → harness(검증)로 한 방향 흐른다.

| 레이어 | 파일 | 역할 | SSOT |
|--------|------|------|------|
| Survey (groundwork) | [`../.survey/free-saas-websearch-ai-stack-2026/`](../.survey/free-saas-websearch-ai-stack-2026/) | 무료 발견·AI·DB 스택 landscape | 발견 근거 |
| Spec (what) | [`spec.md`](spec.md) | 기능·제약·기계검증 AC | **문서 SSOT** |
| Freeze (until done) | [`seed.yaml`](seed.yaml) | 도구 제약 + 성공기준 미러 | **실행 SSOT** |
| Run (tasks) | [`tasks.md`](tasks.md) | team executor 작업 큐(DONE/TODO) | 작업 분해 |
| Tools (with what) | [`cli-harness.md`](cli-harness.md) | 산출물 증거 계약 | 검증 |

## 한 규칙
spec.md가 쓰고, seed.yaml이 동결·게이트하고, harness가 손이다. 방향은 단방향
`spec.md → seed.yaml`. 요구가 바뀌면 spec을 먼저 고치고 seed를 재동결한다(병렬 SSOT 금지).

## 실행 결과 (team)
`tasks.md` 우선순위 큐 T1–T6 **전부 완료**(각 1커밋, 커밋 전 게이트 통과):
**T1 Playwright 러너 ✅ → T2 알람 실채널 ✅ → T3 Cloudflare 엣지 ✅ → T4 UI/URL 복원 ✅ → T5 지오코딩 ✅ → T6 클린업 ✅.**
게이트: 매 슬라이스 `python -m unittest discover -s tests` green + `validate.py` rc==0 확인.

## 검증 상태 (2026-06-21)
- tests: `Ran 56 tests / OK` (T1 러너 E2E +5, T2 채널 +4, T5 지오코딩 +7 포함)
- survey 계약: validator `--platform-topic` 전 항목 OK
- 코드 실측: ~2281 LOC Python. **T1–T6 전부 완료** — F3 Playwright 러너, F4 실채널(Telegram/Web Push),
  배포(Cloudflare ai-proxy/jobqueue), UI URL 상태복원, 지오코딩(결측 0%), 죽은코드 제거.
