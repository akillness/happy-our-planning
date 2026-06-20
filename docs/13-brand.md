---
title: 브랜드 가이드 — 놓치마 (Notchima)
status: draft
owner: planning
okf_type: BrandGuide
updated: 2026-06-20
---

# 브랜드 가이드 — 놓치마 (Notchima)

## 1. 네이밍 결정

- **제품명**: 놓치마 (로마자 핸들 `notchima`)
- **슬로건**: **기회, 다 챙겨드릴게요.**
- **결정 근거**: 후보였던 "놓치마"와 "다챙겨"를 합본. 짧고 핸들/도메인이
  깔끔한 **놓치마**를 이름으로(FOMO·마감 방어 축), 위임·신뢰의 메시지인
  **다챙겨**를 슬로건으로 흡수(매크로+알람+AI 추천 전체를 포괄).
- 코드네임 `happy-our-planning`(repo/CI)은 전환 비용 0으로 유지.

## 2. 마스코트

- 지도핀(teardrop) 형태의 친근한 도우미 캐릭터. 잠자리채로 떨어지는 행사
  티켓을 "잡아채는" 모습 = *기회를 놓치지 않게 대신 잡아준다*는 컨셉의 시각화.
- 자산:
  - `assets/brand/notchima-mascot.png` — 풀바디 원본 (1254×1254)
  - `assets/brand/notchima-mascot-256.png` — README/문서용 축소판
  - `assets/brand/notchima-icon.png` — 앱 아이콘(머리 클로즈업) 원본
  - `web/public/icon.png` (128×128, ~23KB) / `web/public/favicon.png` (64×64) — 웹 헤더·파비콘. 헤더 로고는 36px로 렌더되므로 원본(1254×1254)을 그대로 싣지 않고 web 최적화 축소판을 둔다.

## 3. 컬러

| 역할 | 값(근사) | 용도 |
|---|---|---|
| Primary (민트) | `#3FD3B0` 계열 | 마스코트 본체·브랜드 메인 |
| Accent (코랄) | `#FF7A5C` 계열 | 강조·CTA·핀 상단 |
| 무료/성공 | `#2ea44f` | 배지(₩0/month) |

## 4. 적용 위치

- `README.md` — 마스코트 + 이름 + 슬로건 헤더
- `web/public/index.html` — 파비콘, 헤더 로고(icon.png) + 슬로건

## 5. 생성 방법(재현)

마스코트/아이콘은 `god-tibo-imagen`(`gti`, Codex 백엔드 재사용)으로 생성.
아이콘은 마스코트를 `--image` 레퍼런스로 넘겨 일관성 유지.
