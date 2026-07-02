<!-- Generated: 2026-06-25 23:02 KST | Updated: 2026-06-25 23:02 KST -->

# 놓치마 (Notchima) — happy-our-planning

## Purpose
대한민국 지도 기반 전국 행사(공연·축제·전시·교육·공모·정부지원) 발견 인프라. 공공 API + 웹검색을 수집해 `schema.org/Event`(OKF) flat-file 지식 DB로 정규화하고, 정적 UI(Leaflet 지도 + 5축 필터)로 검색하며, 신청 매크로·마감 알람·AI 주간 플래너를 붙인 **월 0원·무료티어·파일기반** SaaS. 코어 설계 한 줄: `공공 API → 정규화(OKF Markdown = 지식 DB) → 사전빌드 JSON → 정적 UI`; 상태/AI/알람만 엣지 함수.

## Key Files

| File | Description |
|------|-------------|
| `README.md` | 비전·핵심기능·시스템 로직·검증된 동작·빠른 시작 |
| `requirements.txt` | pyyaml·jsonschema·httpx (표준 라이브러리 위주, 오프라인 폴백) |
| `.env.example` | 무료 API 키 주입 템플릿 (키 부재 시 픽스처로 동작) |
| `.github/workflows/ingest.yml` | 일 1회 cron 수집 파이프라인 (CI) |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `scripts/` | Python 파이프라인: 수집·정규화·빌드·추천·알람·매크로 (see `scripts/AGENTS.md`) |
| `web/` | 정적 프런트엔드(Leaflet+Fuse.js) + Cloudflare Worker 엣지 함수 (see `web/AGENTS.md`) |
| `config/` | 통제 어휘 YAML: sources/regions/themes/age-bands/search/macro-sites (see `config/AGENTS.md`) |
| `knowledge/` | flat-file 지식 DB (llm-wiki): index.md + events/** + schema/ (see `knowledge/AGENTS.md`) |
| `tests/` | unittest 스위트 (58종, 회귀 가드) (see `tests/AGENTS.md`) |
| `docs/` | 설계 문서 00–14 + CODE_REVIEW (see `docs/AGENTS.md`) |
| `specs/` | spec-stack 동결 번들 (SSOT): spec/seed/tasks/harness (see `specs/AGENTS.md`) |
| `wiki/` | Obsidian 보관함 + 전략 산출물 루트 (llm-wiki root) (see `wiki/AGENTS.md`) |
| `raw/` | 원본 API 응답 + 오프라인 픽스처 (sample-*) |
| `assets/` | 브랜드 자산(마스코트·아이콘)·다이어그램 SVG |

## For AI Agents

### Working In This Directory
- **SSOT 방향은 단방향**: `specs/spec.md → specs/seed.yaml`. 요구가 바뀌면 spec을 먼저 고치고 seed를 재동결한다. 두 파이프라인을 병렬 SSOT로 돌리지 않는다.
- **지식 DB의 진실원천은 `knowledge/` flat-file**. `web/public/data/events.db`(SQLite)는 삭제 후 재생성 가능한 파생물 — SSOT로 취급 금지.
- 제약 C1–C7(spec.md §2)은 절대 위반 금지: 유료 의존 0, flat-file SSOT, OKF 스키마, 비밀키 미커밋, 약관 자동화 게이팅, AI 가드레일, 월 0원.

### Testing Requirements
bash
python -m scripts.run_pipeline        # 수집→검증→인덱스→SQLite→wiki (오프라인 픽스처로 키 없이 통과)
python -m unittest discover -s tests  # 58종, validate.py rc==0 게이트

완료 정의(DoD): 40+ green, `validate.py` rc==0, `events.db` 재생성 동일 카운트, C1–C7 만족.

### Common Patterns
- 키 부재 시 자동으로 fixture/규칙기반으로 강등되는 폴백 설계가 일관적.
- 모든 외부 호출은 무료티어 한도 내. 신규 키는 `config/search.yaml`·`sources.yaml`에 한도 주석 유지.

## Dependencies

### Internal
`scripts.run_pipeline`가 오케스트레이터 — `to_okf → validate → build_index → build_sqlite → wiki_index` 순으로 호출.

### External
공공 API(KOPIS·TourAPI), 웹검색(Exa·Brave·Tavily), Gemini(Google AI Studio), Telegram Bot API, Cloudflare Pages/Workers, GitHub Actions — 전부 무료티어.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
