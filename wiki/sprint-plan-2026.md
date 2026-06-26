---
title: 놓치마 — 구현 스프린트 계획 2026 (S1–S4)
status: ready
okf_type: TechnicalDocument
layer: wiki/sprint-plan
updated: 2026-06-25
sources: [analysis-2026-current-improvements.md, research-2026-saas-local-events.md, specs/spec.md, specs/seed.yaml]
---

# 구현 스프린트 계획 (S1–S4)

> 입력: [[analysis-2026-current-improvements]] 갭 G1–G7 + [[research-2026-saas-local-events]] 아이디어 B1–B12.
> 규칙: 요구를 바꾸는 작업은 `../specs/spec.md`를 먼저 고치고 `seed.yaml` 재동결 후 구현한다. 모든 스프린트 DoD = `python -m unittest discover -s tests` 40+ green + `python scripts/build/validate.py` rc==0 + `events.db` 재생성 동일 카운트 + C1–C7 유지.

## 전체 개요

| 스프린트 | 테마 | 기간(목표) | 매핑 갭/아이디어 |
|---|---|---|---|
| S1 | 운영 기반 견고화 (logging·http·알림 확장) | 1주 | G1·G4·G5 / B6·B12 |
| S2 | FOMO 방어 + 상태기반 UX | 1.5주 | G1·G7 / B2·B3·B4 |
| S3 | AI 개인화 + 한국어 검색 품질 | 1.5주 | G2·G6 / B7·B8·B9 |
| S4 | 매크로 견고성 + 실데이터/배포 | 2주 | G3·G7 / B5·B10·B11 |

우선순위 근거: 운영 기반(S1)이 모든 후속의 전제 → 사용자 가치(S2 알림/페이지) → 개인화(S3) → 리스크 큰 매크로/배포(S4) 순.

---

## S1 — 운영 기반 견고화

### S1-T1 · `print()` → `logging` 전환 (G4) [P0]
- 파일: `scripts/**/*.py`, 엔트리 `scripts/run_pipeline.py`
- 작업: 모듈별 `logger = logging.getLogger(__name__)`; 진행=INFO, 폴백/스킵=WARNING, 매핑실패=ERROR. `run_pipeline.main`에서 1회 `basicConfig`. CLI JSON 덤프는 print 유지.
- AC: 파이프라인 stdout이 `LOG_LEVEL` 환경변수로 제어됨. 기존 58 테스트 green(행위 보존).
- DoD: `OFFLINE=1 python -m scripts.run_pipeline` 정상 + 로그 레벨 분리 확인.

### S1-T2 · `scripts/common/http.py` 공통 retry/backoff (G5) [P1]
- 작업: `post_json(url, body, *, retries=2, backoff=0.5, timeout=30)` — 타임아웃·5xx 재시도, 4xx 비재시도. `ai_planner.plan()`·remote 어댑터가 공유.
- AC(신규 테스트): `test_post_json_retries_on_5xx`, `test_post_json_no_retry_on_4xx`, `test_post_json_falls_back_on_timeout`(httpx 모킹).
- DoD: 무키/오프라인 폴백 무회귀.

### S1-T3 · 신규/오픈 알림 트리거 (G1) [P0] `DONE`
- 파일: `scripts/notify/dispatch.py`
- 작업: `compute_notifications`에 `new-event`(`fetched_at` 최근 24h + 관심필터 일치)·`application-open`(`application_start==today` + status=Open) 추가. `knowledge/notify/sent.json` dedupe를 트리거 종류별로 확장.
- AC(완료): `test_new_event_notification`·`test_application_open_today`·`test_application_open_requires_open_status`·`test_dedupe_across_trigger_types` green. 기존 `test_deadline_d1_*` 유지.
- DoD: dry-run 무오류, 트리거별 독립 dedupe. 전체 73 tests green / validate rc==0 / 파이프라인 rc==0.

---

## S2 — FOMO 방어 + 상태기반 UX

### S2-T1 · 행사 소개 상세 정적 페이지 빌드 (B2) [P1] `DONE`
- 파일: `scripts/build/build_index.py`(또는 신규 `build_pages.py`), `web/public/`
- 작업: OKF 레코드 1건 → 정적 상세 HTML(빌드타임). schema.org/Event JSON-LD 임베드(SEO). 리스트→상세 라우팅.
- AC: `events/<id>` 상세 페이지 N건 생성, JSON-LD 유효. 빌드 멱등(동일 입력 동일 산출).
- DoD: 정적 검증 + 빌드 재실행 동일 카운트.

### S2-T2 · 참가신청 상태 배지·CTA 분기 (B3·B4) [P1] `DONE`
- 파일: `web/public/app.js`, `scripts/build`(파생 `status`)
- 작업: `status` ∈ {신청전, 오픈, 마감임박, 마감} 배지 + 상태별 CTA(알림예약/매크로신청/결과확인). `application-open` & 매크로 프로필 보유 시 잡 적재 후보 노출.
- AC(UI): 4상태 배지 렌더, 마감 행사 CTA 비활성. `status` 파생 단위 테스트.
- DoD: 360px 반응형·URL 상태 복원 무회귀(AC-F2.3).

### S2-T3 · 시/도 경계 GeoJSON 코로플레스 (B1·G7) [P1] `BLOCKED(외부 경계 데이터)`
- 파일: `web/public/`, `config/`
- 작업: 시도 경계 GeoJSON(공개 데이터) + 행사 밀도 코로플레스, 시군구 드릴다운.
- AC: 17개 시/도 폴리곤 렌더, 행사 카운트 매핑 정확.
- DoD: 좌표 결측률 < 15% 유지, 지도 로드 정상.
- 상태(2026-06-26): 코드 외 의존 — 17개 시/도 경계 폴리곤 GeoJSON(공개 데이터) 다운로드 필요. 오프라인 환경에서 정확한 폴리곤 확보 불가로 미착수. `regions.json` centroid는 이미 산출(코로플레스 카운트 매핑 기반 준비됨).

