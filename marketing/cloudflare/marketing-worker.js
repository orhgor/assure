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
  "script-src 'self' https://www.googletagmanager.com https://static.cloudflareinsights.com",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self'",
  "img-src 'self' data: https:",
  "connect-src 'self' https://api.resend.com https://*.google-analytics.com https://*.analytics.google.com https://*.googletagmanager.com https://cloudflareinsights.com",
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

// --- Endpoint abuse control -------------------------------------------------
// Best-effort per-IP fixed-window rate limiting for the POST endpoints.
// Counters live per Worker isolate: they reset on deploy or isolate eviction
// and are counted independently per Cloudflare PoP. Durable domain-wide
// limits belong in a Cloudflare WAF rate-limiting rule (dashboard-managed).
const RATE_LIMITS = {
  "/contact-submit": { limit: 5, windowMs: 10 * 60 * 1000 },
  "/api/z3-demo": { limit: 30, windowMs: 60 * 1000 },
};
const rateBuckets = new Map();

function rateLimitCheck(pathname, ip) {
  const rule = RATE_LIMITS[pathname];
  if (!rule) return null;
  const now = Date.now();
  const key = pathname + "|" + ip;
  const bucket = rateBuckets.get(key);
  if (!bucket || now - bucket.start >= rule.windowMs) {
    if (rateBuckets.size > 5000) {
      for (const [k, b] of rateBuckets) {
        if (now - b.start >= rule.windowMs) rateBuckets.delete(k);
      }
    }
    rateBuckets.set(key, { start: now, count: 1 });
    return null;
  }
  bucket.count += 1;
  if (bucket.count > rule.limit) {
    return Math.max(1, Math.ceil((bucket.start + rule.windowMs - now) / 1000));
  }
  return null;
}

function tooManyRequests(retryAfter, origin) {
  const headers = {
    "content-type": "application/json; charset=utf-8",
    "Retry-After": String(retryAfter),
  };
  for (const [name, value] of Object.entries(corsHeaders(origin))) {
    headers[name] = value;
  }
  return new Response(
    JSON.stringify({ error: "Too many requests. Please try again later." }),
    { status: 429, headers }
  );
}
// ----------------------------------------------------------------------------

// Locale folders in dist/ (mirrors marketing/locales; en lives at the root).
const LOCALES = ["de", "es", "fr", "tr", "ja", "zh", "ar"];

const SALES_INBOX = "sales@assureproof.com";
const EMAIL_FROM = "AssureProof Website <website@assureproof.com>";

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

/** POST /api/z3-demo — replays the illustrative hero example (simple subtraction, not a Z3 run). */
async function handleZ3Demo(request, env) {
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
      ? `Mismatch marked: the invoice is ${delta > 0 ? "+" : ""}${delta.toLocaleString()} ${currency} over the contract cap. A person reviews and decides.`
      : "No mismatch: the two figures agree.",
    z3Verification: "Illustrative example: simulated data, not a Z3 run.",
  });
}

/** POST /contact-submit — enterprise contact form → Resend email. */
async function handleContactSubmit(request, env) {
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
function withSecurityHeaders(response, origin = "", request = null) {
  const headers = new Headers(response.headers);
  // Versioned static assets (?v=...) are content-addressed: cache immutably.
  // Unversioned static assets (fonts, og image) get a long-but-revalidating TTL.
  // Everything else (HTML) stays short so deploys land quickly.
  let cacheControl = response.status === 404 ? "no-store" : "public, max-age=300";
  const path = request ? new URL(request.url).pathname : "";
  if (response.status !== 404 && path.startsWith("/static/")) {
    cacheControl = request.url.includes("?v=")
      ? "public, max-age=31536000, immutable"
      : "public, max-age=604800";
  }
  headers.set("Cache-Control", cacheControl);
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
    const clientIp = request.headers.get("CF-Connecting-IP") || "unknown";


    // CORS preflight
    if (request.method === "OPTIONS") {
      return handleOptions(request);
    }

    // Contact form → Resend email to the sales inbox.
    if (request.method === "POST" && url.pathname === "/contact-submit") {
      const retryAfter = rateLimitCheck("/contact-submit", clientIp);
      if (retryAfter !== null) {
        return tooManyRequests(retryAfter, origin);
      }
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
      const retryAfter = rateLimitCheck("/api/z3-demo", clientIp);
      if (retryAfter !== null) {
        return tooManyRequests(retryAfter, origin);
      }
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

    // Legacy domain: getassureai.com (and www) → assureproof.com, one 301 hop
    // that also upgrades http→https. Mirrors the dashboard redirect rule so the
    // consolidation survives even if the zone-level rule is ever removed.
    if (url.hostname === "getassureai.com" || url.hostname === "www.getassureai.com") {
      const consolidated = new URL("https://assureproof.com" + url.pathname + url.search);
      return Response.redirect(consolidated.toString(), 301);
    }

    // One host: www.assureproof.com → assureproof.com, path and query kept.
    if (url.hostname === "www.assureproof.com") {
      const apex = new URL(url.toString());
      apex.hostname = "assureproof.com";
      return Response.redirect(apex.toString(), 301);
    }

    // Always HTTPS: http:// → https://, path and query kept (WP1).
    if (url.protocol === "http:") {
      const secure = new URL(url.toString());
      secure.protocol = "https:";
      return Response.redirect(secure.toString(), 301);
    }

    // The fuel-retail page moved to a neutral URL (all locales).
    const fuel = url.pathname.match(/^(\/[a-z]{2})?\/use-cases\/fuel-retail\/?$/i);
    if (fuel) {
      return Response.redirect(new URL(`${fuel[1] || ""}/use-cases/bonus-reconciliation/`, url.origin).toString(), 301);
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
      // Apply the same cache policy to HEAD responses (mirrors withSecurityHeaders)
      let cacheControl = res.status === 404 ? "no-store" : "public, max-age=300";
      const path = new URL(request.url).pathname;
      if (res.status !== 404 && path.startsWith("/static/")) {
        cacheControl = request.url.includes("?v=")
          ? "public, max-age=31536000, immutable"
          : "public, max-age=604800";
      }
      headers.set("Cache-Control", cacheControl);
      return new Response(null, { status: res.status, headers });
    }

    return withSecurityHeaders(res, origin, request);
  },
};