/**
 * Workbench interaction layer: resizable panes, keyboard shortcuts, and the
 * shortcut help sheet. Layout preference lives in localStorage — no network.
 */
(function (global) {
  "use strict";

  var doc = global.document;
  var PANE_KEY = "assure_wb_left_pct";
  var MIN_PCT = 22;
  var MAX_PCT = 68;
  var DEFAULT_PCT = 42;
  var IS_MAC = /Mac|iPhone|iPad/.test(global.navigator.platform || global.navigator.userAgent || "");

  function $(id) {
    return doc.getElementById(id);
  }

  function t(key, fallback) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback || key;
  }

  function toast(message, kind) {
    if (global.AssureToast && typeof global.AssureToast.show === "function") {
      global.AssureToast.show(message, kind || "info");
    }
  }

  /** True when focus is in a text field and plain keys should stay literal. */
  function isTyping(target) {
    if (!target) return false;
    var tag = (target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return true;
    return target.isContentEditable === true;
  }

  function isModified(e) {
    return e.metaKey || e.ctrlKey;
  }

  /* ─── Resizable panes ─────────────────────────────────────────────────── */

  var Panes = {
    workbench: null,
    container: null,
    splitter: null,

    clamp: function (pct) {
      if (!isFinite(pct)) return DEFAULT_PCT;
      return Math.min(MAX_PCT, Math.max(MIN_PCT, pct));
    },

    read: function () {
      try {
        var raw = global.localStorage.getItem(PANE_KEY);
        if (raw == null) return DEFAULT_PCT;
        return this.clamp(parseFloat(raw));
      } catch (_) {
        return DEFAULT_PCT;
      }
    },

    write: function (pct) {
      try {
        global.localStorage.setItem(PANE_KEY, String(Math.round(pct * 10) / 10));
      } catch (_) {}
    },

    apply: function (pct, persist) {
      var value = this.clamp(pct);
      if (this.workbench) {
        this.workbench.style.setProperty("--wb-left", value + "%");
      }
      if (this.splitter) {
        this.splitter.setAttribute("aria-valuenow", String(Math.round(value)));
      }
      if (persist) this.write(value);
      return value;
    },

    current: function () {
      if (!this.workbench) return DEFAULT_PCT;
      var raw = this.workbench.style.getPropertyValue("--wb-left");
      var parsed = parseFloat(raw);
      return isFinite(parsed) ? parsed : this.read();
    },

    pctFromClientX: function (clientX) {
      if (!this.container) return DEFAULT_PCT;
      var box = this.container.getBoundingClientRect();
      if (!box.width) return DEFAULT_PCT;
      return ((clientX - box.left) / box.width) * 100;
    },

    reset: function () {
      this.apply(DEFAULT_PCT, true);
      toast(t("panes.reset", "Pane split reset."), "info");
    },

    bind: function () {
      this.workbench = $("workbench-root");
      this.splitter = $("pane-splitter");
      this.container = this.workbench
        ? this.workbench.querySelector(".app-container")
        : null;
      if (!this.workbench || !this.splitter || !this.container) return;

      var self = this;
      this.apply(this.read(), false);

      this.splitter.addEventListener("pointerdown", function (e) {
        if (e.button !== 0 && e.pointerType === "mouse") return;
        e.preventDefault();
        self.splitter.classList.add("is-dragging");
        doc.body.classList.add("is-pane-resizing");
        try {
          self.splitter.setPointerCapture(e.pointerId);
        } catch (_) {}
      });

      this.splitter.addEventListener("pointermove", function (e) {
        if (!self.splitter.classList.contains("is-dragging")) return;
        e.preventDefault();
        self.apply(self.pctFromClientX(e.clientX), false);
      });

      function endDrag(e) {
        if (!self.splitter.classList.contains("is-dragging")) return;
        self.splitter.classList.remove("is-dragging");
        doc.body.classList.remove("is-pane-resizing");
        try {
          self.splitter.releasePointerCapture(e.pointerId);
        } catch (_) {}
        self.write(self.current());
      }

      this.splitter.addEventListener("pointerup", endDrag);
      this.splitter.addEventListener("pointercancel", endDrag);

      this.splitter.addEventListener("dblclick", function () {
        self.reset();
      });

      this.splitter.addEventListener("keydown", function (e) {
        var step = e.shiftKey ? 6 : 2;
        if (e.key === "ArrowLeft") {
          e.preventDefault();
          self.apply(self.current() - step, true);
        } else if (e.key === "ArrowRight") {
          e.preventDefault();
          self.apply(self.current() + step, true);
        } else if (e.key === "Home" || e.key === "Enter") {
          e.preventDefault();
          self.reset();
        }
      });
    },
  };

  /* ─── Shortcut help sheet ─────────────────────────────────────────────── */

  var Sheet = {
    lastFocus: null,

    el: function () {
      return $("shortcut-sheet");
    },

    isOpen: function () {
      var el = this.el();
      return !!el && !el.hidden;
    },

    open: function () {
      var el = this.el();
      if (!el) return;
      this.lastFocus = doc.activeElement;
      el.hidden = false;
      var close = $("shortcut-sheet-close");
      if (close) close.focus();
    },

    close: function () {
      var el = this.el();
      if (!el || el.hidden) return;
      el.hidden = true;
      if (this.lastFocus && typeof this.lastFocus.focus === "function") {
        this.lastFocus.focus();
      }
      this.lastFocus = null;
    },

    toggle: function () {
      if (this.isOpen()) this.close();
      else this.open();
    },

    /** Ctrl on Windows/Linux, ⌘ on Apple hardware. */
    localiseModKeys: function () {
      if (IS_MAC) return;
      doc.querySelectorAll(".kbd-mod").forEach(function (el) {
        el.textContent = "Ctrl";
      });
      doc.querySelectorAll("[data-shortcut]").forEach(function (el) {
        var value = el.getAttribute("data-shortcut");
        if (value) el.setAttribute("data-shortcut", value.replace(/⌘/g, "Ctrl+"));
      });
    },

    bind: function () {
      var self = this;
      this.localiseModKeys();

      var el = this.el();
      if (el) {
        var backdrop = el.querySelector(".shortcut-sheet-backdrop");
        if (backdrop) {
          backdrop.addEventListener("click", function () {
            self.close();
          });
        }
        var close = $("shortcut-sheet-close");
        if (close) {
          close.addEventListener("click", function () {
            self.close();
          });
        }
        // Keep tab focus inside the sheet while it is open.
        el.addEventListener("keydown", function (e) {
          if (e.key !== "Tab") return;
          var focusable = el.querySelectorAll("button, [href], [tabindex]:not([tabindex='-1'])");
          if (!focusable.length) return;
          var first = focusable[0];
          var last = focusable[focusable.length - 1];
          if (e.shiftKey && doc.activeElement === first) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && doc.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        });
      }

      var hint = $("shortcut-hint-btn");
      if (hint) {
        hint.addEventListener("click", function () {
          self.toggle();
        });
      }
    },
  };

  /* ─── Keyboard shortcuts ──────────────────────────────────────────────── */

  function viewIsActive(id) {
    var el = $(id);
    return !!el && !el.hidden;
  }

  function clickIfUsable(el) {
    if (!el || el.disabled || el.hidden) return false;
    el.click();
    return true;
  }

  var Shortcuts = {
    compile: function () {
      return clickIfUsable($("generate-compile-btn"));
    },

    refine: function () {
      return clickIfUsable($("btn-inquire"));
    },

    dock: function () {
      if (clickIfUsable($("generate-accept-dock-phase"))) return true;
      if (clickIfUsable($("generate-accept-dock"))) return true;
      if (clickIfUsable($("btn-dock-draft"))) return true;
      toast(t("shortcuts.dock_unavailable", "Nothing is ready to dock yet."), "info");
      return true;
    },

    exportMenu: function () {
      var menu = $("export-menu");
      if (!menu) return false;
      menu.open = !menu.open;
      if (menu.open) {
        var first = menu.querySelector(".export-menu-panel a");
        if (first) first.focus();
      }
      return true;
    },

    save: function () {
      return clickIfUsable($("save-status"));
    },

    sidebar: function () {
      return clickIfUsable($("sidebar-toggle"));
    },

    upload: function () {
      if (global.AssureSubstrateVault && typeof global.AssureSubstrateVault.triggerUpload === "function") {
        global.AssureSubstrateVault.triggerUpload();
        return true;
      }
      return clickIfUsable($("substrate-vault-upload-btn"));
    },

    /**
     * Bound in the capture phase on document so it resolves the intent of a
     * chord before the older window-level handlers see it.
     */
    bind: function () {
      doc.addEventListener(
        "keydown",
        function (e) {
          if (e.altKey) return;

          if (e.key === "Escape" && Sheet.isOpen()) {
            e.preventDefault();
            e.stopPropagation();
            Sheet.close();
            return;
          }

          // "?" opens help, but only when it is not part of typed prose.
          if (e.key === "?" && !isModified(e) && !isTyping(e.target)) {
            e.preventDefault();
            e.stopPropagation();
            Sheet.toggle();
            return;
          }

          if (!isModified(e)) return;

          var key = (e.key || "").toLowerCase();

          if (key === "enter") {
            // Compile in the compile view, Refine in the refine view.
            var handled = viewIsActive("panel-draft") || viewIsActive("view-generate")
              ? Shortcuts.compile()
              : Shortcuts.refine();
            if (handled) {
              e.preventDefault();
              e.stopPropagation();
            }
            return;
          }

          if (e.shiftKey && key === "d") {
            e.preventDefault();
            e.stopPropagation();
            Shortcuts.dock();
            return;
          }

          if (e.shiftKey && key === "e") {
            if (Shortcuts.exportMenu()) {
              e.preventDefault();
              e.stopPropagation();
            }
            return;
          }

          if (!e.shiftKey && key === "s") {
            // Keep the browser's own save dialog out of a document tool.
            e.preventDefault();
            e.stopPropagation();
            Shortcuts.save();
            return;
          }

          if (!e.shiftKey && key === "u") {
            // Keep the browser's own "View Source" out of a document tool.
            e.preventDefault();
            e.stopPropagation();
            Shortcuts.upload();
            return;
          }

          if (!e.shiftKey && key === "k") {
            e.preventDefault();
            e.stopPropagation();
            if (global.AssureCommandPalette && typeof global.AssureCommandPalette.open === "function") {
              global.AssureCommandPalette.open();
            }
            return;
          }

          // Cmd+B is bold and Cmd+\ is a text chord inside the editor, so
          // these only act as app shortcuts when focus is outside a field.
          if (isTyping(e.target)) return;

          if (!e.shiftKey && key === "b") {
            if (Shortcuts.sidebar()) {
              e.preventDefault();
              e.stopPropagation();
            }
            return;
          }

          if (key === "\\") {
            e.preventDefault();
            e.stopPropagation();
            Panes.reset();
          }
        },
        true
      );
    },
  };

  /* ─── Action feedback ─────────────────────────────────────────────────── */

  function bindExportFeedback() {
    var menu = $("export-menu");
    if (!menu) return;
    menu.querySelectorAll(".export-menu-panel a").forEach(function (link) {
      link.addEventListener("click", function () {
        toast(t("export.started", "Preparing your export…"), "info");
        menu.open = false;
      });
    });
    // Click-away closes the menu like a real dropdown.
    doc.addEventListener("click", function (e) {
      if (menu.open && !menu.contains(e.target)) menu.open = false;
    });
  }

  /**
   * A dock appends to the end of the document, so show the reader where it
   * landed instead of leaving them at the old scroll position.
   */
  function bindDockFeedback() {
    doc.addEventListener("assure:docked", function () {
      global.requestAnimationFrame(function () {
        var nodes = doc.querySelectorAll("#jdf-render-target .jdf-node");
        if (!nodes.length) return;
        var last = nodes[nodes.length - 1];
        last.classList.add("just-docked");
        try {
          last.scrollIntoView({ block: "nearest", behavior: "smooth" });
        } catch (_) {
          last.scrollIntoView(false);
        }
        global.setTimeout(function () {
          last.classList.remove("just-docked");
        }, 600);
      });
    });
  }

  function init() {
    Panes.bind();
    Sheet.bind();
    Shortcuts.bind();
    bindExportFeedback();
    bindDockFeedback();
  }

  if (doc.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  global.AssureWorkbenchUX = {
    panes: Panes,
    sheet: Sheet,
    shortcuts: Shortcuts,
    isMac: IS_MAC,
  };
})(typeof window !== "undefined" ? window : this);
