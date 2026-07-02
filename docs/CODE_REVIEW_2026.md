# 코드 분석 · 실동작 검증 · 개선 설계 (놓치마)

> 라우팅: **coding 파이프라인 → debugger(실동작 검증) + python-expert(개선 설계)**.
> 모든 지적은 재현 가능한 근거(실행 로그·파일:라인)에 기반한다.

## 1. 실동작 검증 (verified, 추측 아님)

| 검증 | 명령 | 결과 |
|---|---|---|
| 단위 테스트 | `python -m unittest discover -s tests -v` | **55 pass / 3 skipped** (playwright 미설치로 macro 자동제출 경로 skip), 0.09s |
| 전체 파이프라인(offline) | `OFFLINE=1 python -m scripts.run_pipeline` | 정상 완주: 수집 9건 → 검증 10/10 → index events=10 → SQLite 10건(FTS5) → wiki active=10 |
| 의존성 | `requirements.txt` | pyyaml·jsonschema·httpx만, 표준 lib 위주. offline 폴백 설계 양호 |

**기준선 평가**: bare `except:` 0건, mutable default 인자 0건 — 치명 안티패턴 없음. 키 부재 시 fixture/규칙기반으로 자동 강등되는 폴백 설계가 일관적. 아래는 그 위에서의 개선 방향이다.

## 2. 개선 설계 (우선순위순)

### 🟠 HIGH — 관측성: `print()` → `logging` 전환
**근거:** `scripts/` 전반 25+ 호출이 모두 `print()` (logging 모듈 사용 0건). run_pipeline·ingest·notify가 stdout에 직접 출력 → CI 로그 레벨 분리·구조화 불가, 실패 진단이 grep 의존.
**설계:** 모듈별 `logger = logging.getLogger(__name__)`; 진행=INFO, 폴백/스킵=WARNING, 매핑 실패=ERROR. 엔트리포인트(`run_pipeline.main`)에서 한 번만 `basicConfig`. CLI 출력(JSON 덤프)은 print 유지하되 진단 로그와 분리.

### 🟠 HIGH — 네트워크 견고성 공통화 (retry/backoff)
**근거:** `ai_planner.plan()` (ai_planner.py:173-181)은 `httpx.post(timeout=30)` 단발 + 광범위 `except Exception` 폴백. `base._fetch_remote`는 어댑터마다 재구현 예정. 일시적 5xx/타임아웃에 재시도 없음.
**설계:** `scripts/common/http.py`에 `post_json(url, body, *, retries=2, backoff=0.5, timeout=30)` 공통 헬퍼 — 재시도 대상(타임아웃·5xx)과 비재시도(4xx)를 구분. ai_planner·각 remote 어댑터가 공유.

### 🟡 MEDIUM — 타입 안정성: OKF 이벤트에 `TypedDict`, 점수에 `dataclass`
**근거:** TypedDict/dataclass 사용 0건. 이벤트가 전부 `dict`로 흘러 키 오타가 런타임까지 잠복(`rank.plan_week`의 `c["id"]`/`c["_reasons"]` 직접 접근). `_is_free(price)` (rank.py:39) 등 일부 인자 타입힌트 누락.
**설계:** `scripts/common/okf.py`에 `class OkfEvent(TypedDict, total=False)` 정의 후 시그니처 교체. `score_event`의 `tuple[float, list[str]]` 반환을 `@dataclass ScoredEvent`로 — 호출부 가독성·mypy 검증 확보.

### 🟡 MEDIUM — PEP8: 세미콜론 복합문 정리
**근거:** `rank.py` 8곳, `build_sqlite.py` 9곳에서 `score += 3; reasons.append(...)` 형태 한 줄 복합문 (PEP8 E702). 점수 가중치(3/2/1)도 인라인 매직넘버.
**설계:** 복합문 분리 + 가중치를 모듈 상수(`W_REGION = 3` 등)로 추출 → 추천 로직 튜닝 지점 단일화, 향후 A/B 가중치 실험 용이.

### 🟡 MEDIUM — 테스트 공백: macro 자동제출 경로
**근거:** 3개 skip은 모두 `runner.run_job`의 실브라우저 경로(playwright 미설치). 안전 불변(C5: 비자동잡 submit 제거)의 핵심 분기가 CI에서 미실행.
**설계:** playwright를 추상 인터페이스 뒤로 두고 fake page(스파이)로 step 디스패치를 검증 → chromium 없이도 "submit 제거/ pause 정지" 불변을 CI에서 보장. 실브라우저 테스트는 옵션 마커로 유지.

## 3. 구현 현황 ($autopilot 실행 결과)

| 개선 | 상태 | 증거 |
|---|---|---|
| PEP8 세미콜론 + 점수 가중치 상수화 | ✅ 완료 | `rank.py` 세미콜론 0, `W_REGION`/`W_THEME`… 상수; `build_sqlite.py` 복합문 3건 분리 |
| http retry/backoff 공통화 | ✅ 완료 | `scripts/common/http.py` 신설, `ai_planner.plan()` 연동, 신규 테스트 7건(`TestCommonHttp`) |
| macro 안전 불변(C5) CI 테스트 | ✅ 완료 | `runner._filtered_steps`/`_drive` 추출, fake-page 스파이로 chromium 없이 검증, 신규 5건(`TestRunnerUnit`) |
| logging 도입(관측성) | 🟡 부분 | `run_pipeline`·`ingest/base` 진단 로그를 `logging`으로 전환. `notify.dispatch`의 DRY/SENT는 **테스트된 stdout 계약**이라 의도적으로 print 유지 |
| TypedDict/dataclass | ⏸ 보류 | mypy CI 추가와 묶어 진행 권장(별도 슬라이스) |

