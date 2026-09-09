/**
 * Founder workbench — left sidebar, right drawer, grid pane states.
 */
(function (global) {
  "use strict";

  var BREAKPOINT = 1440;
  var leftOpen = true;
  var rightOpen = false;
  var sidebarTab = "runs";
  var drawerMode = null;
  var drawerDetail = null;
  var leftBeforeResponsive = null;

  function $(id) {
    return document.getElementById(id);
  }

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function isFounderShell() {
    return (
      document.body.classList.contains("founder-workbench") &&
      !document.body.classList.contains("legacy-workbench")
    );
  }

  function container() {
    return document.querySelector(".app-container.founder-workbench");
  }

  function isNarrowViewport() {
    return window.innerWidth < BREAKPOINT;
  }

  function isTypingTarget() {
    if (global.AssureStateRail && typeof global.AssureStateRail.isTypingTarget === "function") {
      return global.AssureStateRail.isTypingTarget();
    }
    var el = document.activeElement;
    if (!el) return false;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return true;
    if (el.isContentEditable) return true;
    return false;
  }

  function applyGridClasses() {
    if (rightOpen && isNarrowViewport() && leftOpen) {
      leftBeforeResponsive = true;
      leftOpen = false;
    }
    var root = container();
    if (!root) return;
    root.classList.toggle("pane-left-closed", !leftOpen);
    root.classList.toggle("pane-right-open", rightOpen);
    root.classList.toggle("pane-left-open", leftOpen);
    root.classList.toggle("pane-narrow", isNarrowViewport());
    var left = $("left-pane");
    if (left) {
      left.setAttribute("aria-hidden", leftOpen ? "false" : "true");
    }
    var drawer = $("workbench-right-drawer");
    if (drawer) {
      if (rightOpen) {
        drawer.removeAttribute("hidden");
        drawer.setAttribute("aria-hidden", "false");
      } else {
        drawer.setAttribute("hidden", "");
        drawer.setAttribute("aria-hidden", "true");
      }
    }
  }

  function enforceResponsiveLeft() {
    if (!isFounderShell()) return;
    if (rightOpen && isNarrowViewport()) {
      if (leftOpen) {
        leftBeforeResponsive = true;
        leftOpen = false;
      }
    } else if (!rightOpen && leftBeforeResponsive) {
      leftOpen = true;
      leftBeforeResponsive = null;
    }
    applyGridClasses();
  }

  function toggleLeft(force) {
    var next = typeof force === "boolean" ? force : !leftOpen;
    if (next && rightOpen && isNarrowViewport()) return false;
    leftOpen = next;
    if (leftOpen) leftBeforeResponsive = null;
    applyGridClasses();
    return leftOpen;
  }

  function toggleRight(force) {
    if (typeof force === "boolean") rightOpen = force;
    else rightOpen = !rightOpen;
    if (rightOpen) enforceResponsiveLeft();
    else {
      if (leftBeforeResponsive) {
        leftOpen = true;
        leftBeforeResponsive = null;
      }
      applyGridClasses();
    }
    if (rightOpen) refreshDrawerBody();
    return rightOpen;
  }

  function closeRight() {
    rightOpen = false;
    drawerMode = null;
    drawerDetail = null;
    if (leftBeforeResponsive) {
      leftOpen = true;
      leftBeforeResponsive = null;
    }
    applyGridClasses();
    hideDrawerPanels();
  }

  function hideDrawerPanels() {
    ["drawer-evidence", "drawer-redhat", "drawer-export"].forEach(function (id) {
      var el = $(id);
      if (!el) return;
      el.hidden = true;
      el.setAttribute("aria-hidden", "true");
    });
  }

  function drawerTitleForMode(mode) {
    if (mode === "evidence") {
      return translate("evidence.inspector.title", "Evidence Inspector");
    }
    if (mode === "export") {
      return translate("founder.export.drawer_title", "Export");
    }
    return translate("founder.drawer.audit_title", "Audit & findings");
  }

  function activateDrawerMode(mode, detail) {
    hideDrawerPanels();
    var panelId =
      mode === "evidence" ? "drawer-evidence" : mode === "export" ? "drawer-export" : "drawer-redhat";
    var panel = $(panelId);
    if (panel) {
      panel.hidden = false;
      panel.setAttribute("aria-hidden", "false");
    }
    var title = $("workbench-drawer-title");
    if (title) title.textContent = drawerTitleForMode(mode);
    if (mode === "evidence" && global.AssureEvidenceDrawer && global.AssureEvidenceDrawer.activate) {
      global.AssureEvidenceDrawer.activate(detail || drawerDetail || {});
    } else if (mode === "redhat" && global.AssureRedhatDrawer && global.AssureRedhatDrawer.activate) {
      global.AssureRedhatDrawer.activate();
    } else if (mode === "export" && global.AssureExportDrawer && global.AssureExportDrawer.activate) {
      global.AssureExportDrawer.activate();
    }
  }

  function openDrawer(mode, detail) {
    drawerMode = mode || "redhat";
    drawerDetail = detail || null;
    if (detail && detail.runId && global.AssureRunsStack && global.AssureRunsStack.setSelectedRunId) {
      global.AssureRunsStack.setSelectedRunId(detail.runId);
    }
    toggleRight(true);
    activateDrawerMode(drawerMode, drawerDetail);
  }

  function getDrawerMode() {
    return drawerMode;
  }

  function isLeftOpen() {
    return leftOpen;
  }

  function isRightOpen() {
    return rightOpen;
  }

  function projectId() {
    if (global.AssureFounderMode && typeof global.AssureFounderMode.getWorkspaceId === "function") {
      return global.AssureFounderMode.getWorkspaceId();
    }
    return global.__ASSURE_PROJECT_ID__ || "founder";
  }

  function toast(message, kind) {
    if (global.AssureToast && typeof global.AssureToast.show === "function") {
      global.AssureToast.show(message, kind || "info");
    }
  }

  function formatTimestamp(raw) {
    if (!raw) return "";
    try {
      var d = new Date(String(raw).replace(" ", "T") + "Z");
      if (isNaN(d.getTime())) return String(raw);
      return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
    } catch (_) {
      return String(raw);
    }
  }

  function loadSources() {
    var loading = $("founder-sources-loading");
    var panel = $("panel-sources");
    if (loading) loading.hidden = false;
    if (panel) panel.classList.add("is-loading");
    var done = function () {
      if (loading) loading.hidden = true;
      if (panel) panel.classList.remove("is-loading");
    };
    if (global.AssureSubstrateVault && typeof global.AssureSubstrateVault.fetchList === "function") {
      return Promise.resolve(global.AssureSubstrateVault.fetchList()).then(done).catch(function () {
        done();
        toast(translate("founder.sources.error", "Could not load sources."), "error");
      });
    }
    done();
    return Promise.resolve();
  }

  function renderVersionsTimeline(revisions) {
    var timeline = $("founder-versions-timeline");
    var empty = $("founder-versions-empty");
    if (!timeline) return;
    timeline.innerHTML = "";
    var rows = revisions || [];
    if (empty) empty.hidden = rows.length > 0;
    rows.forEach(function (rev) {
      var li = document.createElement("li");
      li.className = "founder-version-entry";
      li.setAttribute("data-version", String(rev.version || ""));

      var head = document.createElement("div");
      head.className = "founder-version-head";
      var label = document.createElement("span");
      label.className = "founder-version-label";
      label.textContent = "v" + String(rev.version || "?");
      var when = document.createElement("time");
      when.className = "founder-version-time";
      when.textContent = formatTimestamp(rev.timestamp || rev.created_at);
      head.appendChild(label);
      head.appendChild(when);

      var meta = document.createElement("p");
      meta.className = "founder-version-meta";
      meta.textContent = String(rev.mutation_type || "REVISION");

      var summary = document.createElement("p");
      summary.className = "founder-version-summary hint";
      summary.textContent = rev.change_summary || "";

      var restoreBtn = document.createElement("button");
      restoreBtn.type = "button";
      restoreBtn.className = "btn btn-outline btn-sm founder-version-restore";
      restoreBtn.setAttribute("data-version", String(rev.version || ""));
      restoreBtn.textContent = translate("founder.versions.restore", "Restore");

      restoreBtn.addEventListener("click", function () {
        restoreVersion(parseInt(restoreBtn.getAttribute("data-version"), 10));
      });

      li.appendChild(head);
      li.appendChild(meta);
      if (summary.textContent) li.appendChild(summary);
      li.appendChild(restoreBtn);
      timeline.appendChild(li);
    });
  }

  function restoreVersion(version) {
    if (!version || version < 1) return;
    var pid = projectId();
    fetch("/api/projects/" + encodeURIComponent(pid) + "/restore", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: version, workspace_id: pid }),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok && data.ok !== false, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.data.document) {
          toast(
            (result.data && result.data.error) ||
              translate("founder.versions.restore_error", "Restore failed."),
            "error"
          );
          return;
        }
        if (
          global.AssureFounderDraft &&
          typeof global.AssureFounderDraft.applyRestoredDocument === "function"
        ) {
          global.AssureFounderDraft.applyRestoredDocument(result.data.document, version);
          return;
        }
        var editorApi = global.AssureTiptapEditor;
        if (editorApi && typeof editorApi.setContentFromJdf === "function") {
          editorApi.setContentFromJdf(result.data.document);
          toast(
            translate("founder.versions.restored", "Draft restored to version {version}").replace(
              "{version}",
              String(version)
            ),
            "success"
          );
        }
      })
      .catch(function () {
        toast(translate("founder.versions.restore_error", "Restore failed."), "error");
      });
  }

  function loadVersions() {
    var loading = $("founder-versions-loading");
    var timeline = $("founder-versions-timeline");
    var empty = $("founder-versions-empty");
    if (loading) loading.hidden = false;
    if (timeline) timeline.innerHTML = "";
    if (empty) empty.hidden = true;
    var pid = projectId();
    return fetch("/api/projects/" + encodeURIComponent(pid) + "/history", {
      credentials: "same-origin",
    })
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        var revisions = (data && (data.history || data.revisions)) || [];
        renderVersionsTimeline(revisions);
      })
      .catch(function () {
        toast(translate("founder.versions.error", "Could not load version history."), "error");
        if (empty) {
          empty.hidden = false;
          empty.textContent = translate("founder.versions.error", "Could not load version history.");
        }
      })
      .finally(function () {
        if (loading) loading.hidden = true;
      });
  }

  function setSidebarTab(tab) {
    if (tab === "sources" || tab === "versions") sidebarTab = tab;
    else sidebarTab = "runs";
    var runsStack = $("runs-stack");
    var sourcesPanel = $("panel-sources");
    var versionsPanel = $("pane-versions");
    var search = $("runs-stack-search");
    document.querySelectorAll(".founder-sidebar-tab").forEach(function (btn) {
      var active = btn.getAttribute("data-sidebar-tab") === sidebarTab;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (runsStack) runsStack.hidden = sidebarTab !== "runs";
    if (sourcesPanel) {
      var sourcesOn = sidebarTab === "sources";
      sourcesPanel.hidden = !sourcesOn;
      sourcesPanel.classList.toggle("active", sourcesOn);
      sourcesPanel.setAttribute("aria-hidden", sourcesOn ? "false" : "true");
    }
    if (versionsPanel) {
      var versionsOn = sidebarTab === "versions";
      versionsPanel.hidden = !versionsOn;
      versionsPanel.classList.toggle("active", versionsOn);
      versionsPanel.setAttribute("aria-hidden", versionsOn ? "false" : "true");
    }
    if (search) search.hidden = sidebarTab !== "runs";
    if (sidebarTab === "sources") loadSources();
    if (sidebarTab === "versions") loadVersions();
    if (!leftOpen) toggleLeft(true);
  }

  function getSidebarTab() {
    return sidebarTab;
  }

  function refreshDrawerBody() {
    if (!drawerMode) drawerMode = "redhat";
    activateDrawerMode(drawerMode, drawerDetail);
  }

  function openAuditDrawer() {
    if (global.AssureStateRail && typeof global.AssureStateRail.setFilter === "function") {
      global.AssureStateRail.setFilter("redhat");
    }
    openDrawer("redhat");
  }

  function bindHotkeys() {
    document.addEventListener(
      "keydown",
      function (e) {
        if (!isFounderShell()) return;
        if (e.key === "Escape") {
          if (global.AssureFounderInlineDiff && global.AssureFounderInlineDiff.handleEscape()) {
            e.preventDefault();
            return;
          }
          if (rightOpen) {
            e.preventDefault();
            closeRight();
          }
          return;
        }
        if (!e.metaKey && !e.ctrlKey) return;
        if (isTypingTarget()) return;
        var key = String(e.key || "").toLowerCase();
        if (key === "b") {
          e.preventDefault();
          toggleLeft();
          return;
        }
        if (key === "i") {
          e.preventDefault();
          if (rightOpen) closeRight();
          else openAuditDrawer();
        }
      },
      true
    );
  }

  function bindResize() {
    window.addEventListener("resize", function () {
      if (!isFounderShell()) return;
      enforceResponsiveLeft();
    });
  }

  function bindDrawerClose() {
    var closeBtn = $("workbench-drawer-close");
    if (closeBtn) closeBtn.addEventListener("click", closeRight);
  }

  function bindSidebarTabs() {
    document.querySelectorAll(".founder-sidebar-tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setSidebarTab(btn.getAttribute("data-sidebar-tab") || "runs");
      });
    });
  }

  function init() {
    if (!isFounderShell()) return;
    applyGridClasses();
    setSidebarTab("runs");
    bindHotkeys();
    bindDrawerClose();
    bindSidebarTabs();
    bindResize();
    document.addEventListener("assure:runs-updated", function () {
      if (rightOpen) refreshDrawerBody();
    });
  }

  global.AssureWorkbenchPanes = {
    init: init,
    toggleLeft: toggleLeft,
    toggleRight: toggleRight,
    closeRight: closeRight,
    isLeftOpen: isLeftOpen,
    isRightOpen: isRightOpen,
    setSidebarTab: setSidebarTab,
    getSidebarTab: getSidebarTab,
    loadSources: loadSources,
    loadVersions: loadVersions,
    restoreVersion: restoreVersion,
    openDrawer: openDrawer,
    getDrawerMode: getDrawerMode,
    openAuditDrawer: openAuditDrawer,
    refreshDrawerBody: refreshDrawerBody,
    isNarrowViewport: isNarrowViewport,
  };

  document.addEventListener("DOMContentLoaded", init);
})(window);
