(function (global) {
  "use strict";

  var DRAFT_KEY = "assure_draft_prompt";
  var DEBOUNCE_MS = 1000;

  var _inputDirty = false;
  var _documentDirty = false;
  var _isGenerating = false;
  var _draftTimer = null;
  var _beforeUnloadBound = false;

  function t(key, fallback, vars) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, vars || {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback || key;
  }

  function $(id) {
    return document.getElementById(id);
  }

  function recomputeGlobals() {
    global.hasUnsavedChanges = _inputDirty || _documentDirty;
    global.isGenerating = _isGenerating;
  }

  function updateUnsavedPill() {
    if (!_inputDirty && !_documentDirty) return;
    var jdf = global.__assureJdf;
    if (jdf && typeof jdf.setSavePill === "function") {
      jdf.setSavePill("idle", "jdf.save.unsaved");
      return;
    }
    var el = $("save-status");
    if (!el) return;
    el.className = "save-pill pill-idle tooltip-trigger";
    el.setAttribute("data-i18n", "jdf.save.unsaved");
    el.textContent = t("jdf.save.unsaved", "◌ Unsaved");
  }

  function bindBeforeUnload() {
    if (_beforeUnloadBound) return;
    _beforeUnloadBound = true;
    global.addEventListener("beforeunload", function (e) {
      if (_inputDirty || _documentDirty || _isGenerating) {
        e.preventDefault();
        e.returnValue = "";
      }
    });
  }

  function readDraftFields() {
    return {
      compile: ($("generate-intent") && $("generate-intent").value) || "",
      refine: ($("inquiry-input") && $("inquiry-input").value) || "",
    };
  }

  function writeDraftDebounced() {
    if (_draftTimer) clearTimeout(_draftTimer);
    _draftTimer = global.setTimeout(function () {
      _draftTimer = null;
      try {
        var fields = readDraftFields();
        if (fields.compile || fields.refine) {
          localStorage.setItem(DRAFT_KEY, JSON.stringify(fields));
        }
      } catch (_) {}
    }, DEBOUNCE_MS);
  }

  function restoreDraftIfEmpty() {
    try {
      var raw = localStorage.getItem(DRAFT_KEY);
      if (!raw) return;
      var draft = JSON.parse(raw);
      var compileEl = $("generate-intent");
      var refineEl = $("inquiry-input");
      if (compileEl && !compileEl.value.trim() && draft.compile) {
        compileEl.value = draft.compile;
      }
      if (refineEl && !refineEl.value.trim() && draft.refine) {
        refineEl.value = draft.refine;
      }
    } catch (_) {}
  }

  function clearDraftStorage() {
    if (_draftTimer) {
      clearTimeout(_draftTimer);
      _draftTimer = null;
    }
    try {
      localStorage.removeItem(DRAFT_KEY);
    } catch (_) {}
  }

  function onPromptInput() {
    AssureUnsaved.setInputDirty(true);
    writeDraftDebounced();
  }

  function bindInputs() {
    var compileEl = $("generate-intent");
    var refineEl = $("inquiry-input");
    if (compileEl) compileEl.addEventListener("input", onPromptInput);
    if (refineEl) refineEl.addEventListener("input", onPromptInput);
  }

  var AssureUnsaved = {
    get hasUnsavedChanges() {
      return _inputDirty || _documentDirty;
    },
    get isGenerating() {
      return _isGenerating;
    },

    setInputDirty: function (dirty) {
      _inputDirty = !!dirty;
      recomputeGlobals();
      if (_inputDirty) updateUnsavedPill();
    },

    setDocumentDirty: function (dirty) {
      _documentDirty = !!dirty;
      recomputeGlobals();
      if (_documentDirty) updateUnsavedPill();
    },

    clearUnsaved: function () {
      _inputDirty = false;
      _documentDirty = false;
      recomputeGlobals();
    },

    /** Returns false if the user cancels. Clears dirty flags on confirm. */
    confirmLeave: function (messageKey, fallback) {
      if (!_inputDirty && !_documentDirty && !_isGenerating) return true;
      var msg;
      if (_isGenerating) {
        msg = t(
          "unsaved.switch_generating",
          "A compile is still running. Leaving now will stop it. Continue?"
        );
      } else {
        msg = t(
          messageKey || "unsaved.switch_view",
          fallback || "You have unsaved changes. Switch anyway?"
        );
      }
      if (!global.confirm(msg)) return false;
      this.clearUnsaved();
      this.setGenerating(false);
      return true;
    },

    setGenerating: function (on) {
      _isGenerating = !!on;
      recomputeGlobals();
    },

    clearDraft: clearDraftStorage,

    init: function () {
      bindBeforeUnload();
      bindInputs();
      restoreDraftIfEmpty();
      recomputeGlobals();
    },
  };

  global.AssureUnsaved = AssureUnsaved;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      AssureUnsaved.init();
    });
  } else {
    AssureUnsaved.init();
  }
})(window);
