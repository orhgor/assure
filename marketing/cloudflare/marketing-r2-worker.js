/**
 * Marketing from R2; /app and /api hit EC2 via Tunnel (no Worker routes on those paths).
 */
const HTML = "text/html; charset=utf-8";

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

/** Map URL path to R2 object key (mirrors dist/ layout). */
function objectKey(pathname) {
  let p = pathname;
  if (p.endsWith("/") && p.length > 1) {
    p = p.slice(0, -1);
  }
  if (!p || p === "/") {
    return "index.html";
  }
  if (p.startsWith("/")) {
    p = p.slice(1);
  }
  if (/\.[a-z0-9]+$/i.test(p)) {
    return p;
  }
  return `${p}/index.html`;
}

const APP_ORIGIN = "https://app.getassureai.com";
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

  const subject =
    "Enterprise demo request" +
    (company ? " — " + company : "") +
    " — " + name;
  const lines = [
    "Name: " + name,
    "Work email: " + email,
    company ? "Company: " + company : null,
    "",
    "Demo scope:",
    message || "(not provided)",
  ].filter((line) => line !== null);

  const send = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      "Authorization": "Bearer " + env.RESEND_API_KEY,
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

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/contact-submit") {
      return handleContactSubmit(request, env);
    }
    if (url.pathname === "/contact" || url.pathname === "/contact/") {
      return Response.redirect(new URL("/contact-us/", url.origin).toString(), 308);
    }
    if (url.pathname.startsWith("/app") || url.pathname.startsWith("/api")) {
      const target = new URL(url.pathname + url.search, APP_ORIGIN);
      return Response.redirect(target.toString(), 302);
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", { status: 405 });
    }

    const key = objectKey(url.pathname);
    let object = await env.MARKETING.get(key);
    if (!object && !key.endsWith("/index.html")) {
      object = await env.MARKETING.get(`${key}/index.html`);
    }

    if (!object) {
      return new Response("Not found", { status: 404, headers: { "Cache-Control": "no-store" } });
    }

    const headers = new Headers();
    object.writeHttpMetadata(headers);
    headers.set("content-type", contentType(key));
    headers.set("Cache-Control", "public, max-age=300");
    headers.set("Access-Control-Allow-Origin", "*");

    if (request.method === "HEAD") {
      return new Response(null, { status: 200, headers });
    }
    return new Response(object.body, { headers });
  },
};
