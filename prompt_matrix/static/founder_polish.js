/**
 * Founder Main Document — grammar/flow polish with inline diff preview.
 */
(function (global) {
  "use strict";

  var polishing = false;

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function isFounderShell() {
    return (
      document.body.classList.contains("founder-workbench") &&
      !document.body.classList.contains("legacy-workbench")
    );
  }

  function workspaceId() {
    if (global.AssureFounderDraft && typeof global.AssureFounderDraft.getWorkspaceId === "function") {
      return global.AssureFounderDraft.getWorkspaceId();
    }
    if (global.AssureFounderMode && typeof global.AssureFounderMode.getWorkspaceId === "function") {
      return global.AssureFounderMode.getWorkspaceId();
    }
    return global.__ASSURE_PROJECT_ID__ || "founder";
  }

  function captureDocumentTree() {
    var api = global.AssureTiptapEditor;
    var ed = api && api.getEditor && api.getEditor();
    if (!api || !ed || !api.tiptapToJdf) return null;
    var base =
      global.AssureFounderDraft && typeof global.AssureFounderDraft.getDraftTree === "function"
        ? global.AssureFounderDraft.getDraftTree()
        : null;
    try {
      return api.tiptapToJdf(ed.getJSON(), base || undefined);
    } catch (_) {
      return null;
    }
  }

  function setButtonBusy(btn, busy) {
    if (!btn) return;
    btn.disabled = !!busy;
    btn.classList.toggle("is-busy", !!busy);
    if (busy) {
      btn.dataset.busyLabel = btn.textContent;
      btn.textContent = translate("founder.polish.running", "Polishing grammar...");
    } else if (btn.dataset.busyLabel) {
      btn.textContent = btn.dataset.busyLabel;
      delete btn.dataset.busyLabel;
    }
  }

  function toast(msg, kind) {
    if (global.AssureToast && typeof global.AssureToast.show === "function") {
      global.AssureToast.show(msg, kind || "error");
    }
  }

  function applyPolishedDocument(document) {
    var api = global.AssureTiptapEditor;
    if (!api || typeof api.setContentFromJdf !== "function") return false;
    var ok = api.setContentFromJdf(document);
    if (ok && global.AssureFounderDraft && typeof global.AssureFounderDraft.saveDraftNow === "function") {
      global.AssureFounderDraft.saveDraftNow();
    }
    return ok;
  }

  function showPolishPreview(payload) {
    var inline = global.AssureFounderInlineDiff;
    var original = String(payload.plain_before || "").trim();
    var proposed = String(payload.plain_after || "").trim();
    if (!proposed) {
      toast(translate("founder.polish.error", "Polish failed — lock pills must stay intact."));
      return;
    }
    if (original === proposed) {
      toast(translate("founder.polish.no_changes", "No grammar changes suggested."), "info");
      return;
    }
    if (inline && typeof inline.showDiff === "function") {
      inline.showDiff({
        original: original,
        proposed: proposed,
        onAccept: function () {
          applyPolishedDocument(payload.document);
          document.dispatchEvent(
            new CustomEvent("assure:polish:accepted", { detail: { lock_count: payload.lock_count } })
          );
        },
        onReject: function () {
          document.dispatchEvent(new CustomEvent("assure:polish:rejected", { detail: {} }));
        },
      });
      return;
    }
    applyPolishedDocument(payload.document);
  }

  function requestPolish(documentTree) {
    return fetch("/api/projects/" + encodeURIComponent(workspaceId()) + "/polish", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document: documentTree, use_llm: false }),
    }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) {
          var msg =
            (body && body.error) ||
            translate("founder.polish.error", "Polish failed — lock pills must stay intact.");
          throw new Error(msg);
        }
        return body;
      });
    });
  }

  function onPolishMain() {
    if (!isFounderShell() || polishing) return;
    var documentTree = captureDocumentTree();
    if (!documentTree) {
      toast(translate("founder.polish.error", "Polish failed — lock pills must stay intact."));
      return;
    }
    var btn = document.getElementById("btn-polish-main");
    polishing = true;
    setButtonBusy(btn, true);
    requestPolish(documentTree)
      .then(function (payload) {
        if (!payload || payload.status !== "success" || !payload.strict_preservation) {
          throw new Error(
            translate(
              "founder.polish.preservation_failed",
              "Strict preservation failed: verified lock pills were altered."
            )
          );
        }
        showPolishPreview(payload);
      })
      .catch(function (err) {
        toast(String((err && err.message) || err));
      })
      .finally(function () {
        polishing = false;
        setButtonBusy(btn, false);
      });
  }

  function bindPolishButton() {
    var btn = document.getElementById("btn-polish-main");
    if (!btn || btn.dataset.polishBound === "1") return;
    btn.dataset.polishBound = "1";
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      onPolishMain();
    });
  }

  function init() {
    if (!isFounderShell()) return;
    bindPolishButton();
  }

  global.AssureFounderPolish = {
    init: init,
    polishMain: onPolishMain,
  };

  document.addEventListener("DOMContentLoaded", init);
})(window);
