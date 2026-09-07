/**
 * Founder workbench — active draft pane (TipTap + autosave).
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
              meta: {},
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
    var editor = global.AssureTiptapEditor;
    var root = $("founder-draft-editor");
    if (!editor || !root || !editor.tiptapToJdf) return;
    try {
      var doc = editor.tiptapToJdf(editor.getEditor && editor.getEditor(), draftTree);
      draftTree = doc || draftTree;
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
    });
    var ed = global.AssureTiptapEditor.getEditor && global.AssureTiptapEditor.getEditor();
    if (ed && ed.on) {
      ed.on("update", scheduleSave);
    }
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

  function runToPlainText(run) {
    var parts = [];
    var content = run.content || {};
    (content.body || []).forEach(function (sec) {
      (sec.children || []).forEach(function (node) {
        if (node.type === "paragraph" && node.content) parts.push(node.content);
      });
    });
    var locks = run.extracted_locks || [];
    locks.forEach(function (lock, i) {
      var key = lock.canonical_key || lock.metric || "metric";
      parts.push("[🔒 #" + (i + 1) + "] " + key + " = " + lock.value);
    });
    return parts.join("\n\n");
  }

  function appendRun(run) {
    var text = runToPlainText(run);
    var ed = global.AssureTiptapEditor && global.AssureTiptapEditor.getEditor && global.AssureTiptapEditor.getEditor();
    if (ed && ed.commands) {
      ed.commands.insertContent("<p>" + text.replace(/</g, "&lt;").replace(/\n\n/g, "</p><p>") + "</p>");
      scheduleSave();
      return;
    }
    var root = $("founder-draft-editor");
    if (root) {
      root.innerText = (root.innerText ? root.innerText + "\n\n" : "") + text;
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
