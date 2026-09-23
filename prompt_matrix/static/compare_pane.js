/**
 * Compare pane — bounded staging shell with loading, error, and close paths.
 * Renders into #compare-pane .staging-canvas using golden-path DOM (.model-pane, .diff-highlight).
 */
(function (global) {
  "use strict";

  var ERROR_MARKERS = [
    "error:",
    "error ",
    "run aborted",
    "timeout",
    "failed",
    "exception",
    "traceback",
  ];
  var submitting = false;

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function translatef(key, fallback, params) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, params || {});
    }
    var out = translate(key, fallback);
    Object.keys(params || {}).forEach(function (k) {
      out = out.replace("{" + k + "}", String(params[k]));
    });
    return out;
  }

  function toast(message, kind) {
    if (global.AssureToast && typeof global.AssureToast.show === "function") {
      global.AssureToast.show(message, kind || "error");
    }
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function isErrorLikeText(text) {
    var lower = String(text || "").trim().toLowerCase();
    if (!lower) return false;
    for (var i = 0; i < ERROR_MARKERS.length; i += 1) {
      if (lower.indexOf(ERROR_MARKERS[i]) !== -1) return true;
    }
    return false;
  }

  function isSafeToMerge(text) {
    var raw = String(text || "").trim();
    return raw.length > 0 && !isErrorLikeText(raw);
  }

  function isSafeDivergence(d) {
    if (!d) return false;
    var text = String(d.a_text || d.b_text || "").trim();
    return isSafeToMerge(text);
  }

  function modelHasFailed(model) {
    if (!model) return true;
    if (model.error) return true;
    var text = String(model.text || "").trim();
    if (!text) return true;
    return isErrorLikeText(text);
  }

  function paneRoot() {
    return document.getElementById("compare-pane");
  }

  function emptyHint() {
    return document.getElementById("compare-pane-empty");
  }

  function stagingCanvas() {
    var root = paneRoot();
    return root ? root.querySelector(".staging-canvas") : null;
  }

  function bindCloseButton() {
    var btn = document.getElementById("compare-close");
    if (!btn || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      close();
    });
  }

  function openShell() {
    var root = paneRoot();
    var hint = emptyHint();
    if (hint) {
      hint.hidden = true;
      hint.setAttribute("aria-hidden", "true");
    }
    if (!root) return;
    root.classList.remove("hidden");
    root.hidden = false;
    root.setAttribute("aria-hidden", "false");
  }

  function showLoading(label) {
    openShell();
    var root = paneRoot();
    if (!root) return;
    var models = null;
    root.innerHTML =
      '<div class="compare-header">' +
      '<span class="compare-label">' +
      escapeHtml(label || translate("founder.compare.dispatching", "Dispatching to 2 models…")) +
      "</span>" +
      '<button type="button" id="compare-close" class="compare-close-btn" aria-label="' +
      escapeHtml(translate("founder.compare.close", "Close compare")) +
      '">✕</button>' +
      "</div>" +
      '<div class="staging-canvas compare-grid" aria-busy="true">' +
      '<div class="staging-canvas-row">' +
      '<div class="model-pane p-4" data-model="claude">' +
      '<div class="compare-loading">' +
      escapeHtml(translate("founder.compare.model_a_waiting", "Model A: waiting…")) +
      "</div></div>" +
      '<div class="model-pane p-4" data-model="secondary">' +
      '<div class="compare-loading">' +
      escapeHtml(translate("founder.compare.model_b_waiting", "Model B: waiting…")) +
      "</div></div></div></div>";
    bindCloseButton();
  }

  function renderFatalError(message) {
    openShell();
    var root = paneRoot();
    if (!root) return;
    var msg = escapeHtml(message || translate("founder.compare.unknown_error", "Unknown error"));
    root.innerHTML =
      '<div class="compare-header">' +
      '<span class="compare-label">' +
      escapeHtml(translate("founder.compare.failed_title", "Compare failed")) +
      "</span>" +
      '<button type="button" id="compare-close" class="compare-close-btn" aria-label="' +
      escapeHtml(translate("founder.compare.close", "Close compare")) +
      '">✕</button>' +
      "</div>" +
      '<div class="staging-canvas">' +
      '<div class="compare-error-card" role="alert">' +
      '<div class="compare-error-title">' +
      escapeHtml(translate("founder.compare.could_not_compare", "Could not compare models")) +
      "</div>" +
      '<div class="compare-error-body">' +
      msg +
      "</div>" +
      '<div class="compare-error-hint" data-i18n="founder.compare.retry_hint">' +
      escapeHtml(
        translate("founder.compare.retry_hint", "Try again, or use the Main document directly.")
      ) +
      "</div></div></div>";
    bindCloseButton();
    toast(msg, "error");
  }

  function normalizePayload(data) {
    if (!data) return data;
    if (data.models && data.models.claude && data.models.secondary) return data;
    var models = data.models || {};
    var modelA = data.model_a || {};
    var modelB = data.model_b || {};
    return {
      status: data.status || "success",
      stack: data.stack,
      models: {
        claude: {
          name:
            (models.claude && models.claude.name) ||
            modelA.name ||
            modelA.model ||
            translate("founder.compare.model_a_default", "Model A"),
          text: (models.claude && models.claude.text) || modelA.text || "",
          error: (models.claude && models.claude.error) || modelA.error || null,
        },
        secondary: {
          name:
            (models.secondary && models.secondary.name) ||
            modelB.name ||
            modelB.model ||
            translate("founder.compare.model_b_default", "Model B"),
          text: (models.secondary && models.secondary.text) || modelB.text || "",
          error: (models.secondary && models.secondary.error) || modelB.error || null,
        },
      },
    };
  }

  function close() {
    var root = paneRoot();
    if (root) {
      root.classList.add("hidden");
      root.hidden = true;
      root.setAttribute("aria-hidden", "true");
      root.innerHTML = '<div class="staging-canvas"></div>';
    }
    var hint = emptyHint();
    if (hint) {
      hint.hidden = false;
      hint.removeAttribute("aria-hidden");
    }
    submitting = false;
  }

  function isOpen() {
    var root = paneRoot();
    return !!(root && !root.hidden && !root.classList.contains("hidden"));
  }

  function run(intent, sourceIds) {
    var value = String(intent || "").trim();
    if (!value) {
      return Promise.reject(
        new Error(translate("founder.compare.intent_required", "Intent is required"))
      );
    }
    if (submitting) return Promise.resolve();
    submitting = true;

    showLoading(translate("founder.compare.dispatching", "Dispatching to 2 models…"));

    return fetch("/api/runs/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        intent: value,
        source_ids: Array.isArray(sourceIds) ? sourceIds : global.__assureActiveSourceIds || [],
      }),
    })
      .then(function (response) {
        return response.json().then(function (body) {
          if (!response.ok) {
            throw new Error(
              (body && body.error) ||
                translatef("founder.compare.endpoint_status", "Compare endpoint returned {status}", {
                  status: response.status,
                })
            );
          }
          return body;
        });
      })
      .then(function (data) {
        var payload = normalizePayload(data);
        if (!payload || payload.status !== "success") {
          throw new Error(
            (payload && payload.error) ||
              translate("founder.compare.compare_error", "Compare returned an error")
          );
        }
        var claude = (payload.models && payload.models.claude) || {};
        var secondary = (payload.models && payload.models.secondary) || {};
        if (modelHasFailed(claude) && modelHasFailed(secondary)) {
          renderFatalError(
            translate(
              "founder.compare.both_failed",
              "Both models failed. Check staging configuration."
            )
          );
          return payload;
        }
        if (global.AssureOrchestrator && typeof global.AssureOrchestrator.renderStaging === "function") {
          openShell();
          var root = paneRoot();
          if (root) {
            root.innerHTML =
              '<div class="compare-header">' +
              '<span class="compare-label ' +
              (payload.stack === "free" ? "badge-free" : "badge-paid") +
              '">' +
              escapeHtml(
                payload.stack === "free"
                  ? translate("founder.compare.free_stack", "FREE STACK")
                  : translate("founder.compare.production_stack", "PRODUCTION STACK")
              ) +
              "</span>" +
              '<button type="button" id="compare-close" class="compare-close-btn" aria-label="' +
              escapeHtml(translate("founder.compare.close", "Close compare")) +
              '">✕</button>' +
              "</div>" +
              '<div class="staging-canvas"></div>';
            bindCloseButton();
          }
          global.AssureOrchestrator.renderStaging(payload);
          var canvas = stagingCanvas();
          if (canvas) canvas.removeAttribute("aria-busy");
        }
        document.dispatchEvent(
          new CustomEvent("assure:orchestrate:complete", { detail: payload })
        );
        return payload;
      })
      .catch(function (err) {
        var msg = String((err && err.message) || err);
        renderFatalError(msg);
        throw err;
      })
      .finally(function () {
        submitting = false;
      });
  }

  global.AssureComparePane = {
    run: run,
    close: close,
    isOpen: isOpen,
    renderFatalError: renderFatalError,
    isSafeDivergence: isSafeDivergence,
    isSafeToMerge: isSafeToMerge,
    isErrorLikeText: isErrorLikeText,
    modelHasFailed: modelHasFailed,
  };

  document.addEventListener("DOMContentLoaded", function () {
    try {
      localStorage.removeItem("assure_compare_pane_state");
    } catch (_) {}
  });
})(window);
