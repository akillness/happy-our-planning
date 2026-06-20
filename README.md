<h1>
  <img src="assets/brand/notchima-mascot-256.png" alt="놓치마 마스코트" width="96" align="left" hspace="12" />
  놓치마 <small>(Notchima)</small><br/>
  <sub>기회, 다 챙겨드릴게요.</sub>
</h1>

[![ingest](https://github.com/jellyggumi/happy-our-planning/actions/workflows/ingest.yml/badge.svg)](https://github.com/jellyggumi/happy-our-planning/actions/workflows/ingest.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Tests 40 passing](https://img.shields.io/badge/tests-40%20passing-success?logo=pytest&logoColor=white)](tests/test_pipeline.py)
[![Knowledge Format](https://img.shields.io/badge/data-schema.org%2FEvent-orange?logo=json&logoColor=white)](docs/02-data-model-okf.md)
[![Hosting](https://img.shields.io/badge/SaaS-Cloudflare%20%2B%20GitHub%20Actions-F38020?logo=cloudflare&logoColor=white)](docs/09-saas-free-stack.md)
[![Cost ₩0/month](https://img.shields.io/badge/cost-%E2%82%A90%2Fmonth-2ea44f)](docs/00-overview.md)

> **놓칠 뻔한 기회를 대신 챙겨주는 행사 비서.** 대한민국 지도 기반 **행사 발견 · 신청 매크로 · 마감 알람 · AI 주간 플래닝**.
공연·축제·전시·교육·공모·정부지원이 흩어져 마감을 놓치는 사람을 위해 — 지도에서 발견하고, 매크로로 신청하고, 알람으로 지키고, AI가 주간 일정으로 큐레이션한다.
무료 공공 API + flat-file 지식 DB(llm-wiki) + 무료 SaaS(free tier)로 **월 0원** 운영을 목표로 한다. <sub>(코드네임: happy-our-planning)</sub>

## 핵심 기능
- 🗺️ 지역(시/도·시군구) · 기간 · 나이대 · 테마 · 키워드 5축 검색
- 🤖 신청기간 내 신청 보조 매크로(자동/반자동, 약관 준수)
- 🔔 신규·신청오픈·마감임박·신청결과 알람(Telegram/Web Push)
- 🧠 프로필 기반 AI 주간 플랜 추천

## 설계 한 줄 요약
공공 API → 정규화(schema.org/Event = Open Knowledge Format) → **git 안의 Markdown 파일 = 지식 DB** →
사전 빌드 JSON → 정적 UI(검색/지도). 상태/AI/알람만 엣지 함수. 스크립트가 모든 적재·매크로·알람을 구동.

## 시스템 로직 (데이터 흐름)
```mermaid
flowchart LR
  subgraph SRC["무료 데이터 소스"]
    K[KOPIS]
    T[TourAPI 4.0]
    G[data.go.kr / VWorld<br/>지오코딩]
    WS["웹검색 발견<br/>Exa · Brave · Tavily"]
  end

  subgraph WRITE["쓰기 경로 · 스크립트 (GitHub Actions)"]
    I["ingest<br/>raw/**/sample-*"]
    N["normalize → OKF<br/>schema.org/Event"]
    U["upsert<br/>content_hash 증분 · archived"]
    V["validate<br/>JSON Schema 게이트"]
    B["build_index<br/>events/facets/regions/updated.json"]
    SQL["build_sqlite<br/>events.db · FTS5 인덱스"]
    W["wiki_index<br/>REGIONS/THEMES/SOURCES"]
  end

  subgraph DB["지식 DB (git flat-file)"]
    MD["knowledge/events/**.md<br/>YAML frontmatter = SSOT"]
    IDX["knowledge/index.md"]
  end

  subgraph READ["읽기 경로 · 100% 정적"]
    JSON["web/public/data/*.json + events.db"]
    UI["Leaflet 지도 + Fuse 검색<br/>5축 필터 + AI 다이얼로그"]
  end

  subgraph EDGE["엣지 함수 (Cloudflare)"]
    R["recommend<br/>규칙기반 랭킹"]
    AI["ai_planner<br/>Google AI Studio(Gemini) + 폴백"]
    NT["notify<br/>마감 D-1 · dedupe"]
    MC["macro<br/>약관 게이팅"]
  end

  K & T & WS --> I --> N
  G --> N --> U --> MD
  MD --> V --> B --> JSON --> UI
  MD --> SQL --> JSON
  MD --> W --> IDX
  MD --> R --> AI --> UI
  MD --> NT
  MD --> MC
```

## CI 워크플로우 (.github/workflows/ingest.yml)
```mermaid
flowchart TD
  A["⏰ cron 일 1회 / workflow_dispatch"] --> B[checkout]
  B --> C[setup python 3.12 + pip install]
  C --> D["run_pipeline<br/>수집→정규화→OKF적재→검증→인덱스→SQLite→wiki"]
  D --> E["unittest · 40종"]
  E --> F{변경 diff?}
  F -- 있음 --> G["commit & push<br/>knowledge/** + web/public/data/**"]
  F -- 없음 --> H[no-op 종료]
  G --> I[정적 사이트 데이터 최신화]
```

## 구조 (모듈 그래프)
```mermaid
flowchart TB
  RP["scripts/run_pipeline.py<br/>오케스트레이터"]

  subgraph COMMON["scripts/common"]
    CFG["config.py<br/>통제어휘 로더"]
    OKF["okf.py<br/>content_hash · to_markdown"]
  end
  subgraph ING["scripts/ingest"]
    BASE[base.py]
    KO[kopis.py]
    TO[tourapi.py]
    WSA["websearch.py<br/>Exa·Brave·Tavily"]
  end
  subgraph NORM["scripts/normalize"]
    T2O[to_okf.py]
    UPS[upsert.py]
  end
  subgraph BUILD["scripts/build"]
    VAL[validate.py]
    BI[build_index.py]
    SQ["build_sqlite.py<br/>FTS5"]
    WI[wiki_index.py]
  end
  subgraph REC2["scripts/recommend"]
    REC["rank.py<br/>규칙기반"]
    AIP["ai_planner.py<br/>Gemini + 폴백"]
  end
  NOT["scripts/notify/dispatch.py"]
  MAC["scripts/macro/apply.py"]

  RP --> ING --> NORM --> BUILD
  BASE --> KO & TO & WSA
  KO & TO & WSA --> T2O --> UPS
  CFG -.-> T2O & BI & WI & WSA
  OKF -.-> UPS & BI & WI & SQ
  REC --> AIP
  RP --> REC & NOT & MAC
  BUILD --> WEB["web/public/data/*.json + events.db → index.html"]
```

## 문서 (구체 설계)
| 문서 | 내용 |
|---|---|
| [docs/00-overview](docs/00-overview.md) | 비전·제약·성공기준 |
| [docs/01-architecture](docs/01-architecture.md) | 시스템 아키텍처·디렉터리 |
| [docs/02-data-model-okf](docs/02-data-model-okf.md) | OKF/schema.org 데이터 모델 |
| [docs/03-data-sources-apis](docs/03-data-sources-apis.md) | 무료 API 카탈로그 |
| [docs/04-ingestion-pipeline](docs/04-ingestion-pipeline.md) | 수집 파이프라인·지식 DB(llm-wiki) |
| [docs/05-search-and-map](docs/05-search-and-map.md) | 검색·지도 UI |
| [docs/06-application-macro](docs/06-application-macro.md) | 신청 매크로 |
| [docs/07-notifications](docs/07-notifications.md) | 결과 알람 |
| [docs/08-ai-planning](docs/08-ai-planning.md) | AI 추천 플래닝 |
| [docs/09-saas-free-stack](docs/09-saas-free-stack.md) | 무료 SaaS 스택 |
| [docs/10-roadmap-milestones](docs/10-roadmap-milestones.md) | 로드맵·마일스톤·리스크 |
| [docs/11-discovery-sqlite-ai](docs/11-discovery-sqlite-ai.md) | 웹검색 발견·SQLite·Gemini (2026 개선) |
| [docs/12-naming-brainstorm](docs/12-naming-brainstorm.md) | 네이밍 브레인스토밍·후보 평가 |
| [docs/13-brand](docs/13-brand.md) | 브랜드 가이드(이름·슬로건·마스코트·컬러) |

## 레이아웃
```
docs/         계획 문서
knowledge/    지식 DB (llm-wiki: index.md + events/** + schema/)
config/       sources/regions/themes/age-bands 통제 어휘
scripts/      ingest(공공+웹검색) · normalize · build(+sqlite) · recommend(+gemini) · macro · notify
web/          정적 프런트엔드 (Leaflet + Fuse.js)
.github/      ingest 워크플로우 (일 1회 cron)
assets/       브랜드 자산 (마스코트·아이콘 원본)
```

## 빠른 시작 (오프라인, 키 불필요)
샘플 fixture(`raw/**/sample-*`)로 전체 파이프라인이 네트워크 없이 돈다.
```bash
pip install -r requirements.txt
python -m scripts.run_pipeline          # 수집(웹검색 포함)→정규화→검증→인덱스→SQLite→wiki
python -m unittest discover -s tests    # 단위 테스트 40종
python -m scripts.ingest.websearch      # 웹검색 발견 후보(Exa/Brave/Tavily, 오프라인 픽스처)
python -m scripts.build.build_sqlite --query 축제   # SQLite FTS5 전문검색 데모
python -m scripts.recommend.rank        # 규칙기반 추천 + 주간 플랜(JSON)
python -m scripts.recommend.ai_planner  # Google AI Studio(Gemini) 플래너(+규칙 폴백)
python -m scripts.notify.dispatch       # 마감임박/신규 알람(드라이런)
python -m scripts.macro.apply           # 신청 매크로 잡 계획(약관 게이팅)

# 정적 사이트 미리보기
cd web/public && python -m http.server 8000   # → http://localhost:8000
```
실제 운영 시 `.env`(`.env.example` 참고)에 무료 API 키를 넣으면 어댑터가 원격 수집으로 전환.

## 지금 동작하는 것 (검증됨)
- ✅ KOPIS·TourAPI 어댑터 → schema.org/Event(OKF) Markdown 적재, content_hash 증분 upsert + archived 정책
- ✅ JSON Schema 검증 게이트, `events/facets/regions/updated.json` 빌드
- ✅ 정적 UI: Leaflet 지도 + 5축 필터(지역/기간/나이/테마/키워드) + Fuse 검색 + AI 추천 다이얼로그
- ✅ 규칙기반 추천/주간 플래너(무료, LLM 폴백), 마감임박 알람 + 중복억제
- ✅ 신청 매크로 잡 계획 + 약관 자동제출 게이팅(반자동 강등 보장)
- ✅ 지식 wiki 자동 인덱싱(REGIONS/THEMES/SOURCES)
- ✅ 웹검색 발견 레이어(Exa·Brave·Tavily) — 신뢰도/날짜/지역 가드로 OKF 후보 적재, `🔎 발견` 배지
- ✅ SQLite(libSQL) 파생 인덱스 `events.db` — FTS5 한국어 전문검색 + sido/theme/status 교차필터
- ✅ Google AI Studio(Gemini) 주간 플래너 — responseSchema JSON 강제 + 환각 가드 + 규칙 폴백

## 상태
M0–M2 코어 + M3–M6 검증 슬라이스 동작(샘플 데이터). 다음: 실 API 키 연결(원격 수집), Cloudflare Pages/Workers 배포, Playwright 매크로 러너 실행부, 시/도 경계 GeoJSON.
