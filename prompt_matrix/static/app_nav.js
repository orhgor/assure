(function (global) {
  "use strict";

  var STORAGE_KEY = "assure_view";
  var DEFAULT_VIEW = "generate";
  var WORKSPACE_VIEWS = ["projects", "generate", "surgical", "vault"];
  var ALL_VIEWS = WORKSPACE_VIEWS.concat(["library"]);
  var SETTINGS_VIEWS = ["settings", "audit"];

  function $(id) {
    return document.getElementById(id);
  }

  function translate(key, fallback, vars) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, vars || {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    var s = fallback || key;
    if (vars) {
      Object.keys(vars).forEach(function (k) {
        s = s.replace(new RegExp("\\{" + k + "\\}", "g"), String(vars[k]));
      });
    }
    return s;
  }

  function readViewFromHash() {
    var hash = (location.hash || "").replace(/^#/, "");
    if (!hash) return null;
    if (hash.indexOf("tool=") === 0) return hash.slice(5);
    if (hash.indexOf("view=") === 0) {
      var raw = hash.slice(5);
      if (raw === "library") return "vault";
      return raw;
    }
    var legacy = {
      compose: "generate",
      workbench: "surgical",
      projects: "projects",
      library: "vault",
      vault: "vault",
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
      if (stored === "library") return "vault";
      return stored;
    } catch (_) {
      return null;
    }
  }

  /** Abort all in-flight streams when navigating away. */
  var AssureStreamRegistry = {
    controller: null,
    extras: [],
    register: function (ctrl, opts) {
      // Only abort a previous stream. Full abort() also calls AssureGenerate.abort(),
      // which nulls the controller we are registering and crashes startDraftStream
      // on `self.controller.signal`.
      opts = opts || {};
      this.extras = this.extras || [];
      if (opts.parallel) {
        if (ctrl && this.extras.indexOf(ctrl) < 0) this.extras.push(ctrl);
        return;
      }
      if (this.controller && this.controller !== ctrl) {
        try {
          this.controller.abort();
        } catch (_) {}
      }
      this.extras.forEach(function (extra) {
        if (extra && extra !== ctrl) {
          try {
            extra.abort();
          } catch (_) {}
        }
      });
      this.extras = [];
      this.controller = ctrl;
    },
    abort: function () {
      if (this.controller) {
        try {
          this.controller.abort();
        } catch (_) {}
        this.controller = null;
      }
      (this.extras || []).forEach(function (extra) {
        try {
          extra.abort();
        } catch (_) {}
      });
      this.extras = [];
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

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  var AssureCompilerStatus = {
    issueCount: 0,
    documentVersion: 1,
    currentState: "idle",
    lastAction: "",

    update: function (state, detail) {
      var el = $("compiler-status");
      var label = $("compiler-status-label");
      var detailEl = $("compiler-status-detail");
      if (!el || !label) return;

      this.currentState = state || "idle";
      el.className = "compiler-status compiler-" + this.currentState;

      var defaults = {
        idle: ["compiler.status.idle", "All good"],
        processing: ["compiler.status.processing", "Working…"],
        verified: ["compiler.status.verified", "✅ Verified"],
        issues: ["compiler.status.issues", "❌ Issues Found"],
        exporting: ["compiler.status.exporting", "⬡ Exporting…"],
      };
      var pair = defaults[this.currentState] || defaults.idle;
      label.textContent = detail != null && detail !== "" ? detail : translate(pair[0], pair[1]);

      if (detailEl) {
        if (this.currentState === "idle" && this.documentVersion > 1) {
          detailEl.hidden = false;
          detailEl.textContent = "(v" + this.documentVersion + ")";
        } else if (this.currentState === "issues" && this.issueCount > 0) {
          detailEl.hidden = false;
          detailEl.textContent = translate(
            "compiler.status.issues_detail",
            "{n} issue(s)",
            { n: this.issueCount }
          );
        } else {
          detailEl.hidden = true;
          detailEl.textContent = "";
        }
      }

      this._updateHealthBar();
    },

    setLastAction: function (message) {
      this.lastAction = message || "";
      var el = $("workbench-last-action");
      if (el) {
        el.textContent = this.lastAction;
        el.hidden = !this.lastAction;
      }
      this._updateHealthBar();
    },

    _updateHealthBar: function () {
      var healthEl = $("workbench-health");
      var barEl = $("workbench-status-bar");
      if (!healthEl) return;
      var msg;
      if (this.currentState === "processing" || this.currentState === "exporting") {
        msg = translate("workbench.status.health_working", "Working on your draft…");
      } else if (this.currentState === "issues" && this.issueCount > 0) {
        msg = translate("workbench.status.health_issues", "{n} issues to review", {
          n: this.issueCount,
        });
      } else if (this.currentState === "verified") {
        msg = translate("compiler.status.verified", "✅ Verified");
      } else {
        msg = translate("workbench.status.health_ok", "Document looks good");
      }
      healthEl.textContent = msg;
      if (barEl) {
        barEl.classList.toggle("is-flash", this.currentState === "verified");
        if (this.currentState === "verified") {
          window.setTimeout(function () {
            if (barEl) barEl.classList.remove("is-flash");
          }, 1200);
        }
      }
    },

    flashCacheHit: function () {
      var barEl = $("workbench-status-bar");
      if (!barEl) return;
      barEl.classList.add("is-cache-hit");
      window.setTimeout(function () {
        if (barEl) barEl.classList.remove("is-cache-hit");
      }, 1200);
    },

    setVersion: function (version) {
      this.documentVersion = version || 1;
      if (this.currentState === "idle") {
        this.update("idle");
      }
    },

    setIssueCount: function (count) {
      this.issueCount = count || 0;
      if (this.currentState === "issues") {
        this.update("issues");
      }
    },
  };

  function updateCompilerStatus(state, detail) {
    AssureCompilerStatus.update(state, detail);
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

      window.addEventListener("hashchange", function () {
        var view = readViewFromHash();
        if (view && view !== AssureNav.activeView) {
          AssureNav.switchView(view, { replaceHash: false, persist: true });
        }
      });

      AssureStatus.init();
      updateCompilerStatus("idle");

      ["btn-export-docx", "btn-export-md", "btn-export-html", "btn-export-pdf"].forEach(function (id) {
        var el = $(id);
        if (!el) return;
        el.addEventListener("click", function () {
          AssureCompilerStatus.update("exporting");
          window.setTimeout(function () {
            if (AssureCompilerStatus.currentState === "exporting") {
              AssureCompilerStatus.update("idle");
            }
          }, 4000);
        });
      });

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
      if (view === "library") view = "vault";
      if (ALL_VIEWS.indexOf(view) < 0) view = DEFAULT_VIEW;

      if (view !== this.activeView) {
        if (!opts.skipUnsaved && global.AssureUnsaved) {
          var leavingWorkspace = FULL_VIEWS.indexOf(view) >= 0;
          if (
            global.AssureUnsaved.isGenerating ||
            (global.AssureUnsaved.hasUnsavedChanges && leavingWorkspace)
          ) {
            if (
              !global.AssureUnsaved.confirmLeave(
                "unsaved.switch_view",
                "You have unsaved changes. Switch anyway?"
              )
            ) {
              return;
            }
          }
        }
        AssureStreamRegistry.abort();
      }

      this.activeView = view;
      if (opts.persist !== false) persistView(view);

      var layout = $("assure-app");
      var appContent = layout && layout.querySelector(".app-content");
      var workbench = $("jdf-workbench");
      var leftPaneShared = $("left-pane-shared");
      var inWorkspace = WORKSPACE_VIEWS.indexOf(view) >= 0;
      var inFullView = FULL_VIEWS.indexOf(view) >= 0;

      if (layout) {
        layout.querySelectorAll(".app-sidebar-link").forEach(function (link) {
          var tool = link.getAttribute("data-tool") || "";
          var on =
            tool === view ||
            (view === "vault" && tool === "library") ||
            (view === "generate" && tool === "generate") ||
            (view === "surgical" && tool === "surgical") ||
            (view === "projects" && tool === "projects") ||
            (view === "settings" && tool === "settings");
          link.classList.toggle("is-active", on);
          link.setAttribute("aria-current", on ? "page" : "false");
        });
      }

      if (appContent) {
        appContent.classList.toggle("mode-full-view", inFullView);
        appContent.classList.toggle("mode-workspace", inWorkspace);
      }

      if (workbench) workbench.hidden = false;

      var rightPane = $("jdf-document-canvas");
      if (rightPane) rightPane.hidden = inFullView;

      if (leftPaneShared) {
        leftPaneShared.hidden = !inWorkspace || view === "projects";
      }

      ["projects", "generate", "surgical"].forEach(function (name) {
        var el = $("view-" + name);
        if (!el) return;
        var on = inWorkspace && view === name;
        el.classList.toggle("active", on);
        el.hidden = !on;
      });

      FULL_VIEWS.forEach(function (name) {
        var el = $("view-" + name);
        if (!el) return;
        var on = view === name;
        el.classList.toggle("active", on);
        el.hidden = !on;
      });

      var legacyLibrary = $("view-library");
      if (legacyLibrary) {
        legacyLibrary.classList.remove("active");
        legacyLibrary.hidden = true;
      }

      if (view === "vault" && leftPaneShared) {
        var vaultPanel = $("substrate-vault");
        if (vaultPanel && vaultPanel.tagName === "DETAILS") {
          vaultPanel.open = true;
        }
        window.setTimeout(function () {
          if (vaultPanel && typeof vaultPanel.scrollIntoView === "function") {
            vaultPanel.scrollIntoView({ block: "nearest", behavior: "smooth" });
          }
        }, 0);
        if (global.AssureSubstrateVault && typeof global.AssureSubstrateVault.fetchList === "function") {
          global.AssureSubstrateVault.fetchList();
        }
      }

      if (opts.replaceHash !== false) {
        var hashView = view === "vault" ? "library" : view;
        var next = location.pathname + location.search + "#view=" + encodeURIComponent(hashView);
        if (location.pathname + location.search + location.hash !== next) {
          history.replaceState(null, "", next);
        }
      }

      document.body.setAttribute("data-assure-view", view);
      document.body.setAttribute("data-assure-tool", view === "vault" ? "library" : view);
      document.dispatchEvent(new CustomEvent("assure:view", { detail: { view: view } }));
      document.dispatchEvent(
        new CustomEvent("assure:tool", { detail: { tool: view === "vault" ? "library" : view } })
      );

      if (view === "projects" && global.AssureProjects) {
        global.AssureProjects.load();
      }
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
  global.AssureCompilerStatus = AssureCompilerStatus;
  global.updateCompilerStatus = updateCompilerStatus;

  function autoResizeTextarea(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 480) + "px";
  }

  function initAutoResize() {
    document.querySelectorAll("textarea.auto-resize, input.auto-resize").forEach(function (el) {
      autoResizeTextarea(el);
      el.addEventListener("input", function () {
        autoResizeTextarea(el);
      });
    });
  }

  function initDemoRedhatChip() {
    var btn = $("demo-redhat-btn");
    if (!btn) return;

    var demoToastTimer = null;

    function clearDemoToastTimer() {
      if (demoToastTimer) {
        window.clearTimeout(demoToastTimer);
        demoToastTimer = null;
      }
    }

    function showDemoToast() {
      if (!global.__assureDemoRedhatPending) return;
      global.__assureDemoRedhatPending = false;
      clearDemoToastTimer();
      AssureToast.show(
        translate(
          "demo.redhat.toast",
          'Stress Test flagged a logical gap: "Growth rate benchmark is unverified — no industry average provided." Try it with your own documents.'
        ),
        "info"
      );
    }

    document.addEventListener("assure:demo-redhat-audit", showDemoToast);

    function runDemo() {
      var intent = $("generate-intent");
      var compileBtn = $("generate-compile-btn");
      var redhatToggle = $("toggle-redhat");
      if (!intent || !compileBtn) return;

      intent.value = translate(
        "demo.redhat.sample",
        "Our revenue grew 50% this quarter, which is the highest growth rate in the industry."
      );
      autoResizeTextarea(intent);

      if (redhatToggle && !redhatToggle.checked) {
        redhatToggle.checked = true;
      }

      AssureNav.switchView("generate", { replaceHash: false, persist: true });

      global.__assureDemoRedhatPending = true;
      clearDemoToastTimer();
      demoToastTimer = window.setTimeout(showDemoToast, 12000);

      compileBtn.click();
    }

    btn.addEventListener("click", runDemo);
    btn.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        runDemo();
      }
    });
  }

  function initCompileExampleChips() {
    document.querySelectorAll(".example-chip[data-prompt-key]").forEach(function (chip) {
      function applyPrompt() {
        var key = chip.getAttribute("data-prompt-key");
        if (!key) return;
        var text = translate(key, "");
        var target = $("generate-intent");
        if (!target || !text) return;
        target.value = text;
        target.focus();
        autoResizeTextarea(target);
      }
      chip.addEventListener("click", applyPrompt);
      chip.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          applyPrompt();
        }
      });
    });
  }

  global.AssureAutoResize = { resize: autoResizeTextarea, init: initAutoResize };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      AssureNav.init();
      initAutoResize();
      initCompileExampleChips();
      initDemoRedhatChip();
    });
  } else {
    AssureNav.init();
    initAutoResize();
    initCompileExampleChips();
    initDemoRedhatChip();
  }
})(window);
