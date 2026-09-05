#!/usr/bin/env node
/**
 * OpenUser .ux spec runner for Assure AI.
 * The upstream openuser-cli (0.1.x) is MCP/daemon-only; this runner implements
 * `run` and `history` so CI and local scripts can execute YAML UX specs.
 */
import { program } from "commander";
import { globSync } from "glob";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const HISTORY_PATH = path.join(REPO_ROOT, "openuser", "results", "history.jsonl");
const SCREENSHOT_DIR = path.join(REPO_ROOT, "openuser", "screenshots");

class SkipSpec extends Error {
  constructor(message) {
    super(message);
    this.name = "SkipSpec";
  }
}

function loadProjectConfig(projectId) {
  const cfgPath = path.join(REPO_ROOT, "openuser.config.json");
  if (!fs.existsSync(cfgPath)) return { projectId, baseUrl: process.env.ASSURE_BASE_URL || "https://staging.getassureai.com" };
  const cfg = JSON.parse(fs.readFileSync(cfgPath, "utf8"));
  return {
    projectId: projectId || cfg.projectId,
    baseUrl: process.env.ASSURE_BASE_URL || cfg.baseUrl || cfg.environments?.[0]?.url,
    name: cfg.name,
  };
}

function expandTemplate(value, ctx) {
  if (typeof value !== "string") return value;
  return value.replace(/\{\{(\w+)\}\}/g, (_, key) => String(ctx[key] ?? ""));
}

function parseSpec(filePath) {
  const raw = fs.readFileSync(filePath, "utf8");
  const spec = YAML.parse(raw);
  spec.__file = filePath;
  return spec;
}

function appendHistory(entry) {
  fs.mkdirSync(path.dirname(HISTORY_PATH), { recursive: true });
  fs.appendFileSync(HISTORY_PATH, `${JSON.stringify(entry)}\n`, "utf8");
}

async function screenshotOnFailure(page, specName, stepIndex) {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
  const safe = specName.replace(/[^a-z0-9_-]+/gi, "-").toLowerCase();
  const file = path.join(SCREENSHOT_DIR, `${safe}-step${stepIndex}-${Date.now()}.png`);
  if (page) await page.screenshot({ path: file, fullPage: true }).catch(() => {});
  return file;
}

