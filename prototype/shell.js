(function () {
  "use strict";

  var STAGE_ORDER = [
    "Preflight",
    "Drafting",
    "Lock Inference",
    "Compile",
    "Math Check",
    "Verify",
    "Complete",
  ];
  var STORAGE_KEY = "assure_project";
  var DRAFT_TYPE = "full";

  document.addEventListener("DOMContentLoaded", function () {
    var body = document.body;
    var docSurface = document.querySelector(".doc-surface");
    var docEmpty = docSurface ? docSurface.querySelector(".empty-hero") : null;
    var wrap = document.getElementById("dock-input-wrap");
    var text = document.getElementById("dock-text");
    var submit = document.getElementById("dock-submit");

    // ---------------------------------------------------------------
    // Left / right pane toggles (from phase 1)
    // ---------------------------------------------------------------
    var leftCollapse = document.getElementById("left-collapse");
    if (leftCollapse) {
      leftCollapse.addEventListener("click", function () {
        body.classList.toggle("collapsed");
      });
    }
    var rightClose = document.getElementById("right-close");
    function openRight() { body.classList.remove("right-hidden"); }
    function closeRight() { body.classList.add("right-hidden"); }
    if (rightClose) rightClose.addEventListener("click", closeRight);

    document.querySelectorAll("[data-rail-btn]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var kind = btn.getAttribute("data-rail-btn");
        if (kind === "activity") openRight();
        else if (kind === "history") body.classList.toggle("collapsed");
      });
    });

    // ---------------------------------------------------------------
    // Theme toggle (light default / dark override)
    // ---------------------------------------------------------------
    var THEME_KEY = "assure_theme";
    var htmlEl = document.documentElement;
    var themeToggle = document.getElementById("theme-toggle");
    var iconSun  = themeToggle ? themeToggle.querySelector(".theme-icon-sun")  : null;
    var iconMoon = themeToggle ? themeToggle.querySelector(".theme-icon-moon") : null;
    function applyTheme(name) {
      var isDark = (name === "dark");
      htmlEl.setAttribute("data-theme", isDark ? "dark" : "light");
      if (themeToggle) themeToggle.setAttribute("data-theme", isDark ? "dark" : "light");
      if (iconSun)  iconSun.style.display  = isDark ? "none" : "";
      if (iconMoon) iconMoon.style.display = isDark ? ""     : "none";
    }
    (function initTheme() {
      var stored = null;
      try { stored = window.localStorage.getItem(THEME_KEY); } catch (_) { stored = null; }
      applyTheme(stored === "dark" ? "dark" : "light");
    })();
    if (themeToggle) {
      themeToggle.addEventListener("click", function () {
        var cur = htmlEl.getAttribute("data-theme") === "dark" ? "dark" : "light";
        var next = (cur === "dark") ? "light" : "dark";
        try { window.localStorage.setItem(THEME_KEY, next); } catch (_) {}
        applyTheme(next);
      });
    }

    // ---------------------------------------------------------------
    // Docked input focus styles (phase 1)
    // ---------------------------------------------------------------
    if (wrap && text) {
      text.addEventListener("focus", function () { wrap.classList.add("focused"); });
      text.addEventListener("blur", function () { wrap.classList.remove("focused"); });
    }

    // ---------------------------------------------------------------
    // Stage state helpers
    // ---------------------------------------------------------------
    function stageRow(name) {
      return document.querySelector('.stage-row[data-stage="' + name + '"]');
    }
    function resetStages() {
      STAGE_ORDER.forEach(function (n) {
        var r = stageRow(n);
        if (!r) return;
        r.classList.remove("active", "done", "failed");
      });
    }
    function findActiveStage() {
      for (var i = 0; i < STAGE_ORDER.length; i++) {
        var r = stageRow(STAGE_ORDER[i]);
        if (r && r.classList.contains("active")) return STAGE_ORDER[i];
      }
      return null;
    }
    function markDone(name) {
      var r = stageRow(name);
      if (!r) return;
      r.classList.remove("active", "failed");
      r.classList.add("done");
    }
    function markActive(name) {
      var r = stageRow(name);
      if (!r) return;
      r.classList.remove("done", "failed");
      r.classList.add("active");
    }
    function markFailed(name) {
      var r = stageRow(name);
      if (!r) return;
      r.classList.remove("active", "done");
      r.classList.add("failed");
    }

    // ---------------------------------------------------------------
    // Document area helpers
    // ---------------------------------------------------------------
    var draftEl = null;
    var currentJdfDocument = null;
    function ensureDraftArea() {
      if (docSurface && docEmpty) docEmpty.style.display = "none";
      if (draftEl) return draftEl;
      if (!docSurface) return null;
      draftEl = document.createElement("div");
      draftEl.className = "doc-draft";
      docSurface.appendChild(draftEl);
      return draftEl;
    }
    function appendDraftText(delta) {
      var el = ensureDraftArea();
      if (!el) return;
      el.textContent += delta;
      docSurface.scrollTop = docSurface.scrollHeight;
    }
    function appendDocError(message) {
      if (!docSurface) return;
      var err = document.createElement("div");
      err.className = "doc-error";
      err.textContent = message;
      docSurface.appendChild(err);
      docSurface.scrollTop = docSurface.scrollHeight;
    }
    function clearDocument() {
      if (docEmpty) docEmpty.style.display = "";
      if (draftEl && draftEl.parentNode) draftEl.parentNode.removeChild(draftEl);
      draftEl = null;
      currentJdfDocument = null;
      var existing = docSurface ? docSurface.querySelectorAll(".doc-error") : [];
      for (var i = 0; i < existing.length; i++) existing[i].remove();
    }

    // ---------------------------------------------------------------
    // PHASE 4: JDF rendering
    // ---------------------------------------------------------------
    function renderJdfNode(node) {
      if (!node || !node.type) return null;
      var wrapper = document.createElement("div");
      wrapper.className = "jdf-node";
      if (node.id) wrapper.setAttribute("data-node-id", node.id);
      var el = null;
      if (node.type === "section") {
        el = document.createElement("h2");
        el.className = "jdf-h2";
        el.textContent = node.title || "";
        wrapper.appendChild(el);
        if (node.children && Array.isArray(node.children)) {
          for (var i = 0; i < node.children.length; i++) {
            var child = renderJdfNode(node.children[i]);
            if (child) wrapper.appendChild(child);
          }
        }
      } else if (node.type === "paragraph") {
        el = document.createElement("p");
        el.className = "jdf-p";
        el.textContent = node.content || "";
        wrapper.appendChild(el);
      } else if (node.type === "callout") {
        el = document.createElement("aside");
        el.className = "jdf-callout";
        if (node.variant) el.classList.add("jdf-callout-" + node.variant);
        el.textContent = node.content || "";
        wrapper.appendChild(el);
      } else if (node.type === "table") {
        el = document.createElement("table");
        el.className = "jdf-table";
        if (node.headers && Array.isArray(node.headers)) {
          var thead = document.createElement("thead");
          var tr = document.createElement("tr");
          for (var h = 0; h < node.headers.length; h++) {
            var th = document.createElement("th");
            th.textContent = node.headers[h] || "";
            tr.appendChild(th);
          }
          thead.appendChild(tr);
          el.appendChild(thead);
        }
        if (node.rows && Array.isArray(node.rows)) {
          var tbody = document.createElement("tbody");
          for (var r = 0; r < node.rows.length; r++) {
            var trow = document.createElement("tr");
            var cells = node.rows[r];
            if (Array.isArray(cells)) {
              for (var c = 0; c < cells.length; c++) {
                var td = document.createElement("td");
                td.textContent = cells[c] || "";
                trow.appendChild(td);
              }
            }
            tbody.appendChild(trow);
          }
          el.appendChild(tbody);
        }
        wrapper.appendChild(el);
      } else if (node.type === "image") {
        el = document.createElement("figure");
        el.className = "jdf-fig";
        var img = document.createElement("img");
        img.src = node.url || "";
        img.alt = node.alt || "";
        el.appendChild(img);
        if (node.caption) {
          var cap = document.createElement("figcaption");
          cap.textContent = node.caption;
          el.appendChild(cap);
        }
        wrapper.appendChild(el);
      } else if (node.type === "signature") {
        el = document.createElement("div");
        el.className = "jdf-sig";
        el.textContent = node.content || "Signature: " + (node.signer_name || "");
        wrapper.appendChild(el);
      } else if (node.type === "checkbox") {
        el = document.createElement("label");
        el.className = "jdf-check";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        if (node.checked) cb.checked = true;
        el.appendChild(cb);
        var span = document.createElement("span");
        span.textContent = node.label || "";
        el.appendChild(span);
        wrapper.appendChild(el);
      }
      return wrapper;
    }

    function renderConfidenceLegend() {
      var legend = document.createElement("div");
      legend.className = "conf-legend";
      var items = [
        { dot: "green",  label: "Verified in source" },
        { dot: "yellow", label: "Partial match" },
        { dot: "red",    label: "Not verified" },
      ];
      for (var i = 0; i < items.length; i++) {
        var item = document.createElement("span");
        item.className = "conf-legend-item";
        var dot = document.createElement("span");
        dot.className = "conf-legend-dot " + items[i].dot;
        var text = document.createElement("span");
        text.textContent = items[i].label;
        item.appendChild(dot);
        item.appendChild(text);
        legend.appendChild(item);
      }
      return legend;
    }

    function renderJdfDocument(doc) {
      if (!doc || !doc.body || !Array.isArray(doc.body)) return;
      if (!docSurface) return;
      if (docEmpty) docEmpty.style.display = "none";
      // Do NOT remove existing draftEl — it contains the streamed text.
      // Instead, clear its content and re-populate with JDF nodes.
      var draft = draftEl || document.createElement("div");
      if (!draftEl) {
        draft.className = "doc-draft";
        docSurface.appendChild(draft);
        draftEl = draft;
      }
      // Clear existing children, then prepend the confidence legend.
      while (draft.firstChild) draft.removeChild(draft.firstChild);
      draft.appendChild(renderConfidenceLegend());
      for (var i = 0; i < doc.body.length; i++) {
        var nodeEl = renderJdfNode(doc.body[i]);
        if (nodeEl) draft.appendChild(nodeEl);
      }
      currentJdfDocument = doc;
    }

    function getChipIcon(kind, status) {
      if (kind === "z3") return status === "pass" ? "\ud83d\udd12" : "\u26a0";
      else if (kind === "cite") return "\ud83d\udcce";
      else if (kind === "redhat") return status === "open" ? "\ud83d\udea9" : "\u2713";
      return "";
    }

    function addEvidenceChips(doc) {
      if (!doc || !doc.body || !Array.isArray(doc.body)) return;
      function processNode(node) {
        if (!node || !node.id) return;
        var wrapper = draftEl ? draftEl.querySelector('.jdf-node[data-node-id="' + node.id + '"]') : null;
        if (!wrapper) return;
        if (node.annotations && node.annotations.z3 && Array.isArray(node.annotations.z3)) {
          for (var i = 0; i < node.annotations.z3.length; i++) {
            var z3 = node.annotations.z3[i];
            var chip = document.createElement("button");
            chip.className = "chip chip-z3";
            if (z3.status === "violation") chip.classList.add("violation");
            chip.setAttribute("data-node-id", node.id);
            chip.setAttribute("data-kind", "z3");
            chip.setAttribute("data-index", String(i));
            chip.textContent = getChipIcon("z3", z3.status);
            chip.title = z3.message || "";
            wrapper.appendChild(chip);
          }
        }
        // Provenance is a single dict under node.meta.provenance in the
        // live SSE payload (NOT a list on node.provenance). Emit one cite
        // chip 📎 per node when an excerpt is present.
        var metaProv = (node.meta && node.meta.provenance) || null;
        if (metaProv && typeof metaProv === "object" && metaProv.excerpt) {
          var chip = document.createElement("button");
          chip.className = "chip chip-cite";
          chip.setAttribute("data-node-id", node.id);
          chip.setAttribute("data-kind", "cite");
          chip.setAttribute("data-index", "0");
          chip.textContent = getChipIcon("cite");
          chip.title = metaProv.source_name || "";
          wrapper.appendChild(chip);
        }
        if (node.annotations && node.annotations.redhat && Array.isArray(node.annotations.redhat)) {
          for (var k = 0; k < node.annotations.redhat.length; k++) {
            var rh = node.annotations.redhat[k];
            var chip = document.createElement("button");
            chip.className = "chip chip-redhat";
            if (rh.status) chip.classList.add(rh.status);
            chip.setAttribute("data-node-id", node.id);
            chip.setAttribute("data-kind", "redhat");
            chip.setAttribute("data-index", String(k));
            chip.textContent = getChipIcon("redhat", rh.status);
            chip.title = rh.text || "";
            wrapper.appendChild(chip);
          }
        }
        if (node.children && Array.isArray(node.children)) {
          for (var m = 0; m < node.children.length; m++) processNode(node.children[m]);
        }
      }
      for (var i = 0; i < doc.body.length; i++) processNode(doc.body[i]);
      if (draftEl) {
        var chips = draftEl.querySelectorAll(".chip");
        for (var c = 0; c < chips.length; c++) chips[c].addEventListener("click", handleChipClick);
      }
    }

    function applyConfidenceSpans(doc) {
      if (!doc) return;
      // Confidence spans use field names startChar / endChar / nodeId
      // (NOT start / end / node_id). Prefer camelCase, fall back to
      // snake_case; both duplicate the same array on the live payload.
      var spans = doc.confidenceSpans || doc.confidence_spans ||
                  (doc.meta && (doc.meta.confidenceSpans || doc.meta.confidence_spans));
      if (!spans || !Array.isArray(spans) || spans.length === 0) return;

      // Group spans by nodeId so each node's text is rebuilt once.
      var byNode = {};
      for (var i = 0; i < spans.length; i++) {
        var span = spans[i];
        if (!span || !span.nodeId) continue;
        if (span.startChar === undefined || span.endChar === undefined || span.score === undefined) continue;
        if (!byNode[span.nodeId]) byNode[span.nodeId] = [];
        byNode[span.nodeId].push(span);
      }

      function escapeHtml(s) {
        return String(s)
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;");
      }
      function escapeAttr(s) {
        return escapeHtml(s).replace(/"/g, "&quot;");
      }

      var nodeIds = Object.keys(byNode);
      for (var n = 0; n < nodeIds.length; n++) {
        var nodeId = nodeIds[n];
        var wrapper = document.querySelector('.jdf-node[data-node-id="' + nodeId + '"]');
        if (!wrapper) continue;
        var textEl = wrapper.querySelector(".jdf-p, .jdf-h2, .jdf-callout");
        if (!textEl) continue;
        var text = textEl.textContent || "";
        if (text.length === 0) continue;

        // Keep only in-range spans and sort by startChar DESCENDING so
        // wrapping higher spans first never shifts the indices used by
        // the lower spans (offsets are relative to the original text).
        var nodeSpans = [];
        for (var s = 0; s < byNode[nodeId].length; s++) {
          var cand = byNode[nodeId][s];
          var cs = parseInt(cand.startChar, 10);
          var ce = parseInt(cand.endChar, 10);
          if (isNaN(cs) || isNaN(ce) || cs < 0 || ce > text.length || cs >= ce) continue;
          nodeSpans.push(cand);
        }
        if (nodeSpans.length === 0) continue;
        nodeSpans.sort(function (a, b) { return b.startChar - a.startChar; });

        // Build the output from the tail, prepending wrapped spans.
        var html = "";
        var ptr = text.length;
        for (var s = 0; s < nodeSpans.length; s++) {
          var sp = nodeSpans[s];
          var start = parseInt(sp.startChar, 10);
          var end = parseInt(sp.endChar, 10);
          var plain = escapeHtml(text.slice(end, ptr));
          var confClass = "conf-span ";
          if (sp.score > 0.8) confClass += "conf-green";
          else if (sp.score >= 0.4) confClass += "conf-yellow";
          else confClass += "conf-red";
          var wrapped = '<span class="' + confClass + '" data-node-id="' + escapeAttr(nodeId) + '" title="' + escapeAttr(sp.reason || "") + '">' + escapeHtml(text.slice(start, end)) + "</span>";
          html = wrapped + plain + html;
          ptr = start;
        }
        html = escapeHtml(text.slice(0, ptr)) + html;
        textEl.innerHTML = html;
        // Make each new span clickable to open the Evidence drawer.
        var createdSpans = textEl.querySelectorAll(".conf-span");
        for (var csp = 0; csp < createdSpans.length; csp++) {
          createdSpans[csp].addEventListener("click", handleConfidenceClick);
        }
      }
    }

    // ---------------------------------------------------------------
    // SSE parser: parses chunks -> emits (eventName, parsedData) pairs
    // ---------------------------------------------------------------
    function parseSseLoop(
      onEvent,
      onDone,
      onError
    ) {
      // Returns a fn(chunk) you feed stream chunks to, call flush(null) at end.
      var buffer = "";
      var curEvent = "message";
      var curData = [];
      function dispatchOne() {
        if (curData.length === 0 && curEvent === "message") {
          curEvent = "message";
          curData = [];
          return;
        }
        var joined = curData.join("\n");
        curData = [];
        if (curEvent === "message" && joined === "[DONE]") {
          onEvent("[DONE]", null);
          curEvent = "message";
          return;
        }
        var parsed;
        if (joined.length === 0) {
          parsed = null;
        } else {
          try {
            parsed = JSON.parse(joined);
          } catch (e) {
            parsed = { raw: joined };
          }
        }
        onEvent(curEvent, parsed);
        curEvent = "message";
      }
      function flushAll() {
        var parts = buffer.split("\n");
        buffer = parts.pop() || "";
        for (var i = 0; i < parts.length; i++) {
          var line = parts[i].replace(/\r$/, "");
          if (line === "") {
            dispatchOne();
            continue;
          }
          if (line.indexOf(":") === 0) continue; // comment
          var idx = line.indexOf(":");
          var key, val;
          if (idx === -1) {
            key = line;
            val = "";
          } else {
            key = line.slice(0, idx);
            val = line.slice(idx + 1);
            if (val.charAt(0) === " ") val = val.slice(1);
          }
          if (key === "event") curEvent = val;
          else if (key === "data") curData.push(val);
        }
      }
      return {
        feed: function (chunk) {
          try {
            buffer += chunk;
            flushAll();
          } catch (e) {
            onError(e);
          }
        },
        end: function () {
          if (buffer.length) {
            buffer += "\n\n";
            flushAll();
          }
          if (curData.length || curEvent !== "message") dispatchOne();
          onDone();
        },
      };
    }

    // ---------------------------------------------------------------
    // Event dispatch — stage transitions + token appends
    // ---------------------------------------------------------------
    var currentStageIndex = -1;
    function transitionTo(stageName) {
      var idx = STAGE_ORDER.indexOf(stageName);
      if (idx === -1) return;
      for (var i = 0; i < idx; i++) {
        if (i > currentStageIndex) markDone(STAGE_ORDER[i]);
      }
      markActive(stageName);
      currentStageIndex = idx;
    }
    function markRemainingIdle() {
      for (var i = 0; i < STAGE_ORDER.length; i++) {
        var r = stageRow(STAGE_ORDER[i]);
        if (!r) continue;
        if (r.classList.contains("active") ||
            r.classList.contains("done") ||
            r.classList.contains("failed")) continue;
      }
    }
    function handleEvent(event, data) {
      if (event === "[DONE]") return;
      if (event === "status" && data && typeof data === "object") {
        var stage = data.stage;
        if (stage === "preflight") {
          transitionTo("Preflight");
        } else if (stage === "model") {
          markDone("Preflight");
          transitionTo("Drafting");
        } else if (stage === "locks") {
          markDone("Drafting");
          transitionTo("Lock Inference");
        } else if (!stage && typeof data.message === "string" &&
                   /running math check/i.test(data.message)) {
          markDone("Compile");
          transitionTo("Math Check");
        }
      } else if (event === "token" && data && typeof data.delta === "string") {
        appendDraftText(data.delta);
      } else if (event === "compiled") {
        markDone("Lock Inference");
        transitionTo("Compile");
        if (data && data.document) renderJdfDocument(data.document);
      } else if (event === "verified") {
        markDone("Math Check");
        transitionTo("Verify");
        if (data && data.document) {
          addEvidenceChips(data.document);
          applyConfidenceSpans(data.document);
        }
      } else if (event === "complete") {
        markDone("Verify");
        markActive("Complete");
        markDone("Complete");
      } else if (event === "error") {
        var active = findActiveStage() || STAGE_ORDER[
          (currentStageIndex >= 0) ? currentStageIndex : 0
        ];
        markFailed(active);
        var msg = (data && data.error) ? data.error : (data ? JSON.stringify(data) : "unknown error");
        appendDocError(msg);
        try { console.error("[shell] error event:", msg); } catch (_) {}
      }
      markRemainingIdle();
    }

    // ---------------------------------------------------------------
    // Project bootstrap + stream execution
    // ---------------------------------------------------------------
    function jsonPost(url, bodyObj, extraHeaders) {
      var hdrs = { "Content-Type": "application/json", "Accept": "application/json, text/event-stream" };
      if (extraHeaders) {
        for (var k in extraHeaders) if (Object.prototype.hasOwnProperty.call(extraHeaders, k)) hdrs[k] = extraHeaders[k];
      }
      return fetch(url, {
        method: "POST",
        headers: hdrs,
        body: JSON.stringify(bodyObj),
      });
    }

    function ensureProjectId() {
      try {
        var existing = window.localStorage.getItem(STORAGE_KEY);
        if (existing && typeof existing === "string" && existing.length > 0) {
          return Promise.resolve(existing);
        }
      } catch (_) {}
      return jsonPost("/api/projects", { title: "shell-proto" })
        .then(function (resp) {
          if (!resp.ok) throw new Error("projects POST " + resp.status);
          return resp.json();
        })
        .then(function (j) {
          var id = j && j.id;
          if (!id) throw new Error("projects returned no id");
          try { window.localStorage.setItem(STORAGE_KEY, id); } catch (_) {}
          return id;
        });
    }

    function runDraft(intent) {
      resetStages();
      clearDocument();
      currentStageIndex = -1;
      openRight();
      var parser = parseSseLoop(
        handleEvent,
        function () {},
        function (err) {
          var active = findActiveStage() || STAGE_ORDER[Math.max(0, currentStageIndex)];
          markFailed(active);
          appendDocError(String(err && err.message ? err.message : err));
          try { console.error("[shell] stream parse error:", err); } catch (_) {}
        }
      );
      var started = false;
      return ensureProjectId()
        .then(function (projectId) {
          var url = "/api/projects/" + encodeURIComponent(projectId) + "/draft/stream";
          return fetch(url, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Accept": "text/event-stream, application/json",
            },
            body: JSON.stringify({ intent: intent, compileType: DRAFT_TYPE }),
          });
        })
        .then(function (resp) {
          started = true;
          if (!resp.ok) {
            return resp.text().then(function (t) {
              try { var j = JSON.parse(t); handleEvent("error", j); return; }
              catch (_) { handleEvent("error", { ok: false, error: t || "HTTP " + resp.status }); }
            });
          }
          if (!resp.body) {
            handleEvent("error", { ok: false, error: "Response body unavailable." });
            return;
          }
          var reader = resp.body.getReader();
          var decoder = new TextDecoder("utf-8");
          function loop() {
            return reader.read().then(function (chunk) {
              if (chunk.done) {
                parser.end();
                return;
              }
              var str = decoder.decode(chunk.value || new Uint8Array(0), { stream: true });
              parser.feed(str);
              return loop();
            });
          }
          return loop();
        })
        .catch(function (err) {
          if (!started) {
            resetStages();
          }
          handleEvent("error", { ok: false, error: String(err && err.message ? err.message : err) });
        });
    }

    // ---------------------------------------------------------------
    // Docked input submit wiring
    // ---------------------------------------------------------------
    function submitIntent() {
      if (!text) return;
      var v = String(text.value || "").trim();
      if (!v) return;
      text.value = "";
      try { window.sessionStorage.setItem("assure_last_intent", v); } catch (_) {}
      compareDataLoaded = false;
      runDraft(v);
    }

    // ---------------------------------------------------------------
    // Phase 3 — Right-pane mode toggle (Pipeline / Compare)
    // ---------------------------------------------------------------
    var LAST_INTENT_KEY = "assure_last_intent";
    var PINS_KEY = "assure_pins";
    var COMPARE_URL = "/api/runs/compare";

    var pipelineModeEl = document.getElementById("pipeline-mode");
    var compareModeEl  = document.getElementById("compare-mode");
    var evidenceModeEl = document.getElementById("evidence-mode");
    var compareBodyEl  = document.getElementById("compare-body");
    var evidenceBodyEl = document.getElementById("evidence-body");
    var pinnedListEl   = document.getElementById("pinned-list");
    var modeTabs = document.querySelectorAll(".mode-tab");
    var compareInFlight = false;
    var compareDataLoaded = false;

    function setMode(name) {
      if (!pipelineModeEl || !compareModeEl || !evidenceModeEl) return;
      if (name === "compare") {
        pipelineModeEl.style.display = "none";
        compareModeEl.style.display  = "block";
        evidenceModeEl.style.display = "none";
      } else if (name === "evidence") {
        pipelineModeEl.style.display = "none";
        compareModeEl.style.display  = "none";
        evidenceModeEl.style.display = "block";
      } else {
        compareModeEl.style.display  = "none";
        evidenceModeEl.style.display = "none";
        pipelineModeEl.style.display = "block";
      }
      modeTabs.forEach(function (t) {
        var tm = t.getAttribute("data-mode");
        if (tm === name) {
          t.classList.add("is-active");
          t.setAttribute("aria-selected", "true");
        } else {
          t.classList.remove("is-active");
          t.setAttribute("aria-selected", "false");
        }
      });
    }
    function setCompareDisabled(disabled) {
      compareInFlight = !!disabled;
      modeTabs.forEach(function (t) {
        if (t.getAttribute("data-mode") === "compare") {
          if (disabled) t.classList.add("is-disabled");
          else          t.classList.remove("is-disabled");
        }
      });
    }
    modeTabs.forEach(function (t) {
      t.addEventListener("click", function () {
        if (t.classList.contains("is-disabled")) return;
        var name = t.getAttribute("data-mode");
        setMode(name);
        if (name === "compare" && !compareDataLoaded) runCompare();
      });
    });

    // ---------------------------------------------------------------
    // Compare render helpers
    // ---------------------------------------------------------------
    function compareClear() {
      if (!compareBodyEl) return;
      while (compareBodyEl.firstChild) compareBodyEl.removeChild(compareBodyEl.firstChild);
    }
    function compareMessage(text, cls) {
      compareClear();
      var div = document.createElement("div");
      div.className = "compare-message" + (cls ? (" " + cls) : "");
      div.textContent = text;
      compareBodyEl.appendChild(div);
    }
    function compareBuildCol(slotKey, slot) {
      // slot: {model, text, error} or partial. slotKey: "claude" | "deepseek"
      var safe = (slot && typeof slot === "object") ? slot : {};
      var modelId = safe.model || "";
      var err     = (typeof safe.error === "string" && safe.error.length > 0) ? safe.error : null;
      var txt     = (!err && typeof safe.text  === "string") ? safe.text  : "";
      var title = (slotKey === "claude") ? "CLAUDE" : "DEEPSEEK";

      var col = document.createElement("div");
      col.className = "compare-col";

      var head = document.createElement("div");
      head.className = "compare-col-head";
      var t1 = document.createElement("div"); t1.className = "compare-col-title";  t1.textContent = title;
      var t2 = document.createElement("div"); t2.className = "compare-col-model"; t2.textContent = modelId || "\u2014";
      head.appendChild(t1); head.appendChild(t2);
      col.appendChild(head);

      var body = document.createElement("div");
      body.className = "compare-col-body";
      if (err) {
        body.classList.add("is-error");
        body.textContent = "Failed: " + err;
      } else if (txt) {
        body.textContent = txt;
      } else {
        body.textContent = "\u2014";
      }
      col.appendChild(body);

      var acts = document.createElement("div");
      acts.className = "compare-col-actions";
      var btnAccept = document.createElement("button");
      btnAccept.type = "button";
      btnAccept.className = "compare-btn";
      btnAccept.textContent = "Accept";
      if (err) btnAccept.setAttribute("disabled", "disabled");
      btnAccept.addEventListener("click", function () {
        if (!docSurface) return;
        // Replace center document content entirely (do not append)
        if (docEmpty) docEmpty.style.display = "none";
        if (draftEl && draftEl.parentNode) draftEl.parentNode.removeChild(draftEl);
        draftEl = document.createElement("div");
        draftEl.className = "doc-draft";
        draftEl.textContent = txt || "";
        docSurface.appendChild(draftEl);
        // Remove prior errors in center doc
        var oldErrs = docSurface.querySelectorAll(".doc-error");
        for (var i = 0; i < oldErrs.length; i++) oldErrs[i].remove();
        docSurface.scrollTop = 0;
        setMode("pipeline");
      });
      var btnPin = document.createElement("button");
      btnPin.type = "button";
      btnPin.className = "compare-btn";
      btnPin.textContent = "Pin";
      btnPin.addEventListener("click", function () {
        var pinText = err ? ("Failed: " + err) : (txt || "");
        appendPin(pinText, modelId);
      });
      acts.appendChild(btnAccept);
      acts.appendChild(btnPin);
      col.appendChild(acts);
      return col;
    }
    function compareRenderGrid(models) {
      // models: {claude, deepseek}. If both errored → show combined banner + grid still.
      compareClear();
      var claude    = (models && models.claude)    || {};
      var deepseek  = (models && models.deepseek)  || {};
      var cErr = (typeof claude.error   === "string" && claude.error.length   > 0);
      var dErr = (typeof deepseek.error === "string" && deepseek.error.length > 0);
      if (cErr && dErr) {
        var banner = document.createElement("div");
        banner.className = "compare-message is-error";
        banner.textContent = "Both models failed.";
        compareBodyEl.appendChild(banner);
      }
      var grid = document.createElement("div");
      grid.className = "compare-grid";
      grid.appendChild(compareBuildCol("claude",   claude));
      grid.appendChild(compareBuildCol("deepseek", deepseek));
      compareBodyEl.appendChild(grid);
    }

    // ---------------------------------------------------------------
    // Compare request
    // ---------------------------------------------------------------
    function runCompare() {
      if (!compareBodyEl) return;
      if (compareInFlight) return;
      var stored;
      try { stored = window.sessionStorage.getItem(LAST_INTENT_KEY); } catch (_) { stored = null; }
      if (!stored || typeof stored !== "string" || stored.length === 0) {
        compareMessage("Run a draft first, then compare.");
        return;
      }
      setCompareDisabled(true);
      compareMessage("Running compare\u2026", "is-loading");
      fetch(COMPARE_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
        },
        body: JSON.stringify({ intent: stored }),
      }).then(function (resp) {
        if (!resp.ok) {
          throw new Error("HTTP " + resp.status);
        }
        return resp.json();
      }).then(function (j) {
        if (!j || typeof j !== "object") throw new Error("bad compare payload");
        var models = (typeof j.models === "object" && j.models) ? j.models : {};
        // Prefer j.models.claude / .deepseek per the task spec — never model_a / model_b.
        compareRenderGrid(models);
        compareDataLoaded = true;
      }).catch(function (err) {
        try { console.error("[shell] compare failed:", err); } catch (_) {}
        compareMessage("Compare request failed.", "is-error");
      }).then(function () {
        setCompareDisabled(false);
      });
    }

    // ---------------------------------------------------------------
    // Pinned list (left pane) — localStorage assure_pins
    // ---------------------------------------------------------------
    function loadPins() {
      try {
        var raw = window.localStorage.getItem(PINS_KEY);
        if (!raw) return [];
        var arr = JSON.parse(raw);
        if (!Array.isArray(arr)) return [];
        var out = [];
        for (var i = 0; i < arr.length; i++) {
          var it = arr[i];
          if (it && typeof it === "object" &&
              typeof it.text === "string" &&
              typeof it.ts   === "number") {
            out.push({ text: it.text, model: (typeof it.model === "string" ? it.model : ""), ts: it.ts });
          }
        }
        return out;
      } catch (_) { return []; }
    }
    function savePins(arr) {
      try { window.localStorage.setItem(PINS_KEY, JSON.stringify(arr)); } catch (_) {}
    }
    function relativeTime(tsMs) {
      var now = Date.now();
      var diff = Math.max(0, now - tsMs);
      var s  = Math.floor(diff / 1000);
      if (s < 60)   return "just now";
      var m  = Math.floor(s / 60);
      if (m < 60)   return m + "m ago";
      var h  = Math.floor(m / 60);
      if (h < 24)   return h + "h ago";
      var d  = Math.floor(h / 24);
      if (d < 7)    return d + "d ago";
      var wk = Math.floor(d / 7);
      return wk + "w ago";
    }
    function renderPins() {
      if (!pinnedListEl) return;
      var arr = loadPins();
      // Clear children but keep the section-header <h3> element as first child if present
      var keepHeader = null;
      for (var c = pinnedListEl.firstChild; c; c = c.nextSibling) {
        if (c.nodeType === 1 && c.tagName && c.tagName.toLowerCase() === "h3" &&
            c.classList && c.classList.contains("section-header")) {
          keepHeader = c;
          break;
        }
      }
      while (pinnedListEl.firstChild) pinnedListEl.removeChild(pinnedListEl.firstChild);
      if (keepHeader) pinnedListEl.appendChild(keepHeader);
      if (arr.length === 0) {
        var hint = document.createElement("p");
        hint.className = "empty-hint";
        hint.textContent = "Pin a response to keep it here.";
        pinnedListEl.appendChild(hint);
        return;
      }
      // Newest first (ts desc)
      arr.sort(function (a, b) { return b.ts - a.ts; });
      var ul = document.createElement("ul");
      ul.className = "pinned-list";
      for (var i = 0; i < arr.length; i++) {
        (function (pin, idx) {
          var li = document.createElement("li");
          li.className = "pinned-item";
          var main = document.createElement("div");
          main.className = "pinned-item-main";
          var tEl = document.createElement("div");
          tEl.className = "pinned-item-text";
          var s = pin.text || "";
          if (s.length > 60) {
            tEl.textContent = s.slice(0, 60) + "\u2026";
          } else {
            tEl.textContent = s || "\u2014";
          }
          var mEl = document.createElement("div");
          mEl.className = "pinned-item-meta";
          var parts = [];
          if (pin.model) parts.push(pin.model);
          parts.push(relativeTime(pin.ts));
          mEl.textContent = parts.join(" \u00b7 ");
          main.appendChild(tEl); main.appendChild(mEl);
          var rm = document.createElement("button");
          rm.type = "button";
          rm.className = "pinned-item-remove";
          rm.title = "Remove pin";
          rm.setAttribute("aria-label", "Remove pinned answer");
          rm.textContent = "\u2715";
          rm.addEventListener("click", function () {
            var cur = loadPins();
            // Remove by exact (text, ts) match — idx might be stale if list mutated
            var next = [];
            for (var j = 0; j < cur.length; j++) {
              var p = cur[j];
              if (p.text === pin.text && p.ts === pin.ts && p.model === pin.model) continue;
              next.push(p);
            }
            savePins(next);
            renderPins();
          });
          li.appendChild(main); li.appendChild(rm);
          ul.appendChild(li);
        })(arr[i], i);
      }
      pinnedListEl.appendChild(ul);
    }
    function appendPin(text, modelId) {
      var arr = loadPins();
      arr.push({ text: String(text || ""), model: String(modelId || ""), ts: Date.now() });
      savePins(arr);
      renderPins();
    }
    renderPins();

    // ---------------------------------------------------------------
    // PHASE 4: Evidence drawer + surgical actions
    // ---------------------------------------------------------------
    var currentEvidence = null;

    function handleChipClick(e) {
      var chip = e.currentTarget;
      var nodeId = chip.getAttribute("data-node-id");
      var kind = chip.getAttribute("data-kind");
      var index = parseInt(chip.getAttribute("data-index"), 10);
      if (!currentJdfDocument || !nodeId || !kind || isNaN(index)) return;
      function findNode(nodes) {
        for (var i = 0; i < nodes.length; i++) {
          var n = nodes[i];
          if (n.id === nodeId) return n;
          if (n.children && Array.isArray(n.children)) {
            var found = findNode(n.children);
            if (found) return found;
          }
        }
        return null;
      }
      var node = findNode(currentJdfDocument.body || []);
      if (!node) return;
      var evidence = null;
      if (kind === "z3" && node.annotations && node.annotations.z3 && node.annotations.z3[index]) {
        evidence = { kind: "z3", nodeId: nodeId, data: node.annotations.z3[index] };
      } else if (kind === "cite" && node.provenance && node.provenance[index]) {
        evidence = { kind: "cite", nodeId: nodeId, data: node.provenance[index] };
      } else if (kind === "redhat" && node.annotations && node.annotations.redhat && node.annotations.redhat[index]) {
        evidence = { kind: "redhat", nodeId: nodeId, data: node.annotations.redhat[index], index: index };
      }
      if (!evidence) return;
      currentEvidence = evidence;
      renderEvidenceDrawer(evidence);
      openRight();
      setMode("evidence");
    }

    function findJdfNodeById(nodeId) {
      if (!currentJdfDocument) return null;
      function find(nodes) {
        for (var i = 0; i < nodes.length; i++) {
          var n = nodes[i];
          if (n.id === nodeId) return n;
          if (n.children && Array.isArray(n.children)) {
            var found = find(n.children);
            if (found) return found;
          }
        }
        return null;
      }
      return find(currentJdfDocument.body || []);
    }

    function handleConfidenceClick(e) {
      var span = e.currentTarget;
      var nodeId = span.getAttribute("data-node-id");
      if (!nodeId) return;
      var reason = span.getAttribute("title") || "";
      var wrapper = document.querySelector('.jdf-node[data-node-id="' + nodeId + '"]');
      var textEl = wrapper ? wrapper.querySelector(".jdf-p, .jdf-h2, .jdf-callout") : null;
      var preview = textEl ? (textEl.textContent || "") : "";
      if (preview.length > 100) preview = preview.slice(0, 100) + "\u2026";
      var node = findJdfNodeById(nodeId);
      renderConfidenceEvidence(nodeId, reason, preview, node);
      currentEvidence = { kind: "confidence", nodeId: nodeId, data: { reason: reason } };
      openRight();
      setMode("evidence");
    }

    function renderConfidenceEvidence(nodeId, reason, preview, node) {
      if (!evidenceBodyEl) return;
      while (evidenceBodyEl.firstChild) evidenceBodyEl.removeChild(evidenceBodyEl.firstChild);
      var header = document.createElement("div");
      header.className = "evidence-header";
      header.textContent = "Confidence \u00b7 " + nodeId;
      evidenceBodyEl.appendChild(header);
      var content = document.createElement("div");
      content.className = "evidence-content";
      if (reason) {
        var reasonField = document.createElement("div");
        reasonField.className = "evidence-field";
        var reasonLabel = document.createElement("div");
        reasonLabel.className = "evidence-label";
        reasonLabel.textContent = "Reason";
        var reasonValue = document.createElement("div");
        reasonValue.className = "evidence-value";
        reasonValue.textContent = reason;
        reasonField.appendChild(reasonLabel);
        reasonField.appendChild(reasonValue);
        content.appendChild(reasonField);
      }
      if (preview) {
        var previewField = document.createElement("div");
        previewField.className = "evidence-field";
        var previewLabel = document.createElement("div");
        previewLabel.className = "evidence-label";
        previewLabel.textContent = "Node preview";
        var previewValue = document.createElement("div");
        previewValue.className = "evidence-blockquote";
        previewValue.textContent = preview;
        previewField.appendChild(previewLabel);
        previewField.appendChild(previewValue);
        content.appendChild(previewField);
      }
      // Source chip when the node carries provenance — opens the cite view.
      if (node && node.meta && node.meta.provenance) {
        var prov = node.meta.provenance;
        var chip = document.createElement("button");
        chip.type = "button";
        chip.className = "chip chip-cite";
        chip.textContent = getChipIcon("cite");
        chip.title = prov.source_name || "Source";
        chip.addEventListener("click", function () {
          renderEvidenceDrawer({
            kind: "cite",
            nodeId: nodeId,
            data: {
              source_name: prov.source_name || "",
              page_number: prov.page_number,
              extracted_quote: prov.excerpt || prov.extracted_quote || ""
            }
          });
        });
        content.appendChild(chip);
      }
      evidenceBodyEl.appendChild(content);
    }

    function renderEvidenceDrawer(ev) {
      if (!evidenceBodyEl) return;
      while (evidenceBodyEl.firstChild) evidenceBodyEl.removeChild(evidenceBodyEl.firstChild);
      var header = document.createElement("div");
      header.className = "evidence-header";
      header.textContent = ev.kind.toUpperCase() + " · Node: " + ev.nodeId;
      evidenceBodyEl.appendChild(header);
      var content = document.createElement("div");
      content.className = "evidence-content";
      if (ev.kind === "z3") {
        var statusField = document.createElement("div");
        statusField.className = "evidence-field";
        var statusLabel = document.createElement("div");
        statusLabel.className = "evidence-label";
        statusLabel.textContent = "Status";
        var statusValue = document.createElement("div");
        statusValue.className = "evidence-value";
        var statusBadge = document.createElement("span");
        statusBadge.className = "evidence-status " + ev.data.status;
        statusBadge.textContent = ev.data.status || "";
        statusValue.appendChild(statusBadge);
        statusField.appendChild(statusLabel);
        statusField.appendChild(statusValue);
        content.appendChild(statusField);
        if (ev.data.canonical_key) {
          var keyField = document.createElement("div");
          keyField.className = "evidence-field";
          var keyLabel = document.createElement("div");
          keyLabel.className = "evidence-label";
          keyLabel.textContent = "Canonical Key";
          var keyValue = document.createElement("div");
          keyValue.className = "evidence-value";
          keyValue.textContent = ev.data.canonical_key;
          keyField.appendChild(keyLabel);
          keyField.appendChild(keyValue);
          content.appendChild(keyField);
        }
        if (ev.data.message) {
          var msgField = document.createElement("div");
          msgField.className = "evidence-field";
          var msgLabel = document.createElement("div");
          msgLabel.className = "evidence-label";
          msgLabel.textContent = "Message";
          var msgValue = document.createElement("div");
          msgValue.className = "evidence-value";
          msgValue.textContent = ev.data.message;
          msgField.appendChild(msgLabel);
          msgField.appendChild(msgValue);
          content.appendChild(msgField);
        }
      } else if (ev.kind === "cite") {
        if (ev.data.extracted_quote) {
          var quoteEl = document.createElement("blockquote");
          quoteEl.className = "evidence-blockquote";
          quoteEl.textContent = ev.data.extracted_quote;
          content.appendChild(quoteEl);
        }
        if (ev.data.source_name) {
          var srcField = document.createElement("div");
          srcField.className = "evidence-field";
          var srcLabel = document.createElement("div");
          srcLabel.className = "evidence-label";
          srcLabel.textContent = "Source";
          var srcValue = document.createElement("div");
          srcValue.className = "evidence-value";
          srcValue.textContent = ev.data.source_name;
          srcField.appendChild(srcLabel);
          srcField.appendChild(srcValue);
          content.appendChild(srcField);
        }
        if (ev.data.page_number) {
          var pageField = document.createElement("div");
          pageField.className = "evidence-field";
          var pageLabel = document.createElement("div");
          pageLabel.className = "evidence-label";
          pageLabel.textContent = "Page";
          var pageValue = document.createElement("div");
          pageValue.className = "evidence-value";
          pageValue.textContent = String(ev.data.page_number);
          pageField.appendChild(pageLabel);
          pageField.appendChild(pageValue);
          content.appendChild(pageField);
        }
      }
      renderEvidenceRedhat(ev, content);
      evidenceBodyEl.appendChild(content);
      renderEvidenceFooter(ev);
    }

    function renderEvidenceRedhat(ev, content) {
      if (ev.kind !== "redhat") return;
      var rhStatusField = document.createElement("div");
      rhStatusField.className = "evidence-field";
      var rhStatusLabel = document.createElement("div");
      rhStatusLabel.className = "evidence-label";
      rhStatusLabel.textContent = "Status";
      var rhStatusValue = document.createElement("div");
      rhStatusValue.className = "evidence-value";
      var rhStatusBadge = document.createElement("span");
      rhStatusBadge.className = "evidence-status " + (ev.data.status || "open");
      rhStatusBadge.textContent = ev.data.status || "open";
      rhStatusValue.appendChild(rhStatusBadge);
      rhStatusField.appendChild(rhStatusLabel);
      rhStatusField.appendChild(rhStatusValue);
      content.appendChild(rhStatusField);
      if (ev.data.text) {
        var textField = document.createElement("div");
        textField.className = "evidence-field";
        var textLabel = document.createElement("div");
        textLabel.className = "evidence-label";
        textLabel.textContent = "Finding";
        var textValue = document.createElement("div");
        textValue.className = "evidence-value";
        textValue.textContent = ev.data.text;
        textField.appendChild(textLabel);
        textField.appendChild(textValue);
        content.appendChild(textField);
      }
      if (ev.data.status === "open") {
        var sugField = document.createElement("div");
        sugField.className = "evidence-field";
        var sugLabel = document.createElement("div");
        sugLabel.className = "evidence-label";
        sugLabel.textContent = "Suggested Fix (optional)";
        var sugInput = document.createElement("textarea");
        sugInput.className = "evidence-suggestion-input";
        sugInput.id = "evidence-suggestion-text";
        sugInput.placeholder = "Enter a suggested fix...";
        sugField.appendChild(sugLabel);
        sugField.appendChild(sugInput);
        content.appendChild(sugField);
      }
    }

    function renderEvidenceFooter(ev) {
      var footer = document.createElement("div");
      footer.className = "evidence-footer";
      if (ev.kind === "z3" && ev.data.status === "violation") {
        var groundBtn = document.createElement("button");
        groundBtn.type = "button";
        groundBtn.className = "evidence-action primary";
        groundBtn.textContent = "Ground with sources";
        groundBtn.addEventListener("click", function () { performGrounding(ev.nodeId); });
        footer.appendChild(groundBtn);
      }
      if (ev.kind === "redhat" && ev.data.status === "open") {
        var reviseBtn = document.createElement("button");
        reviseBtn.type = "button";
        reviseBtn.className = "evidence-action primary";
        reviseBtn.textContent = "Revise with LLM";
        reviseBtn.addEventListener("click", function () { performRevision(ev.nodeId, ev.data.text); });
        footer.appendChild(reviseBtn);
        var dismissBtn = document.createElement("button");
        dismissBtn.type = "button";
        dismissBtn.className = "evidence-action";
        dismissBtn.textContent = "Dismiss";
        dismissBtn.addEventListener("click", function () { performDismissal(ev); });
        footer.appendChild(dismissBtn);
      }
      evidenceBodyEl.appendChild(footer);
    }

    function performGrounding(nodeId) {
      ensureProjectId().then(function (projectId) {
        return jsonPost("/api/projects/" + projectId + "/nodes/" + nodeId + "/ground", { mode: "auto" });
      }).then(function (resp) {
        if (!resp.ok) throw new Error("Ground failed: " + resp.status);
        return resp.json();
      }).then(function (result) {
        alert("Grounding complete. Node updated with provenance.");
        setMode("pipeline");
      }).catch(function (err) {
        alert("Grounding error: " + (err.message || err));
      });
    }

    function performRevision(nodeId, findingText) {
      ensureProjectId().then(function (projectId) {
        var url = "/api/projects/" + projectId + "/inquire/stream";
        var body = { intent: findingText, target_node_id: nodeId };
        return jsonPost(url, body);
      }).then(function (resp) {
        if (!resp.ok) throw new Error("Revise failed: " + resp.status);
        if (!resp.body) throw new Error("No stream body");
        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var preview = document.createElement("div");
        preview.className = "evidence-revision-preview";
        preview.id = "revision-preview";
        var content = evidenceBodyEl.querySelector(".evidence-content");
        if (content) content.appendChild(preview);
        function read() {
          reader.read().then(function (result) {
            if (result.done) {
              var acceptBtn = document.createElement("button");
              acceptBtn.type = "button";
              acceptBtn.className = "evidence-action primary";
              acceptBtn.textContent = "Accept revision";
              acceptBtn.style.marginTop = "12px";
              acceptBtn.addEventListener("click", function () {
                alert("Accept revision not fully wired. Would update JDF here.");
              });
              if (content) content.appendChild(acceptBtn);
              return;
            }
            var chunk = decoder.decode(result.value, { stream: true });
            preview.textContent += chunk;
            read();
          }).catch(function (err) {
            alert("Stream read error: " + (err.message || err));
          });
        }
        read();
      }).catch(function (err) {
        alert("Revision error: " + (err.message || err));
      });
    }

    function performDismissal(ev) {
      var rationale = "Reviewed by operator.";
      if (!ev.data.id) {
        alert("Cannot dismiss: missing finding ID.");
        return;
      }
      alert("Dismissal would PATCH /api/runs/{runId}/findings/" + ev.data.id + ". Marking locally.");
      ev.data.status = "dismissed";
      renderEvidenceDrawer(ev);
    }

    if (text) {
      text.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
          e.preventDefault();
          submitIntent();
        }
      });
    }
    if (submit) {
      submit.addEventListener("click", function () { submitIntent(); });
    }
  });
})();
