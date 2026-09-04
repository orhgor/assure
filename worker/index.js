/**
 * Edge PDF processing — R2 upload, unpdf extraction, Textract fallback, EC2 ingest.
 */
import { AwsClient } from "aws4fetch";
import { extractText, getDocumentProxy } from "unpdf";
import { checkRateLimit, clientIp, rateLimitResponse } from "./rate-limiter.js";

const JSON_HEADERS = { "Content-Type": "application/json; charset=utf-8" };

const MAX_UPLOADS_PROJECT_DAY = 20;
const IP_RATE_LIMIT = 20;
const IP_RATE_WINDOW_SEC = 60;

async function enforceIpRateLimit(request, env, route) {
  const ip = clientIp(request);
  const ok = await checkRateLimit(env, `ip:${route}:${ip}`, IP_RATE_LIMIT, IP_RATE_WINDOW_SEC);
  if (!ok) {
    return rateLimitResponse(IP_RATE_WINDOW_SEC);
  }
  return null;
}

async function enforceProjectUploadLimit(env, projectId) {
  const usage = await readUsage(env, projectId);
  const uploads = usage.projectObj.uploads || 0;
  if (uploads >= MAX_UPLOADS_PROJECT_DAY) {
    return json(
      {
        ok: false,
        error: `Daily upload limit reached (${MAX_UPLOADS_PROJECT_DAY} per project).`,
      },
      429,
      null,
    );
  }
  return null;
}

function intEnv(env, key, fallback) {
  const raw = env[key];
  if (raw == null || raw === "") return fallback;
  const n = Number.parseInt(String(raw), 10);
  return Number.isFinite(n) ? n : fallback;
}

function todayKey() {
  return new Date().toISOString().slice(0, 10);
}

async function readUsage(env, projectId) {
  const dayKey = `_usage/daily/${todayKey()}.json`;
  const projectKey = `_usage/projects/${projectId}.json`;
  const dayObj = (await readJson(env, dayKey)) || { pages: 0, textract_pages: 0, uploads: 0 };
  const projectObj = (await readJson(env, projectKey)) || { pages: 0, uploads: 0 };
  return { dayObj, projectObj, dayKey, projectKey };
}

async function readJson(env, key) {
  const obj = await env.PDF_BUCKET.get(key);
  if (!obj) return null;
  try {
    return await obj.json();
  } catch (_) {
    return null;
  }
}

async function writeJson(env, key, value) {
  await env.PDF_BUCKET.put(key, JSON.stringify(value), {
    httpMetadata: { contentType: "application/json" },
  });
}

async function countPdfPages(bytes) {
  try {
    const pdf = await getDocumentProxy(new Uint8Array(bytes));
    return pdf.numPages || 1;
  } catch (_) {
    return 1;
  }
}

async function extractWithUnpdf(bytes) {
  const pdf = await getDocumentProxy(new Uint8Array(bytes));
  const { text } = await extractText(pdf, { mergePages: true });
  return String(text || "").trim();
}

async function textractExtract(bytes, env) {
  const region = env.AWS_REGION || "us-east-1";
  const aws = new AwsClient({
    accessKeyId: env.AWS_ACCESS_KEY_ID,
    secretAccessKey: env.AWS_SECRET_ACCESS_KEY,
    region,
    service: "textract",
  });
  const url = `https://textract.${region}.amazonaws.com/`;
  let attempt = 0;
  let waitMs = 500;
  while (attempt < 5) {
    attempt += 1;
    const res = await aws.fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "Textract.AnalyzeDocument",
      },
      body: JSON.stringify({
        Document: { Bytes: btoaString(bytes) },
        FeatureTypes: ["TABLES", "FORMS"],
      }),
    });
    if (res.ok) {
      const payload = await res.json();
      const lines = (payload.Blocks || [])
        .filter((b) => b.BlockType === "LINE" && b.Text)
        .map((b) => b.Text);
      return lines.join("\n").trim();
    }
    if (res.status === 429 || res.status >= 500) {
      await sleep(waitMs);
      waitMs *= 2;
      continue;
    }
    const errText = await res.text();
    throw new Error(`Textract failed (${res.status}): ${errText.slice(0, 200)}`);
  }
  throw new Error("Textract throttled after retries.");
}

