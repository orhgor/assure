/**
 * Document lifecycle stepper — Write / Verify / Audit / Ship.
 * Visual states follow compile and audit events; degrades if the row is missing.
 */
(function (global) {
  "use strict";

  var PHASES = ["write", "verify", "audit", "ship"];

  function $(id) {
    return document.getElementById(id);
  }

  var Stepper = {
    currentPhase: "write",
    _bound: false,

    init: function () {
      if (this._bound) return;
      this._bound = true;
      this.bindEvents();
      this.syncWithCompiler();
      this.setPhase("write", "active");
    },

    bindEvents: function () {
      var toggleBtn = $("workbench-advanced-toggle-btn");
      if (!toggleBtn) return;
      toggleBtn.addEventListener("click", function () {
        var show = !document.body.classList.contains("workbench-advanced-on");
        if (global.AssureWorkbenchClarity && typeof global.AssureWorkbenchClarity.applyAdvancedState === "function") {
          global.AssureWorkbenchClarity.applyAdvancedState(show);
          try {
            localStorage.setItem("assure_show_advanced", show ? "1" : "0");
          } catch (_) {}
        } else {
          document.body.classList.toggle("workbench-advanced-on", show);
          document.body.classList.toggle("workbench-advanced-off", !show);
        }
        toggleBtn.setAttribute("aria-expanded", show ? "true" : "false");
      });
      var expanded = document.body.classList.contains("workbench-advanced-on");
      toggleBtn.setAttribute("aria-expanded", expanded ? "true" : "false");
    },

    setPhase: function (phaseName, state) {
      var targetIdx = PHASES.indexOf(phaseName);
      if (targetIdx < 0) return;
      var self = this;
      PHASES.forEach(function (p, idx) {
        var el = document.querySelector('.step-item[data-phase="' + p + '"]');
        if (!el) return;
        el.classList.remove("is-active", "is-completed", "is-disabled");
        var marker = el.querySelector(".step-marker");
        var completed = idx < targetIdx || (idx === targetIdx && state === "completed");
        var active = idx === targetIdx && state !== "completed";
        if (completed) {
          el.classList.add("is-completed");
          el.setAttribute("aria-selected", "false");
          if (marker) marker.textContent = "✓";
        } else if (active) {
          el.classList.add("is-active");
          el.setAttribute("aria-selected", "true");
          if (marker) marker.textContent = String(idx + 1);
        } else {
          el.classList.add("is-disabled");
          el.setAttribute("aria-selected", "false");
          if (marker) marker.textContent = String(idx + 1);
        }
        var conn = document.querySelector('.step-connector[data-after="' + p + '"]');
        if (conn) {
          conn.classList.toggle("is-complete", completed);
          conn.classList.toggle("is-active", active);
        }
        if (p === "ship" && targetIdx >= PHASES.indexOf("verify") && !completed && !active) {
          el.classList.remove("is-disabled");
        }
      });
      if (phaseName === "audit" && state === "active") {
        var ship = document.querySelector('.step-item[data-phase="ship"]');
        if (ship) {
          ship.classList.remove("is-disabled");
          ship.classList.add("is-active");
        }
      }
      self.currentPhase = phaseName;
    },

    syncWithCompiler: function () {
      var self = this;
      document.addEventListener("assure:compile:start", function () {
        self.setPhase("write", "active");
      });
      document.addEventListener("assure:compile:verified", function () {
        self.setPhase("audit", "active");
        var write = document.querySelector('.step-item[data-phase="write"]');
        var verify = document.querySelector('.step-item[data-phase="verify"]');
        if (write) {
          write.classList.remove("is-active", "is-disabled");
          write.classList.add("is-completed");
          var wm = write.querySelector(".step-marker");
          if (wm) wm.textContent = "✓";
        }
        if (verify) {
          verify.classList.remove("is-active", "is-disabled");
          verify.classList.add("is-completed");
          var vm = verify.querySelector(".step-marker");
          if (vm) vm.textContent = "✓";
        }
      });
      document.addEventListener("assure:audit:complete", function () {
        self.setPhase("ship", "active");
        ["write", "verify", "audit"].forEach(function (p) {
          var el = document.querySelector('.step-item[data-phase="' + p + '"]');
          if (!el) return;
          el.classList.remove("is-active", "is-disabled");
          el.classList.add("is-completed");
          var m = el.querySelector(".step-marker");
          if (m) m.textContent = "✓";
        });
      });
    },
  };

  global.AssureStepper = Stepper;
  document.addEventListener("DOMContentLoaded", function () {
    if (document.querySelector(".stepper-timeline")) Stepper.init();
  });
})(window);
