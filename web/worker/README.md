# `web/worker/` — Cloudflare 엣지 어댑터 (선택 계층 · C7)

flat-file SSOT(C2)를 깨지 않는 **선택적 가속 계층**. 키 미설정 시 클라이언트는 규칙 기반
폴백으로 자연 강등되므로(C1), 이 Worker 없이도 사이트는 동작한다.

| 파일 | 역할 |
|------|------|
| `ai-proxy.js` | Gemini 키를 **서버측 secret**에 보관하는 프록시(`POST /plan`). 브라우저 키 노출 0(C4). |
| `jobqueue.js` | 매크로 잡 접수/상태 큐 stub(`POST /jobs`, `GET /jobs/:id`). 실행은 `scripts/macro/runner.py`. |
| `wrangler.toml` | 무료 tier 설정. ai-proxy(top-level) + jobqueue(`[env.jobqueue]`). |

## 배포 (무료 tier)
```bash
cd web/worker
npx wrangler secret put GEMINI_KEY        # 키는 secret 으로만 — 저장소/번들 금지
npx wrangler deploy --env=""              # ai-proxy
npx wrangler deploy --env jobqueue        # jobqueue(선택, KV 바인딩 후)
```

## 검증 (DoD)
- `npx wrangler deploy --dry-run --env=""` → EXIT 0 (ai-proxy)
- `npx wrangler deploy --dry-run --env jobqueue` → EXIT 0
- 키 비노출: 리터럴 키 0, 키는 `env.GEMINI_KEY`(secret)로만 참조.

## 클라이언트 연동
`web/public/app.js`의 AI 추천은 현재 규칙 기반(오프라인). ai-proxy 배포 후
`POST {proxy}/plan` 으로 `ai_planner.build_request()` 산출을 보내면 Gemini 응답으로 보강된다.
프록시 503(키 미구성) 시 규칙 기반으로 강등한다.
