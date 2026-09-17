/**
 * Golden Path E2E — v1.0 definition of done (docs/user-experience.md).
 *
 * Intended v1.0 DOM contract (Difference Engine + Orchestrator):
 *   .orchestrator-input, .orchestrator-submit, .staging-canvas,
 *   .model-pane[data-model="claude|deepseek"], .diff-highlight,
 *   .push-to-main-btn, .redhat-audit-btn, .redhat-pass-2-complete,
 *   .main-polish-btn, .full-context-scan-btn, .benchmark-compare-btn,
 *   .local-fix-btn, .export-complete
 *
 * v1.0 gate: Steps 1–4 only (orchestrator → diff → click-to-merge). Steps 5–10 → v1.1 backlog (docs/deferred.md).
 */

const { test, expect } = require("@playwright/test");

/** v1.0 selector contract — future UI; do not stub in tests. */
const SEL = {
  mainDocument: "#founder-draft-editor",
  /** Founder TipTap mount: ProseMirror + jdf-tiptap-doc on the same contenteditable (jdf_tiptap.js). */
  mainEditorSurface:
    ".founder-workbench #founder-draft-editor .ProseMirror[contenteditable='true'], .founder-workbench #founder-draft-editor .jdf-tiptap-doc[contenteditable='true']",
  orchestratorInput: ".orchestrator-input",
  orchestratorSubmit: ".orchestrator-submit",
  stagingCanvas: ".staging-canvas",
  claudePane: '.staging-canvas .model-pane[data-model="claude"]',
  deepseekPane: '.staging-canvas .model-pane[data-model="deepseek"]',
  diffHighlight: ".diff-highlight",
  pushToMainBtn: ".push-to-main-btn",
  redhatAuditBtn: ".redhat-audit-btn",
  redhatPass2: ".redhat-pass-2-complete",
  mainPolishBtn: ".main-polish-btn",
  lockHash: "[data-lock-hash]",
  fullContextScanBtn: ".full-context-scan-btn",
  fullContextResults: ".full-context-results",
  benchmarkBtn: ".benchmark-compare-btn",
  benchmarkResults: ".benchmark-results",
  localFixBtn: ".local-fix-btn",
  localFixPanel: ".local-fix-panel",
  exportBtn: "#btn-export-dossier",
  exportComplete: ".export-complete",
};

const INTENT =
  "Compare liability limits for a commercial property policy renewal in Boston.";

const ONBOARDING_KEY = "assure_onboarding_complete";

function modKey(page) {
  return page.evaluate(() => (navigator.platform.includes("Mac") ? "Meta" : "Control"));
}

async function focusMainEditor(page) {
  const editor = page.locator(SEL.mainEditorSurface).first();
  await expect(editor).toBeVisible({ timeout: 10_000 });
  await editor.click({ timeout: 5_000 });
}

async function gotoFounderWorkbench(page) {
  await page.addInitScript((key) => {
    try {
      localStorage.setItem(key, "1");
      localStorage.setItem("assure_founder_workbench", "1");
    } catch (_e) {
      /* ignore */
    }
  }, ONBOARDING_KEY);

  await page.goto("/app", { waitUntil: "domcontentloaded" });
  await expect(page.locator("#workbench-root")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("body.founder-workbench")).toBeVisible({ timeout: 10_000 });
  // await page.waitForFunction(
  //   () =>
  //     document.body.classList.contains("founder-workbench") &&
  //     window.AssureCommandBar &&
  //     window.AssureRunsStack
  // );
}

/**
 * The legacy founder workbench is served at /app, and whether that surface
 * exists is a property of the host — not of the host being localhost. Gating
 * on a local-host regex would skip a remote host that does serve it, so probe
 * the resolved `use.baseURL` (playwright.config.js takes it from
 * ASSURE_BASE_URL, falling back to the local dev server) instead of guessing
 * from its shape.
 *
 * The probe must assert that the base URL *serves* the workbench, not merely
 * that "/app" resolves. A shell-only host answers /app with a 302 to /, so a
 * redirect-following probe sees 200 for a page that has no workbench in it and
 * the guard never fires.
 */
