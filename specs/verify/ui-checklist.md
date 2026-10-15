---
title: 놓치마 — 검색·수채화·반응형·URL 복원 검증 (F2.3 · F2.9)
status: verified
okf_type: TechnicalDocument
layer: spec-stack/verify
updated: 2026-10-15
---

# UI 검증 체크리스트 (AC-F2.3 · AC-F2.9)

동결 계약: `specs/spec.md@1.3.1` → `specs/seed.yaml`. 마지막 로컬 UI 실행은 **57/57 통과, 종료코드 0**이다. Vercel 운영 검증은 아래 별도 배포 증거로 구분한다.

## 재현 명령

```bash
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' python3 specs/verify/run_ui_check.py
```

설치된 Google Chrome으로 임시 HTTP 서버의 정적 앱을 조작한다. 데이터는 **KST 2026-10-15로 고정된 routed fixture**이며 실제 `events.json`, 원격 수집 성공, 공개 배포의 증명이 아니다.

## 보존·추가 검증

| 계약 | 관찰된 결과 |
|---|---|
| 기존 5축 필터·무료·가족·신청 가능 | 기존 48개 체크 보존. 필터와 URL 동기화·복원·초기화 통과 |
| 가족/활동 원문 근거 | 출처 기반 결과만 표시하고 정보 없음은 추정하지 않음 |
| 링크·상세·추천 | 미검증/차단 외부 앵커 금지, 상세·포커스 복귀와 종료 행사 추천 제외 통과 |
| canonical 히어로 | `family-outing.webp` 실제 로드, 1440px에서 높이 160px (상한 180px) |
| 검색 우선 | 전체 필터가 활동 바로가기보다 먼저 표시됨 |
| 두 활동 그림 | 과학·만들기 WebP를 스크롤 후 `complete && naturalWidth > 0`으로 로드 확인. 빈 alt와 텍스트 범주/건수 접근성 이름 유지 |
| 그림 없는 실제 범주 | 통제된 신체·놀이 범주가 텍스트 버튼으로 남음 |
| 활동 선택 | 과학·만들기 클릭 후 select·URL `activity`·aria-pressed·결과/카운트 동기화 |
| 건수 글자 대비 | 미선택 과학 5.45:1, 만들기 4.634:1. 선택 건수는 흰색 유지 |
| 360px | 가로 넘침 없음, 행사 목록이 지도보다 먼저, 활동 타깃 336×84px로 최소 44px 충족 |
| 모바일 히어로 | canonical `family-outing-mobile.webp` 로드 |

## RED → GREEN 증거

1. 개편 전: 56개 중 53개 통과, 종료코드 1. 히어로 300px, 활동 그림 없음, 모바일 지도 우선의 세 체크가 의도대로 실패했다.
2. 개편 후: 같은 명령으로 56/56 통과했다.
3. 코드 리뷰에서 미선택 만들기 건수 대비 4.388:1을 발견했다. 대비 회귀 검사를 추가하자 57개 중 56개 통과, 해당 체크만 실패했다.
4. 건수 색상만 `--muted`에서 `--ink-soft`로 바꾼 뒤 같은 명령으로 **57/57 통과**했다. 기존 선택 상태 CSS의 흰색 우선순위는 유지했다.

## 증거 산출물

- `evidence/ui-check.json`: 57개 체크 이름·결과·관찰값과 fixture 문맥.
- `evidence/ui-desktop-family.png`, `evidence/ui-desktop-filtered.png`: 통제 fixture 데스크톱.
- `evidence/ui-360px.png`, `evidence/ui-mobile-family.png`: 통제 fixture 모바일.
- `evidence/watercolor-desktop.png`, `evidence/watercolor-mobile.png`: Main이 실제 오프라인 공개 목록을 확인한 별도 스모크 캡처.
- `assets/design/pencil-proposals/selection.json`: 세 시안 비교, A 선정 이유와 보존 계약.

## 빌드·배포 경계

`python3 -m scripts.run_pipeline` 종료코드 0: OKF 10/10 검증, JSON/SQLite/정적 상세 각 8건, JSON과 SQLite ID 일치, 원본 knowledge 보존. 세 새 원본과 네 runtime variant의 byte 수·SHA-256은 manifest와 일치했다.

사용자 확인에 따라 실제 배포 대상은 기존 Vercel `happy-our-planning` 프로젝트로 정정했다. Hobby 플랜·기존 CLI 인증·Root Directory `web/public`을 확인한 뒤, 검증된 21개 정적 파일만 prebuilt 업로드하고 운영에 승격했다. 초기 수동 배포 ID는 `dpl_71VHevFRT9QSSrbu6Kjt6ZenrnEK`이다. 당시 운영 주소 https://happy-our-planning.vercel.app/ 의 9개 필수 파일은 인증 우회 없이 HTTP 200·로컬 SHA-256 일치, DB·환경 파일·Worker는 404를 확인했다. 상세 증거 `evidence/vercel-release.json`은 초기 수동 배포와 이후 Git 소스 배포를 구분한다. 이전 Cloudflare 인증 조사는 현재 배포 차단 사유가 아니다.

### 이번 개편의 완료 게이트 재실행

- `python3 -m unittest discover -s tests`: 200개 실행, 197개 통과, 3개 skip, 종료코드 0.
- `python3 scripts/build/validate.py`: 10/10 통과, 0 실패, 종료코드 0.
- `web/public/data/events.db`를 실제 삭제한 상태에서 `python3 -m scripts.run_pipeline` 재실행: 종료코드 0, 삭제 전후 8→8건, 동일 ID 집합과 JSON 일치. 실패 시 원본 파생 DB를 복구하도록 메모리 백업한 뒤 검사했다.
- `node --check web/public/app.js`: 종료코드 0.
- 배포용 묶음 `web/dist/notchima-static-20261015.zip`: 21개 파일, 331,787 bytes, CRC 검사 통과. SHA-256 `98b71f734728df806544467d696635abe0a82b192aa0c3c441156cf948fce88b`. 브라우저가 사용하지 않는 파생 `events.db`, 디자인 연구용 이미지·원본 PNG·Pencil 시안은 배포 묶음에서 제외하고 DB는 빌드 검증용 canonical 위치에 유지한다. 묶음은 기존 ignore 대상인 `web/dist/`에 둔다.
- 최종 리뷰 후 같은 Chrome 명령으로 다시 57/57 통과했다. 필터 순서는 1440px 페이지에서 직접 측정하고, 초기 활동 칩의 `aria-pressed=false`와 선택 후 `true`를 모두 확인한다. 새 그림 로드 검사는 Playwright 타임아웃만 잡아 `loaded=false`로 실패를 기록하며 다른 예외를 숨기지 않는다.
