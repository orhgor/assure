/**
 * Argument Spine: tree outline of the current JDF document
 * (Thesis → Argument Branches → nodes). Driven by
 * assure:jdf:rendered / assure:jdf:selected from jdf_canvas.js.
 *
 * Status dots and lock glyphs reuse JDFCanvasManager.computeNodeStatus
 * and nodeHasLockedNumber so the spine and canvas never disagree.
 * entities_referenced is unused here — locks come from truth_ledger.
 */
(function (global) {
  "use strict";

  var doc = global.document;
  var COLLAPSE_KEY = "assure_spine_collapsed";
  var FOLDED_KEY = "assure_spine_folded_sections";

  function $(id) {
    return doc.getElementById(id);
  }

  function t(key, fallback, params) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, params || {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback;
  }

  function canvas() {
    return global.__assureJdf || null;
  }

  function previewText(node) {
    var mgr = canvas();
    var raw = mgr && typeof mgr._nodeText === "function" ? mgr._nodeText(node) : (node.content || node.caption || node.title || "");
    raw = String(raw || "").replace(/\s+/g, " ").trim();
    if (raw.length <= 60) return raw;
    return raw.slice(0, 59) + "…";
  }

  function nodeStatus(node) {
    var mgr = canvas();
    if (mgr && typeof mgr.computeNodeStatus === "function") {
      return mgr.computeNodeStatus(node);
    }
    return "unverified";
  }

  function sectionStatus(section) {
    var children = section.children || [];
    if (!children.length) return "unverified";
    var worst = "verified";
    children.forEach(function (child) {
      var s = nodeStatus(child);
      if (s === "error") worst = "error";
      else if (s === "warning" && worst !== "error") worst = "warning";
      else if (s === "unverified" && worst === "verified") worst = "unverified";
    });
    return worst;
  }

  function hasLock(node) {
    var mgr = canvas();
    return !!(mgr && typeof mgr.nodeHasLockedNumber === "function" && mgr.nodeHasLockedNumber(node));
  }

  function loadFolded() {
    try {
      var raw = global.localStorage.getItem(FOLDED_KEY);
      var parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (_) {
      return [];
    }
  }

  function saveFolded(ids) {
    try {
      global.localStorage.setItem(FOLDED_KEY, JSON.stringify(ids));
    } catch (_) {}
  }

  var Spine = {
    _bound: false,
    _menuNodeId: null,
    foldedIds: [],
    detailsEl: null,
    treeEl: null,
    emptyEl: null,
    menuEl: null,

    bind: function () {
      if (this._bound) return;
      this._bound = true;
      var self = this;
      this.detailsEl = $("argument-spine");
      this.treeEl = $("argument-spine-tree");
      this.emptyEl = $("argument-spine-empty");
      this.menuEl = $("spine-node-menu");
      this.foldedIds = loadFolded();
      this.bindCollapse();
      this.bindMenu();

      doc.addEventListener("assure:jdf:rendered", function () {
        self.render();
      });
      doc.addEventListener("assure:jdf:selected", function (e) {
        self.markActive(e.detail && e.detail.nodeId);
      });
      doc.addEventListener("assure:i18n", function () {
        self.render();
      });

      if (canvas() && canvas().tree) this.render();
    },

    bindCollapse: function () {
      var self = this;
      if (!this.detailsEl) return;
      try {
        var stored = global.localStorage.getItem(COLLAPSE_KEY);
        var navView = global.AssureNav && global.AssureNav.activeView;
        if (navView === "generate") this.detailsEl.open = false;
        else if (stored === "1") this.detailsEl.open = false;
        else if (stored === "0") this.detailsEl.open = true;
        else this.detailsEl.open = false;
      } catch (_) {}
      this.detailsEl.addEventListener("toggle", function () {
        try {
          global.localStorage.setItem(COLLAPSE_KEY, self.detailsEl.open ? "0" : "1");
        } catch (_) {}
      });
    },

    bindMenu: function () {
      var self = this;
      if (!this.menuEl) return;
      this.menuEl.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-act]");
        if (!btn) return;
        var act = btn.getAttribute("data-act");
        if (act === "scroll") self.scrollTo(self._menuNodeId);
        else if (act === "expand") self.expandAll();
        else if (act === "collapse") self.collapseAll();
        self.closeMenu();
      });
      doc.addEventListener("click", function (e) {
        if (self.menuEl && !self.menuEl.hidden && !self.menuEl.contains(e.target)) {
          self.closeMenu();
        }
      });
      doc.addEventListener("keydown", function (e) {
        if (e.key === "Escape") self.closeMenu();
      });
    },

    openMenu: function (nodeId, clientX, clientY) {
      if (!this.menuEl || !nodeId) return;
      this._menuNodeId = nodeId;
      this.menuEl.hidden = false;
      this.menuEl.setAttribute("aria-hidden", "false");
      var pad = 8;
      var w = this.menuEl.offsetWidth || 180;
      var h = this.menuEl.offsetHeight || 120;
      var x = Math.min(Math.max(pad, clientX), window.innerWidth - w - pad);
      var y = Math.min(Math.max(pad, clientY), window.innerHeight - h - pad);
      this.menuEl.style.left = x + "px";
      this.menuEl.style.top = y + "px";
    },

    closeMenu: function () {
      if (!this.menuEl) return;
      this.menuEl.hidden = true;
      this.menuEl.setAttribute("aria-hidden", "true");
      this._menuNodeId = null;
    },

    isFolded: function (sectionId) {
      return this.foldedIds.indexOf(sectionId) >= 0;
    },

    toggleFold: function (sectionId) {
      var idx = this.foldedIds.indexOf(sectionId);
      if (idx >= 0) this.foldedIds.splice(idx, 1);
      else this.foldedIds.push(sectionId);
      saveFolded(this.foldedIds);
      this.render();
    },

    expandAll: function () {
      this.foldedIds = [];
      saveFolded(this.foldedIds);
      this.render();
    },

    collapseAll: function () {
      var tree = canvas() && canvas().tree;
      this.foldedIds = ((tree && tree.body) || []).map(function (s) {
        return s.id;
      }).filter(Boolean);
      saveFolded(this.foldedIds);
      this.render();
    },

    markActive: function (nodeId) {
      if (!this.treeEl) return;
      this.treeEl.querySelectorAll(".spine-row").forEach(function (row) {
        row.classList.toggle("spine-active", row.dataset.nodeId === nodeId);
      });
    },

    canvasElFor: function (nodeId) {
      var root = canvas() && canvas().rootEl;
      if (!root || !nodeId) return null;
      return (
        root.querySelector('.jdf-node[data-node-id="' + nodeId + '"]') ||
        root.querySelector('[data-node-id="' + nodeId + '"]')
      );
    },

    highlightCanvas: function (nodeId, on) {
      var el = this.canvasElFor(nodeId);
      if (el) el.classList.toggle("hover-highlight", !!on);
    },

    scrollTo: function (nodeId) {
      if (!nodeId) return;
      var mgr = canvas();
      if (mgr && typeof mgr.selectNodeForRefine === "function" && mgr.getNodeById(nodeId) && (mgr.getNodeById(nodeId).type !== "section")) {
        mgr.selectNodeForRefine(nodeId, { toast: false, skipViewSwitch: true, skipRender: true });
      }
      var el = this.canvasElFor(nodeId);
      if (!el) {
        var path = mgr && typeof mgr.getNodeSectionPath === "function" ? mgr.getNodeSectionPath(nodeId) : null;
        if (path && path.section && path.section.children && path.section.children[0]) {
          el = this.canvasElFor(path.section.children[0].id);
        }
      }
      if (el && typeof el.scrollIntoView === "function") {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      this.markActive(nodeId);
    },

    render: function () {
      if (!this.treeEl) return;
      var mgr = canvas();
      var tree = mgr && mgr.tree;
      var sections = (tree && tree.body) || [];
      this.treeEl.innerHTML = "";
      if (!sections.length) {
        if (this.emptyEl) this.emptyEl.hidden = false;
        this.treeEl.hidden = true;
        return;
      }
      if (this.emptyEl) this.emptyEl.hidden = true;
      this.treeEl.hidden = false;

      var self = this;
      var targetId = mgr ? mgr.surgicalTargetId : null;
      sections.forEach(function (section, sIdx) {
        self.treeEl.appendChild(self._renderSection(section, sIdx === 0, targetId));
      });
      if (targetId) this.markActive(targetId);
    },

    _statusDot: function (status) {
      var dot = doc.createElement("span");
      dot.className = "spine-dot spine-dot-" + status;
      var labelKey = {
        unverified: "spine.unverified",
        verified: "spine.verified",
        warning: "spine.warning",
        error: "spine.error",
      }[status] || "spine.unverified";
      var fallback = { unverified: "Unverified", verified: "Verified", warning: "Warning", error: "Error" }[status] || "Unverified";
      dot.setAttribute("aria-label", t(labelKey, fallback));
      dot.title = t(labelKey, fallback);
      return dot;
    },

    _renderSection: function (section, isThesis, targetId) {
      var self = this;
      var li = doc.createElement("li");
      li.className = "spine-section" + (isThesis ? " is-thesis" : "");
      li.setAttribute("role", "treeitem");
      li.setAttribute("aria-expanded", this.isFolded(section.id) ? "false" : "true");

      var row = doc.createElement("div");
      row.className = "spine-row spine-section-row";
      row.dataset.nodeId = section.id;

      var fold = doc.createElement("button");
      fold.type = "button";
      fold.className = "spine-fold";
      fold.setAttribute("aria-label", this.isFolded(section.id) ? t("spine.expand_all", "Expand All") : t("spine.collapse_all", "Collapse All"));
      fold.textContent = this.isFolded(section.id) ? "▸" : "▾";
      fold.addEventListener("click", function (e) {
        e.stopPropagation();
        self.toggleFold(section.id);
      });

      var label = doc.createElement("span");
      label.className = "spine-label";
      if (isThesis) {
        label.textContent = "🧠 " + t("spine.thesis", "Thesis");
      } else {
        label.textContent = t("spine.branch", "Argument Branch") + " · " + (section.title || "");
      }

      row.appendChild(fold);
      row.appendChild(this._statusDot(sectionStatus(section)));
      row.appendChild(label);
      this._bindRow(row, section.id);
      li.appendChild(row);

      if (!this.isFolded(section.id)) {
        var kids = doc.createElement("ul");
        kids.className = "spine-children";
        kids.setAttribute("role", "group");
        (section.children || []).forEach(function (child) {
          kids.appendChild(self._renderNode(child, targetId));
        });
        li.appendChild(kids);
      }
      return li;
    },

    _renderNode: function (node, targetId) {
      var li = doc.createElement("li");
      li.className = "spine-node spine-node-" + (node.type || "paragraph");
      li.setAttribute("role", "treeitem");

      var row = doc.createElement("div");
      row.className = "spine-row";
      row.dataset.nodeId = node.id;
      if (targetId && node.id === targetId) row.classList.add("spine-active");

      row.appendChild(this._statusDot(nodeStatus(node)));

      var label = doc.createElement("span");
      label.className = "spine-label";
      label.textContent = previewText(node) || (node.type || "node");
      row.appendChild(label);

      if (hasLock(node)) {
        var lock = doc.createElement("span");
        lock.className = "spine-lock";
        lock.setAttribute("aria-label", t("spine.locked", "Locked number"));
        lock.title = t("spine.locked", "Locked number");
        lock.textContent = "🔒";
        row.appendChild(lock);
      }
      if (targetId && node.id === targetId) {
        var edit = doc.createElement("span");
        edit.className = "spine-edit";
        edit.setAttribute("aria-hidden", "true");
        edit.textContent = "✏️";
        row.appendChild(edit);
      }

      this._bindRow(row, node.id);
      li.appendChild(row);
      return li;
    },

    _bindRow: function (row, nodeId) {
      var self = this;
      row.addEventListener("click", function () {
        self.scrollTo(nodeId);
      });
      row.addEventListener("mouseenter", function () {
        self.highlightCanvas(nodeId, true);
      });
      row.addEventListener("mouseleave", function () {
        self.highlightCanvas(nodeId, false);
      });
      row.addEventListener("contextmenu", function (e) {
        e.preventDefault();
        e.stopPropagation();
        self.openMenu(nodeId, e.clientX, e.clientY);
      });
    },
  };

  function init() {
    Spine.bind();
  }

  if (doc.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  global.AssureArgumentSpine = Spine;
})(typeof window !== "undefined" ? window : this);
