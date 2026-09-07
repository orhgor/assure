/**
 * Founder workbench shell — default /app layout (Runs Stack + Active Draft).
 * Legacy workspaces remain available via ?legacy=1, ?view=projects, or Show Workspaces.
 */
(function (global) {
  "use strict";

  var PARK_NODE_IDS = [
    "app-sidebar",
    "panel-write",
    "panel-draft",
    "view-surgical",
    "panel-analytics",
    "workbench-status-bar",
  ];

  var HEADER_HIDE_IDS = ["sidebar-toggle", "document-chrome"];

  var parked = {};
  var parkRoot = null;
  var legacyDetached = false;

  function $(id) {
    return document.getElementById(id);
  }

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function isLegacyWorkbench() {
    if (
      global.AssureFounderMode &&
      typeof global.AssureFounderMode.isEnabled === "function"
    ) {
      return !global.AssureFounderMode.isEnabled();
    }
    return document.body.classList.contains("legacy-workbench");
  }

  function isFounderShell() {
    return !isLegacyWorkbench() && document.body.classList.contains("founder-workbench");
  }

  function looksLikeSlug(value) {
    var s = String(value || "").trim();
    if (!s || s.indexOf(" ") >= 0) return false;
    return /^[a-z0-9]+(-[a-z0-9]+)+$/i.test(s) && s.length <= 48;
  }

  function ensureParkRoot() {
    if (parkRoot) return parkRoot;
    parkRoot = $("founder-legacy-park");
    if (!parkRoot) {
      parkRoot = document.createElement("div");
      parkRoot.id = "founder-legacy-park";
      parkRoot.hidden = true;
      parkRoot.setAttribute("aria-hidden", "true");
      document.body.appendChild(parkRoot);
    }
    return parkRoot;
  }

  function parkNodeById(id) {
    var el = $(id);
    if (!el || parked[id]) return;
    parked[id] = { el: el, parent: el.parentNode, next: el.nextSibling };
    ensureParkRoot().appendChild(el);
  }

  function detachLegacyDom() {
    if (!isFounderShell() || legacyDetached) return;
    PARK_NODE_IDS.forEach(parkNodeById);
    HEADER_HIDE_IDS.forEach(function (id) {
      var node = $(id);
      if (node) node.hidden = true;
    });
    document.querySelectorAll(".founder-legacy-chrome").forEach(function (node) {
      node.hidden = true;
    });
    var founderTools = $("founder-header-tools");
    if (founderTools) founderTools.hidden = false;
    var layout = $("assure-app");
    if (layout) layout.classList.add("founder-shell-layout");
    legacyDetached = true;
    updateWorkspaceTitle();
  }

  function restoreLegacyDom() {
    if (!legacyDetached) return;
    Object.keys(parked).forEach(function (id) {
      var rec = parked[id];
      if (!rec || !rec.el || !rec.parent) return;
      rec.parent.insertBefore(rec.el, rec.next);
    });
    parked = {};
    legacyDetached = false;
    HEADER_HIDE_IDS.forEach(function (id) {
      var node = $(id);
      if (!node) return;
      if (id === "document-chrome") node.hidden = true;
      else node.hidden = false;
    });
    document.querySelectorAll(".founder-legacy-chrome").forEach(function (node) {
      node.hidden = false;
    });
    var founderTools = $("founder-header-tools");
    if (founderTools) founderTools.hidden = true;
    var layout = $("assure-app");
    if (layout) layout.classList.remove("founder-shell-layout");
  }

  function resolveWorkspaceTitle() {
    var pid = String(global.__ASSURE_PROJECT_ID__ || "default").trim();
    var title = "";
    if (global.AssureProjects && typeof global.AssureProjects.titleFor === "function") {
      title = global.AssureProjects.titleFor(pid) || "";
    }
    if (!title && pid !== "default" && !looksLikeSlug(pid)) title = pid;
    title = String(title || "").trim();
    if (!title || title === "default" || title.toLowerCase() === "default project" || looksLikeSlug(title)) {
      return translate("founder.workspace.untitled", "Untitled Workspace");
    }
    return title;
  }

  function updateWorkspaceTitle() {
    var titleEl = $("founder-workspace-title");
    if (!titleEl) return;
    titleEl.textContent = resolveWorkspaceTitle();
  }

  function setSidebarFounderMode(on) {
    document.querySelectorAll(".founder-sidebar-legacy").forEach(function (link) {
      link.hidden = !!on;
    });
    var showLegacy = $("founder-show-workspaces");
    if (showLegacy) showLegacy.hidden = !on;
  }

  function hideLegacyLeftPanels() {
    ["panel-write", "panel-draft", "panel-sources", "panel-analytics", "view-surgical"].forEach(function (id) {
      var el = $(id);
      if (!el) return;
      el.hidden = true;
      el.classList.remove("active");
    });
  }

  function applyFounderShell(view) {
    view = view || "runs";
    detachLegacyDom();
    hideLegacyLeftPanels();
    closeSourcesDrawer();

    var runs = $("panel-runs");
    if (runs) {
      runs.hidden = false;
      runs.classList.add("active");
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
    if (analyticsCanvas) analyticsCanvas.hidden = true;

    var toolbar = $("canvas-toolbar");
    if (toolbar) toolbar.hidden = true;

    var strip = $("draft-preview-strip");
    if (strip) strip.hidden = true;

    var overlay = $("confidence-overlay-wrap");
    if (overlay) overlay.hidden = true;

    var layout = $("assure-app");
    var appContent = layout && layout.querySelector(".app-content");
    if (appContent) {
      appContent.classList.remove("mode-full-view");
      appContent.classList.add("mode-workspace");
    }

    setSidebarFounderMode(true);
    updateWorkspaceTitle();

    document.body.setAttribute("data-assure-view", view);
    document.body.setAttribute("data-assure-tool", "runs");
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
    if (global.AssureFounderMode && typeof global.AssureFounderMode.applyBodyClasses === "function") {
      global.AssureFounderMode.applyBodyClasses();
    } else {
      document.body.classList.remove("founder-workbench");
      document.body.classList.add("legacy-workbench");
    }
    document.body.classList.remove("sources-drawer-open");
    restoreLegacyDom();
    setSidebarFounderMode(false);
    if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
      global.AssureNav.switchView("projects", { replaceHash: true, persist: true });
    }
  }

  function bindUi() {
    var attachBtn = $("founder-attach-sources");
    if (attachBtn) {
      attachBtn.addEventListener("click", function (e) {
        e.preventDefault();
        openSourcesDrawer();
      });
    }

    var cmdkBtn = $("founder-cmdk-btn");
    if (cmdkBtn) {
      cmdkBtn.addEventListener("click", function (e) {
        e.preventDefault();
        if (global.AssureCommandBar && typeof global.AssureCommandBar.open === "function") {
          global.AssureCommandBar.open();
        }
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

    document.addEventListener("assure:project", function () {
      updateWorkspaceTitle();
    });
  }

  function init() {
    if (!isFounderShell()) return;
    applyFounderShell("runs");
    bindUi();
  }

  global.AssureFounderShell = {
    isFounderShell: isFounderShell,
    isLegacyWorkbench: isLegacyWorkbench,
    apply: applyFounderShell,
    detachLegacyDom: detachLegacyDom,
    restoreLegacyDom: restoreLegacyDom,
    openSourcesDrawer: openSourcesDrawer,
    closeSourcesDrawer: closeSourcesDrawer,
    enableLegacyWorkspaces: enableLegacyWorkspaces,
    updateWorkspaceTitle: updateWorkspaceTitle,
    init: init,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(window);
