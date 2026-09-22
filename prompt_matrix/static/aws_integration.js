/**
 * AWS / S3 integration banner and form (Sources panel).
 *
 * Asks GET /api/integrations/aws and shows one of three states, each taken
 * from a real probe (HeadBucket with the effective identity), never inferred:
 *   - no bucket configured   → warning: uploads stay on this instance's disk
 *   - bucket unreachable     → error with the reason (no credentials, denied, wrong key…)
 *   - reachable              → quiet status line (bucket, region, identity)
 * The form PUTs the entered IAM credentials and bucket; the server stores the
 * secret encrypted and answers with the probe result of those exact values.
 */
(function (global) {
  "use strict";

  var doc = global.document;

  function $(id) {
    return doc.getElementById(id);
  }

  function t(key, fallback, params) {
    if (typeof global.__assureTf === "function") return global.__assureTf(key, fallback, params || {});
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    if (!params) return fallback;
    return fallback.replace(/\{(\w+)\}/g, function (_, name) {
      return String(params[name] != null ? params[name] : "");
    });
  }

  function esc(v) {
    return String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  var state = { status: null };

  function render() {
    var box = $("aws-integration-banner");
    if (!box) return;
    var s = state.status;
    if (!s) {
      box.hidden = true;
      return;
    }
    var cls = "aws-banner-warn";
    var title;
    var detail = "";
    if (!s.configured) {
      title = t("aws.banner.local", "S3 is not configured — uploads and artifacts stay on this instance's disk.");
      detail = t("aws.banner.local_detail", "Connect an S3 bucket with IAM credentials so every replica and worker sees the same files.");
    } else if (s.reachable === false) {
      cls = "aws-banner-bad";
      title = t("aws.banner.unreachable", "S3 bucket {bucket} is not reachable.", { bucket: s.bucket });
      detail = s.error || "";
    } else {
      cls = "aws-banner-ok";
      title = t("aws.banner.ok", "S3 connected: {bucket} ({region})", { bucket: s.bucket, region: s.region || "—" });
      var src = { env: t("aws.source.env", ".env credentials"), database: t("aws.source.database", "credentials entered here"), role: t("aws.source.role", "machine IAM role") }[s.credential_source] || s.credential_source;
      detail = src + (s.identity && s.identity.arn ? " · " + s.identity.arn : "") + (s.access_key_id_hint ? " · " + s.access_key_id_hint : "");
    }
    box.className = "aws-banner " + cls;
    box.hidden = false;
    box.innerHTML =
      '<div class="aws-banner-text"><strong>' + esc(title) + "</strong>" + (detail ? '<span class="hint">' + esc(detail) + "</span>" : "") + "</div>" +
      '<button type="button" class="btn btn-sm ' + (cls === "aws-banner-ok" ? "btn-ghost" : "btn-primary") + '" id="aws-integration-open">' +
      esc(cls === "aws-banner-ok" ? t("aws.action.edit", "Edit") : t("aws.action.connect", "Connect IAM")) +
      "</button>";
    var btn = $("aws-integration-open");
    if (btn) btn.addEventListener("click", openForm);
  }

  async function refresh(probe) {
    try {
      var res = await global.fetch("/api/integrations/aws" + (probe === false ? "?probe=0" : ""), { credentials: "same-origin" });
      var data = await res.json();
      if (data && data.ok) state.status = data;
    } catch (_) {}
    render();
    return state.status;
  }

  function openForm() {
    var modal = $("aws-integration-modal");
    if (!modal) return;
    var s = state.status || {};
    $("aws-bucket").value = s.bucket || "";
    $("aws-region").value = s.region || "";
    $("aws-prefix").value = s.prefix || "assure/";
    $("aws-access-key").value = "";
    $("aws-secret-key").value = "";
    $("aws-access-key").placeholder = s.access_key_id_hint ? s.access_key_id_hint + " " + t("aws.form.saved", "(saved)") : "AKIA…";
    $("aws-form-result").textContent = "";
    $("aws-form-result").className = "hint";
    modal.hidden = false;
    $("aws-bucket").focus();
  }

  function closeForm() {
    var modal = $("aws-integration-modal");
    if (modal) modal.hidden = true;
  }

  async function submit(e) {
    e.preventDefault();
    var result = $("aws-form-result");
    var submitBtn = $("aws-form-submit");
    result.textContent = t("aws.form.testing", "Saving and testing against S3…");
    result.className = "hint";
    submitBtn.disabled = true;
    try {
      var res = await global.fetch("/api/integrations/aws", {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bucket: $("aws-bucket").value.trim(),
          region: $("aws-region").value.trim(),
          prefix: $("aws-prefix").value.trim(),
          access_key_id: $("aws-access-key").value.trim(),
          secret_access_key: $("aws-secret-key").value,
        }),
      });
      var data = await res.json();
      if (!data.ok) throw new Error(data.error || "Save failed");
      state.status = data;
      render();
      if (data.reachable) {
        result.textContent = t("aws.form.ok", "Connected. Bucket reachable as {arn}.", { arn: (data.identity && data.identity.arn) || data.credential_source });
        result.className = "hint aws-result-ok";
        global.setTimeout(closeForm, 1200);
      } else {
        result.textContent = t("aws.form.saved_unreachable", "Saved, but the bucket is not reachable: {error}", { error: data.error || "" });
        result.className = "hint aws-result-bad";
      }
    } catch (err) {
      result.textContent = String((err && err.message) || err);
      result.className = "hint aws-result-bad";
    } finally {
      submitBtn.disabled = false;
    }
  }

  function bind() {
    var form = $("aws-integration-form");
    if (form && !form.dataset.bound) {
      form.dataset.bound = "1";
      form.addEventListener("submit", submit);
    }
    var close = $("aws-integration-close");
    if (close) close.addEventListener("click", closeForm);
    var backdrop = $("aws-integration-backdrop");
    if (backdrop) backdrop.addEventListener("click", closeForm);
  }

  function init() {
    if (!$("aws-integration-banner")) return;
    bind();
    refresh(true);
  }

  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", init);
  else init();

  global.AssureAwsIntegration = { refresh: refresh, open: openForm };
})(window);
