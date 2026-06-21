---
title: 놓치마 — UI 5필터·반응형·URL 복원 검증 체크리스트 (T4 · F2.3)
status: verified
okf_type: TechnicalDocument
layer: spec-stack/verify
updated: 2026-06-21
---

# UI 검증 체크리스트 (T4 · AC-F2.3)

> 재현: `python3 specs/verify/run_ui_check.py` (Playwright+chromium, 무료·선택).
> web/public 를 임시 HTTP 서버로 띄워 실제 브라우저로 조작·캡처한다. 증거는 `evidence/`.
> **마지막 실행: 8/8 통과 (RC 0).**

## 5축 필터 (F2.3)
| 축 | 컨트롤 | 검증 | 결과 |
|----|--------|------|------|
| 키워드 | `#q`(Fuse 검색) | "축제" 입력 → 결과 좁힘 | ✅ |
| 지역 | `#sido` | 서울특별시 선택 | ✅ |
| 테마 | `#theme` | 축제 선택 | ✅ |
| 나이대 | `#age` | 어린이 선택 | ✅ |
| 기간 | `#from`/`#to` | 2026-01-01 ~ 2026-12-31 | ✅ |
| (부가) 신청가능 | `#applyable` | Open + 신청기간 내 | ✅ |

## URL 상태 동기화·복원 (신규 구현)
`app.js`에 `writeURL()`/`restoreFromURL()` 추가 — 필터 변경 시 `history.replaceState`로
쿼리스트링 동기화, 로드/`popstate` 시 입력값 복원. **공유 링크·새로고침 보존.**

| 체크 | 증거 |
|------|------|
| 5축 필터 → URL 동기화 | `?q=축제&sido=서울특별시&theme=축제&age=어린이&from=2026-01-01&to=2026-12-31&applyable=1` |
| URL 복원(새 세션 입력값) | 신규 페이지가 동일 URL에서 7개 입력값 전부 복원 |
| URL 복원(카운트 일치) | 복원 세션 결과 카운트 == 원본 필터 카운트 |
| reset → 전체 복귀 | 10건 전체로 복귀 |

## 반응형
| 뷰포트 | 스냅샷 | 결과 |
|--------|--------|------|
| 360px 모바일 | `evidence/ui-360px.png` | ✅ 단일 컬럼 레이아웃 캡처 |
| 1280px 데스크톱 | `evidence/ui-desktop-filtered.png` | ✅ 지도+리스트 2단 |

## 증거 산출물
- `evidence/ui-check.json` — 8개 체크 기계판독 결과(passed 8/8)
- `evidence/ui-360px.png`, `evidence/ui-desktop-filtered.png` — 스냅샷

## DoD 충족
- agent-browser 동급의 헤드리스 chromium 스냅샷으로 5축 필터 + URL 상태 복원 확인(증거 첨부). ✅
