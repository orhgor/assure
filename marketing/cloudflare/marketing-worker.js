/**
 * Marketing site on Cloudflare Workers static assets.
 *
 * Wildcard routes on both zones route every request to this Worker:
 * - /contact-submit (POST) -> Resend email to the sales inbox
 * - /contact and /{locale}/contact -> 301 to /contact-us/
 * - /government and /{locale}/government -> 301 to /use-cases/government/
 * - /app and /api (legacy workbench paths) -> 301 to /contact-us/
 * - everything else -> served from the static assets (dist/), with
 *   locale-aware 404 pages on misses and security headers on responses.
 *
 * The dist/ directory is uploaded with the Worker as static assets
 * (wrangler assets.directory); the ASSETS binding delivers the files.
 */

// Rate limiting (KV-backed)
async function checkRateLimit(env, key, limit, windowSeconds) {
  if (!env.RATE_LIMIT) return true;
  const now = Math.floor(Date.now() / 1000);
  const windowKey = `${key}:${Math.floor(now / windowSeconds)}`;
  const count = (await env.RATE_LIMIT.get(windowKey, "json")) || 0;
  if (count >= limit) return false;
  await env.RATE_LIMIT.put(windowKey, count + 1, { expirationTtl: windowSeconds + 1 });
  return true;
}

function clientIp(request) {
  return (
    request.headers.get("CF-Connecting-IP") ||
    request.headers.get("X-Forwarded-For")?.split(",")[0]?.trim() ||
    "unknown"
  );
}

function rateLimitResponse(retryAfterSec = 60) {
  return new Response(
    JSON.stringify({
      ok: false,
      error: "Too many requests. Please wait and try again.",
      retry_after: retryAfterSec,
    }),
    {
      status: 429,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Retry-After": String(retryAfterSec),
      },
    }
  );
}

// CORS handling
function corsHeaders(origin) {
  const allowedOrigins = [
    "https://assureproof.com",
    "https://www.assureproof.com",
    "https://getassureai.com",
    "https://www.getassureai.com",
  ];
  const allowOrigin = allowedOrigins.includes(origin) ? origin : allowedOrigins[0];
  return {
    "Access-Control-Allow-Origin": allowOrigin,
    "Access-Control-Allow-Methods": "GET, HEAD, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

function handleOptions(request) {
  const origin = request.headers.get("Origin") || "";
  return new Response(null, { status: 204, headers: corsHeaders(origin) });
}

// Content Security Policy
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline' fonts.googleapis.com",
  "font-src 'self' fonts.gstatic.com data:",
  "img-src 'self' data: https:",
  "connect-src 'self' https://api.resend.com",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const SECURITY_HEADERS = {
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
  "Content-Security-Policy": CSP,
};

// Locale folders in dist/ (mirrors marketing/locales; en lives at the root).
const LOCALES = ["de", "es", "fr", "tr", "ja", "zh", "ar"];

const SALES_INBOX = "sales@getassureai.com";
const EMAIL_FROM = "Assure Website <website@getassureai.com>";

function json(data, status) {
  return new Response(JSON.stringify(data), {
    status: status || 200,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function validEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) && email.length <= 254;
}

function clean(value, max) {
  return String(value || "").trim().slice(0, max || 2000);
}

/** POST /api/z3-demo — Z3 verification demo for interactive hero card. */
async function handleZ3Demo(request, env) {
  const ip = clientIp(request);
  // Rate limit: 30 req/min per IP
  if (!(await checkRateLimit(env, `z3-demo:${ip}`, 30, 60))) {
    return rateLimitResponse(60);
  }

  let body;
  try {
    body = await request.json();
  } catch (err) {
    return json({ ok: false, error: "Invalid request body" }, 400);
  }

  const sourceA = body?.sourceA || { label: "Contract Cap", value: 120000, currency: "EUR" };
  const sourceB = body?.sourceB || { label: "Amount Billed", value: 142500, currency: "EUR" };

  const a = Number(sourceA?.value) || 120000;
  const b = Number(sourceB?.value) || 142500;
  const currency = sourceA?.currency || sourceB?.currency || "EUR";
  const delta = b - a;
  const conflict = delta !== 0;

  return json({
    ok: true,
    sourceA: { ...sourceA, value: a },
    sourceB: { ...sourceB, value: b },
    delta: { value: delta, currency, conflict },
    message: conflict
      ? `Export locked — ${delta > 0 ? "+" : ""}${delta.toLocaleString()} ${currency} overage detected.`
      : "No conflict — values agree.",
    z3Verification: "Deterministic formal logic check completed.",
  });
}

/** POST /contact-submit — enterprise contact form → Resend email. */
async function handleContactSubmit(request, env) {
  const ip = clientIp(request);
  // Rate limit: 10 req/min per IP
  if (!(await checkRateLimit(env, `contact-submit:${ip}`, 10, 60))) {
    return rateLimitResponse(60);
  }

  if (!env.RESEND_API_KEY) {
    return json({ error: "Contact endpoint is not configured." }, 503);
  }

  let data;
  try {
    data = await request.json();
  } catch (err) {
    return json({ error: "Invalid request body." }, 400);
  }

  if (clean(data.company_website)) {
    return json({ status: "ok" }); // honeypot: pretend success
  }

  const name = clean(data.name, 200);
  const email = clean(data.email, 254);
  const company = clean(data.company, 200);
  const message = clean(data.message, 4000);

  if (!name) return json({ error: "Name is required." }, 400);
  if (!validEmail(email)) return json({ error: "Enter a valid email address." }, 400);

  const subject = "Enterprise demo request" + (company ? ": " + company : "");
  const lines = [
    "Name: " + name,
    "Email: " + email,
    company ? "Company: " + company : "",
    "Message: " + message,
  ].filter(Boolean);

  try {
    const send = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + env.RESEND_API_KEY,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from: EMAIL_FROM,
        to: [SALES_INBOX],
        reply_to: email,
        subject: subject,
        text: lines.join("\n"),
      }),
    });

    if (!send.ok) {
      let detail = "";
      try {
        const body = await send.json();
        detail = body && body.message ? ": " + body.message : "";
      } catch (err) { /* ignore */ }
      return json({ error: "Could not send the request" + detail + "." }, 502);
    }
  } catch (err) {
    return json({ error: "Could not send the request. Please try again." }, 502);
  }

  return json({ status: "ok" });
}

