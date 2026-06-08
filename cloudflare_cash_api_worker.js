const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, PUT, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: JSON_HEADERS,
  });
}

function unauthorized() {
  return jsonResponse({ ok: false, error: "unauthorized" }, 401);
}

function bearerToken(request) {
  const header = request.headers.get("Authorization") || "";
  const match = header.match(/^Bearer\s+(.+)$/i);
  return match ? match[1] : "";
}

async function expectedPushToken(env) {
  return (await env.CASH_DIFF_KV.get("cash:push_token")) || env.CASH_PUSH_TOKEN;
}

function publicPayload(raw) {
  if (!raw) {
    return { ok: false, error: "cash snapshot not found" };
  }
  const payload = JSON.parse(raw);
  return {
    ok: true,
    generated_at: payload.generated_at,
    source: payload.source || "6kaweb",
    latest_id: payload.latest_id,
    latest_cash_record_time: payload.latest_cash_record_time,
    usable_total_amount: payload.usable_total_amount || 0,
    denominations: payload.denominations || {},
    cashbox: payload.cashbox || {},
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: JSON_HEADERS });
    }

    if (url.pathname === "/api/cash/current" && request.method === "GET") {
      const raw = await env.CASH_DIFF_KV.get("cash:current");
      return jsonResponse(publicPayload(raw));
    }

    if (url.pathname === "/api/cash/current" && (request.method === "PUT" || request.method === "POST")) {
      const expected = await expectedPushToken(env);
      if (!expected || bearerToken(request) !== expected) {
        return unauthorized();
      }
      const payload = await request.json();
      const stored = {
        generated_at: new Date().toISOString(),
        source: payload.source || "6kaweb",
        latest_id: payload.latest_id || null,
        latest_cash_record_time: payload.latest_cash_record_time || null,
        usable_total_amount: Number(payload.usable_total_amount || 0),
        denominations: payload.denominations || {},
        cashbox: payload.cashbox || {},
      };
      await env.CASH_DIFF_KV.put("cash:current", JSON.stringify(stored));
      return jsonResponse({ ok: true, stored });
    }

    return jsonResponse({ ok: false, error: "not found" }, 404);
  },
};
