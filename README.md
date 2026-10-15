# 놓치마 (Notchima)  
**기회, 다 챙겨드릴게요.**  
대한민국 지도 기반 전국 행사 발견 · 신청 · 알람 · AI 플래닝 인프라

[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)  
[![CI](https://github.com/akillness/happy-our-planning/actions/workflows/ingest.yml/badge.svg)](https://github.com/akillness/happy-our-planning/actions/workflows/ingest.yml)  
[![Hosting](https://img.shields.io/badge/Vercel%2BActions-₩0%2Fmonth-2ea44f)](https://happy-our-planning.vercel.app/)

## 지도부터 본다

**전국 가족 행사(공연·축제·전시·체험·교육·공모·정부지원)를 지도에서 발견합니다.**  
- 🗺️ **지도 기반 검색** — 행사 위치가 지도에 핀으로 표시됩니다  
- 🎯 **5축 필터** — 지역(시·도·시군구) · 기간 · 나이대 · 테마 · 키워드로 좁혀봅니다  
- 👨‍👩‍👧 **가족 기본 탐색** — 어린이·가족 행사를 보수적으로 구분해 우선 표시(`kids=true`; 일반 행사는 `kids=0`으로 접근)  
- 🖐️ **활동 경험** — 아이가 실제 참여할 수 있는 손-활동(만들기·과학·자연·신체)을 원문 근거와 함께 보여줍니다  
- 📱 **반응형 UI** — 데스크톱은 우측 고정 결과 패널, 모바일은 하단 접기 가능한 시트로 지도와 결과를 함께 조작합니다

**선택 기능** (약관/환경변수 설정 시에만):  
- 📋 **신청 매크로** — 신청기간 내 신청 자동화를 보조합니다 (약관 금지 사이트는 수동 절차로 강등)  
- 🔔 **마감 알람** — 마감 1일 전 Telegram으로 알려줍니다  
- 🤖 **AI 주간 플래너** — 프로필 기반 맞춤 행사를 추천합니다

## 공개 배포

📍 **https://happy-our-planning.vercel.app/**  
Git 기반 Vercel Hobby 배포. `web/public` 정적 산출물만 업로드되며 서버 함수, 파생 DB, 별도 Worker는 포함하지 않습니다.

## 아키텍처 한 줄

**공공 API · 무료 웹검색 → 정규화(schema.org/Event = OKF) → 평면 파일 지식 DB(Markdown + YAML) → 사전 빌드 JSON/SQLite → 정적 UI(Leaflet 지도 + Fuse.js 검색)**

데이터 흐름:
1. **수집** — KOPIS(공연) · TourAPI(축제) · Exa·Brave·Tavily(웹검색) 어댑터가 행사 데이터를 수집합니다
2. **정규화** — 모든 레코드를 schema.org/Event 형식(OKF)으로 변환합니다  
3. **지식 DB** — `knowledge/` 폴더의 Markdown 파일로 저장합니다 (Git 히스토리 추적, 삭제 없이 보존)  
4. **빌드** — 파일에서 `web/public/data/events.json` (검색용), `events.db` (FTS5 전문검색, 선택), `facets.json` (필터 인덱스)를 생성합니다  
5. **배포** — 정적 HTML/CSS/JS + JSON 데이터만 Vercel에 배포합니다

## 빠른 시작 (키 없이도 가능)

### 설치 및 파이프라인 실행

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 전체 파이프라인 (오프라인 픽스처 사용, 네트워크 불필요)
python -m scripts.run_pipeline

# 3. 검증
python scripts/build/validate.py   # OKF 스키마 검증 (exit code 0이면 통과)
python -m unittest discover -s tests  # 단위 테스트 실행

# 4. 정적 서버 미리보기
cd web/public && python -m http.server 8000
# → http://localhost:8000 에서 확인
```

### 개별 도구

```bash
# 지식 DB 수집·정규화·빌드 파이프라인
python -m scripts.run_pipeline

# OKF 스키마 검증 (rc==0이면 통과)
python scripts/build/validate.py

# SQLite 인덱스 생성 (파생물, 수동 재생성용)
python -m scripts.build.build_sqlite

# SQLite FTS5 검색 테스트
python -m scripts.build.build_sqlite --query "축제"

# 규칙 기반 추천 (무료, LLM 불필요)
python -m scripts.recommend.rank

# Google AI Studio Gemini 추천 플래너 (GOOGLE_AI_STUDIO_KEY 필요)
python -m scripts.recommend.ai_planner

# 마감임박 알람 (Telegram 토큰 없으면 dry-run)
python -m scripts.notify.dispatch

# 신청 매크로 계획 (약관 자동 검증)
python -m scripts.macro.apply

# 웹검색 발견 (Exa/Brave/Tavily, 키 없으면 픽스처)
python -m scripts.ingest.websearch

# API 키 설정 확인
python -m scripts.ops.check_keys        # 보유 현황
python -m scripts.ops.check_keys --probe  # 유효성 확인 (소량 요청)
```

## 라이브 데이터 수집 (선택사항)

기본적으로 샘플 픽스처(`raw/**/sample-*`)로 전체 파이프라인이 실행됩니다. **실제 공공 API에서 수집하려면:**

1. `.env` 파일 생성
   ```bash
   cp .env.example .env
   ```

2. 필요한 API 키를 `.env`에 입력  
   (채팅에 붙여넣지 마세요. 로컬 파일에만 기록)

   | 키 | 출처 | 용도 |
   |---|---|---|
   | `KOPIS_KEY` | [kopis.or.kr](https://kopis.or.kr) 회원가입 → 오픈API 인증키 | 공연·전시 |
   | `TOURAPI_KEY` | [data.go.kr](https://data.go.kr) → 한국관광공사 활용신청 | 축제 |
   | `EXA_API_KEY` / `BRAVE_API_KEY` / `TAVILY_API_KEY` | 각 제공자 무료 티어 | 웹검색 |
   | `GOOGLE_AI_STUDIO_KEY` | [aistudio.google.com](https://aistudio.google.com) | AI 플래너 (선택) |
   | `TELEGRAM_TOKEN` | [BotFather](https://t.me/botfather) | 알람 채널 (선택) |

3. 파이프라인 실행
   ```bash
   python -m scripts.run_pipeline  # 자동으로 live 수집 전환
   python -m unittest discover -s tests
   ```

### 일일 자동 수집 (GitHub Actions)

매일 03:00 KST(UTC 18:00)에 자동 수집됩니다.

**설정:**  
`Settings → Secrets and variables → Actions`에 위의 API 키를 GitHub Secrets로 등록하세요.  
(`.github/workflows/ingest.yml`이 이미 변수를 받을 준비가 되어 있습니다)

**동작:**  
1. 공공 API/웹검색으로 행사 데이터 수집
2. 정규화 및 검증
3. 지식 DB 업데이트
4. 인덱스 재생성 및 배포 빌드
5. Git push (성공 시에만)

**모니터링:**  
- GitHub Actions 로그: [ingest 워크플로우](https://github.com/akillness/happy-our-planning/actions/workflows/ingest.yml)
- `knowledge/sources/collection-status.json` — 소스별 수집 상태 (성공/실패/오류)
- `web/public/data/updated.json` → `source_mode` (offline/live/mixed), `last_success_at`

## 설계 문서

| 문서 | 내용 |
|---|---|
| [`specs/spec.md`](specs/spec.md) | 동결 명세 (1.6.0) — 제약·기능·검증 기준 |
| [`specs/seed.yaml`](specs/seed.yaml) | 실행 SSOT — 도구·제약·성공 조건 |
| [`docs/01-architecture.md`](docs/01-architecture.md) | 시스템 아키텍처 및 디렉터리 구조 |
| [`docs/02-data-model-okf.md`](docs/02-data-model-okf.md) | OKF/schema.org 데이터 모델 |
| [`docs/03-data-sources-apis.md`](docs/03-data-sources-apis.md) | 무료 API 카탈로그 |
| [`docs/04-ingestion-pipeline.md`](docs/04-ingestion-pipeline.md) | 수집 파이프라인 상세 |
| [`docs/05-search-and-map.md`](docs/05-search-and-map.md) | 검색 · 지도 UI 기능 |

## 레이아웃

```
docs/              계획 · 설계 문서 및 의사결정 기록
specs/             spec.md (명세), seed.yaml (실행 SSOT), tasks.md (업무 분해)
knowledge/         지식 DB (Markdown 파일 원본 + schema)
config/            sources.yaml, themes.yaml, regions.yaml, age-bands.yaml
scripts/
  ├─ ingest/       KOPIS, TourAPI, 웹검색 어댑터
  ├─ normalize/    스키마 정규화
  ├─ build/        JSON/SQLite 빌드 + 검증
  ├─ recommend/    규칙 · AI 플래너
  ├─ notify/       알람 (Telegram)
  ├─ macro/        신청 매크로 계획
  └─ run_pipeline.py  통합 오케스트레이터
web/
  └─ public/       정적 사이트 (HTML/CSS/JS + data/events.json)
.github/workflows/ 일일 ingest.yml (03:00 KST cron)
assets/            브랜드 자산 (로고, 마스코트, 미리보기)
tests/             단위·통합 테스트 (unittest)
```

## 지도 · 검색 · 필터

### 검색 및 발견
- **중앙 검색** — 행사 이름·설명·테마를 Fuse.js (클라이언트, 실시간)로 검색합니다  
- **SQLite FTS5** — 선택사항. 대규모 데이터에서 한국어 부분 검색을 지원합니다

### 5축 필터 + 선택 필터
1. **지역** — 시/도 선택 후 시군구로 좁혀봅니다  
2. **기간** — 예정/진행/종료 상태로 필터링합니다  
3. **나이대** — age/audience 필드를 config/age-bands.yaml로 정규화합니다  
4. **테마** — config/themes.yaml의 통제 어휘로 분류합니다  
5. **키워드** — 자유 검색  
**추가:**  
- **무료만** — 무료로 명시된 값(`free`, `0`, 숫자0)만 포함하고 가격 미상은 제외
- **가족 기본** — `kids=true` URL 파라미터 (어린이·가족 행사 우선)  
- **활동 경험** — 손-활동이 있는 행사만 표시

### 지도 · 핀 · 팝업
- **Leaflet 지도** — Leaflet 1.9.4 CDN을 사용합니다  
- **핀** — 행사 좌표(경위도)에 마커를 표시합니다  
- **팝업** — 핀 클릭 시 행사 정보와 신청 링크를 표시합니다. 신청기간 후 행사는 `apply-now` CTA를 보이지 않습니다  
- **정밀 주소** — KOPIS 공연장명을 시설검색 API로 실제 좌표로 변환합니다. 미매칭은 시/도 중심으로 폴백합니다  
- **그룹 핀** — 같은 좌표의 여러 행사는 그룹으로 표시합니다

### 결과 정렬 및 페이지네이션
- **관련도** — 검색 점수 (Fuse.js)  
- **다가오는 순** — 예정 행사 우선, 그 안에 활동(`is_experience=true`) 먼저  
- **거리순** — 사용자 위치 기반 (위치 미저장, 세션 내에만 유지)  
- **페이지네이션** — 이전/다음, 현재 건수 표시

## 가족 판정 및 근거

**보수적 판단:** family_friendly=true인 행사만 기본 검색에 포함됩니다. 모든 가족 행사에는 근거가 명시됩니다.

**판정 기준:**
- ✅ 어린이·아동·유아 명시, 나이대 상한 13세 이하, 가족 대상 프로그램
- ❌ 성인 전용, 노키즈, 어린이/아동/미성년자 제한·금지, 학부모/보호자 전용
- ⚠️ 정보 부족 또는 단순 "전연령"은 근거 미충분으로 보존

family_evidence — 근거 구절(원문에서 잘라낸 짧은 인용)

## 활동 경험 분류

**손-활동만 인정합니다:** 공연 관람, 음악 감상, 전시 감상은 활동으로 분류하지 않습니다.

**범주:**
- 자연·생태 — 자연 관찰, 생태 체험
- 과학·탐구 — 과학 실험, 탐구 활동
- 만들기·예술 — 공예, 미술, 악기 연주 체험
- 농촌·먹거리 — 요리, 농사 체험
- 신체·놀이 — 스포츠, 게임, 신체 활동
- 기타 — 그 외 손-활동

is_experience — boolean. experience_summary — 원문에서 잘라낸 근거.

## 링크 검증 및 신청

**링크 상태:**
- ✅ **확인됨** — 최근 7일 이내 검증된 HTTPS. "연결 확인" 라벨로 클릭 가능하게 표시됩니다  
- ⚠️ **미확인** — 네트워크 검사 미실행, 다른 이유로 검증 불가  
- ❌ **차단됨** — 자동화 금지 사이트  
- ❌ **만료됨** — 이전 검증 실패, 응답 없음  
- ❓ **알 수 없음** — 검사 대기 중

link_reason — 짧은 한국어 설명

**신청 매크로:**
- 약관이 자동화를 허용하는 사이트만 자동 신청 시도
- 금지 사이트는 수동 신청으로 강등
- 신청기간 후 행사는 apply-now CTA를 제거하지만 상세 링크(verified fresh HTTPS)는 표시됩니다

## 데이터 신선도 및 보존 정책

**신선도 표시:**  
- `freshness` — 빌드 타임스탐프 및 경과일 (≥3일일 때만 '3일 전 업데이트됨' 형식, 미만은 카운트 표시)
- `last_success_at` — 마지막 실제 API 수집 시점  
- `collection_started_at` — 일일 수집 시작 시점

**90일 보존:**  
유효 종료일(`end_date` 또는 `start_date`)이 오늘로부터 90일 이전인 행사는 공개 index(`events.json`)에서 제외됩니다. 원본 knowledge 파일은 삭제되지 않으며 Git 히스토리에 남습니다.

## 구현된 기능 (코드)

다음 기능들이 코드베이스에 구현되어 있습니다.

- 공공 API 수집 및 웹검색 발견 (KOPIS·TourAPI·Exa·Brave·Tavily, 키 없으면 픽스처)  
- OKF 스키마 검증 및 증분 upsert  
- 지도 기반 5축 검색 · 가족/무료/활동 필터 · 반응형 UI (9개 viewport native/discovery/locale 검증 통과)
- SQLite FTS5 전문검색 (선택)  
- 규칙 기반 추천 · Google AI Studio Gemini 플래너  
- 마감임박 알람 (Telegram 토큰 주입 시)  
- 신청 매크로 계획 및 Playwright E2E 러너  
- GitHub Actions 일일 자동 수집 (03:00 KST)  
- 링크 검증 캐시 (bounded GET, fresh proof 재사용, 7일 한도)  
- 지식 wiki 자동 인덱싱  
- Vercel Hobby 정적 배포

## 현재 한계 및 범위 밖

- ❌ 시·도 경계 GeoJSON 코로플레스 (레이어 배경) — 미구현  
- ❌ 결제·티켓팅 직접 처리 — 외부 위임 (링크 제공)  
- ❌ 모바일 네이티브 앱 — 웹 기반만 제공  
- ❌ SSO 풀스택 — 익명 · 로컬 프로필만  
- ❌ 실시간 좌석 정보 — API 한도 및 약관 제약  

## 구성원 역할 파일 (선택 기능, 자동 검증 없음)

`.codex/agents/` 폴더에 경량 AI 협업 역할 정의가 있습니다. **제약 자동 검증은 하지 않으며**, 활용은 선택입니다.

- **review.toml** — 코드 리뷰 · 패턴 준수 검증  
- **design.toml** — UI/UX 개념 · 컴포넌트 설계  
- **implementation.toml** — 기능 구현  
- **collection.toml** — 결정적 데이터 추출 · 정규화

## Jev 라우팅 (선택, 자문용)

별도 세션에서 `Jev` 모델 추천이 제공될 수 있습니다. **이는 자문일 뿐 의무 설치나 실행 변경이 아닙니다.** Notchima 코드베이스는 Jev를 호출하거나 의존하지 않으며, 런타임 또는 빌드에 요구하지 않습니다.

## 라이선스 및 비용

- 📜 코드 — MIT (또는 기존 라이선스 유지)  
- 💰 운영비 — **월 ₩0** (Vercel Hobby + GitHub Actions 무료 티어)  
- 🔌 외부 API — 무료 티어 한도 내  
- 🖼️ 생성 이미지 — 빌드/개발 선택사항, 런타임 의존 없음

## 검증 및 품질 (v1.6 최종)

### 검증 완료

**테스트:**  
- 245개 unittest 실행 = 242 PASS + 3 SKIP (예상된 스킵)
- mypy 37개 파일 타입 체크 통과
- OKF 스키마 검증 345/345 레코드 통과
- UI 회귀 테스트 (68개 상호작용 체크)
- 9개 viewport (native 131 / discovery 68 / locale 158 exact 93d18) 네이티브 지오메트리/콘텐츠/필터 동작 확인

**데이터 현황:**
- **공개 행사** — 341건 (평면 파일 SSOT, 345 검증, 341 SQLite 인덱스, 342 sitemap)
- **가족 행사** — 10건 (근거 명시, 모두 `family_evidence` 포함)
- **활동 행사** — 7건 손-활동 경험 (검증됨)
- **신청 후보** — 46건 (26건 verified, 19건 blocked, 1건 unverified)
- **소스 모드** — offline (픽스처 기본, KOPIS attempted 06:10:35 KST, last_success 00:08:16 KST 보존)
- **소스 구성** — 334 KOPIS + 2 TourAPI + 4 웹검색 + 1 explicit example
- **비용 원문 누락** — 9/341 행사 (비용 정보 미공개)
- **예약 상태** — 예매 가능 여부 확인 필요 시 공식 사이트 참조 안내

### 배포 제외 사항

공개 배포(`web/public/`)에는 다음이 포함되지 않습니다:
- SQLite `events.db` — 파생물, 로컬 전문검색용
- `.env` 및 GitHub Secrets — API 키 저장
- `raw/` 폴더 — 원천 데이터, 개발용
- `scripts/` — 빌드 도구, 로컬 실행용
- `knowledge/` — 원본 Markdown 파일, Git 기본 제외
- QA 캐시 및 브라우저 드라이버 — `tests/.cache`
- Playwright 캐시 — `.playwright`

배포되는 것:
- `web/public/index.html` — 정적 진입점
- `web/public/data/events.json` — 공개 행사 데이터
- `web/public/data/facets.json` — 필터 옵션
- `web/public/data/updated.json` — 메타데이터 (수집 상태, 신선도)
- 정적 자산 (CSS, JS, 이미지)
- 선택된 행사 상세 HTML (정적 미리보기)

### 테스트 실행 (로컬)

```bash
# 전체 테스트
python -m unittest discover -s tests

# 특정 테스트 모듈
python -m unittest tests.test_pipeline
python -m unittest tests.test_booking

# 스키마 검증
python scripts/build/validate.py

# UI 회귀 테스트
python3 specs/verify/run_ui_check.py
```

### 검증 기준 (seed.yaml 기본)

- **C1** — 무료 API만, 키 부재 시 오프라인 픽스처 rc==0
- **C2** — 평면 파일 SSOT, events.db는 파생물 (삭제 후 재생성 가능)
- **C3** — schema.org/Event 준수 (validate.py rc==0)
- **C4** — 비밀키 커밋 금지 (.env / GitHub Secrets만)
- **C5** — 약관 금지 사이트 자동 제외
- **C6** — AI 출력 강제 검증 + 환각 제거
- **C7** — 월 0원 운영비 (Vercel 무료 + GitHub Actions 무료)
- **C8** — 가족 판정은 명시 근거만
- **C9** — 활동은 손-활동 근거만
- **C10** — 링크 검증 bounded GET, 7일 fresh 재사용, 40건/120초 예산
- **C11** — 일일 cron 단일 직렬화, 실패 투명 공개
- **C12** — 90일 보존, 원본 보존
- **C13** — 링크 큐 40건/120초, 예정 경험 우선

## 개발 · 기여

### 새 행사 추가
`knowledge/events/` 폴더에 새 Markdown 파일 생성:

```markdown
---
okf_type: Event
id: my-event-001
name: 예시 행사
start_date: 2024-12-15
location:
  sido: 서울시
  sigungu: 종로구
  lat: 37.5
  lng: 126.9
url: https://example.com
source: custom
themes:
  - 과학
  - 체험
audience: children
age: "5-10"
---

아이들이 과학 실험을 직접 해볼 수 있는 체험 행사입니다.
```

그 후 `python -m scripts.run_pipeline`을 실행하면 자동으로 인덱스에 추가됩니다.

### 코드 수정
1. 브랜치 생성: `git checkout -b feature/...`  
2. 코드 수정  
3. 테스트 통과: `python -m unittest discover -s tests`  
4. Git 푸시 및 PR 생성  
5. CI/CD 통과 후 merge

### 문서 수정
설계 결정은 `docs/` 및 `specs/` 폴더에 기록합니다.

## 문의 · 피드백

- **이슈 리포트** — [GitHub Issues](https://github.com/akillness/happy-our-planning/issues)  
- **기여** — [Pull Requests](https://github.com/akillness/happy-our-planning/pulls) 환영합니다

## 최종 업데이트

이 README는 **2026-10-15** v1.6 최종 검증 통과 후 작성되었습니다.

- **공개 데이터** — 341건 (혼합 모드: KOPIS live + TourAPI fixture + 웹검색 live)
- **배포** — https://happy-our-planning.vercel.app/ (Vercel Hobby, 정적 산출물만)
- **일일 갱신** — GitHub Actions 03:00 KST cron
- **테스트** — 245 unittest 242 PASS + 3 SKIP, mypy 37 통과, OKF 345/345 통과
