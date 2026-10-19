/**
 * Marketing from R2 (final-copy site, Oct 2026).
 *  - /app, /api (except /api/contact) and workbench routes redirect to app host
 *  - retired pages 301 to new homes; /path/ to /path; ?lang= stripped
 *  - GET /health public; /internal/health token-gated
 *  - POST /api/contact (JSON or form) -> R2 storage + Resend notification
 *  - security headers incl. CSP-Report-Only with per-request nonce
 */
const HTML = "text/html; charset=utf-8";

const APP_ORIGIN = "https://app.getassureai.com";
const NOTIFY_INBOX = "hello@getassureai.com";
const EMAIL_FROM = "Assure Website <hello@getassureai.com>";
const TOPICS = [
  "Demo request",
  "Contract and invoice checks",
  "Claims",
  "Aviation or manufacturing",
  "Regulatory or audit",
  "Security review",
  "Investor inquiry",
  "Other",
];
const WORKBENCH_PREFIXES = ["/signup", "/workbench", "/history", "/library", "/connect", "/app", "/api"];

const RETIRED = {
  "/about": "/company",
  "/architecture": "/features/parsure",
  "/contact-us": "/contact",
  "/pricing": "/contact",
};

function contentType(key) {
  if (key.endsWith(".html")) return HTML;
  if (key.endsWith(".css")) return "text/css; charset=utf-8";
  if (key.endsWith(".js")) return "application/javascript; charset=utf-8";
  if (key.endsWith(".svg")) return "image/svg+xml";
  if (key.endsWith(".png")) return "image/png";
  if (key.endsWith(".jpg") || key.endsWith(".jpeg")) return "image/jpeg";
  if (key.endsWith(".webp")) return "image/webp";
  if (key.endsWith(".xml")) return "application/xml";
  if (key.endsWith(".ico")) return "image/x-icon";
  if (key.endsWith(".txt")) return "text/plain; charset=utf-8";
  return "application/octet-stream";
}

function json(data, status) {
  return new Response(JSON.stringify(data), {
    status: status || 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-robots-tag": "noindex, nofollow",
    },
  });
}

function baseHeaders(ct) {
  return {
    "content-type": ct,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Strict-Transport-Security": "max-age=31536000",
  };
}

function objectKey(pathname) {
  let p = pathname;
  if (p.endsWith("/") && p.length > 1) p = p.slice(0, -1);
  if (!p || p === "/") return "index.html";
  if (p.startsWith("/")) p = p.slice(1);
  if (/\.[a-z0-9]+$/i.test(p)) return p;
  return `${p}/index.html`;
}

async function getAsset(env, key) {
  let object = await env.MARKETING.get(key);
  if (!object && !key.endsWith("/index.html") && !key.endsWith(".html")) {
    object = await env.MARKETING.get(`${key}/index.html`);
  }
  return object;
}

function nonceCSP(nonce) {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' https://plausible.io`,
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self' https://plausible.io " + APP_ORIGIN,
    "form-action 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "object-src 'none'",
  ].join("; ");
}

function processHTML(html, nonce, injectFormGuards) {
  let out = html.replace(/<script(?![^>]*nonce=)([^>]*)>/g, `<script nonce="${nonce}"$1>`);
  if (injectFormGuards && out.includes("<form")) {
    const ts = String(Date.now());
    const guards =
      `<input type="text" name="website" value="" class="hp-field" aria-hidden="true" tabindex="-1" autocomplete="off">` +
      `<input type="hidden" name="render_ts" value="${ts}">`;
    if (out.includes('name="website"')) {
      out = out.replace(/name="render_ts" value="[^"]*"/, `name="render_ts" value="${ts}"`);
    } else {
      out = out.replace("<\/form>", guards + "<\/form>");
    }
  }
  return out;
}

async function resolvePath(url, env) {
  const pathname = url.pathname;

  if (pathname.length > 1 && pathname.endsWith("/")) {
    return Response.redirect(new URL(pathname.slice(0, -1) + (url.search || ""), url.origin).toString(), 301);
  }

  if (url.searchParams.has("lang")) {
    const clean = new URL(url.origin + pathname);
    url.searchParams.delete("lang");
    url.searchParams.forEach((v, k) => clean.searchParams.set(k, v));
    return Response.redirect(clean.toString(), 301);
  }

  const localeMatch = pathname.match(/^\/(de|es|fr|ja|tr|zh)(\/.*)?$/);
  if (localeMatch) {
    const rest = localeMatch[2] || "/";
    if (await getAsset(env, objectKey(rest))) {
      return Response.redirect(new URL(rest, url.origin).toString(), 301);
    }
    return Response.redirect(new URL("/", url.origin).toString(), 301);
  }

  if (RETIRED[pathname]) {
    return Response.redirect(new URL(RETIRED[pathname], url.origin).toString(), 301);
  }

  if (WORKBENCH_PREFIXES.some((pre) => pathname === pre || pathname.startsWith(pre + "/"))) {
    const target = new URL(pathname + url.search, APP_ORIGIN);
    return Response.redirect(target.toString(), 301);
  }

  return null;
}

