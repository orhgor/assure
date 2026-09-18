// §6: #right-evidence has ONE writer.
//
// Booting the fixture document and clicking a confidence span used to paint the
// pane twice: renderEvidencePanel drew the paragraph panel (the dispatcher's
// repaint of the node-selection write), and renderConfidenceEvidence then
// cleared that pane and drew the ledger view, from the click handler, in the
// same tick. The pane is one surface with one state, so the click must land as
// one build: the paragraph panel with the clicked span's ledger tail attached.
//
// The measurement is a plain empty→populated transition count on the pane's own
// appendChild, so it counts builds rather than renders the code claims to make.
const { test, expect } = require("@playwright/test");

const PROJECT_ID = "writers-fix-1";
const PARAGRAPH_ID = "p-fix1";
const SPAN_SCORE = "0.9";
const FIXTURE_TEXT = "The policy limit is five million dollars per occurrence.";
const SOURCE_NAME = "naic-underwriting-policy.pdf";

// GET /api/projects/<pid>/jdf -> {ok, document:{document_id, meta, truth_ledger,
// body}} (shell.js:1309). The document carries one audited paragraph with real
// provenance and one confidence span covering its whole text, which is what the
// span renderer (applyConfidenceSpans) and the two evidence writers read.
const FIXTURE_DOCUMENT = {
  document_id: "doc-" + PROJECT_ID,
  meta: {
    project_id: PROJECT_ID,
    provenance_stats: { anchored: 1, total: 1, unanchored: 0 },
    confidenceSpans: [
      { nodeId: PARAGRAPH_ID, startChar: 0, endChar: FIXTURE_TEXT.length, score: parseFloat(SPAN_SCORE) },
    ],
  },
  truth_ledger: {},
  body: [
    {
      type: "section",
      id: "sec-fix1",
      title: "Fixture section",
      children: [
        {
          type: "paragraph",
          id: PARAGRAPH_ID,
          content: FIXTURE_TEXT,
          annotations: {},
          meta: {
            provenance: [
              {
                source_name: SOURCE_NAME,
                page_number: 3,
                excerpt: FIXTURE_TEXT,
                rule: "limit_consistency",
                confidence: "0.93",
                entailment: { verdict: "yes", reasoning: "The filing states the same limit." },
              },
            ],
          },
        },
      ],
    },
  ],
};

const SHELL_SURFACE_SKIP =
  "targets the prototype shell at /workbench/ — this base URL does not serve it";

test.beforeEach(async ({ request }) => {
  const res = await request.get("/workbench/").catch(() => null);
  const body = res && res.ok() ? await res.text() : "";
  test.skip(!body.includes("app-shell"), SHELL_SURFACE_SKIP);
});

test("a confidence span paints #right-evidence exactly once (panel + ledger)", async ({ page }) => {
  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text() || "");
  });
  page.on("pageerror", (e) => consoleErrors.push("pageerror: " + (e && e.message)));

  await page.addInitScript(
    (fixture) => {
      try {
        window.localStorage.setItem("assure_project", fixture.projectId);
      } catch (_) {}
      // One entry per build of #right-evidence: a build starts when the pane is
      // appended to while empty (every writer clears it first), and the entry is
      // kept up to date so it holds that build's final text.
      window.__evidenceBuilds = [];
      const appendChild = Node.prototype.appendChild;
      Node.prototype.appendChild = function (child) {
        const pane = document.getElementById("right-evidence");
        const isPane = !!pane && this === pane;
        if (isPane && pane.childNodes.length === 0) window.__evidenceBuilds.push("");
        const result = appendChild.call(this, child);
        if (isPane) {
          const i = window.__evidenceBuilds.length - 1;
          window.__evidenceBuilds[i] = (pane.textContent || "").replace(/\s+/g, " ").trim();
        }
        return result;
      };
    },
    { projectId: PROJECT_ID }
  );

  // Boot the fixture document: the shell hydrates the persisted tree on load
  // (_restoreProjectDocument, shell.js:1378), then applies the confidence spans.
  await page.route("**/api/**", (route) => {
    const url = route.request().url();
    if (url.includes("/jdf")) {
      return route.fulfill({ json: { ok: true, document: FIXTURE_DOCUMENT } });
    }
    if (url.includes("/history")) return route.fulfill({ json: { revisions: [] } });
    if (url.endsWith("/api/projects") || url.includes("/api/projects?")) {
      return route.fulfill({ json: { id: PROJECT_ID } });
    }
    return route.fulfill({ json: { ok: true } });
  });

  await page.goto("/workbench/", { waitUntil: "domcontentloaded", timeout: 60_000 });
  const span = page.locator(`.jdf-node[data-node-id="${PARAGRAPH_ID}"] .conf-span`);
  await expect(span).toBeVisible({ timeout: 30_000 });
  await expect(span).toHaveAttribute("data-score", SPAN_SCORE);

  // Ignore everything the boot painted; only the click is under measurement.
  await page.evaluate(() => {
    window.__evidenceBuilds.length = 0;
  });
  await span.click();
  const builds = await page.evaluate(() => window.__evidenceBuilds);

  // Exactly one build, and it is the whole view: the paragraph panel's verdict
  // header and provenance fields, with the span's ledger tail appended.
  expect(builds, `#right-evidence builds: ${JSON.stringify(builds)}`).toHaveLength(1);
  expect(builds[0]).toContain("Verified in source · page 3");
  expect(builds[0]).toContain(SOURCE_NAME);
  expect(builds[0]).toContain("Ledger check score: 90%");

  expect(consoleErrors, JSON.stringify(consoleErrors)).toEqual([]);
});