---

## S3 — AI 개인화 + 한국어 검색 품질

### S3-T1 · AI 플래너 가용일 가드레일 + 개인화 (G2·B7) [P1] `DONE`
- 파일: `scripts/recommend/ai_planner.py`
- 작업: 프롬프트에 프로필(나이대·관심축·지역) 명시 + LLM 일정 날짜가 `available_dates` 범위 내인지 검증 가드레일(범위 밖 제거).
- AC(신규): `test_plan_dates_within_available`, `test_reason_reflects_profile`. 기존 환각 가드·폴백 유지(C6).
- DoD: 무키 폴백 무회귀.

### S3-T2 · 한국어 FTS 형태소/N-gram 보강 (B9) [P2] `DONE`
- 파일: `scripts/build/build_sqlite.py`
- 작업: unicode61 한계 보완 — 간이 N-gram/형태소 전처리로 부분일치 색인. flat-file SSOT 불변.
- AC: `test_fts_korean_partial_match_improved`(기존 `test_fts_korean_match` 유지). `events.db` 재생성 동일성.
- DoD: 검색 재현율 개선, C2 유지.

### S3-T3 · 타입 안정성 + mypy CI (G6) [P2] `DONE`
- 파일: `scripts/common/okf.py`(`OkfEvent(TypedDict)`), `scripts/recommend/rank.py`(`ScoredEvent` dataclass, 가중치 상수 `W_REGION/W_THEME/W_AGE`)
- 작업: dict→TypedDict 시그니처 교체, 매직넘버 상수화. `.github/workflows`에 mypy 게이트 추가.
- AC: mypy rc==0, 58 테스트 green(행위 보존).
- DoD: CI에 mypy 단계 추가.

---

## S4 — 매크로 견고성 + 실데이터/배포

### S4-T1 · 매크로 러너 견고성 + fake-page CI 커버 (G3·B5) [P2] `DONE`
- 파일: `scripts/macro/runner.py`, `tests/test_runner.py`
- 작업: 엘리먼트 대기/팝업/타임아웃 예외 처리. playwright를 추상 인터페이스 뒤로 → fake page 스파이로 "submit 제거/pause" 불변(C5)을 chromium 없이 CI 검증. 신청오픈 시각 잡 스케줄(B5).
- AC: `test_semi_mode_no_autosubmit_with_spy`, `test_runner_handles_missing_element`. 기존 게이팅 테스트 유지.
- DoD: skip 3건 → CI 커버(실브라우저는 옵션 마커).

### S4-T2 · 실 API 키 원격 수집 + 500+ 적재 + dedupe (G7·B10) [P1] `BLOCKED(실 API 키·네트워크)`
- 파일: `scripts/ingest/*`, `config/sources.yaml`
- 작업: `.env`/GitHub Secrets로 실 KOPIS·TourAPI·웹검색 키 연결, 다중 소스 dedupe(`same_as`), archived 정책.
- AC: 실데이터 ≥ 500건 적재, 17개 시/도 데이터 존재, 일1회 cron 갱신·신선도 노출.
- DoD: `validate.py` rc==0, 비밀키 미커밋(C4) — `git grep` 키 리터럴 0.
- 상태(2026-06-26): 코드 경로(어댑터·dedupe·archived)는 준비 완료이나 ≥500건 실적재는 실 KOPIS/TourAPI/웹검색 키 + 네트워크 필요 → 오프라인 미검증. 키 주입 시 `ingest.yml` cron이 그대로 수행.

### S4-T3 · Cloudflare Pages/Workers 배포 + usage 모니터 (G7·B11·B12) [P1] `PARTIAL(usage 모니터 DONE · 배포는 계정 필요)`
- 파일: `web/worker/*`, `scripts/ops/usage_report.py`(신규)
- 작업: 정적 Pages 배포 + ai-proxy/jobqueue Workers + KV 구독 저장(B12). 월1회 무료티어 사용률 추정 리포트(B11).
- AC: 배포 URL 200, 엣지 함수 키 서버측 격리(클라이언트 미노출). usage 리포트 산출.
- DoD: 월 0원 무료티어 한도 내(C7), 한도 초과 시 배치 지연/규칙 강등.
- 상태(2026-06-26): `scripts/ops/usage_report.py` 신규 — 무료티어 사용률 추정 리포트 + 한도 초과 시 rc==1(8 테스트). 실 Cloudflare Pages/Workers 배포는 계정·API 토큰 필요로 미수행.

---

## 리스크 & 완화
| 리스크 | 완화 |
|---|---|
| 무료티어 한도 변동 | 다중 제공자 폴백 + `config` 한도 주석 + usage 모니터(S4-T3) |
| 약관 자동화 금지 | 반자동 강등(C5), 사이트별 `automation_allowed` 플래그 |
| 한국어 FTS 품질 | N-gram 보강(S3-T2) + 클라이언트 Fuse 병행 |
| 공공 API 스키마 변경 | 소스별 격리 skip, 어댑터 분리 |
| PII/보안 | 로컬·KV 최소수집, 비밀키 Secrets 주입(C4) |

## 추적
각 태스크는 1커밋, 커밋 전 게이트(테스트+validate) 통과. 완료 시 `../specs/tasks.md`에 DONE 미러링.
