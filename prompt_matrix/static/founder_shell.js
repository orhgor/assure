/**
 * Founder workbench shell — default /app layout (Runs Stack + Active Draft).
 * Legacy workspaces remain available via ?legacy=1, ?view=projects, or Show Workspaces.
 */
(function (global) {
  "use strict";

  var LEFT_LEGACY_PANELS = [
    "panel-write",
    "panel-draft",
    "panel-sources",
    "panel-analytics",
    "view-surgical",
  ];

  function $(id) {
    return document.getElementById(id);
  }

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function isLegacyWorkbench() {
    return document.body.classList.contains("legacy-workbench");
  }

  function isFounderShell() {
    return document.body.classList.contains("founder-workbench") && !isLegacyWorkbench();
  }

  function setSidebarFounderMode(on) {
    document.querySelectorAll(".founder-sidebar-legacy").forEach(function (link) {
      link.hidden = !!on;
    });
    var showLegacy = $("founder-show-workspaces");
    if (showLegacy) showLegacy.hidden = !on;
  }

  function hideLegacyLeftPanels() {
    LEFT_LEGACY_PANELS.forEach(function (id) {
      var el = $(id);
      if (!el) return;
      el.hidden = true;
      el.classList.remove("active");
    });
  }

  function applyFounderShell(view) {
    view = view || "runs";
    hideLegacyLeftPanels();
    closeSourcesDrawer();

    var runs = $("panel-runs");
    var analytics = $("panel-analytics");
    if (view === "analytics") {
      if (runs) {
        runs.hidden = true;
        runs.classList.remove("active");
      }
      if (analytics) {
        analytics.hidden = false;
        analytics.classList.add("active");
      }
    } else if (runs) {
      runs.hidden = false;
      runs.classList.add("active");
      if (analytics) {
        analytics.hidden = true;
        analytics.classList.remove("active");
      }
    }

    var onboarding = $("canvas-onboarding-state");
    if (onboarding) onboarding.hidden = true;

    var stage = $("canvas-stage");
    if (stage) stage.hidden = false;

    var draftShell = $("founder-draft-shell");
    if (draftShell) draftShell.hidden = view !== "runs";

    var jdfTarget = $("jdf-render-target");
    if (jdfTarget) jdfTarget.hidden = true;

    var sourcePreview = $("canvas-source-preview");
    if (sourcePreview) sourcePreview.hidden = true;

    var analyticsCanvas = $("canvas-analytics-view");
    if (analyticsCanvas) analyticsCanvas.hidden = view !== "analytics";

    var toolbar = $("canvas-toolbar");
    if (toolbar) toolbar.hidden = true;

    var statusBar = $("workbench-status-bar");
    if (statusBar) statusBar.hidden = true;

    var layout = $("assure-app");
    var appContent = layout && layout.querySelector(".app-content");
    if (appContent) {
      appContent.classList.toggle("mode-full-view", view === "analytics");
      appContent.classList.toggle("mode-workspace", view !== "analytics");
    }

    setSidebarFounderMode(true);

    document.body.setAttribute("data-assure-view", view);
    document.body.setAttribute("data-assure-tool", view === "analytics" ? "analytics" : "runs");

    if (view === "analytics" && global.AssureAnalytics && typeof global.AssureAnalytics.render === "function") {
      global.AssureAnalytics.render();
    }
  }

  function openSourcesDrawer() {
    var panel = $("panel-sources");
    if (!panel) return;
    document.body.classList.add("sources-drawer-open");
    panel.hidden = false;
    panel.classList.add("active");
    panel.setAttribute("aria-hidden", "false");
    if (global.AssureSubstrateVault && typeof global.AssureSubstrateVault.fetchList === "function") {
      global.AssureSubstrateVault.fetchList();
    }
    var backdrop = $("sources-drawer-backdrop");
    if (backdrop) {
      backdrop.hidden = false;
      backdrop.setAttribute("aria-hidden", "false");
    }
    var search = $("substrate-vault-search");
    if (search && typeof search.focus === "function") {
      window.setTimeout(function () {
        search.focus();
      }, 0);
    }
  }

  function closeSourcesDrawer() {
    document.body.classList.remove("sources-drawer-open");
    var panel = $("panel-sources");
    if (panel && isFounderShell()) {
      panel.hidden = true;
      panel.classList.remove("active");
      panel.setAttribute("aria-hidden", "true");
    }
    var backdrop = $("sources-drawer-backdrop");
    if (backdrop) {
      backdrop.hidden = true;
      backdrop.setAttribute("aria-hidden", "true");
    }
  }

  function enableLegacyWorkspaces() {
    try {
      localStorage.setItem("assure_founder_workbench", "0");
    } catch (_) {}
    document.body.classList.add("legacy-workbench");
    document.body.classList.remove("sources-drawer-open");
    setSidebarFounderMode(false);
    if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
      global.AssureNav.switchView("projects", { replaceHash: true, persist: true });
    }
  }

  function init() {
    if (!isFounderShell()) return;

    applyFounderShell("runs");

    var attachBtn = $("founder-attach-sources");
    if (attachBtn) {
      attachBtn.addEventListener("click", function (e) {
        e.preventDefault();
        openSourcesDrawer();
      });
    }

    var closeBtn = $("sources-drawer-close");
    if (closeBtn) closeBtn.addEventListener("click", closeSourcesDrawer);

    var backdrop = $("sources-drawer-backdrop");
    if (backdrop) backdrop.addEventListener("click", closeSourcesDrawer);

    var showWorkspaces = $("founder-show-workspaces");
    if (showWorkspaces) {
      showWorkspaces.addEventListener("click", function (e) {
        e.preventDefault();
        enableLegacyWorkspaces();
      });
    }

    var draftEditor = $("founder-draft-editor");
    if (draftEditor) {
      draftEditor.addEventListener("dragover", function (e) {
        e.preventDefault();
        draftEditor.classList.add("is-dragover");
      });
      draftEditor.addEventListener("dragleave", function () {
        draftEditor.classList.remove("is-dragover");
      });
      draftEditor.addEventListener("drop", function (e) {
        e.preventDefault();
        draftEditor.classList.remove("is-dragover");
        openSourcesDrawer();
        var fileInput = $("substrate-vault-file-input");
        if (fileInput && e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
          fileInput.files = e.dataTransfer.files;
          fileInput.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
    }

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && document.body.classList.contains("sources-drawer-open")) {
        e.preventDefault();
        closeSourcesDrawer();
      }
    });
  }

  global.AssureFounderShell = {
    isFounderShell: isFounderShell,
    isLegacyWorkbench: isLegacyWorkbench,
    apply: applyFounderShell,
    openSourcesDrawer: openSourcesDrawer,
    closeSourcesDrawer: closeSourcesDrawer,
    enableLegacyWorkspaces: enableLegacyWorkspaces,
    init: init,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(window);
