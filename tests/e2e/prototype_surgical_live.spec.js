// prototype surgical revision — LIVE model integration test.
// Real compile + real rephrase against staging. Asserts SEMANTICS + structure only
// (no literal-string assertions). If the model doesn't shorten, we report before/after
// and fail WITHOUT loosening the assertion.
const { test, expect } = require("@playwright/test");

const INTENT = "State the liability limit and its dollar amount in one sentence.";

test("prototype surgical revision — live model integration", async ({ page }) => {
  const consoleErrors = [];
  await page.addInitScript(() => {
    try {
      localStorage.setItem("assure_onboarding_complete", "1");
      localStorage.setItem("assure_founder_workbench", "1");
    } catch (_) {}
  });
  page.on("console", (msg) => {
    const t = (msg.text() || "") + "";
    if (msg.type === "error") consoleErrors.push(t);
  });
  page.on("pageerror", (e) => consoleErrors.push("pageerror: " + (e && e.message)));

  await page.goto("/", { waitUntil: "networkidle", timeout: 60_000 });
  await expect(page.locator(".app-shell")).toBeVisible({ timeout: 30_000 });

  // Live compile -> render a node.
  await page.locator("#dock-text").fill(INTENT);
  await page.locator("#dock-submit").click();
  const firstNode = page.locator(".jdf-node[data-node-id]").first();
  await expect(firstNode).toBeVisible({ timeout: 180_000 });

  const before = (await firstNode.innerText()) || "";

  // Open rephrase editor (_attachNodeRephrase :1097).
  await firstNode.click();
  // F7: the field is a textarea, so Enter inserts a newline; submit is the chord.
  const rephraseInput = page.locator("textarea[placeholder='Rephrase this paragraph…']");
  await expect(rephraseInput).toBeVisible({ timeout: 15_000 });

  // Type + submit (_submitRephrase :1153): instruct the model to shorten.
  await rephraseInput.fill("Shorten this sentence.");
  await rephraseInput.press("Control+Enter");
  await expect(rephraseInput).toBeHidden({ timeout: 120_000 });

  const afterNode = page.locator(".jdf-node[data-node-id]").first();
  await expect(afterNode).toBeVisible({ timeout: 30_000 });
  const after = (await afterNode.innerText()) || "";

  // Report before/after (up to 200 chars) so a not-shortened failure is diagnosable.
  console.log("BEFORE=" + before.slice(0, 200));
  console.log("AFTER=" + after.slice(0, 200));

  // Semantics: the edit must have happened and shortened the node.
  expect(after.length, "after should be non-empty").toBeGreaterThan(0);
  expect(after, "after should differ from before").not.toBe(before);
  expect(after.length, "after should be shorter than before (intent: shorten)").toBeLessThan(
    before.length
  );

  // Verification surface visible.
  await page.locator('#pane-right .mode-tab[data-right-tab="redhat"]').click();
  await expect(page.locator("#right-redhat")).toBeVisible({ timeout: 20_000 });

  // Node history rows > 0 (a real revision, not the empty-hint).
  await expect(
    page.locator("#right-node-history-list li:not(.empty-hint)").first()
  ).toBeAttached({ timeout: 30_000 });

  // Zero console errors.
  expect(consoleErrors, JSON.stringify(consoleErrors)).toEqual([]);
});