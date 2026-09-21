// prototype JDF ingest + search E2E against the live prototype.
const { test, expect } = require("@playwright/test");

const TERM = "liability limit";
const FIXTURE = "tests/fixtures/policy-sample.pdf";

test("prototype JDF ingest and search", async ({ page }) => {
  const consoleErrors = [];
  const requestFailures = [];
  const sseFailures = [];

  await page.addInitScript(() => {
    try {
      localStorage.setItem("assure_onboarding_complete", "1");
      localStorage.setItem("assure_founder_workbench", "1");
    } catch (_) {}
  });
  page.on("console", (msg) => {
    const t = (msg.text() || "") + "";
    if (t.includes("[sse-failure]")) sseFailures.push(t);
    else if (msg.type === "error") consoleErrors.push(t);
  });
  page.on("requestfailed", (req) => {
    requestFailures.push(
      `${(req.method || "").toUpperCase()} ${req.url} :: ${req.failure || "unknown"}`
    );
  });

  async function panelSnapshot() {
    try {
      const el = await page.$("#dock-search-results");
      if (!el) return "(panel not present)";
      const txt = (await el.innerText()) || "(empty panel)";
      return txt;
    } catch (e) {
      return "snapshot error: " + e;
    }
  }

  await page.goto("/", { waitUntil: "networkidle", timeout: 60_000 });
  await expect(page.locator(".app-shell")).toBeVisible({ timeout: 30_000 });

  // Ingest
  await page.locator("#dock-ingest").click();
  await page.locator("#dock-ingest-file").setInputFiles(FIXTURE);
  try {
    await expect(page.locator("#dock-search-results")).toContainText(
      /\d+ figures found in/,
      { timeout: 60_000 }
    );
    console.log("INGEST_OK");
  } catch (err) {
    console.log("FAILURE_PANEL_DOM:\n" + (await panelSnapshot()));
    throw err;
  }

  // Search
  await page.locator("#dock-search").fill(TERM);
  await page.locator("#dock-search").press("Enter");
  try {
    await expect(page.locator("#dock-search-results > div").first()).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.locator("#dock-search-results")).toContainText(/liability|policy-sample/, {
      timeout: 30_000,
    });
    await expect(page.locator("#dock-search-results")).not.toContainText("No matches", {
      timeout: 10_000,
    });
    console.log("SEARCH_OK");
  } catch (err) {
    console.log("SEARCH_FAILURE_PANEL_DOM:\n" + (await panelSnapshot()));
    throw err;
  }

  expect(consoleErrors, JSON.stringify(consoleErrors)).toEqual([]);
  expect(requestFailures, JSON.stringify(requestFailures)).toEqual([]);
  console.log("sse_failures=" + JSON.stringify(sseFailures));
});