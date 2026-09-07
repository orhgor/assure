/**
 * Founder workbench — global command bar (⌘K).
 */
(function (global) {
  "use strict";

  var overlay = null;
  var input = null;
  var statusEl = null;
  var pendingFiles = [];
  var workspaceId = "default";

  function $(id) {
    return document.getElementById(id);
  }

  function open() {
    if (!overlay) return;
    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
    if (input) {
      input.value = "";
      input.focus();
    }
    pendingFiles = [];
    if (statusEl) statusEl.textContent = "";
  }

  function close() {
    if (!overlay) return;
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
  }

  function uploadFiles(files) {
    var uploads = Array.from(files || []).map(function (file) {
      var fd = new FormData();
      fd.append("file", file);
      return fetch(
        "/api/projects/" + encodeURIComponent(workspaceId) + "/substrate/upload",
        { method: "POST", credentials: "same-origin", body: fd }
      )
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          return data.id || data.file_id || null;
        });
    });
    return Promise.all(uploads).then(function (ids) {
      return ids.filter(Boolean);
    });
  }

  function submitDirective() {
    var directive = (input && input.value.trim()) || "";
    if (!directive) {
      if (statusEl) statusEl.textContent = "Enter a directive first.";
      return;
    }
    if (statusEl) statusEl.textContent = "Running verification…";
    var chain = pendingFiles.length
      ? uploadFiles(pendingFiles)
      : Promise.resolve([]);
    chain
      .then(function (sourceIds) {
        return fetch("/api/runs", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            directive: directive,
            workspace_id: workspaceId,
            source_ids: sourceIds,
            model: "gemini",
          }),
        });
      })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          throw new Error(data.error || "Run failed");
        }
        close();
        if (global.AssureRunsStack && typeof global.AssureRunsStack.prepend === "function") {
          global.AssureRunsStack.prepend(data.run);
        }
        document.dispatchEvent(new CustomEvent("assure:run-created", { detail: data.run }));
      })
      .catch(function (err) {
        if (statusEl) statusEl.textContent = String(err.message || err);
      });
  }

  function bindDropzone() {
    var zone = $("command-bar-dropzone");
    var fileInput = $("command-bar-file-input");
    if (!zone) return;
    zone.addEventListener("click", function () {
      if (fileInput) fileInput.click();
    });
    if (fileInput) {
      fileInput.addEventListener("change", function () {
        pendingFiles = Array.from(fileInput.files || []);
        if (statusEl && pendingFiles.length) {
          statusEl.textContent = pendingFiles.length + " file(s) attached";
        }
      });
    }
    zone.addEventListener("dragover", function (e) {
      e.preventDefault();
      zone.classList.add("is-dragover");
    });
    zone.addEventListener("dragleave", function () {
      zone.classList.remove("is-dragover");
    });
    zone.addEventListener("drop", function (e) {
      e.preventDefault();
      zone.classList.remove("is-dragover");
      pendingFiles = Array.from(e.dataTransfer.files || []);
      if (statusEl && pendingFiles.length) {
        statusEl.textContent = pendingFiles.length + " file(s) attached";
      }
    });
  }

  function init() {
    overlay = $("command-bar-overlay");
    input = $("command-bar-input");
    statusEl = $("command-bar-status");
    workspaceId = global.__ASSURE_PROJECT_ID__ || "default";
    if (input) {
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          submitDirective();
        }
        if (e.key === "Escape") close();
      });
    }
    if (overlay) {
      overlay.addEventListener("click", function (e) {
        if (e.target === overlay) close();
      });
    }
    bindDropzone();
    document.addEventListener("assure:project", function (ev) {
      workspaceId = (ev.detail && ev.detail.projectId) || workspaceId;
    });
    document.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k" && !e.shiftKey) {
        var founderOn =
          global.AssureFounderMode && typeof global.AssureFounderMode.isEnabled === "function"
            ? global.AssureFounderMode.isEnabled()
            : document.body.classList.contains("founder-workbench");
        if (founderOn) {
          e.preventDefault();
          e.stopPropagation();
          if (overlay && overlay.hidden) open();
          else close();
        }
      }
    }, true);
  }

  global.AssureCommandBar = { open: open, close: close, submit: submitDirective, init: init };
  document.addEventListener("DOMContentLoaded", init);
})(window);
