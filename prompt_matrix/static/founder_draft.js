/**
 * Founder workbench — active draft pane (TipTap + autosave + lock pills).
 */
(function (global) {
  "use strict";

  var saveTimer = null;
  var saveInFlight = null;
  var z3FallbackTimer = null;
  var workspaceId = "founder";
  var draftTree = {
    document_id: "draft-founder",
    meta: { project_id: "founder", source: "founder_draft" },
    truth_ledger: {},
    body: [],
  };
  var streamingLockIndex = 0;
  var streamingActive = false;
  var activeRunId = null;

  function $(id) {
    return document.getElementById(id);
  }

  function resolveWorkspaceId() {
    if (global.AssureFounderMode && typeof global.AssureFounderMode.getWorkspaceId === "function") {
      return global.AssureFounderMode.getWorkspaceId();
    }
    return global.__ASSURE_PROJECT_ID__ || "default";
  }

  function isFounderShell() {
    return (
      document.body.classList.contains("founder-workbench") &&
      !document.body.classList.contains("legacy-workbench")
    );
  }

  function emptyDoc() {
    return {
      document_id: "draft-" + workspaceId,
      meta: { project_id: workspaceId, source: "founder_draft", founder_blank: true },
      truth_ledger: {},
      body: [],
    };
  }

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function showSync(text, hideAfter) {
    if (global.AssureWorkbenchPanes && typeof global.AssureWorkbenchPanes.showSyncStatus === "function") {
      global.AssureWorkbenchPanes.showSyncStatus(text, hideAfter);
    } else if (typeof global.showSyncStatus === "function") {
      global.showSyncStatus(text, hideAfter);
    }
  }

  function afterDraftSaved() {
    showSync(translate("founder.sync.z3_verifying", "Z3 Verifying..."));
    if (z3FallbackTimer) clearTimeout(z3FallbackTimer);
    z3FallbackTimer = setTimeout(function () {
      document.dispatchEvent(new CustomEvent("assure:z3-verified"));
    }, 2500);
  }

  function saveDraftNow() {
    if (saveInFlight) return saveInFlight;
    var editorApi = global.AssureTiptapEditor;
    var ed = editorApi && editorApi.getEditor && editorApi.getEditor();
    if (!editorApi || !ed || !editorApi.tiptapToJdf) return Promise.resolve();
    try {
      draftTree = editorApi.tiptapToJdf(ed.getJSON(), draftTree) || draftTree;
    } catch (_) {
      return Promise.resolve();
    }
    var url;
    var method;
    var body;
    if (isFounderShell()) {
      url = "/api/projects/" + encodeURIComponent(workspaceId) + "/draft";
      method = "POST";
      body = JSON.stringify({ content: draftTree });
    } else {
      url = "/api/drafts";
      method = "PUT";
      body = JSON.stringify({ workspace_id: workspaceId, content: draftTree });
    }
    saveInFlight = fetch(url, {
      method: method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: body,
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok && data.ok !== false, data: data };
        });
      })
      .then(function (result) {
        if (result.ok && isFounderShell()) afterDraftSaved();
        return result;
      })
      .catch(function () {})
      .finally(function () {
        saveInFlight = null;
      });
    return saveInFlight;
  }

  function scheduleSave() {
    showSync(translate("founder.sync.saving", "Saving..."));
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      saveTimer = null;
      saveDraftNow();
    }, 800);
  }

  function flushSave() {
    if (saveTimer) {
      clearTimeout(saveTimer);
      saveTimer = null;
    }
    return saveDraftNow() || Promise.resolve();
  }

  function mountEditor(tree) {
    var root = $("founder-draft-editor");
    var editorApi = global.AssureTiptapEditor;
    if (!root || !editorApi) return;
    draftTree = tree || emptyDoc();
    var isBlank = !!(draftTree.meta && draftTree.meta.founder_blank);
    editorApi.mount({
      rootEl: root,
      tree: draftTree,
      canvas: null,
      editable: true,
      founderMode: true,
      onUpdate: function () {
        updatePlaceholder();
        scheduleSave();
      },
    });
    if (global.AssureOperatorPrompt && typeof global.AssureOperatorPrompt.init === "function") {
      global.AssureOperatorPrompt.init();
    }
    if (global.AssureOrchestrator && typeof global.AssureOrchestrator.init === "function") {
      global.AssureOrchestrator.init();
    }
    if (global.AssureFounderPolish && typeof global.AssureFounderPolish.init === "function") {
      global.AssureFounderPolish.init();
    }
    if (global.AssureFounderScan && typeof global.AssureFounderScan.init === "function") {
      global.AssureFounderScan.init();
    }
    var ed = editorApi.getEditor && editorApi.getEditor();
    if (ed && isBlank) {
      try {
        ed.commands.setContent(
          {
            type: "doc",
            content: [
              {
                type: "jdfParagraph",
                attrs: { nodeId: "", gutter: "unverified" },
                content: [],
              },
            ],
          },
          false
        );
      } catch (_) {}
      draftTree = emptyDoc();
      delete draftTree.meta.founder_blank;
    }
    updatePlaceholder();
    window.setTimeout(updatePlaceholder, 100);
  }

  function draftHasContent() {
    var editorApi = global.AssureTiptapEditor;
    var ed = editorApi && editorApi.getEditor && editorApi.getEditor();
    if (ed) {
      try {
        if (typeof ed.isEmpty === "boolean") return !ed.isEmpty;
        return ed.getText().trim().length > 0;
      } catch (_) {
        return false;
      }
    }
    var root = $("founder-draft-editor");
    if (root && root.textContent && root.textContent.trim().length > 0) return true;
    return false;
  }

  function updatePlaceholder() {
    var placeholder = $("founder-draft-placeholder");
    if (!placeholder) return;
    var editorApi = global.AssureTiptapEditor;
    var ed = editorApi && editorApi.getEditor && editorApi.getEditor();
    if (!ed) {
      placeholder.hidden = false;
      return;
    }
    placeholder.hidden = draftHasContent();
  }

  function resetToEmpty() {
    activeRunId = null;
    draftTree = emptyDoc();
    var editorApi = global.AssureTiptapEditor;
    if (editorApi && editorApi.clearForStreaming) {
      editorApi.clearForStreaming();
    }
    mountEditor(emptyDoc());
  }

  function applyRestoredDocument(document, version) {
    if (!document || typeof document !== "object") return false;
    draftTree = document;
    streamingActive = false;
    var editorApi = global.AssureTiptapEditor;
    if (editorApi && typeof editorApi.setContentFromJdf === "function") {
      editorApi.setContentFromJdf(document);
    } else if (editorApi && typeof editorApi.mount === "function") {
      mountEditor(document);
    } else {
      return false;
    }
    updatePlaceholder();
    scheduleSave();
    if (version != null && global.AssureToast && typeof global.AssureToast.show === "function") {
      var msg =
        typeof global.__assureTf === "function"
          ? global.__assureTf("founder.versions.restored", "Draft restored to version {version}", {
              version: version,
            })
          : "Draft restored to version " + version;
      global.AssureToast.show(msg, "success");
    }
    return true;
  }

  function loadDraft() {
    if (!isFounderShell()) return Promise.resolve();
    return fetch("/api/drafts?workspace_id=" + encodeURIComponent(workspaceId), {
      credentials: "same-origin",
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var content = (data.draft && data.draft.content) || null;
        if (content && content.body && content.body.length) {
          mountEditor(content);
          return;
        }
        resetToEmpty();
      })
      .catch(function () {
        resetToEmpty();
      });
  }

  function syncDraftWithRuns(runs) {
    if (!isFounderShell() || streamingActive) return;
    var list = runs || [];
    if (!list.length) {
      if (!draftHasContent()) resetToEmpty();
      return;
    }
    if (activeRunId) {
      var still = list.some(function (r) {
        return r.id === activeRunId;
      });
      if (!still && !draftHasContent()) resetToEmpty();
    }
  }

  function runParagraphText(run) {
    var parts = [];
    var content = run.content || {};
    (content.body || []).forEach(function (sec) {
      (sec.children || []).forEach(function (node) {
        if (node.type === "paragraph" && node.content) parts.push(node.content);
      });
    });
    return parts.join("\n\n");
  }

  function beginStreaming() {
    streamingActive = true;
    streamingLockIndex = 0;
    activeRunId = null;
    var editorApi = global.AssureTiptapEditor;
    if (editorApi && editorApi.clearForStreaming) {
      editorApi.clearForStreaming();
    }
    updatePlaceholder();
  }

  function appendStreamToken(delta) {
    if (!streamingActive) beginStreaming();
    var editorApi = global.AssureTiptapEditor;
    if (editorApi && editorApi.appendStreamText) {
      editorApi.appendStreamText(delta);
      updatePlaceholder();
      scheduleSave();
      return true;
    }
    return false;
  }

  function insertStreamLock(lock) {
    streamingLockIndex += 1;
    var pill = Object.assign({}, lock || {}, { lock_index: streamingLockIndex });
    var editorApi = global.AssureTiptapEditor;
    if (editorApi && editorApi.insertStreamLockPill) {
      editorApi.insertStreamLockPill(pill);
      scheduleSave();
      updatePlaceholder();
      return true;
    }
    return false;
  }

  function finishStreaming() {
    streamingActive = false;
    scheduleSave();
    updatePlaceholder();
  }

  function appendRun(run) {
    if (!run) return;
    activeRunId = run.id || null;
    var text = runParagraphText(run);
    var locks = run.extracted_locks || [];
    var editorApi = global.AssureTiptapEditor;
    if (editorApi && editorApi.insertLockPills && editorApi.getEditor && editorApi.getEditor()) {
      if (editorApi.insertLockPills(text, locks)) {
        scheduleSave();
        updatePlaceholder();
        return;
      }
    }
    if (editorApi && editorApi.insertAtCursor) {
      var fallback = text;
      locks.forEach(function (lock, i) {
        var idx = String(lock.lock_index || i + 1).padStart(2, "0");
        fallback += " [🔒 #" + idx + "]";
      });
      editorApi.insertAtCursor(fallback);
      scheduleSave();
      updatePlaceholder();
      return;
    }
    var root = $("founder-draft-editor");
    if (root) {
      locks.forEach(function (lock, i) {
        var span = document.createElement("span");
        span.className = "lock-pill interactive-element";
        var idx = String(lock.lock_index || i + 1).padStart(2, "0");
        span.textContent = "[🔒 #" + idx + "]";
        span.setAttribute("data-lock-hash", lock.lock_hash || "");
        span.setAttribute("data-source-id", lock.source_id || "");
        span.setAttribute("data-page-coordinates", JSON.stringify(lock.page_coordinates || {}));
        span.setAttribute("data-lock-index", String(lock.lock_index || i + 1));
        span.addEventListener("click", function (e) {
          e.preventDefault();
          document.dispatchEvent(
            new CustomEvent("assure:lock-pill-click", {
              detail: {
                lockHash: lock.lock_hash,
                sourceId: lock.source_id,
                pageCoordinates: lock.page_coordinates || {},
                lockIndex: lock.lock_index || i + 1,
              },
            })
          );
        });
        root.appendChild(document.createTextNode(text + " "));
        root.appendChild(span);
        root.appendChild(document.createTextNode(" "));
      });
      if (!locks.length && text) root.appendChild(document.createTextNode(text));
      scheduleSave();
      updatePlaceholder();
    }
  }

  function init() {
    workspaceId = resolveWorkspaceId();
    if (isFounderShell()) {
      global.__ASSURE_PROJECT_ID__ = workspaceId;
    }

    document.addEventListener("assure:project", function (ev) {
      if (isFounderShell()) return;
      workspaceId = (ev.detail && ev.detail.projectId) || workspaceId;
      loadDraft();
    });

    document.addEventListener("assure:runs-updated", function (ev) {
      var detail = (ev && ev.detail) || {};
      syncDraftWithRuns(detail.runs || []);
    });
    document.addEventListener("assure:redhat-fix-applied", function () {
      scheduleSave();
    });

    if (
      (global.AssureFounderMode && global.AssureFounderMode.isEnabled()) ||
      document.body.classList.contains("founder-workbench")
    ) {
      loadDraft();
    }
  }

  global.AssureFounderDraft = {
    init: init,
    appendRun: appendRun,
    loadDraft: loadDraft,
    applyRestoredDocument: applyRestoredDocument,
    getDraftTree: function () {
      return draftTree;
    },
    resetToEmpty: resetToEmpty,
    beginStreaming: beginStreaming,
    appendStreamToken: appendStreamToken,
    insertStreamLock: insertStreamLock,
    finishStreaming: finishStreaming,
    flushSave: flushSave,
    saveDraftNow: saveDraftNow,
    getWorkspaceId: function () {
      return workspaceId;
    },
  };
  document.addEventListener("DOMContentLoaded", init);
})(window);
