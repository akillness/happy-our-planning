---
title: 놓치마(Notchima) — 동결 명세 (SSOT)
status: frozen
okf_type: TechnicalDocument
layer: spec-stack/spec
spec_version: "1.6.0"
updated: 2026-10-15
source_of_truth: document        # 실행 SSOT는 specs/seed.yaml
supersedes_doc_map: docs/00-overview.md §6 (성공 기준을 기계검증형으로 승격)
---

# 놓치마 — 제품 명세 (Write 레이어)

> spec-stack 패턴 A(full-stack). 이 문서는 **문서 SSOT**다. 실행 SSOT는
> [`seed.yaml`](seed.yaml), 작업 분해는 [`tasks.md`](tasks.md), 검증 증거 계약은
> [`cli-harness.md`](cli-harness.md). 요구가 바뀌면 **이 문서를 먼저 고치고** seed를 재동결한다.
> 발견(discovery) 근거는 `.survey/free-saas-websearch-ai-stack-2026/`.

## 1. 한 줄 정의
대한민국 지도 기반으로 전국 행사(공연·축제·전시·교육·공모·정부지원)를 **지역·기간·나이대·테마·키워드**
5축으로 검색하고, 어린이·가족 행사를 보수적으로 구분해 기본 탐색하며(`kids=true`, `kids=0`이면 해제),
아이들이 실제로 참여할 수 있는 손-활동을 출처 문구에서만 추려 활동 범주·근거와 함께 보여주고,
**신청 매크로**로 신청기간 내 신청을 보조하고, **결과 알람**으로 통지하며, **AI 추천 플래닝**으로 주간 일정을 설계하는 경량·무료·파일기반 웹 인프라.