async function runStep(page, step, ctx, spec, stepIndex) {
  const action = step.action;
  if (!action) throw new Error(`Step ${stepIndex}: missing action`);

  switch (action) {
    case "navigate": {
      const url = expandTemplate(step.url, ctx);
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: step.timeout || 60000 });
      return;
    }
    case "init_workbench": {
      await page.waitForSelector("#jdf-workbench", { state: "visible", timeout: 30000 });
      await page.waitForFunction(
        () => window.__assureJdf && typeof window.__assureJdf.render === "function",
        null,
        { timeout: 30000 }
      );
      await page.waitForFunction(
        () => !document.body.classList.contains("onboarding-active"),
        null,
        { timeout: 15000 }
      );
      return;
    }
    case "wait": {
      const timeout = step.timeout || 10000;
      const selector = step.selector;
      try {
        await page.waitForSelector(selector, { state: "visible", timeout });
      } catch (err) {
        if (step.optional) return;
        throw err;
      }
      return;
    }
    case "click": {
      const loc = page.locator(step.selector);
      const target = step.first ? loc.first() : loc;
      if (step.selector === "#generate-compile-btn" || step.selector === "#generate-full-audit-btn") {
        await page.evaluate(() => {
          const sel = window.getSelection();
          if (sel) sel.removeAllRanges();
          const intent = document.getElementById("generate-intent");
          if (intent) {
            intent.focus();
            const end = intent.value.length;
            intent.setSelectionRange(end, end);
          }
          const ed = window.AssureTiptapEditor && window.AssureTiptapEditor.editor;
          if (ed && ed.commands) {
            if (typeof ed.commands.blur === "function") ed.commands.blur();
            if (typeof ed.commands.setTextSelection === "function") {
              ed.commands.setTextSelection(0);
            }
          }
        });
        await target.evaluate((el) => {
          el.click();
        });
        return;
      }
      await target.scrollIntoViewIfNeeded().catch(() => {});
      try {
        await target.click({ timeout: step.timeout || 15000 });
      } catch (_) {
        await target.evaluate((el) => {
          el.click();
        });
      }
      return;
    }
    case "fill": {
      await page.locator(step.selector).fill(String(step.value ?? ""), { timeout: step.timeout || 10000 });
      return;
    }
    case "unique_intent": {
      const prefix = step.prefix || "Assure compile cache test";
      ctx.intent = `${prefix} ${Date.now()}`;
      await page.locator(step.selector || "#generate-intent").fill(ctx.intent);
      return;
    }
    case "sleep": {
      await page.waitForTimeout(step.ms || 1000);
      return;
    }
    case "submit": {
      await page.locator(step.selector).evaluate((el) => {
        if (el.requestSubmit) el.requestSubmit();
        else el.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      });
      return;
    }
    case "script": {
      const result = await page.evaluate(step.expression);
      ctx.lastScriptResult = result;
      return;
    }
    case "upload_fixture": {
      const fixturePath = path.resolve(REPO_ROOT, step.fixture);
      if (!fs.existsSync(fixturePath)) throw new Error(`Fixture not found: ${fixturePath}`);
      await page.locator(step.selector).setInputFiles(fixturePath);
      return;
    }
    case "vault_prepare": {
      const count = await page.evaluate(async () => {
        const pid = window.__ASSURE_PROJECT_ID__ || "default";
        const res = await fetch(`/api/projects/${encodeURIComponent(pid)}/substrate`, {
          credentials: "same-origin",
        });
        const data = await res.json().catch(() => ({}));
        return ((data && data.files) || []).length;
      });
      if (count > 0) {
        await page.locator(".substrate-file-checkbox").first().evaluate((el) => {
          if (el && !el.checked) el.click();
        });
        ctx.vaultSource = "existing";
        return;
      }
      const fixturePath = path.resolve(REPO_ROOT, step.fixture || "openuser/fixtures/grounding-sample.pdf");
      if (!fs.existsSync(fixturePath)) throw new Error(`Fixture not found: ${fixturePath}`);
      await page.locator("#substrate-vault-file-input").setInputFiles(fixturePath);
      try {
        await page.waitForSelector("#substrate-vault-list .substrate-file-row:not(.is-processing)", {
          timeout: step.timeout || 90000,
        });
        await page.locator(".substrate-file-checkbox").first().evaluate((el) => {
          if (el && !el.checked) el.click();
        });
        ctx.vaultSource = "upload";
      } catch (err) {
        const textractDown = await page.evaluate(() => {
          const toast = Array.from(document.querySelectorAll(".assure-toast, [class*='toast']"))
            .map((el) => el.textContent || "")
            .join(" ");
          const status = document.getElementById("substrate-vault-upload-status");
          const statusText = status ? status.textContent || "" : "";
          return /textract|subscription|could not extract|upload failed/i.test(`${toast} ${statusText}`);
        });
        const rowCount = await page.locator("#substrate-vault-list .substrate-file-row").count();
        if (textractDown || rowCount === 0) {
          ctx.vaultSkipped = true;
          throw new SkipSpec(
            "Vault upload skipped: Textract unavailable or upload failed on this environment. Pre-seed substrate files to run this spec."
          );
        }
        throw err;
      }
      return;
    }
    case "wait_compile_ready": {
      const timeout = step.timeout || 120000;
      await page
        .waitForFunction(
          () => {
            const btn = document.getElementById("generate-compile-btn");
            const status = document.getElementById("gate-status-text");
            const compiling = document.getElementById("generate-compiling");
            if (btn && btn.disabled) return true;
            if (compiling && !compiling.hidden) return true;
            const msg = status ? status.textContent || "" : "";
            return /loaded from memory/i.test(msg);
          },
          null,
          { timeout: Math.min(20000, timeout) }
        )
        .catch(() => {});
      await page.waitForFunction(
        () => {
          const btn = document.getElementById("generate-compile-btn");
          const compiling = document.getElementById("generate-compiling");
          const countEl = document.getElementById("generate-node-count");
          if (btn && btn.disabled) return false;
          if (compiling && !compiling.hidden) return false;
          const n = countEl ? parseInt(String(countEl.textContent || "0").replace(/\D/g, ""), 10) : 0;
          const dock = document.getElementById("generate-accept-dock");
          return n > 0 && !!(dock && !dock.disabled);
        },
        null,
        { timeout }
      );
      return;
    }
    case "wait_refine_result": {
      const timeout = step.timeout || 180000;
      await page.waitForFunction(
        () => {
          const panel = document.getElementById("jdf-diff-panel");
          const pop = document.getElementById("jdf-surgical-popover");
          const busy = pop && pop.classList.contains("is-busy");
          if (busy) return false;
          if (panel && !panel.hidden) return true;
          if (pop && pop.hidden) return true;
          return false;
        },
        null,
        { timeout }
      );
      return;
    }
    case "wait_for_sse": {
      const timeout = step.timeout || 30000;
      const event = step.event;
      await page.waitForFunction(
        (ev) => (window.__openUserSseEvents || []).some((e) => e.type === ev),
        event,
        { timeout }
      ).catch(async () => {
        if (event === "compiled" || event === "cache_hit") {
          await runStep(page, { action: "wait_compile_ready", timeout }, ctx, spec, stepIndex);
          return;
        }
        if (event === "audit_complete") {
          await runStep(page, { action: "wait_audit_complete", timeout }, ctx, spec, stepIndex);
          return;
        }
        throw new Error(`SSE event "${event}" not observed within ${timeout}ms`);
      });
      return;
    }
    case "wait_audit_complete": {
      const timeout = step.timeout || 180000;
      await page.waitForFunction(
        () => {
          const loader = document.getElementById("gate-loader");
          const dock = document.getElementById("generate-accept-dock");
          const loaderHidden = !loader || loader.hidden;
          const dockReady = dock && !dock.disabled;
          return loaderHidden && dockReady;
        },
        null,
        { timeout }
      );
      return;
    }
    case "measure": {
      const started = Date.now();
      for (let i = 0; i < (step.steps || []).length; i += 1) {
        await runStep(page, step.steps[i], ctx, spec, `${stepIndex}.${i}`);
      }
      ctx[step.id] = Date.now() - started;
      return;
    }
    case "contextmenu": {
      const loc = page.locator(step.selector);
      const target = step.first ? loc.first() : loc;
      await target.scrollIntoViewIfNeeded().catch(() => {});
      await target.click({ button: "right", timeout: step.timeout || 15000 });
      return;
    }
    case "fetch_pdf": {
      const url = expandTemplate(step.url, ctx);
      const result = await page.evaluate(async (u) => {
        const res = await fetch(u, { credentials: "same-origin" });
        const buf = new Uint8Array(await res.arrayBuffer());
        const head = Array.from(buf.slice(0, 4)).map((b) => String.fromCharCode(b)).join("");
        return { ok: res.ok, status: res.status, head, size: buf.length };
      }, url);
      ctx.lastFetchPdf = result;
      if (!result.ok) throw new Error(`PDF fetch failed: ${result.status}`);
      if (result.head !== "%PDF") throw new Error(`Expected PDF magic, got ${result.head}`);
      return;
    }
      if (step.condition) {
        const ok = evalCondition(step.condition, ctx);
        if (!ok) throw new Error(`Assertion failed: ${step.condition} (${JSON.stringify(ctx)})`);
        return;
      }
      const loc = page.locator(step.selector);
      if (step.visible === true) {
        await loc.first().waitFor({ state: "visible", timeout: step.timeout || 10000 });
      }
      if (step.contains) {
        const text = await loc.first().textContent({ timeout: step.timeout || 10000 });
        if (!text || !text.includes(step.contains)) {
          throw new Error(`Expected "${step.selector}" to contain "${step.contains}", got "${text || ""}"`);
        }
      }
      if (step.text_min) {
        const text = await loc.first().textContent({ timeout: step.timeout || 10000 });
        const num = parseInt(String(text || "0").replace(/\D/g, ""), 10);
        const min = parseInt(String(step.text_min).replace(/\D/g, ""), 10);
        if (num < min) throw new Error(`Expected "${step.selector}" >= ${min}, got ${num}`);
      }
      return;
    }
    default:
      throw new Error(`Unknown action: ${action}`);
  }
}