async function handleContact(request, env) {
  const isJSON = (request.headers.get("content-type") || "").includes("application/json");
  let data = {};
  if (isJSON) {
    try { data = await request.json(); } catch (err) { return json({ error: "Invalid request body." }, 400); }
  } else {
    const form = await request.formData();
    for (const [k, v] of form.entries()) data[k] = v;
  }

  if (String(data.website || "").trim() !== "") return json({ status: "ok" });

  const ts = Number(data.render_ts || 0);
  if (!ts || Date.now() - ts < 2000) {
    return json({ error: "Your request could not be sent. Please try again, or email hello@getassureai.com." }, 400);
  }

  const name = String(data.name || "").trim().slice(0, 120);
  const email = String(data.email || "").trim().slice(0, 254);
  const company = String(data.company || "").trim().slice(0, 160);
  const role = String(data.role || "").trim().slice(0, 120);
  const topic = String(data.topic || "").trim();
  const message = String(data.message || "").trim().slice(0, 2000);
  const consent = data.consent === true || data.consent === "on" || data.consent === "true";

  if (!name) return json({ error: "Name is required." }, 400);
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return json({ error: "Enter a valid work email address." }, 400);
  if (!company) return json({ error: "Company is required." }, 400);
  if (!TOPICS.some((t) => t === topic)) return json({ error: "Choose a topic." }, 400);
  if (!consent) return json({ error: "Consent is required." }, 400);

  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const salt = env.RATE_LIMIT_SALT || "assure";
  const hashBuf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(salt + ip));
  const ipHash = [...new Uint8Array(hashBuf)].slice(0, 16).map((b) => b.toString(16).padStart(2, "0")).join("");
  const window = Math.floor(Date.now() / 600000);
  const counterKey = `ratelimit/${window}/${ipHash}`;
  const counter = await env.MARKETING.get(counterKey);
  const count = counter ? parseInt(counter, 10) : 0;
  if (count >= 5) {
    return json({ error: "Your request could not be sent. Please try again, or email hello@getassureai.com." }, 429);
  }
  await env.MARKETING.put(counterKey, String(count + 1), { expirationTtl: 700 });

  const now = new Date().toISOString();
  const record = { received_at: now, name, email, company, role, topic, message, consent: true, ip_hash: ipHash };
  let stored = false;
  try {
    const id = crypto.randomUUID();
    await env.MARKETING.put(`submissions/${now.replace(/[:.]/g, "-")}-${id}.json`, JSON.stringify(record, null, 2));
    stored = true;
  } catch (err) { /* storage failure: mail may still succeed */ }

  if (env.RESEND_API_KEY) {
    const send = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Authorization": "Bearer " + env.RESEND_API_KEY,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from: EMAIL_FROM,
        to: [NOTIFY_INBOX],
        reply_to: email,
        subject: "Request a demo - " + topic + (company ? " - " + company : "") + " - " + name,
        text: [
          "Name: " + name,
          "Work email: " + email,
          company ? "Company: " + company : null,
          role ? "Role: " + role : null,
          "Topic: " + topic,
          "",
          "What should the demo cover:",
          message || "(not provided)",
          "",
          stored ? "(stored in R2 submissions/)" : "(storage failed - record only in this email)",
        ].filter((l) => l !== null).join("\n"),
      }),
    });
    if (!send.ok && !stored) {
      return json({ error: "Your request could not be sent. Please try again, or email hello@getassureai.com." }, 502);
    }
  } else if (!stored) {
    return json({ error: "Your request could not be sent. Please try again, or email hello@getassureai.com." }, 503);
  }

  return json({ status: "ok" });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const pathname = url.pathname;

    const isContactPost = request.method === "POST" &&
      (pathname === "/contact" || pathname === "/api/contact" || pathname === "/contact-submit");
    if (request.method !== "GET" && request.method !== "HEAD" && !isContactPost) {
      return new Response("Method not allowed", { status: 405 });
    }
    if (isContactPost) return handleContact(request, env);

    if (pathname === "/health") return json({ status: "ok" });
    if (pathname === "/internal/health") {
      const token = request.headers.get("x-internal-token");
      if (!env.INTERNAL_HEALTH_TOKEN || !token || token !== env.INTERNAL_HEALTH_TOKEN) {
        return json({ error: "Not found" }, 404);
      }
      return json({ status: "ok", worker: "marketing-r2", ts: Date.now() });
    }

    const redirect = await resolvePath(url, env);
    if (redirect) return redirect;

    if (request.method === "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    const nonce = crypto.randomUUID().replace(/-/g, "");
    const isContactPage = pathname === "/contact";
    const key = objectKey(pathname);
    const object = await getAsset(env, key);

    const headers = baseHeaders(contentType(key));
    headers["Cache-Control"] = isContactPage ? "no-store" : "public, max-age=300";
    headers["Content-Security-Policy-Report-Only"] = nonceCSP(nonce);

    if (!object) {
      const notFound = await getAsset(env, "404.html");
      headers.set("content-type", HTML);
      headers.set("Cache-Control", "no-store");
      headers.set("X-Robots-Tag", "noindex, nofollow");
      if (notFound) {
        const nf = processHTML(await notFound.text(), nonce, false);
        return new Response(nf, { status: 404, headers });
      }
      return new Response("Not found", { status: 404, headers });
    }

    if (pathname === "/robots.txt" || pathname === "/sitemap.xml" || pathname === "/healthz") {
      headers.set("Cache-Control", "no-store");
    }

    if (request.method === "HEAD") {
      return new Response(null, { status: 200, headers });
    }

    let body = await object.text();
    if (contentType(key) === HTML) {
      body = processHTML(body, nonce, isContactPage);
    }

    return new Response(body, { headers });
  },
};