## 2. 동결 제약 (Constraints) — 위반 시 스펙 드리프트
| ID | 제약 | 기계 검증 방법 |
|----|------|----------------|
| C1 | 유료 의존 0. 외부 호출은 무료티어 한도 내(공공 API·Exa/Brave/Tavily·Gemini/Groq 무료등급). 런타임은 외부 모델·검색·이미지 제공자를 호출하지 않고 정적 산출물만 제공 | `config/*.yaml`에 유료 엔드포인트 없음 + 키 부재 시 오프라인 픽스처로 전 파이프라인 통과 |
| C2 | 지식 DB = flat-file. 별도 DBMS 서버 없음. `events.db`는 **재생성 가능한 파생물** | `web/public/data/events.db` 삭제 후 `run_pipeline`로 재생성 동일 카운트 |
| C3 | 각 행사 레코드는 schema.org/Event 정합 OKF(JSON-LD frontmatter) | `scripts/build/validate.py` rc==0, `knowledge/schema/event.schema.json` 준수 |
| C4 | 비밀키는 저장소 미커밋. `.env`/GitHub Secrets로만 주입 | `git grep`에 키 리터럴 0, `.env`는 gitignore |
| C5 | 약관상 자동화 금지 사이트는 매크로 자동제출 불가(반자동 강등) | `tests` `test_tos_blocked_site_is_semi_no_autosubmit` 통과 |
| C6 | AI 출력은 responseSchema 강제 + 환각 id 제거 + 규칙 폴백 필수 | `test_constrain_drops_hallucinated_ids`·`test_plan_falls_back_without_key` 통과 |
| C7 | 월 운영비 = 0원(free tier 한도 내), 기존 Vercel 프로젝트의 정적 배포를 사용한다. 생성형 이미지·추천 provider는 빌드/개발 시 선택 사항이며 런타임 필수 의존이 아니다. 별도 Cloudflare Worker는 이번 배포에 포함하지 않는다 | 정적 파일만 업로드, 서버 함수·유료 빌드 의존 없음, 호스팅·CI 무료티어 한도 준수 |
| C8 | 가족 판정은 실제 `age`·`age_bands`·`audience`·`name`·`description`·`themes`의 명시 근거만 사용한다. 성인 전용·노키즈·어린이/아동/유아/미취학아동/미성년자 토큰 가까이의 불가·금지·제한·사절·입장불 표현은 제외하고, 학부모·보호자 전용/만 참석 대상은 아동 참석 근거가 없으면 제외한다. 노키즈·금지 표현의 부정문 여부는 해석하지 않고 안전상 false로 둔다. 수치 연령은 상한 13세 이하 범위만 인정하며, 전연령 단독·정보 부족은 unknown으로 유지한다 | `family_friendly=true`이면 `family_evidence` 비어 있지 않음 + 성인 전용/아동 접근 제한/보호자 전용/전연령 단독/정보 부족 fixture가 true 아님 |
| C9 | 활동 분류는 원문 `name`·`description`·`themes`의 손-활동 근거만 사용한다. `experience_categories`는 허용된 한국어 범주 배열, `experience_summary`는 짧은 원문 근거, `is_experience`는 근거 존재 여부다. 관람·공연·인형극·음악 감상만으로는 true가 아니다 | 순수 `derive_experiences(...)`와 현재 4개 family fixture의 범주·근거·관람형 제외 확인 |
| C10 | 외부 링크는 빌드/예약 수집에서만 검증한다. 검증은 브라우저와 같은 bounded GET 하나의 내비게이션 방식으로 수행하고 모든 GET 리다이렉트(최대 3회)를 수동 검증한다(HEAD는 충분한 증명이 아니며 필수가 아니다). 기본 sender는 `config/search.yaml`의 `max_response_bytes`(기본 64KB)까지만 스트림을 읽고 닫으며, `Content-Length`가 한도를 넘더라도 제한 내에서 도달한 2xx 응답은 검증 가능하므로 전체 HTML 다운로드·파싱·JS 실행을 하지 않는다. 정적 공개는 최근 7일 이내 `verified`이고 자격증명 없는 HTTPS인 경우에만 `link_url`을 노출하며, 오프라인/실패/만료에도 행사 레코드는 보존한다. 공개 캐시는 원본 URL 해시만 키로 저장하고 원본 URL·userinfo·credential query를 저장하지 않는다. `link_status`·`link_reason`은 검증 불확실성을 숨기지 않는다 | link cache 순수 판정과 오프라인 build 결과; 네트워크 checker는 명시 opt-in일 때만 실행 | 
| C11 | 일일 GitHub Actions 수집은 기존 03:00 KST cron 하나만 사용하고 동시 실행을 직렬화한다. 실패·부분 실패는 Actions job 상태와 `knowledge/sources/collection-status.json`의 소스별 mode/last_success_at/error로 드러내며, fixture 빌드 시각을 live 성공으로 오인하지 않는다 | workflow concurrency와 source-status sidecar, `updated.json.sources`/집계 메타데이터 확인 |
| C12 | 파생 공개 산출물은 `config/search.yaml`의 `publication_policy.retention_days`(기본 90 KST 일)를 사용한다. 유효 종료일 `end_date || start_date`가 KST 오늘-보존일수보다 엄격히 이전이면 `events.json`과 정적 상세 페이지에서 제외하지만 원본 knowledge 이력은 삭제하지 않는다. 경계일은 보존하고, `updated.json`에 보존/제외 건수를 보고한다 | 주입 시계의 경계 테스트와 build 산출물의 retained/excluded 집계 |
| C13 | 일일 링크 검사 큐는 현재 공개 대상 URL만 사용하고, 7일 이내 `verified` 증명을 다음 실행에도 재사용해 fresh proof의 네트워크 검사를 생략한다. 실제 네트워크 검사는 최대 40건·총 120초 이내다. 실제 예약 URL을 정보 URL보다 먼저 검사하고, 각 종류 안에서는 예정/진행 어린이 경험 → 예정/진행 가족 → 그 외 현재 행사 → 종료 행사 순으로 우선한다. 더 이상 공개되지 않는 URL의 증명은 같은 해시 캐시에서 정리하고 두 번째 링크 캐시를 만들지 않는다 | 주입 시계/큐 fixture에서 fresh 재사용·합산 검사 수·120초 예산·예약/가족 우선순위·prune 확인 |

C10의 목적별 도메인 정책: 정보 링크는 기존 `trusted_domains`만 사용한다. 원천이 제공한 예약 링크만 `role="booking"`으로 처리하고 별도 `booking_trusted_domains`의 근거 있는 호스트를 추가로 허용한다. 예약용 허용 목록은 검색/랭킹·정보 링크 정책으로 확장하지 않는다. 최초 URL·모든 리다이렉트·캐시 읽기/재사용/쓰기에서 해당 목적의 정책을 다시 검증한다. 캐시는 기존 URL SHA-256 키 하나를 유지하며 같은 URL의 예약용 증명을 정보 링크의 신뢰 확장으로 재사용하지 않는다. 원천의 HTTP 예약 URL은 같은 호스트/경로/쿼리를 유지하는 HTTPS 전환만 허용하고 원천 예약 URL 원문은 기존 상세 캐시에 보존한다.