function btoaString(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function corsHeaders(origin) {
  if (!origin) return {};
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, POST, PUT, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Assure-Worker-Secret",
    "Access-Control-Max-Age": "86400",
  };
}

async function handleUploadUrl(request, env) {
  const limited = await enforceIpRateLimit(request, env, "upload-url");
  if (limited) return limited;
  const url = new URL(request.url);
  const projectId = (url.searchParams.get("projectId") || "").trim();
  const filename = (url.searchParams.get("filename") || "upload.pdf").trim();
  if (!projectId) {
    return json({ ok: false, error: "projectId is required." }, 400, request);
  }
  if (!/\.pdf$/i.test(filename)) {
    return json({ ok: false, error: "Only PDF uploads are supported at the edge." }, 400, request);
  }
  const key = `${projectId}/${crypto.randomUUID()}-${filename.replace(/[^\w.\-]+/g, "_")}`;
  const ttl = intEnv(env, "UPLOAD_URL_TTL_SEC", 300);
  const expiresAt = Date.now() + ttl * 1000;
  const uploadUrl = `${url.origin}/upload/${encodeURIComponent(key)}?expires=${expiresAt}`;
  return json({ ok: true, key, uploadUrl, expiresAt, maxBytes: intEnv(env, "MAX_FILE_BYTES", 10 * 1024 * 1024) });
}

async function handleUploadPut(request, env, key) {
  const url = new URL(request.url);
  const expires = Number.parseInt(url.searchParams.get("expires") || "0", 10);
  if (!expires || Date.now() > expires) {
    return json({ ok: false, error: "Upload URL expired." }, 410, request);
  }
  const maxBytes = intEnv(env, "MAX_FILE_BYTES", 10 * 1024 * 1024);
  const bytes = new Uint8Array(await request.arrayBuffer());
  if (!bytes.length) {
    return json({ ok: false, error: "Empty upload." }, 400, request);
  }
  if (bytes.length > maxBytes) {
    return json({ ok: false, error: `File exceeds ${maxBytes} bytes.` }, 400, request);
  }
  await env.PDF_BUCKET.put(key, bytes, {
    httpMetadata: { contentType: "application/pdf" },
  });
  return json({ ok: true, key, bytes: bytes.length });
}

