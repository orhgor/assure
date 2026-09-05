/** Legacy Worker — apex is EC2 via Tunnel. Redirect workers.dev hits to canonical host. */
const CANONICAL_ORIGIN = "https://getassureai.com";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const target = new URL(url.pathname + url.search, CANONICAL_ORIGIN);
    return Response.redirect(target.toString(), 301);
  },
};
