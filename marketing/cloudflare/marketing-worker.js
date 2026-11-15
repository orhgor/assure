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
const SECURITY_HEADERS = {
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
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
  return json({ status: "ok" });
}

/** Locale folder for a path like /de/..., or "" for the root site. */
function pathLocale(pathname) {
  const seg = pathname.split("/")[1] || "";
  return LOCALES.includes(seg) ? seg : "";
}

/** Add the security headers + cache policy to an asset or 404 response. */
function withSecurityHeaders(response) {
  const headers = new Headers(response.headers);
  headers.set("Cache-Control", response.status === 404 ? "no-store" : "public, max-age=300");
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
    headers.set(name, value);
  }
  return new Response(response.body, { status: response.status, headers });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Contact form → Resend email to the sales inbox.
    if (request.method === "POST" && url.pathname === "/contact-submit") {
      return handleContactSubmit(request, env);
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
      for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
        headers.set(name, value);
      }
      return new Response(null, { status: res.status, headers });
    }

    return withSecurityHeaders(res);
  },
};

