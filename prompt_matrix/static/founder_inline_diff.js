/**
 * Founder workbench — inline accept/reject diff blocks before draft merge.
 */
(function (global) {
  "use strict";

  var pending = null;

  function $(id) {
    return document.getElementById(id);
  }

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function hostEl() {
    return $("founder-inline-diff");
  }

  function clear() {
    pending = null;
    var host = hostEl();
    if (host) {
      host.hidden = true;
      host.innerHTML = "";
    }
  }

  function renderBlock(block, index) {
    return (
      '<div class="founder-diff-block" data-diff-index="' +
      index +
      '" tabindex="0">' +
      '<p class="founder-diff-label hint">' +
      translate("founder.diff.review", "Review change") +
      " · ⌘↵ " +
      translate("founder.diff.accept", "Accept") +
      " · ⌫ " +
      translate("founder.diff.reject", "Reject") +
      "</p>" +
      '<div class="founder-diff-remove">' +
      esc(block.original) +
      "</div>" +
      '<div class="founder-diff-add">' +
      esc(block.proposed) +
      "</div>" +
      "</div>"
    );
  }

  function showDiff(options) {
    options = options || {};
    var original = String(options.original || "").trim();
    var proposed = String(options.proposed || "").trim();
    if (!proposed || original === proposed) {
      if (typeof options.onAccept === "function") options.onAccept(proposed);
      return false;
    }
    pending = {
      original: original,
      proposed: proposed,
      onAccept: options.onAccept,
      onReject: options.onReject,
      blocks: [{ original: original, proposed: proposed }],
    };
    var host = hostEl();
    if (!host) {
      if (typeof options.onAccept === "function") options.onAccept(proposed);
      return false;
    }
    host.innerHTML = pending.blocks.map(renderBlock).join("");
    host.hidden = false;
    var first = host.querySelector(".founder-diff-block");
    if (first && first.focus) first.focus();
    return true;
  }

  function acceptBlock(index) {
    if (!pending) return;
    var block = pending.blocks[index];
    if (!block) return;
    if (typeof pending.onAccept === "function") pending.onAccept(block.proposed);
    clear();
  }

  function rejectBlock(index) {
    if (!pending) return;
    var block = pending.blocks[index];
    if (!block) return;
    if (typeof pending.onReject === "function") pending.onReject(block.original);
    clear();
  }

  function bindKeys() {
    document.addEventListener(
      "keydown",
      function (e) {
        if (!pending) return;
        var host = hostEl();
        if (!host || host.hidden) return;
        var focused = host.querySelector(".founder-diff-block:focus") || host.querySelector(".founder-diff-block");
        var index = focused ? parseInt(focused.getAttribute("data-diff-index") || "0", 10) : 0;
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
          e.preventDefault();
          acceptBlock(index);
          return;
        }
        if (e.key === "Backspace" && document.activeElement && host.contains(document.activeElement)) {
          e.preventDefault();
          rejectBlock(index);
        }
      },
      true
    );
  }

  function getDraftPlainText() {
    var editorApi = global.AssureTiptapEditor;
    var ed = editorApi && editorApi.getEditor && editorApi.getEditor();
    if (ed) {
      try {
        return ed.getText().trim();
      } catch (_) {}
    }
    var root = $("founder-draft-editor");
    return root ? (root.textContent || "").trim() : "";
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
      onReject: function () {
        clear();
      },
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
    clear: clear,
    getDraftPlainText: getDraftPlainText,
    hasPending: function () {
      return !!pending;
    },
  };

  document.addEventListener("DOMContentLoaded", init);
})(window);