## 3. 데이터 모델 (계약)
정규 레코드 = `knowledge/schema/event.schema.json`. 필수 키:
`id, name, start_date, url, location.sido, source, fetched_at, content_hash`.
1급 필터 축은 기존 5축을 유지하며 모델의 정규 속성으로 보장한다:
- **지역** → `location.sido` / `location.sigungu` (+ `lat/lng` 33–39N·124–132E)
- **기간** → `start_date / end_date / application_start / application_end` + 파생 `status`
- **나이대** → `age` / `audience` (`config/age-bands.yaml`로 밴드 매핑)
- **테마** → `themes[]` (`config/themes.yaml`로 정규화), `event_type` enum
- **키워드** → `name / description / themes` 풀텍스트 (FTS5 + 클라이언트 Fuse.js)
- **추가 무료 필터·원문 요금** → `free_only`는 기존 정규 `price`만 사용한다. 원천 요금 문구는 `price_text`에 그대로 보존하고 목록·동적/정적 상세에서 표시한다. `전석무료`처럼 전체 무료가 명확한 값만 `price="free"`, 단일 확정 금액만 숫자로 정규화하며 다중 등급/조건부 무료/모호한 요금은 정규 레코드의 `price`를 생략한다(정적 인덱스에서는 null). `price_text`는 보존 필드일 뿐 가격 추정·무료 판정에 사용하지 않으며 JSON-LD에는 유효한 정규 가격만 내보낸다.
- **어린이·가족 판정(파생)** → 정적 인덱스에 `family_friendly`(boolean)와 `family_evidence`(짧은 한국어 근거)를 추가한다. 판정은 `age`, `age_bands`, `audience`, `name`, `description`, `themes`의 실제 텍스트에서만 보수적으로 도출한다. 어린이 전용/가족 대상 연령·체험·프로그램·축제 신호가 명시될 때만 `true`; 수치 연령은 범위 상한이 13세 이하일 때만 인정하고 `만 7세 이상` 같은 최소 연령이나 `13-18`은 인정하지 않는다. 성인 전용·청소년/성인 제한·노키즈·어린이/아동/유아/미취학아동/미성년자 접근 불가 표현과 학부모·보호자 전용/만 참석 대상은 아동 참석 근거가 없으면 `false`; 부정문처럼 보이는 노키즈·금지 표현도 안전상 `false`로 유지한다. `전연령`·`all ages` 단독 표기나 정보 부족은 `true`로 승격하지 않는다. 정보 부족은 `false`와 함께 `family_evidence`에 `근거 부족`을 남겨 unknown으로 보존하고, `true`인 모든 레코드는 비어 있지 않은 근거를 가져야 한다. 기존 `age_bands`와 일반 행사 결과는 그대로 유지한다.
- **활동 경험(파생)** → 정적 인덱스에 `experience_categories`(허용 범주 배열), `experience_summary`(원문에서 잘라낸 짧은 근거 또는 null), `is_experience`(boolean)를 추가한다. 자연·생태/과학·탐구/만들기·예술/농촌·먹거리/신체·놀이/기타 체험 중 실제 참여·실습·관찰·제작·놀이 등 손-활동이 명시된 경우만 true다. 공연·음악·인형극·전시 관람만 있는 문구는 false다. `derive_experiences(...)`는 `derive_family_flags(...)`의 보수적 아동 토큰·제외 판단을 재사용하고, 나이·시설·준비물·요금은 추론하지 않는다.
- **링크 검증(파생)** → 원본 `url`은 flat-file에 유지하고, `scripts.common.links.link_fields`가 `link_status`(unchecked/verified/unverified/blocked/dead), `link_checked_at`(ISO/null), `link_url`(검증된 HTTPS 또는 null), `link_reason`(짧은 비민감 한국어)를 붙인다. 네트워크 checker는 브라우저와 같은 bounded GET만 사용해 모든 GET 리다이렉트를 검증하며 HEAD를 proof로 사용하지 않는다. `web/public/data/link-checks.json`은 원본 URL 해시 키와 안전한 verified 최종 `link_url`만 담는 파생 캐시이며 SSOT가 아니다(원본 URL/userinfo/credential query는 캐시에 저장하지 않음). `knowledge/sources/collection-status.json`의 소스별 수집 상태는 `updated.json.sources`로 그대로 복사한다.
- **상세·예약 정보** → 원본 출처 `url`과 실제 예매처/신청처 URL을 구분한다. 제공자가 명시한 예약 목적지는 `offers.url`로 정규화하고 중첩 `offers`를 정적 인덱스에도 보존한다. 기존 단일 링크 검증 캐시/안전 정책으로 정보 링크와 독립된 `booking_link_status`·`booking_link_checked_at`·`booking_link_url`·`booking_link_reason`을 생성한다. 출처 상세 페이지나 홈페이지를 예약 사이트로 승격하지 않는다. 원문 설명·장소·주소·일정·관람/참여 연령·요금·주최자·소요시간은 제공된 경우 상세 화면과 정적 상세에 보존하고, 없는 정보는 추측하지 않는다. 소요시간 원문은 `duration_text`에 보존하고 모호하지 않은 경우만 ISO 8601 `duration`으로 추가 정규화한다. JSON-LD에는 검증된 각 목적지와 유효한 ISO duration만 내보낸다.
- **운영 신선도(파생)** → `updated.json`은 기존 `generated_at`/`freshness`를 유지하고 `source_mode`(offline/live/mixed), `last_success_at`(성공한 live 수집만), `collection_started_at`, `collection_error_count`, `collection_errors`를 집계한다. fixture build 시각은 live 성공으로 기록하지 않는다.

