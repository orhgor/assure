(function (global) {
  "use strict";

  var STORAGE_KEY = "assure_tool";
  var MODE_STORAGE_KEY = "assure_mode";
  var DEFAULT_TOOL = "projects";
  var DEFAULT_MODE = "compose";
  var ROUTE_TOOLS = {};

  function $(id) {
    return document.getElementById(id);
  }

  function readToolFromHash() {
    var hash = (location.hash || "").replace(/^#/, "");
    if (!hash) return null;
    if (hash.indexOf("tool=") === 0) return hash.slice(5);
    var legacy = { compose: "projects", workbench: "projects" };
    if (legacy[hash]) return legacy[hash];
    if (["projects", "library", "settings"].indexOf(hash) >= 0) return hash;
    return null;
  }

  function persistTool(tool) {
    try {
      localStorage.setItem(STORAGE_KEY, tool);
    } catch (_) {}
  }

  function readStoredTool() {
    try {
      var stored = localStorage.getItem(STORAGE_KEY);
      if (stored === "compose" || stored === "workbench") return "projects";
      return stored;
    } catch (_) {
      return null;
    }
  }

  function persistMode(mode) {
    try {
      localStorage.setItem(MODE_STORAGE_KEY, mode);
    } catch (_) {}
  }

  function readStoredMode() {
    try {
      return localStorage.getItem(MODE_STORAGE_KEY);
    } catch (_) {
      return null;
    }
  }

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback || key;
  }

  var AssureToast = {
    show: function (message, kind) {
      var root = $("toast-root");
      if (!root || !message) return;
      var toast = document.createElement("div");
      toast.className = "toast toast-" + (kind || "info");
      toast.setAttribute("role", "status");
      toast.textContent = message;
      root.appendChild(toast);
      requestAnimationFrame(function () {
        toast.classList.add("is-visible");
      });
      window.setTimeout(function () {
        toast.classList.remove("is-visible");
        window.setTimeout(function () {
          toast.remove();
        }, 220);
      }, 3200);
    },
  };

  var AssureMode = {
    activeMode: DEFAULT_MODE,

    init: function () {
      var layout = $("assure-app");
      if (!layout) return;

      var initial = readStoredMode() || DEFAULT_MODE;
      this.activate(initial, { persist: false });

      layout.querySelectorAll(".mode-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          AssureMode.activate(btn.getAttribute("data-mode"));
        });
      });
    },

    getMode: function () {
      return this.activeMode;
    },

    activate: function (mode, opts) {
      opts = opts || {};
      if (!mode) mode = DEFAULT_MODE;
      this.activeMode = mode;
      if (opts.persist !== false) persistMode(mode);

      var layout = $("assure-app");
      if (layout) {
        layout.querySelectorAll(".mode-btn").forEach(function (btn) {
          var on = btn.getAttribute("data-mode") === mode;
          btn.classList.toggle("active", on);
          btn.setAttribute("aria-pressed", on ? "true" : "false");
        });
        layout.querySelectorAll("[data-mode-panel]").forEach(function (panel) {
          var on = panel.getAttribute("data-mode-panel") === mode;
          panel.classList.toggle("active", on);
          panel.hidden = !on;
        });
      }

      var dockBtn = $("btn-dock-draft");
      if (dockBtn && mode !== "compose") dockBtn.hidden = true;

      var shortcutHint = $("inquiry-shortcut-hint");
      if (shortcutHint) shortcutHint.hidden = mode !== "surgical";

      document.body.setAttribute("data-assure-mode", mode);
      document.dispatchEvent(new CustomEvent("assure:mode", { detail: { mode: mode } }));
    },
  };

  var AssureNav = {
    activeTool: DEFAULT_TOOL,

    init: function () {
      var layout = $("assure-app");
      if (!layout) return;

      AssureMode.init();

      var initial = readToolFromHash() || readStoredTool() || DEFAULT_TOOL;
      this.activate(initial, { replaceHash: false, persist: false });

      layout.querySelectorAll("[data-tool]").forEach(function (node) {
        if (node.classList.contains("app-sidebar-link")) {
          node.addEventListener("click", function (event) {
            event.preventDefault();
            AssureNav.activate(node.getAttribute("data-tool"));
          });
        }
      });

      var toggle = $("sidebar-toggle");
      if (toggle) {
        toggle.addEventListener("click", function () {
          layout.classList.toggle("sidebar-collapsed");
          var collapsed = layout.classList.contains("sidebar-collapsed");
          toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
        });
      }

      window.addEventListener("hashchange", function () {
        var tool = readToolFromHash();
        if (tool && tool !== AssureNav.activeTool) {
          AssureNav.activate(tool, { replaceHash: false, persist: true });
        }
      });

      AssureStatus.init();

      if (window.matchMedia && window.matchMedia("(max-width: 768px)").matches) {
        layout.classList.add("sidebar-collapsed");
        if (toggle) toggle.setAttribute("aria-expanded", "false");
      }

      var openSettings = $("open-settings-from-nav");
      if (openSettings) {
        openSettings.addEventListener("click", function () {
          if (global.AssureKeys && global.AssureKeys.openSettingsModal) {
            global.AssureKeys.openSettingsModal(false);
          }
        });
      }
    },

    activate: function (tool, opts) {
      opts = opts || {};
      if (!tool) tool = DEFAULT_TOOL;
      if (tool === "compose" || tool === "workbench") tool = "projects";

      if (ROUTE_TOOLS[tool]) {
        var u = new URL(ROUTE_TOOLS[tool], location.origin);
        var lang = new URL(location.href).searchParams.get("lang");
        if (lang) u.searchParams.set("lang", lang);
        location.href = u.pathname + u.search;
        return;
      }

      this.activeTool = tool;
      if (opts.persist !== false) persistTool(tool);

      var layout = $("assure-app");
      if (layout) {
        layout.querySelectorAll(".app-sidebar-link").forEach(function (link) {
          var on = link.getAttribute("data-tool") === tool;
          link.classList.toggle("is-active", on);
          link.setAttribute("aria-current", on ? "page" : "false");
        });
        layout.querySelectorAll(".tool-panel").forEach(function (panel) {
          var on = panel.getAttribute("data-tool") === tool || panel.id === "tool-" + tool;
          panel.classList.toggle("active", on);
          panel.hidden = !on;
        });
      }

      if (opts.replaceHash !== false) {
        var next = location.pathname + location.search + "#tool=" + encodeURIComponent(tool);
        if (location.pathname + location.search + location.hash !== next) {
          history.replaceState(null, "", next);
        }
      }

      document.body.setAttribute("data-assure-tool", tool);
      document.dispatchEvent(new CustomEvent("assure:tool", { detail: { tool: tool } }));
    },
  };

  var AssureStatus = {
    timer: null,

    init: function () {
      var dot = $("engine-status-dot");
      if (!dot) return;
      this.poll();
      this.timer = window.setInterval(this.poll.bind(this), 45000);
    },

    poll: function () {
      var dot = $("engine-status-dot");
      if (!dot) return;
      fetch("/health", { credentials: "same-origin" })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok && data && data.ok !== false, data: data };
          });
        })
        .then(function (result) {
          var online = result.ok;
          dot.classList.toggle("is-online", online);
          dot.classList.toggle("is-offline", !online);
          dot.title = online
            ? translate("app.status.online", "Z3 engine online")
            : translate("app.status.offline", "Backend unavailable");
          dot.setAttribute(
            "aria-label",
            online
              ? translate("app.status.online", "Z3 engine online")
              : translate("app.status.offline", "Backend unavailable")
          );
        })
        .catch(function () {
          dot.classList.remove("is-online");
          dot.classList.add("is-offline");
          dot.title = translate("app.status.offline", "Backend unavailable");
        });
    },
  };

  global.AssureNav = AssureNav;
  global.AssureMode = AssureMode;
  global.AssureToast = AssureToast;
  global.AssureStatus = AssureStatus;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      AssureNav.init();
    });
  } else {
    AssureNav.init();
  }
})(window);
