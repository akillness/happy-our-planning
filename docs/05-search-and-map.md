---
title: 검색 & 지도 UI
status: draft
okf_type: TechnicalDocument
updated: 2026-10-15
---

# 05. 검색 & 지도 UI (정적, 무료)

## 1. 프런트엔드 스택
- 정적 `web/public/index.html` + Vanilla JS/CSS. `python3 -m scripts.run_pipeline`로 갱신하며 Vite나 별도 프런트엔드 빌드는 사용하지 않는다. 운영은 기존 Vercel Hobby 프로젝트 `happy-our-planning`이며 주소는 https://happy-our-planning.vercel.app/ 이다.
- 지도: Leaflet + OpenStreetMap 타일. 좌표가 같은 행사는 같은 핀의 목록에서 고른다. 시/도 경계 코로플레스·시군구 드릴다운은 구현 범위가 아니다.
- 검색: Fuse.js 퍼지 검색 + 사전 빌드 `events.json`. 라이브 검색·이미지 생성 provider를 브라우저에서 호출하지 않는다.
- 상태: 기존 5축과 `kids`, `free_only`, `activity`, `applyable`을 URL로 동기화하고 복원한다.

## 2. 데이터 계약 (빌드 산출물)
- `events.json`: 원문 기반 행사·가족/활동 근거·가격·출처 연결 상태를 담은 평탄화 배열.
- `facets.json`: 시/도·테마·나이대·상태·경험 범주 카운트.
- `regions.json`: 지역별 집계와 대표 좌표. 경계 GeoJSON이나 시군구 드릴다운 데이터가 아니다.
- `updated.json`: 빌드 시각과 실제 수집 성공 시각을 구분하고 소스별 offline/live/fixture/error 상태를 표시한다.
- `events.db`와 `events/*.html`: 같은 공개 대상에서 만든 파생 SQLite·정적 상세 페이지이며 knowledge 원본을 대체하지 않는다.

## 3. 화면 구성 — Pencil A 선정
1. 작은 수채화 히어로와 한국어 질문 뒤에 **전체 필터**를 배치한다. 1440px 히어로 그림 높이는 160px이다.
2. 검색창, 지역·테마·나이대·활동·기간 시작/종료, 가족·무료·신청 가능 토글과 초기화를 유지한다. 샘플/수집/출처 안내를 숨기지 않는다.
3. 활동 바로가기는 실제 필터 결과에 존재하는 범주만 보여준다. 과학·탐구와 만들기·예술은 장식 그림을 더하고, 나머지 실제 범주는 텍스트로 탐색한다. 선택은 `activity` URL·드롭다운·결과와 동기화한다.
4. 데스크톱은 왼쪽 지도/오른쪽 목록, 1040px 이하에서는 **목록 다음 지도**다. DOM도 목록을 먼저 두어 모바일 읽기·키보드 순서를 유지한다.
5. 결과는 진행 중/예정 → 종료 순서, 같은 상태에서는 경험 우선이다. 비경험 가족 행사도 숨기지 않는다. 제목·지역·나이·기간·가격·가족/활동 원문 근거·링크 상태를 표시하며 없는 정보는 추정하지 않는다.
6. 상세·조건 맞춤 추천, 로딩·빈 결과·오류/재시도·지도 폴백과 대화상자 포커스 복귀를 보존한다. 최신 검증 HTTPS만 외부 이동을 허용하며 종료 행사는 추천/신청 대상으로 제안하지 않는다.

세 시안과 선정 근거는 `assets/design/pencil-proposals/selection.json`, 모바일/공통 상태를 포함한 PNG는 같은 폴더에 있다. 동결 수용 기준은 `specs/spec.md@1.3.1` F2, 실행 기준은 `specs/seed.yaml`이다.

## 4. 필터 = 데이터 모델 1급 속성
| 축 | 소스/파생 필드 | 컨트롤 | 기본값 |
|---|---|---|---|
| 지역 | `sido` | 단일 드롭다운·지역 바로가기 | 전체 |
| 기간 | `start_date` / `end_date` | 시작·종료 날짜, 기간 겹침 검색 | 전체 |
| 나이대 | `age` / `age_bands` | 단일 드롭다운 | 전체 |
| 테마 | `themes` | 단일 드롭다운 | 전체 |
| 키워드 | `name` / `description` / `themes` | 검색창 | 공란 |
| 가족 | `family_friendly` / `family_evidence` | 아이와 함께 체크박스, `kids=0`으로 해제 | 켜짐 |
| 활동 | `is_experience` / `experience_categories` | 단일 드롭다운·활동 바로가기, URL `activity` | 전체 |
| 무료 | 원문 `price` 기반 `free_only` | 무료만 체크박스 | 꺼짐 |
| 신청 가능 | 신청 시작/종료·상태 | 지금 신청 가능 체크박스 | 꺼짐 |