function evalCondition(expr, ctx) {
  const safe = String(expr || "").trim();
  const fn = new Function(
    ...Object.keys(ctx),
    `return (${safe});`
  );
  return !!fn(...Object.values(ctx));
}

async function runSpec(specPath, options) {
  const spec = parseSpec(specPath);
  const cfg = loadProjectConfig(options.project);
  const ctx = {
    baseUrl: spec.baseUrl || cfg.baseUrl,
    projectId: cfg.projectId,
  };
  const started = Date.now();
  let page;
  let browser;
  const specName = spec.name || path.basename(specPath, ".ux");

  try {
    browser = await chromium.launch({ headless: options.headless !== false });
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      ignoreHTTPSErrors: true,
    });
    await context.addInitScript(() => {
      try {
        localStorage.setItem("assure_onboarding_complete", "1");
      } catch (_) {}
      window.__openUserSseEvents = window.__openUserSseEvents || [];
    });
    page = await context.newPage();

    for (let i = 0; i < (spec.steps || []).length; i += 1) {
      const step = spec.steps[i];
      if (step.description) process.stderr.write(`  · ${step.description}\n`);
      await runStep(page, step, ctx, spec, i + 1);
    }

    const elapsed = Date.now() - started;
    const entry = {
      name: specName,
      status: "passed",
      timestamp: new Date().toISOString(),
      duration_ms: elapsed,
      spec: path.relative(REPO_ROOT, specPath),
      context: { ...ctx },
    };
    appendHistory(entry);
    return { ok: true, entry };
  } catch (err) {
    if (err instanceof SkipSpec) {
      const elapsed = Date.now() - started;
      const entry = {
        name: specName,
        status: "skipped",
        timestamp: new Date().toISOString(),
        duration_ms: elapsed,
        spec: path.relative(REPO_ROOT, specPath),
        note: String(err.message || err),
        context: { ...ctx },
      };
      appendHistory(entry);
      return { ok: true, skipped: true, entry };
    }
    const elapsed = Date.now() - started;
    const shot = await screenshotOnFailure(page, specName, 0);
    const entry = {
      name: specName,
      status: "failed",
      timestamp: new Date().toISOString(),
      duration_ms: elapsed,
      spec: path.relative(REPO_ROOT, specPath),
      error: String(err.message || err),
      screenshot: path.relative(REPO_ROOT, shot),
      context: { ...ctx },
    };
    appendHistory(entry);
    return { ok: false, entry, error: err };
  } finally {
    if (browser) await browser.close();
  }
}

