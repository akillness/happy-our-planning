---
title: 놓치마 — 지도 우선 뷰포트·팝업 기하·필터·URL 복원 검증 (F2.9)
status: running
okf_type: TechnicalDocument
layer: spec-stack/verify
updated: 2026-10-15
---

# UI 검증 체크리스트 (AC-F2.9)

동결 계약: `specs/spec.md@1.6.0` → `specs/seed.yaml`. 새 지도 우선 게이트는 현재 실행 중이며, 아래 1.3.1 역사적 결과는 회귀 증거로만 보존한다.

## 재현 명령

```bash
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' python3 specs/verify/run_ui_check.py
```

설치된 Google Chrome으로 임시 HTTP 서버의 정적 앱을 조작한다. 데이터는 **KST 2026-10-15로 고정된 routed fixture**이며 실제 `events.json`, 원격 수집 성공, 공개 배포의 증명이 아니다.

## 신규 계약 상태 (1.6.0) — 진행 중

**현재:** 3개 UI 게이트 실행 중, 아직 PASS 아님. 최종 게이트 카운트는 Main 에이전트가 제공할 예정.

## 지도 우선 신규 계약 (1.6.0) (현재 진행 중)

| 계약 | 필요한 관찰 |
|---|---|
| 데스크톱 셸 | 1440×900 및 1280×800에서 실제 `#map`/`.map-panel`이 물리 뷰포트 네 변을 1 CSS px 이내로 채움. 검색 dock 중심, 우측 결과 panel은 지도 위 overlay. |
| 모바일 셸 | 360×800 및 390×844에서 지도 우선, 결과는 **기본 접힌 우측 드로어**(아이콘으로 열기/닫기), `#resultsScroll` 내부만 스크롤, 문서 스크롤·가로 넘침 없음. |
| 실제 Leaflet | Leaflet 1.9.4 실제 `L.Map`의 `latLngToContainerPoint`와 렌더된 marker/popup DOM을 비교하고 pan/zoom/resize 뒤에도 지리 좌표 tip 오차 ≤2 CSS px. |
| 핀·팝업 | 마커 아이콘 bottom-center tip과 지리 좌표가 일치하며, popup pointer bottom-center가 pin head에 닿음(≤2 CSS px). single/group/edge/long-content를 포함한다. |
| overlay 안전 | 검색 dock·결과 sidebar·mobile sheet 안으로 popup이 들어가지 않으며 Leaflet root CSS transform 덮어쓰기가 없다. |
| 상호작용·fallback | 필터 disclosure를 네이티브로 열고, 필터 조작은 map gesture가 되지 않음. 결과 collapse/Escape/focus 복귀, paging/filter/URL/error/empty/reduced-motion을 보존하며, Leaflet 초기화 실패 시 Locate는 위치를 가장하지 않고 fallback 안내를 표시한다. |
| 터치 | 보이는 주요 컨트롤은 44×44 CSS px 이상이다. |

필수 재현 명령:

```bash
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' python3 specs/verify/map_viewport_check.py
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' python3 specs/verify/run_ui_check.py
```

두 harness 모두 routed in-memory fixture만 사용하며 `web/public/data`를 쓰지 않는다. 새 결과가 하나라도 실패하면 종료코드 1이다.

## 역사적 1.3.1 증거 (현재 게이트 아님)

기존 `specs/spec.md@1.3.1`의 58/58 결과와 hero ≤180px, 목록-before-map, 모바일 hero 이미지 주장은 **역사적 관찰**이다. 1.4.0 지도 우선 계약의 통과 근거로 재사용하지 않는다.

## 역사적 1.3.1 RED → GREEN 증거

1. 개편 전: 56개 중 53개 통과, 종료코드 1. 히어로 300px, 활동 그림 없음, 모바일 지도 우선의 세 체크가 의도대로 실패했다.
2. 개편 후: 같은 명령으로 56/56 통과했다.
3. 코드 리뷰에서 미선택 만들기 건수 대비 4.388:1을 발견했다. 대비 회귀 검사를 추가하자 57개 중 56개 통과, 해당 체크만 실패했다.
4. 건수 색상만 `--muted`에서 `--ink-soft`로 바꾼 뒤 같은 명령으로 **57/57 통과**했다. 기존 선택 상태 CSS의 흰색 우선순위는 유지했다.

## 역사적 1.3.1 증거 산출물

