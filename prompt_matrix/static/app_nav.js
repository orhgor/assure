(function (global) {
  "use strict";

  var STORAGE_KEY = "assure_view";
  var THEME_KEY = "assure-theme";
  var DEFAULT_VIEW = "generate";
  var WORKSPACE_VIEWS = ["projects", "generate", "surgical"];
  var FULL_VIEWS = ["library", "settings"];
  var ALL_VIEWS = WORKSPACE_VIEWS.concat(FULL_VIEWS);

  function $(id) {
    return document.getElementById(id);
  }

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback || key;
  }

  function readViewFromHash() {
    var hash = (location.hash || "").replace(/^#/, "");
    if (!hash) return null;
    if (hash.indexOf("tool=") === 0) return hash.slice(5);
    if (hash.indexOf("view=") === 0) return hash.slice(5);
    var legacy = {
      compose: "generate",
      workbench: "surgical",
      projects: "projects",
      library: "library",
      audit: "settings",
      generate: "generate",
      surgical: "surgical",
      settings: "settings",
    };
    if (legacy[hash]) return legacy[hash];
    if (ALL_VIEWS.indexOf(hash) >= 0) return hash;
    return null;
  }

  function persistView(view) {
    try {
      localStorage.setItem(STORAGE_KEY, view);
      localStorage.setItem("assure_tool", view);
    } catch (_) {}
  }

  function readStoredView() {
    try {
      var stored = localStorage.getItem(STORAGE_KEY) || localStorage.getItem("assure_tool");
      if (stored === "compose" || stored === "workbench") {
        return stored === "compose" ? "generate" : "surgical";
      }
      if (stored === "audit") return "settings";
      return stored;
    } catch (_) {
      return null;
    }
  }

  /** Abort all in-flight streams when navigating away. */
  var AssureStreamRegistry = {
    controller: null,
    register: function (ctrl) {
      this.abort();
      this.controller = ctrl;
    },
    abort: function () {
      if (this.controller) {
        try {
          this.controller.abort();
        } catch (_) {}
        this.controller = null;
      }
      if (global.__assureJdf && global.__assureJdf.streamClient) {
        try {
          global.__assureJdf.streamClient.abort();
        } catch (_) {}
      }
      if (global.AssureGenerate && typeof global.AssureGenerate.abort === "function") {
        global.AssureGenerate.abort();
      }
      document.dispatchEvent(new CustomEvent("assure:abort-streams"));
    },
  };

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

  var AssureProjects = {
    load: function () {
      var list = $("projects-list");
      var activeEl = $("projects-active-label");
      if (!list) return;
      list.innerHTML =
        "<li class=\"hint\">" + escapeHtml(translate("projects.loading", "Loading projects…")) + "</li>";

      fetch("/api/projects", { credentials: "same-origin" })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok, data: data };
          });
        })
        .then(function (result) {
          if (!result.ok) throw new Error("Failed to load projects");
          var projects = (result.data && result.data.projects) || [];
          var current = global.__ASSURE_PROJECT_ID__ || "default";
          if (activeEl) {
            activeEl.textContent = translate("projects.active", "Active project") + ": " + current;
          }
          if (!projects.length) {
            list.innerHTML =
              "<li class=\"hint\">" + escapeHtml(translate("projects.empty", "No projects yet.")) + "</li>";
            AssureNav.switchView("generate", { replaceHash: false, persist: true });
            return;
          }
          list.innerHTML = "";
          projects.forEach(function (p) {
            var li = document.createElement("li");
            li.className = "projects-list-item" + (p.id === current ? " is-active" : "");
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = "projects-list-btn";
            btn.textContent = (p.title || p.id) + " (v" + (p.current_version || 1) + ")";
            btn.dataset.projectId = p.id;
            if (p.id === current) {
              btn.setAttribute("aria-current", "true");
            }
            btn.addEventListener("click", function () {
              if (p.id === current) {
                AssureNav.switchView("generate");
                return;
              }
              if (global.AssureToast) {
                global.AssureToast.show(
                  translate("projects.switch_soon", "Multi-project switch coming soon. Using ") + p.id,
                  "info"
                );
              }
            });
            li.appendChild(btn);
            list.appendChild(li);
          });
        })
        .catch(function () {
          list.innerHTML =
            "<li class=\"hint bad\">" + escapeHtml(translate("projects.failed", "Could not load projects.")) + "</li>";
        });
    },
  };

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function getTheme() {
    return document.documentElement.getAttribute("data-theme") || "light";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (_) {}
    updateToggleIcon(theme);
  }

  function updateToggleIcon(theme) {
    var btn = $("theme-toggle");
    if (!btn) return;
    var isDark = theme === "dark";
    btn.setAttribute(
      "aria-label",
      translate(
        isDark ? "theme.toggle.light" : "theme.toggle.dark",
        isDark ? "Switch to light mode" : "Switch to dark mode"
      )
    );
  }

  function toggleTheme() {
    applyTheme(getTheme() === "dark" ? "light" : "dark");
  }

  function initTheme() {
    updateToggleIcon(getTheme());
    var btn = $("theme-toggle");
    if (btn) {
      btn.addEventListener("click", toggleTheme);
    }
    document.addEventListener("assure:i18n", function () {
      updateToggleIcon(getTheme());
    });
    try {
      var mq = window.matchMedia("(prefers-color-scheme: dark)");
      if (mq.addEventListener) {
        mq.addEventListener("change", function (e) {
          if (!localStorage.getItem(THEME_KEY)) {
            applyTheme(e.matches ? "dark" : "light");
          }
        });
      }
    } catch (_) {}
  }

  var AssureLandingBridge = {
    init: function () {
      var layout = $("assure-app");
      if (!layout) return;

      var params;
      try {
        params = new URL(location.href).searchParams;
      } catch (_) {
        return;
      }
      if (params.get("import") !== "latest") return;

      var draft = "";
      var model = "";
      try {
        draft = sessionStorage.getItem("assure_landing_draft") || "";
        model = sessionStorage.getItem("assure_landing_model") || "";
        sessionStorage.removeItem("assure_landing_draft");
        sessionStorage.removeItem("assure_landing_model");
      } catch (_) {}

      if (draft) {
        var genIntent = $("generate-intent");
        if (genIntent) genIntent.value = draft;
        var task = $("task");
        if (task) task.value = draft;
      }
      if (model) {
        try {
          localStorage.setItem("assure_preferred_target", model);
        } catch (_) {}
      }

      AssureNav.switchView("generate", { replaceHash: false, persist: true });

      var cleaned = new URL(location.href);
      cleaned.searchParams.delete("import");
      cleaned.searchParams.delete("mode");
      history.replaceState(null, "", cleaned.pathname + cleaned.search + cleaned.hash);

      if (draft) {
        var tryDock = function (attempts) {
          if (global.__assureJdf && typeof global.__assureJdf.dockDraftToCanvas === "function") {
            global.__assureJdf
              .dockDraftToCanvas(draft)
              .then(function () {
                AssureToast.show(translate("landing.import.docked", "Landing draft docked to canvas."), "success");
              })
              .catch(function () {
                AssureToast.show(translate("landing.import.ready", "Draft loaded into workspace."), "info");
              });
            return;
          }
          if (attempts > 0) {
            window.setTimeout(function () {
              tryDock(attempts - 1);
            }, 200);
          }
        };
        tryDock(25);
      }
    },
  };

  function isMobileNav() {
    return window.matchMedia && window.matchMedia("(max-width: 768px)").matches;
  }

  function setSidebarOpen(layout, open) {
    if (!layout) return;
    layout.classList.toggle("sidebar-collapsed", !open);
    var toggle = $("sidebar-toggle");
    if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
    var backdrop = $("sidebar-backdrop");
    if (backdrop && isMobileNav()) {
      backdrop.hidden = !open;
      backdrop.setAttribute("aria-hidden", open ? "false" : "true");
    }
  }

  function closeMobileSidebar() {
    var layout = $("assure-app");
    if (layout && isMobileNav()) {
      setSidebarOpen(layout, false);
    }
  }

  var AssureNav = {
    activeView: DEFAULT_VIEW,

    init: function () {
      var layout = $("assure-app");
      if (!layout) return;

      var initial = readViewFromHash() || readStoredView() || DEFAULT_VIEW;
      this.switchView(initial, { replaceHash: false, persist: false });

      layout.querySelectorAll(".app-sidebar-link[data-tool]").forEach(function (node) {
        node.addEventListener("click", function (event) {
          event.preventDefault();
          AssureNav.switchView(node.getAttribute("data-tool"));
          closeMobileSidebar();
        });
      });

      var toggle = $("sidebar-toggle");
      if (toggle) {
        toggle.addEventListener("click", function () {
          var collapsed = layout.classList.contains("sidebar-collapsed");
          setSidebarOpen(layout, collapsed);
        });
      }

      var backdrop = $("sidebar-backdrop");
      if (backdrop) {
        backdrop.addEventListener("click", function () {
          closeMobileSidebar();
        });
      }

      var refreshBtn = $("projects-refresh-btn");
      if (refreshBtn) {
        refreshBtn.addEventListener("click", function () {
          AssureProjects.load();
        });
      }

      window.addEventListener("hashchange", function () {
        var view = readViewFromHash();
        if (view && view !== AssureNav.activeView) {
          AssureNav.switchView(view, { replaceHash: false, persist: true });
        }
      });

      AssureStatus.init();
      initTheme();

      if (isMobileNav()) {
        setSidebarOpen(layout, false);
      }

      AssureLandingBridge.init();
      AssureNav.initSettings();
    },

    initSettings: function () {
      var toggle = $("settings-show-citations");
      if (!toggle) return;
      var projectId = global.__ASSURE_PROJECT_ID__ || "default";

      fetch("/api/projects/" + encodeURIComponent(projectId) + "/settings", {
        credentials: "same-origin",
      })
        .then(function (res) {
          return res.json();
        })
        .then(function (data) {
          if (data.settings && typeof data.settings.show_citations === "boolean") {
            toggle.checked = data.settings.show_citations;
            if (global.__assureJdf) {
              global.__assureJdf.showCitations = data.settings.show_citations;
              if (typeof global.__assureJdf.render === "function") {
                global.__assureJdf.render();
              }
            }
          }
        })
        .catch(function () {});

      toggle.addEventListener("change", function () {
        var show = !!toggle.checked;
        fetch("/api/projects/" + encodeURIComponent(projectId) + "/settings", {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ show_citations: show }),
        })
          .then(function (res) {
            return res.json();
          })
          .then(function () {
            if (global.__assureJdf) {
              global.__assureJdf.showCitations = show;
              if (typeof global.__assureJdf.render === "function") {
                global.__assureJdf.render();
              }
            }
          })
          .catch(function () {});
      });
    },

    /** Primary navigation — aborts streams and toggles view containers. */
    switchView: function (view, opts) {
      opts = opts || {};
      if (!view) view = DEFAULT_VIEW;
      if (view === "compose") view = "generate";
      if (view === "workbench") view = "surgical";
      if (view === "audit") view = "settings";
      if (ALL_VIEWS.indexOf(view) < 0) view = DEFAULT_VIEW;

      if (view !== this.activeView) {
        AssureStreamRegistry.abort();
      }

      this.activeView = view;
      if (opts.persist !== false) persistView(view);

      var layout = $("assure-app");
      if (layout) {
        layout.querySelectorAll(".app-sidebar-link").forEach(function (link) {
          var on = link.getAttribute("data-tool") === view;
          link.classList.toggle("is-active", on);
          link.setAttribute("aria-current", on ? "page" : "false");
        });
      }

      var workbench = $("jdf-workbench");
      var inWorkspace = WORKSPACE_VIEWS.indexOf(view) >= 0;
      if (workbench) workbench.hidden = !inWorkspace;

      ALL_VIEWS.forEach(function (name) {
        var el = $("view-" + name);
        if (!el) return;
        var on = name === view;
        el.classList.toggle("active", on);
        el.hidden = !on;
      });

      if (view === "projects") {
        AssureProjects.load();
      }

      if (opts.replaceHash !== false) {
        var next = location.pathname + location.search + "#view=" + encodeURIComponent(view);
        if (location.pathname + location.search + location.hash !== next) {
          history.replaceState(null, "", next);
        }
      }

      document.body.setAttribute("data-assure-view", view);
      document.body.setAttribute("data-assure-tool", view);
      document.dispatchEvent(new CustomEvent("assure:view", { detail: { view: view } }));
      document.dispatchEvent(new CustomEvent("assure:tool", { detail: { tool: view } }));
    },

    /** @deprecated use switchView */
    activate: function (tool, opts) {
      this.switchView(tool, opts);
    },
  };

  /** Backward compat for jdf_canvas.js */
  var AssureMode = {
    getMode: function () {
      var v = AssureNav.activeView;
      if (v === "generate") return "compose";
      if (v === "surgical") return "surgical";
      return "surgical";
    },
    activate: function (mode) {
      if (mode === "compose") AssureNav.switchView("generate");
      else if (mode === "surgical") AssureNav.switchView("surgical");
    },
    init: function () {},
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
        })
        .catch(function () {
          dot.classList.remove("is-online");
          dot.classList.add("is-offline");
        });
    },
  };

  global.AssureNav = AssureNav;
  global.AssureMode = AssureMode;
  global.AssureToast = AssureToast;
  global.AssureStatus = AssureStatus;
  global.AssureStreamRegistry = AssureStreamRegistry;
  global.AssureProjects = AssureProjects;
  global.initTheme = initTheme;
  global.toggleTheme = toggleTheme;
  global.updateToggleIcon = updateToggleIcon;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      AssureNav.init();
    });
  } else {
    AssureNav.init();
  }
})(window);
