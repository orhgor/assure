// prototype surgical revision from search (Path B) — defines the CLIENT capability:
// a JDF search result should load into the editor as an editable .jdf-node[data-node-id]
// so the inline rephrase editor (shell.js:1097) can operate on it.
//
// IMPLEMENTED 2026-09-17: the bridge landed (prototype/shell.js:
// _openJdfSearchResultInEditor). A search hit now opens as an editable
// .jdf-node[data-node-id] with the inline rephrase editor attached, so this spec
// asserts the capability rather than documenting its absence.
const { test, expect } = require("@playwright/test");

const TERM = "liability limit";
const FIXTURE = "tests/fixtures/policy-sample.pdf";

test("prototype surgical revision from search (Path B — defines missing capability)", async ({ page }) => {
  await page.addInitScript(() => {
    try {
      localStorage.setItem("assure_onboarding_complete", "1");
      localStorage.setItem("assure_founder_workbench", "1");
    } catch (_) {}
  });

  // Steps 3-4: ingest.
  await page.goto("/", { waitUntil: "networkidle", timeout: 60_000 });
  await expect(page.locator(".app-shell")).toBeVisible({ timeout: 30_000 });
  await page.locator("#dock-ingest").click();
  await page.locator("#dock-ingest-file").setInputFiles(FIXTURE);
  await expect(page.locator("#dock-search-results")).toContainText(/Indexed \d+ chunks/, {
    timeout: 60_000,
  });

  // Step 5: search.
  await page.locator("#dock-search").fill(TERM);
  await page.locator("#dock-search").press("Enter");
  await expect(page.locator("#dock-search-results > div").first()).toBeVisible({ timeout: 30_000 });

  // Step 6: click the first search result.
  await page.locator("#dock-search-results > div").first().click();

  // Step 7: the result must load into the editor as an editable JDF node.
  const node = page.locator(".jdf-node[data-node-id]").first();
  let loaded = false;
  try {
    await node.waitFor({ state: "visible", timeout: 15_000 });
    loaded = true;
  } catch (_) {
    loaded = false;
  }
  if (!loaded) {
    await page.screenshot({ path: "test-results/surgical-from-search-missing.png" });
    throw new Error(
      "SEARCH→EDIT FLOW NOT IMPLEMENTED — client surgical revision requires search results to load into editable state"
    );
  }
});