<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-06-25 23:02 KST | Updated: 2026-06-25 23:02 KST -->

# docs/

## Purpose
설계 문서 묶음. 비전·아키텍처·데이터모델·소스·파이프라인·검색·매크로·알람·AI·SaaS스택·로드맵·발견개선·네이밍·브랜드·포지셔닝, 그리고 코드 리뷰. spec-stack의 배경 설명 레이어(실행 SSOT는 `../specs/`).

## Key Files

| File | Description |
|------|-------------|
| `00-overview.md` | 비전·제약·성공기준 |
| `01-architecture.md` | 시스템 아키텍처·디렉터리 |
| `02-data-model-okf.md` | OKF/schema.org 데이터 모델 |
| `03-data-sources-apis.md` | 무료 API 카탈로그 |
| `04-ingestion-pipeline.md` | 수집 파이프라인·지식 DB |
| `05-search-and-map.md` | 검색·지도 UI |
| `06-application-macro.md` | 신청 매크로 |
| `07-notifications.md` | 결과 알람 |
| `08-ai-planning.md` | AI 추천 플래닝 |
| `09-saas-free-stack.md` | 무료 SaaS 스택 |
| `10-roadmap-milestones.md` | 로드맵·마일스톤(M0–M7)·리스크 |
| `11-discovery-sqlite-ai.md` | 웹검색 발견·SQLite·Gemini(2026 개선) |
| `12-naming-brainstorm.md` | 네이밍 브레인스토밍 |
| `13-brand.md` | 브랜드 가이드 |
| `14-positioning-and-gaps.md` | 포지셔닝 재정의 + 기술 갭 분석 + 개선 액션플랜 |
| `CODE_REVIEW_2026.md` | 실동작 검증 + 우선순위 개선 설계 |

## For AI Agents

### Working In This Directory
- 문서는 배경/설명. 기계검증 요구가 바뀌면 `../specs/spec.md`를 먼저 고친다(병렬 SSOT 금지).
- 신규 전략·연구·스프린트 산출물은 `../wiki/`(llm-wiki 루트)에 둔다.

### Testing Requirements
문서 디렉터리 — 코드 테스트 없음. 링크 정합·메타 frontmatter 유지.

### Common Patterns
번호 prefix로 읽기 순서 명시. frontmatter에 status/updated/okf_type.

## Dependencies

### Internal
`../specs/`(SSOT), `../scripts/`(구현), `../wiki/`(전략 산출물).

### External
없음.

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
