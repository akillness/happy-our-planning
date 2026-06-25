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
