(function (global) {
  "use strict";

  function computeWordDiff(oldStr, newStr) {
    var a = (oldStr || "").split(/\s+/).filter(Boolean);
    var b = (newStr || "").split(/\s+/).filter(Boolean);
    var n = a.length;
    var m = b.length;
    var dp = Array.from({ length: n + 1 }, function () {
      return new Array(m + 1).fill(0);
    });
    var i;
    var j;
    for (i = n - 1; i >= 0; i -= 1) {
      for (j = m - 1; j >= 0; j -= 1) {
        dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
    var out = [];
    i = 0;
    j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) {
        out.push({ t: "eq", w: a[i] });
        i += 1;
        j += 1;
      } else if (dp[i + 1][j] >= dp[i][j + 1]) {
        out.push({ t: "del", w: a[i] });
        i += 1;
      } else {
        out.push({ t: "ins", w: b[j] });
        j += 1;
      }
    }
    while (i < n) {
      out.push({ t: "del", w: a[i++] });
    }
    while (j < m) {
      out.push({ t: "ins", w: b[j++] });
    }
    return out;
  }

  function renderDiffHtml(tokens) {
    return tokens
      .map(function (tok) {
        if (tok.t === "ins") return '<ins class="diff-ins">' + tok.w + "</ins>";
        if (tok.t === "del") return '<del class="diff-del">' + tok.w + "</del>";
        return tok.w;
      })
      .join(" ");
  }

  function parseSseChunk(buffer) {
    var events = [];
    var parts = buffer.split("\n\n");
    var remainder = parts.pop() || "";
    parts.forEach(function (block) {
      if (!block.trim()) return;
      var eventName = "message";
      var dataLine = "";
      block.split("\n").forEach(function (line) {
        if (line.indexOf("event:") === 0) eventName = line.slice(6).trim();
        if (line.indexOf("data:") === 0) dataLine += line.slice(5).trim();
      });
      if (dataLine) {
        try {
          events.push({ event: eventName, data: JSON.parse(dataLine) });
        } catch (_) {
          events.push({ event: eventName, data: { raw: dataLine } });
        }
      }
    });
    return { events: events, remainder: remainder };
  }

  function formatMetricValue(raw) {
    var n = Number(raw);
    if (!isFinite(n)) return String(raw);
    if (Math.abs(n) >= 1e9) return "$" + (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
    if (Math.abs(n) >= 1e6) return "$" + (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (Math.abs(n) >= 1e3) return "$" + (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
    return String(n);
  }

  function describeViolations(violations) {
    return (violations || [])
      .map(function (v) {
        var m = /Metric '([^']+)'=([0-9.]+) contradicts locked == ([0-9.]+)/.exec(v || "");
        if (m) {
          return jdfT(
            "jdf.truth.violation.metric",
            "Locked {key} is {locked}; your text claimed {claimed}.",
            {
              key: m[1],
              locked: formatMetricValue(m[3]),
              claimed: formatMetricValue(m[2]),
            }
          );
        }
        return v;
      })
      .join(" ");
  }

  function InquireStreamClient(projectId, handlers) {
    this.projectId = projectId;
    this.handlers = handlers || {};
    this.controller = null;
  }

  InquireStreamClient.prototype.abort = function () {
    if (this.controller) this.controller.abort();
  };

  InquireStreamClient.prototype.start = function (payload) {
    var self = this;
    this.controller = new AbortController();
    if (global.AssureStreamRegistry) {
      global.AssureStreamRegistry.register(this.controller);
    }
    var url = "/api/projects/" + encodeURIComponent(this.projectId) + "/inquire/stream";
    var postStream =
      global.AssureSse && typeof global.AssureSse.postStream === "function"
        ? global.AssureSse.postStream
        : null;

    function dispatch(frame) {
      var key = "on" + frame.event.replace(/_/g, "");
      if (typeof self.handlers[key] === "function") self.handlers[key](frame.data);
      if (typeof self.handlers.onEvent === "function") self.handlers.onEvent(frame.event, frame.data);
    }

    if (postStream) {
      return postStream({
        url: url,
        body: payload || {},
        signal: this.controller.signal,
        parseBuffer: parseSseChunk,
        onFrame: dispatch,
      });
    }

    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
      signal: this.controller.signal,
    }).then(function (res) {
      if (!res.ok || !res.body) throw new Error("Stream failed (" + res.status + ")");
      var reader = res.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "";
      function pump() {
        return reader.read().then(function (result) {
          if (result.done) return;
          buffer += decoder.decode(result.value, { stream: true });
          var parsed = parseSseChunk(buffer);
          buffer = parsed.remainder;
          parsed.events.forEach(dispatch);
          return pump();
        });
      }
      return pump();
    });
  };

  function pickEl(primaryId, fallbackId) {
    return document.getElementById(primaryId) || (fallbackId ? document.getElementById(fallbackId) : null);
  }

  function jdfT(key, fallback, vars) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, vars || {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback !== undefined && fallback !== "" ? fallback : key;
  }

  function jdfHasText(key, fallback) {
    var text = jdfT(key, fallback);
    return Boolean(text && text !== key);
  }

  function JDFCanvasManager(opts) {
    this.projectId = (opts && opts.projectId) || global.__ASSURE_PROJECT_ID__ || "default";
    this.tree = (opts && opts.document) || global.__INITIAL_JDF__ || {
      document_id: "doc-" + this.projectId,
      meta: { title: "Untitled" },
      truth_ledger: {},
      body: [],
    };
    this.rootEl =
      (opts && opts.rootEl) || pickEl("jdf-render-target", "jdf-tree");
    this.previewEl = (opts && opts.previewEl) || document.getElementById("jdf-live-preview");
    this.statusEl = (opts && opts.pillEl) || pickEl("save-status", "jdf-save-pill");
    this.surgicalEl = (opts && opts.surgicalEl) || pickEl("aperture-indicator", "jdf-surgical-mode");
    this.truthEl = (opts && opts.truthEl) || pickEl("truth-ledger-badge", "jdf-truth-pill");
    this.stopBtn = pickEl("btn-stop-stream");
    this.redhatFindingsEl = document.getElementById("redhat-findings");
    this.streamStatusEl = document.getElementById("stream-status");
    this.truthErrorEl = document.getElementById("truth-error-detail");
    this.isDirty = false;
    this.isStreaming = false;
    this.surgicalTargetId = null;
    this.activeInsertAfterId = null;
    this.streamClient = null;
    this.livePreview = "";
    this.documentVersion = 1;
    this.showCitations = true;
    this._animateNextRender = false;
    this.isFirstLoad = true;
    this.isFirstVerification = true;
    this._compilerIssueCount = 0;
    this.currentVerificationState = null;
    this.verifyTimeout = null;
    this._savePillState = { state: "idle", key: "jdf.save.unsaved", vars: null };
  }

  JDFCanvasManager.prototype._setDirty = function (dirty) {
    this.isDirty = !!dirty;
    if (global.AssureUnsaved) global.AssureUnsaved.setDocumentDirty(this.isDirty);
  };

  JDFCanvasManager.prototype._setStreaming = function (streaming) {
    this.isStreaming = !!streaming;
    if (global.AssureUnsaved) global.AssureUnsaved.setGenerating(this.isStreaming);
  };

  JDFCanvasManager.prototype.loadProjectSettings = function () {
    var self = this;
    return fetch("/api/projects/" + encodeURIComponent(this.projectId) + "/settings")
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.settings && typeof data.settings.show_citations === "boolean") {
          self.showCitations = data.settings.show_citations;
        }
        if (self.rootEl) {
          self.rootEl.classList.toggle("hide-citations", !self.showCitations);
        }
      })
      .catch(function () {
        /* default show */
      });
  };

  JDFCanvasManager.prototype._normalizeProvenanceList = function (node) {
    var prov = (node && node.provenance) || [];
    if (!Array.isArray(prov)) {
      if (prov && typeof prov === "object") {
        prov = [prov];
      } else {
        prov = [];
      }
    }
    return prov.map(function (p) {
      if (!p || typeof p !== "object") return {};
      return {
        source_type: p.source_type || "internal_doc",
        source_name: p.source_name || p.source_file || "",
        url_or_doi: p.url_or_doi || "",
        source_id: p.source_id || "",
        page_number: p.page_number || p.page_or_timestamp || "",
        extracted_quote: p.extracted_quote || p.exact_quote || "",
        accessed_date: p.accessed_date || "",
      };
    });
  };

  JDFCanvasManager.prototype.setVersion = function (version) {
    this.documentVersion = version || 1;
    var verEl = document.getElementById("version-display");
    if (verEl) verEl.textContent = String(this.documentVersion);
    if (global.AssureCompilerStatus && typeof global.AssureCompilerStatus.setVersion === "function") {
      global.AssureCompilerStatus.setVersion(this.documentVersion);
    }
  };

  JDFCanvasManager.prototype._clearVerifyRetryUi = function () {
    var statusEl = document.getElementById("compiler-status");
    if (!statusEl) return;
    statusEl.classList.remove("verify-timeout-retry");
    statusEl.removeAttribute("role");
    statusEl.removeAttribute("tabindex");
    statusEl.onclick = null;
    statusEl.onkeydown = null;
  };

  JDFCanvasManager.prototype._syncCompilerStatus = function (state, detail) {
    if (typeof global.updateCompilerStatus === "function") {
      global.updateCompilerStatus(state, detail);
      if (global.AssureCompilerStatus && typeof global.AssureCompilerStatus.setIssueCount === "function") {
        global.AssureCompilerStatus.setIssueCount(this._compilerIssueCount);
      }
    }
  };

  JDFCanvasManager.prototype.setStreamStatus = function (step, messageKey, fallback, messageOverride) {
    if (step === 0) return;
    this._syncCompilerStatus("processing", messageOverride || jdfT(messageKey, fallback || "", {}));
  };

  JDFCanvasManager.prototype.clearTruthError = function () {
    if (!this.truthErrorEl) return;
    this.truthErrorEl.hidden = true;
    this.truthErrorEl.innerHTML = "";
  };

  JDFCanvasManager.prototype._showRedhatFinding = function (text) {
    if (!this.redhatFindingsEl || !text) return;
    var wrap = document.getElementById("redhat-findings-wrap");
    if (wrap) wrap.hidden = false;
    var block = document.createElement("div");
    block.className = "jdf-callout callout-redhat";
    block.textContent = text;
    this.redhatFindingsEl.appendChild(block);
    this.setStressTestStatus(this.redhatFindingsEl.children.length);
  };

  JDFCanvasManager.prototype.showTruthError = function (violations) {
    if (!this.truthErrorEl) return;
    var detail = describeViolations(violations);
    this.truthErrorEl.hidden = false;
    this.truthErrorEl.innerHTML =
      "<strong>" +
      jdfT("jdf.truth.violation.what", "What happened:") +
      "</strong> " +
      detail +
      "<br><strong>" +
      jdfT("jdf.truth.violation.fix_label", "How to fix:") +
      "</strong> " +
      jdfT(
        "jdf.truth.violation.fix",
        "Adjust your prompt to match the locked values in the Truth Ledger, or update the ledger if the source data changed."
      );
  };

  JDFCanvasManager.prototype.renderEmptyCanvas = function () {
    var projectId = global.__ASSURE_PROJECT_ID__ || "default";
    var wrap = document.createElement("div");
    wrap.className = "jdf-canvas-empty";
    wrap.setAttribute("role", "status");
    wrap.innerHTML =
      '<div class="jdf-canvas-empty-body">' +
      '<h2 class="jdf-empty-project-title">' + escapeHtml(projectId) + '</h2>' +
      '<p class="jdf-empty-sub">' +
      jdfT("jdf.canvas.empty.lead", "Open Compile to generate your first draft.") +
      '</p>' +
      '</div>';
    return wrap;
  };

  JDFCanvasManager.prototype.renderSkeleton = function (count) {
    if (!this.rootEl) return;
    this.rootEl.innerHTML = "";
    var n = count || 4;
    for (var i = 0; i < n; i += 1) {
      var block = document.createElement("div");
      block.className = "skeleton-block is-streaming-skeleton";
      block.setAttribute("aria-hidden", "true");
      this.rootEl.appendChild(block);
    }
  };

  JDFCanvasManager.prototype.setPreviewSkeleton = function (on) {
    if (!this.previewEl) return;
    if (on && !this.livePreview) {
      this.previewEl.classList.add("is-streaming-skeleton");
      this.previewEl.textContent = "";
    } else {
      this.previewEl.classList.remove("is-streaming-skeleton");
    }
  };

  JDFCanvasManager.prototype.loadProject = function () {
    var self = this;
    if (this.isFirstLoad) {
      this.renderSkeleton(4);
    }
    return this.loadProjectSettings().then(function () {
      return fetch("/api/projects/" + encodeURIComponent(self.projectId) + "/jdf")
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (data.document) self.tree = data.document;
          if (data.version) self.setVersion(data.version);
          self._animateNextRender = self.isFirstLoad;
          self.render();
          self.isFirstLoad = false;
        });
    });
  };

  JDFCanvasManager.prototype.refreshCanvas = function () {
    var self = this;
    return fetch("/api/projects/" + encodeURIComponent(this.projectId) + "/jdf")
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.document) self.tree = data.document;
        if (data.version) self.setVersion(data.version);
        self.render();
      });
  };

  JDFCanvasManager.prototype.setSavePill = function (state, messageKey, vars) {
    this._savePillState = {
      state: state || "idle",
      key: messageKey || "jdf.save.unsaved",
      vars: vars || null,
    };
    var fallbacks = {
      "jdf.save.unsaved": "◌ Unsaved",
      "jdf.save.ready": "◌ Unsaved",
      "jdf.status.compiling": "⬡ Working…",
      "jdf.status.committed": "● Committed (v{version})",
      "jdf.save.saving": "Saving…",
      "jdf.save.saved": "Saved",
      "jdf.save.error": "Save failed",
      "jdf.save.streaming": "Streaming…",
      "jdf.save.stream_complete": "Stream complete",
    };
    var text = jdfT(messageKey, fallbacks[messageKey] || messageKey, vars || {});
    var el = this.statusEl;
    if (!el) return;
    el.className = "save-pill pill-" + (state || "idle") + " tooltip-trigger";
    if (messageKey) el.setAttribute("data-i18n", messageKey);
    el.textContent = text;
    if (messageKey === "jdf.status.committed" && vars && vars.version) {
      if (global.AssureCompilerStatus) global.AssureCompilerStatus.setVersion(vars.version);
    }
  };

  JDFCanvasManager.prototype.refreshSavePill = function () {
    var s = this._savePillState || { state: "idle", key: "jdf.save.unsaved", vars: null };
    this.setSavePill(s.state, s.key, s.vars);
  };

  JDFCanvasManager.prototype.setStressTestStatus = function (issueCount) {
    this._compilerIssueCount = issueCount || 0;
    if (global.AssureCompilerStatus) {
      global.AssureCompilerStatus.setIssueCount(this._compilerIssueCount);
    }
    if (this._compilerIssueCount > 0) {
      this.currentVerificationState = "issues";
      this._syncCompilerStatus(
        "issues",
        jdfT("jdf.status.redhat", "⚠️ Stress Test: {n} Issues", { n: this._compilerIssueCount })
      );
    } else if (this.currentVerificationState === "verified") {
      this._syncCompilerStatus("verified");
    } else {
      this._syncCompilerStatus("idle");
    }
  };

  JDFCanvasManager.prototype.clearVerifyTimeout = function () {
    if (this.verifyTimeout) {
      this.verifyTimeout.clear();
      this.verifyTimeout = null;
    }
  };

  JDFCanvasManager.prototype.startVerifyTimeout = function (retryFn) {
    var self = this;
    this.clearVerifyTimeout();
    var onTimeout = function () {
      var msg =
        global.AssureAuditGate && typeof global.AssureAuditGate.timeoutRetryMessage === "function"
          ? global.AssureAuditGate.timeoutRetryMessage()
          : jdfT("audit.timeout", "Verification timeout — click to retry");
      self.setStreamStatus(2, "audit.timeout", msg, msg);
      var statusEl = document.getElementById("compiler-status");
      if (statusEl) {
        statusEl.classList.add("verify-timeout-retry");
        statusEl.setAttribute("role", "button");
        statusEl.setAttribute("tabindex", "0");
        statusEl.onclick = function () {
          statusEl.classList.remove("verify-timeout-retry");
          statusEl.removeAttribute("role");
          statusEl.removeAttribute("tabindex");
          statusEl.onclick = null;
          statusEl.onkeydown = null;
          if (typeof retryFn === "function") retryFn();
        };
        statusEl.onkeydown = function (e) {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            statusEl.onclick();
          }
        };
      }
    };
    if (global.AssureAuditGate && typeof global.AssureAuditGate.createVerificationTimeout === "function") {
      this.verifyTimeout = global.AssureAuditGate.createVerificationTimeout(onTimeout);
    } else {
      var timer = setTimeout(onTimeout, 12000);
      this.verifyTimeout = { clear: function () { clearTimeout(timer); } };
    }
  };

  JDFCanvasManager.prototype._triggerLockAnimation = function () {
    if (!this.isFirstVerification) return;
    this.isFirstVerification = false;
    var el = document.getElementById("compiler-status");
    if (global.AssureAuditGate && typeof global.AssureAuditGate.triggerLockAnimation === "function") {
      global.AssureAuditGate.triggerLockAnimation(el);
    } else if (el) {
      el.classList.remove("lock-animate");
      void el.offsetWidth;
      el.classList.add("lock-animate");
    }
  };

  JDFCanvasManager.prototype.setTruthBadge = function (status, violationCount) {
    if (status === "PASS") {
      this.currentVerificationState = "verified";
      this._compilerIssueCount = 0;
      this._syncCompilerStatus("verified", jdfT("compiler.status.verified", "✅ Verified"));
      this._triggerLockAnimation();
    } else if (status === "FAIL") {
      this.currentVerificationState = "issues";
      this._compilerIssueCount = violationCount || 1;
      this._syncCompilerStatus("issues", jdfT("compiler.status.issues", "❌ Issues Found"));
    } else {
      this.currentVerificationState = null;
      if (!this.isStreaming) {
        this._syncCompilerStatus("idle");
      }
    }
  };

  JDFCanvasManager.prototype.saveDocument = function (mutationType, extra) {
    var self = this;
    this.setSavePill("saving", "jdf.save.saving");
    return fetch("/api/projects/" + encodeURIComponent(this.projectId) + "/jdf", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        Object.assign(
          {
            document: this.tree,
            mutation_type: mutationType || "MANUAL_SAVE",
          },
          extra || {}
        )
      ),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) {
            var err = new Error((data && (data.error || data.detail)) || "Save failed");
            err.response = data;
            throw err;
          }
          return data;
        });
      })
      .then(function (data) {
        if (data.document) self.tree = data.document;
        if (data.version) self.setVersion(data.version);
        self._setDirty(false);
        if (global.AssureUnsaved) global.AssureUnsaved.clearUnsaved();
        self.setSavePill("saved", "jdf.status.committed", {
          version: data.version || self.documentVersion,
        });
        return data;
      })
      .catch(function (err) {
        self.setSavePill("error", "jdf.save.error");
        throw err;
      });
  };

  JDFCanvasManager.prototype.saveNodePatch = function (nodeId, nodeData, mutationType, extra) {
    var self = this;
    this.setSavePill("saving", "jdf.save.saving");
    return fetch("/api/projects/" + encodeURIComponent(this.projectId) + "/jdf", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        Object.assign(
          {
            id: nodeId,
            node_data: nodeData,
            mutation_type: mutationType || "NODE_UPDATE",
            insert_after_id: this.activeInsertAfterId || null,
          },
          extra || {}
        )
      ),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) {
            var err = new Error((data && (data.error || data.detail)) || "Save failed");
            err.response = data;
            throw err;
          }
          return data;
        });
      })
      .then(function (data) {
        if (data.document) self.tree = data.document;
        if (data.version) self.setVersion(data.version);
        self._setDirty(false);
        if (global.AssureUnsaved) global.AssureUnsaved.clearUnsaved();
        self.setSavePill("saved", "jdf.status.committed", {
          version: data.version || self.documentVersion,
        });
        return data;
      })
      .catch(function (err) {
        self.setSavePill("error", "jdf.save.error");
        throw err;
      });
  };

  JDFCanvasManager.prototype._insertDockNode = function (node) {
    if (!this.tree.body || !this.tree.body.length) {
      this.tree.body = [
        {
          type: "section",
          id: newNodeId("sec"),
          title: jdfT("jdf.canvas.empty.title", "Your Document Workspace"),
          children: [node],
          meta: {},
        },
      ];
      return;
    }
    if (this.activeInsertAfterId) {
      var inserted = false;
      var self = this;
      (this.tree.body || []).forEach(function (section) {
        if (inserted) return;
        var children = section.children || [];
        for (var i = 0; i < children.length; i += 1) {
          if (children[i].id === self.activeInsertAfterId) {
            children.splice(i + 1, 0, node);
            inserted = true;
            break;
          }
        }
        if (!inserted && section.id === self.activeInsertAfterId) {
          children.unshift(node);
          inserted = true;
        }
      });
      if (!inserted) {
        var last = this.tree.body[this.tree.body.length - 1];
        last.children = last.children || [];
        last.children.push(node);
      }
      return;
    }
    var targetSection = this.tree.body[this.tree.body.length - 1];
    targetSection.children = targetSection.children || [];
    targetSection.children.push(node);
  };

  JDFCanvasManager.prototype._nodeText = function (node) {
    if (!node) return "";
    if (node.type === "paragraph") return node.content || "";
    if (node.type === "callout") return (node.title || "") + "\n" + (node.content || "");
    return node.content || node.caption || "";
  };

  JDFCanvasManager.prototype._escapeHtml = function (text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  };

  JDFCanvasManager.prototype.getNodeById = function (nodeId) {
    var found = null;
    (this.tree.body || []).forEach(function (section) {
      if (found) return;
      (section.children || []).forEach(function (child) {
        if (child.id === nodeId) found = child;
      });
    });
    return found;
  };

  JDFCanvasManager.prototype._isInteractiveNodeClick = function (e) {
    var t = e.target;
    return !!(
      t.closest("button") ||
      t.closest('[role="button"]') ||
      t.closest("input") ||
      t.closest("select") ||
      t.closest("textarea") ||
      t.closest(".interactive-element") ||
      t.closest(".jdf-node-toolbar") ||
      t.closest(".node-toolbar")
    );
  };

  JDFCanvasManager.prototype.selectNodeForRefine = function (nodeId, opts) {
    opts = opts || {};
    var node = this.getNodeById(nodeId);
    if (!node) return;
    this.surgicalTargetId = nodeId;
    if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
      global.AssureNav.switchView("surgical");
    } else if (global.AssureMode && typeof global.AssureMode.activate === "function") {
      global.AssureMode.activate("surgical");
    }
    if (this.surgicalEl) this.surgicalEl.hidden = false;
    var targetEl = document.getElementById("active-target-id");
    if (targetEl) targetEl.textContent = nodeId;
    var intentEl = pickEl("inquiry-input", "jdf-intent");
    if (intentEl) {
      intentEl.focus();
      if (typeof intentEl.select === "function") intentEl.select();
    }
    this.render();
    if (opts.toast !== false && global.AssureToast) {
      global.AssureToast.show(
        jdfT("jdf.refine.selected", "Node selected for refine."),
        "info"
      );
    }
  };

  JDFCanvasManager.prototype._renderNodeBodyWithCitations = function (node, bodyEl) {
    var text = this._nodeText(node);
    var provList = this._normalizeProvenanceList(node);
    var provAttr = this._escapeHtml(JSON.stringify(provList.length ? provList[0] : {})).replace(/'/g, "&#39;");
    var html = this._escapeHtml(text);
    if (this.showCitations) {
      var ledger = this.tree.truth_ledger || {};
      var values = Object.keys(ledger).map(function (k) {
        return String(ledger[k]);
      });
      values.sort(function (a, b) {
        return b.length - a.length;
      });
      values.forEach(function (val) {
        if (!val) return;
        var re = new RegExp(val.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g");
        html = html.replace(re, function (match) {
          return (
            '<span class="truth-pill inline-citation interactive-element truth-pass" data-provenance="' +
            provAttr +
            '" title="View source">' +
            match +
            "</span>"
          );
        });
      });
      if (html === this._escapeHtml(text)) {
        html = html.replace(/\$[\d,.]+[KMB]?|\b\d+(?:\.\d+)?%/gi, function (match) {
          return (
            '<span class="truth-pill inline-citation interactive-element truth-idle" data-provenance="' +
            provAttr +
            '" title="View source">' +
            match +
            "</span>"
          );
        });
      }
    }
    bodyEl.innerHTML = html;
  };

  JDFCanvasManager.prototype._renderCitationBadge = function (node, article) {
    var self = this;
    var provList = this._normalizeProvenanceList(node);
    if (!provList.length) return;
    var badge = document.createElement("button");
    badge.type = "button";
    badge.className = "citation-badge";
    badge.setAttribute(
      "aria-label",
      jdfT("jdf.citation.badge", "Citations") + " " + provList.length
    );
    badge.innerHTML =
      "📎 " +
      jdfT("jdf.citation.count", "{count} source(s)", { count: String(provList.length) });
    var details = document.createElement("div");
    details.className = "citation-details";
    details.hidden = true;
    provList.forEach(function (p) {
      var row = document.createElement("div");
      row.className = "citation-detail-row";
      var typeLabel = jdfT(
        "jdf.citation.source_type." + (p.source_type || "internal_doc"),
        p.source_type || "internal_doc"
      );
      row.innerHTML =
        "<strong>" +
        self._escapeHtml(typeLabel) +
        "</strong>" +
        (p.source_name
          ? "<div>" +
            self._escapeHtml(jdfT("jdf.citation.source_name", "Source")) +
            ": " +
            self._escapeHtml(p.source_name) +
            "</div>"
          : "") +
        (p.page_number
          ? "<div>" +
            self._escapeHtml(jdfT("jdf.citation.page", "Page")) +
            ": " +
            self._escapeHtml(p.page_number) +
            "</div>"
          : "") +
        (p.url_or_doi
          ? "<div>" +
            self._escapeHtml(jdfT("jdf.citation.url", "URL / DOI")) +
            ": " +
            self._escapeHtml(p.url_or_doi) +
            "</div>"
          : "") +
        (p.extracted_quote
          ? "<blockquote>" + self._escapeHtml(p.extracted_quote) + "</blockquote>"
          : "") +
        (p.accessed_date
          ? "<div class=\"hint\">" +
            self._escapeHtml(jdfT("jdf.citation.accessed", "Accessed")) +
            ": " +
            self._escapeHtml(p.accessed_date) +
            "</div>"
          : "");
      details.appendChild(row);
    });
    badge.addEventListener("click", function () {
      details.hidden = !details.hidden;
      badge.classList.toggle("is-open", !details.hidden);
    });
    article.appendChild(badge);
    article.appendChild(details);
  };

  JDFCanvasManager.prototype._makeDockAnchor = function (afterId) {
    var self = this;
    var el = document.createElement("div");
    el.className = "dock-anchor";
    el.dataset.afterId = afterId || "";
    el.innerHTML = '<button type="button" class="dock-btn">+ Insert Here</button>';
    el.querySelector(".dock-btn").addEventListener("click", function () {
      self.activeInsertAfterId = afterId || null;
      document.querySelectorAll(".dock-anchor").forEach(function (n) {
        n.classList.toggle("is-active", n === el);
      });
    });
    return el;
  };

  JDFCanvasManager.prototype._toolbar = function (node, sectionIdx, childIdx) {
    var self = this;
    var bar = document.createElement("div");
    bar.className = "node-toolbar jdf-node-toolbar";
    bar.innerHTML =
      '<button type="button" data-act="edit">⚡ Edit</button>' +
      '<button type="button" data-act="up">▲</button>' +
      '<button type="button" data-act="down">▼</button>' +
      '<button type="button" data-act="del">🗑️ Delete</button>';
    bar.querySelector('[data-act="edit"]').addEventListener("click", function () {
      self.selectNodeForRefine(node.id, { toast: false });
    });
    bar.querySelector('[data-act="del"]').addEventListener("click", function () {
      var section = self.tree.body[sectionIdx];
      section.children.splice(childIdx, 1);
      self._setDirty(true);
      self.render();
    });
    bar.querySelector('[data-act="up"]').addEventListener("click", function () {
      if (childIdx <= 0) return;
      var section = self.tree.body[sectionIdx];
      var tmp = section.children[childIdx - 1];
      section.children[childIdx - 1] = section.children[childIdx];
      section.children[childIdx] = tmp;
      self._setDirty(true);
      self.render();
    });
    bar.querySelector('[data-act="down"]').addEventListener("click", function () {
      var section = self.tree.body[sectionIdx];
      if (childIdx >= section.children.length - 1) return;
      var tmp = section.children[childIdx + 1];
      section.children[childIdx + 1] = section.children[childIdx];
      section.children[childIdx] = tmp;
      self._setDirty(true);
      self.render();
    });
    return bar;
  };

  JDFCanvasManager.prototype.render = function (options) {
    if (!this.rootEl) return;
    var self = this;
    options = options || {};
    var animate = !!options.animate || this._animateNextRender;
    this._animateNextRender = false;
    var nodeIndex = 0;
    this.rootEl.classList.toggle("hide-citations", !this.showCitations);
    this.rootEl.innerHTML = "";
    var title = document.createElement("h2");
    title.className = "jdf-doc-title";
    title.contentEditable = "true";
    title.textContent = (this.tree.meta && this.tree.meta.title) || "Untitled";
    title.addEventListener("blur", function () {
      self.tree.meta = self.tree.meta || {};
      self.tree.meta.title = title.textContent.trim();
      self._setDirty(true);
    });
    this.rootEl.appendChild(title);

    if (!(this.tree.body || []).length) {
      this.rootEl.appendChild(this.renderEmptyCanvas());
    }

    (this.tree.body || []).forEach(function (section, sIdx) {
      var wrap = document.createElement("section");
      wrap.className = "jdf-section-block";
      wrap.appendChild(self._makeDockAnchor(section.id));
      var h = document.createElement("h3");
      h.className = "jdf-section-title";
      h.textContent = section.title || "Section";
      wrap.appendChild(h);
      (section.children || []).forEach(function (node, cIdx) {
        wrap.appendChild(self._makeDockAnchor(node.id));
        var article = document.createElement("article");
        article.className = "jdf-node jdf-node-" + (node.type || "paragraph");
        article.dataset.nodeId = node.id;
        if (self.surgicalTargetId === node.id) {
          article.classList.add("node-target", "selected");
        }
        article.addEventListener("click", function (e) {
          if (self._isInteractiveNodeClick(e)) return;
          var id = article.dataset.nodeId;
          if (id) self.selectNodeForRefine(id, { toast: true });
        });
        if (self.surgicalTargetId) {
          var siblings = section.children || [];
          var tIdx = siblings.findIndex(function (n) {
            return n.id === self.surgicalTargetId;
          });
          if (tIdx >= 0 && cIdx === tIdx - 1) article.classList.add("node-aperture-prev");
          if (tIdx >= 0 && cIdx === tIdx + 1) article.classList.add("node-aperture-next");
          if (tIdx === 0 && cIdx === 0) article.dataset.boundaryStart = "true";
          if (tIdx === siblings.length - 1 && cIdx === tIdx) article.dataset.boundaryEnd = "true";
        }
        var body = document.createElement("div");
        body.className = "jdf-node-body";
        self._renderNodeBodyWithCitations(node, body);
        if (node.type === "paragraph") {
          body.addEventListener("dblclick", function () {
            body.contentEditable = "true";
            body.focus();
          });
          body.addEventListener("blur", function () {
            body.contentEditable = "false";
            node.content = body.textContent;
            self._setDirty(true);
            self.saveDocument("MANUAL_TOUCHUP", { target_node_id: node.id });
            self._renderNodeBodyWithCitations(node, body);
          });
        }
        article.appendChild(self._toolbar(node, sIdx, cIdx));
        article.appendChild(body);
        if (self.showCitations) {
          self._renderCitationBadge(node, article);
        }
        if (animate) {
          article.classList.add("animated");
          article.style.animationDelay = (nodeIndex * 30) + "ms";
          nodeIndex += 1;
        }
        wrap.appendChild(article);
      });
      self.rootEl.appendChild(wrap);
    });

    if (this.previewEl) {
      this.previewEl.textContent = this.livePreview
        ? this.livePreview
        : jdfT("jdf.preview.empty", "Streaming output will appear here…");
    }
  };

  JDFCanvasManager.prototype._flattenPreview = function () {
    var lines = [];
    (this.tree.body || []).forEach(function (sec) {
      lines.push("# " + (sec.title || "Section"));
      (sec.children || []).forEach(function (node) {
        lines.push(this._nodeText(node));
      }, this);
    }, this);
    return lines.join("\n\n");
  };

  JDFCanvasManager.prototype.enterSurgicalMode = function (nodeId) {
    this.selectNodeForRefine(nodeId, { toast: false });
  };

  JDFCanvasManager.prototype.exitSurgicalMode = function () {
    this.surgicalTargetId = null;
    this.activeInsertAfterId = null;
    if (this.surgicalEl) this.surgicalEl.hidden = true;
    document.querySelectorAll(".diff-container").forEach(function (n) {
      n.remove();
    });
    this.render();
  };

  JDFCanvasManager.prototype.showDiffPreview = function (targetNodeId, originalText, newText, onAccept, onDiscard) {
    var self = this;
    var host = this.rootEl.querySelector('[data-node-id="' + targetNodeId + '"]');
    if (!host) return;
    var card = document.createElement("div");
    card.className = "diff-container";
    card.innerHTML =
      '<div class="diff-header">' +
      jdfT("jdf.diff.title", "Proposed changes") +
      "</div>" +
      '<div class="diff-body">' +
      renderDiffHtml(computeWordDiff(originalText, newText)) +
      "</div>" +
      '<p class="diff-help">' +
      jdfT(
        "jdf.diff.help",
        "Review the changes above. Click Accept to apply, or Discard to cancel."
      ) +
      "</p>" +
      '<div class="diff-actions">' +
      '<button type="button" class="btn btn-primary diff-accept">' +
      jdfT("jdf.diff.accept", "Accept mutation") +
      "</button>" +
      '<button type="button" class="btn btn-outline diff-discard">' +
      jdfT("jdf.diff.discard", "Discard") +
      "</button>" +
      "</div>";
    card.querySelector(".diff-accept").addEventListener("click", function () {
      if (typeof onAccept === "function") onAccept();
      card.remove();
    });
    card.querySelector(".diff-discard").addEventListener("click", function () {
      if (typeof onDiscard === "function") onDiscard();
      card.remove();
    });
    host.insertAdjacentElement("afterend", card);
  };

  JDFCanvasManager.prototype.applyMutation = function (targetNodeId, node) {
    var self = this;
    (this.tree.body || []).forEach(function (section) {
      (section.children || []).forEach(function (child, idx) {
        if (child.id === targetNodeId) section.children[idx] = node;
      });
    });
    return this.saveDocument("MUTATION_ACCEPT", {
      target_node_id: targetNodeId,
      change_summary: "Accepted SSE mutation",
    }).then(function () {
      self.exitSurgicalMode();
      self.render();
    });
  };

  function newNodeId(prefix) {
    return (prefix || "para") + "-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  function buildComposeIntent() {
    var taskEl = document.getElementById("task");
    var contextEl = document.getElementById("context");
    var intent = global.state && global.state.intent;
    var parts = [];
    if (intent) parts.push("[intent: " + intent + "]");
    if (taskEl && taskEl.value.trim()) parts.push(taskEl.value.trim());
    var ctx = contextEl && contextEl.value.trim();
    if (ctx) parts.push("Context:\n" + ctx);
    if (global.state && global.state.fileText) parts.push(global.state.fileText.trim());
    return parts.join("\n\n") || jdfT("compose.generating", "Draft a document section");
  }

  JDFCanvasManager.prototype.dockDraftToCanvas = function (draftText) {
    var self = this;
    var text = (draftText || this.livePreview || "").trim();
    if (!text) return Promise.reject(new Error("No draft text"));
    var node = {
      type: "paragraph",
      id: newNodeId("para"),
      content: text,
      entities_referenced: [],
      meta: { source: "compose_dock", optimistic: true },
    };
    var treeSnapshot = JSON.parse(JSON.stringify(this.tree || { body: [], meta: {}, truth_ledger: {} }));

    this._insertDockNode(node);
    this.render();
    this.setSavePill("saving", "jdf.save.saving");

    return this.saveNodePatch(node.id, node, "NODE_DOCK", {
      change_summary: "Docked compose draft",
    })
      .then(function (data) {
        if (node.meta) delete node.meta.optimistic;
        self.livePreview = "";
        if (data.document) self.tree = data.document;
        self.render({ animate: true });
        if (global.AssureInquire && global.AssureInquire.showDockButton) {
          global.AssureInquire.showDockButton(false);
        }
        if (global.AssureToast) {
          global.AssureToast.show(
            jdfT("compose.draft_ready", "Draft docked to canvas."),
            "success"
          );
        }
        if (global.AssureMode) global.AssureMode.activate("surgical");
        if (global.AssureNav) global.AssureNav.switchView("surgical");
        return node;
      })
      .catch(function (err) {
        self.tree = treeSnapshot;
        self.render();
        var detail = (err && err.message) || jdfT("jdf.save.error", "Save failed");
        if (global.AssureToast) {
          global.AssureToast.show(
            jdfT("jdf.dock.rollback", "Change rolled back: {detail}", { detail: detail }),
            "error"
          );
        }
        throw err;
      });
  };

  JDFCanvasManager.prototype.composeInquire = function (runRedhat) {
    var self = this;
    var intent = buildComposeIntent();
    if (!intent || !(document.getElementById("task") || {}).value.trim()) {
      if (global.AssureToast) {
        global.AssureToast.show(jdfT("error.task", "Write a question or topic first."), "error");
      }
      return Promise.reject(new Error("empty task"));
    }

    this._setStreaming(true);
    this.livePreview = "";
    this.clearVerifyTimeout();
    this.setPreviewSkeleton(true);
    this.clearTruthError();
    if (global.AssureInquire && global.AssureInquire.showDockButton) {
      global.AssureInquire.showDockButton(false);
    }
    this.setStreamStatus(1, "compose.generating", "Generating draft…");
    if (this.redhatFindingsEl) this.redhatFindingsEl.innerHTML = "";
    var redhatWrap = document.getElementById("redhat-findings-wrap");
    if (redhatWrap) redhatWrap.hidden = true;
    if (this.stopBtn) {
      this.stopBtn.style.display = "inline-flex";
      this.stopBtn.onclick = function () {
        if (self.streamClient) self.streamClient.abort();
      };
    }
    this.setSavePill("saving", "jdf.save.streaming");
    if (this.streamClient) this.streamClient.abort();
    this.streamClient = new InquireStreamClient(this.projectId, {
      onstatus: function (data) {
        var step = data && data.step;
        var message = data && data.message;
        if (step === 3 || (data && data.stage === "redhat")) {
          self.setStreamStatus(3, "jdf.stream.redhat", "Running stress test…", message);
        } else if (step === 2 || (data && data.stage === "verify")) {
          self.setStreamStatus(2, "jdf.stream.verifying", "Verifying numbers…", message);
          self.startVerifyTimeout(function () {
            self.composeInquire(runRedhat);
          });
        } else {
          self.setStreamStatus(1, "compose.generating", "Generating draft…", message);
        }
      },
      ontoken: function (data) {
        self.livePreview += data.delta || "";
        if (self.previewEl) {
          self.previewEl.classList.remove("is-streaming-skeleton");
          self.previewEl.textContent = self.livePreview;
        }
      },
      ontruthcheck: function (data) {
        self.clearVerifyTimeout();
        self._clearVerifyRetryUi();
        if (data.status === "PASS") {
          self.setTruthBadge("PASS");
          self.clearTruthError();
        } else {
          self.setTruthBadge("FAIL", (data.violations || []).length);
          self.showTruthError(data.violations || []);
        }
      },
      onredhatcallout: function (data) {
        if (self.redhatFindingsEl && data.node) {
          self._showRedhatFinding(data.node.content || "");
        }
      },
      onredhatannotation: function (data) {
        if (data.annotation && data.annotation.text) {
          self._showRedhatFinding(data.annotation.text);
        }
      },
      oncomplete: function (data) {
        self.clearVerifyTimeout();
        self._setStreaming(false);
        self.setPreviewSkeleton(false);
        if (self.stopBtn) self.stopBtn.style.display = "none";
        self.setStreamStatus(4, "compose.draft_ready", "Draft ready");
        self.setSavePill("saved", "jdf.save.stream_complete");
        if (!data || data.ok !== false) {
          if (global.AssureInquire && global.AssureInquire.showDockButton) {
            global.AssureInquire.showDockButton(true, self.livePreview);
          }
        }
      },
      onEvent: function (ev, data) {
        if (ev === "complete") {
          self._setStreaming(false);
          self.setPreviewSkeleton(false);
          if (!data || data.ok !== false) {
            self.setStreamStatus(4, "compose.draft_ready", "Draft ready");
            if (global.AssureInquire && global.AssureInquire.showDockButton) {
              global.AssureInquire.showDockButton(true, self.livePreview);
            }
          }
        }
      },
    });
    return this.streamClient.start({
      user_intent: intent,
      target_node_id: null,
      run_redhat: runRedhat !== false,
      document: this.tree,
    });
  };

  JDFCanvasManager.prototype.inquire = function (intent, runRedhat) {
    var self = this;
    if (runRedhat !== false && global.AssureSessionLimit && !global.AssureSessionLimit.tryConsume()) {
      if (global.AssureToast) {
        global.AssureToast.show(
          jdfT("safeguard.session.limit_reached", "Session Limit Reached"),
          "info"
        );
      }
      return;
    }
    this._setStreaming(true);
    this.livePreview = "";
    this.clearVerifyTimeout();
    this.setPreviewSkeleton(true);
    this.clearTruthError();
    this.setStreamStatus(1, "jdf.stream.thinking", "Thinking…");
    if (this.redhatFindingsEl) this.redhatFindingsEl.innerHTML = "";
    this.setStressTestStatus(0);
    if (this.stopBtn) {
      this.stopBtn.style.display = "inline-flex";
      this.stopBtn.textContent = jdfT("jdf.stop_stream", "Stop stream");
      this.stopBtn.title = jdfT(
        "jdf.stop_stream.hint",
        "Cancel the live stream if it seems stuck."
      );
      this.stopBtn.onclick = function () {
        if (self.streamClient) self.streamClient.abort();
      };
    }
    this.setSavePill("saving", "jdf.save.streaming");
    if (this.streamClient) this.streamClient.abort();
    this.streamClient = new InquireStreamClient(this.projectId, {
      onstatus: function (data) {
        var step = data && data.step;
        var message = data && data.message;
        if (step === 1 || (data && data.stage === "preflight") || (data && data.stage === "model")) {
          self.setStreamStatus(1, "jdf.stream.thinking", "Thinking…", message);
        } else if (step === 2 || (data && data.stage === "verify")) {
          self.setStreamStatus(2, "jdf.stream.verifying", "Verifying numbers…", message);
          self.startVerifyTimeout(function () {
            var intentEl = pickEl("inquiry-input", "jdf-intent");
            var redhatEl = pickEl("toggle-redhat", "jdf-redhat");
            self.inquire((intentEl && intentEl.value) || "Revise document", redhatEl && redhatEl.checked);
          });
        } else if (step === 3 || (data && data.stage === "redhat")) {
          self.setStreamStatus(3, "jdf.stream.redhat", "Running stress test…", message);
        } else if (step === 4 || (data && data.stage === "ready")) {
          self.setStreamStatus(4, "jdf.stream.ready", "Ready to dock", message);
        } else if (message) {
          self.setStreamStatus(step || 0, "", message, message);
        }
      },
      ontoken: function (data) {
        self.livePreview += data.delta || "";
        if (self.previewEl) {
          self.previewEl.classList.remove("is-streaming-skeleton");
          self.previewEl.textContent = self.livePreview;
        }
      },
      ontruthcheck: function (data) {
        self.clearVerifyTimeout();
        self._clearVerifyRetryUi();
        self.setStreamStatus(2, "jdf.stream.verifying", "Verifying numbers…");
        if (data.status === "PASS") {
          self.setTruthBadge("PASS");
          self.clearTruthError();
        } else {
          self.setTruthBadge("FAIL", (data.violations || []).length);
          self.showTruthError(data.violations || []);
        }
      },
      onjdfnodeready: function (data) {
        if (data.is_mutation && data.target_node_id) {
          self.showDiffPreview(
            data.target_node_id,
            data.original_content || "",
            data.new_content || self._nodeText(data.node),
            function () {
              self.applyMutation(data.target_node_id, data.node);
            },
            function () {
              self.exitSurgicalMode();
            }
          );
        }
      },
      onredhatcallout: function (data) {
        if (self.redhatFindingsEl && data.node) {
          self._showRedhatFinding(data.node.content || "");
        }
      },
      onredhatannotation: function (data) {
        if (data.annotation && data.annotation.text) {
          self._showRedhatFinding(data.annotation.text);
        }
      },
      oncomplete: function () {
        self.clearVerifyTimeout();
        self._setStreaming(false);
        self.setPreviewSkeleton(false);
        if (self.stopBtn) self.stopBtn.style.display = "none";
        self.setStreamStatus(4, "jdf.stream.ready", "Ready to dock");
        self.setSavePill("saved", "jdf.save.stream_complete");
      },
      onEvent: function (ev, data) {
        if (ev === "complete") {
          self._setStreaming(false);
          self.setPreviewSkeleton(false);
          if (!data || data.ok !== false) {
            self.setStreamStatus(4, "jdf.stream.ready", "Ready to dock");
          }
        }
      },
    });
    return this.streamClient.start({
      user_intent: intent,
      target_node_id: this.surgicalTargetId,
      run_redhat: runRedhat !== false,
      document: this.tree,
    });
  };

  JDFCanvasManager.prototype.bind = function () {
    var self = this;
    var inquireBtn = pickEl("btn-inquire", "jdf-inquire");
    var intentEl = pickEl("inquiry-input", "jdf-intent");
    var redhatEl = pickEl("toggle-redhat", "jdf-redhat");
    var saveBtn = document.getElementById("save-status") || document.getElementById("jdf-save");
    var cancelBtn = pickEl("btn-cancel-edit", "jdf-surgical-cancel");

    if (inquireBtn) {
      inquireBtn.addEventListener("click", function () {
        var view = global.AssureNav ? global.AssureNav.activeView : "surgical";
        var redhatOn = redhatEl && redhatEl.checked;
        if (view === "generate") {
          if (global.AssureGenerate && typeof global.AssureGenerate.compileFromIntent === "function") {
            global.AssureGenerate.compileFromIntent();
          }
          return;
        }
        if (global.AssureUnsaved) global.AssureUnsaved.clearDraft();
        self.inquire((intentEl && intentEl.value) || "Revise document", redhatOn);
      });
    }
    var dockBtn = document.getElementById("btn-dock-draft");
    if (dockBtn) {
      dockBtn.addEventListener("click", function () {
        self.dockDraftToCanvas(self.livePreview).catch(function (err) {
          if (global.AssureToast) global.AssureToast.show(String(err.message || err), "error");
        });
      });
    }
    if (saveBtn) {
      saveBtn.addEventListener("click", function () {
        self.saveDocument("MANUAL_SAVE");
      });
    }
    if (cancelBtn) {
      cancelBtn.addEventListener("click", function () {
        self.exitSurgicalMode();
      });
    }
    window.addEventListener("keydown", function (e) {
      if (e.key === "Escape") self.exitSurgicalMode();
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        if (document.activeElement && document.activeElement.contentEditable === "true") {
          document.activeElement.blur();
        } else if (inquireBtn) {
          inquireBtn.click();
        }
      }
    });

    if (!global.__INITIAL_JDF__ || !(global.__INITIAL_JDF__.body || []).length) {
      this.loadProject();
    } else {
      this.loadProjectSettings().then(function () {
        self.render();
      });
    }
    this.setSavePill("idle", "jdf.save.unsaved");
  };

  global.JDFCanvasManager = JDFCanvasManager;
  global.InquireStreamClient = InquireStreamClient;
  global.computeWordDiff = computeWordDiff;
  global.dockDraftToCanvas = function (text) {
    if (global.__assureJdf) return global.__assureJdf.dockDraftToCanvas(text);
    return Promise.reject(new Error("JDF manager not ready"));
  };
  global.selectNodeForRefine = function (nodeId, opts) {
    if (global.__assureJdf && typeof global.__assureJdf.selectNodeForRefine === "function") {
      return global.__assureJdf.selectNodeForRefine(nodeId, opts);
    }
  };
})(window);