## 4. 기능 명세 + 기계검증형 수용기준(AC)
각 AC는 **명령 + 기대 산출물**로 검증한다(“보기 좋다” 금지).

### F1 — 수집·적재 (지식 DB 인프라)
- 동작: 어댑터(`scripts/ingest/{kopis,tourapi,websearch}.py`) fetch→`to_okf`→`upsert`→`validate`→`build`.
  신규/변경분만 diff upsert, 출처·`fetched_at`·`content_hash` 유지.
- KOPIS/TourAPI 원격 경로는 공통 `PublicApiCollector`를 통해 실제 `_fetch_remote`를 구현한다. 페이지네이션(상한 포함), 조회일/행사일 범위 파라미터, 공통 retry/timeout을 사용하고, 오프라인 기본 경로는 네트워크를 호출하지 않는다. 테스트는 bounded fake transport로 실제 수집 경로를 검증한다.
- **AC-F1.1** 키 없이 `python -m scripts.run_pipeline`(오프라인 픽스처) rc==0, `events.json` 생성.
- **AC-F1.2** 동일 입력 재실행 시 upsert가 멱등(생성→skip): `test_create_then_skip_idempotent`.
- **AC-F1.3** 내용 변경 시에만 update + `content_hash` 갱신: `test_update_on_change`.
- **AC-F1.4** 17개 시/도 enum과 별칭/부분일치 정규화: `test_canonical_sido_alias_and_partial`.
- **AC-F1.5** `KopisAdapter._fetch_remote`와 `TourApiAdapter._fetch_remote`가 `PublicApiCollector`의 bounded paging/date 계약을 사용해 fake 응답을 여러 페이지에서 수집하고, 상한·중복·날짜 범위를 적용하며, 네트워크 없는 fixture 모드와 분리된다.

