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

  function renderOriginalDiffHtml(tokens) {
    return tokens
      .map(function (tok) {
        if (tok.t === "ins") return "";
        if (tok.t === "del") return '<del class="diff-del">' + tok.w + "</del>";
        return tok.w;
      })
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function renderProposedDiffHtml(tokens) {
    return tokens
      .map(function (tok) {
        if (tok.t === "del") return "";
        if (tok.t === "ins") return '<ins class="diff-ins">' + tok.w + "</ins>";
        return tok.w;
      })
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();
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
        idleTimeoutMs: 90000,
        parseBuffer: parseSseChunk,
        onFrame: dispatch,
      }).catch(function (err) {
        if (typeof self.handlers.onError === "function") self.handlers.onError(err);
        else if (typeof self.handlers.onEvent === "function") {
          self.handlers.onEvent("error", { ok: false, error: String(err && err.message ? err.message : err) });
        }
        throw err;
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
    this.showConfidenceOverlay = true;
    try {
      if (global.localStorage && global.localStorage.getItem("assure_confidence_overlay") === "0") {
        this.showConfidenceOverlay = false;
      }
    } catch (_) {}
    this.confidenceSpans =
      (this.tree && this.tree.meta && this.tree.meta.confidenceSpans) || [];
    this._revisionHistory = [];
    this._versionPreview = null;
    this._liveTreeSnapshot = null;
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
    if (verEl && !document.getElementById("version-history-slider")) {
      verEl.textContent = String(this.documentVersion);
    }
    this._syncVersionSliderUi(version);
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
          var files = global.AssureProjectFileManager;
          var after = Promise.resolve();
          if (files && typeof files.hydrate === "function") {
            after = files.hydrate(self.projectId);
          }
          return after.then(function () {
            self._liveTreeSnapshot = JSON.parse(JSON.stringify(self.tree || {}));
            self._animateNextRender = self.isFirstLoad;
            self.render();
            self.isFirstLoad = false;
            return self.loadRevisionHistory();
          });
        });
    });
  };

  JDFCanvasManager.prototype.loadRevisionHistory = function () {
    var self = this;
    return fetch("/api/projects/" + encodeURIComponent(this.projectId) + "/history")
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        self._revisionHistory = (data.revisions || [])
          .slice()
          .sort(function (a, b) {
            return a.version - b.version;
          });
        self._syncVersionSliderUi(self.documentVersion);
      })
      .catch(function () {
        self._revisionHistory = [];
        self._syncVersionSliderUi(self.documentVersion);
      });
  };

  JDFCanvasManager.prototype._revisionMetaForVersion = function (version) {
    var hist = this._revisionHistory || [];
    var i;
    for (i = 0; i < hist.length; i += 1) {
      if (hist[i].version === version) return hist[i];
    }
    return null;
  };

  JDFCanvasManager.prototype._formatRevisionWhen = function (iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      return d.toLocaleString();
    } catch (_) {
      return String(iso);
    }
  };

  JDFCanvasManager.prototype._syncVersionSliderUi = function (version) {
    var slider = document.getElementById("version-history-slider");
    var label = document.getElementById("version-history-label");
    var restoreBtn = document.getElementById("version-restore-btn");
    if (!slider) return;
    var hist = this._revisionHistory || [];
    var maxV = hist.length ? hist[hist.length - 1].version : this.documentVersion || 1;
    var minV = hist.length ? hist[0].version : 1;
    slider.min = String(minV);
    slider.max = String(maxV);
    slider.disabled = maxV <= minV;
    var current = version || this.documentVersion || maxV;
    slider.value = String(current);
    if (label) {
      var meta = this._revisionMetaForVersion(current);
      var when = meta && meta.created_at ? this._formatRevisionWhen(meta.created_at) : "";
      label.textContent = when
        ? jdfT("jdf.version.label_when", "v{version} — {when}", { version: String(current), when: when })
        : jdfT("jdf.version.label", "v{version}", { version: String(current) });
    }
    if (restoreBtn) {
      restoreBtn.hidden = !this._versionPreview || current >= maxV;
    }
  };

  JDFCanvasManager.prototype.loadJdfVersion = function (version) {
    var self = this;
    var target = parseInt(version, 10);
    if (!target || isNaN(target)) return Promise.resolve();
    var hist = this._revisionHistory || [];
    var maxV = hist.length ? hist[hist.length - 1].version : this.documentVersion || target;
    if (target >= maxV) {
      this._versionPreview = null;
      this._syncVersionSliderUi(maxV);
      if (this._liveTreeSnapshot) {
        this.tree = JSON.parse(JSON.stringify(this._liveTreeSnapshot));
        this.render();
      } else {
        return this.refreshCanvas();
      }
      return Promise.resolve();
    }
    return fetch(
      "/api/projects/" + encodeURIComponent(this.projectId) + "/jdf?version=" + encodeURIComponent(String(target))
    )
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data.document) return;
        self._versionPreview = target;
        self.tree = data.document;
        self.render();
        self._syncVersionSliderUi(target);
      });
  };

  JDFCanvasManager.prototype.restoreVersionPreview = function () {
    var self = this;
    if (!this._versionPreview || !this.tree) return Promise.resolve();
    var version = this._versionPreview;
    return this.saveDocument("MANUAL_TOUCHUP", {
      change_summary: "Restored from version " + version,
    }).then(function () {
      self._versionPreview = null;
      self._liveTreeSnapshot = JSON.parse(JSON.stringify(self.tree || {}));
      return self.loadRevisionHistory();
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
        self._liveTreeSnapshot = JSON.parse(JSON.stringify(self.tree || {}));
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

  var JDF_ALLOWED_KEYS = {
    paragraph: ["type", "id", "content", "entities_referenced", "provenance", "meta", "annotations"],
    section: ["type", "id", "title", "children", "meta", "annotations"],
    callout: ["type", "id", "variant", "title", "content", "annotations"],
    table: ["type", "id", "caption", "headers", "rows", "bound_entities", "annotations"],
    document: ["document_id", "meta", "truth_ledger", "body"],
  };

  function sanitizeJDFNode(node) {
    if (!node || typeof node !== "object") return node;
    var type = node.type;
    var keys = type && JDF_ALLOWED_KEYS[type];
    if (!keys) return node;
    var sanitized = {};
    var i;
    for (i = 0; i < keys.length; i += 1) {
      var key = keys[i];
      if (node[key] !== undefined) sanitized[key] = node[key];
    }
    if (sanitized.children && Array.isArray(sanitized.children)) {
      sanitized.children = sanitized.children.map(sanitizeJDFNode);
    }
    return sanitized;
  }

  function sanitizeJDFDocument(tree) {
    if (!tree || typeof tree !== "object") return tree;
    var keys = JDF_ALLOWED_KEYS.document;
    var sanitized = {};
    var i;
    for (i = 0; i < keys.length; i += 1) {
      var key = keys[i];
      if (tree[key] !== undefined) sanitized[key] = tree[key];
    }
    if (sanitized.body && Array.isArray(sanitized.body)) {
      sanitized.body = sanitized.body.map(sanitizeJDFNode);
    }
    return sanitized;
  }

  JDFCanvasManager.prototype.saveDocument = function (mutationType, extra) {
    var self = this;
    this.setSavePill("saving", "jdf.save.saving");
    return fetch("/api/projects/" + encodeURIComponent(this.projectId) + "/jdf", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        Object.assign(
          {
            document: sanitizeJDFDocument(this.tree),
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
        self._liveTreeSnapshot = JSON.parse(JSON.stringify(self.tree || {}));
        self._versionPreview = null;
        self._setDirty(false);
        if (global.AssureUnsaved) global.AssureUnsaved.clearUnsaved();
        self.setSavePill("saved", "jdf.status.committed", {
          version: data.version || self.documentVersion,
        });
        self.loadRevisionHistory();
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
            node_data: sanitizeJDFNode(nodeData),
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
      if (section.id === nodeId) {
        found = section;
        return;
      }
      (section.children || []).forEach(function (child) {
        if (child.id === nodeId) found = child;
      });
    });
    return found;
  };

  /** Walk body to return { section, node } for a child id, or { section } for a section id. */
  JDFCanvasManager.prototype.getNodeSectionPath = function (nodeId) {
    var path = null;
    (this.tree.body || []).forEach(function (section) {
      if (path) return;
      if (section.id === nodeId) {
        path = { section: section, node: null };
        return;
      }
      (section.children || []).forEach(function (child) {
        if (child.id === nodeId) path = { section: section, node: child };
      });
    });
    return path;
  };

  JDFCanvasManager.prototype.getSectionPath = JDFCanvasManager.prototype.getNodeSectionPath;

  JDFCanvasManager.prototype.reorderSections = function (orderedSectionIds) {
    var body = this.tree.body || [];
    var byId = {};
    body.forEach(function (sec) {
      byId[sec.id] = sec;
    });
    var next = (orderedSectionIds || [])
      .map(function (id) {
        return byId[id];
      })
      .filter(Boolean);
    if (next.length !== body.length) return false;
    this.tree.body = next;
    this._setDirty(true);
    if (global.AssureEditorBridge && typeof global.AssureEditorBridge.syncReorderedASTToCanvas === "function") {
      global.AssureEditorBridge.syncReorderedASTToCanvas(orderedSectionIds);
    } else {
      this.render();
    }
    this.saveDocument("SECTION_REORDER");
    return true;
  };

  JDFCanvasManager.prototype.mergeSectionWithNext = function (sectionId) {
    var body = this.tree.body || [];
    var sIdx = -1;
    body.forEach(function (sec, idx) {
      if (sec.id === sectionId) sIdx = idx;
    });
    if (sIdx < 0 || sIdx >= body.length - 1) return false;
    var sec = body[sIdx];
    var next = body[sIdx + 1];
    sec.children = (sec.children || []).concat(next.children || []);
    body.splice(sIdx + 1, 1);
    this._setDirty(true);
    this.render();
    this.saveDocument("SECTION_MERGE", { target_node_id: sectionId });
    return true;
  };

  JDFCanvasManager.prototype.deleteSection = function (sectionId) {
    var body = this.tree.body || [];
    if (body.length <= 1) return false;
    var next = body.filter(function (sec) {
      return sec.id !== sectionId;
    });
    if (next.length === body.length) return false;
    this.tree.body = next;
    if (this.surgicalTargetId) {
      var path = this.getNodeSectionPath(this.surgicalTargetId);
      if (path && path.section && path.section.id === sectionId) {
        this.exitSurgicalMode();
      }
    }
    this._setDirty(true);
    this.render();
    this.saveDocument("SECTION_DELETE", { target_node_id: sectionId });
    return true;
  };

  JDFCanvasManager.prototype.connectSections = function (sourceSectionId, targetSectionId) {
    var body = this.tree.body || [];
    var targetSec = null;
    var sourceSec = null;
    body.forEach(function (sec) {
      if (sec.id === sourceSectionId) sourceSec = sec;
      if (sec.id === targetSectionId) targetSec = sec;
    });
    if (!sourceSec || !targetSec) return false;
    var bridgeNode = (targetSec.children || [])[0];
    if (!bridgeNode || !bridgeNode.id) return false;
    var intent =
      "Rewrite this section so it flows smoothly from the previous section titled \"" +
      (sourceSec.title || "Section") +
      "\". Preserve facts and locked numbers; improve the transition only.";
    this.selectNodeForRefine(bridgeNode.id, { toast: false, skipViewSwitch: true, skipRender: true });
    this.inquire(intent, true);
    return true;
  };

  JDFCanvasManager.prototype.refineFullDocument = function (intent, runRedhat) {
    var self = this;
    var text = (intent || "").trim();
    if (!text) return false;
    this.surgicalTargetId = null;
    var targetEl = document.getElementById("active-target-id");
    if (targetEl) targetEl.textContent = "—";
    if (this.rootEl) {
      this.rootEl.querySelectorAll(".jdf-node").forEach(function (el) {
        el.classList.remove("selected", "node-target");
      });
    }
    this._setInquiryIntent(text);
    this.inquire(text, runRedhat !== false);
    return true;
  };

  /**
   * Shared status used by the canvas gutter and the Argument Spine.
   * Z3 annotations use status "violation" | "pass" — never "FAIL".
   */
  JDFCanvasManager.prototype.computeNodeStatus = function (node) {
    if (!node) return "unverified";
    if (this.rootEl && this.rootEl.classList.contains("is-draft-preview")) return "unverified";
    var z3 = node.annotations && node.annotations.z3;
    if (Array.isArray(z3) && z3.some(function (z) { return z.status === "violation"; })) {
      return "error";
    }
    var redhat = node.annotations && node.annotations.redhat;
    if (Array.isArray(redhat) && redhat.length) return "warning";
    return "verified";
  };

  /** Same truth_ledger substring match the canvas uses for lock pills. */
  JDFCanvasManager.prototype.nodeHasLockedNumber = function (node) {
    var text = this._nodeText(node);
    if (!text) return false;
    var ledger = (this.tree && this.tree.truth_ledger) || {};
    return Object.keys(ledger).some(function (key) {
      var val = String(ledger[key]);
      return !!val && text.indexOf(val) >= 0;
    });
  };

  JDFCanvasManager.prototype._emitJdfRendered = function () {
    document.dispatchEvent(
      new CustomEvent("assure:jdf:rendered", { detail: { tree: this.tree } })
    );
  };

  JDFCanvasManager.prototype._locateNode = function (nodeId) {
    var loc = null;
    (this.tree.body || []).forEach(function (section, sIdx) {
      if (loc) return;
      (section.children || []).forEach(function (child, cIdx) {
        if (child.id === nodeId) loc = { section: section, sIdx: sIdx, node: child, cIdx: cIdx };
      });
    });
    return loc;
  };

  JDFCanvasManager.prototype.duplicateNode = function (nodeId) {
    var loc = this._locateNode(nodeId || this.surgicalTargetId);
    if (!loc) return;
    var copy = JSON.parse(JSON.stringify(loc.node));
    copy.id = newNodeId("p");
    copy.meta = Object.assign({}, copy.meta || {}, { duplicated_from: loc.node.id });
    loc.section.children.splice(loc.cIdx + 1, 0, copy);
    this._setDirty(true);
    this.render();
    this.saveDocument("NODE_DUPLICATE", { target_node_id: copy.id });
  };

  JDFCanvasManager.prototype.splitSectionAtNode = function (nodeId) {
    var loc = this._locateNode(nodeId || this.surgicalTargetId);
    if (!loc || loc.cIdx <= 0) return;
    var moved = loc.section.children.splice(loc.cIdx);
    var next = {
      type: "section",
      id: newNodeId("sec"),
      title: (loc.section.title || "Section") + " (2)",
      children: moved,
      annotations: { redhat: [], z3: [] },
      meta: {},
    };
    this.tree.body.splice(loc.sIdx + 1, 0, next);
    this._setDirty(true);
    this.render();
    this.saveDocument("SECTION_SPLIT", { target_node_id: nodeId });
  };

  JDFCanvasManager.prototype.mergeWithNext = function (nodeId) {
    var loc = this._locateNode(nodeId || this.surgicalTargetId);
    if (!loc) return;
    if (loc.cIdx < loc.section.children.length - 1) {
      var next = loc.section.children[loc.cIdx + 1];
      loc.node.content = (this._nodeText(loc.node) + "\n\n" + this._nodeText(next)).trim();
      loc.section.children.splice(loc.cIdx + 1, 1);
    } else if (loc.sIdx < this.tree.body.length - 1) {
      var nextSec = this.tree.body[loc.sIdx + 1];
      loc.section.children = loc.section.children.concat(nextSec.children || []);
      this.tree.body.splice(loc.sIdx + 1, 1);
    } else {
      return;
    }
    this._setDirty(true);
    this.render();
    this.saveDocument("NODE_MERGE", { target_node_id: loc.node.id });
  };

  JDFCanvasManager.prototype.showRevisionDiff = function (original, proposed, options) {
    var panel = document.getElementById("jdf-diff-panel");
    var origEl = document.getElementById("jdf-diff-original");
    var propEl = document.getElementById("jdf-diff-proposed");
    if (!panel || !origEl || !propEl) return;
    options = options || {};
    if (options.sideBySideDiff) {
      var tokens = computeWordDiff(original, proposed);
      origEl.innerHTML =
        renderOriginalDiffHtml(tokens) || this._escapeHtml(original || "");
      propEl.innerHTML =
        renderProposedDiffHtml(tokens) || this._escapeHtml(proposed || "");
    } else {
      origEl.textContent = original || "";
      propEl.textContent = proposed || "";
    }
    panel.hidden = false;
    if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
      global.AssureNav.switchView("surgical");
    }
    try {
      panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (_) {}
  };

  JDFCanvasManager.prototype.hideRevisionDiff = function () {
    var panel = document.getElementById("jdf-diff-panel");
    if (panel) panel.hidden = true;
    this._pendingDiff = null;
  };

  JDFCanvasManager.prototype.acceptRevisionDiff = function () {
    if (!this._pendingDiff) return;
    var self = this;
    if (this._pendingDiff.surgical && this._pendingDiff.payload) {
      this._applyRefinedPayload(this._pendingDiff.payload);
      var nid =
        this._pendingDiff.nodeId ||
        (this._pendingDiff.payload.node && this._pendingDiff.payload.node.id);
      this.saveDocument("MUTATION_ACCEPT", {
        target_node_id: nid,
        change_summary: "Accepted surgical refine",
      }).then(function () {
        self._liveTreeSnapshot = JSON.parse(JSON.stringify(self.tree || {}));
        self.loadRevisionHistory();
      });
      this.hideRevisionDiff();
      if (global.AssureToast) {
        global.AssureToast.show(
          jdfT("surgical.click.done", "Node updated. Math check re-run."),
          "success"
        );
      }
      return;
    }
    if (!this._pendingDiff.nodeId) {
      this.hideRevisionDiff();
      return;
    }
    var node = this.getNodeById(this._pendingDiff.nodeId);
    if (node) {
      node.content = this._pendingDiff.proposed;
      this._setDirty(true);
      this.render();
      this.saveDocument("REVISION_ACCEPT", { target_node_id: node.id });
    }
    this.hideRevisionDiff();
    this.livePreview = "";
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
      t.closest(".node-toolbar") ||
      t.closest("#jdf-node-menu")
    );
  };

  JDFCanvasManager.prototype.closeNodeMenu = function () {
    var menu = document.getElementById("jdf-node-menu");
    if (!menu) return;
    menu.hidden = true;
    menu.setAttribute("aria-hidden", "true");
    this._menuNodeId = null;
  };

  JDFCanvasManager.prototype.openNodeMenu = function (nodeId, clientX, clientY) {
    var menu = document.getElementById("jdf-node-menu");
    if (!menu || !nodeId) return;
    this._menuNodeId = nodeId;
    menu.hidden = false;
    menu.setAttribute("aria-hidden", "false");
    var pad = 8;
    var w = menu.offsetWidth || 220;
    var h = menu.offsetHeight || 160;
    var x = Math.min(Math.max(pad, clientX), window.innerWidth - w - pad);
    var y = Math.min(Math.max(pad, clientY), window.innerHeight - h - pad);
    menu.style.left = x + "px";
    menu.style.top = y + "px";
    this._menuOpenedAt = Date.now();
  };

  JDFCanvasManager.prototype._setInquiryIntent = function (text) {
    var intentEl = pickEl("inquiry-input", "jdf-intent");
    if (intentEl) intentEl.value = text || "";
  };

  JDFCanvasManager.prototype._runNodeMenuAction = function (act, nodeId) {
    var node = this.getNodeById(nodeId);
    if (!node || !act) return;
    if (act !== "edit" && this.isStreaming) {
      if (global.AssureToast) {
        global.AssureToast.show(jdfT("jdf.menu.busy", "Wait for the current compile or refine to finish."), "info");
      }
      return;
    }
    this.selectNodeForRefine(nodeId, { toast: act === "edit" });
    if (act === "edit") return;
    var intent;
    if (act === "revise") {
      intent = jdfT(
        "jdf.menu.revise_intent",
        "Revise this node. Keep locked numbers and neighboring sections consistent."
      );
      this._setInquiryIntent(intent);
      this.inquire(intent, true);
      return;
    }
    if (act === "reprompt") {
      intent = jdfT("jdf.menu.reprompt_intent", "Rewrite this node:\n{content}", {
        content: this._nodeText(node),
      });
      this._setInquiryIntent(intent);
      this.inquire(intent, true);
      return;
    }
    if (act === "revision") {
      intent = jdfT(
        "jdf.menu.revision_intent",
        "Propose a revised version of this node for comparison. Do not change locked facts."
      );
      this._setInquiryIntent(intent);
      this._pendingDiff = { nodeId: nodeId, original: this._nodeText(node), proposed: "" };
      if (global.AssureToast) {
        global.AssureToast.show(
          jdfT("jdf.menu.revision_toast", "Revision proposed — accept or discard the diff on the canvas."),
          "info"
        );
      }
      this.inquire(intent, true);
      return;
    }
    if (act === "redhat") {
      this.runRedhatAnalysis("node", nodeId);
      return;
    }
    if (act === "history") {
      this.openNodeHistoryModal(nodeId);
    }
  };

  JDFCanvasManager.prototype.openNodeHistoryModal = function (nodeId) {
    var self = this;
    var modal = document.getElementById("node-revision-modal");
    var list = document.getElementById("node-revision-list");
    var restoreBtn = document.getElementById("node-revision-restore-btn");
    if (!modal || !list || !nodeId) return;
    this._historyNodeId = nodeId;
    this._historyRevisionId = null;
    if (restoreBtn) restoreBtn.disabled = true;
    list.innerHTML = "";
    modal.hidden = false;
    fetch(
      "/api/projects/" +
        encodeURIComponent(this.projectId) +
        "/nodes/" +
        encodeURIComponent(nodeId) +
        "/history",
      { credentials: "same-origin" }
    )
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var rows = (data && data.revisions) || [];
        if (!rows.length) {
          list.innerHTML =
            '<li class="node-revision-empty">' +
            jdfT("jdf.node.history.empty", "No prior revisions for this node.") +
            "</li>";
          return;
        }
        rows.forEach(function (rev) {
          var li = document.createElement("li");
          li.className = "node-revision-item";
          li.dataset.revisionId = rev.revision_id;
          li.innerHTML =
            "<strong>v" +
            rev.version +
            "</strong> · " +
            (rev.preview || rev.change_summary || "") +
            '<span class="node-revision-when">' +
            (rev.created_at || "") +
            "</span>";
          li.addEventListener("click", function () {
            list.querySelectorAll(".node-revision-item").forEach(function (el) {
              el.classList.remove("is-selected");
            });
            li.classList.add("is-selected");
            self._historyRevisionId = rev.revision_id;
            if (restoreBtn) restoreBtn.disabled = !self._historyRevisionId;
          });
          list.appendChild(li);
        });
      })
      .catch(function () {
        list.innerHTML =
          '<li class="node-revision-empty">' +
          jdfT("jdf.node.history.error", "Could not load node history.") +
          "</li>";
      });
  };

  JDFCanvasManager.prototype.closeNodeHistoryModal = function () {
    var modal = document.getElementById("node-revision-modal");
    if (modal) modal.hidden = true;
    this._historyNodeId = null;
    this._historyRevisionId = null;
  };

  JDFCanvasManager.prototype.restoreSelectedNodeRevision = function () {
    var self = this;
    var nodeId = this._historyNodeId;
    var revisionId = this._historyRevisionId;
    if (!nodeId || !revisionId) return;
    fetch(
      "/api/projects/" +
        encodeURIComponent(this.projectId) +
        "/nodes/" +
        encodeURIComponent(nodeId) +
        "/restore",
      {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revision_id: revisionId }),
      }
    )
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data || data.ok === false) {
          throw new Error((data && data.error) || "restore failed");
        }
        if (data.document) {
          self.tree = data.document;
          self.render();
        }
        self.closeNodeHistoryModal();
        if (global.AssureToast) {
          global.AssureToast.show(
            jdfT("jdf.node.history.restored", "Node revision restored."),
            "success"
          );
        }
      })
      .catch(function (err) {
        if (global.AssureToast) {
          global.AssureToast.show(String(err.message || err), "error");
        }
      });
  };

  JDFCanvasManager.prototype._bindNodeMenu = function () {
    var self = this;
    if (this._nodeMenuBound) return;
    this._nodeMenuBound = true;
    var menu = document.getElementById("jdf-node-menu");
    if (menu) {
      menu.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-act]");
        if (!btn) return;
        var act = btn.getAttribute("data-act");
        var nodeId = self._menuNodeId;
        self.closeNodeMenu();
        self._runNodeMenuAction(act, nodeId);
      });
    }
    document.addEventListener("click", function (e) {
      if (e.button === 2) return;
      if (self._menuOpenedAt && Date.now() - self._menuOpenedAt < 250) return;
      if (e.target.closest && e.target.closest("#jdf-node-menu")) return;
      self.closeNodeMenu();
    });
    document.addEventListener("scroll", function () {
      self.closeNodeMenu();
    }, true);
  };

  JDFCanvasManager.prototype.selectNodeForRefine = function (nodeId, opts) {
    opts = opts || {};
    var node = this.getNodeById(nodeId);
    if (!node) return;
    this.surgicalTargetId = nodeId;
    if (!opts.skipViewSwitch) {
      if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
        global.AssureNav.switchView("surgical");
      } else if (global.AssureMode && typeof global.AssureMode.activate === "function") {
        global.AssureMode.activate("surgical");
      }
    }
    if (this.surgicalEl) this.surgicalEl.hidden = false;
    var targetEl = document.getElementById("active-target-id");
    if (targetEl) targetEl.textContent = nodeId;
    var intentEl = pickEl("inquiry-input", "jdf-intent");
    if (intentEl) {
      intentEl.focus();
      if (typeof intentEl.select === "function") intentEl.select();
    }
    var aperture = document.getElementById("aperture-indicator");
    if (aperture) aperture.hidden = false;
    if (this.rootEl) {
      this.rootEl.querySelectorAll(".jdf-node").forEach(function (el) {
        el.classList.toggle("selected", el.dataset.nodeId === nodeId);
        el.classList.toggle("node-target", el.dataset.nodeId === nodeId);
      });
    }
    if (!opts.skipRender && !(global.AssureTiptapEditor && global.AssureTiptapEditor.getEditor && global.AssureTiptapEditor.getEditor())) {
      this.render();
    }
    if (opts.toast !== false && global.AssureToast) {
      global.AssureToast.show(
        jdfT("jdf.refine.selected", "Node selected for refine."),
        "info"
      );
    }
    document.dispatchEvent(
      new CustomEvent("assure:jdf:selected", { detail: { nodeId: nodeId } })
    );
  };

  JDFCanvasManager.prototype._renderNodeBodyWithCitations = function (node, bodyEl) {
    var text = this._nodeText(node);
    var provList = this._normalizeProvenanceList(node);
    var provAttr = this._escapeHtml(JSON.stringify(provList.length ? provList[0] : {})).replace(/'/g, "&#39;");
    var html = this._escapeHtml(text);
    if (this.showCitations) {
      var ledger = this.tree.truth_ledger || {};
      var ledgerKeys = Object.keys(ledger);
      // Sort by value length descending so longer numbers match before shorter substrings
      ledgerKeys.sort(function (a, b) {
        return String(ledger[b]).length - String(ledger[a]).length;
      });
      var lockSourceName = provList.length && provList[0].source_name ? provList[0].source_name : "";
      ledgerKeys.forEach(function (key) {
        var val = String(ledger[key]);
        if (!val) return;
        var re = new RegExp(val.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g");
        var escapedKey = key.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
        html = html.replace(re, function (match) {
          var tip = "\uD83D\uDD12 " + escapedKey + " = " + match;
          if (lockSourceName) {
            tip += " \u00B7 " + jdfT("jdf.citation.source_name", "Source") + ": " + lockSourceName;
          }
          return (
            '<span class="truth-pill inline-citation interactive-element truth-pass lock-glyph-host" ' +
            'data-provenance="' + provAttr + '" ' +
            'data-lock-key="' + escapedKey + '" ' +
            'title="' + tip.replace(/"/g, "&quot;") + '">' +
            match +
            '<span class="lock-glyph" aria-hidden="true">\uD83D\uDD12</span>' +
            '</span>'
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
      '<button type="button" data-act="edit">' +
      jdfT("jdf.toolbar.edit", "Edit") +
      "</button>" +
      '<button type="button" data-act="up" aria-label="' +
      jdfT("jdf.toolbar.up", "Move up") +
      '">▲</button>' +
      '<button type="button" data-act="down" aria-label="' +
      jdfT("jdf.toolbar.down", "Move down") +
      '">▼</button>' +
      '<button type="button" data-act="del">' +
      jdfT("jdf.toolbar.delete", "Delete") +
      "</button>";
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

  JDFCanvasManager.prototype.setDraftPreview = function (previewTree) {
    if (!this.rootEl) return;
    if (this._previewSavedTree === undefined) {
      this._previewSavedTree = this.tree;
    }
    this.tree = previewTree;
    this.rootEl.classList.add("is-draft-preview");
    this.render();
  };

  JDFCanvasManager.prototype.clearDraftPreview = function () {
    if (!this.rootEl) return;
    if (this._previewSavedTree !== undefined) {
      this.tree = this._previewSavedTree;
      this._previewSavedTree = undefined;
    }
    this.rootEl.classList.remove("is-draft-preview");
    this.render();
  };

  /** Canvas → left-pane cross-highlight.
   * Uses event delegation on rootEl so it survives render() rebuilds.
   * Bound once from bind(); idempotent. */
  JDFCanvasManager.prototype._bindCrossPaneLinks = function () {
    var rootEl = this.rootEl;
    if (!rootEl || this._crossPaneBound) return;
    this._crossPaneBound = true;

    var lastNode = null;

    function collectKeys(node) {
      var keys = [];
      node.querySelectorAll("[data-lock-key]").forEach(function (el) {
        var k = el.dataset.lockKey;
        if (k && keys.indexOf(k) < 0) keys.push(k);
      });
      return keys;
    }

    function clearChecklistHighlights() {
      var cl = document.getElementById("generate-lock-checklist");
      if (cl) cl.querySelectorAll(".cross-highlight").forEach(function (r) { r.classList.remove("cross-highlight"); });
    }

    function applyChecklistHighlights(keys) {
      var cl = document.getElementById("generate-lock-checklist");
      if (!cl || !keys.length) return;
      cl.querySelectorAll(".lock-check-row[data-lock-key]").forEach(function (row) {
        if (keys.indexOf(row.dataset.lockKey) >= 0) row.classList.add("cross-highlight");
      });
    }

    rootEl.addEventListener("mouseover", function (e) {
      var node = e.target.closest && e.target.closest(".jdf-node");
      if (node === lastNode) return;
      clearChecklistHighlights();
      lastNode = node;
      if (node) applyChecklistHighlights(collectKeys(node));
    });

    rootEl.addEventListener("mouseleave", function () {
      clearChecklistHighlights();
      lastNode = null;
    });

    /* Touch: tap a node → highlight its checklist rows; tap anywhere else → clear. */
    rootEl.addEventListener("touchstart", function (e) {
      var node = e.target.closest && e.target.closest(".jdf-node");
      clearChecklistHighlights();
      lastNode = node || null;
      if (node) applyChecklistHighlights(collectKeys(node));
    }, { passive: true });

    document.addEventListener("touchstart", function (e) {
      if (!rootEl.contains(e.target)) {
        clearChecklistHighlights();
        lastNode = null;
      }
    }, { passive: true });
  };

  /** Substrate Vault → canvas: clicking a vault file highlights every node
   * whose provenance cites it (matched on provenance.source_id). */
  JDFCanvasManager.prototype._bindSubstrateFocus = function () {
    var self = this;
    if (this._substrateFocusBound) return;
    this._substrateFocusBound = true;
    document.addEventListener("assure:substrate:focus", function (e) {
      var fileId = e.detail && e.detail.fileId;
      var rootEl = self.rootEl;
      if (!rootEl) return;
      var nodes = rootEl.querySelectorAll(".jdf-node");
      var firstMatch = null;
      nodes.forEach(function (el) {
        var node = self.getNodeById(el.dataset.nodeId);
        var provList = node ? self._normalizeProvenanceList(node) : [];
        var matches = !!fileId && provList.some(function (p) {
          return p.source_id === fileId;
        });
        el.classList.toggle("provenance-highlight", matches);
        if (matches && !firstMatch) firstMatch = el;
      });
      if (firstMatch && typeof firstMatch.scrollIntoView === "function") {
        firstMatch.scrollIntoView({ behavior: "smooth", block: "center" });
      } else if (fileId && global.AssureToast) {
        global.AssureToast.show(
          jdfT("substrate.vault.no_claims_yet", "No claims from this file yet — compile a document to ground it."),
          "info"
        );
      }
    });
  };

  JDFCanvasManager.prototype.setAllGutterState = function (state) {
    var gutters = (this.rootEl || document).querySelectorAll(".verification-gutter");
    gutters.forEach(function (g) {
      g.className = "verification-gutter " + (state || "");
    });
  };

  JDFCanvasManager.prototype._applyRefinedPayload = function (payload) {
    if (!payload) return;
    if (payload.document) {
      this.tree = sanitizeJDFDocument(payload.document);
    } else if (payload.node && payload.node.id) {
      var spliced = JSON.parse(JSON.stringify(this.tree || {}));
      var nid = payload.node.id;
      var replaced = false;
      (spliced.body || []).forEach(function (section) {
        if (section && section.id === nid) {
          Object.assign(section, payload.node);
          replaced = true;
        }
        (section.children || []).forEach(function (child, i) {
          if (child && child.id === nid) {
            section.children[i] = payload.node;
            replaced = true;
          }
        });
      });
      if (replaced) this.tree = sanitizeJDFDocument(spliced);
    }
    var spans =
      payload.confidenceSpans ||
      payload.confidence_spans ||
      (this.tree.meta && this.tree.meta.confidenceSpans) ||
      [];
    this.setConfidenceSpans(spans, { render: false });
    this.render();
    if (global.AssureProjectFileManager && typeof global.AssureProjectFileManager.saveCompiled === "function") {
      global.AssureProjectFileManager.saveCompiled(this.tree).catch(function () {});
    }
    if (global.compiledDocument) {
      global.compiledDocument = this.tree;
    }
    if (global.AssureGenerate) {
      global.AssureGenerate.compiledDocument = this.tree;
    }
  };

  JDFCanvasManager.prototype.applyRefinedNode = function (payload) {
    var self = this;
    if (!payload) return;
    this._applyRefinedPayload(payload);
    if (this.saveDocument) {
      this.saveDocument("surgical_refine", { target_node_id: payload.node && payload.node.id }).catch(
        function () {}
      );
    }
  };

  JDFCanvasManager.prototype.patchGutterFromRedhat = function (critiques) {
    (critiques || []).forEach(function (c) {
      var nodeId = c.target_node_id || c.node_id;
      if (!nodeId) return;
      var g = document.querySelector('.verification-gutter[data-node-id="' + nodeId + '"]');
      if (g) g.className = "verification-gutter warning";
    });
  };

  JDFCanvasManager.prototype.setShowConfidenceOverlay = function (on) {
    this.showConfidenceOverlay = !!on;
    try {
      global.localStorage.setItem("assure_confidence_overlay", this.showConfidenceOverlay ? "1" : "0");
    } catch (_) {}
    var toggle = document.getElementById("confidence-overlay-toggle");
    if (toggle) toggle.checked = this.showConfidenceOverlay;
    if (global.AssureTiptapEditor && typeof global.AssureTiptapEditor.setOverlayEnabled === "function") {
      global.AssureTiptapEditor.setOverlayEnabled(this.showConfidenceOverlay);
    }
    this.render();
  };

  JDFCanvasManager.prototype.renderAuditAppendix = function (claims, opts) {
    var wrap = document.getElementById("jdf-audit-appendix");
    var body = document.getElementById("jdf-audit-appendix-body");
    if (!wrap || !body) return;
    opts = opts || {};
    if (!claims) {
      wrap.hidden = true;
      body.innerHTML = "";
      return;
    }
    wrap.hidden = false;
    body.innerHTML = "";
    function tt(key, fallback) {
      return jdfT(key, fallback);
    }
    if (!claims.length) {
      var empty = document.createElement("p");
      empty.className = "jdf-audit-appendix-empty";
      empty.textContent = opts.pending
        ? tt("generate.audit_manifest.pending", "Waiting for Z3 and Red-Hat…")
        : tt("generate.audit_manifest.empty", "No claims yet.");
      body.appendChild(empty);
      return;
    }
    var table = document.createElement("table");
    table.className = "jdf-audit-appendix-table";
    var thead = document.createElement("thead");
    var headRow = document.createElement("tr");
    ["generate.audit_manifest.claim", "generate.audit_manifest.z3", "generate.audit_manifest.redhat"].forEach(function (key, i) {
      var th = document.createElement("th");
      th.textContent = tt(key, ["Claim", "Z3 score", "Red-Hat critique"][i]);
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    claims.forEach(function (row) {
      var tr = document.createElement("tr");
      var claimTd = document.createElement("td");
      claimTd.textContent = row.claim || "";
      var scoreTd = document.createElement("td");
      var score = row.z3Score != null ? row.z3Score : row.z3_score;
      scoreTd.textContent =
        score == null
          ? tt("generate.audit_manifest.pending_z3", "Waiting for Z3…")
          : String(Math.round(Number(score) * 100) / 100);
      var rhTd = document.createElement("td");
      var critique = row.redhatCritique || row.redhat_critique || "";
      rhTd.textContent =
        critique ||
        (opts.pending
          ? tt("generate.audit_manifest.pending_redhat", "Waiting for Red-Hat…")
          : "—");
      tr.appendChild(claimTd);
      tr.appendChild(scoreTd);
      tr.appendChild(rhTd);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    body.appendChild(table);
  };

  JDFCanvasManager.prototype.setConfidenceSpans = function (spans, options) {
    this.confidenceSpans = Array.isArray(spans) ? spans : [];
    global._assureConfidenceSpans = this.confidenceSpans;
    if (this.tree) {
      this.tree.meta = this.tree.meta || {};
      this.tree.meta.confidenceSpans = this.confidenceSpans;
    }
    if (global.AssureTiptapEditor && typeof global.AssureTiptapEditor.setConfidenceSpans === "function") {
      global.AssureTiptapEditor.setConfidenceSpans(this.confidenceSpans);
    }
    if (!(options && options.render === false)) {
      this.render();
    }
  };

  JDFCanvasManager.prototype._renderCacheBadge = function (node, hostEl) {
    if (!hostEl || !node || !node.meta || !node.meta.cache_hit) return;
    var badge = document.createElement("span");
    badge.className =
      "inline-flex items-center text-xs text-yellow-600 bg-yellow-50 px-1.5 py-0.5 rounded-full ml-2 assure-cache-badge";
    badge.setAttribute(
      "title",
      jdfT("generate.cache_badge_tip", "Loaded from memory")
    );
    badge.textContent = jdfT("generate.cache_badge", "⚡ Cached");
    hostEl.appendChild(badge);
  };

  JDFCanvasManager.prototype._confidenceSpansForNode = function (node) {
    var id = node && node.id;
    var spans = this.confidenceSpans || [];
    if (!spans.length && this.tree && this.tree.meta) {
      spans = this.tree.meta.confidenceSpans || [];
    }
    if (!id) return spans.slice();
    return spans.filter(function (span) {
      var nid = span.nodeId || span.node_id;
      return !nid || nid === id;
    });
  };

  JDFCanvasManager.prototype._confidenceSpansForRange = function (node, start, end) {
    return this._confidenceSpansForNode(node)
      .map(function (span) {
        var a = Number(span.startChar != null ? span.startChar : span.start);
        var b = Number(span.endChar != null ? span.endChar : span.end);
        var from = Math.max(a, start);
        var to = Math.min(b, end);
        if (!(to > from)) return null;
        return Object.assign({}, span, { startChar: from - start, endChar: to - start });
      })
      .filter(Boolean);
  };

  JDFCanvasManager.prototype._paintConfidence = function (el, text, spans) {
    var highlighter = global.AssureConfidenceHighlighter;
    if (highlighter && typeof highlighter.paint === "function") {
      highlighter.paint(el, text, spans, this.showConfidenceOverlay);
      return;
    }
    el.textContent = text == null ? "" : String(text);
  };

  JDFCanvasManager.prototype._listLikeLines = function (text) {
    var lines = String(text || "").split(/\n/);
    var nonempty = lines.filter(function (line) {
      return line.trim();
    });
    if (!nonempty.length) return null;
    var bullet = /^\s*(?:[-*+]|\d+[.)])\s+/;
    if (
      !nonempty.every(function (line) {
        return bullet.test(line);
      })
    ) {
      return null;
    }
    return nonempty.map(function (line) {
      return line.replace(bullet, "").trim();
    });
  };

  JDFCanvasManager.prototype._renderAstInner = function (node) {
    var type = (node && node.type) || "paragraph";
    var listItems = null;
    if (type === "list") {
      listItems = (node.items || node.children || []).map(function (item) {
        return typeof item === "string" ? item : item.content || item.text || "";
      });
    } else if (type === "paragraph") {
      listItems = this._listLikeLines(node.content);
    }
    if (type === "heading" || type === "section") {
      var heading = document.createElement("h2");
      var headingText = node.title || node.content || "";
      this._paintConfidence(heading, headingText, this._confidenceSpansForNode(node));
      return heading;
    }
    if (listItems) {
      var ul = document.createElement("ul");
      var raw = String(node.content || "");
      var cursor = 0;
      var self = this;
      listItems.forEach(function (item) {
        var li = document.createElement("li");
        var at = raw.indexOf(item, cursor);
        if (at < 0) at = raw.indexOf(item);
        var sliceSpans =
          at >= 0
            ? self._confidenceSpansForRange(node, at, at + item.length)
            : self._confidenceSpansForNode(node);
        if (at >= 0) cursor = at + item.length;
        self._paintConfidence(li, item, sliceSpans);
        ul.appendChild(li);
      });
      return ul;
    }
    if type === "table" && node.rows && node.rows.length) {
      var table = document.createElement("table");
      node.rows.forEach(function (row) {
        var tr = document.createElement("tr");
        (row.cells || row || []).forEach(function (cell) {
          var td = document.createElement("td");
          td.textContent = typeof cell === "string" ? cell : cell.text || cell.content || "";
          tr.appendChild(td);
        });
        table.appendChild(tr);
      });
      return table;
    }
    if (type === "image" && node.src) {
      var figure = document.createElement("figure");
      figure.className = "jdf-node-image";
      var img = document.createElement("img");
      img.src = node.src;
      img.alt = node.alt || "";
      img.loading = "lazy";
      figure.appendChild(img);
      if (node.caption) {
        var cap = document.createElement("figcaption");
        cap.textContent = node.caption;
        figure.appendChild(cap);
      }
      return figure;
    }
    var p = document.createElement("p");
    var paraText = node.content || node.title || "";
    this._paintConfidence(p, paraText, this._confidenceSpansForNode(node));
    return p;
  };

  JDFCanvasManager.prototype._wrapAstDetails = function (summaryText, innerEl, className) {
    var details = document.createElement("details");
    details.className = className || "jdf-ast-accordion";
    details.open = true;
    var summary = document.createElement("summary");
    summary.textContent = summaryText || "";
    details.appendChild(summary);
    details.appendChild(innerEl);
    return details;
  };

  JDFCanvasManager.prototype.renderCompiledAstAccordion = function (tree) {
    var self = this;
    var tiptap = global.AssureTiptapEditor;
    if (tiptap && typeof tiptap.destroy === "function") {
      try {
        tiptap.destroy();
      } catch (_) {}
    }
    this.rootEl.classList.remove("is-tiptap");
    this.rootEl.innerHTML = "";
    var title = document.createElement("h2");
    title.className = "jdf-doc-title";
    title.textContent = (tree.meta && tree.meta.title) || jdfT("generate.draft_preview_title", "Draft Preview");
    this.rootEl.appendChild(title);
    var body = (tree && tree.body) || [];
    if (!body.length) {
      this.rootEl.appendChild(this.renderEmptyCanvas());
      return;
    }
    body.forEach(function (section) {
      var sectionDetails = document.createElement("details");
      sectionDetails.className = "jdf-ast-accordion jdf-ast-section";
      sectionDetails.open = true;
      if (section.id) sectionDetails.dataset.nodeId = section.id;
      sectionDetails.classList.add("jdf-ast-hit");
      var sectionSummary = document.createElement("summary");
      var h2 = document.createElement("h2");
      var headingLabel = section.title || jdfT("generate.ast.heading", "Heading");
      self._paintConfidence(h2, headingLabel, self._confidenceSpansForNode(section));
      sectionSummary.appendChild(h2);
      self._renderCacheBadge(section, sectionSummary);
      sectionDetails.appendChild(sectionSummary);
      (section.children || []).forEach(function (node) {
        var type = node.type || "paragraph";
        var labelKey =
          type === "callout"
            ? "generate.ast.callout"
            : type === "table"
              ? "generate.ast.table"
              : type === "list" || self._listLikeLines(node.content)
                ? "generate.ast.list"
                : type === "heading"
                  ? "generate.ast.heading"
                  : "generate.ast.paragraph";
        var fallbacks = {
          "generate.ast.callout": "Callout",
          "generate.ast.table": "Table",
          "generate.ast.list": "List",
          "generate.ast.heading": "Heading",
          "generate.ast.paragraph": "Paragraph",
        };
        var snippet = (self._nodeText(node) || "").trim().slice(0, 80);
        var summaryText = snippet || jdfT(labelKey, fallbacks[labelKey] || type);
        var inner = self._renderAstInner(node);
        var wrap = self._wrapAstDetails(summaryText, inner, "jdf-ast-accordion jdf-ast-node");
        if (node.id) wrap.dataset.nodeId = node.id;
        wrap.classList.add("jdf-ast-hit");
        var wrapSummary = wrap.querySelector("summary");
        if (wrapSummary) self._renderCacheBadge(node, wrapSummary);
        sectionDetails.appendChild(wrap);
      });
      self.rootEl.appendChild(sectionDetails);
    });
  };

  JDFCanvasManager.prototype.render = function (options) {
    if (!this.rootEl) return;
    var self = this;
    options = options || {};
    var animate = !!options.animate || this._animateNextRender;
    this._animateNextRender = false;
    var nodeIndex = 0;
    var isPreview = this.rootEl.classList.contains("is-draft-preview");
    this.rootEl.classList.toggle("hide-citations", !this.showCitations);
    this.rootEl.classList.toggle("confidence-overlay-off", !this.showConfidenceOverlay);
    var overlayWrap = document.getElementById("confidence-overlay-wrap");
    if (overlayWrap) {
      overlayWrap.hidden = !(this.confidenceSpans && this.confidenceSpans.length);
    }
    var tiptap = global.AssureTiptapEditor;
    if (isPreview) {
      this.renderCompiledAstAccordion(this.tree);
      this._emitJdfRendered();
      return;
    }
    if (tiptap && typeof tiptap.mount === "function" && global.AssureTiptap) {
      try {
        tiptap.mount({
          rootEl: this.rootEl,
          tree: this.tree,
          canvas: this,
          editable: !isPreview,
        });
        if (this.previewEl) {
          this.previewEl.textContent = this.livePreview
            ? this.livePreview
            : jdfT("jdf.preview.empty", "Streaming output will appear here…");
        }
        this._emitJdfRendered();
        return;
      } catch (err) {
        if (typeof console !== "undefined" && console.warn) {
          console.warn("TipTap mount failed; using DOM renderer", err);
        }
        if (tiptap.destroy) tiptap.destroy();
        this.rootEl.classList.remove("is-tiptap");
      }
    }
    this.rootEl.innerHTML = "";
    var title = document.createElement("h2");
    title.className = "jdf-doc-title";
    title.textContent = (this.tree.meta && this.tree.meta.title) || "Untitled";
    if (!isPreview) {
      title.contentEditable = "true";
      title.addEventListener("blur", function () {
        self.tree.meta = self.tree.meta || {};
        self.tree.meta.title = title.textContent.trim();
        self._setDirty(true);
      });
    }
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
          if (self.rootEl && self.rootEl.classList.contains("is-draft-preview")) return;
          if (self._isInteractiveNodeClick(e)) return;
          var id = article.dataset.nodeId;
          if (id) self.selectNodeForRefine(id, { toast: true });
        });
        article.addEventListener("contextmenu", function (e) {
          if (self.rootEl && self.rootEl.classList.contains("is-draft-preview")) return;
          if (self._isInteractiveNodeClick(e)) return;
          e.preventDefault();
          e.stopPropagation();
          var id = article.dataset.nodeId;
          if (id) self.openNodeMenu(id, e.clientX, e.clientY);
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
            if (self.rootEl && self.rootEl.classList.contains("is-draft-preview")) return;
            body.contentEditable = "true";
            body.focus();
          });
          body.addEventListener("blur", function () {
            if (self.rootEl && self.rootEl.classList.contains("is-draft-preview")) return;
            body.contentEditable = "false";
            node.content = body.textContent;
            self._setDirty(true);
            self.saveDocument("MANUAL_TOUCHUP", { target_node_id: node.id });
            self._renderNodeBodyWithCitations(node, body);
          });
        }
        article.appendChild(self._toolbar(node, sIdx, cIdx));
        var cacheHeader = document.createElement("div");
        cacheHeader.className = "jdf-node-cache-header";
        self._renderCacheBadge(node, cacheHeader);
        if (cacheHeader.childNodes.length) {
          article.insertBefore(cacheHeader, article.firstChild);
        }
        article.appendChild(body);
        if (self.showCitations) {
          self._renderCitationBadge(node, article);
        }
        // Verification gutter — 4px left bar communicating node state
        var gutter = document.createElement("div");
        gutter.className = "verification-gutter";
        gutter.setAttribute("data-node-id", node.id);
        gutter.classList.add(self.computeNodeStatus(node));
        article.appendChild(gutter);
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
    this._emitJdfRendered();
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

  /** On-demand Red-Hat analysis over the docked canvas — the surgical-view
   * counterpart to Generate's opt-in "Run Stress Test" prompt. scope is
   * "node" (a single selected node, via the context menu) or "full" (the
   * whole docked document, via the toolbar button). Reuses the same
   * /draft/redhat/stream pipeline; findings are shown in #redhat-findings
   * and persisted onto the canvas by node id. */
  JDFCanvasManager.prototype.runRedhatAnalysis = function (scope, nodeId) {
    var self = this;
    if (this.isStreaming) {
      if (global.AssureToast) {
        global.AssureToast.show(jdfT("jdf.menu.busy", "Wait for the current compile or refine to finish."), "info");
      }
      return;
    }

    var targetNodeId = null;
    var text;
    if (scope === "node") {
      var node = this.getNodeById(nodeId);
      if (!node) return;
      targetNodeId = nodeId;
      text = this._nodeText(node);
    } else {
      text = this._flattenPreview();
    }
    if (!text || !text.trim()) {
      if (global.AssureToast) {
        global.AssureToast.show(jdfT("jdf.redhat.empty", "Nothing to analyze yet."), "error");
      }
      return;
    }

    var fullBtn = document.getElementById("redhat-analyze-full-btn");
    if (scope === "full" && fullBtn) {
      fullBtn.disabled = true;
      fullBtn.classList.add("is-busy");
    }
    this._syncCompilerStatus("processing", jdfT("audit.progress.redhat", "Running stress test…"));
    if (global.AssureToast) {
      global.AssureToast.show(jdfT("audit.progress.redhat", "Running stress test…"), "info");
    }

    var url = "/api/projects/" + encodeURIComponent(this.projectId) + "/draft/redhat/stream";
    var body = { draft_text: text, document: sanitizeJDFDocument(this.tree), target_node_id: targetNodeId };
    var postStream =
      global.AssureSse && typeof global.AssureSse.postStream === "function"
        ? global.AssureSse.postStream
        : null;

    function finish() {
      if (scope === "full" && fullBtn) {
        fullBtn.disabled = false;
        fullBtn.classList.remove("is-busy");
      }
    }

    function handleFrame(frame) {
      var data = frame.data || {};
      var type = data.type || frame.event || "message";

      if (type === "audit_complete") {
        finish();
        var critiques = data.redhat_critiques || [];
        if (!critiques.length) {
          self._syncCompilerStatus("verified");
          if (global.AssureToast) {
            global.AssureToast.show(jdfT("jdf.redhat.none", "No issues found."), "success");
          }
          return;
        }
        critiques.forEach(function (c) {
          self._showRedhatFinding((c.title ? c.title + ": " : "") + (c.content || ""));
        });
        self._syncCompilerStatus("issues");
        if (data.document) self.tree = data.document;
        self.saveDocument("REDHAT_ANALYSIS", targetNodeId ? { target_node_id: targetNodeId } : {})
          .then(function () {
            self.render();
          })
          .catch(function () {
            self.render();
          });
        return;
      }
      if (type === "error" || (type === "complete" && data.ok === false)) {
        finish();
        self._syncCompilerStatus("idle");
        if (global.AssureToast) {
          global.AssureToast.show(
            String(data.error || jdfT("generate.failed", "Stress Test failed.")),
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
        idleTimeoutMs: 90000,
        parseBuffer: parseSseChunk,
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
            var parsed = parseSseChunk(buffer);
            buffer = parsed.remainder;
            parsed.events.forEach(handleFrame);
            return pump();
          });
        }
        return pump();
      });
    }

    streamPromise.catch(function (err) {
      finish();
      self._syncCompilerStatus("idle");
      if (global.AssureToast) {
        global.AssureToast.show(String((err && err.message) || err), "error");
      }
    });
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
    if (!host && this.surgicalTargetId) {
      host = this.rootEl.querySelector('[data-node-id="' + this.surgicalTargetId + '"]');
    }
    if (!host) {
      host = this.rootEl.querySelector(".jdf-node.selected") || this.rootEl.querySelector(".jdf-node");
    }
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
    // TipTap owns the node DOM; inserting beside a node view is stripped on the next transaction.
    var tiptapHost = this.rootEl.querySelector(".jdf-tiptap-host");
    if (tiptapHost) {
      tiptapHost.insertAdjacentElement("afterend", card);
    } else {
      host.insertAdjacentElement("afterend", card);
    }
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
          if (self._pendingDiff) {
            self._pendingDiff.proposed = self.livePreview || "";
            self.showRevisionDiff(self._pendingDiff.original, self._pendingDiff.proposed);
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
        if (ev === "error") {
          self.clearVerifyTimeout();
          self._setStreaming(false);
          self.setPreviewSkeleton(false);
          if (self.stopBtn) self.stopBtn.style.display = "none";
          self.setSavePill("saved", "jdf.save.stream_complete");
          self._syncCompilerStatus("idle");
          if (global.AssureToast) {
            global.AssureToast.show(
              String((data && data.error) || jdfT("generate.failed", "Compilation failed.")),
              "error"
            );
          }
        }
      },
    });
    return this.streamClient.start({
      user_intent: intent,
      target_node_id: null,
      run_redhat: runRedhat !== false,
      document: sanitizeJDFDocument(this.tree),
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
      document: sanitizeJDFDocument(this.tree),
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
    var redhatFullBtn = document.getElementById("redhat-analyze-full-btn");
    if (redhatFullBtn) {
      redhatFullBtn.addEventListener("click", function () {
        self.runRedhatAnalysis("full", null);
      });
    }
    var dupBtn = document.getElementById("generate-duplicate-node");
    if (dupBtn) dupBtn.addEventListener("click", function () { self.duplicateNode(); });
    var overlayToggle = document.getElementById("confidence-overlay-toggle");
    if (overlayToggle && !overlayToggle.dataset.bound) {
      overlayToggle.dataset.bound = "1";
      overlayToggle.checked = self.showConfidenceOverlay;
      overlayToggle.addEventListener("change", function () {
        self.setShowConfidenceOverlay(overlayToggle.checked);
      });
    }
    var splitBtn = document.getElementById("generate-split-section");
    if (splitBtn) splitBtn.addEventListener("click", function () { self.splitSectionAtNode(); });
    var mergeBtn = document.getElementById("generate-merge-next");
    if (mergeBtn) mergeBtn.addEventListener("click", function () { self.mergeWithNext(); });
    var diffAccept = document.getElementById("jdf-diff-accept");
    if (diffAccept) diffAccept.addEventListener("click", function () { self.acceptRevisionDiff(); });
    var diffReject = document.getElementById("jdf-diff-reject");
    if (diffReject) diffReject.addEventListener("click", function () { self.hideRevisionDiff(); });
    var versionSlider = document.getElementById("version-history-slider");
    if (versionSlider && !versionSlider.dataset.bound) {
      versionSlider.dataset.bound = "1";
      versionSlider.addEventListener("input", function () {
        self.loadJdfVersion(parseInt(versionSlider.value, 10));
      });
    }
    var versionRestore = document.getElementById("version-restore-btn");
    if (versionRestore && !versionRestore.dataset.bound) {
      versionRestore.dataset.bound = "1";
      versionRestore.addEventListener("click", function () {
        self.restoreVersionPreview().catch(function (err) {
          if (global.AssureToast) global.AssureToast.show(String(err.message || err), "error");
        });
      });
    }
    this._bindNodeMenu();
    this._bindCrossPaneLinks();
    this._bindSubstrateFocus();
    var nodeRevClose = document.getElementById("node-revision-close");
    var nodeRevBackdrop = document.querySelector("#node-revision-modal .node-revision-backdrop");
    var nodeRevRestore = document.getElementById("node-revision-restore-btn");
    function closeNodeRevModal() {
      var modal = document.getElementById("node-revision-modal");
      if (modal) modal.hidden = true;
    }
    if (nodeRevClose) nodeRevClose.addEventListener("click", closeNodeRevModal);
    if (nodeRevBackdrop) nodeRevBackdrop.addEventListener("click", closeNodeRevModal);
    if (nodeRevRestore) {
      nodeRevRestore.addEventListener("click", function () {
        self.restoreSelectedNodeRevision().catch(function (err) {
          if (global.AssureToast) global.AssureToast.show(String(err.message || err), "error");
        });
      });
    }
    var importPdfBtn = document.getElementById("jdf-import-pdf-btn");
    var importPdfInput = document.getElementById("jdf-import-pdf-input");
    if (importPdfBtn && importPdfInput && !importPdfBtn.dataset.bound) {
      importPdfBtn.dataset.bound = "1";
      importPdfBtn.addEventListener("click", function () { importPdfInput.click(); });
      importPdfInput.addEventListener("change", function () {
        var file = importPdfInput.files && importPdfInput.files[0];
        if (!file) return;
        var fd = new FormData();
        fd.append("file", file);
        fetch("/api/projects/" + encodeURIComponent(self.projectId) + "/import-pdf", {
          method: "POST",
          body: fd,
        })
          .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
          .then(function (res) {
            if (!res.ok) throw new Error((res.j && res.j.detail) || "Import failed");
            if (res.j.document) {
              self.tree = res.j.document;
              self.render();
              self.saveDocument("PDF_IMPORT");
            }
            if (global.AssureToast) {
              global.AssureToast.show(jdfT("jdf.import.pdf_ok", "PDF imported"), "success");
            }
          })
          .catch(function (err) {
            if (global.AssureToast) global.AssureToast.show(String(err.message || err), "error");
          })
          .finally(function () { importPdfInput.value = ""; });
      });
    }
    window.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        var menu = document.getElementById("jdf-node-menu");
        if (menu && !menu.hidden) {
          self.closeNodeMenu();
          e.preventDefault();
          return;
        }
        self.exitSurgicalMode();
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        if (document.activeElement && document.activeElement.contentEditable === "true") {
          document.activeElement.blur();
        } else if (inquireBtn) {
          inquireBtn.click();
        }
      }
    });

    if (global.AssureProjectFileManager && typeof global.AssureProjectFileManager.remember === "function") {
      global.AssureProjectFileManager.remember(this.projectId);
    }
    this.loadProject();
    this.setSavePill("idle", "jdf.save.unsaved");
  };

  global.JDFCanvasManager = JDFCanvasManager;
  global.InquireStreamClient = InquireStreamClient;
  global.computeWordDiff = computeWordDiff;
  global.sanitizeJDFNode = sanitizeJDFNode;
  global.sanitizeJDFDocument = sanitizeJDFDocument;
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
