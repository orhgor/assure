/**
 * Workbench context: active tab, project, canvas view.
 * Complements AssureNav — does not replace hash/persist routing.
 */
(function (global) {
  "use strict";

  var TAB_BY_VIEW = {
    projects: "write",
    generate: "draft",
    vault: "sources",
    library: "sources",
    analytics: "analytics",
    surgical: "polish",
  };

  var PANEL_BY_TAB = {
    write: "panel-write",
    draft: "panel-draft",
    sources: "panel-sources",
    analytics: "panel-analytics",
  };

  var CANVAS_BY_TAB = {
    write: "canvas-onboarding-state",
    draft: "jdf-render-target",
    sources: "canvas-source-preview",
    analytics: "canvas-analytics-view",
    polish: "jdf-render-target",
  };

  function $(id) {
    return document.getElementById(id);
  }

  function hasNamedProject() {
    var pid = String(global.__ASSURE_PROJECT_ID__ || "").trim();
    return !!pid && pid !== "default";
  }

  var AssureWorkbenchContext = {
    activeTab: "draft",
    activeProject: "",
    documentStatus: "idle",
    viewMode: "workspace",

    init: function () {
      var self = this;
      document.addEventListener("assure:view", function (ev) {
        var view = (ev.detail && ev.detail.view) || "";
        self.activeTab = TAB_BY_VIEW[view] || self.activeTab;
        self.render();
      });
      document.addEventListener("assure:project", function (ev) {
        self.activeProject = (ev.detail && ev.detail.projectId) || global.__ASSURE_PROJECT_ID__ || "";
        self.render();
      });
      this.activeProject = global.__ASSURE_PROJECT_ID__ || "";
      if (global.AssureNav && global.AssureNav.activeView) {
        this.activeTab = TAB_BY_VIEW[global.AssureNav.activeView] || "draft";
      }
      this.render();
    },

    setTab: function (tab) {
      var view =
        tab === "write"
          ? "projects"
          : tab === "sources"
            ? "vault"
            : tab === "analytics"
              ? "analytics"
              : "generate";
      if (global.AssureNav) global.AssureNav.switchView(view);
    },

    render: function () {
      var tab = this.activeTab || "draft";
      var layout = $("assure-app");
      if (layout) layout.setAttribute("data-active-tab", tab);

      Object.keys(PANEL_BY_TAB).forEach(function (name) {
        var el = $(PANEL_BY_TAB[name]);
        if (!el) return;
        var on = name === tab;
        el.hidden = !on;
        el.classList.toggle("active", on);
      });

      var surgical = $("view-surgical");
      if (surgical) {
        var polishOn = tab === "polish";
        surgical.hidden = !polishOn;
        surgical.classList.toggle("active", polishOn);
      }

      var statusBar = $("workbench-status-bar");
      if (statusBar) {
        var showStatus = tab === "draft" || (tab === "sources" && hasNamedProject());
        statusBar.hidden = !showStatus;
      }

      var canvasIds = {
        "canvas-onboarding-state": tab === "write",
        "jdf-render-target": tab === "draft" || tab === "polish",
        "canvas-source-preview": tab === "sources",
        "canvas-analytics-view": tab === "analytics",
      };
      Object.keys(canvasIds).forEach(function (id) {
        var node = $(id);
        if (!node) return;
        node.hidden = !canvasIds[id];
      });
      var stage = $("canvas-stage");
      if (stage) stage.hidden = tab === "write" || tab === "sources" || tab === "analytics";

      var toolbar = $("canvas-toolbar");
      if (toolbar) toolbar.hidden = tab !== "draft" && tab !== "polish";

      var overlay = $("confidence-overlay-wrap");
      if (overlay && tab !== "draft" && tab !== "polish") overlay.hidden = true;

      var strip = $("draft-preview-strip");
      if (strip && tab !== "draft") strip.hidden = true;
    },
  };

  global.AssureWorkbenchContext = AssureWorkbenchContext;
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      AssureWorkbenchContext.init();
    });
  } else {
    AssureWorkbenchContext.init();
  }
})(window);