### F2 — 검색 & 지도 UI
- 동작: 기존 Leaflet 지도/지역 결과와 기존 5축 필터(지역·기간·나이대·테마·키워드) + `free_only` 추가 필터 + 가족 기본 탐색(`kids=true`, URL `kids=0`으로 해제) + Fuse.js/FTS5 검색, URL 상태 복원, 모바일 반응형. 시·도 경계 GeoJSON 코로플레스는 이 버전의 구현 완료로 주장하지 않으며 `regions.json`의 집계/대표 좌표만 계약한다.
- 기본 결과 집합은 기존 가족 결과를 유지한다. 정렬은 **경험 우선**(진행 중/예정 행사를 종료 행사보다 먼저, 그 안에서 `is_experience=true` 우선)으로만 바꾸며 비경험 행사를 기본에서 숨기지 않는다.
- 가족 판정은 `family_friendly=true`만 기본 결과에 포함하고, `family_evidence`를 카드/상세에서 노출해 근거 없는 홍보 문구를 만들지 않는다. `false`/unknown 행사도 가족 토글 해제 시 기존 일반 검색에서 계속 노출한다.
- 경험 탐색은 `is_experience=true`와 `experience_categories`/`experience_summary`를 사용한다. 기본 어린이 탐색에서 `family_friendly=true`와 결합해도 원문에 없는 시설·준비·연령·요금은 보충하지 않으며, 관람형 공연/음악/인형극은 활동 결과에 자동 포함하지 않는다. 다만 원문이 악기·오케스트라 체험/연주/참여/워크숍을 명시하면 `만들기·예술` 경험으로 분류할 수 있다. 카테고리 칩/필터는 URL `activity`로 복원 가능하고, 경험만 보기 토글은 일반 탐색 결과를 삭제하지 않는 선택 기능이다.
- 외부 링크는 최신 검증 캐시가 있는 `verified` HTTPS만 `연결 확인` 라벨로 클릭 노출한다. 그 외 상태는 `link_status`와 짧은 `link_reason`을 보여주고 행사를 제거하지 않는다. 신청기간이 끝난 행사는 링크가 verified여도 신청 CTA를 `apply-now`로 표시하지 않는다.
- 파생 공개는 `publication_policy.retention_days`(기본 90 KST 일)로 보존 기간을 적용한다. 유효 종료일은 `end_date || start_date`이며 KST 오늘-보존일수보다 엄격히 이전인 행사만 `events.json`·정적 상세에서 제외한다. 원본 knowledge는 삭제하지 않고, `updated.json.publication`에 `retention_days`, `retained`, `excluded`를 기록한다.
- 링크 검사 큐는 첫 공개 빌드가 만든 현재 eligible events 집합의 예약·정보 URL을 함께 사용한다. fresh verified proof는 재사용하고 새 네트워크 검사를 생략한다. 예약 URL을 정보 URL보다 먼저, 각 종류 안에서는 예정/진행 어린이 경험·가족 행사를 먼저 검사하고 현재 공개 집합에 없는 URL 해시 증명은 정리한다. 기본 오프라인 빌드는 네트워크를 호출하지 않는다.
- 화면은 360px 폭에서 가로 스크롤 없이 읽혀야 하며, 주요 터치 타깃은 최소 44px, 본문·필터 대비는 WCAG AA 수준을 목표로 한다. 기준 팔레트는 크림 `#fffaf0`, 잉크 `#193c35`, 그린 `#24755c`, 오렌지 `#f3a849`, 스카이 `#cdebf3`를 사용하고 한국어 텍스트를 우선한다.
- 정적 이미지 자산은 기존 `web/public/assets/family-outing.webp`와 `family-outing-mobile.webp` 경로에 새 오리지널 자연·가족 수채화를 적용한다. `family-science-spot.webp`와 `family-craft-spot.webp`는 해당 활동의 장식용 분류 그림이며 행사 장소·활동 존재의 근거가 아니다. `assets/design/family-concept.png`와 생성 이력 `assets/design/family-assets.json`을 보존한다. 이미지 생성/변환은 빌드·개발 단계에서만 가능하며 런타임 provider 호출이나 유료 API에 의존하지 않는다.
- 화면 구조는 지도 우선이다. 지도는 뷰포트 전체의 주 화면이고 검색·조건·결과·도구는 배경 투명도가 있는 독립 팝업이다. 검색 도크는 모든 해상도에서 하단 중앙에 놓고 조건 팝업은 위쪽으로 열리며 내부 스크롤한다. 결과는 우측 팝업의 목록·페이지네이션으로 제공하고 문서 전체가 아닌 목록 내부에서 스크롤한다. 모바일 결과는 기본 접힌 아이콘에서 여는 우측 드로어이며 기존 하단 결과 시트는 대체한다. 모바일의 검색 조건 팝업과 결과 드로어는 하나씩 확장해 서로를 가리지 않는다. 장문 히어로·상단 중앙 검색·모바일 하단 결과 시트는 이전 시안으로만 남긴다.
- **AC-F2.1** SQLite FTS5 한국어 부분검색 + 시/도·테마 교차필터: `test_fts_korean_match`·`test_filter_sido_and_theme`.
- **AC-F2.2** `facets.json`/`regions.json`/`updated.json` 빌드 산출(신선도 노출).
- **AC-F2.3** UI 응답형 + 타깃 크기: 브라우저 스냅샷 검증(360px 폭, 44px 터치 타깃), 기존 5축 URL 상태 복원.
- **AC-F2.4** 인덱스의 모든 `family_friendly=true` 레코드가 비어 있지 않은 `family_evidence`를 갖고, 명시적 성인 전용·아동 접근 제한·보호자 전용 레코드는 `true`가 아니며, `전연령` 단독/정보 부족 레코드는 `true`가 아니다. `web/public/assets/family-outing.webp`, `web/public/assets/family-outing-mobile.webp`, `assets/design/family-concept.png`가 존재한다.
- **AC-F2.5** 각 이벤트에 `experience_categories`·`experience_summary`·`is_experience`가 결정적으로 존재한다. 현재 4개 family fixture 수를 유지하고, `websearch:bb38c44890b9` 과학 체험은 손-활동 근거로 true/`과학·탐구`, `kopis:PF200002` 인형극과 음악 감상형 레코드는 공연 관람만으로 true가 아니다. 음악 감상형과 달리 원문에 악기·오케스트라 체험/참여가 명시된 레코드는 `만들기·예술`로 true가 될 수 있다.
- **AC-F2.6** 오프라인 build는 네트워크 없이도 events를 보존하고 link 필드를 결정적으로 생성한다. 최근 7일 verified HTTPS만 `link_url`을 노출하며, 만료·unverified·blocked·dead·unchecked 상태도 원인과 함께 유지한다.
- **AC-F2.7** 주입한 KST 시계에서 유효 종료일이 경계일과 같은 행사는 보존되고, 경계보다 오래된 행사는 `events.json` 및 정적 상세에서 제외된다. 기본 `retention_days=90`과 `config/search.yaml` 재정의가 결정적으로 적용되며, `updated.json.publication`이 retained/excluded 수를 보고하고 knowledge 원본은 남는다.
- **AC-F2.8** 일일 checker fixture가 fresh verified proof를 재사용해 네트워크 호출을 생략하고, 예약·정보 URL 합산 실제 검사 40건 이하·총 120초 이하를 지키며, 예약 URL 우선 및 각 종류 내 예정/진행 어린이 경험·가족 우선 큐와 비공개 URL 증명 prune을 결정적으로 수행한다. 동일 `link-checks.json` 해시 캐시만 사용한다.
- **AC-F2.9** 1440×900/1280×800/360×800/390×844에서 지도 네 변은 뷰포트와 1 CSS px 이내, 검색 도크 중심은 뷰포트 가로 중앙과 2 CSS px 이내이며 하단 간격은 12–24px이다. 검색 조건은 위로 열리는 높이 제한/내부 스크롤 팝업이고 키보드로 열고 닫으며 초점이 복원된다. 결과는 우측 12–24px 안쪽의 목록·페이지네이션 팝업이고 문서는 스크롤되지 않는다. 모바일 결과는 기본 접힘, 아이콘으로 여는 우측 드로어이며 검색 도크를 가리지 않고 최소 56px의 지도 폭을 남긴다. 모바일 조건/결과 확장은 상호 배타적이다. 가로 넘침이 없고 오버레이 위 조작은 지도로 전달되지 않는다. 실제 Leaflet 핀 끝↔지리 좌표, 말풍선 끝↔핀 상단 중앙은 각각 2 CSS px 이내이고 같은 lat/lng를 사용한다. pan/zoom/resize, 단일/동일좌표 그룹/가장자리/긴 내용에서 정합성과 오버레이 가림 방지를 유지하며 Leaflet 루트 transform을 덮어쓰지 않는다. 열린 조건에서 먼 지역을 선택한 후 지도를 눌러 닫아도 결과 마커가 새 결과의 보이는 지도 영역에 들어오고, 첫 드래그 직전에 줌/중심이 갑자기 바뀌거나 드래그가 무시되지 않는다. 기존 5축·가족/무료/활동·정직한 신청 가능 필터, URL 복원/초기화, 전체 건수·페이지네이션·정렬/거리순·위치·그룹 핀·출처/신선도/연결 상태·오류/빈 결과/재시도/지도 실패와 키보드 초점을 보존한다. 주요 타깃 44px·reduced-motion, 고정 fixture와 실제 공개 데이터 증거 구분을 유지한다.
- **AC-F2.10** 기존 Vercel `happy-our-planning` 프로젝트에 검증한 브라우저 정적 산출물만 배포하고 DB·설계 원본·Worker·QA 증거/스크립트는 제외한다. production alias의 HTML/CSS/JS/언어 리소스/events.json/활동 그림이 HTTP200 및 검증 커밋 산출물과 일치해야 한다. 실제 브라우저에서 전체 지도·하단 중앙 검색/조건·우측 결과/페이지네이션·모바일 아이콘·한영 전환·종료 표시·핀/팝업·예약 연결을 확인하고 deployment ID/URL/커밋/명령/관찰 결과를 남긴다. 공개 mixed/live/fixture 상태와 주입 fixture 증거를 구분한다.
- **AC-F2.11** 목록/지도에서 여는 상세와 공유용 정적 상세가 같은 행사 ID의 제목·원문 설명·장소/주소·일정·연령·요금·소요시간 및 출처 정보를 제공한다. 원천에 없는 값은 `정보 없음` 등으로 구분하고, 제공된 상세 설명을 버리거나 제목만으로 대체하지 않는다. 공식 정보 링크와 예약/신청 링크는 목적과 라벨이 구분되며 실제 클릭으로 해당 행사 페이지에 도달한다.
- **AC-F2.12** KOPIS 상세 응답의 `relates/relateurl` 등 원천이 명시한 행사별 예약 URL을 `offers.url`로 보존한다. 예약 URL은 기존 SSRF/리다이렉트/HTTPS/7일 신선도 정책과 동일 캐시로 검증하며, 정보·예약 URL을 합쳐 기존 검사 40건/120초 예산을 넘지 않는다. live 일일 수집·공개 경로는 검증 캐시를 갱신·재사용해 공개 데이터에도 반영하고 오프라인 build는 네트워크를 호출하지 않는다. 검증된 예약 링크와 원천의 유효한 신청/판매 기간 근거가 없으면 `신청가능`/`예약 가능`을 표시하지 않는다. 판매/잔여석을 확인하지 못한 경우 예약 링크가 있어도 `예매처에서 확인`으로 구분한다. 실제 공개 `events.json`의 전체/가족 건수, 최신 검증된 정보 링크/예약 링크 수를 기록하고, 원천이 예약 URL을 제공하는 현재 행사 최소 1건은 목록 → 상세 → 공식 예약 사이트 연결을 실제 브라우저로 증명한다. 홈페이지 smoke나 주입 fixture만으로 이 기준을 충족했다고 주장하지 않으며 예약·결제·로그인·최종 제출은 실행하지 않는다.
- 예약 검증 용량은 기존 무료 예산을 유지하고 예약 우선 큐로 배분한다. 현재 예정/진행 가족 행사의 예약 후보가 40개 이하인 본 공개 검증에서는 후보 100%가 이번 bounded 검사 또는 유효한 기존 verified proof로 처리되어야 한다. 전체·가족 후보의 검사 적용률/verified 비율을 따로 기록하고 예약 가능으로 표시한 행사의 검증·기간 근거 충족률은 항상 100%여야 한다. KOPIS 상세 조회는 기존 무료 API 호출 예산 안에서 실행별 상한과 만료 캐시 재사용을 적용하며 원격 실패·누락을 숨기지 않는다.
- 신청/판매 기간이 없는 공연의 `Open`/`Scheduled` 상태는 신청 `오픈`/`신청전`의 근거가 아니다. 이 경우 행사 일정만으로 `예정`/`진행중`/`종료`, 일정 정보가 없으면 `정보 없음`을 표시한다. 명시된 신청 기간의 시작/마감은 행사 종료와 별개로 표시하며 예약·잔여석 확인을 뜻하지 않는다.
- **AC-F2.13** 실제 종료된 행사 일정은 목록·지도 팝업·상세의 날짜에 취소선을 표시하고 붉은 `종료`/`Ended` 배지로 강조한다. 행사 종료와 신청 마감은 구분해 아직 진행 전인 행사 날짜를 신청 마감만으로 지우지 않는다. 색만으로 상태를 표현하지 않는다.
  종료 판정은 C12의 유효 종료일과 KST 오늘을 기준으로 한다.