## 5. 정적 이미지 자산 (runtime provider 호출 없음)
- `web/public/assets/family-outing.webp`: 새 자연·가족 수채화, 1200×675px, 161,798 bytes. 기존 canonical 경로를 유지한다.
- `web/public/assets/family-outing-mobile.webp`: 같은 원본의 모바일 구성, 720×480px, 73,390 bytes.
- `family-science-spot.webp` / `family-craft-spot.webp`: 384×384px, 각각 19,702 / 21,634 bytes. 실제 활동 존재나 행사장 사진의 증거가 아닌 장식용 그림이다.
- 세 새 원본과 Codex/gti 명령·응답·SHA-256은 `assets/design/family-assets.json`에 기록한다. 이전 콘셉트 `family-concept.png`와 생성 이력도 보존한다.
- 디자인 연구용 `family-nature-hero*.webp`는 `assets/design/`에 보관하고 배포 폴더에서 제외한다. 런타임은 위 네 WebP만 정적으로 로드하며 provider 호출은 없다.

## 6. 출처 증거 & 링크 검증 (GET-only, 7day TTL, async)
### 출처 투명성
- 각 행사는 수집 `source`와 provenance용 원본 URL을 가진다. API 주소나 원본 URL 자체가 클릭 가능한 행사 페이지라는 뜻은 아니다.
- UI 외부 이동은 최근 검증된 `link_url`만 사용한다. 미검증·차단·실패 상태는 원인과 함께 텍스트로 남긴다.
- **API 응답 원본**: 실제 제공자 public 페이지, placeholder/example domain 제외
  - ❌ `example.org`, `example.com`, `test.example.kr` — 비공개/fixture 표시, 공개 금지
  - 공공 API 도메인이라도 브라우저용 공개 행사 페이지가 아니면 클릭 가능한 출처로 취급하지 않는다.

### 링크 검증 계약 (C10 제약)
**검증 수행** (opt-in):
- 환경변수 `NOTCHIMA_CHECK_LINKS=1` 또는 일일 cron 자동 활성화
- bounded sender: 최대 64KB 읽음 후 연결 종료, full HTML parse/JS 미수행
- 2xx 응답(body 크기 불문) = `verified` (oversized Content-Length도 reachable 2xx로 판정)
- **GET 방식**만(HEAD 미사용), 리다이렉트 3회 한도(301/302/307/308 추적)
- 네트워크: 40건 한도 · 120초 예산 내
- 예정 중 어린이 경험을 우선하고, 같은 우선순위의 미검사·오래된 검사부터 검증해 실패 URL이 매일 큐를 독점하지 않게 한다.

**검증 결과 (7day cache)**:
- `link_status`: `verified`(7일 이내 2xx GET) | `unverified`(네트워크 실패·타임아웃·만료 등) | `blocked`(정책 위반) | `dead`(404 등) | `unchecked`(미검증)
- `link_url`: `verified`인 HTTPS URL만 노출, 그 외는 `null` + `link_reason` 한국어 설명
- `link_checked_at`: ISO 8601 타임스탬프 (UTC)
- **캐시**: `web/public/data/link-checks.json` (원본 URL 해시 키, 원본 URL/credential 미저장)

**공개 규칙**:
- UI는 `verified` 상태 HTTPS만 클릭 가능한 `[출처 확인]` 라벨로 표시
- `unverified`/`blocked`/`dead`/`unchecked`는 상태+이유 투명 공개, 행사 제거 안 함
- 네트워크 실패/타임아웃 시에도 행사 데이터 보존
- fresh verified proof(7일 이내) 재사용 → 다음 실행에서 네트워크 skip (효율성)

### 90일 파생 보존 정책 (C12 제약)
**보존 기간**: `config/search.yaml.publication_policy.retention_days` (기본 90 KST 일)

**제외 대상**:
- 유효 종료일 (`end_date || start_date`)이 KST 오늘 - 90일보다 **엄격히 이전**인 행사만
- 경계일도 보존한다. 예: 종료일 = KST 오늘 - 90일이면 포함한다.

**적용 범위**:
- `web/public/data/events.json`에서 제외
- 정적 상세 페이지(`web/public/events/<id>.html`) 미생성
- 원본 knowledge 파일(`knowledge/events/<YYYY>/<MM>/<sido>/<id>.md`) **삭제 없음** (이력 보존)

**공개 메타**:
- `updated.json.publication`: `retention_days`, `retained`(공개 건수), `excluded`(보존하지만 비공개) 기록
- SQLite `events.db`는 동일 정책으로 필터링된 후 재생성 (derivation consistent)

**refresh & cache policy**:
- 매일 cron이 `retention_days` 적용 후 build
- fresh verified proof는 동일 URL 해시로 재사용 (7day TTL)
- 오래된 증명(7day 초과)도 파일로 보존, 필요 시 명시 재검사