- `evidence/ui-check.json`: 57개 체크 이름·결과·관찰값과 fixture 문맥.
- `evidence/ui-desktop-family.png`, `evidence/ui-desktop-filtered.png`: 통제 fixture 데스크톱.
- `evidence/ui-360px.png`, `evidence/ui-mobile-family.png`: 통제 fixture 모바일.
- `evidence/watercolor-desktop.png`, `evidence/watercolor-mobile.png`: Main이 실제 오프라인 공개 목록을 확인한 별도 스모크 캡처.
- `assets/design/pencil-proposals/selection.json`: 세 시안 비교, A 선정 이유와 보존 계약.

## 역사적 1.3.1 빌드·배포 경계

`python3 -m scripts.run_pipeline` 종료코드 0: OKF 10/10 검증, JSON/SQLite/정적 상세 각 8건, JSON과 SQLite ID 일치, 원본 knowledge 보존. 세 새 원본과 네 runtime variant의 byte 수·SHA-256은 manifest와 일치했다.

사용자 확인에 따라 실제 배포 대상은 기존 Vercel `happy-our-planning` 프로젝트로 정정했다. Hobby 플랜·기존 CLI 인증·Root Directory `web/public`을 확인한 뒤, 검증된 21개 정적 파일만 prebuilt 업로드하고 운영에 승격했다. 초기 수동 배포 ID는 `dpl_71VHevFRT9QSSrbu6Kjt6ZenrnEK`이다. 당시 운영 주소 https://happy-our-planning.vercel.app/ 의 9개 필수 파일은 인증 우회 없이 HTTP 200·로컬 SHA-256 일치, DB·환경 파일·Worker는 404를 확인했다. 상세 증거 `evidence/vercel-release.json`은 초기 수동 배포와 이후 Git 소스 배포를 구분한다. 이전 Cloudflare 인증 조사는 현재 배포 차단 사유가 아니다.

### 역사적 1.3.1 완료 게이트 재실행

- 최종 Python 3.12 게이트 `uvx --python 3.12 --with-requirements requirements.txt python -m unittest discover -s tests`: 206개 실행, 203개 통과, 기존 3개 skip, 종료코드 0.
- 최종 Python 3.12 스키마 게이트 `uvx --python 3.12 --with-requirements requirements.txt python scripts/build/validate.py`: 10/10 통과, 0 실패, 종료코드 0.
- `web/public/data/events.db`를 실제 삭제한 상태에서 `python3 -m scripts.run_pipeline` 재실행: 종료코드 0, 삭제 전후 8→8건, 동일 ID 집합과 JSON 일치. 실패 시 원본 파생 DB를 복구하도록 메모리 백업한 뒤 검사했다.
- `node --check web/public/app.js`: 종료코드 0.
- 초기 수동 배포용 묶음 `web/dist/notchima-static-20261015.zip`: 21개 파일, 331,787 bytes, CRC 검사 통과. SHA-256 `98b71f734728df806544467d696635abe0a82b192aa0c3c441156cf948fce88b`. 이후 Fancy UI Git 배포와는 다른 초기 관측 산출물이다. 파생 `events.db`·설계 원본·Pencil 시안은 묶음에서 제외했고 DB는 빌드 검증용 위치에 유지한다. 묶음은 기존 ignore 대상인 `web/dist/`에 둔다.
- 최종 리뷰 후 같은 Chrome 명령으로 다시 57/57 통과했다. 필터 순서는 1440px 페이지에서 직접 측정하고, 초기 활동 칩의 `aria-pressed=false`와 선택 후 `true`를 모두 확인한다. 새 그림 로드 검사는 Playwright 타임아웃만 잡아 `loaded=false`로 실패를 기록하며 다른 예외를 숨기지 않는다.

### 역사적 1.3.1 Fancy UI 회귀 게이트

동시 반영된 Fancy UI에서 54/57 RED를 재현했다. 추천 제목이 `strong`에서 `.rec-open` 버튼으로 바뀌었는데 테스트가 이전 선택자를 사용한 것이 원인이었다. Tester가 추출 위치를 수정한 뒤 기존 57/57을 재확인했다. 시작일만 있는 종료 행사의 제외 검사도 실제 결정적 fixture의 종료 카드·활성 대조 추천을 요구하도록 바로잡아 빈 목록 통과를 없앴다. 행사 ID/개수·활동 범위·활성 행사만 추천·레이아웃 수용 기준은 그대로 유지했다.

역사적 1.3.1 관찰: 종료·마감 배지의 렌더 대비 검사 1개를 추가해 57/58 RED(4.0379:1)를 확인한 뒤, 전경색만 `--ink-soft`로 변경했다. 최종 실행은 58/58이었다.

역사적 1.3.1 최종 명령·타입 게이트 기록이다. 이는 1.4.0 지도 우선 계약의 실행 결과가 아니다.
