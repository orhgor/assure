/**
 * Surgical diff viewer — renders a computed_diff as inline changes.
 *
 * The proposal's diff arrives as [{op: "equal"|"insert"|"delete", text}], already
 * word-level and already normalised by the backend. This module only paints it;
 * it never computes a diff, because a second implementation here would drift
 * from the one the proposal was verified against.
 *
 * Three rendering decisions are load-bearing and each has a comment where it is
 * applied: pre-wrap (inline elements otherwise collapse the spaces between
 * them), horizontal-only padding (vertical padding jitters the line box), and a
 * non-breaking space in empty elements (a deleted space otherwise paints
 * nothing).
 */
(function (global) {
  "use strict";

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  /**
   * The diff as an HTML string.
   *
   * Every segment is escaped. The proposal's text is model output, so it is
   * untrusted in exactly the way source material is.
   */
  function render(diff) {
    var ops = Array.isArray(diff) ? diff : [];
    if (!ops.length) return "";
    var parts = [];
    for (var i = 0; i < ops.length; i++) {
      var op = ops[i] || {};
      var text = esc(op.text || "");
      var kind = op.op;
      if (kind === "delete") {
        parts.push('<del class="diff-del surgical-diff-del">' + text + "</del>");
      } else if (kind === "insert") {
        parts.push('<ins class="diff-ins surgical-diff-ins">' + text + "</ins>");
      } else {
        parts.push("<span>" + text + "</span>");
      }
      // The backend joins tokens with a single space, so the separator is
      // restored here rather than inside a <del>/<ins> — a space inside an
      // element is underlined, struck through and highlighted with it.
      if (i < ops.length - 1) parts.push(" ");
    }
    return parts.join("");
  }

  /** "N words removed, M added" — or "" when nothing changed. */
  function summarize(summary, t) {
    var s = summary || {};
    if (!s.changed) return "";
    var translate = typeof t === "function" ? t : function (k, f) { return f || k; };
    var bits = [];
    if (s.deleted_words) {
      bits.push(translate("surgical.diff.removed", "{n} words removed").replace("{n}", s.deleted_words));
    }
    if (s.inserted_words) {
      bits.push(translate("surgical.diff.added", "{n} words added").replace("{n}", s.inserted_words));
    }
    return bits.join(", ");
  }

  /**
   * The verification banner for a proposal.
   *
   * "Error" is deliberately not rendered like "Pass": a check that could not run
   * is not a check that succeeded, and the reader has to be able to tell them
   * apart to make the apply/force-apply decision.
   */
  function verificationBanner(status, reason, t) {
    var translate = typeof t === "function" ? t : function (k, f) { return f || k; };
    var s = String(status || "").toLowerCase();
    if (s === "pass") {
      return {
        className: "surgical-verify is-pass",
        icon: "\u2713",
        label: translate("surgical.verify.pass", "Verified against sources"),
        detail: "",
      };
    }
    if (s === "fail") {
      return {
        className: "surgical-verify is-fail",
        icon: "\u26a0",
        label: translate(
          "surgical.verify.fail",
          "Warning: proposed edit fails source verification or introduces unsupported claims"
        ),
        detail: String(reason || ""),
      };
    }
    return {
      className: "surgical-verify is-error",
      icon: "\u25cb",
      label: translate("surgical.verify.error", "Not verified"),
      detail: String(reason || ""),
    };
  }

  /** Whether applying a proposal in this state needs the force path. */
  function requiresForce(status) {
    return String(status || "").toLowerCase() === "fail";
  }

  /**
   * The proposal rendered into a container element.
   *
   * Returns the banner state so the caller does not recompute it.
   */
  function renderProposal(container, proposal, t) {
    if (!container) return null;
    var p = proposal || {};
    var banner = verificationBanner(p.verification_status, p.verification_reason, t);
    var diffHtml = render(p.computed_diff);
    var summary = summarize(p.diff_summary, t);
    container.innerHTML =
      '<div class="surgical-proposal">' +
      '<div class="' + banner.className + '">' +
      '<span class="surgical-verify__icon" aria-hidden="true">' + banner.icon + "</span>" +
      '<span class="surgical-verify__label">' + esc(banner.label) + "</span>" +
      (banner.detail ? '<p class="surgical-verify__detail">' + esc(banner.detail) + "</p>" : "") +
      "</div>" +
      (summary ? '<p class="surgical-diff-summary">' + esc(summary) + "</p>" : "") +
      '<div class="surgical-diff-viewer">' + diffHtml + "</div>" +
      "</div>";
    return banner;
  }

  /** Lock or release the target node while the proposal is in flight. */
  function setNodeLocked(nodeId, locked) {
    var el =
      global.document &&
      global.document.querySelector &&
      global.document.querySelector('[data-node-id="' + nodeId + '"]');
    if (!el) return;
    if (locked) {
      el.classList.add("is-surgical-pending");
      el.setAttribute("aria-busy", "true");
    } else {
      el.classList.remove("is-surgical-pending");
      el.removeAttribute("aria-busy");
    }
  }

  global.AssureSurgicalDiff = {
    render: render,
    summarize: summarize,
    verificationBanner: verificationBanner,
    requiresForce: requiresForce,
    renderProposal: renderProposal,
    setNodeLocked: setNodeLocked,
  };
})(typeof window !== "undefined" ? window : this);