## 7. 성능 & 접근성
- 초기 로드: `facets.json`+`regions.json` 먼저, `events.json` 지연 로드
- 시/도별 샤드 분할 (`events/seoul.json` 등) 가능
- 키보드 네비게이션(Tab/Arrow/Enter) + ARIA 라벨
- 색약 대비: 코로플레스 팔레트 + 텍스트 라벨
- SEO: 각 행사 정적 상세에 schema.org/Event JSON-LD 주입
- **모바일 반응형**: 360px 이상 가로스크롤 없음, 터치 타깃 최소 44px

## 8. 검증 항목 + 테스트 실행

- **AC-F2.1** SQLite FTS5 한국어: `python -m unittest tests.test_pipeline.TestSqlite.test_fts_korean_match`
- **AC-F2.1** 필터 조합: `python -m unittest tests.test_pipeline.TestSqlite.test_filter_sido_and_theme`
- **AC-F2.4** 가족 필드: `python -m unittest discover -s tests -p test_family_discovery.py -v` (6개)
  - 명시 근거만: `test_explicit_family_or_child_evidence_is_true_and_explained`
  - 성인 전용/정보 부족 제외: `test_adult_only_universal_and_missing_information_are_not_family`
  - 나이 범위 제외(13세 이상): `test_minimum_age_ratings_and_teen_range_do_not_promote_family`
  - 제외 표현 우선: `test_prohibition_phrases_override_positive_words`
  - 파생 필드 전파: `test_flatten_propagates_derived_family_fields`
  - fixture 정확도: `test_current_fixture_has_exact_four_family_events_with_evidence`
- **AC-F2.5** 행사 경험: `python -m unittest discover -s tests -p test_experiences.py -v` (구현 완료)
  - 손-활동 근거: `websearch:bb38c44890b9`(과학·탐구 true) vs `kopis:PF200002`(음악 감상, false)
  - 원문 기반만: 악기 체험/참여 명시 시만 `만들기·예술`로 분류
  - 관람형 제외: 공연/음악/인형극 관람만으로는 true 아님
- **AC-F2.6** 링크 검증 + 공개: `python -m unittest tests.test_links.py -v`
  - fresh verified proof 재사용: `test_link_cache_reuse`
  - 7day TTL: `test_link_ttl_boundary`
  - 40건/120초 큐: `test_link_checker_budget`
  - 어린이 경험 우선: `test_link_checker_experience_priority`
  - URL 해시 캐시: `test_link_cache_no_raw_url_storage`
- **AC-F2.7** 90day 보존 정책: `python -m unittest tests.test_retention.py -v`
  - 경계일: `test_retention_boundary_included`
  - 이전 날 제외: `test_retention_boundary_excluded`
  - 집계 보고: `updated.json.publication` 존재 확인
- **AC-F2.3** UI 응답형 + 타깃 크기: 브라우저 스냅샷 (360px 폭, 44px 터치 타깃, WCAG AA 대비).
- **AC-F2.2** 빌드 산출물: `web/public/data/facets.json`, `regions.json`, `updated.json`, `link-checks.json` 존재.
- **샘플/오프라인 데이터**: `raw/**/sample-*` 픽스처 → `run_pipeline` 키 불필요.

## Vercel 운영 배포 (2026-10-15)

기존 프로젝트는 Framework `Other`, Root Directory `web/public`, 명시적 build/output override 없음, GitHub `akillness/happy-our-planning`의 `main`에 연결되어 있다. 이번에는 검증한 21개 브라우저 파일을 `web/dist/vercel-release/.vercel/output/static`에 배치하고 `config.json`의 `version: 3`으로 prebuilt 업로드했다. DB·설계 원본·Worker는 포함하지 않았다.

초기 수동 배포 당시 인증된 `vercel curl`로 보호된 배포 URL의 HTML·행사 JSON·양쪽 히어로를 확인한 뒤 `vercel promote`로 같은 배포를 운영에 승격했다. 당시 운영 URL은 인증 우회 없이 9개 필수 파일의 HTTP 200·SHA-256 일치, DB/환경 파일/Worker의 404를 확인했다. 이후 Git 배포와 구분한 상세 증거는 `specs/verify/evidence/vercel-release.json`에 남긴다.

초기 배포는 검증한 로컬 산출물의 직접 업로드였다. 기존 Git 연동은 `akillness/happy-our-planning`의 `main`을 감시하며, 일일 수집 push도 자동 배포를 시작한다. 개편의 영구 반영은 UI·수집/빌드 코드·테스트·네 WebP를 함께 `origin/main`에 커밋·push하는 방식으로 관리한다. 초기 수동 업로드와 이후 Git 소스 배포의 재검증은 `specs/verify/evidence/vercel-release.json`에서 구분한다. `notchima.vercel.app`은 Git 연결이 없는 별도 프로젝트이며 이번 Git 배포 대상이 아니다.
