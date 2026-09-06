/**
 * Workbench clarity — progressive disclosure and action grouping.
 */
(function (global) {
  "use strict";

  var STORAGE_KEY = "assure_show_advanced";

  function t(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function $(id) {
    return document.getElementById(id);
  }

  var Clarity = {
    _bound: false,

    init: function () {
      if (this._bound) return;
      this._bound = true;
      this.applyAdvancedState(this.readAdvanced());
      var toggle = $("workbench-show-advanced");
      if (toggle) {
        toggle.addEventListener("change", function () {
          Clarity.applyAdvancedState(toggle.checked);
          try {
            localStorage.setItem(STORAGE_KEY, toggle.checked ? "1" : "0");
          } catch (_) {}
        });
      }
    },

    readAdvanced: function () {
      try {
        return localStorage.getItem(STORAGE_KEY) === "1";
      } catch (_) {
        return false;
      }
    },

    applyAdvancedState: function (show) {
      var toggle = $("workbench-show-advanced");
      if (toggle) toggle.checked = !!show;
      document.body.classList.toggle("workbench-advanced-on", !!show);
      document.body.classList.toggle("workbench-advanced-off", !show);
    },

    syncDockButtons: function () {},
  };

  global.AssureWorkbenchClarity = Clarity;
  document.addEventListener("DOMContentLoaded", function () {
    Clarity.init();
  });
})(window);