async function handleProcess(request, env) {
  const limited = await enforceIpRateLimit(request, env, "process");
  if (limited) return limited;
  const body = await request.json();
  const key = String(body.key || "").trim();
  const projectId = String(body.projectId || "").trim();
  if (!key || !projectId) {
    return json({ ok: false, error: "key and projectId are required." }, 400, request);
  }
  const uploadLimited = await enforceProjectUploadLimit(env, projectId);
  if (uploadLimited) return uploadLimited;
  if (!key.startsWith(`${projectId}/`)) {
    return json({ ok: false, error: "key does not match projectId." }, 403, request);
  }

  const obj = await env.PDF_BUCKET.get(key);
  if (!obj) {
    return json({ ok: false, error: "PDF not found in R2." }, 404, request);
  }
  const bytes = new Uint8Array(await obj.arrayBuffer());
  const maxBytes = intEnv(env, "MAX_FILE_BYTES", 10 * 1024 * 1024);
  if (bytes.length > maxBytes) {
    await env.PDF_BUCKET.delete(key);
    return json({ ok: false, error: "File exceeds size limit." }, 400, request);
  }

  const pageCount = await countPdfPages(bytes);
  const maxDoc = intEnv(env, "MAX_PAGES_DOC", 5);
  const maxProject = intEnv(env, "MAX_PAGES_PROJECT", 50);
  const maxDay = intEnv(env, "MAX_PAGES_DAY", 20);
  if (pageCount > maxDoc) {
    await env.PDF_BUCKET.delete(key);
    return json({ ok: false, error: `Document exceeds ${maxDoc} page limit.`, pageCount }, 400, request);
  }

  const usage = await readUsage(env, projectId);
  const nextProjectPages = (usage.projectObj.pages || 0) + pageCount;
  const nextDayPages = (usage.dayObj.pages || 0) + pageCount;
  if (nextProjectPages > maxProject) {
    await env.PDF_BUCKET.delete(key);
    return json(
      { ok: false, error: `Project exceeds ${maxProject} page limit.`, pageCount, projectPages: usage.projectObj.pages },
      429,
      request,
    );
  }
  if (nextDayPages > maxDay) {
    await env.PDF_BUCKET.delete(key);
    return json({ ok: false, error: `Daily Textract/page limit (${maxDay}) reached.`, pageCount }, 429, request);
  }

  let text = await extractWithUnpdf(bytes);
  let usedTextract = false;
  const minChars = intEnv(env, "MIN_TEXT_CHARS", 200);
  if (text.length < minChars) {
    if (!env.AWS_ACCESS_KEY_ID || !env.AWS_SECRET_ACCESS_KEY) {
      await env.PDF_BUCKET.delete(key);
      return json({ ok: false, error: "Scanned PDF requires Textract but AWS credentials are not configured." }, 503, request);
    }
    text = await textractExtract(bytes, env);
    usedTextract = true;
  }
  if (text.length <= 10) {
    await env.PDF_BUCKET.delete(key);
    return json({ ok: false, error: "Could not extract enough readable text." }, 400, request);
  }

  const backend = String(env.EC2_BACKEND_URL || "").replace(/\/$/, "");
  if (!backend) {
    await env.PDF_BUCKET.delete(key);
    return json({ ok: false, error: "EC2_BACKEND_URL is not configured." }, 503, request);
  }
  const ingestHeaders = { "Content-Type": "application/json" };
  if (env.SUBSTRATE_INGEST_SECRET) {
    ingestHeaders["X-Assure-Worker-Secret"] = env.SUBSTRATE_INGEST_SECRET;
  }
  const ingestRes = await fetch(`${backend}/api/substrate`, {
    method: "POST",
    headers: ingestHeaders,
    body: JSON.stringify({
      projectId,
      text,
      pageCount,
      filename: key.split("/").pop() || "upload.pdf",
      source: usedTextract ? "edge-textract" : "edge-unpdf",
    }),
  });
  const ingestPayload = await ingestRes.json().catch(() => ({}));
  if (!ingestRes.ok || !ingestPayload.ok) {
    await env.PDF_BUCKET.delete(key);
    return json(
      {
        ok: false,
        error: ingestPayload.error || "Backend ingest failed.",
        backendStatus: ingestRes.status,
      },
      502,
      request,
    );
  }

  await env.PDF_BUCKET.delete(key);
  usage.projectObj.pages = nextProjectPages;
  usage.projectObj.uploads = (usage.projectObj.uploads || 0) + 1;
  usage.dayObj.pages = nextDayPages;
  if (usedTextract) {
    usage.dayObj.textract_pages = (usage.dayObj.textract_pages || 0) + pageCount;
  }
  await writeJson(env, usage.projectKey, usage.projectObj);
  await writeJson(env, usage.dayKey, usage.dayObj);

  return json({
    ok: true,
    text,
    pageCount,
    usedTextract,
    textChars: text.length,
    substrateId: ingestPayload.id || null,
  });
}

function json(payload, status = 200, request) {
  const origin = request?.headers?.get("Origin") || "";
  return new Response(JSON.stringify(payload), {
    status,
    headers: { ...JSON_HEADERS, ...corsHeaders(origin) },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(request.headers.get("Origin") || "*") });
    }
    if (url.pathname === "/api/upload-url" && request.method === "GET") {
      return handleUploadUrl(request, env);
    }
    if (url.pathname === "/api/process" && request.method === "POST") {
      return handleProcess(request, env);
    }
    if (url.pathname.startsWith("/upload/") && request.method === "PUT") {
      const key = decodeURIComponent(url.pathname.slice("/upload/".length));
      return handleUploadPut(request, env, key);
    }
    if (url.pathname === "/health") {
      return json({ ok: true, service: "assure-pdf-worker", environment: env.ENVIRONMENT || "unknown" });
    }
    return json({ ok: false, error: "Not found." }, 404, request);
  },
};
