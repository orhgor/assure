(function (global) {
  "use strict";

  var overlay = null;
  var input = null;
  var results = null;
  var items = [];
  var active = 0;
  var lastFocus = null;

  function t(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback;
  }

  function $(id) {
    return document.getElementById(id);
  }

  function commands() {
    return [
      {
        id: "new",
        name: t("palette.cmd.new", "New Workspace"),
        desc: t("palette.cmd.new_desc", "Create a blank document"),
        run: function () {
          if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
            global.AssureNav.switchView("projects");
          }
          if (global.AssureProjects && typeof global.AssureProjects.beginCreate === "function") {
            global.AssureProjects.beginCreate("blank");
          }
        },
      },
      {
        id: "z3",
        name: t("palette.cmd.z3", "Run Z3 Audit"),
        desc: t("palette.cmd.z3_desc", "Verify current document against compliance"),
        run: function () {
          if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
            global.AssureNav.switchView("generate", { replaceHash: false, persist: true });
          }
          var btn = $("generate-full-audit-btn");
          if (btn) btn.click();
        },
      },
      {
        id: "export",
        name: t("palette.cmd.export", "Export Compliance Pack"),
        desc: t("palette.cmd.export_desc", "Download PDF, CSV, JSON"),
        run: function () {
          var btn = $("btn-audit-manifest");
          if (btn) btn.click();
        },
      },
      {
        id: "analytics",
        name: t("palette.cmd.analytics", "Switch to Analytics"),
        desc: t("palette.cmd.analytics_desc", "View Z3 pass rate"),
        run: function () {
          if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
            global.AssureNav.switchView("analytics", { replaceHash: false, persist: true });
          }
        },
      },
    ];
  }

  function isOpen() {
    return overlay && overlay.classList.contains("is-open");
  }

  function filtered() {
    var q = ((input && input.value) || "").trim().toLowerCase();
    return commands().filter(function (cmd) {
      if (!q) return true;
      return cmd.name.toLowerCase().indexOf(q) !== -1 || cmd.desc.toLowerCase().indexOf(q) !== -1;
    });
  }

  function paint() {
    if (!results) return;
    items = filtered();
    results.replaceChildren();
    if (!items.length) {
      var empty = document.createElement("div");
      empty.className = "palette-empty";
      empty.setAttribute("role", "option");
      empty.textContent = t("palette.empty", "No matching commands");
      results.appendChild(empty);
      active = 0;
      return;
    }
    if (active >= items.length) active = items.length - 1;
    if (active < 0) active = 0;
    items.forEach(function (cmd, i) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "palette-item" + (i === active ? " is-active" : "");
      row.setAttribute("role", "option");
      row.setAttribute("id", "palette-opt-" + cmd.id);
      row.setAttribute("aria-selected", i === active ? "true" : "false");
      var name = document.createElement("span");
      name.className = "palette-item-name";
      name.textContent = cmd.name;
      var desc = document.createElement("span");
      desc.className = "palette-item-desc";
      desc.textContent = cmd.desc;
      row.appendChild(name);
      row.appendChild(desc);
      row.addEventListener("click", function () {
        execute(cmd);
      });
      row.addEventListener("mousemove", function () {
        if (active !== i) {
          active = i;
          paint();
        }
      });
      results.appendChild(row);
    });
    if (input) input.setAttribute("aria-activedescendant", "palette-opt-" + items[active].id);
  }

  function execute(cmd) {
    close();
    if (cmd && typeof cmd.run === "function") cmd.run();
  }

  function open() {
    if (!overlay || !input) return;
    lastFocus = document.activeElement;
    overlay.classList.add("is-open");
    overlay.setAttribute("aria-hidden", "false");
    input.value = "";
    active = 0;
    paint();
    input.focus();
  }

  function close() {
    if (!overlay) return;
    overlay.classList.remove("is-open");
    overlay.setAttribute("aria-hidden", "true");
    if (input) input.value = "";
    if (lastFocus && typeof lastFocus.focus === "function") {
      try {
        lastFocus.focus();
      } catch (_) {}
    }
  }

  function onOverlayKey(e) {
    if (!isOpen()) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      active += 1;
      paint();
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      active -= 1;
      paint();
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (items[active]) execute(items[active]);
      return;
    }
    if (e.key === "Tab") {
      e.preventDefault();
      input.focus();
    }
  }

  function bindDetailsA11y(el) {
    if (!el) return;
    var summary = el.querySelector("summary");
    if (!summary) return;
    summary.setAttribute("aria-haspopup", "menu");
    function sync() {
      summary.setAttribute("aria-expanded", el.open ? "true" : "false");
    }
    sync();
    el.addEventListener("toggle", sync);
    summary.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && el.open) {
        e.preventDefault();
        el.open = false;
        summary.focus();
      }
    });
  }

  function init() {
    overlay = $("command-palette-overlay");
    input = $("palette-input");
    results = $("palette-results");
    if (!overlay || !input || !results) return;
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) close();
    });
    input.addEventListener("input", function () {
      active = 0;
      paint();
    });
    overlay.addEventListener("keydown", onOverlayKey);
    bindDetailsA11y($("export-menu"));
    bindDetailsA11y($("command-deck-more"));
    var searchBtn = $("header-command-palette-btn");
    if (searchBtn) {
      searchBtn.addEventListener("click", function () {
        open();
      });
    }
  }

  global.AssureCommandPalette = {
    open: open,
    close: close,
    isOpen: isOpen,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(window);
