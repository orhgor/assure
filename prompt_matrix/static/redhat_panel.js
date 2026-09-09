/**
 * Refine Workspace Red-Hat button — micro (node) or macro (full document) audit.
 */
(function (global) {
  "use strict";

  var doc = global.document;

  function init() {
    var redHatBtn = doc.getElementById("executeRedHatBtn");
    if (!redHatBtn) return;
    redHatBtn.addEventListener("click", function () {
      if (
        document.body.classList.contains("founder-workbench") &&
        !document.body.classList.contains("legacy-workbench") &&
        global.AssureRedhatDrawer &&
        typeof global.AssureRedhatDrawer.triggerRedHatAudit === "function"
      ) {
        return;
      }
      var mgr = global.__assureJdf;
      if (!mgr || typeof mgr.runRedhatAnalysis !== "function") return;
      var focus =
        global.AssureRefineEngine && global.AssureRefineEngine.activeFocusNodeId
          ? global.AssureRefineEngine.activeFocusNodeId
          : mgr.surgicalTargetId;
      if (focus) {
        mgr.runRedhatAnalysis("node", focus);
      } else {
        mgr.runRedhatAnalysis("full", null);
      }
    });
  }

  if (doc.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(typeof window !== "undefined" ? window : this);
