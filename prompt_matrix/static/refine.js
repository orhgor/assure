/**
 * Refine Workspace — implicit node focus, selection refine, full-document refine.
 */
(function (global) {
  "use strict";

  var doc = global.document;

  function $(id) {
    return doc.getElementById(id);
  }

  function t(key, fallback, params) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, params || {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback;
  }

  function canvas() {
    return global.__assureJdf || null;
  }

  function redhatOn() {
    var el = $("toggle-redhat");
    return !el || el.checked;
  }

  var Refine = {
    _bound: false,
    focusIndicator: null,
    intentInput: null,
    refineBtn: null,
    fullDocBtn: null,
    activeFocusNodeId: null,

    bind: function () {
      if (this._bound) return;
      this._bound = true;
      var self = this;
      this.focusIndicator = $("refineNodeFocusIndicator");
      this.intentInput = $("refineIntentInput");
      this.refineBtn = $("executeNodeRefineBtn");
      this.fullDocBtn = $("executeFullDocRefineBtn");

      if (this.refineBtn) {
        this.refineBtn.addEventListener("click", function () {
          self.executeTargetedRefinement();
        });
      }
      if (this.fullDocBtn) {
        this.fullDocBtn.addEventListener("click", function () {
          self.executeFullDocumentRefinement();
        });
      }

      doc.addEventListener("assure:jdf:selected", function (e) {
        self.updateImplicitSelection(e.detail && e.detail.nodeId);
      });

      global.AssureRefineEngine = this;
    },

    updateImplicitSelection: function (nodeId) {
      this.activeFocusNodeId = nodeId || null;
      if (!this.focusIndicator) return;
      if (nodeId) {
        this.focusIndicator.textContent = t("refine.refining_node", "Refining node: {id}", { id: nodeId });
        this.focusIndicator.classList.add("is-active");
      } else {
        this.focusIndicator.textContent = t(
          "refine.click_to_refine",
          "Click a paragraph in the canvas to refine it"
        );
        this.focusIndicator.classList.remove("is-active");
      }
    },

    _intent: function () {
      return ((this.intentInput && this.intentInput.value) || "").trim();
    },

    _toast: function (msg, kind) {
      if (global.AssureToast) global.AssureToast.show(msg, kind || "info");
      else global.alert(msg);
    },

    executeTargetedRefinement: function () {
      if (!this.activeFocusNodeId) {
        this._toast(t("refine.select_node", "Click a paragraph in the canvas to target your refinement instruction."), "error");
        return;
      }
      var directive = this._intent();
      if (!directive) {
        this._toast(t("refine.no_intent", "Enter a refinement instruction first."), "error");
        return;
      }
      var mgr = canvas();
      if (!mgr || typeof mgr.inquire !== "function") return;
      if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
        global.AssureNav.switchView("surgical");
      }
      mgr.selectNodeForRefine(this.activeFocusNodeId, { toast: false, skipViewSwitch: true });
      mgr._setInquiryIntent(directive);
      if (this.intentInput) {
        var legacy = $("inquiry-input");
        if (legacy) legacy.value = directive;
      }
      mgr.inquire(directive, redhatOn());
    },

    executeFullDocumentRefinement: function () {
      var directive = this._intent();
      if (!directive) {
        this._toast(t("refine.no_intent", "Enter a refinement instruction first."), "error");
        return;
      }
      var mgr = canvas();
      if (!mgr || typeof mgr.refineFullDocument !== "function") return;
      if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
        global.AssureNav.switchView("surgical");
      }
      mgr.refineFullDocument(directive, redhatOn());
    },
  };

  function init() {
    Refine.bind();
  }

  if (doc.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(typeof window !== "undefined" ? window : this);
