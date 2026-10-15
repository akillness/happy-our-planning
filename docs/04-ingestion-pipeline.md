---
title: 수집 파이프라인 & 지식 DB (llm-wiki)
status: draft
okf_type: TechnicalDocument
updated: 2026-06-20
---

# 04. 수집 파이프라인 & 지식 DB (llm-wiki 적용)

## 1. 파이프라인 단계 (정확한 흐름)

fetch → validate → currentindex → optionalGETcheck → indexreapplyproof → loadsameJSONonce → SQLite+countgate → HTMLpages → wiki

1. **fetch**: scripts/ingest/<src>.py → raw/<src>/<date>/page-*.json (불변 원본)
2. **normalize**: scripts/normalize/to_okf.py --profile <src> → OKF Event dict + geocode
3. **upsert**: scripts/normalize/upsert.py → knowledge/events/<YYYY>/<MM>/<sido>/<id>.md (content_hash diff)
4. **validate**: scripts/build/validate.py → JSON Schema 검증, 실패 시 CI fail (이후 단계 진행 안 함)
5. **currentindex**: scripts/build/build_index.py → web/public/data/{events.json, facets.json, regions.json, updated.json}
6. **optionalGETcheck**: env NOTCHIMA_CHECK_LINKS=1 시만 활성 → scripts/ops/check_links.py → link-checks.json 캐시 갱신 (미수행 시 skip)
7. **indexreapplyproof**: cached verified proof 40건/120초 한도 내 적용, fresh proof(7day 내) 재사용
8. **loadsameJSONonce**: SQLite+countgate가 step 5의 events.json 공유 사용 (재fetch 없음)
9. **SQLite+countgate**: scripts/build/build_sqlite.py → events.db 재생성, 행사 수 검증
10. **HTMLpages**: web/public/events/{id}.html 정적 상세 페이지 생성 (동일 events 배열 기준)
11. **wiki**: scripts/build/wiki_index.py → knowledge/index.md + graphify update

**API 키 처리**: KOPIS/TourAPI 어댑터는 auth.param에 대해 strip() 후 한 번만 unquote/decode 수행. 원본 raw 응답은 knowledge/sources/에 보존.

**에러 정책**:
- JSON Schema 검증 실패 → 이후 단계(events.json 재생성, pages, wiki) 진행 안 함, 기존 published 미변경
- SQLite 에러 → HTML pages/wiki 미생성, 기존 데이터 보존
- 부분 실패 → collection-status.json에 소스별 mode/error 기록, 성공 소스만 updated.json 반영

## 2. 증분 적재 알고리즘 (가볍게 축적)
1. fetch는 `since = max(fetched_at)` 기준 신규/갱신 페이지만.
2. normalize 후 `content_hash = sha256(정규필드 정렬 직렬화)`.
3. 기존 파일의 hash와 동일 → skip(쓰기 없음 → git diff 0).
4. 다르면 파일 갱신 + `fetched_at` 갱신.
5. 소스에서 사라진 항목은 `status: archived`, `x_lastSeen` 기록(삭제 안 함).
→ 데이터는 **축적**되고 변경분만 **개선**된다.

## 3. llm-wiki 시스템 적용 (지식 DB = wiki)
`knowledge/` 를 llm-wiki 패턴으로 구성한다 (참조: `~/vaults/llm-wiki`).
- `knowledge/index.md` — 콘텐츠 지향 맵. 자동 생성 블록:
  - `<!-- SOURCES:START -->` 소스별 최신 수집 요약/건수
  - `<!-- THEMES:START -->` 테마별 행사 수 + 대표 링크
  - `<!-- REGIONS:START -->` 시/도별 행사 수 + 링크
- `knowledge/events/**` — 행사 페이지(frontmatter=OKF, 본문=큐레이션 노트).
- `knowledge/regions/<sido>.md`, `knowledge/themes/<theme>.md` — 집계/허브 페이지(빌드 생성).
- `knowledge/sources/<src>-<date>.md` — 수집 실행 로그(건수/에러/소요) = llm-wiki sources/.
- `knowledge/reports/` — 주간 트렌드/큐레이션 리포트(AI 생성, 09 참조).
- **graphify**: 파이프라인 6단계에서 `graphify update .` 실행 → `graphify-out/GRAPH_REPORT.md` 로 지식 그래프 유지(행사↔지역↔테마↔주최 관계).

