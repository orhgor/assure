(function (global) {
  "use strict";

  function interpolate(s, vars) {
    if (!s || !vars) return s || "";
    Object.keys(vars).forEach(function (k) {
      s = String(s).replace(new RegExp("\\{" + k + "\\}", "g"), String(vars[k]));
    });
    return s;
  }

  function t(key, fallback, vars) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, vars || {});
    }
    // The workbench exposes only `__assureT`, which returns the catalog string
    // with its `{placeholders}` untouched. Interpolating here keeps a count from
    // rendering as "{checked}" on the page whose numbers this badge exists to
    // show; the fallback path was doing it already.
    var text =
      typeof global.__assureT === "function" ? global.__assureT(key, fallback) : fallback || key;
    return interpolate(text, vars);
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
      return t("audit.gate.pass", "Verified — ready to export.");
    }
    if (audit.gate_status === "blocked") {
      return t(
        "audit.gate.blocked",
        "Export blocked — Math Check found {n} issue(s).",
        { n: violCount }
      );
    }
    if (audit.redhat_count > 0) {
      return t(
        "audit.gate.review",
        "{n} stress test finding(s) need review before export.",
        { n: audit.redhat_count }
      );
    }
    return t("audit.gate.warnings", "Export allowed with warnings.");
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
      return t("jdf.truth.pass", "Verified");
    }
    if (z3Status === "VIOLATION") {
      return t("jdf.truth.fail", "Issues Found");
    }
    return z3Status || "Checked";
  }

  function z3BadgeText(audit) {
    var lockCount = audit.lock_count != null ? audit.lock_count : (audit.locks || []).length;
    return t(
      "audit.z3.badge",
      "🛡️ Math Check: {locks} number{plural} {status}",
      { locks: lockCount, plural: plural(lockCount), status: z3StatusLabel(audit.z3_status) }
    );
  }

  function redhatBadgeText(audit) {
    var n = audit.redhat_count || 0;
    return t("audit.redhat.badge", "🔍 Stress Test: {n} gap{plural}", { n: n, plural: plural(n) });
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

  /** The Math Check counters a payload actually carries, or null for a run that
   * predates them. `null` is not zero: a document compiled before this shipped
   * shows its status rather than a fabricated "0 checked". */
  function z3Counters(z3) {
    if (!z3 || z3.metrics_checked == null) return null;
    return {
      checked: z3.metrics_checked,
      verified: z3.verified || 0,
      violated: z3.violated || 0,
      unverified: z3.unverified || 0,
      byValue: z3.checked_by_value || 0,
      byRelational: z3.checked_by_relational || 0,
    };
  }

  function z3SummaryText(audit, counts) {
    var status = audit.z3_status;
    if (status === "PASS") {
      return t("audit.z3.summary_pass", "Verified — {checked} checked ({by_value} by value, {by_relational} by relationship)", {
        checked: counts.checked,
        by_value: counts.byValue,
        by_relational: counts.byRelational,
      });
    }
    if (status === "VIOLATION") {
      return t("audit.z3.summary_fail", "Issues Found — {violated} of {checked} checked disagree with the source", {
        violated: counts.violated,
        checked: counts.checked,
      });
    }
    return t("audit.z3.summary_skipped", "Math Check skipped — nothing was checked");
  }

  function z3CountsText(counts) {
    return t("audit.z3.counts", "{verified} verified · {violated} violated · {unverified} unverified", {
      verified: counts.verified,
      violated: counts.violated,
      unverified: counts.unverified,
    });
  }

  /** A violated claim: the draft's figure and the source's, side by side, then
   * Z3's explanation of the query that refuted it. */
  function z3FindingItem(record) {
    var li = document.createElement("li");
    li.className = "z3-finding";
    var detail = record.counterexample || {};
    var unit = detail.unit ? " " + detail.unit : "";

    var head = document.createElement("div");
    head.className = "z3-finding-metric";
    head.textContent = String(detail.metric || record.claim || "");
    li.appendChild(head);

    var values = document.createElement("div");
    values.className = "z3-finding-values";
    var claimed = document.createElement("span");
    claimed.className = "z3-value z3-value-draft";
    claimed.textContent = t("audit.z3.value_draft", "draft says {value}", {
      value: String(detail.claimed != null ? detail.claimed : "?") + unit,
    });
    var source = document.createElement("span");
    source.className = "z3-value z3-value-source";
    source.textContent = t("audit.z3.value_source", "source says {value}", {
      value: String(detail.source != null ? detail.source : "?") + unit,
    });
    values.appendChild(claimed);
    values.appendChild(source);
    li.appendChild(values);

    var why = document.createElement("div");
    why.className = "z3-finding-reason";
    why.textContent = [record.reason, record.evidence ? "· " + record.evidence : ""]
      .join(" ")
      .trim();
    li.appendChild(why);
    return li;
  }

  function z3UnverifiedItem(record) {
    var li = document.createElement("li");
    li.className = "z3-unverified";
    li.textContent = t("audit.z3.not_checked", "Not checked: {reason}", {
      reason: String(record.reason || record.claim || ""),
    });
    return li;
  }

  function renderMathCheck(el, audit) {
    var z3 = audit.z3_results || {};
    var status = audit.z3_status;
    var counts = z3Counters(z3);

    el.hidden = false;
    el.className =
      "gate-z3-status verification-badge " +
      (status === "PASS" ? "is-pass" : status === "VIOLATION" ? "is-fail" : "");
    el.innerHTML = "";

    var summary = document.createElement("div");
    summary.className = "z3-summary";
    summary.textContent = counts
      ? z3SummaryText(audit, counts)
      : t("generate.z3.skipped", "Math check skipped.");
    el.appendChild(summary);

    if (counts && counts.checked > 0) {
      var totals = document.createElement("div");
      totals.className = "z3-totals";
      totals.textContent = z3CountsText(counts);
      el.appendChild(totals);
    }

    var records = z3.claim_results || [];
    var findings = document.createElement("ul");
    findings.className = "z3-findings";
    var rendered = [];
    records.forEach(function (record) {
      // Structured findings where the translation gave us both numbers; the
      // tier-1 fallback has no counterexample and is rendered from its string.
      if (record && record.verdict === "VIOLATED" && record.counterexample) {
        findings.appendChild(z3FindingItem(record));
        if (record.violation) rendered.push(String(record.violation));
      }
    });
    // Violation strings not already shown in structured form: Tier 1's own
    // messages, and the fallback's value comparison.
    (z3.violations || []).forEach(function (text) {
      if (rendered.indexOf(String(text)) !== -1) return;
      var li = document.createElement("li");
      li.className = "z3-finding";
      li.textContent = String(text);
      findings.appendChild(li);
    });
    records.forEach(function (record) {
      if (record && record.tier === "unverified") findings.appendChild(z3UnverifiedItem(record));
    });
    if (findings.children.length) el.appendChild(findings);

    if (counts && counts.unverified > 0 && !records.length && z3.unverified_reason) {
      var reasons = document.createElement("ul");
      reasons.className = "z3-findings";
      reasons.appendChild(z3UnverifiedItem({ reason: z3.unverified_reason }));
      el.appendChild(reasons);
    }

    if (status === "PASS") {
      syncCompilerStatus("verified");
      triggerLockAnimation(document.getElementById("compiler-status"));
    } else if (status === "VIOLATION") {
      syncCompilerStatus("issues");
    }
  }

  function renderWorkbenchAudit(data, opts) {
    opts = opts || {};
    var audit = normalizeAuditData(data);
    var z3El = opts.z3El;
    var redhatEl = opts.redhatEl;
    var gateBanner = opts.gateBanner;
    var gateText = opts.gateText;

    if (gateBanner) {
      updateGateBanner(gateBanner, gateText, audit);
    }

    if (z3El) {
      renderMathCheck(z3El, audit);
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
          title.textContent = c.title || t("jdf.redhat.findings", "Stress Test Alert");
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
    ["audit.progress.compile", "Working…"],
    ["audit.progress.locks", "Inferring truth-ledger locks…"],
    ["audit.progress.z3", "Running math check…"],
    ["audit.progress.redhat", "Running stress test…"],
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