**검증:** 단위 테스트 **70 pass / 3 skip**(58→70, +12 신규), `OFFLINE=1 run_pipeline` 엔드투엔드 완주(events=10, sqlite 10, wiki 10), ai_planner 무키 폴백 정상.

## 4. 다음 단계 (Next step)
TypedDict/dataclass 도입은 mypy를 CI에 추가하는 작업과 묶어 별도 슬라이스로 진행. logging 전면 전환은 notify의 stdout 계약을 구조화 로그로 옮길지 제품 결정 후 확대.


## 5. 후속 리뷰 — 정확성 버그 수정 (2026-06-28, critic 라우팅)

> 베이스라인(96 tests·mypy clean·`OFFLINE=1 run_pipeline` rc==0) 위에서 오프라인 검증 가능한 정확성 버그만 수정. 외부 키·네트워크·브라우저·경계데이터 의존 항목(BLOCKED)은 제외.

| 결함 | 위치 | 영향 | 수정 |
|---|---|---|---|
| 🔴 중복 `upsert` 호출 | `normalize/to_okf.py:58-59` | 동일 `upsert` 2회 → 2번째가 모두 `skipped`로 덮어써 `created/updated` 항상 0 보고(수집 로그·summary 왜곡), 파일 IO 2배 | 중복 라인 제거(단일 호출) |
| 🟠 형제 시/도 접두 붕괴 | `common/config.py:49` | `name.startswith(canon[:2])`가 같은 2자 접두 공유 시/도(충청남/북·경상남/북)를 `regions.yaml` 첫 항목으로 붕괴 → 부분/더티 입력 ~절반 오분류 | 단일 후보만 채택 + 형제는 남/북 보조 글자로 판별, 모호하면 보존 |
| 🟡 광범위 `except` 은폐 | `notify/dispatch.py:64` | `fetched_at` 파싱 실패를 `except Exception: pass`로 삼켜 `new-event` 트리거 무단 누락 | `(ValueError, TypeError)`로 좁힘 |

**검증:** 단위 테스트 **97 tests(94 pass / 3 skip)** — 신규 회귀 가드 `test_run_reports_created_not_all_skipped`(중복 upsert), `test_canonical_sido_alias_and_partial` 확장(형제 시/도 4건 + 모호 보존). mypy rc==0, `OFFLINE=1 run_pipeline` rc==0, `validate.py` 10/10 rc==0. C1–C7 불변.

> 검토했으나 **의도적 미수정**: `dispatch.delivered`가 dry-run도 카운트하는 것은 `test_*`가 고정한 stdout/dedupe 계약(AGENTS.md notify §)이라 보존.


## 6. 후속 리뷰 2 — 빌드/발견 정확성 (2026-06-28, critic 라우팅 #2)

> 미검토였던 build/normalize/ingest 10개 모듈 전수 critic 리뷰. 오프라인 검증 가능한 정확성 결함만 수정.

| 결함 | 위치 | 영향 | 수정 |
|---|---|---|---|
| 🟠 마감일만 있는 행사 배지 누락 | `build/build_index.py:75` `derive_status` | `application_start` 부재(정부지원형 다수) + `status≠Open` 행사는 `open_now`가 항상 False → 마감 전까지 "신청전", 마감 후 곧장 "마감"으로 점프. **핵심 FOMO 배지 "오픈/마감임박"이 도달 불가** | open 윈도 확장: 시작일 미상이어도 기간 내(`not astart or astart<=today`)면 오픈, 단 날짜·상태 신호가 전혀 없으면 신청전 보존 |
| 🟠 SOURCES 인덱스 비멱등 | `build/wiki_index.py:43` | "최근 갱신"에 `dt.date.today()`를 전 소스 동일 스탬프 → 값 부정확 + 데이터 무변경에도 매 실행 `knowledge/index.md`(SSOT) 재기록(허위 git diff) | 소스별 실제 `fetched_at` 최댓값에서 파생 → 연속 실행 byte-identical |
| 🟡 신뢰 도메인 경계 오매칭 | `ingest/websearch.py:218` `_confidence` | `dom.endswith("go.kr")`가 `notgo.kr` 등 라벨 경계 아닌 유사 도메인에 +0.1 가점 → `min_confidence` 우회 가능 | `dom == t or dom.endswith("." + t)` 라벨 경계 매칭 |
| 🟡 Brave 날짜 오파싱(잠재) | `ingest/websearch.py:113` `parse_brave` | 상대표현 `age`("3 days ago")를 ISO `page_age`보다 우선해 `[:10]` 절단 → "3 days ag". 현재 `published` 미소비라 무해하나 잠재 버그 | ISO `page_age` 우선 |

**검증:** 단위 테스트 **104 tests(101 pass / 3 skip, +7)** — `derive_status` 마감일전용 3건 + 무날짜 비오픈 보존 1건, `_confidence` 유사도메인 무가점 1건, `wiki_index` fetched_at 파생·재생성 멱등 2건. mypy rc==0, `OFFLINE=1 run_pipeline` rc==0(연속 실행 `index.md` byte-identical 확인), `validate.py` 10/10 rc==0. C1–C7 불변.