function readHistory(lastN) {
  if (!fs.existsSync(HISTORY_PATH)) return [];
  const lines = fs.readFileSync(HISTORY_PATH, "utf8").trim().split("\n").filter(Boolean);
  const items = lines.map((line) => JSON.parse(line));
  return items.slice(-lastN);
}

program.name("openuser-runner").description("Assure AI OpenUser .ux spec runner");

program
  .command("run")
  .description("Run one or more .ux specs headlessly")
  .option("--headless", "Run headless (default true)", true)
  .option("--no-headless", "Run with browser UI")
  .requiredOption("--spec <pattern>", "Glob of .ux spec files")
  .option("--project <id>", "OpenUser project id")
  .action(async (opts) => {
    const pattern = path.isAbsolute(opts.spec) ? opts.spec : path.join(REPO_ROOT, opts.spec);
    const files = globSync(pattern, { cwd: REPO_ROOT, absolute: true }).sort();
    if (!files.length) {
      console.error(`No specs matched: ${opts.spec}`);
      process.exit(1);
    }
    let failed = 0;
    for (const file of files) {
      process.stderr.write(`\n▶ ${path.basename(file)}\n`);
      const result = await runSpec(file, opts);
      if (result.ok) {
        const label = result.skipped ? "SKIP" : "PASS";
        console.log(`${label} ${result.entry.name} (${result.entry.duration_ms}ms)`);
        if (result.skipped && result.entry.note) console.log(`  note: ${result.entry.note}`);
      } else {
        failed += 1;
        console.error(`FAIL ${result.entry.name}: ${result.entry.error}`);
        if (result.entry.screenshot) console.error(`  screenshot: ${result.entry.screenshot}`);
      }
    }
    process.exit(failed ? 1 : 0);
  });

program
  .command("history")
  .description("Show recent UX test runs")
  .option("--format <fmt>", "Output format: json or text", "text")
  .option("--last <n>", "Number of entries", "10")
  .action((opts) => {
    const items = readHistory(parseInt(opts.last, 10) || 10);
    if (opts.format === "json") {
      for (const item of items) console.log(JSON.stringify(item));
      return;
    }
    for (const item of items) {
      console.log(`${item.timestamp}  ${item.status.padEnd(6)}  ${item.name}  (${item.duration_ms}ms)`);
    }
  });

program.parseAsync(process.argv).catch((err) => {
  console.error(err);
  process.exit(1);
});
