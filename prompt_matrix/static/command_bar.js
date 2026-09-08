/**
 * Founder workbench — global command bar (⌘K) with Auto-Compiler SSE streaming.
 */
(function (global) {
  "use strict";

  var overlay = null;
  var input = null;
  var statusEl = null;
  var pendingFiles = [];
  var workspaceId = "default";
  var activeStream = null;
  var submitting = false;

  function $(id) {
    return document.getElementById(id);
  }

  function toast(message, kind) {
    if (global.AssureToast && typeof global.AssureToast.show === "function") {
      global.AssureToast.show(message, kind || "error");
    }
  }

  function setStatus(message) {
    if (statusEl) statusEl.textContent = message || "";
  }

  function setPipelineStatus(message) {
    if (global.AssureRunsStack && typeof global.AssureRunsStack.setPipelineStatus === "function") {
      global.AssureRunsStack.setPipelineStatus(message);
    }
  }

  function clearBusy() {
    submitting = false;
    setPipelineStatus("");
    if (activeStream && typeof activeStream.abort === "function") {
      try {
        activeStream.abort();
      } catch (_) {}
    }
    activeStream = null;
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
    setStatus("");
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

  function formatStatus(data) {
    if (!data) return "";
    var stage = data.stage || "router";
    var intent = data.intent_type || "draft";
    var count = data.source_count != null ? data.source_count : "—";
    var ms = data.router_ms != null ? " · " + data.router_ms + "ms" : "";
    if (stage === "compile_prompt") {
      return "Compiling prompt… " + intent + (data.web_fallback ? " · web fallback" : "");
    }
    return "Routing… " + intent + " · " + count + " sources" + ms;
  }

  function handleSseFrame(frame) {
    var event = frame && frame.event;
    var data = (frame && frame.data) || {};
    if (event === "status") {
      var msg = formatStatus(data);
      setStatus(msg);
      setPipelineStatus(msg);
      return;
    }
    if (event === "token" && data.delta) {
      if (global.AssureFounderDraft && typeof global.AssureFounderDraft.appendStreamToken === "function") {
        global.AssureFounderDraft.appendStreamToken(data.delta);
      }
      return;
    }
    if (event === "lock") {
      if (global.AssureFounderDraft && typeof global.AssureFounderDraft.insertStreamLock === "function") {
        global.AssureFounderDraft.insertStreamLock(data);
      }
      return;
    }
    if (event === "complete") {
      if (global.AssureFounderDraft && typeof global.AssureFounderDraft.finishStreaming === "function") {
        global.AssureFounderDraft.finishStreaming();
      }
      clearBusy();
      close();
      if (data.run && global.AssureRunsStack && typeof global.AssureRunsStack.prepend === "function") {
        global.AssureRunsStack.prepend(data.run);
      }
      document.dispatchEvent(new CustomEvent("assure:run-created", { detail: data.run || data }));
      return;
    }
    if (event === "error") {
      clearBusy();
      var errMsg = data.error || "Run failed";
      setStatus(errMsg);
      toast(errMsg, "error");
      if (global.AssureFounderDraft && typeof global.AssureFounderDraft.finishStreaming === "function") {
        global.AssureFounderDraft.finishStreaming();
      }
    }
  }

  function submitDirectiveSync(directive, sourceIds) {
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
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.ok) {
          throw new Error(data.error || "Run failed");
        }
        if (global.AssureFounderDraft && typeof global.AssureFounderDraft.appendRun === "function") {
          global.AssureFounderDraft.appendRun(data.run);
        }
        close();
        if (global.AssureRunsStack && typeof global.AssureRunsStack.prepend === "function") {
          global.AssureRunsStack.prepend(data.run);
        }
        document.dispatchEvent(new CustomEvent("assure:run-created", { detail: data.run }));
      });
  }

  function submitDirectiveStream(directive, sourceIds) {
    var postSse =
      (global.AssureSse && global.AssureSse.postStream) ||
      (global.AssureInquire && global.AssureInquire.postSseStream);
    if (!postSse) {
      return submitDirectiveSync(directive, sourceIds);
    }

    if (global.AssureFounderDraft && typeof global.AssureFounderDraft.beginStreaming === "function") {
      global.AssureFounderDraft.beginStreaming();
    }

    var controller = new AbortController();
    activeStream = { abort: function () { controller.abort(); } };

    return postSse({
      url: "/api/runs",
      credentials: "same-origin",
      signal: controller.signal,
      headers: { Accept: "text/event-stream" },
      body: {
        directive: directive,
        workspace_id: workspaceId,
        source_ids: sourceIds,
        model: "gemini",
        stream: true,
      },
      onFrame: handleSseFrame,
    }).catch(function (err) {
      if (err && err.name === "AbortError") return;
      clearBusy();
      if (global.AssureFounderDraft && typeof global.AssureFounderDraft.finishStreaming === "function") {
        global.AssureFounderDraft.finishStreaming();
      }
      return submitDirectiveSync(directive, sourceIds);
    });
  }

  function submitDirective() {
    var directive = (input && input.value.trim()) || "";
    if (!directive) {
      setStatus("Enter a directive first.");
      return;
    }
    if (submitting) return;
    submitting = true;
    setStatus("Running verification…");
    setPipelineStatus("Starting…");

    var chain = pendingFiles.length ? uploadFiles(pendingFiles) : Promise.resolve([]);
    chain
      .then(function (sourceIds) {
        return submitDirectiveStream(directive, sourceIds);
      })
      .catch(function (err) {
        clearBusy();
        var msg = String((err && err.message) || err);
        setStatus(msg);
        toast(msg, "error");
      })
      .finally(function () {
        submitting = false;
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

  function isTypingTarget() {
    var el = document.activeElement;
    if (!el) return false;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return true;
    if (el.isContentEditable) return true;
    return false;
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
      if (isTypingTarget()) return;
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