- **AC-F2.14** 모바일 지도 도구·결과 열기/닫기·조건 도구는 시각적 한글 설명 대신 아이콘으로 표시하되 44px 타깃과 번역되는 접근성 이름/상태를 유지한다. 폼 필드 라벨·행사 정보·오류/상태 안내와 지도 저작권 표기는 삭제하지 않는다.
- **AC-F2.15** 한국어/영어 토글이 지도 UI·필터 어휘·날짜/요금·상태·상세/예약 안내를 전환한다. `lang=ko|en` URL·`html[lang]`·저장한 언어를 동기화하고 필터/페이지/지도 뷰를 잃지 않는다. 공식 번역이 없는 행사명·장소·주소·원문은 출처 언어를 유지하고 이를 안내한다. 번역 문자열은 하나의 정적 언어 리소스를 공유하며 런타임 번역 모델/유료 호출은 없다.
- **AC-F2.16** 지도 팝업은 배경 알파를 사용해 개방감을 주되 본문 표면은 대략 0.88 이상으로 유지하고 텍스트/컨트롤 자체의 opacity는 낮추지 않는다. 최악의 밝고 어두운 지도 배경에서 주요 텍스트 대비 4.5:1을 확인하고 `prefers-reduced-transparency`에서는 불투명 표면·blur 해제를 제공한다. 포커스·클릭 영역·라이선스 표기를 투명 효과로 가리지 않는다.

