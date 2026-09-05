/**
 * JDF AST serialization — wraps TipTap → JDF using existing Pydantic-aligned mappers.
 */
(function (global) {
  "use strict";

  function JdfAstSerializer(editor) {
    this.editor = editor;
    this.lastSerializedPayload = null;
  }

  JdfAstSerializer.prototype.serializeCanvasToAst = function (documentId) {
    var canvas = global.__assureJdf;
    var tiptap = global.AssureTiptapEditor;
    if (!canvas || !this.editor || !tiptap || typeof tiptap.tiptapToJdf !== "function") {
      return null;
    }
    try {
      var tree = tiptap.tiptapToJdf(this.editor.getJSON(), canvas.tree);
      if (typeof global.sanitizeJDFDocument === "function") {
        tree = global.sanitizeJDFDocument(tree);
      }
      if (documentId) tree.document_id = documentId;
      else if (!tree.document_id) tree.document_id = canvas.tree.document_id || "doc-default";
      this.lastSerializedPayload = tree;
      return tree;
    } catch (err) {
      console.error("AST serialization error:", err);
      return null;
    }
  };

  global.initializeAstSerializer = function (tiptapInstance) {
    global.AssureAstSerializer = new JdfAstSerializer(tiptapInstance);
    return global.AssureAstSerializer;
  };
})(typeof window !== "undefined" ? window : this);
