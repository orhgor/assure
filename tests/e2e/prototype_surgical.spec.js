// prototype surgical revision (Path A): edit from a compiled draft.
// Trigger a live compile so a .jdf-node[data-node-id] renders, then click it,
// enter the rephrase editor (shell.js:1097), submit (shell.js:1153), and verify.
const { test, expect } = require("@playwright/test");

const INTENT = "State the liability limit and its dollar amount in one sentence.";

test("prototype surgical revision from compiled draft (Path A)", async ({ page }) => {
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

  const report = (k, v) => console.log(`${k}=${v}`);

  await page.goto("/", { waitUntil: "networkidle", timeout: 60_000 });
  await expect(page.locator(".app-shell")).toBeVisible({ timeout: 30_000 });

  // Step 3-4: trigger a compile -> render a .jdf-node[data-node-id].
  await page.locator("#dock-text").fill(INTENT);
  await page.locator("#dock-submit").click();
  const firstNode = page.locator(".jdf-node[data-node-id]").first();
  await expect(firstNode).toBeVisible({ timeout: 180_000 }); // live model compile

  // Step 5-6: click node -> rephrase editor opens (_attachNodeRephrase :1097).
  await firstNode.click();
  const rephraseInput = page.locator("input[placeholder='Rephrase this paragraph…']");
  await expect(rephraseInput).toBeVisible({ timeout: 15_000 });
  report("EDITOR_OPENED", true);

  // Step 7-8: type + submit (_submitRephrase :1153).
  const newText = "REVISED limit of $5,000,000";
  await rephraseInput.fill(newText);
  await rephraseInput.press("Enter");

  // Editor should close on success (node replaced / re-rendered).
  try {
    await expect(rephraseInput).toBeHidden({ timeout: 120_000 });
    report("REPHRASE_SUBMITTED", true);
  } catch (e) {
    report("REPHRASE_SUBMITTED", false);
    throw e;
  }

  // Step 9a: node content after submit (model-generated — informational).
  await expect(firstNode).toBeVisible({ timeout: 30_000 });
  const nodeText = (await firstNode.innerText()) || "";
  report("NODE_CONTAINS_NEW_TEXT", nodeText.includes("REVISED"));
  report("NODE_TEXT_SNIPPET", nodeText.slice(0, 120).replace(/\s+/g, " "));

  // Step 9b: verification surface (Red-Hat :2990 OR Evidence :2918 OR anchored).
  const verification = page
    .locator("#right-redhat, #redhat-panel, .redhat-panel, #right-evidence, .evidence-panel, [data-anchored], .jdf-chip")
    .first();
  try {
    await verification.waitFor({ state: "visible", timeout: 15_000 });
    report("VERIFICATION_SURFACE", true);
  } catch (_) {
    report("VERIFICATION_SURFACE", false);
  }

  // Step 9c: node history incremented (shell.js:822 _loadNodeHistory).
  try {
    await page.locator("#right-node-history-list li").first().waitFor({ state: "visible", timeout: 15_000 });
    report("HISTORY_ROWS", await page.locator("#right-node-history-list li").count());
  } catch (_) {
    report("HISTORY_ROWS", 0);
  }

  // Step 9d: Cmd+Z undo (informational — no undo handler observed).
  const before = (await firstNode.innerText()) || "";
  await page.keyboard.press("Meta+Z");
  await page.waitForTimeout(500);
  const after = (await firstNode.innerText()) || "";
  report("UNDO_RESTORED_ORIGINAL", after !== before && before !== "");

  // Step 10: zero console errors.
  report("CONSOLE_ERRORS", JSON.stringify(consoleErrors));
  expect(consoleErrors, JSON.stringify(consoleErrors)).toEqual([]);
});