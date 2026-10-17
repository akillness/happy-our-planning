---
title: 놓치마 — 하니스/증거 계약 (cli-anything 레이어)
status: active
okf_type: TechnicalDocument
layer: spec-stack/tools
updated: 2026-10-17
---

# 하니스 & 증거 계약 (Tools 레이어)

> spec-stack 규칙: **종료코드가 아니라 산출물을 검증**한다. evaluate 단계가 받는 증거는
> 아래 명령의 산출물(JSON/파일/카운트)이다. 새 하니스는 레지스트리 우선, 생성은 폴백.

## 검증 하니스 (이미 존재하는 스크립트를 하니스로 사용)
| 하니스 | 명령 | 증거(artifact) | 게이트하는 AC |
|--------|------|----------------|----------------|
| pipeline | `python -m scripts.run_pipeline` | `web/public/data/{events.json,facets.json,regions.json,updated.json}` + rc | AC-F1, AC-F6 |
| schema-validate | `python scripts/build/validate.py` | rc==0 + 위반 레코드 목록 | C3, AC-F1 |
| sqlite-rebuild | `python -m scripts.build.build_sqlite` | `events.db` 재생성 + row 카운트 | C2, AC-F2-search |
| tests | `python -m unittest discover -s tests` | Ran N / OK 요약 | 전 AC 회귀 |
| timeline-directions | `python3 -m unittest tests.test_timeline_directions -v` | unittest `Ran N` / `OK` 요약 | AC-F7.2, AC-F7.3 |
| timeline-import | `python3 -m unittest tests.test_timeline_import -v` | unittest `Ran N` / `OK` 요약 | AC-F8.1, AC-F8.2 |
| timeline-verify | `python3 specs/verify/timeline_check.py` | JSON {checks[], passed/total, error?} | AC-F7.1, AC-F7.3 |
| history-import-verify | `python3 specs/verify/history_import_check.py` | JSON {checks[], passed/total, boundary} | AC-F7.1, AC-F7.2, AC-F8.1, AC-F8.2, AC-F8.3 |
| macro-plan | `python -m scripts.macro.apply <event.json>` | `job`(steps[], mode) JSON | AC-F3-gate |
| macro-run(신규) | `python -m scripts.macro.runner --job job.json --headless` | `{submitted, result_text, screenshot}` JSON | AC-F3-runner |
| notify | `python -m scripts.notify.dispatch` | 전송/ dry-run 요약 JSON | AC-F4 |
| ai-plan | `python -m scripts.recommend.ai_planner <profile.json>` | 주간 플랜 JSON(responseSchema 정합) | AC-F5 |
| survey-validate | `python3 ~/.agents/skills/survey/scripts/validate_survey_artifacts.py .survey/<slug> --platform-topic` | OK/FAIL 행 | 발견 계약 |
## 증거 규율
1. **재생성 동일성**(C2): `rm web/public/data/events.db && python -m scripts.run_pipeline` →
   `sqlite3 events.db "select count(*) from events"`가 이전과 동일.
2. **무키 오프라인 통과**(C1): 환경변수 키 미설정 상태에서 위 모든 하니스 rc==0.
3. **키 비노출**(C4): `git grep -nE "(API_KEY|TOKEN)\s*[:=]\s*['\"][A-Za-z0-9_-]{12,}"` → 0 매치.
4. **AI 가드레일**(C6): ai-plan 산출 플랜의 모든 `event_id`가 후보 집합의 부분집합.

## UI 후퇴·기능 게이트 (F7/F8)

**기존 기능 무손실 검증** — F7(타임라인/길찾기/메뉴)의 추가로 기존 지도/검색/5축/상세/바로가기 동작 회귀 테스트.  
**최종 상태 (2026-10-17):** 아래 5개 브라우저 게이트 모두 종료코드 0. 전체 unittest 263/263 PASS, 무키·오프라인 파이프라인 종료코드 0, OKF 440/440 PASS, SQLite 재생성 전후 427건 동일. 증거는 [`verify/evidence/`](verify/evidence/)에 보관합니다.

| 테스트 | 명령 | 상태 |
|--------|------|------|
| 기존 지도 5축 필터 | `python3 specs/verify/run_ui_check.py` | **72/72 PASS** |
| 기존 지도 뷰포트 | `python3 specs/verify/map_viewport_check.py` | **139/139 PASS** |
| 기존 UI 지역화·지도 상태 | `python3 specs/verify/ui_locale_check.py` | **172/172 PASS** |
| F7 타임라인 | `python3 specs/verify/timeline_check.py` | **60/60 PASS** |
| F8 방문 기록 가져오기 | `python3 specs/verify/history_import_check.py` | **21/21 PASS** |

추가 회귀는 명시적 필터 맞춤과 언어 보존이 겹치는 경우를 실제 앱 상태·`fitBounds` 호출·지도 bounds로 검증합니다. 결과 패널을 접어 지도 공간을 확보한 동일 조건에서 현재 코드는 통과하고, 옛 preserve 우선 가드를 복원한 메모리 전용 실패 대조(negative control)에서는 추가 회귀가 실패했습니다. 대조 실행은 프로덕션 파일을 변경하지 않았으며, 정준 증거에는 정상 실행 결과만 남겼습니다.


## 신규 하니스 도입 정책 (cli-anything)
- 먼저 레지스트리/기존 스크립트 재사용. 없을 때만 생성.
- 외부 도구는 무료·선택. Playwright는 `playwright install chromium`(CI) / 로컬 미설치 시 테스트 skip.
- 새 하니스는 반드시 `--json`(또는 구조화 stdout) 산출을 제공해 evaluate가 파싱 가능해야 한다.
