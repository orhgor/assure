/**
 * Founder workbench — active draft pane (TipTap + autosave + lock pills).
 */
(function (global) {
  "use strict";

  var saveTimer = null;
  var workspaceId = "default";
  var draftTree = { body: [] };
  var streamingLockIndex = 0;
  var streamingActive = false;

  function $(id) {
    return document.getElementById(id);
  }

  function emptyDoc() {
    return {
      document_id: "draft-" + workspaceId,
      meta: { project_id: workspaceId, source: "founder_draft", founder_blank: true },
      truth_ledger: {},
      body: [],
    };
  }

  function scheduleSave() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(saveDraft, 800);
  }

  function saveDraft() {
    var editorApi = global.AssureTiptapEditor;
    var ed = editorApi && editorApi.getEditor && editorApi.getEditor();
    if (!editorApi || !ed || !editorApi.tiptapToJdf) return;
    try {
      draftTree = editorApi.tiptapToJdf(ed.getJSON(), draftTree) || draftTree;
    } catch (_) {}
    fetch("/api/drafts", {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace_id: workspaceId, content: draftTree }),
    }).catch(function () {});
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
    var ed = editorApi.getEditor && editorApi.getEditor();
    if (ed && isBlank) {
      try {
        ed.commands.setContent({ type: "doc", content: [{ type: "paragraph" }] }, false);
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

  function loadDraft() {
    return fetch("/api/drafts?workspace_id=" + encodeURIComponent(workspaceId), {
      credentials: "same-origin",
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var content = (data.draft && data.draft.content) || emptyDoc();
        mountEditor(content);
      })
      .catch(function () {
        mountEditor(emptyDoc());
      });
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
    workspaceId = global.__ASSURE_PROJECT_ID__ || "default";
    document.addEventListener("assure:project", function (ev) {
      workspaceId = (ev.detail && ev.detail.projectId) || workspaceId;
      loadDraft();
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
    beginStreaming: beginStreaming,
    appendStreamToken: appendStreamToken,
    insertStreamLock: insertStreamLock,
    finishStreaming: finishStreaming,
  };
  document.addEventListener("DOMContentLoaded", init);
})(window);