const LEGACY_SURFACE_SKIP =
  "targets the legacy /app surface — not present on this base URL";

/** Markup the legacy workbench emits; the shell prototype does not. */
const LEGACY_SURFACE_MARKER = "workbench-root";

test.beforeEach(async ({ request, baseURL }) => {
  if (!baseURL) return; // nothing configured to probe — run, don't hide
  // maxRedirects: 0 — a 3xx is the answer we want to see. This host redirects
  // /app elsewhere, so it does not serve the workbench.
  const res = await request.get(new URL("/app", baseURL).toString(), {
    maxRedirects: 0,
  });
  if (res.status() !== 200) test.skip(true, LEGACY_SURFACE_SKIP);
  // A 200 alone is still not proof: a catch-all route can answer /app with
  // something that is not the workbench. Require its markup.
  const body = await res.text();
  test.skip(!body.includes(LEGACY_SURFACE_MARKER), LEGACY_SURFACE_SKIP);
});

test("Golden Path — v1.0 E2E (Steps 1–4)", async ({ page }) => {
  // Step 1 — Start workbook / Main document ready
  await test.step("Step 1: Main document is open", async () => {
    await gotoFounderWorkbench(page);
    await expect(page.locator(SEL.mainDocument)).toBeVisible();
  });

  // Step 2 — Command bar (⌘K / Investigate) — no TipTap mount required.
  await test.step("Step 2: Submit intent via orchestrator", async () => {
    const key = await modKey(page);
    await page.keyboard.press(`${key}+k`);

    const barInput = page.locator("#command-bar-input");
    const opened = await barInput.isVisible({ timeout: 3_000 }).catch(() => false);
    if (!opened) {
      await page.locator("#founder-cmdk-btn").click();
    }
    await expect(barInput).toBeVisible({ timeout: 5_000 });
    await barInput.fill(INTENT);
    await barInput.press("Enter");
  });

  // Step 3 — Side-by-side model outputs with visual diff highlights
  await test.step("Step 3: Claude + DeepSeek panes with diff highlights", async () => {
    const staging = page.locator(SEL.stagingCanvas);
    await expect(staging).toBeVisible({ timeout: 30_000 });

    await expect(page.locator(SEL.claudePane)).toBeVisible({ timeout: 120_000 });
    await expect(page.locator(SEL.deepseekPane)).toBeVisible({ timeout: 120_000 });

    // Staging live compare runs two model calls (60-180s). Wait for both
    // panes to contain real text, not the "waiting" placeholder.
    await page.waitForFunction(
      () => {
        const c = document.querySelector('.staging-canvas .model-pane[data-model="claude"]');
        const d = document.querySelector('.staging-canvas .model-pane[data-model="deepseek"]');
        if (!c || !d) return false;
        const cText = (c.textContent || "").trim();
        const dText = (d.textContent || "").trim();
        return (
          cText.length > 20 && dText.length > 20 &&
          !/waiting/i.test(cText) && !/waiting/i.test(dText)
        );
      },
      { timeout: 180_000 }
    );

    await expect(page.locator(SEL.diffHighlight).first()).toBeVisible({ timeout: 120_000 });
  });

  // Step 4 — Hybrid merge: push highlighted block to Main document
  await test.step("Step 4: Add highlighted block to Main document", async () => {
    const addBtn = page.locator(SEL.pushToMainBtn).first();
    await expect(addBtn).toBeVisible({ timeout: 30_000 });
    await addBtn.click();
    await expect(page.locator(SEL.mainDocument)).toContainText(/liability|Boston/i, {
      timeout: 15_000,
    });
  });

});

