// prototype surgical revision — DETERMINISTIC frontend flow (SSE mocked).
// Routes the two streaming endpoints + node history so the model input is controlled
// and literal assertions are valid. Matches the shell's SSE parser (shell.js:1416
// parseSseLoop: `event:`/`data:` frames), renderJdfDocument (:1036), the rephrase
// contract (_handleRephraseFrame :1132 expects `jdf_node_ready`), and _loadNodeHistory
// (:822 expects { revisions: [...] }).
const { test, expect } = require("@playwright/test");

const DRAFT_SSE = [
  'event: status\ndata: {"stage":"preflight","message":"start"}',
  'event: status\ndata: {"stage":"model","message":"drafting"}',
  'event: redhat\ndata: {"redhat":{"status":"ran","findings_count":0,"findings":[]}}',
  "event: compiled\ndata: " +
    JSON.stringify({
      document: {
        document_id: "doc-mock1",
        meta: {},
        truth_ledger: {},
        body: [
          {
            type: "section",
            id: "sec-mock1",
            title: "Mock Section",
            children: [
              { type: "paragraph", id: "p-mock1", content: "ORIGINAL paragraph to rewrite." },
            ],
          },
        ],
      },
    }),
  'event: complete\ndata: {"ok":true}',
].join("\n\n") + "\n\n";

// Deterministic SSE bodies. The rephrase node id is echoed from the request's
// target_node_id so it matches whichever node is clicked.
const inquire = (nodeId) =>
  [
    "event: jdf_node_ready\ndata: " +
      JSON.stringify({
        node: { type: "paragraph", id: nodeId, content: "REVISED BY TEST", annotations: {}, meta: {} },
      }),
    'event: complete\ndata: {"ok":true}',
  ].join("\n\n") + "\n\n";

test("prototype surgical revision — deterministic frontend flow (mocked SSE)", async ({ page }) => {
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

  // Fully deterministic: mock the project create (real one is slow) + the streaming
// + history endpoints. Everything else passes through to the real backend.
  await page.route("**/api/projects**", async (route) => {
    const req = route.request();
    const u = req.url();
    const m = req.method();
    if (u.endsWith("/draft/stream")) {
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: DRAFT_SSE });
    }
    if (u.endsWith("/inquire/stream")) {
      let nid = "p-mock1";
      try {
        const pd = req.postDataJSON();
        if (pd && pd.target_node_id) nid = String(pd.target_node_id);
      } catch (_) {}
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: inquire(nid) });
    }
    if (/\/nodes\/[^/]+\/history$/.test(u)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          revisions: [
            { version: 1, timestamp: "2026-01-01T00:00:00Z" },
            { version: 2, timestamp: "2026-01-02T00:00:00Z" },
          ],
        }),
      });
    }
    if (u.endsWith("/api/projects") && m === "POST") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: "prj-test", title: "Test" }),
      });
    }
    return route.continue();
  });

  await page.goto("/", { waitUntil: "networkidle", timeout: 60_000 });
  await expect(page.locator(".app-shell")).toBeVisible({ timeout: 30_000 });

  // Compile (mocked) -> renders the paragraph node.
  await page.locator("#dock-text").fill("Rewrite the paragraph");
  await page.locator("#dock-submit").click();
  const para = page.locator('.jdf-node[data-node-id="p-mock1"]');
  await expect(para).toBeVisible({ timeout: 30_000 });

  // Open the rephrase editor (_attachNodeRephrase :1097).
  await para.click();
  // F7: the field is a textarea (a finding is multi-line), so Enter inserts a
  // newline and Cmd/Ctrl+Enter submits.
  const rephraseInput = page.locator("textarea[placeholder='Rephrase this paragraph…']");
  await expect(rephraseInput).toBeVisible({ timeout: 15_000 });

  // Submit (mocked) (_submitRephrase :1153).
  await rephraseInput.fill("REVISED BY TEST");
  await rephraseInput.press("Control+Enter");
  await expect(rephraseInput).toBeHidden({ timeout: 30_000 });

  // 9a: literal content (model input is controlled -> correct assert).
  await expect(para).toContainText("REVISED BY TEST", { timeout: 30_000 });

  // 9b: verification surface — reveal the Red-Hat pane (:2990) via the tab.
  await page.locator('#pane-right .mode-tab[data-right-tab="redhat"]').click();
  await expect(page.locator("#right-redhat")).toBeVisible({ timeout: 20_000 });

  // 9c: node history rows > 0 via the mocked { revisions } endpoint (:822).
  await expect(page.locator("#right-node-history-list li")).toHaveCount(2, { timeout: 20_000 });

  // 10: zero console errors.
  expect(consoleErrors, JSON.stringify(consoleErrors)).toEqual([]);
});