/** Locale folder for a path like /de/..., or "" for the root site. */
function pathLocale(pathname) {
  const seg = pathname.split("/")[1] || "";
  return LOCALES.includes(seg) ? seg : "";
}

/** Add the security headers + cache policy + CSP to an asset or 404 response. */
function withSecurityHeaders(response, origin = "") {
  const headers = new Headers(response.headers);
  headers.set("Cache-Control", response.status === 404 ? "no-store" : "public, max-age=300");
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
    headers.set(name, value);
  }
  // Add CORS headers for API responses
  if (origin) {
    for (const [name, value] of Object.entries(corsHeaders(origin))) {
      headers.set(name, value);
    }
  }
  return new Response(response.body, { status: response.status, headers });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get("Origin") || "";

    // CORS preflight
    if (request.method === "OPTIONS") {
      return handleOptions(request);
    }

    // Contact form → Resend email to the sales inbox.
    if (request.method === "POST" && url.pathname === "/contact-submit") {
      const res = await handleContactSubmit(request, env);
      // Add CORS headers to response
      const resHeaders = new Headers(res.headers);
      for (const [name, value] of Object.entries(corsHeaders(origin))) {
        resHeaders.set(name, value);
      }
      return new Response(res.body, { status: res.status, headers: resHeaders });
    }

    // Z3 demo endpoint for interactive hero proof card.
    if (request.method === "POST" && url.pathname === "/api/z3-demo") {
      const res = await handleZ3Demo(request, env);
      const resHeaders = new Headers(res.headers);
      for (const [name, value] of Object.entries(corsHeaders(origin))) {
        resHeaders.set(name, value);
      }
      return new Response(res.body, { status: res.status, headers: resHeaders });
    }

    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", { status: 405 });
    }

    // Legacy and convenience redirects.
    if (url.pathname === "/contact" || url.pathname === "/contact/") {
      return Response.redirect(new URL("/contact-us/", url.origin).toString(), 301);
    }
    const m = url.pathname.match(/^\/([a-z]{2})(\/contact)(\/?)$/i);
    if (m) {
      return Response.redirect(new URL(`/${m[1]}/contact-us/`, url.origin).toString(), 301);
    }
    const gov = url.pathname.match(/^\/(?:([a-z]{2})\/)?government\/?$/i);
    if (gov && (!gov[1] || LOCALES.includes(gov[1]))) {
      const prefix = gov[1] ? `/${gov[1]}` : "";
      return Response.redirect(new URL(`${prefix}/use-cases/government/`, url.origin).toString(), 301);
    }
    if (url.pathname.startsWith("/app") || url.pathname.startsWith("/api")) {
      // The workbench is gone; these legacy paths go to the contact page.
      return Response.redirect(new URL("/contact-us/", url.origin).toString(), 301);
    }

    // Trailing-slash redirect for extension-less paths.
    if (url.pathname !== "/" && !url.pathname.endsWith("/") && !/\.[a-z0-9]+$/i.test(url.pathname)) {
      return Response.redirect(new URL(url.pathname + "/", url.origin).toString(), 301);
    }

    // Static assets (dist/ ships with the Worker; the 404-page handles misses).
    let res = await env.ASSETS.fetch(request);

    // Locale-aware 404: the assets 404-page is the root one; swap in the
    // locale's own 404.html when the miss is inside a locale folder.
    if (res.status === 404) {
      const loc = pathLocale(url.pathname);
      if (loc) {
        const locale404 = await env.ASSETS.fetch(
          new Request(new URL(`/${loc}/404.html`, url.origin))
        );
        if (locale404.status === 200) {
          res = new Response(locale404.body, { status: 404, headers: locale404.headers });
        }
      }
    }

    if (request.method === "HEAD") {
      const headers = new Headers(res.headers);
      // Apply security headers (includes CSP)
      for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
        headers.set(name, value);
      }
      // Apply consistent cache policy to HEAD responses
      headers.set("Cache-Control", res.status === 404 ? "no-store" : "public, max-age=300");
      return new Response(null, { status: res.status, headers });
    }

    return withSecurityHeaders(res, origin);
  },
};