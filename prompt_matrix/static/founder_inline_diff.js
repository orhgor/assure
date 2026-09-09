/**
 * Founder workbench — TipTap suggestion nodes for inline accept/reject diffs.
 */
(function (global) {
  "use strict";

  var pending = null;

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function editorApi() {
    return global.AssureTiptapEditor;
  }

  function clearPending() {
    pending = null;
  }

  function getDraftPlainText() {
    var api = editorApi();
    var ed = api && api.getEditor && api.getEditor();
    if (ed) {
      try {
        return ed.getText().trim();
      } catch (_) {}
    }
    return "";
  }

  function showDiff(options) {
    options = options || {};
    var original = String(options.original || "").trim();
    var proposed = String(options.proposed || "").trim();
    if (!proposed || original === proposed) {
      if (typeof options.onAccept === "function") options.onAccept(proposed);
      return false;
    }
    var api = editorApi();
    if (!api || typeof api.insertSuggestion !== "function") {
      if (typeof options.onAccept === "function") options.onAccept(proposed);
      return false;
    }
    var suggestionId = api.insertSuggestion({
      originalText: original,
      newText: proposed,
    });
    if (!suggestionId) {
      if (typeof options.onAccept === "function") options.onAccept(proposed);
      return false;
    }
    pending = {
      suggestionId: suggestionId,
      onAccept: options.onAccept,
      onReject: options.onReject,
    };
    return true;
  }

  function resolveSuggestionId(explicitId) {
    var api = editorApi();
    if (!api) return null;
    if (explicitId) return explicitId;
    if (pending && pending.suggestionId) return pending.suggestionId;
    if (typeof api.getFocusedSuggestionId === "function") return api.getFocusedSuggestionId();
    return null;
  }

  function acceptSuggestion(explicitId) {
    var id = resolveSuggestionId(explicitId);
    if (!id) return false;
    var api = editorApi();
    var callbacks = pending && pending.suggestionId === id ? pending : null;
    if (api && typeof api.acceptSuggestion === "function") {
      api.acceptSuggestion(id, { skipContentReplace: !!(callbacks && callbacks.onAccept) });
    }
    if (callbacks && typeof callbacks.onAccept === "function") {
      callbacks.onAccept();
    }
    if (pending && pending.suggestionId === id) clearPending();
    return true;
  }

  function rejectSuggestion(explicitId) {
    var id = resolveSuggestionId(explicitId);
    if (!id) return false;
    var api = editorApi();
    var callbacks = pending && pending.suggestionId === id ? pending : null;
    if (api && typeof api.rejectSuggestion === "function") {
      api.rejectSuggestion(id);
    }
    if (callbacks && typeof callbacks.onReject === "function") {
      callbacks.onReject();
    }
    if (pending && pending.suggestionId === id) clearPending();
    return true;
  }

  function handleEscape() {
    if (!editorApi() || !editorApi().hasSuggestion || !editorApi().hasSuggestion()) return false;
    rejectSuggestion();
    return true;
  }

  function resolveFinding(runId, findingId) {
    fetch(
      "/api/runs/" + encodeURIComponent(runId) + "/findings/" + encodeURIComponent(findingId),
      {
        method: "PATCH",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "accept", dismissal_rationale: "" }),
      }
    )
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.ok && global.AssureRunsStack && global.AssureRunsStack.load) {
          global.AssureRunsStack.load();
        }
      })
      .catch(function () {});
  }

  function openFindingDiff(detail) {
    detail = detail || {};
    var runs = (global.AssureRunsStack && global.AssureRunsStack.getRuns()) || [];
    var run = runs.filter(function (r) {
      return r.id === detail.runId;
    })[0];
    if (!run) return;
    var finding = (run.redhat_findings || []).filter(function (f) {
      return f.id === detail.findingId;
    })[0];
    if (!finding) return;
    var original = getDraftPlainText();
    var proposed = String(finding.suggested_fix || finding.content || "").trim();
    showDiff({
      original: original,
      proposed: proposed,
      onAccept: function () {
        if (global.AssureFounderDraft && global.AssureFounderDraft.resetToEmpty) {
          global.AssureFounderDraft.resetToEmpty();
        }
        if (global.AssureFounderDraft && global.AssureFounderDraft.appendRun) {
          var patched = Object.assign({}, run, { content: run.content || {} });
          if (proposed) {
            patched.content = Object.assign({}, patched.content, {
              body: [
                {
                  type: "section",
                  id: "sec-fix",
                  title: "Fix",
                  children: [{ type: "paragraph", id: "para-fix", content: proposed, meta: {}, annotations: {} }],
                },
              ],
            });
          }
          global.AssureFounderDraft.appendRun(patched);
        }
        resolveFinding(run.id, finding.id);
        document.dispatchEvent(
          new CustomEvent("assure:inline-diff-accepted", {
            detail: { runId: run.id, findingId: finding.id },
          })
        );
      },
      onReject: function () {},
    });
  }

  function bindKeys() {
    document.addEventListener(
      "keydown",
      function (e) {
        if (!editorApi() || !editorApi().hasSuggestion || !editorApi().hasSuggestion()) return;
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
          e.preventDefault();
          acceptSuggestion();
          return;
        }
        if (e.key === "Escape" && !e.metaKey && !e.ctrlKey && !e.altKey) {
          var overlay = document.getElementById("command-bar-overlay");
          if (overlay && !overlay.hidden) return;
          if (global.AssureWorkbenchPanes && global.AssureWorkbenchPanes.isRightOpen && global.AssureWorkbenchPanes.isRightOpen()) {
            return;
          }
          e.preventDefault();
          rejectSuggestion();
        }
      },
      true
    );

    document.addEventListener("assure:suggestion-action", function (ev) {
      var detail = (ev && ev.detail) || {};
      if (detail.action === "accept") acceptSuggestion(detail.suggestionId);
      if (detail.action === "reject") rejectSuggestion(detail.suggestionId);
    });
  }

  function init() {
    bindKeys();
    document.addEventListener("assure:drawer-accept-fix", function (ev) {
      openFindingDiff((ev && ev.detail) || {});
    });
    document.addEventListener("assure:accept-finding-diff", function (ev) {
      openFindingDiff((ev && ev.detail) || {});
    });
  }

  global.AssureFounderInlineDiff = {
    init: init,
    showDiff: showDiff,
    clear: clearPending,
    getDraftPlainText: getDraftPlainText,
    hasPending: function () {
      return !!(pending || (editorApi() && editorApi().hasSuggestion && editorApi().hasSuggestion()));
    },
    acceptSuggestion: acceptSuggestion,
    rejectSuggestion: rejectSuggestion,
    handleEscape: handleEscape,
  };

  document.addEventListener("DOMContentLoaded", init);
})(window);
