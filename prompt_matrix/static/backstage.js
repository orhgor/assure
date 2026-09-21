(function () {
  "use strict";

  function t(key, fallback) {
    if (typeof window.__assureT === "function") {
      return window.__assureT(key, fallback);
    }
    return fallback || key;
  }

  function text(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function escapeHtml(raw) {
    return String(raw == null ? "" : raw)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function loadBudget() {
    var res = await fetch("/api/backstage/actions-budget", { credentials: "same-origin" });
    if (!res.ok) return;
    var data = await res.json();
    text("backstage-actions-limit", String(data.included_minutes != null ? data.included_minutes : 3000));
    if (data.used_minutes == null) {
      text("backstage-actions-used", "—");
      text(
        "backstage-actions-source",
        t(
          "backstage.actions.unknown_used",
          "Set GITHUB_ACTIONS_MINUTES_USED from the GitHub billing page, or add a billing token."
        )
      );
    } else {
      text("backstage-actions-used", String(data.used_minutes));
      text(
        "backstage-actions-source",
        t("backstage.actions.source", "Source") + ": " + String(data.source || "config")
      );
    }
    text(
      "backstage-actions-remaining",
      data.remaining_minutes == null ? "—" : String(data.remaining_minutes)
    );
    var policy = document.getElementById("backstage-actions-policy");
    if (policy && Array.isArray(data.policy)) {
      policy.innerHTML = data.policy
        .map(function (line) {
          return "<li>" + escapeHtml(line) + "</li>";
        })
        .join("");
    }
    var link = document.getElementById("backstage-billing-link");
    if (link && data.billing_url) link.setAttribute("href", data.billing_url);
  }

  async function loadFeedback() {
    var res = await fetch("/api/backstage/feedback", { credentials: "same-origin" });
    if (!res.ok) return;
    var data = await res.json();
    var rows = data.rows || [];
    var empty = document.getElementById("backstage-feedback-empty");
    var body = document.getElementById("backstage-feedback-body");
    if (!body) return;
    if (!rows.length) {
      if (empty) empty.hidden = false;
      body.innerHTML = "";
      return;
    }
    if (empty) empty.hidden = true;
    body.innerHTML = rows
      .map(function (row) {
        return (
          "<tr>" +
          "<td>" +
          escapeHtml(row.created_at || "") +
          "</td>" +
          "<td>" +
          escapeHtml(row.user_email || "") +
          "</td>" +
          "<td>" +
          escapeHtml(row.url || "") +
          "</td>" +
          "<td>" +
          escapeHtml(row.rating == null ? "" : row.rating) +
          "</td>" +
          "<td class=\"backstage-message\">" +
          escapeHtml(row.message || "") +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  loadBudget().catch(function () {});
  loadFeedback().catch(function () {});
})();
