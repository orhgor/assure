(function (global) {
  "use strict";

  function $(id) {
    return document.getElementById(id);
  }

  function t(key, fallback, vars) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, vars || {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback || key;
  }

  function projectId() {
    return global.__ASSURE_PROJECT_ID__ || "default";
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** Robust SSE parser — buffer until full frames; safe JSON.parse. */
  function parseSseBuffer(buffer) {
    var events = [];
    var remainder = buffer;
    var sep;
    while ((sep = remainder.indexOf("\n\n")) >= 0) {
      var block = remainder.slice(0, sep);
      remainder = remainder.slice(sep + 2);
      if (!block.trim()) continue;
      if (block.trim() === "data: [DONE]") {
        events.push({ event: "done", data: { type: "done" } });
        continue;
      }
      var eventName = "message";
      var dataParts = [];
      block.split("\n").forEach(function (line) {
        if (line.indexOf("event:") === 0) {
          eventName = line.slice(6).trim();
        } else if (line.indexOf("data:") === 0) {
          dataParts.push(line.slice(5).trim());
        }
      });
      var dataLine = dataParts.join("\n");
      if (!dataLine || dataLine === "[DONE]") {
        if (dataLine === "[DONE]") {
          events.push({ event: "done", data: { type: "done" } });
        }
        continue;
      }
      try {
        events.push({ event: eventName, data: JSON.parse(dataLine) });
      } catch (_) {
        events.push({ event: eventName, data: { raw: dataLine, parse_error: true } });
      }
    }
    return { events: events, remainder: remainder };
  }

  function eventType(frame) {
    var data = frame.data || {};
    return data.type || frame.event || "message";
  }

  /** Lock inference + Red-Hat can run 30–90s with no SSE chunks. */
  var SSE_IDLE_MS = 90000;
  var REDHAT_WALL_MS = 90000;

  var AssureGenerate = {
    controller: null,
    compiledNodes: [],
    compiledLocks: [],
    compiledDocument: null,
    draftText: "",
    auditComplete: false,
    verifyTimeout: null,
    _streamRetryCount: 0,
    _docked: false,
    _redhatPending: false,
    _redhatCtx: null,
    fullAudit: false,
    redhatController: null,
    _auditManifest: null,

    clearVerifyTimeout: function () {
      if (this.verifyTimeout) {
        this.verifyTimeout.clear();
        this.verifyTimeout = null;
      }
    },

    showVerifyTimeout: function () {
      var self = this;
      var msg =
        global.AssureAuditGate && typeof global.AssureAuditGate.timeoutRetryMessage === "function"
          ? global.AssureAuditGate.timeoutRetryMessage()
          : t("audit.timeout", "Verification timeout — click to retry");
      if (global.__assureDemoRedhatPending) {
        document.dispatchEvent(new CustomEvent("assure:demo-redhat-audit"));
      }
      self.setGateLoading(false);
      var statusText = $("gate-status-text");
      var loader = $("gate-loader");
      if (loader) loader.hidden = false;
      if (statusText) {
        statusText.textContent = msg;
        statusText.classList.add("verify-timeout-retry");
        statusText.setAttribute("role", "button");
        statusText.setAttribute("tabindex", "0");
        statusText.onclick = function () {
          statusText.classList.remove("verify-timeout-retry");
          statusText.removeAttribute("role");
          statusText.removeAttribute("tabindex");
          statusText.onclick = null;
          self.startDraftStream();
        };
        statusText.onkeydown = function (e) {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            statusText.onclick();
          }
        };
      }
    },

    startVerifyTimeout: function () {
      var self = this;
      this.clearVerifyTimeout();
      // Hybrid compile: this only has to cover lock inference + the fast,
      // local Z3 check (the "verified" event), not the slow Stress Test
      // that now runs in the background after docking is already unblocked.
      if (global.AssureAuditGate && typeof global.AssureAuditGate.createVerificationTimeout === "function") {
        this.verifyTimeout = global.AssureAuditGate.createVerificationTimeout(function () {
          if (!self.auditComplete) self.showVerifyTimeout();
        }, 20000);
      } else {
        var timer = setTimeout(function () {
          if (!self.auditComplete) self.showVerifyTimeout();
        }, 20000);
        this.verifyTimeout = {
          clear: function () {
            clearTimeout(timer);
          },
        };
      }
    },

    init: function () {
      var self = this;
      var btn = $("generate-compile-btn");
      var intentEl = $("generate-intent");
      var dockBtn = $("generate-accept-dock");

      if (btn) {
        btn.addEventListener("click", function () {
          btn.classList.remove("is-pulse");
          void btn.offsetWidth;
          btn.classList.add("is-pulse");
          self.startDraftStream(false, { fullAudit: false });
        });
      }
      var fullAuditBtn = $("generate-full-audit-btn");
      if (fullAuditBtn) {
        fullAuditBtn.addEventListener("click", function () {
          self.startDraftStream(false, { fullAudit: true });
        });
      }
      if (intentEl) {
        intentEl.addEventListener("keydown", function (e) {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            self.startDraftStream();
          }
        });
      }
      if (dockBtn) {
        dockBtn.addEventListener("click", function () {
          self.acceptAndDock();
        });
      }
      var recompileBtn = $("generate-recompile-btn");
      if (recompileBtn) {
        recompileBtn.addEventListener("click", function () {
          self.startDraftStream();
        });
      }
      var discardBtn = $("generate-discard-btn");
      if (discardBtn) {
        discardBtn.addEventListener("click", function () {
          self.resetUi();
        });
      }
      var previewDockBtn = $("draft-preview-dock-btn");
      if (previewDockBtn) {
        previewDockBtn.addEventListener("click", function () {
          self.acceptAndDock();
        });
      }
      var previewDiscardBtn = $("draft-preview-discard-btn");
      if (previewDiscardBtn) {
        previewDiscardBtn.addEventListener("click", function () {
          self.resetUi();
        });
      }
      var redhatRunBtn = $("redhat-run-btn");
      if (redhatRunBtn) {
        redhatRunBtn.addEventListener("click", function () {
          self.runRedhatStress();
        });
      }
      var redhatSkipBtn = $("redhat-skip-btn");
      if (redhatSkipBtn) {
        redhatSkipBtn.addEventListener("click", function () {
          self.skipRedhatStress();
        });
      }

      document.addEventListener("assure:abort-streams", function () {
        self.abort();
      });

      // ── Cross-pane linkage: checklist ↔ canvas ─────────────────────────
      var lockChecklist = $("generate-lock-checklist");
      if (lockChecklist) {
        lockChecklist.addEventListener("mouseover", function (e) {
          var row = e.target.closest && e.target.closest(".lock-check-row");
          var key = row && row.dataset.lockKey;
          if (key) self._highlightCanvasNodesByKey(key);
        });
        lockChecklist.addEventListener("mouseleave", function () {
          self._clearCanvasHighlights();
        });
        lockChecklist.addEventListener("click", function (e) {
          var row = e.target.closest && e.target.closest(".lock-check-row");
          var key = row && row.dataset.lockKey;
          if (key) self._scrollCanvasToNodeByKey(key);
        });
        /* Touch: tap row → highlight + scroll canvas (mouseover does not fire on touch) */
        lockChecklist.addEventListener("touchstart", function (e) {
          var row = e.target.closest && e.target.closest(".lock-check-row");
          var key = row && row.dataset.lockKey;
          if (key) {
            self._highlightCanvasNodesByKey(key);
            self._scrollCanvasToNodeByKey(key);
          }
        }, { passive: true });
      }

      // Click the "Locks: N" counter → open the panel and flash locked nodes
      var lockCount = $("generate-lock-count");
      if (lockCount) {
        lockCount.style.cursor = "pointer";
        lockCount.addEventListener("click", function () {
          var panel = $("generate-locks-panel");
          if (panel && !panel.open) panel.open = true;
          self._flashAllLockedNodes();
        });
      }

      var modelSel = $("generate-model-select");
      if (modelSel) {
        try {
          var saved = localStorage.getItem("assure_compile_model");
          if (saved) modelSel.value = saved;
        } catch (_) {}
        modelSel.addEventListener("change", function () {
          try {
            localStorage.setItem("assure_compile_model", modelSel.value);
          } catch (_) {}
        });
      }
      if (global.AssurePromptHistory && typeof global.AssurePromptHistory.bind === "function") {
        global.AssurePromptHistory.bind();
      }
    },

    compileFromIntent: function () {
      this.startDraftStream();
    },

    getCompilePayload: function () {
      var intentEl = $("generate-intent");
      var fullIntent = (intentEl && intentEl.value.trim()) || "";
      var selected = "";
      if (global.AssureTiptapEditor && typeof global.AssureTiptapEditor.getSelectedTextRange === "function") {
        var range = global.AssureTiptapEditor.getSelectedTextRange();
        if (range && range.text && range.text.trim()) {
          selected = range.text.trim();
        }
      }
      if (!selected && intentEl && typeof intentEl.selectionStart === "number" && intentEl.selectionStart !== intentEl.selectionEnd) {
        selected = intentEl.value.slice(intentEl.selectionStart, intentEl.selectionEnd).trim();
      }
      if (selected) {
        return { compileType: "selection", content: selected, intent: fullIntent || selected };
      }
      return { compileType: "full", content: fullIntent, intent: fullIntent };
    },

    abort: function () {
      this.clearVerifyTimeout();
      if (this._streamRetryTimer) {
        clearTimeout(this._streamRetryTimer);
        this._streamRetryTimer = null;
      }
      if (this.controller) {
        this.controller.abort();
        this.controller = null;
      }
      if (this.redhatController) {
        this.redhatController.abort();
        this.redhatController = null;
      }
      if (global.AssureUnsaved) global.AssureUnsaved.setGenerating(false);
    },

    /** Retired — draft now renders in the canvas; no left-pane panel to expand. */
    bindDraftPanel: function () {},
    setDraftExpanded: function () {},
    setDraftFullscreen: function () {},

    setGateLoading: function (on, message) {
      var loader = $("gate-loader");
      var statusText = $("gate-status-text");
      var dockBtn = $("generate-accept-dock");
      if (loader) loader.hidden = !on;
      if (statusText && message) statusText.textContent = message;
      if (dockBtn && on) {
        dockBtn.disabled = true;
        dockBtn.hidden = false;
      }
    },

    /** Background progress indicator that does NOT touch dock enablement —
     * used once "verified" has already unblocked docking, so the still-
     * running Stress Test cannot silently re-disable the dock button. */
    setBackgroundStatus: function (on, message) {
      var loader = $("gate-loader");
      var statusText = $("gate-status-text");
      if (loader) loader.hidden = !on;
      if (statusText && message) statusText.textContent = message;
    },

    setCompiling: function (on) {
      var el = $("generate-compiling");
      if (el) el.hidden = !on;
      var streamWrap = $("generate-stream-wrap");
      if (streamWrap) streamWrap.hidden = !on;
      var btn = $("generate-compile-btn");
      if (btn) btn.disabled = !!on;
      var auditBtn = $("generate-full-audit-btn");
      if (auditBtn) auditBtn.disabled = !!on;
      if (on) {
        if (global.AssureFirstCompileCoachmark && typeof global.AssureFirstCompileCoachmark.dismiss === "function") {
          global.AssureFirstCompileCoachmark.dismiss();
        }
        if (typeof global.updateCompilerStatus === "function") {
          global.updateCompilerStatus("processing");
        }
      } else if (!this.auditComplete) {
        if (typeof global.updateCompilerStatus === "function") {
          global.updateCompilerStatus("idle");
        }
      }
    },

    setPreviewSkeleton: function () {
      /* Left-pane skeleton removed — canvas skeleton only on first load */
    },

    setSummaryVisible: function (on) {
      var el = $("compilation-summary");
      if (el) el.hidden = !on;
      var controls = $("generate-draft-controls");
      if (controls) controls.hidden = !on;
    },

    resetUi: function () {
      this.clearVerifyTimeout();
      this.compiledNodes = [];
      this.compiledLocks = [];
      this.draftText = "";
      this.auditComplete = false;
      this.compiledDocument = null;
      this._docked = false;
      this._redhatPending = false;
      this._redhatCtx = null;
      this._auditManifest = null;
      this.hideRedhatPrompt();
      this.hideAuditAppendix();
      global.compiledDraftNodes = [];
      global.compiledLocks = [];
      global.compiledDocument = null;

      var preview = $("generate-stream-preview");
      if (preview) {
        preview.textContent = "";
        preview.classList.remove("is-streaming-skeleton");
      }
      var strip = $("draft-preview-strip");
      if (strip) strip.hidden = true;
      var previewDockBtn = $("draft-preview-dock-btn");
      if (previewDockBtn) previewDockBtn.disabled = true;
      var jdfCanvas = global.__assureJdf;
      if (jdfCanvas && typeof jdfCanvas.clearDraftPreview === "function") {
        jdfCanvas.clearDraftPreview();
      }

      this.setCompiling(false);
      this.setSummaryVisible(false);
      this.setGateLoading(false);

      var gateBanner = $("preflight-gate-banner");
      if (gateBanner) gateBanner.hidden = true;

      var z3 = $("z3-status");
      if (z3) {
        z3.hidden = true;
        z3.textContent = "";
        z3.className = "gate-z3-status verification-badge";
      }
      var redhat = $("redhat-preview");
      if (redhat) {
        redhat.hidden = true;
        redhat.innerHTML = "";
      }
      var list = $("generate-lock-checklist");
      if (list) list.innerHTML = "";
      var dockBtn = $("generate-accept-dock");
      if (dockBtn) {
        dockBtn.disabled = true;
        dockBtn.hidden = false;
      }
    },

    startDraftStream: function (isRetry, opts) {
      var self = this;
      opts = opts || {};
      var payload = this.getCompilePayload();
      var intent = payload.intent;
      if (!intent) {
        if (global.AssureToast) {
          global.AssureToast.show(t("generate.intent_required", "Tell us what you want to write first."), "error");
        }
        return;
      }
      if (Object.prototype.hasOwnProperty.call(opts, "fullAudit")) {
        this.fullAudit = !!opts.fullAudit;
      } else if (!isRetry) {
        this.fullAudit = false;
      }

      if (!isRetry && global.AssureSessionLimit && !global.AssureSessionLimit.tryConsume()) {
        if (global.AssureToast) {
          global.AssureToast.show(
            t("safeguard.session.limit_reached", "Session Limit Reached"),
            "info"
          );
        }
        return;
      }

      if (global.AssureUnsaved) {
        global.AssureUnsaved.clearDraft();
        global.AssureUnsaved.setInputDirty(false);
      }

      if (!isRetry) {
        this._streamRetryCount = 0;
      }

      this.abort();
      if (!isRetry) {
        var keepFullAudit = this.fullAudit;
        this.resetUi();
        this.fullAudit = keepFullAudit;
      }
      this.setCompiling(true);
      this.setPreviewSkeleton(true);
      this.controller = new AbortController();
      if (global.AssurePromptHistory && typeof global.AssurePromptHistory.push === "function") {
        var modelSel = $("generate-model-select");
        global.AssurePromptHistory.push(
          payload.compileType === "selection" ? payload.content : intent,
          {
            model: modelSel ? modelSel.value : "",
            cycle: String(Date.now()),
          }
        );
      }
      if (global.AssureUnsaved) global.AssureUnsaved.setGenerating(true);
      if (global.AssureStreamRegistry) {
        global.AssureStreamRegistry.register(this.controller);
      }
      if (typeof global.updateCompilerStatus === "function") {
        global.updateCompilerStatus("processing");
      }
      if (global.__assureJdf && typeof global.__assureJdf.setStressTestStatus === "function") {
        global.__assureJdf.setStressTestStatus(0);
      }

      var preview = $("generate-stream-preview");
      var url = "/api/projects/" + encodeURIComponent(projectId()) + "/draft/stream";
      var postStream =
        global.AssureSse && typeof global.AssureSse.postStream === "function"
          ? global.AssureSse.postStream
          : null;
      var modelEl = $("generate-model-select");
      var lockOn = !($("generate-lock-toggle") && !$("generate-lock-toggle").checked);
      var substrateIds =
        global.AssureSubstrateVault && typeof global.AssureSubstrateVault.selectedIncludedIds === "function"
          ? global.AssureSubstrateVault.selectedIncludedIds()
          : [];
      var requestBody = {
        intent: payload.intent,
        compileType: payload.compileType,
        compile_type: payload.compileType,
        content: payload.content,
        target_ai: modelEl ? modelEl.value : undefined,
        lock_numbers: lockOn,
        substrate_file_ids: substrateIds,
      };

      function handleFrame(frame) {
        var type = eventType(frame);
        var data = frame.data || {};

        if (type === "token" && data.delta) {
          self.draftText += data.delta;
          if (preview) {
            preview.classList.remove("is-streaming-skeleton");
            preview.textContent = self.draftText;
          }
          return;
        }

        if (type === "status") {
          if (data.stage === "model" || data.stage === "preflight") return;
          if (data.message && !self.auditComplete) {
            self.setGateLoading(true, data.message);
          }
          return;
        }

        if (type === "compiled") {
          self.setCompiling(false);
          self.setPreviewSkeleton(false);
          var streamWrap = $("generate-stream-wrap");
          var preview = $("generate-stream-preview");
          if (streamWrap) streamWrap.hidden = true;
          if (preview) preview.setAttribute("aria-live", "off");
          var locksPanel = $("generate-locks-panel");
          if (locksPanel && locksPanel.hasAttribute("open")) {
            locksPanel.removeAttribute("open");
          }
          self.compiledNodes = data.nodes || (data.document && data.document.body) || [];
          self.compiledLocks = data.locks || [];
          self.compiledDocument = data.document || null;
          if (data.cache_hit && self.compiledDocument) {
            self.compiledDocument.meta = self.compiledDocument.meta || {};
            self.compiledDocument.meta.cache_hit = true;
            (self.compiledDocument.body || []).forEach(function (sec) {
              sec.meta = Object.assign({}, sec.meta || {}, { cache_hit: true });
              (sec.children || []).forEach(function (node) {
                node.meta = Object.assign({}, node.meta || {}, { cache_hit: true });
              });
            });
            if (global.AssureCompilerStatus && typeof global.AssureCompilerStatus.flashCacheHit === "function") {
              global.AssureCompilerStatus.flashCacheHit();
            }
          }
          self.draftText = data.draft_text || self.draftText;
          global.compiledDraftNodes = self.compiledNodes;
          global.compiledLocks = self.compiledLocks;
          global.compiledDocument = self.compiledDocument;
          self.renderDraftNodes(self.compiledNodes);
          self.renderLockChecklist();
          self.renderSummaryCounts(data);
          if (global.AssurePromptHistory && typeof global.AssurePromptHistory.markLatest === "function") {
            global.AssurePromptHistory.markLatest({
              status: "ok",
              nodes: (self.compiledNodes || []).length,
            });
          }
          self.setSummaryVisible(true);
          var gateBanner = $("preflight-gate-banner");
          if (gateBanner) gateBanner.hidden = false;
          self.setGateLoading(
            true,
            t("audit.progress.z3", "Running math check…")
          );
          self.startVerifyTimeout();
          if (self.fullAudit) {
            self.showAuditAppendixPending();
          }
          return;
        }

        if (type === "verified") {
          // Hybrid compile gate: Z3 passed (or was skipped) — unblock
          // docking now. The Stress Test (Red-Hat) is opt-in: ask the user
          // whether to continue with it rather than auto-running it.
          self.clearVerifyTimeout();
          self.auditComplete = true;
          if (data.document) {
            self.compiledDocument = data.document;
            global.compiledDocument = data.document;
            if (global.AssureProjectFileManager && typeof global.AssureProjectFileManager.saveCompiled === "function") {
              global.AssureProjectFileManager.saveCompiled(data.document).catch(function () {});
            }
          }
          var confidenceSpans =
            data.confidenceSpans ||
            data.confidence_spans ||
            (data.document && data.document.meta && data.document.meta.confidenceSpans) ||
            [];
          if (global.__assureJdf && typeof global.__assureJdf.setConfidenceSpans === "function") {
            global.__assureJdf.setConfidenceSpans(confidenceSpans);
          }
          self.renderAuditGate(data);
          var verifiedDockBtn = $("generate-accept-dock");
          if (verifiedDockBtn) {
            verifiedDockBtn.disabled = false;
            verifiedDockBtn.hidden = false;
          }
          var previewDockBtn = $("draft-preview-dock-btn");
          if (previewDockBtn) previewDockBtn.disabled = false;
          var z3s = ((data.z3_results || {}).z3_status || "UNKNOWN");
          var gutterVerified = z3s === "VIOLATION" ? "error" : "verified";
          if (global.__assureJdf && typeof global.__assureJdf.setAllGutterState === "function") {
            global.__assureJdf.setAllGutterState(gutterVerified);
          }
          self.setGateLoading(false);
          self._redhatCtx = {
            draftText: self.draftText,
            document: data.document || self.compiledDocument,
            z3Results: data.z3_results || null,
          };
          if (global.AssureCompilerStatus) {
            global.AssureCompilerStatus.setLastAction(
              t("generate.draft_ready", "✅ Draft ready!")
            );
          }
          if (global.AssureToast) {
            global.AssureToast.show(t("generate.draft_ready", "✅ Draft ready!"), "success");
          }
          self.mergeAuditManifest(data);
          if (self.fullAudit) {
            self.hideRedhatPrompt();
            self._redhatCtx = {
              draftText: self.draftText,
              document: data.document || self.compiledDocument,
              z3Results: data.z3_results || null,
            };
            self.runRedhatStress({ parallel: true });
          } else {
            self.showRedhatPrompt();
          }
          return;
        }

        if (type === "error" || (type === "complete" && data.ok === false)) {
          self.setCompiling(false);
          self.setPreviewSkeleton(false);
          self.setGateLoading(false);
          if (global.AssureUnsaved) global.AssureUnsaved.setGenerating(false);
          if (global.AssureToast) {
            global.AssureToast.show(String(data.error || t("generate.failed", "Compilation failed.")), "error");
          }
          if (global.AssurePromptHistory && typeof global.AssurePromptHistory.markLatest === "function") {
            global.AssurePromptHistory.markLatest({ status: "fail" });
          }
          return;
        }

        if (type === "done" || type === "complete") {
          self.setCompiling(false);
          self.setPreviewSkeleton(false);
          if (global.AssureUnsaved) global.AssureUnsaved.setGenerating(false);
          if (!self.auditComplete) {
            self.setGateLoading(false);
            var dock = $("generate-accept-dock");
            if (dock && self.compiledNodes.length) dock.disabled = false;
          }
        }
      }

      var streamPromise;
      if (postStream) {
        streamPromise = postStream({
          url: url,
          body: requestBody,
          credentials: "same-origin",
          signal: self.controller.signal,
          idleTimeoutMs: SSE_IDLE_MS,
          parseBuffer: global.parseSseBuffer || parseSseBuffer,
          onFrame: handleFrame,
        });
      } else {
        streamPromise = fetch(url, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(requestBody),
          signal: self.controller.signal,
        }).then(function (res) {
          if (!res.ok || !res.body) throw new Error("Stream failed (" + res.status + ")");
          var reader = res.body.getReader();
          var decoder = new TextDecoder();
          var buffer = "";

          function pump() {
            return reader.read().then(function (result) {
              if (result.done) return;
              buffer += decoder.decode(result.value, { stream: true });
              var parsed = parseSseBuffer(buffer);
              buffer = parsed.remainder;
              parsed.events.forEach(handleFrame);
              return pump();
            });
          }
          return pump();
        });
      }

      streamPromise
        .catch(function (err) {
          self.setCompiling(false);
          self.setPreviewSkeleton(false);
          self.setGateLoading(false);
          if (global.AssureUnsaved) global.AssureUnsaved.setGenerating(false);
          if (err && err.name === "AbortError") return;
          if (!self.auditComplete && self._streamRetryCount < 1) {
            self._streamRetryCount += 1;
            if (global.AssureToast) {
              global.AssureToast.show(
                t("stream.reconnect", "Connection dropped — retrying…"),
                "info"
              );
            }
            self._streamRetryTimer = window.setTimeout(function () {
              self._streamRetryTimer = null;
              self.startDraftStream(true);
            }, 800);
            return;
          }
          if (global.AssureToast) {
            global.AssureToast.show(String(err.message || err), "error");
          }
        });
    },

    compileFromIntent: function () {
      this.startDraftStream();
    },

    renderDraftNodes: function (nodes) {
      var self = this;
      var jdf = global.__assureJdf;
      if (!jdf || typeof jdf.setDraftPreview !== "function") return;
      var previewDoc = {
        document_id: "draft-preview",
        meta: {
          title: t("generate.draft_preview_title", "Draft Preview"),
          cache_hit: !!(this.compiledDocument && this.compiledDocument.meta && this.compiledDocument.meta.cache_hit),
        },
        truth_ledger: (this.compiledDocument && this.compiledDocument.truth_ledger) || {},
        body: (nodes || []).map(function (sec) {
          var copy = JSON.parse(JSON.stringify(sec));
          if (self.compiledDocument && self.compiledDocument.meta && self.compiledDocument.meta.cache_hit) {
            copy.meta = Object.assign({}, copy.meta || {}, { cache_hit: true });
            (copy.children || []).forEach(function (child) {
              child.meta = Object.assign({}, child.meta || {}, { cache_hit: true });
            });
          }
          return copy;
        }),
      };
      jdf.setDraftPreview(previewDoc);
      if (typeof jdf.setAllGutterState === "function") jdf.setAllGutterState("verifying");
      var strip = $("draft-preview-strip");
      if (strip) strip.hidden = false;
      var previewDockBtn = $("draft-preview-dock-btn");
      if (previewDockBtn) previewDockBtn.disabled = true;
    },

    renderSummaryCounts: function (data) {
      var nodeCount = data.node_count != null ? data.node_count : (this.compiledNodes || []).length;
      var lockCount = data.lock_count != null ? data.lock_count : (this.compiledLocks || []).length;
      var nc = $("generate-node-count");
      var lc = $("generate-lock-count");
      if (nc) nc.textContent = String(nodeCount);
      if (lc) lc.textContent = String(lockCount);
    },

    renderLockChecklist: function () {
      var list = $("generate-lock-checklist");
      if (!list) return;
      list.innerHTML = "";
      if (!this.compiledLocks.length) {
        list.innerHTML =
          "<p class=\"hint\">" + escapeHtml(t("generate.locks_none", "No high-confidence locks inferred.")) + "</p>";
        return;
      }
      this.compiledLocks.forEach(function (item, idx) {
        var row = document.createElement("label");
        row.className = "lock-check-row";
        var key = item.canonical_key || item.metric || "";
        if (key) row.dataset.lockKey = key;
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = true;
        cb.dataset.lockIdx = String(idx);
        var label = document.createElement("span");
        label.textContent =
          (item.entity || "Metric") +
          " · " +
          (item.metric || "") +
          " = " +
          (item.value != null ? item.value : "") +
          (item.unit ? " " + item.unit : "") +
          " (" +
          Math.round((item.confidence || 0) * 100) +
          "%)";
        row.appendChild(cb);
        row.appendChild(label);
        list.appendChild(row);
      });
    },

    renderAuditGate: function (data) {
      if (global.AssureAuditGate) {
        global.AssureAuditGate.renderWorkbenchAudit(data, {
          z3El: $("z3-status"),
          redhatEl: $("redhat-preview"),
          gateBanner: $("preflight-gate-banner"),
          gateText: $("preflight-gate-text"),
        });
        return;
      }
      var z3 = data.z3_results || {};
      var z3El = $("z3-status");
      var jdf = global.__assureJdf;
      if (z3El) {
        var status = z3.z3_status || z3.status || "UNKNOWN";
        z3El.hidden = false;
        z3El.className = "gate-z3-status verification-badge " + (status === "PASS" ? "is-pass" : status === "VIOLATION" ? "is-fail" : "");
        if (status === "PASS") {
          z3El.textContent = t("generate.z3.pass", "Z3 verification passed.") +
            (z3.locks_verified ? " (" + z3.locks_verified + " locks)" : "");
          if (typeof global.updateCompilerStatus === "function") {
            global.updateCompilerStatus("verified");
          } else if (jdf && typeof jdf.setTruthBadge === "function") {
            jdf.setTruthBadge("PASS");
          }
        } else if (status === "VIOLATION") {
          var viol = (z3.violations || []).join(" ");
          z3El.textContent = t("generate.z3.fail", "Z3 found contradictions.") + (viol ? " " + viol : "");
          if (typeof global.updateCompilerStatus === "function") {
            global.updateCompilerStatus("issues");
          } else if (jdf && typeof jdf.setTruthBadge === "function") {
            jdf.setTruthBadge("FAIL");
          }
        } else {
          z3El.textContent = t("generate.z3.skipped", "Z3 verification skipped.");
        }
      }

      var critiques = data.redhat_critiques || [];
      var redhatEl = $("redhat-preview");
      if (redhatEl) {
        redhatEl.innerHTML = "";
        if (critiques.length) {
          redhatEl.hidden = false;
          if (typeof global.updateCompilerStatus === "function") {
            global.updateCompilerStatus("issues");
          } else if (jdf && typeof jdf.setStressTestStatus === "function") {
            jdf.setStressTestStatus(critiques.length);
          }
          critiques.forEach(function (c) {
            var li = document.createElement("li");
            var title = document.createElement("div");
            title.className = "redhat-preview-title";
            title.textContent = c.title || t("jdf.redhat.findings", "Stress Test Alert");
            var body = document.createElement("div");
            body.textContent = c.content || "";
            li.appendChild(title);
            li.appendChild(body);
            redhatEl.appendChild(li);
          });
        } else {
          redhatEl.hidden = true;
          if (jdf && typeof jdf.setStressTestStatus === "function") {
            jdf.setStressTestStatus(0);
          }
        }
      }
    },

    acceptAndDock: function () {
      var self = this;
      if (!this.auditComplete) {
        if (global.AssureToast) {
          global.AssureToast.show(t("generate.gate.wait", "Wait for audit to complete before docking."), "info");
        }
        return;
      }
      if (!this.compiledNodes.length) {
        if (global.AssureToast) {
          global.AssureToast.show(t("generate.no_nodes", "Nothing to dock yet."), "error");
        }
        return;
      }

      var acceptedLocks = [];
      var list = $("generate-lock-checklist");
      if (list) {
        list.querySelectorAll("input[type=checkbox]").forEach(function (cb) {
          if (!cb.checked) return;
          var idx = parseInt(cb.dataset.lockIdx || "-1", 10);
          if (idx >= 0 && self.compiledLocks[idx]) acceptedLocks.push(self.compiledLocks[idx]);
        });
      }

      var jdf = global.__assureJdf;
      if (!jdf || !jdf.tree) {
        if (global.AssureToast) {
          global.AssureToast.show(t("generate.jdf_missing", "Canvas not ready."), "error");
        }
        return;
      }

      var dockBtn = $("generate-accept-dock");
      if (dockBtn) dockBtn.disabled = true;
      var previewDockBtn = $("draft-preview-dock-btn");
      if (previewDockBtn) previewDockBtn.disabled = true;
      var strip = $("draft-preview-strip");
      if (strip) strip.hidden = true;
      if (typeof jdf.clearDraftPreview === "function") jdf.clearDraftPreview();

      var cycleId = "cycle-" + Date.now().toString(36);
      function stampCycle(sections) {
        return (sections || []).map(function (sec) {
          var copy = JSON.parse(JSON.stringify(sec));
          copy.meta = Object.assign({}, copy.meta || {}, { prompt_cycle: cycleId });
          if (self.compiledDocument && self.compiledDocument.meta && self.compiledDocument.meta.cache_hit) {
            copy.meta.cache_hit = true;
            (copy.children || []).forEach(function (child) {
              child.meta = Object.assign({}, child.meta || {}, { cache_hit: true });
            });
          }
          return copy;
        });
      }
      var lockOn = !($("generate-lock-toggle") && !$("generate-lock-toggle").checked);

      if (self.compiledDocument && self.compiledDocument.body) {
        var base = JSON.parse(JSON.stringify(jdf.tree));
        base.body = (base.body || []).concat(stampCycle(self.compiledDocument.body || []));
        if (lockOn) {
          base.truth_ledger = Object.assign(
            {},
            base.truth_ledger || {},
            self.compiledDocument.truth_ledger || {}
          );
        }
        fetch("/api/projects/" + encodeURIComponent(projectId()) + "/jdf", {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            document:
              typeof global.sanitizeJDFDocument === "function"
                ? global.sanitizeJDFDocument(base)
                : base,
            mutation_type: "GENERATE_DOCK",
            change_summary: "Generate: docked document tree",
          }),
        })
          .then(function (res) {
            return res.json().then(function (data) {
              return { ok: res.ok, data: data };
            });
          })
        .then(function (result) {
          if (!result.ok) throw new Error((result.data && result.data.error) || "Save failed");
          if (result.data.document) jdf.tree = result.data.document;
          self._docked = true;
          if (typeof jdf.render === "function") jdf.render();
          if (
            global.AssureTiptapEditor &&
            typeof global.AssureTiptapEditor.applyConfidenceToTipTap === "function"
          ) {
            setTimeout(function () {
              global.AssureTiptapEditor.applyConfidenceToTipTap();
            }, 150);
          }
          if (result.data.version && typeof jdf.setVersion === "function") {
            jdf.setVersion(result.data.version);
          }
          if (typeof jdf.setSavePill === "function") {
            jdf.setSavePill("saved", "jdf.status.committed", {
              version: result.data.version || jdf.documentVersion,
            });
          }
          if (global.AssureUnsaved) global.AssureUnsaved.clearUnsaved();
          if (global.AssureToast) {
            global.AssureToast.show(t("generate.docked", "Nodes docked to canvas."), "success");
          }
          document.dispatchEvent(new CustomEvent("assure:docked", { detail: { cycle: cycleId } }));
          if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
            global.AssureNav.switchView("surgical");
          }
        })
        .catch(function (err) {
          if (global.AssureToast) {
            global.AssureToast.show(String(err.message || err), "error");
          }
        })
        .finally(function () {
          if (dockBtn) dockBtn.disabled = false;
        });
        return;
      }

      var doc = JSON.parse(JSON.stringify(jdf.tree));
      doc.body = (doc.body || []).concat(stampCycle(this.compiledNodes));
      var ledger = doc.truth_ledger || {};
      if (lockOn) {
        acceptedLocks.forEach(function (lock) {
          var key = lock.canonical_key || lock.metric;
          if (key && lock.value != null) ledger[key] = Number(lock.value);
        });
      }
      doc.truth_ledger = ledger;

      var dockBtn = $("generate-accept-dock");
      if (dockBtn) dockBtn.disabled = true;

      fetch("/api/projects/" + encodeURIComponent(projectId()) + "/jdf", {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          document: doc,
          mutation_type: "GENERATE_DOCK",
          change_summary: "Generate: docked " + self.compiledNodes.length + " section(s)",
        }),
      })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok, data: data };
          });
        })
        .then(function (result) {
          if (!result.ok) throw new Error((result.data && result.data.error) || "Save failed");
          if (result.data.document) jdf.tree = result.data.document;
          self._docked = true;
          if (typeof jdf.render === "function") jdf.render();
          if (result.data.version && typeof jdf.setVersion === "function") {
            jdf.setVersion(result.data.version);
          }
          if (typeof jdf.setSavePill === "function") {
            jdf.setSavePill("saved", "jdf.status.committed", {
              version: result.data.version || jdf.documentVersion,
            });
          }
          if (global.AssureUnsaved) global.AssureUnsaved.clearUnsaved();
          if (global.AssureToast) {
            global.AssureToast.show(t("generate.docked", "Nodes docked to canvas."), "success");
          }
          document.dispatchEvent(new CustomEvent("assure:docked", {}));
          if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
            global.AssureNav.switchView("surgical");
          }
        })
        .catch(function (err) {
          if (global.AssureToast) {
            global.AssureToast.show(String(err.message || err), "error");
          }
        })
        .finally(function () {
          if (dockBtn) dockBtn.disabled = false;
        });
    },

    /** Highlight all canvas .jdf-node elements that contain a lock glyph
     * matching the given ledger key. */
    _highlightCanvasNodesByKey: function (key) {
      this._clearCanvasHighlights();
      if (!key) return;
      var canvas = document.getElementById("jdf-render-target");
      if (!canvas) return;
      canvas.querySelectorAll('[data-lock-key="' + key + '"]').forEach(function (el) {
        var node = el.closest(".jdf-node");
        if (node) node.classList.add("cross-highlight");
      });
    },

    _clearCanvasHighlights: function () {
      document.querySelectorAll(".jdf-node.cross-highlight").forEach(function (el) {
        el.classList.remove("cross-highlight");
      });
    },

    /** Scroll the canvas to the first node containing the given lock key
     * and briefly flash it. */
    _scrollCanvasToNodeByKey: function (key) {
      if (!key) return;
      var canvas = document.getElementById("jdf-render-target");
      if (!canvas) return;
      var el = canvas.querySelector('[data-lock-key="' + key + '"]');
      if (!el) return;
      var node = el.closest(".jdf-node");
      if (!node) return;
      node.scrollIntoView({ behavior: "smooth", block: "center" });
      node.classList.add("cross-flash");
      setTimeout(function () { node.classList.remove("cross-flash"); }, 800);
    },

    /** Flash all canvas nodes that carry any lock glyph. */
    _flashAllLockedNodes: function () {
      var canvas = document.getElementById("jdf-render-target");
      if (!canvas) return;
      var seen = [];
      canvas.querySelectorAll("[data-lock-key]").forEach(function (el) {
        var node = el.closest(".jdf-node");
        if (node && seen.indexOf(node) < 0) {
          seen.push(node);
          node.classList.add("cross-flash");
          setTimeout(function () { node.classList.remove("cross-flash"); }, 700);
        }
      });
    },

    hideAuditAppendix: function () {
      var el = $("jdf-audit-appendix");
      var body = $("jdf-audit-appendix-body");
      if (el) el.hidden = true;
      if (body) body.innerHTML = "";
      if (global.__assureJdf && typeof global.__assureJdf.renderAuditAppendix === "function") {
        global.__assureJdf.renderAuditAppendix(null);
      }
    },

    showAuditAppendixPending: function () {
      var claims = (this._auditManifest && this._auditManifest.claims) || [];
      this.renderAuditManifest(claims, { pending: true });
    },

    mergeAuditManifest: function (data) {
      var incoming =
        (data && (data.audit_manifest || data.claims || data.auditManifest)) || [];
      var existing = (this._auditManifest && this._auditManifest.claims) || [];
      var byNode = {};
      existing.forEach(function (row) {
        var nid = row.nodeId || row.node_id || "";
        if (!byNode[nid]) byNode[nid] = [];
        byNode[nid].push(row);
      });
      var z3 = (data && data.z3_results) || {};
      var z3Live = String(z3.status || (data && data.z3_status) || "").toUpperCase();
      var z3Trusted = z3Live === "PASS" || z3Live === "VIOLATION";
      var merged;
      if (incoming.length) {
        merged = incoming.map(function (row, idx) {
          var nid = row.nodeId || row.node_id || "";
          var prev = (byNode[nid] && byNode[nid][0]) || existing[idx] || {};
          var score = row.z3Score != null ? row.z3Score : row.z3_score;
          var prevScore = prev.z3Score != null ? prev.z3Score : prev.z3_score;
          if (!z3Trusted && prevScore != null) score = prevScore;
          else if (score == null) score = prevScore;
          var critique = row.redhatCritique || row.redhat_critique || "";
          var prevCrit = prev.redhatCritique || prev.redhat_critique || "";
          return {
            claim: row.claim || prev.claim || "",
            nodeId: nid,
            z3Score: score,
            redhatCritique: critique || prevCrit,
          };
        });
      } else {
        merged = existing.slice();
      }
      this._auditManifest = { claims: merged };
      this.renderAuditManifest(merged, { pending: this.fullAudit && this._redhatPending });
    },

    renderAuditManifest: function (claims, opts) {
      opts = opts || {};
      var hasRh = (claims || []).some(function (row) {
        return !!(row.redhatCritique || row.redhat_critique);
      });
      if (!this.fullAudit && !hasRh && !opts.pending) {
        this.hideAuditAppendix();
        return;
      }
      if (global.__assureJdf && typeof global.__assureJdf.renderAuditAppendix === "function") {
        global.__assureJdf.renderAuditAppendix(claims || [], opts);
        return;
      }
      var wrap = $("jdf-audit-appendix");
      var body = $("jdf-audit-appendix-body");
      if (!wrap || !body) return;
      wrap.hidden = false;
      body.textContent = "";
    },

    showRedhatPrompt: function () {
      var el = $("redhat-prompt");
      var textEl = $("redhat-prompt-text");
      var skipBtn = $("redhat-skip-btn");
      if (textEl) {
        textEl.textContent = t(
          "generate.redhat_prompt",
          "Math Check passed. Run Stress Test (adversarial review)?"
        );
      }
      if (skipBtn) skipBtn.hidden = false;
      if (el) el.hidden = false;
    },

    hideRedhatPrompt: function () {
      var el = $("redhat-prompt");
      if (el) el.hidden = true;
    },

    /** Shared handling for the "audit_complete" frame, whether it comes
     * from the (legacy) single-stream pipeline or the opt-in Stress Test
     * call — updates the gate, re-enables docking, and patches an
     * already-docked canvas by node id if docking already happened. */
    _handleAuditComplete: function (data) {
      var self = this;
      self._redhatPending = false;
      self.auditComplete = true;
      if (data.document) {
        self.compiledDocument = data.document;
        global.compiledDocument = data.document;
      }
      self.setBackgroundStatus(false);
      self.renderAuditGate(data);
      var dockBtn = $("generate-accept-dock");
      if (dockBtn) dockBtn.disabled = false;
      var critiques = data.redhat_critiques || [];
      if (critiques.length && global.__assureJdf && typeof global.__assureJdf.patchGutterFromRedhat === "function") {
        global.__assureJdf.patchGutterFromRedhat(critiques);
      }
      self.mergeAuditManifest(data);
      if (self._docked) {
        self.patchDockedRedhat(data.document);
      }
      if (global.__assureDemoRedhatPending) {
        document.dispatchEvent(new CustomEvent("assure:demo-redhat-audit"));
      }
    },

    /** Opt-in Stage 4, or Full Audit parallel pass. Runs over the compiled
     * document. Parallel mode must not abort the in-flight Z3 draft SSE. */
    finishRedhatStress: function (opts) {
      opts = opts || {};
      this._redhatPending = false;
      this.redhatController = null;
      if (this._redhatWallTimer) {
        clearTimeout(this._redhatWallTimer);
        this._redhatWallTimer = null;
      }
      this.setBackgroundStatus(false);
      if (opts.error && global.AssureToast) {
        global.AssureToast.show(String(opts.error), "error");
      }
      if (opts.fallbackManifest) {
        this.mergeAuditManifest(opts.fallbackManifest);
      } else if (this._auditManifest && this._auditManifest.claims) {
        this.renderAuditManifest(this._auditManifest.claims, { pending: false });
      }
    },

    runRedhatStress: function (opts) {
      var self = this;
      var ctx = this._redhatCtx;
      opts = opts || {};
      this.hideRedhatPrompt();
      if (!ctx || !ctx.document) {
        if (global.AssureToast) {
          global.AssureToast.show(t("generate.jdf_missing", "Canvas not ready."), "error");
        }
        return;
      }

      this.setBackgroundStatus(true, t("audit.progress.redhat", "Running stress test…"));
      this._redhatPending = true;

      if (this.redhatController) {
        try {
          this.redhatController.abort();
        } catch (_) {}
      }
      if (this._redhatWallTimer) {
        clearTimeout(this._redhatWallTimer);
        this._redhatWallTimer = null;
      }
      this.redhatController = new AbortController();
      this._redhatWallTimer = window.setTimeout(function () {
        if (!self._redhatPending) return;
        try {
          self.redhatController.abort();
        } catch (_) {}
        var msg = t("audit.timeout", "Verification timeout — click to retry");
        self.finishRedhatStress({
          error: msg,
          fallbackManifest: {
            claims: (self._auditManifest && self._auditManifest.claims) || [],
            audit_manifest: (self._auditManifest && self._auditManifest.claims) || [],
          },
        });
      }, REDHAT_WALL_MS);
      if (global.AssureStreamRegistry) {
        global.AssureStreamRegistry.register(this.redhatController, { parallel: !!opts.parallel || !!this.fullAudit });
      }

      var url = "/api/projects/" + encodeURIComponent(projectId()) + "/draft/redhat/stream";
      var body = {
        draft_text: ctx.draftText || self.draftText,
        document: ctx.document,
        z3_results: ctx.z3Results,
      };
      var postStream =
        global.AssureSse && typeof global.AssureSse.postStream === "function"
          ? global.AssureSse.postStream
          : null;
      var sawAuditComplete = false;

      function handleFrame(frame) {
        var type = eventType(frame);
        var data = frame.data || {};

        if (type === "status") {
          if (data.message) self.setBackgroundStatus(true, data.message);
          return;
        }
        if (type === "audit_complete") {
          sawAuditComplete = true;
          self.redhatController = null;
          if (self._redhatWallTimer) {
            clearTimeout(self._redhatWallTimer);
            self._redhatWallTimer = null;
          }
          self._handleAuditComplete(data);
          return;
        }
        if (type === "error" || (type === "complete" && data.ok === false)) {
          sawAuditComplete = true;
          self.finishRedhatStress({
            error: data.error || t("generate.failed", "Stress Test failed."),
          });
        }
      }

      function onStreamEnd() {
        if (sawAuditComplete || !self._redhatPending) return;
        self.finishRedhatStress({
          error: t("audit.timeout", "Verification timeout — click to retry"),
        });
      }

      var streamPromise;
      if (postStream) {
        streamPromise = postStream({
          url: url,
          body: body,
          credentials: "same-origin",
          signal: self.redhatController.signal,
          idleTimeoutMs: SSE_IDLE_MS,
          parseBuffer: global.parseSseBuffer || parseSseBuffer,
          onFrame: handleFrame,
          onDone: onStreamEnd,
        });
      } else {
        streamPromise = fetch(url, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: self.redhatController.signal,
        }).then(function (res) {
          if (!res.ok || !res.body) throw new Error("Stream failed (" + res.status + ")");
          var reader = res.body.getReader();
          var decoder = new TextDecoder();
          var buffer = "";

          function pump() {
            return reader.read().then(function (result) {
              if (result.done) {
                onStreamEnd();
                return;
              }
              buffer += decoder.decode(result.value, { stream: true });
              var parsed = parseSseBuffer(buffer);
              buffer = parsed.remainder;
              parsed.events.forEach(handleFrame);
              return pump();
            });
          }
          return pump();
        });
      }

      streamPromise.catch(function (err) {
        if (err && err.name === "AbortError") {
          if (!self._redhatPending) return;
        }
        self.finishRedhatStress({
          error: (err && err.message) || t("generate.failed", "Stress Test failed."),
        });
      });
    },

    /** User declined the Stress Test — leave the Math-Check-only document
     * as-is, but keep the "Run Stress Test" button live (just collapse the
     * Skip option) so it can genuinely still be triggered later, including
     * after docking — patchDockedRedhat() will attach findings in place. */
    skipRedhatStress: function () {
      var textEl = $("redhat-prompt-text");
      var skipBtn = $("redhat-skip-btn");
      if (textEl) {
        textEl.textContent = t(
          "generate.redhat_skipped_note",
          "Stress Test skipped. You can still run it later from the workbench."
        );
      }
      if (skipBtn) skipBtn.hidden = true;
    },

    /** Collect {nodeId: annotations} for every node/section carrying a
     * Red-Hat finding in a compiled (small) document tree. */
    _collectRedhatAnnotations: function (doc) {
      var map = {};
      (doc && doc.body ? doc.body : []).forEach(function (section) {
        if (section && section.id && section.annotations && (section.annotations.redhat || []).length) {
          map[section.id] = section.annotations;
        }
        (section && section.children ? section.children : []).forEach(function (node) {
          if (node && node.id && node.annotations && (node.annotations.redhat || []).length) {
            map[node.id] = node.annotations;
          }
        });
      });
      return map;
    },

    /** Stress Test findings can land after the document was already docked
     * (hybrid compile: dock happens on "verified", Red-Hat keeps running).
     * Patch the matching nodes already saved on the canvas in place, by id,
     * instead of dropping the late findings on the floor. */
    patchDockedRedhat: function (annotatedDocument) {
      var self = this;
      var jdf = global.__assureJdf;
      if (!jdf || !jdf.tree || !annotatedDocument) return;

      var redhatById = this._collectRedhatAnnotations(annotatedDocument);
      if (!Object.keys(redhatById).length) return;

      var patched = JSON.parse(JSON.stringify(jdf.tree));
      var touched = false;
      (patched.body || []).forEach(function (section) {
        if (redhatById[section.id]) {
          section.annotations = redhatById[section.id];
          touched = true;
        }
        (section.children || []).forEach(function (node) {
          if (redhatById[node.id]) {
            node.annotations = redhatById[node.id];
            touched = true;
          }
        });
      });
      if (!touched) return;

      fetch("/api/projects/" + encodeURIComponent(projectId()) + "/jdf", {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          document: patched,
          mutation_type: "GENERATE_REDHAT_ATTACH",
          change_summary: "Stress Test findings attached after docking",
        }),
      })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok, data: data };
          });
        })
        .then(function (result) {
          if (!result.ok) throw new Error((result.data && result.data.error) || "Save failed");
          if (result.data.document) jdf.tree = result.data.document;
          if (typeof jdf.render === "function") jdf.render();
          if (result.data.version && typeof jdf.setVersion === "function") {
            jdf.setVersion(result.data.version);
          }
          if (global.AssureToast) {
            global.AssureToast.show(
              t("generate.redhat_attached", "Stress Test findings added to the document."),
              "info"
            );
          }
        })
        .catch(function (err) {
          if (global.AssureToast) {
            global.AssureToast.show(String(err.message || err), "error");
          }
        });
    },
  };

  global.AssureGenerate = AssureGenerate;
  global.parseSseBuffer = parseSseBuffer;
  global.compiledDraftNodes = [];
  global.compiledLocks = [];

  global.startDraftStream = function () {
    if (global.AssureGenerate) global.AssureGenerate.startDraftStream();
  };

  var AssureFirstCompileCoachmark = {
    _dismissed: false,

    check: function (force) {
      if (this._dismissed && !force) return;
      var mark = $("first-compile-coachmark");
      if (!mark) return;
      var pid = projectId();
      fetch("/api/projects/" + encodeURIComponent(pid) + "/files", { credentials: "same-origin" })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          var manifest = (data && data.manifest) || {};
          var nodes = manifest.lastCompiledOutput || [];
          var empty = !nodes || !nodes.length;
          if (empty && !AssureFirstCompileCoachmark._dismissed) {
            mark.hidden = false;
          }
        })
        .catch(function () {});
    },

    dismiss: function () {
      this._dismissed = true;
      var mark = $("first-compile-coachmark");
      if (mark) mark.hidden = true;
    },

    init: function () {
      var dismissBtn = $("first-compile-coachmark-dismiss");
      if (dismissBtn) {
        dismissBtn.addEventListener("click", function () {
          AssureFirstCompileCoachmark.dismiss();
        });
      }
      this.check(false);
    },
  };

  global.AssureFirstCompileCoachmark = AssureFirstCompileCoachmark;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      AssureGenerate.init();
      AssureFirstCompileCoachmark.init();
    });
  } else {
    AssureGenerate.init();
    AssureFirstCompileCoachmark.init();
  }
})(window);
