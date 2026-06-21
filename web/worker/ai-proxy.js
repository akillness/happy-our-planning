/**
 * ai-proxy — Cloudflare Worker (C7 · 선택적 엣지 가속 계층).
 *
 * 목적: Gemini(Google AI Studio) 키를 서버측(Worker secret)에 보관하고,
 * 브라우저는 키 없이 이 프록시에 요청한다. 키는 클라이언트 번들에 절대 노출되지 않는다.
 *
 * 계약:
 *   POST /plan  { "request": <ai_planner.build_request() 산출 JSON> }
 *   → Gemini generateContent 응답을 그대로 중계(JSON).
 *
 * 무료/벤더 종속 최소화: Worker는 선택 계층이다. 키 미설정 시 503을 반환해
 * 클라이언트가 규칙 기반 폴백(app.js)으로 자연히 강등되게 한다(C1·C2 flat-file SSOT 유지).
 *
 * 환경 변수(secret): GEMINI_KEY (필수), GEMINI_MODEL (선택, 기본 gemini-1.5-flash).
 */

const ALLOWED_ORIGIN = "*"; // 정적 호스팅 도메인으로 좁히는 것을 권장.

function cors(extra = {}) {
  return {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    ...extra,
  };
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: cors({ "Content-Type": "application/json; charset=utf-8" }),
  });
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors() });
    }
    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/plan") {
      return json({ error: "POST /plan 만 지원" }, 404);
    }
    if (!env.GEMINI_KEY) {
      // 키 미설정 → 클라이언트가 규칙 기반 폴백으로 강등.
      return json({ error: "ai-proxy 미구성 — 규칙 기반 폴백 사용", fallback: true }, 503);
    }

    let payload;
    try {
      payload = await request.json();
    } catch (_) {
      return json({ error: "JSON 본문 필요" }, 400);
    }
    const geminiRequest = payload && payload.request;
    if (!geminiRequest || typeof geminiRequest !== "object") {
      return json({ error: "`request` 필드(ai_planner.build_request 산출)가 필요" }, 400);
    }

    const model = env.GEMINI_MODEL || "gemini-1.5-flash";
    const endpoint =
      `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`;

    const upstream = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-goog-api-key": env.GEMINI_KEY, // 키는 서버측에만 존재.
      },
      body: JSON.stringify(geminiRequest),
    });

    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: cors({ "Content-Type": "application/json; charset=utf-8" }),
    });
  },
};
