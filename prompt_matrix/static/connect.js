/**
 * Connect Sections — bridge two adjacent sections via surgical refine.
 */
(function (global) {
  "use strict";

  var doc = global.document;

  function t(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback;
  }

  function init() {
    var connectBtn = doc.getElementById("actionConnectBtn");
    if (!connectBtn) return;
    connectBtn.addEventListener("click", function () {
      var structure = global.AssureDocumentStructure;
      var mgr = global.__assureJdf;
      if (!structure || !mgr || typeof mgr.connectSections !== "function") return;
      var ids = structure.selectedForConnect || [];
      if (ids.length !== 2) return;
      var ordered = structure.sections || [];
      var i0 = ordered.findIndex(function (s) {
        return s.id === ids[0];
      });
      var i1 = ordered.findIndex(function (s) {
        return s.id === ids[1];
      });
      var sourceId = i0 < i1 ? ids[0] : ids[1];
      var targetId = i0 < i1 ? ids[1] : ids[0];
      if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
        global.AssureNav.switchView("surgical");
      }
      if (global.AssureToast) {
        global.AssureToast.show(t("refine.connecting", "Connecting sections…"), "info");
      }
      mgr.connectSections(sourceId, targetId);
      structure.selectedForConnect = [];
      structure.updateConnectButton();
      structure.treeContainer &&
        structure.treeContainer.querySelectorAll(".tree-node-item").forEach(function (el) {
          el.classList.remove("node-connect-selected");
        });
    });
  }

  if (doc.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(typeof window !== "undefined" ? window : this);
