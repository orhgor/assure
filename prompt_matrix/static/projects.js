(function (global) {
  "use strict";

  var PRJ_ID_RE = /^prj_[A-Za-z0-9_-]+$/;
  var ARCHIVE_KEY = "assure_archived_workspaces";

  var selectedId = null;
  var isCreating = false;
  var statusFilter = "all";
  var projectsCache = [];
  var createTemplateId = "blank";
  var pendingCreateFiles = [];
  var pendingDelete = null;

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

  function fallbackAuditTitle(project) {
    var created = project && (project.created_at || project.createdAt);
    var d = created ? new Date(String(created).replace(" ", "T")) : new Date();
    var when = isNaN(d.getTime()) ? new Date() : d;
    return t("projects.title_fallback", "Audit - {date}", {
      date: when.toLocaleDateString(),
    });
  }

  function usesFallbackTitle(project) {
    var title = ((project && project.title) || "").trim();
    if (
      project &&
      project.id === "default" &&
      (!title || title === "Default project" || title.toLowerCase() === "default")
    ) {
      return false;
    }
    return !title || PRJ_ID_RE.test(title);
  }

  function displayTitle(project) {
    var title = ((project && project.title) || "").trim();
    if (
      project &&
      project.id === "default" &&
      (!title || title === "Default project" || title.toLowerCase() === "default")
    ) {
      return t("projects.default_workspace", "Main Workspace");
    }
    if (!title || PRJ_ID_RE.test(title)) {
      return fallbackAuditTitle(project);
    }
    return title;
  }

  function readArchived() {
    try {
      var raw = localStorage.getItem(ARCHIVE_KEY);
      var list = raw ? JSON.parse(raw) : [];
      return Array.isArray(list) ? list : [];
    } catch (_) {
      return [];
    }
  }

  function writeArchived(ids) {
    try {
      localStorage.setItem(ARCHIVE_KEY, JSON.stringify(ids));
    } catch (_) {}
  }

  function isArchived(id) {
    return readArchived().indexOf(id) !== -1;
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

  function bucketStatus(project) {
    if (isArchived(project.id)) return "archived";
    var st = project.status || "drafting";
    if (st === "audited" || st === "ready_to_export") return "audited";
    return "drafting";
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
          escHtml(t("projects.vitals.nodes", "{count} nodes", { count: String(project.node_count) })) +
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

  function syncDocumentChrome() {
    var chrome = $("document-chrome");
    if (!chrome) return;
    var view = (global.AssureNav && global.AssureNav.activeView) || "";
    var compiler = view === "generate" || view === "surgical";
    var pid = global.__ASSURE_PROJECT_ID__ || "";
    chrome.hidden = !(compiler && pid);
  }

  function paintWorkspaceCanvas() {
    var noneEl = $("workspace-canvas-none");
    var selEl = $("workspace-canvas-selected");
    var createEl = $("workspace-canvas-create");
    if (!noneEl || !selEl || !createEl) return;

    if (isCreating) {
      noneEl.hidden = true;
      selEl.hidden = true;
      createEl.hidden = false;
      return;
    }

    var project = projectsCache.filter(function (p) {
      return p.id === selectedId;
    })[0];

    if (!selectedId || !project) {
      noneEl.hidden = false;
      selEl.hidden = true;
      createEl.hidden = true;
      return;
    }

    noneEl.hidden = true;
    createEl.hidden = true;
    selEl.hidden = false;
    var titleEl = $("workspace-canvas-title");
    var previewEl = $("workspace-canvas-preview");
    var sourcesEl = $("workspace-canvas-sources");
    var locksEl = $("workspace-canvas-locks");
    if (titleEl) titleEl.textContent = displayTitle(project);
    if (previewEl) {
      previewEl.textContent = t("canvas.workspace.preview", "{count} document nodes", {
        count: String(project.node_count || 0),
      });
    }
    if (sourcesEl) {
      sourcesEl.textContent = t("canvas.workspace.sources", "Sources loaded in the vault for this workspace.");
    }
    if (locksEl) {
      locksEl.textContent = project.lock_count
        ? t("projects.vitals.locks", "{count} locks verified", { count: String(project.lock_count) })
        : t("projects.vitals.locks_none", "No locks yet");
    }
  }

  function loadProjectIntoCompiler(projectId) {
    global.__ASSURE_PROJECT_ID__ = projectId;
    if (global.__assureJdf) global.__assureJdf.projectId = projectId;
    if (global.AssureProjectFileManager && typeof global.AssureProjectFileManager.remember === "function") {
      global.AssureProjectFileManager.remember(projectId);
    }

    ["btn-export-docx", "btn-export-md", "btn-export-html", "btn-export-pdf"].forEach(function (id) {
      var el = $(id);
      if (!el || !el.href) return;
      var fmt = id.split("-").pop();
      el.href = "/api/projects/" + encodeURIComponent(projectId) + "/export?format=" + fmt;
    });

    try {
      var url = new URL(global.location.href);
      if (projectId === "default") url.searchParams.delete("project");
      else url.searchParams.set("project", projectId);
      global.history.replaceState({ projectId: projectId }, "", url.pathname + url.search + url.hash);
    } catch (_) {}

    if (global.__assureJdf && typeof global.__assureJdf.loadProject === "function") {
      global.__assureJdf.loadProject().catch(function () {});
    }
    document.dispatchEvent(new CustomEvent("assure:project", { detail: { projectId: projectId } }));
    syncDocumentChrome();
  }

  function openCompiler(projectId) {
    selectedId = projectId;
    isCreating = false;
    loadProjectIntoCompiler(projectId);
    if (global.AssureNav) {
      global.AssureNav.switchView("generate", { replaceHash: false, skipUnsaved: true });
    }
  }

  function selectWorkspace(projectId) {
    selectedId = projectId || null;
    isCreating = false;
    renderDashboard(projectsCache);
    paintWorkspaceCanvas();
    document.dispatchEvent(
      new CustomEvent("assure:workspace-select", { detail: { selectedId: selectedId } })
    );
  }

  function beginCreate(templateId) {
    isCreating = true;
    selectedId = null;
    createTemplateId = templateId || "blank";
    pendingCreateFiles = [];
    renderDashboard(projectsCache);
    paintWorkspaceCanvas();
    var titleInput = $("workspace-create-title");
    if (titleInput && !titleInput.value.trim()) titleInput.focus();
    document.querySelectorAll("[data-create-template]").forEach(function (btn) {
      btn.classList.toggle("is-selected", btn.getAttribute("data-create-template") === createTemplateId);
    });
  }

  function persistWorkspace(opts) {
    opts = opts || {};
    var titleInput = $("workspace-create-title");
    var title = (titleInput && titleInput.value.trim()) || "";
    if (!title) {
      if (titleInput) titleInput.focus();
      return Promise.resolve();
    }
    var payload = { title: title, template_id: createTemplateId };
    return fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payload),
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
          return null;
        }
        var uploads = pendingCreateFiles.map(function (file) {
          var fd = new FormData();
          fd.append("file", file);
          return fetch("/api/projects/" + encodeURIComponent(data.id) + "/substrate/upload", {
            method: "POST",
            credentials: "same-origin",
            body: fd,
          }).then(function (r) {
            // Queued uploads finish on the worker; the processing panel shows them.
            if (r.status === 202 && global.AssureIngestJobs) global.AssureIngestJobs.track();
            return r;
          });
        });
        return Promise.all(uploads).then(function () {
          isCreating = false;
          pendingCreateFiles = [];
          selectedId = data.id;
          if (opts.openCompiler) openCompiler(data.id);
          else {
            loadProjects();
            paintWorkspaceCanvas();
          }
          return data;
        });
      });
  }

  function switchToProject(projectId, projectTitle, opts) {
    opts = opts || {};
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
    if (opts.selectOnly) {
      selectWorkspace(projectId);
      return;
    }
    openCompiler(projectId);
  }

  function runSuggestedAction(project, actionId) {
    openCompiler(project.id);
    if (!global.AssureNav) return;
    if (actionId === "review") {
      global.AssureNav.switchView("surgical", { replaceHash: false, skipUnsaved: true });
    }
  }

  function closeOpenMenus(except) {
    document.querySelectorAll(".project-overflow.is-open").forEach(function (el) {
      if (el !== except) el.classList.remove("is-open");
    });
  }

  function emptyStateMessage() {
    if (!projectsCache.length) {
      return t(
        "projects.empty",
        "No workspaces yet. Click + New to initialize your first project."
      );
    }
    if (statusFilter === "drafting") {
      return t("projects.empty.filter_drafting", "No Drafting workspaces found.");
    }
    if (statusFilter === "audited") {
      return t("projects.empty.filter_audited", "No Audited workspaces found.");
    }
    if (statusFilter === "archived") {
      return t("projects.empty.filter_archived", "No Archived workspaces found.");
    }
    return t(
      "projects.empty",
      "No workspaces yet. Click + New to initialize your first project."
    );
  }

  function renderDashboard(projects) {
    var host = $("projects-dashboard");
    if (!host) return;
    projectsCache = projects || [];

    var filtered = projectsCache.filter(function (p) {
      var bucket = bucketStatus(p);
      if (statusFilter === "all") return bucket !== "archived";
      return bucket === statusFilter;
    });

    if (!filtered.length) {
      host.innerHTML =
        '<p class="projects-dashboard-empty">' + escHtml(emptyStateMessage()) + "</p>";
      paintWorkspaceCanvas();
      return;
    }

    host.innerHTML = "";
    filtered.forEach(function (p) {
      var isActive = !!selectedId && p.id === selectedId;
      var archived = isArchived(p.id);
      var meta = statusMeta(p.status || "drafting");
      var card = document.createElement("article");
      card.className =
        "project-work-card project-status-" +
        (p.status || "drafting") +
        (isActive ? " is-active" : "") +
        (archived ? " is-archived" : "");
      card.dataset.projectId = p.id;
      card.setAttribute("role", "listitem");

      var titleText = displayTitle(p);
      var titleTooltip = usesFallbackTitle(p)
        ? t(
            "projects.title_fallback_tooltip",
            "System-generated title. Click 'Rename' in actions to customize."
          )
        : "";
      var statusPill = archived
        ? '<span class="project-status-pill project-archived-badge" data-status="archived">' +
          escHtml(t("projects.badge.archived", "Archived")) +
          "</span>"
        : '<span class="project-status-pill" data-status="' +
          escHtml(p.status || "drafting") +
          '">' +
          escHtml(t(meta.labelKey, meta.labelFallback)) +
          "</span>";

      card.innerHTML =
        '<div class="project-work-card-top">' +
        statusPill +
        (isActive
          ? '<span class="project-active-badge">' + escHtml(t("projects.active_badge", "Active")) + "</span>"
          : "") +
        '<div class="project-overflow">' +
        '<button type="button" class="projects-action-btn project-overflow-toggle" aria-haspopup="menu" aria-expanded="false" title="' +
        escHtml(t("projects.menu.actions", "Workspace actions")) +
        '" aria-label="' +
        escHtml(t("projects.menu.actions_menu", "Workspace actions menu")) +
        '">…</button>' +
        '<div class="project-overflow-menu" hidden role="menu">' +
        '<button type="button" role="menuitem" data-tool="rename">' +
        escHtml(t("projects.menu.rename", "Rename Workspace")) +
        "</button>" +
        '<button type="button" role="menuitem" data-tool="duplicate">' +
        escHtml(t("projects.menu.duplicate", "Duplicate Workspace")) +
        "</button>" +
        '<button type="button" role="menuitem" data-tool="archive">' +
        escHtml(t("projects.menu.archive", "Archive Workspace")) +
        "</button>" +
        (p.id !== "default"
          ? '<button type="button" role="menuitem" class="is-danger" data-tool="delete">' +
            escHtml(t("projects.menu.delete", "Delete Workspace")) +
            "</button>"
          : "") +
        "</div></div></div>" +
        '<button type="button" class="project-work-open" aria-label="' +
        escHtml(t("projects.action.open", "Open workspace")) +
        '">' +
        '<h4 class="project-work-title"' +
        (titleTooltip ? ' title="' + escHtml(titleTooltip) + '"' : "") +
        ">" +
        escHtml(titleText) +
        "</h4>" +
        '<div class="project-work-vitals">' +
        vitalSignsHtml(p) +
        "</div></button>";

      card.querySelector(".project-work-open").addEventListener("click", function () {
        selectWorkspace(p.id);
      });

      var overflow = card.querySelector(".project-overflow");
      var toggle = card.querySelector(".project-overflow-toggle");
      var menu = card.querySelector(".project-overflow-menu");
      toggle.addEventListener("click", function (e) {
        e.stopPropagation();
        var open = !overflow.classList.contains("is-open");
        closeOpenMenus(overflow);
        overflow.classList.toggle("is-open", open);
        menu.hidden = !open;
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
      });
      toggle.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggle.click();
        }
        if (e.key === "Escape") {
          closeOpenMenus();
          menu.hidden = true;
          toggle.setAttribute("aria-expanded", "false");
        }
      });
      card.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
          closeOpenMenus();
          menu.hidden = true;
          toggle.setAttribute("aria-expanded", "false");
        }
      });

      card.querySelector('[data-tool="rename"]').addEventListener("click", function (e) {
        e.stopPropagation();
        closeOpenMenus();
        startInlineRename(card, p);
      });
      card.querySelector('[data-tool="duplicate"]').addEventListener("click", function (e) {
        e.stopPropagation();
        closeOpenMenus();
        duplicateWorkspace(p);
      });
      card.querySelector('[data-tool="archive"]').addEventListener("click", function (e) {
        e.stopPropagation();
        closeOpenMenus();
        archiveWorkspace(p);
      });
      var del = card.querySelector('[data-tool="delete"]');
      if (del) {
        del.addEventListener("click", function (e) {
          e.stopPropagation();
          closeOpenMenus();
          confirmDelete(p);
        });
      }

      host.appendChild(card);
    });
    paintWorkspaceCanvas();
  }

  function startInlineRename(card, project) {
    var existing = card.querySelector(".projects-rename-row");
    if (existing) {
      existing.querySelector("input").focus();
      return;
    }
    var openBtn = card.querySelector(".project-work-open");
    if (openBtn) openBtn.hidden = true;
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

  function duplicateWorkspace(project) {
    fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ title: displayTitle(project) + " copy" }),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) return;
        return fetch("/api/projects/" + encodeURIComponent(project.id) + "/files", {
          credentials: "same-origin",
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (files) {
            return fetch("/api/projects/" + encodeURIComponent(data.id) + "/files", {
              method: "PUT",
              credentials: "same-origin",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                source_md: files.source_md || "",
                lastCompiledOutput: files.lastCompiledOutput || files.manifest || null,
              }),
            });
          })
          .then(function () {
            loadProjects();
          });
      });
  }

  function archiveWorkspace(project) {
    var ids = readArchived();
    if (ids.indexOf(project.id) === -1) ids.push(project.id);
    writeArchived(ids);
    if (selectedId === project.id) selectedId = null;
    loadProjects();
  }

  function showConfirm(message, onOk) {
    var dlg = $("workspace-confirm-dialog");
    var body = $("workspace-confirm-body");
    var ok = $("workspace-confirm-ok");
    var cancel = $("workspace-confirm-cancel");
    var backdrop = $("workspace-confirm-backdrop");
    if (!dlg || !body || !ok) {
      if (global.confirm(message)) onOk();
      return;
    }
    body.textContent = message;
    dlg.hidden = false;
    function close() {
      dlg.hidden = true;
      ok.removeEventListener("click", accept);
      cancel.removeEventListener("click", close);
      if (backdrop) backdrop.removeEventListener("click", close);
    }
    function accept() {
      close();
      onOk();
    }
    ok.addEventListener("click", accept);
    if (cancel) cancel.addEventListener("click", close);
    if (backdrop) backdrop.addEventListener("click", close);
  }

  function confirmDelete(project) {
    pendingDelete = project;
    var msg = t("projects.confirm_delete", 'Delete "{name}"? This cannot be undone.', {
      name: displayTitle(project),
    });
    showConfirm(msg, function () {
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
          if (selectedId === project.id) selectedId = null;
          if (project.id === (global.__ASSURE_PROJECT_ID__ || "default")) {
            global.__ASSURE_PROJECT_ID__ = "default";
          }
          loadProjects();
        });
    });
  }

  function loadProjects() {
    var host = $("projects-dashboard");
    if (!host) return;
    host.innerHTML =
      '<p class="projects-dashboard-loading">' + escHtml(t("projects.loading", "Loading…")) + "</p>";
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

  function initNewProjectForm() {
    var newBtn = $("projects-new-btn");
    if (newBtn) {
      newBtn.addEventListener("click", function () {
        beginCreate("blank");
      });
    }
    var confirmBtn = $("workspace-create-confirm");
    if (confirmBtn) {
      confirmBtn.addEventListener("click", function () {
        persistWorkspace({ openCompiler: false });
      });
    }
    document.querySelectorAll("[data-create-template]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        createTemplateId = btn.getAttribute("data-create-template") || "blank";
        document.querySelectorAll("[data-create-template]").forEach(function (el) {
          el.classList.toggle("is-selected", el === btn);
        });
      });
    });
    var drop = $("workspace-create-dropzone");
    var fileInput = $("workspace-create-file");
    if (drop && fileInput) {
      drop.addEventListener("click", function () {
        fileInput.click();
      });
      drop.addEventListener("dragover", function (e) {
        e.preventDefault();
      });
      drop.addEventListener("drop", function (e) {
        e.preventDefault();
        var files = e.dataTransfer && e.dataTransfer.files;
        if (!files || !files.length) return;
        pendingCreateFiles = Array.prototype.slice.call(files);
        persistWorkspace({ openCompiler: false });
      });
      fileInput.addEventListener("change", function () {
        pendingCreateFiles = Array.prototype.slice.call(fileInput.files || []);
        fileInput.value = "";
      });
    }
    var openBtn = $("workspace-open-compiler");
    if (openBtn) {
      openBtn.addEventListener("click", function () {
        if (selectedId) openCompiler(selectedId);
      });
    }
    document.querySelectorAll("#projects-status-tabs [data-filter]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        statusFilter = btn.getAttribute("data-filter") || "all";
        document.querySelectorAll("#projects-status-tabs [data-filter]").forEach(function (el) {
          el.classList.toggle("is-active", el === btn);
        });
        renderDashboard(projectsCache);
      });
    });
    document.addEventListener("click", function () {
      closeOpenMenus();
    });
  }

  function restoreProjectFromUrl() {
    var hashId = null;
    try {
      var hash = (location.hash || "").replace(/^#/, "");
      if (hash.indexOf("view=") === 0) {
        hashId = new URLSearchParams(hash).get("id");
      }
    } catch (_) {}
    var files = global.AssureProjectFileManager;
    var pid = hashId;
    if (!pid) {
      try {
        pid = new URL(global.location.href).searchParams.get("project");
      } catch (_) {}
    }
    if (pid && (location.hash || "").indexOf("view=compiler") !== -1) {
      openCompiler(pid);
    } else if (pid && files && typeof files.hydrate === "function") {
      files.hydrate(pid).catch(function () {});
    }
  }

  function titleFor(projectId) {
    var pid = String(projectId || "").trim();
    if (!pid) return "";
    var project = projectsCache.filter(function (p) {
      return p.id === pid;
    })[0];
    if (!project) project = { id: pid, title: "" };
    return displayTitle(project);
  }

  var AssureProjects = {
    load: loadProjects,
    switchTo: switchToProject,
    select: selectWorkspace,
    beginCreate: beginCreate,
    openCompiler: openCompiler,
    titleFor: titleFor,
    get selectedId() {
      return selectedId;
    },
    get isCreating() {
      return isCreating;
    },
    init: function () {
      initNewProjectForm();
      restoreProjectFromUrl();
      document.addEventListener("assure:view", syncDocumentChrome);
      syncDocumentChrome();
    },
  };

  global.AssureProjects = AssureProjects;
})(window);
