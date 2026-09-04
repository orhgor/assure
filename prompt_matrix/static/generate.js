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
          self.startDraftStream();
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
          self.setDraftFullscreen(false);
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

      this.bindDraftPanel();

      document.addEventListener("assure:abort-streams", function () {
        self.abort();
      });
    },

    abort: function () {
      this.clearVerifyTimeout();
      if (this.controller) {
        this.controller.abort();
        this.controller = null;
      }
      if (global.AssureUnsaved) global.AssureUnsaved.setGenerating(false);
    },

    bindDraftPanel: function () {
      var self = this;
      if (this._draftPanelBound) return;
      this._draftPanelBound = true;
      document.addEventListener("click", function (e) {
        var expandBtn = e.target.closest && e.target.closest("#expand-draft-btn");
        var fullBtn = e.target.closest && e.target.closest("#fullscreen-draft-btn");
        if (!expandBtn && !fullBtn) return;
        e.preventDefault();
        var panel = $("generate-nodes-preview");
        if (expandBtn) {
          self.setDraftExpanded(!(panel && panel.classList.contains("is-expanded")));
        }
        if (fullBtn) {
          self.setDraftFullscreen(!(panel && panel.classList.contains("is-fullscreen")));
        }
      });
      document.addEventListener("keydown", function (e) {
        if (e.key !== "Escape") return;
        var panel = $("generate-nodes-preview");
        if (panel && panel.classList.contains("is-fullscreen")) {
          self.setDraftFullscreen(false);
        }
      });
      try {
        if (global.localStorage && global.localStorage.getItem("assure_draft_expanded") === "1") {
          this.setDraftExpanded(true);
        }
      } catch (_) {}
    },

    setDraftExpanded: function (on) {
      var panel = $("generate-nodes-preview");
      var body = $("generate-nodes-body");
      var btn = $("expand-draft-btn");
      if (panel) {
        panel.hidden = false;
        panel.classList.toggle("is-expanded", !!on);
      }
      if (body && on) body.style.height = "";
      if (btn) {
        var key = on ? "generate.collapse" : "generate.expand";
        var fallback = on ? "Collapse draft" : "Expand draft";
        btn.setAttribute("data-i18n", key);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
        btn.textContent = t(key, fallback);
      }
      try {
        if (global.localStorage) {
          global.localStorage.setItem("assure_draft_expanded", on ? "1" : "0");
        }
      } catch (_) {}
    },

    setDraftFullscreen: function (on) {
      var panel = $("generate-nodes-preview");
      var btn = $("fullscreen-draft-btn");
      if (!panel) return;
      panel.hidden = false;
      if (on) {
        if (!this._draftPanelParent) {
          this._draftPanelParent = panel.parentNode;
          this._draftPanelNext = panel.nextSibling;
        }
        if (panel.parentNode !== document.body) {
          document.body.appendChild(panel);
        }
        panel.classList.add("is-fullscreen");
      } else {
        panel.classList.remove("is-fullscreen");
        if (this._draftPanelParent && panel.parentNode !== this._draftPanelParent) {
          if (this._draftPanelNext && this._draftPanelNext.parentNode === this._draftPanelParent) {
            this._draftPanelParent.insertBefore(panel, this._draftPanelNext);
          } else {
            this._draftPanelParent.appendChild(panel);
          }
        }
      }
      if (btn) {
        var key = on ? "generate.fullscreen_exit" : "generate.fullscreen";
        var fallback = on ? "Exit full screen" : "Full screen";
        btn.setAttribute("data-i18n", key);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
        btn.textContent = t(key, fallback);
      }
      document.body.classList.toggle("draft-fullscreen-open", !!on);
    },

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
      if (on) {
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
      this.hideRedhatPrompt();
      global.compiledDraftNodes = [];
      global.compiledLocks = [];
      global.compiledDocument = null;

      var preview = $("generate-stream-preview");
      if (preview) {
        preview.textContent = "";
        preview.classList.remove("is-streaming-skeleton");
      }
      var nodesPreview = $("generate-nodes-preview");
      if (nodesPreview) nodesPreview.hidden = true;
      var nodesBody = $("generate-nodes-body");
      if (nodesBody) nodesBody.innerHTML = "";

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

    startDraftStream: function (isRetry) {
      var self = this;
      var intentEl = $("generate-intent");
      var intent = intentEl && intentEl.value.trim();
      if (!intent) {
        if (global.AssureToast) {
          global.AssureToast.show(t("generate.intent_required", "Describe what to compile first."), "error");
        }
        return;
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
        this.resetUi();
      }
      this.setCompiling(true);
      this.setPreviewSkeleton(true);
      this.controller = new AbortController();
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
          self.draftText = data.draft_text || self.draftText;
          global.compiledDraftNodes = self.compiledNodes;
          global.compiledLocks = self.compiledLocks;
          global.compiledDocument = self.compiledDocument;
          self.renderDraftNodes(self.compiledNodes);
          self.renderLockChecklist();
          self.renderSummaryCounts(data);
          self.setSummaryVisible(true);
          var gateBanner = $("preflight-gate-banner");
          if (gateBanner) gateBanner.hidden = false;
          self.setGateLoading(
            true,
            t("audit.progress.z3", "Running math check…")
          );
          self.startVerifyTimeout();
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
          }
          self.renderAuditGate(data);
          var verifiedDockBtn = $("generate-accept-dock");
          if (verifiedDockBtn) {
            verifiedDockBtn.disabled = false;
            verifiedDockBtn.hidden = false;
          }
          self.setGateLoading(false);
          self._redhatCtx = {
            draftText: self.draftText,
            document: data.document || self.compiledDocument,
            z3Results: data.z3_results || null,
          };
          self.showRedhatPrompt();
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
          body: { intent: intent },
          credentials: "same-origin",
          signal: self.controller.signal,
          parseBuffer: global.parseSseBuffer || parseSseBuffer,
          onFrame: handleFrame,
        });
      } else {
        streamPromise = fetch(url, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ intent: intent }),
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
            window.setTimeout(function () {
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
      var wrap = $("generate-nodes-preview");
      var body = $("generate-nodes-body");
      if (!wrap || !body) return;
      body.innerHTML = "";
      (nodes || []).forEach(function (section) {
        if (section.title) {
          var h = document.createElement("h4");
          h.textContent = section.title;
          body.appendChild(h);
        }
        (section.children || []).forEach(function (child) {
          if (child.type === "paragraph" && child.content) {
            var p = document.createElement("p");
            p.textContent = child.content;
            body.appendChild(p);
          }
        });
      });
      wrap.hidden = !body.childNodes.length;
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

      if (self.compiledDocument && self.compiledDocument.body) {
        var base = JSON.parse(JSON.stringify(jdf.tree));
        base.body = (base.body || []).concat(self.compiledDocument.body || []);
        base.truth_ledger = Object.assign(
          {},
          base.truth_ledger || {},
          self.compiledDocument.truth_ledger || {}
        );
        fetch("/api/projects/" + encodeURIComponent(projectId()) + "/jdf", {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            document: base,
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
      doc.body = (doc.body || []).concat(this.compiledNodes);
      var ledger = doc.truth_ledger || {};
      acceptedLocks.forEach(function (lock) {
        var key = lock.canonical_key || lock.metric;
        if (key && lock.value != null) ledger[key] = Number(lock.value);
      });
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
      if (self._docked) {
        self.patchDockedRedhat(data.document);
      }
      if (global.__assureDemoRedhatPending) {
        document.dispatchEvent(new CustomEvent("assure:demo-redhat-audit"));
      }
    },

    /** Opt-in Stage 4: only called when the user clicks "Run Stress Test"
     * on the hybrid gate prompt. Runs over the already Math-Check-verified
     * document from the "verified" event — never automatic. */
    runRedhatStress: function () {
      var self = this;
      var ctx = this._redhatCtx;
      this.hideRedhatPrompt();
      if (!ctx || !ctx.document) {
        if (global.AssureToast) {
          global.AssureToast.show(t("generate.jdf_missing", "Canvas not ready."), "error");
        }
        return;
      }

      this.setBackgroundStatus(true, t("audit.progress.redhat", "Running stress test…"));
      this._redhatPending = true;

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

      function handleFrame(frame) {
        var type = eventType(frame);
        var data = frame.data || {};

        if (type === "status") {
          if (data.message) self.setBackgroundStatus(true, data.message);
          return;
        }
        if (type === "audit_complete") {
          self._handleAuditComplete(data);
          return;
        }
        if (type === "error" || (type === "complete" && data.ok === false)) {
          self._redhatPending = false;
          self.setBackgroundStatus(false);
          if (global.AssureToast) {
            global.AssureToast.show(
              String(data.error || t("generate.failed", "Stress Test failed.")),
              "error"
            );
          }
        }
      }

      var streamPromise;
      if (postStream) {
        streamPromise = postStream({
          url: url,
          body: body,
          credentials: "same-origin",
          parseBuffer: global.parseSseBuffer || parseSseBuffer,
          onFrame: handleFrame,
        });
      } else {
        streamPromise = fetch(url, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
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

      streamPromise.catch(function (err) {
        self._redhatPending = false;
        self.setBackgroundStatus(false);
        if (global.AssureToast) {
          global.AssureToast.show(String((err && err.message) || err), "error");
        }
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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      AssureGenerate.init();
    });
  } else {
    AssureGenerate.init();
  }
})(window);
