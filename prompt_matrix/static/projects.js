(function (global) {
  "use strict";

  function $(id) { return document.getElementById(id); }

  function t(key, fallback) {
    if (global.AssureI18n && global.AssureI18n.t) return global.AssureI18n.t(key) || fallback;
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
    } catch (_) { return ""; }
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

    // Update global state
    global.__ASSURE_PROJECT_ID__ = projectId;
    global.__assureJdf.projectId = projectId;

    // Update export link
    var exportBtn = $("btn-export-docx");
    if (exportBtn) {
      exportBtn.href = "/api/projects/" + encodeURIComponent(projectId) + "/export?format=docx";
    }

    // Update URL without reload
    try {
      var url = new URL(global.location.href);
      if (projectId === "default") {
        url.searchParams.delete("project");
      } else {
        url.searchParams.set("project", projectId);
      }
      global.history.pushState({ projectId: projectId }, "", url.pathname + url.search + url.hash);
    } catch (_) {}

    // Update the empty canvas title when it re-renders
    global.__assureJdf.loadProject().catch(function () {});

    // Switch the left pane to generate view
    if (global.AssureNav) {
      global.AssureNav.switchView("generate", { replaceHash: false, skipUnsaved: true });
    }
  }

  // ─── List rendering ───────────────────────────────────────────────────────

  function renderList(projects) {
    var list = $("projects-list");
    if (!list) return;
    var current = global.__ASSURE_PROJECT_ID__ || "default";

    if (!projects.length) {
      list.innerHTML = "<li class='projects-list-empty'>" +
        escHtml(t("projects.empty", "No projects yet. Create one to begin.")) + "</li>";
      return;
    }

    list.innerHTML = "";
    projects.forEach(function (p) {
      var isActive = p.id === current;
      var li = document.createElement("li");
      li.className = "projects-list-item" + (isActive ? " is-active" : "");
      li.dataset.projectId = p.id;

      var lockText = p.lock_count
        ? ("🔒 " + p.lock_count + " " + (p.lock_count === 1 ? t("projects.lock", "lock") : t("projects.locks", "locks")))
        : t("projects.no_locks", "No locks yet");

      li.innerHTML =
        "<button class='projects-list-btn' type='button'" + (isActive ? " aria-current='true'" : "") + ">" +
          "<span class='projects-list-name'>" + escHtml(p.title || p.id) + "</span>" +
          "<span class='projects-list-meta'>" +
            escHtml(fmtDate(p.updated_at)) +
            (p.lock_count !== undefined ? " · " + escHtml(lockText) : "") +
          "</span>" +
        "</button>" +
        "<div class='projects-list-actions'>" +
          "<button class='projects-action-btn' type='button' data-action='rename' title='" + escHtml(t("projects.rename", "Rename")) + "' aria-label='" + escHtml(t("projects.rename", "Rename")) + "'>✏️</button>" +
          (p.id !== "default" ? "<button class='projects-action-btn' type='button' data-action='delete' title='" + escHtml(t("projects.delete", "Delete")) + "' aria-label='" + escHtml(t("projects.delete", "Delete")) + "'>🗑️</button>" : "") +
        "</div>";

      // Main click → switch
      li.querySelector(".projects-list-btn").addEventListener("click", function () {
        if (isActive) {
          if (global.AssureNav) global.AssureNav.switchView("generate");
          return;
        }
        switchToProject(p.id, p.title);
        // Re-render list to mark new active
        loadProjects();
      });

      // Rename
      var renameBtn = li.querySelector("[data-action='rename']");
      if (renameBtn) {
        renameBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          startInlineRename(li, p);
        });
      }

      // Delete
      var deleteBtn = li.querySelector("[data-action='delete']");
      if (deleteBtn) {
        deleteBtn.addEventListener("click", function (e) {
          e.stopPropagation();
          confirmDelete(p);
        });
      }

      list.appendChild(li);
    });
  }

  // ─── Inline rename ───────────────────────────────────────────────────────

  function startInlineRename(li, project) {
    var existing = li.querySelector(".projects-rename-input");
    if (existing) { existing.focus(); return; }

    var nameBtn = li.querySelector(".projects-list-btn");
    if (nameBtn) nameBtn.style.display = "none";
    var actions = li.querySelector(".projects-list-actions");
    if (actions) actions.style.display = "none";

    var row = document.createElement("div");
    row.className = "projects-rename-row";
    row.innerHTML =
      "<input class='projects-rename-input form-control' type='text' maxlength='80' value='" + escHtml(project.title || project.id) + "'>" +
      "<button class='btn btn-primary btn-sm' type='button' data-save>✓</button>" +
      "<button class='btn btn-outline btn-sm' type='button' data-cancel>✕</button>";
    li.appendChild(row);

    var input = row.querySelector("input");
    input.focus();
    input.select();

    function cancelRename() {
      row.remove();
      if (nameBtn) nameBtn.style.display = "";
      if (actions) actions.style.display = "";
    }

    function doRename() {
      var newTitle = input.value.trim();
      if (!newTitle || newTitle === (project.title || project.id)) { cancelRename(); return; }
      fetch("/api/projects/" + encodeURIComponent(project.id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ title: newTitle }),
      })
        .then(function (r) { return r.json(); })
        .then(function () { loadProjects(); })
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
    var msg = t("projects.confirm_delete", "Delete \"" + (project.title || project.id) + "\"? This cannot be undone.");
    msg = msg.replace("{name}", project.title || project.id);
    if (!global.confirm(msg)) return;

    fetch("/api/projects/" + encodeURIComponent(project.id), {
      method: "DELETE",
      credentials: "same-origin",
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) {
          if (global.AssureToast) global.AssureToast.show(data.error || t("projects.delete_failed", "Could not delete project."), "error");
          return;
        }
        // If we deleted the active project, fall back to default
        if (project.id === (global.__ASSURE_PROJECT_ID__ || "default")) {
          switchToProject("default", "Default project", { skipUnsaved: true });
        }
        loadProjects();
      })
      .catch(function () {
        if (global.AssureToast) global.AssureToast.show(t("projects.delete_failed", "Could not delete project."), "error");
      });
  }

  // ─── Load ─────────────────────────────────────────────────────────────────

  function loadProjects() {
    var list = $("projects-list");
    if (!list) return;
    list.innerHTML = "<li class='projects-list-loading'>" +
      escHtml(t("projects.loading", "Loading…")) + "</li>";

    fetch("/api/projects", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderList(data.projects || []);
      })
      .catch(function () {
        list.innerHTML = "<li class='projects-list-empty'>" +
          escHtml(t("projects.load_failed", "Could not load projects.")) + "</li>";
      });
  }

  // ─── New project form ─────────────────────────────────────────────────────

  function initNewProjectForm() {
    var newBtn = $("projects-new-btn");
    var form = $("projects-new-form");
    var input = $("projects-new-input");
    var confirm = $("projects-new-confirm");
    var cancel = $("projects-new-cancel");

    if (!newBtn || !form || !input || !confirm || !cancel) return;

    newBtn.addEventListener("click", function () {
      form.hidden = false;
      newBtn.hidden = true;
      input.value = "";
      input.focus();
    });

    cancel.addEventListener("click", function () {
      form.hidden = true;
      newBtn.hidden = false;
    });

    function doCreate() {
      var title = input.value.trim();
      if (!title) { input.focus(); return; }

      if (
        global.AssureUnsaved &&
        !global.AssureUnsaved.confirmLeave(
          "unsaved.switch_project",
          "You have unsaved changes. Switching projects will lose them. Continue?"
        )
      ) {
        return;
      }

      confirm.disabled = true;
      fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ title: title }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.ok) {
            if (global.AssureToast) global.AssureToast.show(data.error || t("projects.create_failed", "Could not create project."), "error");
            confirm.disabled = false;
            return;
          }
          form.hidden = true;
          newBtn.hidden = false;
          confirm.disabled = false;
          switchToProject(data.id, data.title, { skipUnsaved: true });
        })
        .catch(function () {
          confirm.disabled = false;
        });
    }

    confirm.addEventListener("click", doCreate);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") doCreate();
      if (e.key === "Escape") {
        form.hidden = true;
        newBtn.hidden = false;
      }
    });
  }

  // ─── URL param restore on page load ───────────────────────────────────────

  function restoreProjectFromUrl() {
    try {
      var url = new URL(global.location.href);
      var pid = url.searchParams.get("project");
      if (pid && pid !== (global.__ASSURE_PROJECT_ID__ || "default")) {
        switchToProject(pid, pid, { skipUnsaved: true });
      }
    } catch (_) {}
  }

  // ─── Public API ───────────────────────────────────────────────────────────

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
