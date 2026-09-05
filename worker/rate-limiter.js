/**
 * Simple KV-backed rate limiter for Worker endpoints.
 */

export async function checkRateLimit(env, key, limit, windowSeconds) {
  if (!env.RATE_LIMIT) {
    return true;
  }
  const now = Math.floor(Date.now() / 1000);
  const windowKey = `${key}:${Math.floor(now / windowSeconds)}`;
  const count = (await env.RATE_LIMIT.get(windowKey, "json")) || 0;
  if (count >= limit) {
    return false;
  }
  await env.RATE_LIMIT.put(windowKey, count + 1, { expirationTtl: windowSeconds + 1 });
  return true;
}

export function clientIp(request) {
  return (
    request.headers.get("CF-Connecting-IP") ||
    request.headers.get("X-Forwarded-For")?.split(",")[0]?.trim() ||
    "unknown"
  );
}

export function rateLimitResponse(retryAfterSec = 60) {
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
    },
  );
}
