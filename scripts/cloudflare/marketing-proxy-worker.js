/**
 * Marketing edge router: static site from Pages; workbench/API on app subdomain.
 * Worker routes on getassureai.com run before Cloudflare Tunnel.
 */
const PAGES_ORIGIN = "https://assure-marketing.pages.dev";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const target = new URL(url.pathname + url.search, PAGES_ORIGIN);
    return fetch(target.toString(), {
      method: request.method,
      headers: request.headers,
      body: request.method !== "GET" && request.method !== "HEAD" ? request.body : undefined,
    });
  },
};
