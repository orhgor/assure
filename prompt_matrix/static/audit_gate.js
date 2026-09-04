(function (global) {
  "use strict";

  function t(key, fallback, vars) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, vars || {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    var s = fallback || key;
    if (vars) {
      Object.keys(vars).forEach(function (k) {
        s = s.replace(new RegExp("\\{" + k + "\\}", "g"), String(vars[k]));
      });
    }
    return s;
  }

  function plural(n) {
    return n === 1 ? "" : "s";
  }

  function normalizeAuditData(data) {
    data = data || {};
    var critiques = data.redhat_critiques || data.redhat_results || [];
    var z3 = data.z3_results || {};
    var z3Status = data.z3_status || z3.status || "SKIPPED";
    var redhatCount = data.redhat_count != null ? data.redhat_count : critiques.length;
    var gateStatus = data.gate_status;
    if (!gateStatus) {
      if (z3Status === "VIOLATION") gateStatus = "blocked";
      else if (redhatCount > 0) gateStatus = "review";
      else if (z3Status === "PASS") gateStatus = "pass";
      else gateStatus = "review";
    }
    return {
      ok: data.ok === true || (data.ok == null && z3Status === "PASS"),
      gate_status: gateStatus,
      z3_status: z3Status,
      z3_results: z3,
      redhat_critiques: critiques,
      redhat_count: redhatCount,
      node_count: data.node_count,
      lock_count: data.lock_count,
      nodes: data.nodes,
      locks: data.locks,
      document: data.document,
    };
  }

  function gateSummaryText(audit) {
    var z3 = audit.z3_results || {};
    var violCount = (z3.violations || []).length;
    if (audit.gate_status === "pass") {
      return t("audit.gate.pass", "Pre-Flight Gate — PASS. All locks verified, no logic gaps.");
    }
    if (audit.gate_status === "blocked") {
      return t(
        "audit.gate.blocked",
        "Pre-Flight Gate — BLOCKED. Z3 detected {n} violation(s) before export.",
        { n: violCount }
      );
    }
    if (audit.redhat_count > 0) {
      return t(
        "audit.gate.review",
        "Pre-Flight Gate — {n} Red-Hat finding(s) require review.",
        { n: audit.redhat_count }
      );
    }
    return t("audit.gate.warnings", "Pre-Flight Gate — Audit complete with warnings.");
  }

  function applyGateClasses(el, audit) {
    if (!el) return;
    el.classList.toggle("gate-pass", audit.gate_status === "pass");
    el.classList.toggle("gate-fail", audit.gate_status === "blocked");
    el.classList.toggle("gate-review", audit.gate_status === "review");
  }

  function updateGateBanner(bannerEl, textEl, data) {
    if (!bannerEl) return;
    var audit = normalizeAuditData(data);
    if (textEl) textEl.textContent = gateSummaryText(audit);
    bannerEl.classList.remove("hidden");
    applyGateClasses(bannerEl, audit);
  }

  function z3StatusLabel(z3Status) {
    if (z3Status === "PASS") {
      return t("jdf.truth.pass", "✅ Proof Passing").replace(/^[^\w]+/, "").trim() || "Proof Passing";
    }
    if (z3Status === "VIOLATION") {
      return t("jdf.truth.fail", "❌ Build Failing").replace(/^[^\w]+/, "").trim() || "Build Failing";
    }
    return z3Status || "Checked";
  }

  function z3BadgeText(audit) {
    var lockCount = audit.lock_count != null ? audit.lock_count : (audit.locks || []).length;
    return t(
      "audit.z3.badge",
      "🛡️ Z3 Ledger: {locks} Variable{plural} {status}",
      { locks: lockCount, plural: plural(lockCount), status: z3StatusLabel(audit.z3_status) }
    );
  }

  function redhatBadgeText(audit) {
    var n = audit.redhat_count || 0;
    return t("audit.redhat.badge", "🔍 Red-Hat: {n} Logic Gap{plural}", { n: n, plural: plural(n) });
  }

  function updateZ3Badge(el, data) {
    if (!el) return;
    var audit = normalizeAuditData(data);
    el.textContent = z3BadgeText(audit);
    el.classList.toggle("z3-pass", audit.z3_status === "PASS");
    el.classList.toggle("is-pass", audit.z3_status === "PASS");
    el.classList.toggle("z3-fail", audit.z3_status === "VIOLATION");
    el.classList.toggle("is-fail", audit.z3_status === "VIOLATION");
  }

  function updateRedhatBadge(el, data) {
    if (!el) return;
    el.textContent = redhatBadgeText(normalizeAuditData(data));
  }

  function renderWorkbenchAudit(data, opts) {
    opts = opts || {};
    var audit = normalizeAuditData(data);
    var z3El = opts.z3El;
    var redhatEl = opts.redhatEl;
    var gateBanner = opts.gateBanner;
    var gateText = opts.gateText;
    var jdf = global.__assureJdf;

    if (gateBanner) {
      updateGateBanner(gateBanner, gateText, audit);
    }

    if (z3El) {
      var z3 = audit.z3_results;
      var status = audit.z3_status;
      z3El.hidden = false;
      z3El.className = "gate-z3-status " + (status === "PASS" ? "is-pass" : status === "VIOLATION" ? "is-fail" : "");
      if (status === "PASS") {
        z3El.textContent =
          t("generate.z3.pass", "Z3 verification passed.") +
          (z3.locks_verified ? " (" + z3.locks_verified + " locks)" : "");
        syncCompilerStatus("verified");
        triggerLockAnimation(document.getElementById("compiler-status"));
      } else if (status === "VIOLATION") {
        var viol = (z3.violations || []).join(" ");
        z3El.textContent = t("generate.z3.fail", "Z3 found contradictions.") + (viol ? " " + viol : "");
        syncCompilerStatus("issues");
      } else {
        z3El.textContent = t("generate.z3.skipped", "Z3 verification skipped.");
      }
    }

    if (redhatEl) {
      redhatEl.innerHTML = "";
      var critiques = audit.redhat_critiques;
      if (critiques.length) {
        redhatEl.hidden = false;
        syncCompilerStatus("issues");
        critiques.forEach(function (c) {
          var li = document.createElement("li");
          var title = document.createElement("div");
          title.className = "redhat-preview-title";
          title.textContent = c.title || t("jdf.redhat.findings", "Red-hat findings");
          var body = document.createElement("div");
          body.textContent = c.content || "";
          li.appendChild(title);
          li.appendChild(body);
          redhatEl.appendChild(li);
        });
      } else {
        redhatEl.hidden = true;
      }
    }
  }

  var PROGRESS_KEYS = [
    ["audit.progress.compile", "Compiling JDF AST…"],
    ["audit.progress.locks", "Inferring truth-ledger locks…"],
    ["audit.progress.z3", "Running Z3 verification…"],
    ["audit.progress.redhat", "Red-Hat adversarial audit…"],
  ];

  var isFirstVerification = true;

  function triggerLockAnimation(el) {
    if (!el || !isFirstVerification) return;
    isFirstVerification = false;
    el.classList.remove("lock-animate");
    void el.offsetWidth;
    el.classList.add("lock-animate");
  }

  function syncCompilerStatus(state, detail) {
    if (typeof global.updateCompilerStatus === "function") {
      global.updateCompilerStatus(state, detail);
    }
  }

  function createVerificationTimeout(onTimeout, ms) {
    var delay = ms || 12000;
    var timer = setTimeout(function () {
      if (typeof onTimeout === "function") onTimeout();
    }, delay);
    return {
      clear: function () {
        clearTimeout(timer);
      },
    };
  }

  function timeoutRetryMessage() {
    return t("audit.timeout", "Verification timeout — click to retry");
  }

  global.AssureAuditGate = {
    normalize: normalizeAuditData,
    gateSummaryText: gateSummaryText,
    updateGateBanner: updateGateBanner,
    updateZ3Badge: updateZ3Badge,
    updateRedhatBadge: updateRedhatBadge,
    renderWorkbenchAudit: renderWorkbenchAudit,
    triggerLockAnimation: triggerLockAnimation,
    createVerificationTimeout: createVerificationTimeout,
    timeoutRetryMessage: timeoutRetryMessage,
    progressSteps: function () {
      return PROGRESS_KEYS.map(function (pair) {
        return t(pair[0], pair[1]);
      });
    },
  };
})(typeof window !== "undefined" ? window : this);
