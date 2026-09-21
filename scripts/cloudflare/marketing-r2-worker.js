/**
 * Marketing from R2; /app and /api hit EC2 via Tunnel (no Worker routes on those paths).
 */
const HTML = "text/html; charset=utf-8";

function contentType(key) {
  if (key.endsWith(".html")) return HTML;
  if (key.endsWith(".css")) return "text/css; charset=utf-8";
  if (key.endsWith(".js")) return "application/javascript; charset=utf-8";
  if (key.endsWith(".svg")) return "image/svg+xml";
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

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
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
      return new Response("Not found", { status: 404 });
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
