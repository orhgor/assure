/**
 * Founder workbench — left sidebar, right drawer, grid pane states.
 */
(function (global) {
  "use strict";

  var BREAKPOINT = 1440;
  var leftOpen = true;
  var rightOpen = false;
  var sidebarTab = "runs";
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
    if (leftBeforeResponsive) {
      leftOpen = true;
      leftBeforeResponsive = null;
    }
    applyGridClasses();
  }

  function isLeftOpen() {
    return leftOpen;
  }

  function isRightOpen() {
    return rightOpen;
  }

  function setSidebarTab(tab) {
    sidebarTab = tab === "sources" ? "sources" : "runs";
    var runsStack = $("runs-stack");
    var sourcesPanel = $("panel-sources");
    var search = $("runs-stack-search");
    document.querySelectorAll(".founder-sidebar-tab").forEach(function (btn) {
      var active = btn.getAttribute("data-sidebar-tab") === sidebarTab;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (runsStack) runsStack.hidden = sidebarTab !== "runs";
    if (sourcesPanel) {
      if (sidebarTab === "sources") {
        sourcesPanel.removeAttribute("hidden");
        sourcesPanel.classList.add("active");
        sourcesPanel.setAttribute("aria-hidden", "false");
      } else {
        sourcesPanel.setAttribute("hidden", "");
        sourcesPanel.classList.remove("active");
        sourcesPanel.setAttribute("aria-hidden", "true");
      }
    }
    if (search) search.hidden = sidebarTab !== "runs";
    if (sidebarTab === "sources" && global.AssureSubstrateVault && global.AssureSubstrateVault.fetchList) {
      global.AssureSubstrateVault.fetchList();
    }
    if (!leftOpen) toggleLeft(true);
  }

  function getSidebarTab() {
    return sidebarTab;
  }

  function renderDrawerFindings() {
    var body = $("workbench-drawer-body");
    if (!body) return;
    var runs = (global.AssureRunsStack && global.AssureRunsStack.getRuns()) || [];
    var html = "";
    runs.forEach(function (run) {
      (run.redhat_findings || []).forEach(function (f) {
        if (f.status !== "open") return;
        html +=
          '<article class="drawer-finding" data-run-id="' +
          (run.id || "") +
          '" data-finding-id="' +
          (f.id || "") +
          '">' +
          "<h4>" +
          (f.title || "Finding") +
          "</h4>" +
          "<p>" +
          (f.content || "") +
          "</p>" +
          '<button type="button" class="btn btn-outline btn-sm drawer-accept-fix" data-action="accept-finding">' +
          translate("founder.runs.accept_fix", "Accept Fix") +
          "</button>" +
          "</article>";
      });
    });
    if (!html) {
      html = '<p class="hint">' + translate("founder.drawer.empty", "No open audit findings.") + "</p>";
    }
    body.innerHTML = html;
    body.querySelectorAll(".drawer-accept-fix").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var article = btn.closest(".drawer-finding");
        if (!article) return;
        document.dispatchEvent(
          new CustomEvent("assure:drawer-accept-fix", {
            detail: {
              runId: article.getAttribute("data-run-id"),
              findingId: article.getAttribute("data-finding-id"),
            },
          })
        );
      });
    });
  }

  function refreshDrawerBody() {
    var title = $("workbench-drawer-title");
    if (title) title.textContent = translate("founder.drawer.audit_title", "Audit & findings");
    renderDrawerFindings();
  }

  function openAuditDrawer() {
    if (global.AssureStateRail && typeof global.AssureStateRail.setFilter === "function") {
      global.AssureStateRail.setFilter("redhat");
    }
    toggleRight(true);
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
    openAuditDrawer: openAuditDrawer,
    refreshDrawerBody: refreshDrawerBody,
    isNarrowViewport: isNarrowViewport,
  };

  document.addEventListener("DOMContentLoaded", init);
})(window);