- `.github/workflows/ingest.yml` — 예약 설정: 매일 03:00 KST (UTC 18:00 cron `0 18 * * *`) + workflow_dispatch 수동 실행
  concurrency: 동시 실행 직렬화(`cancel-in-progress: false`)
  성공 시 git commit/push 자동 발생
  실패 시 published 데이터 미변경, 진단 artifact(`knowledge/sources/collection-status.json`) 7일 보존

## 5. 관측/품질
- 매 실행 `knowledge/sources/<src>-<date>.md` 에 {fetched, upserted, skipped, errors, ms} 기록.
- 검증 실패율, 좌표 결측률, 소스별 신선도(가장 오래된 fetched_at)를 빌드 시 `updated.json`에 노출.
- 중복 병합 후보(`same_as`)는 `knowledge/reports/dedupe-candidates.md`로 주기 출력.

## 6. 로컬 실행
```bash
python -m scripts.ingest.kopis --since 2026-06-01
python -m scripts.normalize.to_okf --profile kopis
python -m scripts.normalize.upsert
python -m scripts.build.validate
python -m scripts.build.build_index
python -m scripts.build.wiki_index && graphify update .

## 7. 온라인 vs 오프라인 모드 & 수동 재실행

### 오프라인 픽스처 (샘플 데이터 — 네트워크 불필요)
```bash
# 키 없이 전체 파이프라인 실행 (설치 검증용)
python -m scripts.run_pipeline          # raw/**/sample-* 사용, 네트워크 호출 0

# 결과: web/public/data/events.json 생성 (fixture 모드 표시)
# knowledge/sources/<src>-<date>.md에 "mode: offline" 기록
```

### 온라인 수집 (실제 public API)
```bash
# .env에 키 설정 후
export KOPIS_KEY=...
export TOURAPI_KEY=...
export VWORLD_KEY=...          # 지오코딩
export EXA_API_KEY=...         # 선택: 웹검색

python -m scripts.run_pipeline          # live 어댑터 활성화
# 결과: knowledge/sources/<src>-<date>.md에 "mode: live" + fetched_count/errors 기록
```

### 오류 복구 — 안전한 수동 재실행
일일 cron 실패 시 **blind rebase/push 금지**. 대신:

**Step 1: 실패 원인 파악**
```bash
# GitHub Actions 로그 또는 로컬 진단
git pull
cat knowledge/sources/collection-status.json | jq '.sources[] | {key, mode, last_success_at, error}'

# 예: {"key": "kopis", "mode": "offline", "error": "Network timeout"}
# → 해당 소스만 실패, 다른 소스는 성공 가능
```

**Step 2: 선택적 재수집 (부분 또는 전체)**
```bash
# 부분: 특정 소스만 재시도
python -m scripts.ingest.kopis --since 2026-10-14   # KOPIS만

# 또는 전체 파이프라인 재실행 (fixture 모드에서 시작)
python -m scripts.run_pipeline
```

**Step 3: 검증 후 커밋**
```bash
# 변경 확인
git status
git diff knowledge/sources/ | head -50    # 신규/변경 요약

# 정상이면 수동 커밋
git add knowledge/ web/public/data/
git commit -m "fix(ingest): retry after network error (manual run)"
git push
```

**주의 — blind rebase/push는 절대 금지:**
- `git rebase origin/main` ❌ (이전 성공 데이터 손상)
- `git push -f` ❌ (히스토리 소실)
- `--no-verify` 스킵 ❌ (검증 게이트 무시)

### 마지막 성공 구분
- `knowledge/sources/<src>-<date>.md` 의 `mode: live`가 실제 API 수집
- fixture 빌드 시각은 `updated.json.last_success_at`에 **기록 안 함** (투명성)
- cron 자동 성공만 `last_success_at` 갱신
- 수동 재실행도 모드/시각을 기록하므로 부분 성공·전체 성공을 명확히 추적 가능
