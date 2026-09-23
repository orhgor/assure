/**
 * Founder workbench — global command bar (⌘K).
 * Founder mode routes to Difference Engine (POST /api/runs/compare via AssureOrchestrator).
 * Legacy workbench keeps Auto-Compiler SSE streaming (POST /api/runs).
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

  function translate(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function translatef(key, fallback, params) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, params || {});
    }
    var out = translate(key, fallback);
    Object.keys(params || {}).forEach(function (k) {
      out = out.replace("{" + k + "}", String(params[k]));
    });
    return out;
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

  function setSubmitUiBusy(on) {
    var modal = overlay && overlay.querySelector(".command-bar-modal");
    if (modal) modal.classList.toggle("is-busy", !!on);
    if (input) {
      input.disabled = !!on;
      input.setAttribute("aria-busy", on ? "true" : "false");
    }
  }

  function clearBusy() {
    submitting = false;
    setSubmitUiBusy(false);
    setPipelineStatus("");
    if (activeStream && typeof activeStream.abort === "function") {
      try {
        activeStream.abort();
      } catch (_) {}
    }
    activeStream = null;
  }

  function captureInvokeContext() {
    var ctx = {
      selected_text: "",
      full_document_context: [],
      active_source_ids: [],
    };
    if (global.AssureTiptapEditor) {
      if (typeof global.AssureTiptapEditor.getSelectedTextRange === "function") {
        var range = global.AssureTiptapEditor.getSelectedTextRange();
        ctx.selected_text = (range && range.text) || "";
      }
      if (typeof global.AssureTiptapEditor.getDocumentContextAst === "function") {
        ctx.full_document_context = global.AssureTiptapEditor.getDocumentContextAst() || [];
      }
    }
    if (global.AssureSubstrateVault && typeof global.AssureSubstrateVault.selectedIncludedIds === "function") {
      ctx.active_source_ids = global.AssureSubstrateVault.selectedIncludedIds() || [];
    }
    try {
      console.info("[Assure] context-aware invoke payload", ctx);
    } catch (_) {}
    return ctx;
  }

  function open(options) {
    options = options || {};
    if (!overlay) return;
    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
    if (input) {
      input.value = options.prefill || "";
      input.focus();
      if (options.prefill) {
        input.setSelectionRange(input.value.length, input.value.length);
      }
    }
    pendingFiles = [];
    setStatus("");
  }

  function openWithSelectionContext() {
    var ctx = captureInvokeContext();
    if (!ctx.selected_text) {
      var ed =
        global.AssureTiptapEditor && global.AssureTiptapEditor.getEditor && global.AssureTiptapEditor.getEditor();
      if (ed && !ed.state.selection.empty) {
        ctx.selected_text = ed.state.doc.textBetween(ed.state.selection.from, ed.state.selection.to, "\n");
      }
    }
    open({ prefill: ctx.selected_text || "" });
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
          return r.json().then(function (data) { return { status: r.status, data: data }; });
        })
        .then(function (res) {
          var data = res.data || {};
          if (res.status === 202 && data.task_id && global.AssureIngestJobs) {
            // Queued to the parse worker: the vault row exists only once the
            // task finishes, so wait for it before compiling against it.
            global.AssureIngestJobs.track();
            return global.AssureIngestJobs.awaitTask(data.task_id).then(function (result) {
              var entry = (result && result.entry) || result || {};
              return entry.id || entry.file_id || null;
            }).catch(function () { return null; });
          }
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
      return translatef("command.bar.status_compiling", "Compiling prompt… {intent}{fallback}", {
        intent: intent,
        fallback: data.web_fallback ? translate("command.bar.web_fallback", " · web fallback") : "",
      });
    }
    return translatef("command.bar.status_routing", "Routing… {intent} · {count} sources{ms}", {
      intent: intent,
      count: count,
      ms: ms,
    });
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
    if (event === "verification_complete") {
      var locks = (data.locks || (data.data && data.data.locks)) || [];
      locks.forEach(function (lock) {
        if (lock.status !== "grounded") return;
        if (global.AssureFounderDraft && typeof global.AssureFounderDraft.insertStreamLock === "function") {
          global.AssureFounderDraft.insertStreamLock({
            claim_id: lock.claim_id,
            lock_hash: lock.lock_hash,
            source_id: lock.source_id,
            page_coordinates: lock.page_coordinates,
            metric: lock.text || lock.claim_id,
            lock_index: lock.lock_index,
          });
        }
      });
      document.dispatchEvent(new CustomEvent("assure:z3-verified"));
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

  function isFounderShell() {
    var founderOn =
      global.AssureFounderMode && typeof global.AssureFounderMode.isEnabled === "function"
        ? global.AssureFounderMode.isEnabled()
        : document.body.classList.contains("founder-workbench");
    return founderOn && !document.body.classList.contains("legacy-workbench");
  }

  function mergedSourceIds(uploadedIds) {
    var ctx = captureInvokeContext();
    var fromVault = ctx.active_source_ids || [];
    var merged = (uploadedIds || []).slice();
    fromVault.forEach(function (id) {
      if (merged.indexOf(id) === -1) merged.push(id);
    });
    return merged;
  }

  function buildIntent(directive, options) {
    var text = String(directive || "").trim();
    var selected = String((options && options.selected_text) || "").trim();
    if (selected) {
      text = text + "\n\n---\nSelected:\n" + selected;
    }
    return text;
  }

  function runCompareDirective(directive, sourceIds, options) {
    options = options || {};
    if (submitting) return Promise.resolve();
    submitting = true;
    setSubmitUiBusy(true);
    var statusMsg = translate("command.bar.running_compare", "Running compare…");
    setStatus(statusMsg);
    setPipelineStatus(statusMsg);
    global.__assureActiveSourceIds = mergedSourceIds(sourceIds);
    var intent = buildIntent(directive, options);
    var run =
      global.AssureComparePane && typeof global.AssureComparePane.run === "function"
        ? global.AssureComparePane.run.bind(global.AssureComparePane)
        : global.AssureOrchestrator && typeof global.AssureOrchestrator.run === "function"
          ? global.AssureOrchestrator.run
          : null;
    if (!run) {
      submitting = false;
      var unavailable = translate(
        "command.bar.compare_unavailable",
        "Compare engine unavailable"
      );
      setStatus(unavailable);
      toast(unavailable, "error");
      setSubmitUiBusy(false);
      return Promise.reject(new Error(unavailable));
    }
    return run(intent)
      .then(function () {
        close();
        pendingFiles = [];
      })
      .catch(function (err) {
        close();
        var msg = String((err && err.message) || err);
        setStatus(msg);
        toast(msg, "error");
        throw err;
      })
      .finally(function () {
        submitting = false;
        setSubmitUiBusy(false);
        setPipelineStatus("");
      });
  }

  function runDirective(directive, sourceIds) {
    if (submitting) return Promise.resolve();
    submitting = true;
    setSubmitUiBusy(true);
    setStatus(translate("command.bar.running", "Running verification…"));
    setPipelineStatus(translate("command.bar.running", "Running verification…"));
    return submitDirectiveStream(directive, sourceIds || [])
      .catch(function (err) {
        clearBusy();
        var msg = String((err && err.message) || err);
        setStatus(msg);
        toast(msg, "error");
        throw err;
      })
      .finally(function () {
        submitting = false;
        setSubmitUiBusy(false);
      });
  }

  function submitDirective() {
    var directive = (input && input.value.trim()) || "";
    if (!directive) {
      setStatus(translate("command.bar.enter_directive", "Describe what to investigate first."));
      return;
    }
    var chain = pendingFiles.length ? uploadFiles(pendingFiles) : Promise.resolve([]);
    chain.then(function (sourceIds) {
      if (isFounderShell()) {
        return runCompareDirective(directive, sourceIds);
      }
      return runDirective(directive, sourceIds);
    });
  }

  function submitExternal(directive, options) {
    options = options || {};
    var text = String(directive || "").trim();
    if (!text) return Promise.resolve();
    if (isFounderShell()) {
      return runCompareDirective(text, options.source_ids || [], options);
    }
    text = buildIntent(text, options);
    if (global.AssureFounderDraft && typeof global.AssureFounderDraft.beginStreaming === "function") {
      global.AssureFounderDraft.beginStreaming();
    }
    return runDirective(text, options.source_ids || []);
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
          statusEl.textContent = translatef(
            "command.bar.files_attached",
            "{count} file(s) attached",
            { count: pendingFiles.length }
          );
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
        statusEl.textContent = translatef(
          "command.bar.files_attached",
          "{count} file(s) attached",
          { count: pendingFiles.length }
        );
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
    workspaceId =
      global.AssureFounderMode && typeof global.AssureFounderMode.getWorkspaceId === "function"
        ? global.AssureFounderMode.getWorkspaceId()
        : global.__ASSURE_PROJECT_ID__ || "default";
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
      if (global.AssureFounderMode && global.AssureFounderMode.isEnabled()) return;
      workspaceId = (ev.detail && ev.detail.projectId) || workspaceId;
    });
    document.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k" && !e.shiftKey) {
        var founderOn =
          global.AssureFounderMode && typeof global.AssureFounderMode.isEnabled === "function"
            ? global.AssureFounderMode.isEnabled()
            : document.body.classList.contains("founder-workbench");
        if (founderOn) {
          var ed =
            global.AssureTiptapEditor && global.AssureTiptapEditor.getEditor && global.AssureTiptapEditor.getEditor();
          if (ed && ed.isFocused) {
            return;
          }
          e.preventDefault();
          e.stopPropagation();
          if (!overlay || overlay.hidden) openWithSelectionContext();
          else close();
          return;
        }
      }
      if (isTypingTarget()) return;
    }, true);
  }

  global.AssureCommandBar = {
    open: open,
    openWithSelectionContext: openWithSelectionContext,
    captureInvokeContext: captureInvokeContext,
    close: close,
    submit: submitDirective,
    submitExternal: submitExternal,
    init: init,
  };
  document.addEventListener("DOMContentLoaded", init);
})(window);
