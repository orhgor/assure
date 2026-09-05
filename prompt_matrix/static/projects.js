(function (global) {
  "use strict";

  function $(id) {
    return document.getElementById(id);
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

  function escHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtDate(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso.replace(" ", "T") + (iso.indexOf("T") === -1 ? "Z" : ""));
      var now = new Date();
      var diffMs = now - d;
      var diffDays = Math.floor(diffMs / 86400000);
      if (diffDays === 0) return t("date.today", "Today");
      if (diffDays === 1) return t("date.yesterday", "Yesterday");
      if (diffDays < 7) return diffDays + " " + t("date.days_ago", "days ago");
      return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    } catch (_) {
      return "";
    }
  }

  function displayTitle(project) {
    var title = (project && project.title) || "";
    if (
      project &&
      project.id === "default" &&
      (!title || title === "Default project" || title.toLowerCase() === "default")
    ) {
      return t("projects.default_workspace", "Main Workspace");
    }
    return title || project.id;
  }

  var STATUS_META = {
    drafting: {
      labelKey: "projects.status.drafting",
      labelFallback: "Drafting",
      actionKey: "projects.action.compile",
      actionFallback: "Compile document",
      actionId: "compile",
    },
    verifying: {
      labelKey: "projects.status.verifying",
      labelFallback: "Verifying",
      actionKey: "projects.action.verify",
      actionFallback: "Run verification",
      actionId: "verify",
    },
    audited: {
      labelKey: "projects.status.audited",
      labelFallback: "Audited",
      actionKey: "projects.action.review",
      actionFallback: "Review findings",
      actionId: "review",
    },
    ready_to_export: {
      labelKey: "projects.status.ready",
      labelFallback: "Ready to Export",
      actionKey: "projects.action.export",
      actionFallback: "Export document",
      actionId: "export",
    },
  };

  function statusMeta(status) {
    return STATUS_META[status] || STATUS_META.drafting;
  }

  function vitalSignsHtml(project) {
    var parts = [];
    var when = fmtDate(project.updated_at);
    if (when) {
      parts.push(
        '<span class="project-vital">' +
          escHtml(t("projects.vitals.edited", "Edited {when}", { when: when })) +
          "</span>"
      );
    }
    if (project.node_count) {
      parts.push(
        '<span class="project-vital">' +
          escHtml(
            t("projects.vitals.nodes", "{count} nodes", { count: String(project.node_count) })
          ) +
          "</span>"
      );
    }
    if (project.lock_count) {
      parts.push(
        '<span class="project-vital project-vital-lock">' +
          escHtml(
            t("projects.vitals.locks", "{count} locks verified", {
              count: String(project.lock_count),
            })
          ) +
          "</span>"
      );
    } else {
      parts.push(
        '<span class="project-vital project-vital-muted">' +
          escHtml(t("projects.vitals.locks_none", "No locks yet")) +
          "</span>"
      );
    }
    if (project.redhat_count) {
      parts.push(
        '<span class="project-vital project-vital-warning">' +
          escHtml(
            t("projects.vitals.redhat", "{count} Red-Hat findings", {
              count: String(project.redhat_count),
            })
          ) +
          "</span>"
      );
    } else if ((project.node_count || 0) > 0) {
      parts.push(
        '<span class="project-vital project-vital-ok">' +
          escHtml(t("projects.vitals.redhat_clear", "Stress test clear")) +
          "</span>"
      );
    }
    return parts.join("");
  }

  // ─── Project switch ───────────────────────────────────────────────────────

  function switchToProject(projectId, projectTitle, opts) {
    opts = opts || {};
    if (!global.__assureJdf) return;
    if (projectId === (global.__ASSURE_PROJECT_ID__ || "default")) {
      if (global.AssureNav) {
        global.AssureNav.switchView("generate", { replaceHash: false, skipUnsaved: true });
      }
      return;
    }

    if (
      !opts.skipUnsaved &&
      global.AssureUnsaved &&
      !global.AssureUnsaved.confirmLeave(
        "unsaved.switch_project",
        "You have unsaved changes. Switching projects will lose them. Continue?"
      )
    ) {
      return;
    }

    global.__ASSURE_PROJECT_ID__ = projectId;
    global.__assureJdf.projectId = projectId;
    if (global.AssureProjectFileManager && typeof global.AssureProjectFileManager.remember === "function") {
      global.AssureProjectFileManager.remember(projectId);
    }

    var exportBtn = $("btn-export-docx");
    if (exportBtn) {
      exportBtn.href = "/api/projects/" + encodeURIComponent(projectId) + "/export?format=docx";
    }
    var exportMd = $("btn-export-md");
    if (exportMd) {
      exportMd.href = "/api/projects/" + encodeURIComponent(projectId) + "/export?format=md";
    }
    var exportHtml = $("btn-export-html");
    if (exportHtml) {
      exportHtml.href = "/api/projects/" + encodeURIComponent(projectId) + "/export?format=html";
    }

    try {
      var url = new URL(global.location.href);
      if (projectId === "default") {
        url.searchParams.delete("project");
      } else {
        url.searchParams.set("project", projectId);
      }
      global.history.pushState({ projectId: projectId }, "", url.pathname + url.search + url.hash);
    } catch (_) {}

    global.__assureJdf.loadProject().catch(function () {});

    if (global.AssureNav) {
      global.AssureNav.switchView("generate", { replaceHash: false, skipUnsaved: true });
    }
  }

  function runSuggestedAction(project, actionId) {
    if (project.id !== (global.__ASSURE_PROJECT_ID__ || "default")) {
      switchToProject(project.id, project.title, { skipUnsaved: false });
    }
    if (!global.AssureNav) return;
    if (actionId === "compile" || actionId === "verify") {
      global.AssureNav.switchView("generate", { replaceHash: false, skipUnsaved: true });
    } else if (actionId === "review") {
      global.AssureNav.switchView("surgical", { replaceHash: false, skipUnsaved: true });
    } else if (actionId === "export") {
      global.AssureNav.switchView("generate", { replaceHash: false, skipUnsaved: true });
      var exportBtn = $("btn-export-docx");
      if (exportBtn && exportBtn.href) {
        global.location.href = exportBtn.href;
      }
    } else {
      global.AssureNav.switchView("generate", { replaceHash: false, skipUnsaved: true });
    }
  }

  // ─── Dashboard rendering ──────────────────────────────────────────────────

  function renderDashboard(projects) {
    var host = $("projects-dashboard");
    if (!host) return;
    var current = global.__ASSURE_PROJECT_ID__ || "default";

    if (!projects.length) {
      host.innerHTML =
        '<p class="projects-dashboard-empty">' +
        escHtml(t("projects.empty", "No projects yet. Create one to begin.")) +
        "</p>";
      return;
    }

    host.innerHTML = "";
    projects.forEach(function (p) {
      var isActive = p.id === current;
      var meta = statusMeta(p.status || "drafting");
      var card = document.createElement("article");
      card.className =
        "project-work-card project-status-" + (p.status || "drafting") + (isActive ? " is-active" : "");
      card.dataset.projectId = p.id;
      card.setAttribute("role", "listitem");

      card.innerHTML =
        '<div class="project-work-card-top">' +
        '<span class="project-status-pill" data-status="' +
        escHtml(p.status || "drafting") +
        '">' +
        escHtml(t(meta.labelKey, meta.labelFallback)) +
        "</span>" +
        (isActive
          ? '<span class="project-active-badge">' +
            escHtml(t("projects.active_badge", "Active")) +
            "</span>"
          : "") +
        "</div>" +
        '<button type="button" class="project-work-open" aria-label="' +
        escHtml(t("projects.action.open", "Open workspace")) +
        '">' +
        '<h4 class="project-work-title">' +
        escHtml(displayTitle(p)) +
        "</h4>" +
        '<div class="project-work-vitals">' +
        vitalSignsHtml(p) +
        "</div>" +
        "</button>" +
        '<div class="project-work-footer">' +
        '<button type="button" class="btn-trust-action project-work-action" data-action="' +
        escHtml(meta.actionId) +
        '">' +
        escHtml(t(meta.actionKey, meta.actionFallback)) +
        "</button>" +
        '<div class="project-work-tools">' +
        '<button type="button" class="projects-action-btn" data-tool="rename" title="' +
        escHtml(t("projects.rename", "Rename")) +
        '" aria-label="' +
        escHtml(t("projects.rename", "Rename")) +
        '">✏️</button>' +
        (p.id !== "default"
          ? '<button type="button" class="projects-action-btn" data-tool="delete" title="' +
            escHtml(t("projects.delete", "Delete")) +
            '" aria-label="' +
            escHtml(t("projects.delete", "Delete")) +
            '">🗑️</button>'
          : "") +
        "</div>" +
        "</div>";

      card.querySelector(".project-work-open").addEventListener("click", function () {
        if (isActive) {
          if (global.AssureNav) global.AssureNav.switchView("generate");
          return;
        }
        switchToProject(p.id, p.title);
        loadProjects();
      });

      var actionBtn = card.querySelector(".project-work-action");
      if (actionBtn) {
        actionBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          runSuggestedAction(p, meta.actionId);
        });
      }

      var renameBtn = card.querySelector('[data-tool="rename"]');
      if (renameBtn) {
        renameBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          startInlineRename(card, p);
        });
      }

      var deleteBtn = card.querySelector('[data-tool="delete"]');
      if (deleteBtn) {
        deleteBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          confirmDelete(p);
        });
      }

      host.appendChild(card);
    });
  }

  // ─── Inline rename ───────────────────────────────────────────────────────

  function startInlineRename(card, project) {
    var existing = card.querySelector(".projects-rename-row");
    if (existing) {
      existing.querySelector("input").focus();
      return;
    }

    var openBtn = card.querySelector(".project-work-open");
    var footer = card.querySelector(".project-work-footer");
    if (openBtn) openBtn.hidden = true;
    if (footer) footer.hidden = true;

    var row = document.createElement("div");
    row.className = "projects-rename-row";
    row.innerHTML =
      "<input class='projects-rename-input form-control' type='text' maxlength='80' value='" +
      escHtml(displayTitle(project)) +
      "'>" +
      "<button class='btn btn-primary btn-sm' type='button' data-save>✓</button>" +
      "<button class='btn btn-outline btn-sm' type='button' data-cancel>✕</button>";
    card.appendChild(row);

    var input = row.querySelector("input");
    input.focus();
    input.select();

    function cancelRename() {
      row.remove();
      if (openBtn) openBtn.hidden = false;
      if (footer) footer.hidden = false;
    }

    function doRename() {
      var newTitle = input.value.trim();
      if (!newTitle || newTitle === displayTitle(project)) {
        cancelRename();
        return;
      }
      fetch("/api/projects/" + encodeURIComponent(project.id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ title: newTitle }),
      })
        .then(function (r) {
          return r.json();
        })
        .then(function () {
          loadProjects();
        })
        .catch(cancelRename);
    }

    row.querySelector("[data-save]").addEventListener("click", doRename);
    row.querySelector("[data-cancel]").addEventListener("click", cancelRename);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") doRename();
      if (e.key === "Escape") cancelRename();
    });
  }

  // ─── Delete confirm ───────────────────────────────────────────────────────

  function confirmDelete(project) {
    var msg = t("projects.confirm_delete", 'Delete "{name}"? This cannot be undone.', {
      name: displayTitle(project),
    });
    if (!global.confirm(msg)) return;

    fetch("/api/projects/" + encodeURIComponent(project.id), {
      method: "DELETE",
      credentials: "same-origin",
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          if (global.AssureToast) {
            global.AssureToast.show(
              data.error || t("projects.delete_failed", "Could not delete project."),
              "error"
            );
          }
          return;
        }
        if (project.id === (global.__ASSURE_PROJECT_ID__ || "default")) {
          switchToProject("default", "Default project", { skipUnsaved: true });
        }
        loadProjects();
      })
      .catch(function () {
        if (global.AssureToast) {
          global.AssureToast.show(t("projects.delete_failed", "Could not delete project."), "error");
        }
      });
  }

  // ─── Load ─────────────────────────────────────────────────────────────────

  function loadProjects() {
    var host = $("projects-dashboard");
    if (!host) return;
    host.innerHTML =
      '<p class="projects-dashboard-loading">' +
      escHtml(t("projects.loading", "Loading…")) +
      "</p>";

    fetch("/api/projects", { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        renderDashboard(data.projects || []);
      })
      .catch(function () {
        host.innerHTML =
          '<p class="projects-dashboard-empty">' +
          escHtml(t("projects.load_failed", "Could not load projects.")) +
          "</p>";
      });
  }

  // ─── New project form ─────────────────────────────────────────────────────

  function initNewProjectForm() {
    var newBtn = $("projects-new-btn");
    var form = $("projects-new-form");
    var input = $("projects-new-input");
    var confirmBtn = $("projects-new-confirm");
    var cancelBtn = $("projects-new-cancel");

    if (!newBtn || !form || !input || !confirmBtn || !cancelBtn) return;

    newBtn.addEventListener("click", function () {
      form.hidden = false;
      newBtn.hidden = true;
      input.value = "";
      input.focus();
    });

    cancelBtn.addEventListener("click", function () {
      form.hidden = true;
      newBtn.hidden = false;
    });

    function doCreate() {
      var title = input.value.trim();
      if (!title) {
        input.focus();
        return;
      }

      if (
        global.AssureUnsaved &&
        !global.AssureUnsaved.confirmLeave(
          "unsaved.switch_project",
          "You have unsaved changes. Switching projects will lose them. Continue?"
        )
      ) {
        return;
      }

      confirmBtn.disabled = true;
      fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ title: title }),
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (!data.ok) {
            if (global.AssureToast) {
              global.AssureToast.show(
                data.error || t("projects.create_failed", "Could not create project."),
                "error"
              );
            }
            confirmBtn.disabled = false;
            return;
          }
          form.hidden = true;
          newBtn.hidden = false;
          confirmBtn.disabled = false;
          switchToProject(data.id, data.title, { skipUnsaved: true });
        })
        .catch(function () {
          confirmBtn.disabled = false;
        });
    }

    confirmBtn.addEventListener("click", doCreate);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") doCreate();
      if (e.key === "Escape") {
        form.hidden = true;
        newBtn.hidden = false;
      }
    });
  }

  function restoreProjectFromUrl() {
    var files = global.AssureProjectFileManager;
    var pid = null;
    if (files && typeof files.restoreActiveId === "function") {
      pid = files.restoreActiveId();
    } else {
      try {
        pid = new URL(global.location.href).searchParams.get("project");
      } catch (_) {}
    }
    if (pid && pid !== (global.__ASSURE_PROJECT_ID__ || "default")) {
      switchToProject(pid, pid, { skipUnsaved: true });
    } else if (pid && files && typeof files.hydrate === "function") {
      files.hydrate(pid).catch(function () {});
    }
  }

  var AssureProjects = {
    load: loadProjects,
    switchTo: switchToProject,
    init: function () {
      initNewProjectForm();
      restoreProjectFromUrl();
    },
  };

  global.AssureProjects = AssureProjects;
})(window);