### F3 — 신청 매크로
- 동작: `plan_job(event, profile)`가 사이트 프로필로 스텝 렌더 → Playwright 러너가 소비.
  `automation_allowed=False`는 `mode='semi'`로 강등, 자동 submit 제거 + 사용자 최종제출 pause.
- **AC-F3.1** 자동 사이트는 submit 액션 보유: `test_auto_site_has_submit`.
- **AC-F3.2** 약관금지 사이트는 자동제출 없음: `test_tos_blocked_site_is_semi_no_autosubmit`.
- **AC-F3.3** 토큰 치환 정확: `test_template_substitution`; 미등록 사이트는 manual: `test_unknown_site_manual`.
- **AC-F3.4** (신규) Playwright 러너가 mock 폼에 `plan_job` 결과 입력→자동제출 E2E 성공(헤드리스).

### F4 — 결과 알람
- 동작: `compute_notifications`(순수) → `dispatch`(전송, 채널 토큰 없으면 dry-run). 중복 억제 `knowledge/notify/sent.json`.
- **AC-F4.1** 마감 D-1 + 필터 일치만 알림: `test_deadline_d1_and_filter_match`·`test_filter_mismatch_no_notif`.
- **AC-F4.2** 2회차 실행 중복 0: `test_dedupe_suppresses_second_run`.
- **AC-F4.3** (신규) Telegram 실채널 1건 도달(토큰 주입 시) / 무토큰 시 dry-run 무오류.

