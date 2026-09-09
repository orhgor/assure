/**
 * Founder workbench — 48px state rail, stage filters, guarded hotkeys.
 */
(function (global) {
  "use strict";

  var store = {
    filter: "all",
    streaming: false,
    pendingCritique: false,
    allLocked: false,
    verifiedClaimCount: 0,
  };
  var listeners = [];

  function $(id) {
    return document.getElementById(id);
  }

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function isFounderShell() {
    return (
      global.AssureFounderShell &&
      typeof global.AssureFounderShell.isFounderShell === "function" &&
      global.AssureFounderShell.isFounderShell()
    );
  }

  function isTypingTarget() {
    var el = document.activeElement;
    if (!el) return false;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return true;
    if (el.isContentEditable) return true;
    return false;
  }

  function notify() {
    listeners.forEach(function (fn) {
      try {
        fn(store);
      } catch (_) {}
    });
    updateRailUi();
  }

  function subscribe(fn) {
    if (typeof fn === "function") listeners.push(fn);
  }

  function getStore() {
    return Object.assign({}, store);
  }

  function getFilter() {
    return store.filter;
  }

  function setActiveButton(filter) {
    var rail = $("state-rail");
    if (!rail) return;
    rail.querySelectorAll(".state-rail-btn").forEach(function (btn) {
      var btnFilter = btn.getAttribute("data-filter") || "all";
      var stage = btn.getAttribute("data-stage") || "";
      var active = false;
      if (stage === "directive") {
        active = false;
      } else if (stage === "runs") {
        active = filter === "all";
      } else {
        active = btnFilter === filter;
      }
      btn.classList.toggle("is-active", active);
    });
  }

  function setFilter(filter, options) {
    options = options || {};
    filter = filter || "all";
    store.filter = filter;
    var stack = $("runs-stack");
    if (stack) stack.setAttribute("data-filter", filter);
    setActiveButton(filter);
    if (global.AssureRunsStack && typeof global.AssureRunsStack.render === "function") {
      global.AssureRunsStack.render();
    }
    if (!options.silent) {
      document.dispatchEvent(new CustomEvent("assure:state-filter", { detail: { filter: filter } }));
    }
    notify();
  }

  function openDirective() {
    if (global.AssureCommandBar && typeof global.AssureCommandBar.open === "function") {
      global.AssureCommandBar.open();
    }
  }

  function openGroundingVault() {
    if (global.AssureFounderShell && typeof global.AssureFounderShell.openSourcesDrawer === "function") {
      global.AssureFounderShell.openSourcesDrawer();
    }
  }

  function triggerDossierExport() {
    var btn = $("btn-export-dossier");
    if (btn && typeof btn.click === "function") btn.click();
  }

  function handleStageClick(stage, filter) {
    if (stage === "directive") {
      openDirective();
      setFilter("all");
      return;
    }
    if (stage === "grounded") {
      setFilter("grounded");
      return;
    }
    if (stage === "redhat") {
      setFilter("redhat");
      return;
    }
    if (stage === "dossier") {
      setFilter("dossier");
      triggerDossierExport();
      return;
    }
    setFilter(filter || "all");
  }

  function updateRailUi() {
    var rail = $("state-rail");
    if (!rail) return;

    var groundedTip = translate("founder.state_rail.grounded_tip", "Grounding Vault (Shift+3)");
    if (store.verifiedClaimCount > 0) {
      groundedTip = translate(
        "founder.state_rail.grounded_verified",
        "Grounding Vault ({count} claims verified)"
      ).replace("{count}", String(store.verifiedClaimCount));
    }
    var groundedBtn = rail.querySelector('[data-stage="grounded"] .state-rail-tooltip');
    if (groundedBtn) groundedBtn.textContent = groundedTip;

    rail.querySelectorAll(".state-rail-btn").forEach(function (btn) {
      var dot = btn.querySelector(".state-rail-dot");
      if (!dot) return;
      dot.hidden = true;
      dot.classList.remove("is-green", "is-amber", "is-streaming");
    });

    var runsDot = rail.querySelector('[data-stage="runs"] .state-rail-dot');
    if (runsDot && store.streaming) {
      runsDot.hidden = false;
      runsDot.classList.add("is-streaming");
    }

    var rhDot = rail.querySelector('[data-stage="redhat"] .state-rail-dot');
    if (rhDot && store.pendingCritique) {
      rhDot.hidden = false;
      rhDot.classList.add("is-amber");
    }

    var gDot = rail.querySelector('[data-stage="grounded"] .state-rail-dot');
    if (gDot && store.allLocked && store.verifiedClaimCount > 0) {
      gDot.hidden = false;
      gDot.classList.add("is-green");
    }
  }

  function syncFromRuns(runs, streaming) {
    runs = runs || [];
    store.streaming = !!streaming;
    store.verifiedClaimCount = runs.reduce(function (sum, run) {
      return sum + ((run.extracted_locks || []).length || 0);
    }, 0);
    store.pendingCritique = runs.some(function (run) {
      return (run.redhat_findings || []).some(function (f) {
        return f.status === "open";
      });
    });
    store.allLocked = runs.length > 0 && runs.every(function (run) {
      var locks = (run.extracted_locks || []).length;
      return locks > 0 && run.status === "stamped";
    });
    notify();
  }

  function bindRail() {
    var rail = $("state-rail");
    if (!rail) return;
    rail.querySelectorAll(".state-rail-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        handleStageClick(btn.getAttribute("data-stage"), btn.getAttribute("data-filter"));
      });
    });
  }

  function bindHotkeys() {
    document.addEventListener(
      "keydown",
      function (e) {
        if (!isFounderShell()) return;
        if (isTypingTarget()) return;

        if (!e.shiftKey || e.metaKey || e.ctrlKey || e.altKey) return;

        var key = e.key;
        if (key === "!" || key === "1") {
          e.preventDefault();
          openDirective();
          return;
        }
        if (key === "@" || key === "2") {
          e.preventDefault();
          setFilter("all");
          return;
        }
        if (key === "#" || key === "3") {
          e.preventDefault();
          setFilter("grounded");
          return;
        }
        if (key === "$" || key === "4") {
          e.preventDefault();
          setFilter("redhat");
          return;
        }
        if (key === "%" || key === "5") {
          e.preventDefault();
          setFilter("dossier");
          triggerDossierExport();
        }
      },
      true
    );
  }

  function showRail() {
    var rail = $("state-rail");
    if (rail) rail.hidden = false;
  }

  function hideRail() {
    var rail = $("state-rail");
    if (rail) rail.hidden = true;
  }

  function init() {
    bindRail();
    bindHotkeys();
    if (isFounderShell()) {
      showRail();
      setFilter("all", { silent: true });
    }
    document.addEventListener("assure:runs-updated", function (ev) {
      var detail = (ev && ev.detail) || {};
      syncFromRuns(detail.runs || [], detail.streaming);
    });
    document.addEventListener("assure:pipeline-status", function (ev) {
      var msg = ev && ev.detail && ev.detail.message;
      store.streaming = !!msg;
      notify();
    });
  }

  global.AssureStateRail = {
    init: init,
    setFilter: setFilter,
    getFilter: getFilter,
    getStore: getStore,
    subscribe: subscribe,
    syncFromRuns: syncFromRuns,
    showRail: showRail,
    hideRail: hideRail,
    isTypingTarget: isTypingTarget,
  };

  document.addEventListener("DOMContentLoaded", init);
})(window);
