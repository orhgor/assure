/**
 * Founder workbench — Red-Hat multi-pass telemetry feed (right drawer).
 */
(function (global) {
  "use strict";

  var POLL_MS = 2500;
  var pollTimer = null;
  var polling = false;
  var lastStatus = null;
  var renderedIds = Object.create(null);
  var feedEl = null;
  var pass2LoadingEl = null;

  function $(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function panel() {
    return $("drawer-redhat");
  }

  function projectId() {
    if (global.AssureFounderMode && typeof global.AssureFounderMode.getWorkspaceId === "function") {
      return global.AssureFounderMode.getWorkspaceId();
    }
    return global.__ASSURE_PROJECT_ID__ || "founder";
  }

  function isFounderShell() {
    return (
      document.body.classList.contains("founder-workbench") &&
      !document.body.classList.contains("legacy-workbench")
    );
  }

  function ensureFeedShell() {
    var el = panel();
    if (!el) return null;
    if (!feedEl || !el.contains(feedEl)) {
      el.innerHTML =
        '<div class="redhat-drawer-intro hint" id="redhat-drawer-intro"></div>' +
        '<div class="redhat-feed" id="redhat-feed" role="feed"></div>' +
        '<div id="pass2-loading" class="redhat-pass2-loading hidden" hidden aria-live="polite">' +
        '<span class="redhat-pass2-loading-dot" aria-hidden="true"></span>' +
        '<span class="redhat-pass2-loading-text" data-i18n="founder.redhat.pass2_loading">' +
        esc(translate("founder.redhat.pass2_loading", "Deep Adversarial Stress-Test Running...")) +
        "</span></div>';
      feedEl = $("redhat-feed");
      pass2LoadingEl = $("pass2-loading");
      renderedIds = Object.create(null);
    }
    return feedEl;
  }

  function setIntro(text) {
    var intro = $("redhat-drawer-intro");
    if (intro) intro.textContent = text || "";
  }

  function setPass2Loading(visible) {
    if (!pass2LoadingEl) return;
    pass2LoadingEl.classList.toggle("hidden", !visible);
    pass2LoadingEl.hidden = !visible;
  }

  function severityClass(severity, passNum) {
    var s = String(severity || "medium").toLowerCase();
    if (passNum === 2 || s === "high" || s === "fatal" || s === "critical") {
      return "is-pass2 is-fatal";
    }
    return "is-pass1 is-warning";
  }

  function passBadge(finding) {
    var passNum = parseInt(finding.pass, 10) || 1;
    if (passNum === 2) {
      return (
        '<span class="redhat-pass-badge is-pass2">' +
        esc(translate("founder.redhat.pass2_badge", "Pass 2: Adversarial")) +
        "</span>"
      );
    }
    var badge =
      '<span class="redhat-pass-badge is-pass1">' +
      esc(translate("founder.redhat.pass1_badge", "Pass 1: Scrutinizer")) +
      "</span>";
    if (finding.cache_hit) {
      badge +=
        ' <span class="redhat-cache-hit" title="' +
        esc(translate("founder.redhat.cache_hit", "Cache Hit")) +
        '">⚡ ' +
        esc(translate("founder.redhat.cache_hit", "Cache Hit")) +
        "</span>";
    }
    return badge;
  }

  function buildFindingCard(finding) {
    var id = String(finding.id || finding.title || Math.random());
    var passNum = parseInt(finding.pass, 10) || 1;
    var patch = String(finding.patch_html || finding.suggested_fix || "").trim();
    var blockHash = String(finding.block_hash || "");
    var actions =
      passNum === 2 && patch
        ? '<div class="redhat-finding-actions">' +
          '<button type="button" class="btn btn-outline btn-sm redhat-accept-fix" data-block-hash="' +
          esc(blockHash) +
          '" data-node-id="' +
          esc(String(finding.node_id || "")) +
          '" data-patch-html="' +
          esc(patch) +
          '">' +
          esc(translate("founder.runs.accept_fix", "Accept Fix")) +
          "</button>" +
          '<button type="button" class="btn btn-ghost btn-sm redhat-reject-fix" data-finding-id="' +
          esc(id) +
          '">' +
          esc(translate("founder.redhat.reject", "Reject")) +
          "</button></div>"
        : "";
    return (
      '<article class="drawer-redhat-card redhat-feed-card ' +
      severityClass(finding.severity, passNum) +
      '" data-finding-id="' +
      esc(id) +
      '" data-pass="' +
      passNum +
      '">' +
      '<div class="redhat-finding-badges">' +
      passBadge(finding) +
      "</div>" +
      "<h4>" +
      esc(finding.title || "Finding") +
      "</h4>" +
      "<p>" +
      esc(finding.content || "") +
      "</p>" +
      (finding.suggested_fix
        ? '<p class="drawer-redhat-fix hint"><strong>' +
          esc(translate("founder.drawer.suggested_fix", "Suggested fix")) +
          ":</strong> " +
          esc(finding.suggested_fix) +
          "</p>"
        : "") +
      actions +
      "</article>"
    );
  }

  function bindFindingActions(root) {
    if (!root) return;
    root.querySelectorAll(".redhat-accept-fix").forEach(function (btn) {
      if (btn.dataset.bound === "1") return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", function () {
        var patch = btn.getAttribute("data-patch-html") || "";
        var blockHash = btn.getAttribute("data-block-hash") || "";
        var nodeId = btn.getAttribute("data-node-id") || "";
        if (typeof global.applyRedHatFix === "function") {
          global.applyRedHatFix(blockHash, patch, btn, nodeId);
        }
      });
    });
    root.querySelectorAll(".redhat-reject-fix").forEach(function (btn) {
      if (btn.dataset.bound === "1") return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", function () {
        var card = btn.closest(".redhat-feed-card");
        if (card) {
          card.classList.add("is-rejected");
          card.hidden = true;
        }
      });
    });
  }

  function upsertFindingCard(finding) {
    var feed = ensureFeedShell();
    if (!feed || !finding) return;
    var id = String(finding.id || "");
    if (!id) return;
    if (renderedIds[id]) return;
    var wrapper = document.createElement("div");
    wrapper.innerHTML = buildFindingCard(finding);
    var card = wrapper.firstElementChild;
    if (!card) return;
    feed.appendChild(card);
    renderedIds[id] = true;
    bindFindingActions(card);
  }

  function renderRedHatFeed(status, findings) {
    ensureFeedShell();
    status = status || {};
    findings = findings || [];

    if (!findings.length && !status.pass1_complete && !status.pass2_running && !status.complete) {
      setIntro(
        translate("founder.redhat.loading_initial", "Starting multi-pass Red-Hat audit...")
      );
    } else {
      setIntro(
        translate("founder.redhat.mission_telemetry", "Mission Telemetry")
      );
    }

    findings.forEach(function (f) {
      upsertFindingCard(f);
    });

    setPass2Loading(!!status.pass2_running && !status.complete);

    if (status.complete && !findings.length) {
      var feed = feedEl;
      if (feed && !feed.querySelector(".redhat-feed-card")) {
        var empty = document.createElement("p");
        empty.className = "hint redhat-feed-empty";
        empty.textContent = translate(
          "founder.drawer.redhat_empty",
          "No Red-Hat findings for this run."
        );
        feed.appendChild(empty);
      }
    }

    if (status.error) {
      setIntro(translate("founder.redhat.error", "Audit error: {error}").replace("{error}", status.error));
    }

    lastStatus = status;
  }

  function stopPolling() {
    polling = false;
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function pollStatus() {
    if (!polling) return;
    var pid = projectId();
    fetch("/api/projects/" + encodeURIComponent(pid) + "/redhat/status", {
      credentials: "same-origin",
    })
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (!data || data.ok === false) return;
        renderRedHatFeed(data.status || {}, data.findings || []);
        if (data.status && data.status.complete) {
          stopPolling();
          document.dispatchEvent(
            new CustomEvent("assure:redhat-audit-complete", { detail: data })
          );
          if (global.AssureRunsStack && typeof global.AssureRunsStack.load === "function") {
            global.AssureRunsStack.load();
          }
          return;
        }
        pollTimer = setTimeout(pollStatus, POLL_MS);
      })
      .catch(function () {
        if (polling) pollTimer = setTimeout(pollStatus, POLL_MS);
      });
  }

  function startPolling() {
    stopPolling();
    polling = true;
    pollStatus();
  }

  function openDrawerForAudit() {
    if (global.AssureWorkbenchPanes && typeof global.AssureWorkbenchPanes.openDrawer === "function") {
      global.AssureWorkbenchPanes.openDrawer("redhat");
    }
    var title = $("workbench-drawer-title");
    if (title) {
      title.textContent = translate("founder.redhat.audit_log_title", "Red-Hat Audit Log");
    }
  }

  function triggerRedHatAudit(opts) {
    opts = opts || {};
    if (!isFounderShell()) return Promise.resolve({ ok: false });
    var pid = projectId();
    openDrawerForAudit();
    ensureFeedShell();
    setIntro(translate("founder.redhat.loading_initial", "Starting multi-pass Red-Hat audit..."));
    setPass2Loading(false);
    if (feedEl) {
      feedEl.innerHTML = "";
      renderedIds = Object.create(null);
    }

    return fetch("/api/projects/" + encodeURIComponent(pid) + "/redhat/auto", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: opts.runId || null }),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok && data.ok !== false, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          setIntro(
            (result.data && result.data.error) ||
              translate("founder.redhat.error", "Audit could not start.")
          );
          return result;
        }
        renderRedHatFeed(result.data.status || {}, result.data.findings || []);
        startPolling();
        return result;
      })
      .catch(function () {
        setIntro(translate("founder.redhat.error", "Audit could not start."));
        return { ok: false };
      });
  }

  function activate() {
    ensureFeedShell();
    if (polling) return;
    var pid = projectId();
    fetch("/api/projects/" + encodeURIComponent(pid) + "/redhat/status", {
      credentials: "same-origin",
    })
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (!data || data.ok === false) {
          renderRedHatFeed({}, []);
          return;
        }
        renderRedHatFeed(data.status || {}, data.findings || []);
        if (data.status && !data.status.complete && data.status.state !== "idle") {
          startPolling();
        }
      })
      .catch(function () {
        renderRedHatFeed({}, []);
      });
  }

  function render() {
    activate();
  }

  function bindTriggers() {
    var btn = $("executeRedHatBtn");
    if (btn && btn.dataset.redhatDrawerBound !== "1") {
      btn.dataset.redhatDrawerBound = "1";
      btn.addEventListener("click", function (e) {
        if (!isFounderShell()) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        triggerRedHatAudit();
      }, true);
    }
    var fullBtn = $("redhat-analyze-full-btn");
    if (fullBtn && fullBtn.dataset.redhatDrawerBound !== "1") {
      fullBtn.dataset.redhatDrawerBound = "1";
      fullBtn.addEventListener("click", function (e) {
        if (!isFounderShell()) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        triggerRedHatAudit();
      }, true);
    }
  }

  function init() {
    bindTriggers();
    document.addEventListener("assure:runs-updated", function () {
      if (
        global.AssureWorkbenchPanes &&
        global.AssureWorkbenchPanes.getDrawerMode &&
        global.AssureWorkbenchPanes.getDrawerMode() === "redhat" &&
        !polling
      ) {
        activate();
      }
    });
  }

  global.triggerRedHatAudit = triggerRedHatAudit;
  global.renderRedHatFeed = renderRedHatFeed;
  global.AssureRedhatDrawer = {
    activate: activate,
    render: render,
    init: init,
    triggerRedHatAudit: triggerRedHatAudit,
    stopPolling: stopPolling,
  };

  document.addEventListener("DOMContentLoaded", init);
})(window);
