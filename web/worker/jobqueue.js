/**
 * jobqueue — Cloudflare Worker (C7 · 매크로 잡 큐 stub).
 *
 * 신청 매크로(scripts/macro/runner.py)는 브라우저 자동화라 엣지에서 직접 실행하지 않는다.
 * 이 Worker는 잡 접수/상태 조회만 담당하는 얇은 큐 어댑터다(실행은 외부 러너가 폴링).
 *
 * 영속화: Cloudflare KV(JOBS 바인딩)가 있으면 사용, 없으면 메모리(휘발) 폴백.
 * flat-file SSOT(C2)를 깨지 않도록, 잡 큐는 일시적 실행 상태만 담는다(이벤트 DB가 아님).
 *
 * 계약:
 *   POST /jobs        { job: <apply.plan_job() 산출> }      → { id, state: "queued" }
 *   GET  /jobs/:id                                          → { id, state, result? }
 *   POST /jobs/:id/result { submitted, result_text }        → { id, state: "result" }
 */

const mem = new Map(); // KV 미바인딩 시 휘발 폴백.

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

async function put(env, id, value) {
  if (env.JOBS) await env.JOBS.put(id, JSON.stringify(value));
  else mem.set(id, value);
}

async function get(env, id) {
  if (env.JOBS) {
    const raw = await env.JOBS.get(id);
    return raw ? JSON.parse(raw) : null;
  }
  return mem.get(id) || null;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean); // ["jobs", ":id", "result"?]

    if (request.method === "POST" && parts.length === 1 && parts[0] === "jobs") {
      const body = await request.json().catch(() => ({}));
      if (!body.job) return json({ error: "`job` 필요" }, 400);
      const id = crypto.randomUUID();
      const record = { id, state: "queued", job: body.job, result: null };
      await put(env, id, record);
      return json({ id, state: "queued" }, 201);
    }

    if (parts[0] === "jobs" && parts[1]) {
      const id = parts[1];
      const record = await get(env, id);
      if (!record) return json({ error: "잡 없음" }, 404);

      if (request.method === "GET" && parts.length === 2) {
        return json({ id, state: record.state, result: record.result });
      }
      if (request.method === "POST" && parts[2] === "result") {
        const body = await request.json().catch(() => ({}));
        record.state = "result";
        record.result = { submitted: !!body.submitted, result_text: body.result_text || "" };
        await put(env, id, record);
        return json({ id, state: "result" });
      }
    }

    return json({ error: "지원하지 않는 경로" }, 404);
  },
};
