/**
 * TipTap / ProseMirror sync bridge for Document Structure mutations.
 * Reorders section blocks while preserving editor history where possible.
 */
(function (global) {
  "use strict";

  function AssureEditorSyncBridge(editor) {
    this.editor = editor;
  }

  AssureEditorSyncBridge.prototype.syncReorderedASTToCanvas = function (orderedSectionIds) {
    var canvas = global.__assureJdf;
    var editor = this.editor;
    if (!canvas || !editor || !Array.isArray(orderedSectionIds)) return false;

    var body = canvas.tree.body || [];
    var byId = {};
    body.forEach(function (sec) {
      byId[sec.id] = sec;
    });
    var reordered = orderedSectionIds
      .map(function (id) {
        return byId[id];
      })
      .filter(Boolean);
    if (reordered.length !== body.length) return false;

    canvas.tree.body = reordered;
    canvas._tiptapSyncing = true;
    try {
      var tiptap = global.AssureTiptapEditor;
      if (tiptap && typeof tiptap.jdfToTiptap === "function") {
        var json = tiptap.jdfToTiptap(canvas.tree, false);
        editor.commands.setContent(json, true);
      } else {
        canvas.render();
      }
    } finally {
      canvas._tiptapSyncing = false;
    }
    return true;
  };

  global.initializeEditorSyncBridge = function (tiptapInstance) {
    global.AssureEditorBridge = new AssureEditorSyncBridge(tiptapInstance);
    return global.AssureEditorBridge;
  };
})(typeof window !== "undefined" ? window : this);
