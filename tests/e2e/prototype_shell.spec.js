// Prototype shell smoke test — asserts the live shell loads clean.
// Selectors are taken verbatim from prototype/index.html / are bound by
// prototype/shell.js (do NOT rename assets or selectors).
//
// golden_path.spec.js is NOT expected to fail here: it targets /app (the legacy
// founder workbench, absent from this host) and probes that path, skipping only
// where the surface is missing. It runs wherever /app returns 200 — locally and
// on staging.getassureai.com, not on prototype.getassureai.com.
//
// This spec is the mirror case: it needs the PROTOTYPE shell at "/". The default
// local base URL (scripts/playwright_dev_server.py) serves the marketing landing
// page there, so the gate below skips instead of failing with a missing .app-shell.
const { test, expect } = require("@playwright/test");

const SHELL_SURFACE_SKIP =
  "targets the prototype shell — this base URL serves a different surface at /";

test.beforeEach(async ({ request, baseURL }) => {
  const res = await request.get("/").catch(() => null);
  const body = res && res.ok() ? await res.text() : "";
  test.skip(!body.includes("app-shell"), SHELL_SURFACE_SKIP);
});

test("prototype shell loads clean (no console errors, no failed requests, no sse-failure)", async ({ page }) => {
  const consoleErrors = [];
  const sseFailures = [];
  const requestFailures = [];

  // Collect console errors, "[sse-failure]" warnings, and network failures.
  page.on("console", (msg) => {
    const text = msg.text || "";
    if (text.includes("[sse-failure]")) {
      sseFailures.push(text);
    } else if (msg.type === "error") {
      // GET /api/projects/<id>/parsure/latest is optional: an API without the
      // intake report answers 404 once and the shell hides the widget. The
      // browser logs that 404 as a resource error; it is not a shell defect.
      if (/parsure\/latest/.test(text) || (/404/.test(text) && /Failed to load resource/.test(text))) return;
      consoleErrors.push(text);
    }
  });
  page.on("requestfailed", (req) => {
    requestFailures.push(
      `${(req.method || "").toUpperCase()} ${req.url} :: ${req.failure || "unknown"}`
    );
  });

  await page.goto("/", { waitUntil: "networkidle", timeout: 60_000 });

  // Root container from prototype/index.html (`<div class="app-shell">`).
  const shell = page.locator(".app-shell");
  await expect(shell).toBeVisible({ timeout: 30_000 });

  // Elements that shell.js actually binds on DOMContentLoaded.
  await expect(page.locator("header.app-header")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("#dock-text")).toBeVisible({ timeout: 30_000 });
  // The three header zones (brief §3A): one status chip, one primary, one menu.
  await expect(page.locator("#shell-status-chip")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("#shell-primary")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("#shell-more")).toBeVisible({ timeout: 30_000 });

  // Give async work (e.g. ensureProjectId -> /api/projects) a moment to settle.
  await page.waitForTimeout(2500);

  // Assert zero of each category.
  expect(
    consoleErrors,
    `unexpected console errors:\n${JSON.stringify(consoleErrors, null, 2)}`
  ).toEqual([]);
  expect(
    requestFailures,
    `unexpected failed requests:\n${JSON.stringify(requestFailures, null, 2)}`
  ).toEqual([]);
  expect(
    sseFailures,
    `unexpected [sse-failure] warnings:\n${JSON.stringify(sseFailures, null, 2)}`
  ).toEqual([]);
});