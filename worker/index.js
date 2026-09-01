const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function json(status, body, extra) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...(extra || {}),
    },
  });
}

function corsHeaders(request) {
  return {
    "access-control-allow-origin": new URL(request.url).origin,
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-allow-headers": "content-type",
  };
}

async function waitlist(request, env) {
  const headers = corsHeaders(request);
  const url = String(env.SUPABASE_URL || "").replace(/\/$/, "");
  const key = String(
    env.SUPABASE_SECRET_KEY || env.SUPABASE_SERVICE_ROLE_KEY || ""
  ).trim();
  if (!url || !key) {
    return json(
      503,
      { error: "The waitlist is not open yet. Try again later." },
      headers
    );
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json(400, { error: "Send a name and email." }, headers);
  }

  const name = String((body && body.name) || "").trim();
  const email = String((body && body.email) || "").trim();
  if (!name) return json(400, { error: "Enter a name." }, headers);
  if (!EMAIL_RE.test(email)) {
    return json(400, { error: "Enter a valid email." }, headers);
  }

  const res = await fetch(url + "/rest/v1/waitlist", {
    method: "POST",
    headers: {
      apikey: key,
      Authorization: "Bearer " + key,
      "Content-Type": "application/json",
      Prefer: "return=minimal",
    },
    body: JSON.stringify({ name, email }),
  });

  if (res.status === 409 || res.ok) {
    return json(200, { status: "ok" }, headers);
  }
  return json(
    503,
    { error: "Could not save to the waitlist right now. Try again." },
    headers
  );
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/waitlist") {
      if (request.method === "OPTIONS") {
        return new Response(null, {
          status: 204,
          headers: corsHeaders(request),
        });
      }
      if (request.method !== "POST") {
        return json(405, { error: "Use POST." }, corsHeaders(request));
      }
      return waitlist(request, env);
    }
    return env.ASSETS.fetch(request);
  },
};
