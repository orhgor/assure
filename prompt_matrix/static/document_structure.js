/**
 * Document Structure panel — section tree, drag reorder, connect, context menu.
 */
(function (global) {
  "use strict";

  var doc = global.document;

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

  function sectionPreview(section) {
    var children = section.children || [];
    if (!children.length) return t("structure.empty", "Empty section");
    var mgr = canvas();
    var first = children[0];
    var raw =
      mgr && typeof mgr._nodeText === "function"
        ? mgr._nodeText(first)
        : first.content || first.caption || "";
    raw = String(raw || "").replace(/\s+/g, " ").trim();
    if (raw.length <= 60) return raw;
    return raw.slice(0, 59) + "…";
  }

  var Structure = {
    _bound: false,
    treeContainer: null,
    connectBtn: null,
    emptyEl: null,
    menuEl: null,
    sections: [],
    selectedForConnect: [],
    _menuSectionId: null,

    bind: function () {
      if (this._bound) return;
      this._bound = true;
      var self = this;
      this.treeContainer = $("structureTreeContainer");
      this.connectBtn = $("actionConnectBtn");
      this.emptyEl = $("document-structure-empty");
      this.menuEl = $("structure-node-menu");
      if (!this.treeContainer) return;

      this.treeContainer.addEventListener("contextmenu", function (e) {
        var item = e.target.closest(".tree-node-item");
        if (!item) return;
        e.preventDefault();
        self.openMenu(item.dataset.sectionId, e.clientX, e.clientY);
      });

      doc.addEventListener("assure:jdf:rendered", function (e) {
        var tree = (e.detail && e.detail.tree) || (canvas() && canvas().tree);
        self.render(tree);
      });
      doc.addEventListener("assure:i18n", function () {
        self.render(canvas() && canvas().tree);
      });

      if (canvas() && canvas().tree) this.render(canvas().tree);
    },

    render: function (tree) {
      if (!this.treeContainer) return;
      tree = tree || (canvas() && canvas().tree) || { body: [] };
      var sections = tree.body || [];
      this.sections = sections.slice();
      this.selectedForConnect = [];

      if (!sections.length) {
        this.treeContainer.innerHTML = "";
        this.treeContainer.hidden = true;
        if (this.emptyEl) this.emptyEl.hidden = false;
        this.updateConnectButton();
        return;
      }

      if (this.emptyEl) this.emptyEl.hidden = true;
      this.treeContainer.hidden = false;
      this.treeContainer.innerHTML = "";

      var self = this;
      sections.forEach(function (section, index) {
        var li = doc.createElement("li");
        li.className = "tree-node-item";
        li.draggable = true;
        li.dataset.sectionId = section.id || "";
        li.dataset.index = String(index);
        li.innerHTML =
          '<div style="display:flex;align-items:center;min-width:0;flex:1;">' +
          '<span class="tree-node-drag-handle" aria-hidden="true">≡</span>' +
          '<span class="tree-node-label"></span>' +
          "</div>" +
          '<span class="tree-node-preview"></span>';
        li.querySelector(".tree-node-label").textContent = section.title || "Section " + (index + 1);
        li.querySelector(".tree-node-preview").textContent = sectionPreview(section);

        li.addEventListener("mouseenter", function () {
          self.highlightSection(section.id, true);
        });
        li.addEventListener("mouseleave", function () {
          self.highlightSection(section.id, false);
        });
        li.addEventListener("click", function (e) {
          if (e.target.closest(".tree-node-drag-handle")) return;
          self.onSectionClick(section.id);
        });
        li.addEventListener("dragstart", function (e) {
          e.dataTransfer.setData("text/plain", String(index));
          e.dataTransfer.effectAllowed = "move";
        });
        li.addEventListener("dragover", function (e) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
        });
        li.addEventListener("drop", function (e) {
          self.onDrop(e, index);
        });

        self.treeContainer.appendChild(li);
      });

      this.updateConnectButton();
    },

    highlightSection: function (sectionId, on) {
      var mgr = canvas();
      if (!mgr || !mgr.rootEl) return;
      var heading = mgr.rootEl.querySelector('h3[data-section-id="' + sectionId + '"]');
      if (!heading) {
        var path = mgr.getNodeSectionPath && mgr.getNodeSectionPath(sectionId);
        if (path && path.section) {
          var firstChild = (path.section.children || [])[0];
          if (firstChild) {
            var nodeEl = mgr.rootEl.querySelector('[data-node-id="' + firstChild.id + '"]');
            if (nodeEl) nodeEl.classList.toggle("canvas-section-highlight", on);
          }
        }
        return;
      }
      var block = heading.closest("article") || heading.parentElement;
      if (block) block.classList.toggle("canvas-section-highlight", on);
      mgr.rootEl.querySelectorAll('[data-section-id="' + sectionId + '"]').forEach(function (el) {
        el.classList.toggle("canvas-section-highlight", on);
      });
    },

    onSectionClick: function (sectionId) {
      var mgr = canvas();
      if (!mgr) return;
      var path = mgr.getNodeSectionPath(sectionId);
      var firstChild = path && path.section && (path.section.children || [])[0];
      if (firstChild && firstChild.id) {
        mgr.selectNodeForRefine(firstChild.id, { toast: false, skipViewSwitch: true, skipRender: true });
        var el = mgr.rootEl && mgr.rootEl.querySelector('[data-node-id="' + firstChild.id + '"]');
        if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      }

      this.treeContainer.querySelectorAll(".tree-node-item").forEach(function (el) {
        el.classList.toggle("node-active", el.dataset.sectionId === sectionId);
      });

      if (global.AssureRefineEngine && typeof global.AssureRefineEngine.updateImplicitSelection === "function") {
        global.AssureRefineEngine.updateImplicitSelection(firstChild ? firstChild.id : null);
      }

      this.trackConnectSelection(sectionId);
    },

    trackConnectSelection: function (sectionId) {
      var idx = this.sections.findIndex(function (s) {
        return s.id === sectionId;
      });
      if (this.selectedForConnect.indexOf(sectionId) >= 0) {
        this.selectedForConnect = [];
      } else if (this.selectedForConnect.length === 1) {
        var prevIdx = this.sections.findIndex(function (s) {
          return s.id === this.selectedForConnect[0];
        }, this);
        if (Math.abs(idx - prevIdx) === 1) {
          this.selectedForConnect.push(sectionId);
        } else {
          this.selectedForConnect = [sectionId];
        }
      } else {
        this.selectedForConnect = [sectionId];
      }

      var selected = this.selectedForConnect;
      this.treeContainer.querySelectorAll(".tree-node-item").forEach(function (el) {
        el.classList.toggle("node-connect-selected", selected.indexOf(el.dataset.sectionId) >= 0);
      });
      this.updateConnectButton();
    },

    updateConnectButton: function () {
      if (this.connectBtn) {
        this.connectBtn.disabled = this.selectedForConnect.length !== 2;
      }
    },

    onDrop: function (e, targetIndex) {
      e.preventDefault();
      var sourceIndex = parseInt(e.dataTransfer.getData("text/plain"), 10);
      if (isNaN(sourceIndex) || sourceIndex === targetIndex) return;

      var ids = this.sections.map(function (s) {
        return s.id;
      });
      var moved = ids.splice(sourceIndex, 1)[0];
      ids.splice(targetIndex, 0, moved);

      var mgr = canvas();
      if (mgr && typeof mgr.reorderSections === "function") {
        mgr.reorderSections(ids);
      }
    },

    openMenu: function (sectionId, x, y) {
      if (!this.menuEl || !sectionId) return;
      this._menuSectionId = sectionId;
      this.menuEl.hidden = false;
      this.menuEl.setAttribute("aria-hidden", "false");
      var pad = 8;
      var w = this.menuEl.offsetWidth || 200;
      var h = this.menuEl.offsetHeight || 120;
      this.menuEl.style.left = Math.min(Math.max(pad, x), global.innerWidth - w - pad) + "px";
      this.menuEl.style.top = Math.min(Math.max(pad, y), global.innerHeight - h - pad) + "px";
    },

    closeMenu: function () {
      if (!this.menuEl) return;
      this.menuEl.hidden = true;
      this.menuEl.setAttribute("aria-hidden", "true");
      this._menuSectionId = null;
    },

    handleMenuAction: function (action) {
      var sectionId = this._menuSectionId;
      var mgr = canvas();
      if (!sectionId || !mgr) return;
      if (action === "merge" && typeof mgr.mergeSectionWithNext === "function") {
        mgr.mergeSectionWithNext(sectionId);
      } else if (action === "split") {
        var path = mgr.getNodeSectionPath(sectionId);
        var firstChild = path && path.section && (path.section.children || [])[0];
        if (firstChild && typeof mgr.splitSectionAtNode === "function") {
          mgr.splitSectionAtNode(firstChild.id);
        }
      } else if (action === "delete") {
        if (
          global.confirm(
            t("structure.delete_confirm", "Delete this section and all its contents?")
          ) &&
          typeof mgr.deleteSection === "function"
        ) {
          mgr.deleteSection(sectionId);
        }
      }
      this.closeMenu();
    },

    bindMenu: function () {
      var self = this;
      if (!this.menuEl) return;
      this.menuEl.querySelectorAll("[data-act]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          self.handleMenuAction(btn.getAttribute("data-act"));
        });
      });
      doc.addEventListener("click", function () {
        self.closeMenu();
      });
    },
  };

  function init() {
    Structure.bind();
    Structure.bindMenu();
    global.AssureDocumentStructure = Structure;
  }

  if (doc.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(typeof window !== "undefined" ? window : this);