test("Compare pane closes cleanly on failure", async ({ page }) => {
  await page.route("**/api/runs/compare", (route) =>
    route.fulfill({ status: 500, body: "forced failure" })
  );

  await gotoFounderWorkbench(page);
  await page.locator("#founder-cmdk-btn").click();
  const barInput = page.locator("#command-bar-input");
  await expect(barInput).toBeVisible({ timeout: 5_000 });
  await barInput.fill("test compare failure");
  await barInput.press("Enter");

  await expect(page.locator("#compare-pane")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("#compare-pane .compare-error-card")).toBeVisible({ timeout: 10_000 });

  await expect(page.locator("#command-bar-overlay")).toBeHidden({ timeout: 5_000 });
  await page.locator("#compare-close").click();
  await expect(page.locator("#compare-pane")).toBeHidden({ timeout: 5_000 });
  await expect(page.locator("#compare-pane-empty")).toBeVisible();
});

test("Never merge error strings into Main document", async ({ page }) => {
  await page.route("**/api/drafts?**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ draft: { content: { body: [] } } }),
    });
  });
  await page.route("**/api/runs/compare", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "success",
        stack: "free",
        models: {
          claude: {
            name: "Gemini 3.6 Flash",
            text: "Boston commercial property liability limit is $5,000,000 per occurrence.",
            error: null,
          },
          deepseek: {
            name: "DeepSeek V3",
            text: "",
            error:
              "ERROR: Run aborted due to timeout (60s). Please increase PEM_TIMEOUT_SECONDS or split the task.",
          },
        },
      }),
    });
  });

  await gotoFounderWorkbench(page);
  // await page.waitForFunction(
  //   () =>
  //     window.AssureOrchestrator &&
  //     typeof window.AssureOrchestrator.run === "function" &&
  //     window.AssureTiptapEditor &&
  //     typeof window.AssureTiptapEditor.getEditor === "function"
  // );
  await page.waitForFunction(
    () => window.AssureOrchestrator && typeof window.AssureOrchestrator.run === "function",
    { timeout: 15_000 }
  );
  await page.evaluate(async (intent) => {
    await window.AssureOrchestrator.run(intent);
  }, INTENT);

  await expect(page.locator('.model-pane[data-model="deepseek"] .compare-error-card')).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.locator('.model-pane[data-model="deepseek"] .push-to-main-btn')).toHaveCount(0);

  const successBtn = page.locator('.model-pane[data-model="claude"] .push-to-main-btn').first();
  await expect(successBtn).toBeVisible({ timeout: 15_000 });
  await successBtn.click();

  const mainText = (await page.locator(SEL.mainDocument).innerText()).toLowerCase();
  expect(mainText).toMatch(/boston|liability|5,000,000/);
  expect(mainText).not.toMatch(/run aborted|pem_timeout/);
});

test.describe("Golden Path — v1.1 backlog (Steps 5–10)", () => {
  test.skip(true, "Deferred to v1.1 — see docs/deferred.md § v1.1 backlog");

  test("Steps 5–10 full path", async ({ page }) => {
    await gotoFounderWorkbench(page);
    await page.locator(SEL.redhatAuditBtn).click();
    await expect(page.locator(SEL.redhatPass2)).toBeVisible({ timeout: 120_000 });
    const locksBefore = await page.locator(SEL.lockHash).count();
    await page.locator(SEL.mainPolishBtn).click();
    await expect(page.locator(SEL.lockHash)).toHaveCount(locksBefore);
    await page.locator(SEL.fullContextScanBtn).click();
    await expect(page.locator(SEL.fullContextResults)).toBeVisible({ timeout: 120_000 });
    await page.locator(SEL.benchmarkBtn).click();
    await expect(page.locator(SEL.benchmarkResults)).toBeVisible({ timeout: 120_000 });
    await page.locator(SEL.localFixBtn).first().click();
    await expect(page.locator(SEL.localFixPanel)).toBeVisible();
    await page.locator(SEL.exportBtn).click();
    await expect(page.locator(SEL.exportComplete)).toBeVisible({ timeout: 60_000 });
  });
});
