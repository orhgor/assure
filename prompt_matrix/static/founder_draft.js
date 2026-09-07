/**
 * Founder workbench — active draft pane (TipTap + autosave + lock pills).
 */
(function (global) {
  "use strict";

  var saveTimer = null;
  var workspaceId = "default";
  var draftTree = { body: [] };

  function $(id) {
    return document.getElementById(id);
  }

  function emptyDoc() {
    return {
      document_id: "draft-" + workspaceId,
      meta: { project_id: workspaceId, source: "founder_draft" },
      truth_ledger: {},
      body: [
        {
          type: "section",
          id: "sec-draft",
          title: "Draft",
          children: [
            {
              type: "paragraph",
              id: "para-draft-1",
              content: "",
              entities_referenced: [],
              provenance: [],
              meta: { lock_pills: [] },
              annotations: { redhat: [], z3: [] },
            },
          ],
          meta: {},
          annotations: { redhat: [], z3: [] },
        },
      ],
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
    if (!root || !global.AssureTiptapEditor) return;
    draftTree = tree || emptyDoc();
    global.AssureTiptapEditor.mount({
      rootEl: root,
      tree: draftTree,
      canvas: null,
      editable: true,
      founderMode: true,
      onUpdate: function () {
        scheduleSave();
      },
    });
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

  function appendRun(run) {
    var text = runParagraphText(run);
    var locks = run.extracted_locks || [];
    var editorApi = global.AssureTiptapEditor;
    if (editorApi && editorApi.insertLockPills && editorApi.getEditor && editorApi.getEditor()) {
      if (editorApi.insertLockPills(text, locks)) {
        scheduleSave();
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
    }
  }

  function init() {
    workspaceId = global.__ASSURE_PROJECT_ID__ || "default";
    document.addEventListener("assure:project", function (ev) {
      workspaceId = (ev.detail && ev.detail.projectId) || workspaceId;
      loadDraft();
    });
    if (document.body.classList.contains("founder-workbench")) {
      loadDraft();
    }
  }

  global.AssureFounderDraft = { init: init, appendRun: appendRun, loadDraft: loadDraft };
  document.addEventListener("DOMContentLoaded", init);
})(window);
