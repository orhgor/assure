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
  return page.evaluate(() =>
    navigator.platform.includes("Mac") ? "Meta" : "Control"
  );
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
  await page.waitForFunction(
    () =>
      document.body.classList.contains("founder-workbench") &&
      window.AssureCommandBar &&
      window.AssureRunsStack
  );
}

test("Golden Path — v1.0 E2E (Steps 1–4)", async ({ page }) => {
  // Step 1 — Start workbook / Main document ready
  await test.step("Step 1: Main document is open", async () => {
    await gotoFounderWorkbench(page);
    await expect(page.locator(SEL.mainDocument)).toBeVisible();
  });

  // Step 2 — Submit intent via Orchestrator command bar (⌘K).
  // v1.0 contract renames #operator-prompt-input → .orchestrator-input (Sprint 1).
  await test.step("Step 2: Submit intent via orchestrator", async () => {
    await page.locator(`${SEL.mainDocument} .ProseMirror`).click({ timeout: 10_000 });
    await page.waitForFunction(
      () =>
        window.AssureTiptapEditor &&
        typeof window.AssureTiptapEditor.getEditor === "function" &&
        window.AssureTiptapEditor.getEditor()
    );

    const key = await modKey(page);
    await page.keyboard.press(`${key}+k`);
    const prompt = page.locator("#operator-prompt");
    const opened = await prompt.isVisible().catch(() => false);
    if (!opened) {
      await page.evaluate(() => {
        const ed = window.AssureTiptapEditor.getEditor();
        if (typeof window.showOperatorPrompt === "function") {
          window.showOperatorPrompt(ed);
        } else {
          window.AssureOperatorPrompt.open(ed);
        }
      });
    }
    await expect(prompt).toBeVisible({ timeout: 5_000 });

    const legacyBar = page.locator("#operator-prompt-input");
    await legacyBar.fill(INTENT);
    await legacyBar.press("Enter");

    // Future: page.locator(SEL.orchestratorInput) + SEL.orchestratorSubmit
  });

  // Step 3 — Side-by-side model outputs with visual diff highlights
  await test.step("Step 3: Claude + DeepSeek panes with diff highlights", async () => {
    const staging = page.locator(SEL.stagingCanvas);
    await expect(staging).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(SEL.claudePane)).toBeVisible();
    await expect(page.locator(SEL.deepseekPane)).toBeVisible();
    await expect(page.locator(SEL.diffHighlight).first()).toBeVisible();
  });

  // Step 4 — Hybrid merge: push highlighted block to Main document
  await test.step("Step 4: Add highlighted block to Main document", async () => {
    const addBtn = page.locator(SEL.pushToMainBtn).first();
    await expect(addBtn).toBeVisible();
    await addBtn.click();
    await expect(page.locator(SEL.mainDocument)).toContainText(/liability|Boston/i);
  });

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
