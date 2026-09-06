/**
 * Role-based workbench views and role switcher.
 */
(function (global) {
  "use strict";

  var ROLES = ["admin", "compliance", "developer", "executive"];
  var STORAGE_KEY = "assure_workbench_role";

  function t(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function $(id) {
    return document.getElementById(id);
  }

  var ROLE_CONFIG = {
    admin: {
      defaultView: "generate",
      tools: ["projects", "generate", "surgical", "library", "analytics", "settings"],
      widgets: ["risk", "audit", "debug", "kpi"],
    },
    compliance: {
      defaultView: "generate",
      tools: ["projects", "generate", "library", "analytics"],
      widgets: ["risk", "audit", "signoff"],
    },
    developer: {
      defaultView: "generate",
      tools: ["projects", "generate", "surgical", "library", "analytics", "settings"],
      widgets: ["debug", "api", "queue"],
    },
    executive: {
      defaultView: "projects",
      tools: ["projects", "analytics"],
      widgets: ["kpi", "team", "compliance_health"],
    },
  };

  var RoleWorkbench = {
    role: "compliance",
    _bound: false,

    init: function () {
      if (this._bound) return;
      this._bound = true;
      this.loadRole().then(function () {
        RoleWorkbench.applyRole();
        RoleWorkbench.bindSwitcher();
      });
    },

    loadRole: function () {
      var self = this;
      var stored = null;
      try {
        stored = localStorage.getItem(STORAGE_KEY);
      } catch (_) {}
      if (stored && ROLES.indexOf(stored) >= 0) {
        self.role = stored;
      }
      return fetch("/api/user/role", { credentials: "same-origin" })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (data.role && ROLES.indexOf(data.role) >= 0) {
            self.role = data.role;
          }
          try {
            localStorage.setItem(STORAGE_KEY, self.role);
          } catch (_) {}
          var sel = $("role-switcher");
          if (sel) sel.value = self.role;
        })
        .catch(function () {});
    },

    bindSwitcher: function () {
      var sel = $("role-switcher");
      if (!sel) return;
      sel.addEventListener("change", function () {
        var next = sel.value;
        if (ROLES.indexOf(next) < 0) return;
        fetch("/api/user/role", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: next }),
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            if (!data.ok) return;
            RoleWorkbench.role = data.role;
            try {
              localStorage.setItem(STORAGE_KEY, data.role);
            } catch (_) {}
            RoleWorkbench.applyRole();
            document.dispatchEvent(
              new CustomEvent("assure:role-changed", { detail: { role: data.role } })
            );
          })
          .catch(function () {});
      });
    },

    applyRole: function () {
      var cfg = ROLE_CONFIG[this.role] || ROLE_CONFIG.compliance;
      document.body.setAttribute("data-workbench-role", this.role);
      document.querySelectorAll(".app-sidebar-link[data-tool]").forEach(function (btn) {
        var tool = btn.getAttribute("data-tool");
        var map = { vault: "library", library: "library" };
        var key = map[tool] || tool;
        var allowed = cfg.tools.indexOf(key) >= 0 || cfg.tools.indexOf(tool) >= 0;
        btn.hidden = !allowed;
        btn.setAttribute("aria-hidden", allowed ? "false" : "true");
      });
      var rail = $("role-widget-rail");
      if (rail) {
        rail.querySelectorAll("[data-role-widget]").forEach(function (el) {
          var w = el.getAttribute("data-role-widget");
          el.hidden = cfg.widgets.indexOf(w) < 0;
        });
      }
      if (
        global.AssureNav &&
        typeof global.AssureNav.switchView === "function" &&
        !location.hash
      ) {
        var current = document.querySelector(".app-sidebar-link.is-active");
        var activeTool = current && current.getAttribute("data-tool");
        if (activeTool && cfg.tools.indexOf(activeTool) < 0 && cfg.tools.indexOf("library") < 0) {
          global.AssureNav.switchView(cfg.defaultView, { replaceHash: false, persist: false });
        }
      }
    },
  };

  global.AssureRoleWorkbench = RoleWorkbench;
  document.addEventListener("DOMContentLoaded", function () {
    RoleWorkbench.init();
  });
})(window);
