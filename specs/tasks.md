---
title: 놓치마 — 작업 분해 (team 실행 계획)
status: active
okf_type: TechnicalDocument
layer: spec-stack/tasks · team
spec_ref: specs/spec.md@1.0.0 · specs/seed.yaml
updated: 2026-06-21
---

# 작업 분해 (Run 레이어 / team executor 큐)

> 각 작업 = `team`이 직렬 실행하는 1 슬라이스. 형식:
> **파일 경로 + 함수 시그니처 + DoD(기계검증 명령)**. 이미 코드가 있는 항목은 `DONE`로
> 회귀 게이트만 유지하고, 신규는 `TODO`. seed의 `success_criteria.status: pending`과 1:1.

## 상태 요약 (실측: ~2281 LOC Python · 56 tests green · T1–T6 전부 DONE)
| 영역 | 모듈 | 상태 |
|------|------|------|
| 수집 | `scripts/ingest/{base,kopis,tourapi,websearch}.py` | DONE |
| 정규화/적재 | `scripts/normalize/{to_okf,upsert}.py` | DONE |
| 빌드/검증 | `scripts/build/{validate,build_index,build_sqlite,wiki_index}.py` | DONE |
| 검색 인덱스 | `events.db`(FTS5) + `data/*.json` | DONE |
| 매크로 계획+실행 | `scripts/macro/{apply,runner}.py` (`plan_job`·`run_job`) | DONE |
| 알람 계산+전송 | `scripts/notify/dispatch.py` (`compute_notifications`·telegram/webpush) | DONE |
| AI 플래닝 | `scripts/recommend/{rank,ai_planner}.py` | DONE |
| 지식 wiki | `knowledge/` + `wiki_index.build()` | DONE |

## 진행 중 / 신규 큐 (우선순위 순)

### T1 — Playwright 신청 러너 (F3.4 · AC-F3-runner) `DONE`
- 신규: `scripts/macro/runner.py`
  - `def run_job(job: dict, *, headless: bool = True, dry_run: bool = False) -> dict`
    — `apply.plan_job()` 산출 `job`(steps[])을 소비, `is_auto_submit(job)`가 False면 최종 submit 직전 pause.
  - 스텝 액션 매핑: `fill`(`{selector,value}`), `click`, `pause`(반자동), `assert_text`(결과 캡처).
  - 반환: `{site, mode, submitted: bool, result_text: str, screenshot: path|None}`.
- 신규 픽스처: `tests/fixtures/mock_apply_form.html` (로컬 file:// 폼).
- 신규 테스트: `tests/test_runner.py`
  - `test_auto_site_fills_and_submits_mock` (headless, submitted==True)
  - `test_semi_site_pauses_before_submit` (submitted==False, pause 단계 존재)
- 의존성 게이트: Playwright 미설치 시 테스트 `skipUnless`(무료·선택 의존), CI는 `playwright install chromium`.
- **DoD**: `python -m unittest tests.test_runner` green; C5(약관게이트) 불변.

### T2 — 알람 실채널 마감 (F4.3 · AC-F4-channel) `DONE`
- 기존: `scripts/notify/dispatch.py::_send_telegram` 확장 + Web Push 어댑터 추가.
  - `def _send_telegram(notif: dict) -> bool` — `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` 없으면 dry-run True 반환(무오류).
  - 신규 `def _send_webpush(notif, subscription) -> bool` (선택, VAPID 무료).
- 신규 테스트: `test_dispatch_dryrun_without_token`(stdout 캡처) / `test_send_payload_shape`(전송 페이로드 구조).
- **DoD**: 무토큰 dry-run 무오류 + 토큰 주입 시 200 응답(수동/CI secret). `TestNotify` 회귀 green.

### T3 — Cloudflare 엣지 어댑터 (C7 · 배포) `DONE`
- 신규: `web/worker/ai-proxy.js` — Gemini 키를 서버측 보관하는 프록시(브라우저 키 노출 금지).
- 신규: `web/worker/wrangler.toml` (free tier), `web/worker/jobqueue.js`(매크로 잡 큐 stub).
- **DoD**: `wrangler deploy --dry-run` 통과 + 키가 클라이언트 번들에 없음(`git grep` 0).
- 주의: 벤더 종속 최소화 — flat-file SSOT 유지, Worker는 선택적 가속 계층(C2).

### T4 — UI 5필터·반응형 검증 (F2.3) `DONE`
- 기존: `web/public/{index.html,app.js,styles.css}`.
- 검증 산출: `specs/verify/ui-checklist.md` — 5필터 조합/URL 복원/360px 스냅샷 증거.
- **DoD**: agent-browser 스냅샷으로 5축 필터 + URL 상태 복원 확인(증거 첨부).

### T5 — 멀티소스·지오코딩 품질 (M2 잔여) `DONE`
- 신규: `scripts/normalize/geocode.py` — 캐시→VWorld(선택)→시/도 centroid 3단 폴백.
  `config/regions.yaml`에 17개 시/도 centroid 추가, `knowledge/sources/geocache.json` 캐시.
  `to_okf.run()`이 수집 직후 `geocode.enrich()` 호출(content_hash 재계산 → C2 idempotent).
- **DoD**: 좌표 결측률 0%(< 15% 목표) — 빌드 산출 10/10 좌표 보유. centroid 테이블 17/17 시/도
  커버(결측 제거 보장). 샘플 데이터는 6개 시/도 분포(실소스 적재 시 17개로 확대).
  검증: `geocode.coverage(events)` + tests(TestGeocode 7건).

### T6 — 클린업: 죽은 코드 (drift_guards) `DONE`
- `scripts/run_pipeline.py` `main()` 말미 **중복 `return 0`**(도달불가) 제거.
- **DoD**: `python -m scripts.run_pipeline` rc==0 유지 + 라인 제거.

## 실행 규약 (team)
1. 작업당 1 커밋, 메시지에 `Txx` + 충족 AC id.
2. 커밋 전 게이트: `python -m unittest discover -s tests` green + `validate.py` rc==0.
3. 실패 시 blind retry 금지 → 원인 수정 후 증거 재수집(seed.evaluate).
4. 신규 외부 의존(Playwright 등)은 무료·선택(skip 가능)으로만 도입(C1).