### F5 — AI 추천 플래닝
- 동작: 규칙 랭킹(`rank.py`) + Gemini 구조화 플랜(`ai_planner.py`, responseSchema) + 폴백 + 캐시.
- **AC-F5.1** 요청에 schema·후보 id 포함: `test_build_request_has_schema_and_ids`.
- **AC-F5.2** 유효 플랜 파싱 / 잘못된 형태 거부: `test_parse_plan_valid`·`test_parse_plan_rejects_bad_shape`.
- **AC-F5.3** 환각 event_id 제거: `test_constrain_drops_hallucinated_ids`; 무키 폴백: `test_plan_falls_back_without_key`.
- **AC-F5.4** `free_only` 준수·1일 상한: `test_free_only_excludes_paid`·`test_plan_respects_max_per_day`.

### F6 — 운영·지식 루프 (llm-wiki)
- 동작: `wiki_index.build()`로 `knowledge/` 갱신, graphify 그래프, 주간 큐레이션/ dedupe 리포트.
- **AC-F6.1** `run_pipeline` 5단계가 `knowledge/` wiki 인덱스 갱신.
- **AC-F6.2** GitHub Actions cron(ingest.yml)으로 일1회 갱신·신선도 노출.
- **AC-F6.3** 기존 03:00 KST 단일 cron은 동시 실행을 직렬화한다. 중복 실행은 취소/대기되어 산출물을 덮어쓰지 않으며, 실패·부분 실패는 Actions job 상태와 `knowledge/sources/collection-status.json`의 `sources[*].mode/last_success_at/error`로 확인 가능하다.
- **AC-F6.4** `updated.json.sources`는 collection-status sidecar의 `sources` 맵을 변경 없이 내보내고, `updated.json.freshness`는 레코드 `fetched_at`만 나타낸다. `updated.json`의 `source_mode`/`last_success_at`/`collection_started_at`/오류 집계는 live 수집 메타데이터에서만 채우며 fixture build 시각은 live 성공으로 기록하지 않는다.

## 5. 비범위 (1차)
결제·티켓팅 직접 처리(외부 위임), SSO 풀스택(익명+로컬 프로필), 모바일 네이티브 앱, 시·도 경계 GeoJSON 코로플레스 구현.

## 6. 전역 완료 정의 (DoD)
`python -m unittest discover -s tests` = 40+ green, `validate.py` rc==0,
`events.db` 재생성 동일성, C1–C13 전부 만족, F2 가족 판정/근거·활동 분류·안전한 링크 노출·지도 우선 가독성·이미지 자산 AC와 F3.4/F4.3 신규 AC 충족. 시·도 경계 GeoJSON 코로플레스는 미완료 범위로 남기며 구현됐다고 보고하지 않는다.
