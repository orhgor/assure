/**
 * Ingest jobs: the processing panel and the shared "wait for a queued upload"
 * helper.
 *
 * Uploads no longer finish inside the request — the web tier answers 202 with
 * a task id and a job id, and a worker parses, OCRs, verifies and persists the
 * document. This module gives the workbench one place to watch that happen:
 *
 *   AssureIngestJobs.awaitTask(taskId)   → Promise resolving with the task's
 *                                          result payload once it is terminal
 *   AssureIngestJobs.refresh()           → repaint the processing panel from
 *                                          GET /api/projects/<id>/ingest-jobs
 *   AssureIngestJobs.track()             → start polling while jobs are active
 *
 * Every figure shown comes from the job row the worker wrote (parser, pages,
 * OCR confidence, Z3 verdict, Red-Hat status, duration). Nothing is estimated
 * client-side.
 */
(function (global) {
  "use strict";

  var doc = global.document;
  var POLL_MS = 2500;
  var timer = null;
  var lastPayload = null;

  function $(id) {
    return doc.getElementById(id);
  }

  function projectId() {
    if (global.AssureFounderMode && typeof global.AssureFounderMode.getWorkspaceId === "function") {
      return global.AssureFounderMode.getWorkspaceId();
    }
    return global.__ASSURE_PROJECT_ID__ || "default";
  }

  function t(key, fallback, params) {
    if (typeof global.__assureTf === "function") return global.__assureTf(key, fallback, params || {});
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    if (!params) return fallback;
    return fallback.replace(/\{(\w+)\}/g, function (_, name) {
      return String(params[name] != null ? params[name] : "");
    });
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function sleep(ms) {
    return new Promise(function (resolve) {
      global.setTimeout(resolve, ms);
    });
  }

  /** Poll GET /api/tasks/<id> until the task is terminal; resolve with its payload. */
  async function awaitTask(taskId, opts) {
    opts = opts || {};
    var interval = opts.intervalMs || 2000;
    var deadline = Date.now() + (opts.timeoutMs || 15 * 60 * 1000);
    while (Date.now() < deadline) {
      var res = await global.fetch("/api/tasks/" + encodeURIComponent(taskId), { credentials: "same-origin" });
      var data = await res.json().catch(function () {
        return {};
      });
      var status = String(data.status || "").toLowerCase();
      if (typeof opts.onUpdate === "function") opts.onUpdate(data);
      if (status === "success") return data.result || {};
      if (status === "failure" || status === "skipped") {
        var err = new Error(
          (data.result && data.result.error) || data.error || (data.job && data.job.error) || t("ingest.failed", "Processing failed")
        );
        err.task = data;
        throw err;
      }
      await sleep(interval);
    }
    throw new Error(t("ingest.timeout", "Processing is taking longer than expected. It will continue in the background."));
  }

  var STAGE_ORDER = ["queued", "fetching", "parsing", "verifying", "persisting", "done"];

  function stageLabel(stage) {
    var labels = {
      queued: t("ingest.stage.queued", "Queued"),
      fetching: t("ingest.stage.fetching", "Fetching"),
      parsing: t("ingest.stage.parsing", "Parsing"),
      verifying: t("ingest.stage.verifying", "Verifying"),
      persisting: t("ingest.stage.persisting", "Saving"),
      done: t("ingest.stage.done", "Done"),
      failed: t("ingest.stage.failed", "Failed"),
      skipped: t("ingest.stage.skipped", "Skipped"),
    };
    return labels[stage] || stage;
  }

  function parserLabel(job) {
    var name = String(job.parser_name || "");
    if (!name) return "";
    if (name.indexOf("tesseract") >= 0) return t("ingest.parser.ocr", "JDF + OCR");
    if (name === "textract") return "Textract";
    if (name === "pymupdf") return "PyMuPDF";
    if (name.indexOf("jdf") >= 0) return "JDF CI";
    return name;
  }

  function z3Badge(job) {
    var status = String(job.z3_status || "");
    if (!status) {
      return job.status === "done"
        ? '<span class="ingest-badge ingest-badge-muted">' + esc(t("ingest.z3.none", "Z3: not run")) + "</span>"
        : "";
    }
    var cls = "ingest-badge-muted";
    var text = "Z3 " + status;
    if (status === "PASS") cls = "ingest-badge-ok";
    else if (status === "VIOLATION") {
      cls = "ingest-badge-bad";
      text = t("ingest.z3.violations", "Z3: {n} contradiction(s)", { n: job.z3_violation_count || 0 });
    } else if (status === "TIMEOUT" || status === "ERROR") {
      cls = "ingest-badge-warn";
      text = t("ingest.z3.unverified", "Z3: unverified ({status})", { status: status });
    }
    return '<span class="ingest-badge ' + cls + '">' + esc(text) + "</span>";
  }

  function progressHtml(job) {
    var idx = STAGE_ORDER.indexOf(job.status);
    var failed = job.status === "failed" || job.status === "skipped";
    var html = '<ol class="ingest-progress" aria-label="' + esc(t("ingest.progress", "Processing stages")) + '">';
    STAGE_ORDER.forEach(function (stage, i) {
      var cls = "";
      if (failed) cls = i <= STAGE_ORDER.indexOf(lastCompletedStage(job)) ? "is-done" : "";
      else if (i < idx) cls = "is-done";
      else if (i === idx) cls = job.status === "done" ? "is-done" : "is-current";
      html += '<li class="' + cls + '" title="' + esc(stageLabel(stage)) + '"></li>';
    });
    html += "</ol>";
    return html;
  }

  function lastCompletedStage(job) {
    var history = job.stage_history || [];
    for (var i = history.length - 1; i >= 0; i--) {
      if (STAGE_ORDER.indexOf(history[i].stage) >= 0) return history[i].stage;
    }
    return "queued";
  }

  function formatDuration(ms) {
    if (ms == null) return "";
    var n = Number(ms);
    if (n < 1000) return n + " ms";
    return (n / 1000).toFixed(1) + " s";
  }

  function rowHtml(job) {
    var meta = [];
    var parser = parserLabel(job);
    if (parser) meta.push(esc(parser));
    if (job.page_count != null) meta.push(esc(t("ingest.pages", "{n} pages", { n: job.page_count })));
    if (job.ocr_confidence != null) {
      meta.push(esc(t("ingest.ocr_confidence", "OCR {pct}%", { pct: Math.round(Number(job.ocr_confidence) * 100) })));
    }
    if (job.duration_ms != null) meta.push(esc(formatDuration(job.duration_ms)));
    var stateCls = job.status === "done" ? "is-done" : job.status === "failed" ? "is-failed" : "is-active";
    var actions = "";
    if (job.status === "done" && job.revision_id) {
      actions +=
        '<button type="button" class="btn btn-sm btn-ghost ingest-report-btn" data-job="' + esc(job.job_id) + '">' +
        esc(t("ingest.report", "Report")) +
        "</button>";
    }
    if (job.status === "failed" || job.status === "skipped") {
      actions +=
        '<button type="button" class="btn btn-sm btn-outline ingest-retry-btn" data-job="' + esc(job.job_id) + '">' +
        esc(t("ingest.retry", "Retry")) +
        "</button>";
    }
    return (
      '<li class="ingest-job ' + stateCls + '" data-job="' + esc(job.job_id) + '">' +
      '<div class="ingest-job-head">' +
      '<span class="ingest-job-name" title="' + esc(job.filename) + '">' + esc(job.filename) + "</span>" +
      '<span class="ingest-job-stage">' + esc(stageLabel(job.status)) + "</span>" +
      "</div>" +
      progressHtml(job) +
      '<div class="ingest-job-meta">' +
      (meta.length ? '<span class="ingest-job-facts">' + meta.join(" · ") + "</span>" : "") +
      (job.redhat_status ? '<span class="ingest-badge ingest-badge-muted">Red-Hat ' + esc(job.redhat_status) + "</span>" : "") +
      z3Badge(job) +
      "</div>" +
      (job.error ? '<p class="ingest-job-error">' + esc(job.error) + "</p>" : "") +
      (actions ? '<div class="ingest-job-actions">' + actions + "</div>" : "") +
      '<div class="ingest-job-report" hidden></div>' +
      "</li>"
    );
  }

  function render(payload) {
    lastPayload = payload;
    var list = $("ingest-jobs-list");
    var empty = $("ingest-jobs-empty");
    var summary = $("ingest-jobs-summary");
    if (!list) return;
    var jobs = (payload && payload.jobs) || [];
    list.innerHTML = jobs.map(rowHtml).join("");
    if (empty) empty.hidden = jobs.length > 0;
    if (summary && payload && payload.stats) {
      var s = payload.stats;
      summary.textContent = t("ingest.summary", "{active} processing · {done} done · {failed} failed", {
        active: s.active || 0,
        done: s.done || 0,
        failed: s.failed || 0,
      });
      var z3 = s.z3 || {};
      var z3Text = [];
      if (z3.PASS) z3Text.push(t("ingest.z3.pass_count", "{n} verified", { n: z3.PASS }));
      if (z3.VIOLATION) z3Text.push(t("ingest.z3.violation_count", "{n} with contradictions", { n: z3.VIOLATION }));
      if (z3Text.length) summary.textContent += " · " + z3Text.join(", ");
    }
    var panel = $("ingest-jobs-panel");
    if (panel) panel.hidden = jobs.length === 0;
  }

  async function refresh() {
    var res = await global.fetch(
      "/api/projects/" + encodeURIComponent(projectId()) + "/ingest-jobs?limit=25",
      { credentials: "same-origin" }
    );
    if (!res.ok) return null;
    var data = await res.json().catch(function () {
      return null;
    });
    if (data && data.ok) render(data);
    return data;
  }

  function track() {
    if (timer) return;
    var tick = async function () {
      timer = null;
      var data = null;
      try {
        data = await refresh();
      } catch (_) {}
      if (data && data.active) timer = global.setTimeout(tick, POLL_MS);
      else if (typeof global.AssureSubstrateVault === "object" && global.AssureSubstrateVault && typeof global.AssureSubstrateVault.load === "function") {
        try { global.AssureSubstrateVault.load(); } catch (_) {}
      }
    };
    timer = global.setTimeout(tick, 400);
  }

  async function showReport(jobId, container) {
    var res = await global.fetch(
      "/api/projects/" + encodeURIComponent(projectId()) + "/ingest-jobs/" + encodeURIComponent(jobId) + "/report",
      { credentials: "same-origin" }
    );
    var data = await res.json().catch(function () {
      return {};
    });
    if (!data.ok) {
      container.innerHTML = '<p class="ingest-job-error">' + esc(data.error || "Report unavailable") + "</p>";
      container.hidden = false;
      return;
    }
    var v = data.verification || {};
    var z3 = v.z3 || {};
    var violations = z3.violations || [];
    var html = '<dl class="ingest-report">';
    html += "<dt>" + esc(t("ingest.report.revision", "Revision")) + "</dt><dd>v" + esc(v.version) + " · " + esc(v.node_count) + " " + esc(t("ingest.report.nodes", "nodes")) + "</dd>";
    html += "<dt>Z3</dt><dd>" + esc(z3.z3_status || t("ingest.z3.none", "Z3: not run")) + (z3.checked_at ? " · " + esc(z3.checked_at) : "") + "</dd>";
    html += "<dt>Red-Hat</dt><dd>" + esc(t("ingest.report.findings", "{n} finding(s)", { n: v.redhat_finding_count || 0 })) + "</dd>";
    if (data.omp) html += "<dt>OMP</dt><dd>" + esc(data.omp.artifact_id) + "</dd>";
    html += "</dl>";
    if (violations.length) {
      html += '<ul class="ingest-violations">';
      violations.forEach(function (item) {
        html += "<li>" + esc(item.message || item.detail || item.title || JSON.stringify(item)) + "</li>";
      });
      html += "</ul>";
    }
    container.innerHTML = html;
    container.hidden = false;
  }

  async function retry(jobId) {
    var res = await global.fetch(
      "/api/projects/" + encodeURIComponent(projectId()) + "/ingest-jobs/" + encodeURIComponent(jobId) + "/retry",
      { method: "POST", credentials: "same-origin" }
    );
    var data = await res.json().catch(function () {
      return {};
    });
    if (!res.ok && global.AssureToast) global.AssureToast.show(data.error || t("ingest.retry_failed", "Retry failed"), "error");
    track();
  }

  function bind() {
    var list = $("ingest-jobs-list");
    if (!list || list.dataset.bound) return;
    list.dataset.bound = "1";
    list.addEventListener("click", function (e) {
      var btn = e.target.closest("button");
      if (!btn) return;
      var jobId = btn.getAttribute("data-job");
      if (!jobId) return;
      if (btn.classList.contains("ingest-report-btn")) {
        var row = btn.closest(".ingest-job");
        var container = row && row.querySelector(".ingest-job-report");
        if (container) {
          if (!container.hidden) container.hidden = true;
          else showReport(jobId, container).catch(function () {});
        }
      } else if (btn.classList.contains("ingest-retry-btn")) {
        retry(jobId).catch(function () {});
      }
    });
    var refreshBtn = $("ingest-jobs-refresh");
    if (refreshBtn) refreshBtn.addEventListener("click", function () { track(); });
  }

  function init() {
    bind();
    track();
  }

  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", init);
  else init();

  global.AssureIngestJobs = { awaitTask: awaitTask, refresh: refresh, track: track, render: render, last: function () { return lastPayload; } };
})(window);
