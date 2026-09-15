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

  // ---------------------------------------------------------------
  // SHELL — single source of truth for UI state (pure refactor base).
  // Subsystems migrate onto this one at a time; none are migrated yet.
  // ---------------------------------------------------------------
  var SHELL = {
    project:  { id: null, title: "" },
    sources:  [],
    streams:  { draft: null, compareA: null, compareB: null },
    compare:  { a: null, b: null, inflight: false, loaded: false },
    document: { current: null, mode: "empty" },
    compiler: { ask: "", prompt: "", route: "" },
    pipeline: { activeIndex: null },
    ui: {
      leftTab: "sources",
      rightTab: "evidence",
      selection: { nodeId: null, evidence: null },
    },
  };

  function setShell(path, value) {
    var parts = path.split(".");
    var target = SHELL;
    for (var i = 0; i < parts.length - 1; i++) {
      if (!target[parts[i]]) target[parts[i]] = {};
      target = target[parts[i]];
    }
    target[parts[parts.length - 1]] = value;
    if (typeof _syncShellPathToDom === "function") {
      _syncShellPathToDom(path, value);
    }
  }

  function _syncShellPathToDom(path, value) {
    // TODO: per-path DOM sync during migration steps
  }
  var DRAFT_TYPE = "full";

  document.addEventListener("DOMContentLoaded", function () {
    var body = document.body;
    var docSurface = document.querySelector(".doc-surface");
    var docEmpty = docSurface ? docSurface.querySelector(".empty-hero") : null;
    var wrap = document.getElementById("dock-input-wrap");
    var text = document.getElementById("dock-text");
    var submit = document.getElementById("dock-submit");

    var newDraftBtn = docEmpty ? docEmpty.querySelector(".btn-primary") : null;
    if (newDraftBtn && text) {
      newDraftBtn.addEventListener("click", function () {
        // 0. Guard: a draft on screen is destructive to replace — confirm.
        var hasDraft = !!(currentJdfDocument || draftEl);
        if (hasDraft && !window.confirm("Start a new draft? Your current draft will be lost.")) { return; }
        // 1. abort everything in flight (abort BEFORE clearing, so no late
        //    callback rewrites the canvas).
        if (SHELL.streams.draft) { try { SHELL.streams.draft.abort(); } catch (_) {} }
        setShell("streams.draft", null);
        if (SHELL.streams.compareA) { try { SHELL.streams.compareA.abort(); } catch (_) {} }
        setShell("streams.compareA", null);
        if (SHELL.streams.compareB) { try { SHELL.streams.compareB.abort(); } catch (_) {} }
        setShell("streams.compareB", null);
        // 2. reset canvas + stages (restores the empty hero, nulls
        //    currentJdfDocument via clearDocument). Does NOT clear uploaded
        //    sources or reload the page.
        resetStages();
        clearDocument();
        // 3. reset compiler panel fields (YOUR ASK / COMPILED PROMPT / ROUTED TO).
        populateCompilerAsk("");
        setCompilerPrompt("");
        populateCompilerRoute("");
        // 4. switch to the Compiler tab + reset the dock.
        leftGroupSetTab("compiler");
        text.value = "";
        text.focus();
        try { text.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (_) {}
        text.classList.add("dock-pulse");
        setTimeout(function () { text.classList.remove("dock-pulse"); }, 700);
      });
    }

    // ---------------------------------------------------------------
    // Source Vault upload (SOURCES tab) — .txt / .md only locally
    // ---------------------------------------------------------------
    var sourceIds = [];   // uploaded substrate file ids (module-level)
    function sourceUploadError(msg) {
      try {
        var el = document.getElementById("source-list");
        if (el) {
          var row = document.createElement("div");
          row.className = "source-item is-error";
          row.textContent = msg;
          el.appendChild(row);
        }
      } catch (_) {}
      try { console.error("[shell] source upload:", msg); } catch (_) {}
    }
    function appendSourceItem(name, id) {
      var el = document.getElementById("source-list");
      if (!el) return;
      var row = document.createElement("div");
      row.className = "source-item";
      row.textContent = name;
      row.setAttribute("data-source-id", id || "");
      el.appendChild(row);
    }
    function readFileAsText(file) {
      return new Promise(function (resolve, reject) {
        var reader = new FileReader();
        reader.onload = function () { resolve(reader.result || ""); };
        reader.onerror = function () { reject(new Error("Could not read file.")); };
        reader.readAsText(file);
      });
    }
    function handleSourceFile(file) {
      if (!file) return;
      var name = file.name || "source.txt";
      if (!/\.(txt|md)$/i.test(name)) {
        sourceUploadError(
          "Only .txt or .md are supported in this shell. PDF and DOCX need Textract, which is not wired locally."
        );
        var fi = document.getElementById("source-file-input");
        if (fi) fi.value = "";
        return;
      }
      readFileAsText(file)
        .then(function (txt) {
          if (!txt || !String(txt).trim()) throw new Error("File is empty.");
          return ensureProjectId().then(function (pid) {
            return jsonPost("/api/substrate", {
              projectId: pid,
              filename: name,
              pageCount: 1,
              text: String(txt),
            });
          });
        })
        .then(function (resp) {
          if (!resp.ok) return resp.text().then(function (t) { throw new Error(t || ("HTTP " + resp.status)); });
          return resp.json();
        })
        .then(function (j) {
          if (!j || !j.id) throw new Error("No file id returned.");
          sourceIds.push(String(j.id));
          appendSourceItem(name, String(j.id));
        })
        .catch(function (err) {
          sourceUploadError(String(err && err.message ? err.message : err));
        })
        .then(function () {
          var fi = document.getElementById("source-file-input");
          if (fi) fi.value = "";
        });
    }
    var sourceUploadBtn = document.getElementById("source-upload-btn");
    var sourceFileInput = document.getElementById("source-file-input");
    if (sourceUploadBtn && sourceFileInput) {
      sourceUploadBtn.addEventListener("click", function () { sourceFileInput.click(); });
      sourceFileInput.addEventListener("change", function () {
        var f = sourceFileInput.files && sourceFileInput.files[0];
        if (f) handleSourceFile(f);
      });
    }

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
    function openLeft() { body.classList.remove("collapsed"); }

    // Rail icons open the correct tab in the left (generation) or right
    // (verification) column. leftGroupSetTab / rightGroupSetTab are declared
    // below (hoisted). Theme + settings buttons are untouched.
    document.querySelectorAll("[data-rail-btn]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var kind = btn.getAttribute("data-rail-btn");
        if (kind === "folder")        leftGroupSetTab("sources");
        else if (kind === "sparkle")  leftGroupSetTab("compiler");
        else if (kind === "activity") leftGroupSetTab("pipeline");
        else if (kind === "history")  leftGroupSetTab("history");
        else if (kind === "shield")   rightGroupSetTab("evidence");
        else if (kind === "swap")     rightGroupSetTab("compare");
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

    function renderJdfDocument(doc, targetEl) {
      if (!doc || !doc.body || !Array.isArray(doc.body)) return;
      if (targetEl) {
        // Alternate surface (compare column): clear it and populate with
        // JDF nodes. Does NOT touch the center-doc globals.
        targetEl.__jdfDoc = doc;
        while (targetEl.firstChild) targetEl.removeChild(targetEl.firstChild);
        targetEl.appendChild(renderConfidenceLegend());
        for (var j = 0; j < doc.body.length; j++) {
          var nodeElAlt = renderJdfNode(doc.body[j]);
          if (nodeElAlt) targetEl.appendChild(nodeElAlt);
        }
        return;
      }
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

    function addEvidenceChips(doc, targetEl) {
      if (!doc || !doc.body || !Array.isArray(doc.body)) return;
      var rootEl = targetEl || draftEl;
      function processNode(node) {
        if (!node || !node.id) return;
        var wrapper = rootEl ? rootEl.querySelector('.jdf-node[data-node-id="' + node.id + '"]') : null;
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
      if (rootEl) {
        var chips = rootEl.querySelectorAll(".chip");
        for (var c = 0; c < chips.length; c++) chips[c].addEventListener("click", handleChipClick);
      }
    }

    function applyConfidenceSpans(doc, targetEl) {
      if (!doc) return;
      // Confidence spans use field names startChar / endChar / nodeId
      // (NOT start / end / node_id). Prefer camelCase, fall back to
      // snake_case; both duplicate the same array on the live payload.
      var spans = doc.confidenceSpans || doc.confidence_spans ||
                  (doc.meta && (doc.meta.confidenceSpans || doc.meta.confidence_spans));
      if (!spans || !Array.isArray(spans) || spans.length === 0) return;
      // Scope node lookups to the target surface (compare column) when
      // provided; otherwise fall back to the whole document.
      var scopeEl = targetEl || document;

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
        var wrapper = scopeEl.querySelector('.jdf-node[data-node-id="' + nodeId + '"]');
        if (!wrapper) continue;
        var textEl = wrapper.querySelector(".jdf-p, .jdf-h2, .jdf-callout");
        if (!textEl) continue;
        var text = textEl.textContent || "";
        if (text.length === 0) continue;

        // Provenance-derived tooltip: source_name · p.page. Only used when
        // the node carries real provenance; otherwise fall back to the short
        // confidence reason. Never the 280-char excerpt in a tooltip.
        var provNode = findJdfNodeById(nodeId, doc);
        var provMeta = provNode ? (provNode.meta && provNode.meta.provenance) : null;
        var provList = Array.isArray(provMeta) ? provMeta : (provMeta ? [provMeta] : []);
        var prov0 = provList[0] || null;
        var provSrc = prov0 ? String(prov0.source_name || "") : "";
        var provPageRaw = prov0 ? prov0.page_number : "";
        var provPage = (provPageRaw != null && provPageRaw !== "") ? String(provPageRaw) : "";
        var provTitle = provSrc ? (provSrc + (provPage ? " \u00b7 p." + provPage : "")) : "";

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
          var confClass = "conf-span";
          if (prov0) {
            if (sp.score > 0.8) confClass += " conf-green";
            else if (sp.score >= 0.4) confClass += " conf-yellow";
            else confClass += " conf-red";
          }
          var title = prov0 ? (provTitle || String(sp.reason || "")) : "(no source matched)";
          var wrapped = '<span class="' + confClass + '" data-node-id="' + escapeAttr(nodeId) +
            '" data-score="' + escapeAttr(String(sp.score)) + '" title="' + escapeAttr(title) + '">' +
            escapeHtml(text.slice(start, end)) + "</span>";
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
        if (data && data.document) {
          var cdoc = data.document;
          if (cdoc && cdoc.body && Array.isArray(cdoc.body)) {
            renderJdfDocument(cdoc);
          } else {
            // Parse failure: never replace the document with raw text.
            try { console.error("[shell] compiled event missing parseable doc.body"); } catch (_) {}
          }
        }
      } else if (event === "verified") {
        markDone("Math Check");
        transitionTo("Verify");
        if (data && data.document) {
          currentJdfDocument = data.document;
          var vdoc = data.document;
          if (vdoc && vdoc.body && Array.isArray(vdoc.body)) {
            addEvidenceChips(vdoc);
            applyConfidenceSpans(vdoc);
          } else {
            try { console.error("[shell] verified event missing parseable doc.body"); } catch (_) {}
          }
        }
      } else if (event === "complete") {
        markDone("Verify");
        markActive("Complete");
        markDone("Complete");
        runInProgress = false;
        clearIntentSlot();
      } else if (event === "error") {
        var active = findActiveStage() || STAGE_ORDER[
          (currentStageIndex >= 0) ? currentStageIndex : 0
        ];
        markFailed(active);
        runInProgress = false;
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

    // ---------------------------------------------------------------
    // Project switcher (top bar dropdown)
    // ---------------------------------------------------------------
    var projectSwitcherBtn = document.getElementById("project-switcher");
    var projectSwitcherPanel = document.getElementById("project-switcher-panel");
    var projectCurrentNameEl = document.getElementById("project-current-name");
    var projectNewBtn = document.getElementById("project-new-btn");
    var projectListEl = document.getElementById("project-list");

    function _projectRelativeTime(ts) {
      if (!ts) return "never";
      var s = String(ts);
      var t = new Date(s.indexOf("T") >= 0 ? s : (s.replace(" ", "T") + "Z"));
      var diff = (Date.now() - (t ? t.getTime() : Date.now())) / 1000;
      if (diff < 60) return "just now";
      if (diff < 3600) return Math.floor(diff / 60) + "m ago";
      if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
      return Math.floor(diff / 86400) + "d ago";
    }
    function _openProjectPanel() {
      if (!projectSwitcherPanel) return;
      projectSwitcherPanel.hidden = false;
      if (projectSwitcherBtn) projectSwitcherBtn.setAttribute("aria-expanded", "true");
      _loadProjectsList();
    }
    function _closeProjectPanel() {
      if (!projectSwitcherPanel) return;
      projectSwitcherPanel.hidden = true;
      if (projectSwitcherBtn) projectSwitcherBtn.setAttribute("aria-expanded", "false");
    }
    function _loadProjectsList() {
      if (!projectListEl) return;
      fetch("/api/projects")
        .then(function (resp) { if (!resp.ok) throw new Error("projects GET " + resp.status); return resp.json(); })
        .then(function (j) {
          while (projectListEl.firstChild) projectListEl.removeChild(projectListEl.firstChild);
          var projects = (j && j.projects) || [];
          var totalProjects = projects.length;
          projects = projects.slice(0, 10);
          var active = "";
          try { active = window.localStorage.getItem(STORAGE_KEY) || ""; } catch (_) {}
          projects.forEach(function (p) {
            var row = document.createElement("button");
            row.type = "button";
            row.className = "project-row" + (p.id === active ? " is-active" : "");
            var title = document.createElement("span");
            title.className = "project-row-title";
            title.textContent = p.title || p.id;
            var meta = document.createElement("span");
            meta.className = "project-row-meta";
            meta.textContent = (p.source_count || 0) + " sources \u00b7 last modified " + _projectRelativeTime(p.updated_at);
            row.title = p.id;
            row.addEventListener("click", function () { _switchProject(p.id, p.title); });
            row.appendChild(title);
            row.appendChild(meta);
            projectListEl.appendChild(row);
          });
          if (totalProjects > projects.length) {
            var more = document.createElement("div");
            more.className = "project-list-more project-row-meta";
            more.textContent = "Showing 10 of " + totalProjects + " — older projects hidden";
            projectListEl.appendChild(more);
          }
        })
        .catch(function () {
          while (projectListEl.firstChild) projectListEl.removeChild(projectListEl.firstChild);
          var row = document.createElement("div");
          row.className = "project-row";
          row.textContent = "Could not load projects.";
          projectListEl.appendChild(row);
        });
    }
    function _refreshProjectName() {
      var active = "";
      try { active = window.localStorage.getItem(STORAGE_KEY) || ""; } catch (_) {}
      if (!active) { if (projectCurrentNameEl) projectCurrentNameEl.textContent = "Untitled"; return; }
      fetch("/api/projects")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          var projects = (j && j.projects) || [];
          var found = null;
          for (var i = 0; i < projects.length; i++) { if (projects[i].id === active) { found = projects[i]; break; } }
          if (found && projectCurrentNameEl) projectCurrentNameEl.textContent = found.title || "Untitled";
        })
        .catch(function () {});
    }
    function _loadProjectSourceList(id) {
      if (!id) return;
      fetch("/api/projects/" + encodeURIComponent(id) + "/substrate")
        .then(function (r) { return r.ok ? r.json() : { files: [] }; })
        .then(function (j) {
          var rows = (j && j.files) || [];
          sourceIds = rows.map(function (f) { return f.id; });
          var el = document.getElementById("source-list");
          if (el) {
            while (el.firstChild) el.removeChild(el.firstChild);
            rows.forEach(function (f) {
              var d = document.createElement("div");
            d.className = "source-item";
            d.textContent = f.filename || "";
            d.setAttribute("data-source-id", f.id || "");
            el.appendChild(d);
          });
        }
      })
      .catch(function () {});
    }
    var _switchToken = 0;
    function _switchProject(id, title) {
      _closeProjectPanel();
      if (!id) return;
      var myToken = ++_switchToken;
      // B) abort in-flight streams
      if (SHELL.streams.draft) { try { SHELL.streams.draft.abort(); } catch (_) {} }
      setShell("streams.draft", null);
      if (SHELL.streams.compareA) { try { SHELL.streams.compareA.abort(); } catch (_) {} }
      setShell("streams.compareA", null);
      if (SHELL.streams.compareB) { try { SHELL.streams.compareB.abort(); } catch (_) {} }
      setShell("streams.compareB", null);
      // C) persist active project
      try { window.localStorage.setItem(STORAGE_KEY, id); } catch (_) {}
      if (projectCurrentNameEl) projectCurrentNameEl.textContent = title || "Untitled";
      setShell("project.id", id);
      setShell("project.title", title || "");
      // D) fetch target project's latest document (parallel with E)
      fetch("/api/projects/" + encodeURIComponent(id) + "/jdf")
        .then(function (r) { return r.ok ? r.json() : {}; })
        .then(function (res) {
          if (myToken !== _switchToken) return;   // stale-switch guard
          var doc = (res && res.document) || null;
          var empty = !(doc && Array.isArray(doc.body) && doc.body.length);
          if (!empty) {
            renderJdfDocument(doc);
            setShell("document.current", doc);
            setShell("document.mode", "ready");
          } else {
            resetStages();
            clearDocument();
            setShell("document.mode", "empty");
          }
          // compiler panel is per-project — always reset (JDF carries no meta.ask)
          populateCompilerAsk("");
          setCompilerPrompt("");
          populateCompilerRoute("");
          leftGroupSetTab("compiler");
        })
        .catch(function (err) {
          if (myToken !== _switchToken) return;
          console.error("[switchProject] failed", id, err);
          resetStages();
          clearDocument();
          setShell("document.mode", "empty");
          populateCompilerAsk("");
          setCompilerPrompt("");
          populateCompilerRoute("");
          leftGroupSetTab("compiler");
        });
      // E) reload source list for the target (parallel)
      _loadProjectSourceList(id);
    }
    function _createNewProject() {
      var name = window.prompt("New project name", "Untitled");
      if (name === null) return;
      var title = String(name || "").trim() || "Untitled";
      jsonPost("/api/projects", { title: title })
        .then(function (resp) { if (!resp.ok) throw new Error("projects POST " + resp.status); return resp.json(); })
        .then(function (j) {
          var id = j && j.id;
          if (!id) throw new Error("no project id");
          try { window.localStorage.setItem(STORAGE_KEY, id); } catch (_) {}
          _closeProjectPanel();
          _switchProject(id, (j && j.title) || title);
        })
        .catch(function (err) { try { console.error("[shell] new project failed:", err); } catch (_) {} });
    }
    if (projectSwitcherBtn) {
      projectSwitcherBtn.addEventListener("click", function () {
        if (projectSwitcherPanel && projectSwitcherPanel.hidden) _openProjectPanel();
        else _closeProjectPanel();
      });
    }
    if (projectNewBtn) projectNewBtn.addEventListener("click", _createNewProject);
    document.addEventListener("click", function (e) {
      if (!projectSwitcherPanel || projectSwitcherPanel.hidden) return;
      var t = e.target;
      if (projectSwitcherBtn && projectSwitcherBtn.contains(t)) return;
      if (projectSwitcherPanel.contains(t)) return;
      _closeProjectPanel();
    });
    _refreshProjectName();

    // Export (top bar) → audit PDF download for the active project.
    var exportBtn = document.getElementById("export-btn");
    if (exportBtn) {
      exportBtn.addEventListener("click", function () {
        var id = SHELL.project.id;
        if (!id) {
          try { id = window.localStorage.getItem(STORAGE_KEY); } catch (_) { id = null; }
        }
        if (!id) {
          console.warn("[export] no active project");
          return;
        }
        window.location.href =
          "/api/projects/" + encodeURIComponent(id) + "/export?format=audit-pdf";
      });
    }

    function runDraft(intent) {
      // Abort any previous center draft stream, then start a fresh one.
      if (SHELL.streams.draft) { try { SHELL.streams.draft.abort(); } catch (_) {} }
      setShell("streams.draft", new AbortController());
      var thisRequest = SHELL.streams.draft;
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
            body: JSON.stringify({ intent: intent, compileType: DRAFT_TYPE, substrate_file_ids: sourceIds }),
            signal: SHELL.streams.draft.signal,
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
            // Stale guard: if a newer intent started, stop feeding this one
            // so late tokens can't pollute the fresh canvas.
            if (thisRequest !== SHELL.streams.draft) return undefined;
            return reader.read().then(function (chunk) {
              if (chunk.done) {
                parser.end();
                return;
              }
              if (thisRequest !== SHELL.streams.draft) return;
              var str = decoder.decode(chunk.value || new Uint8Array(0), { stream: true });
              parser.feed(str);
              return loop();
            }).catch(function (err) {
              // Abort is intentional — exit quietly, no error event.
              if (err && err.name === "AbortError") return;
              throw err;
            });
          }
          return loop();
        })
        .catch(function (err) {
          // A clean abort must not surface as an error or rewrite the canvas.
          if (err && err.name === "AbortError") {
            return;
          }
          if (!started) {
            resetStages();
          }
          handleEvent("error", { ok: false, error: String(err && err.message ? err.message : err) });
        });
    }

    // ---------------------------------------------------------------
    // Docked input submit wiring
    // ---------------------------------------------------------------
    // ---------------------------------------------------------------
    // Intent Compilation panel — two-step submit (preview, then Run)
    // ---------------------------------------------------------------
    var pendingIntent = null;     // raw user ask, waiting for Run
    var intentPanelOpen = false;
    var lastCompile = null;       // last /api/preview response
    var runInProgress = false;    // a draft/stream is actively running
    var healthSnapshot = null;    // last /health JSON when available

    function submitIntent() {
      if (!text) return;
      var v = String(text.value || "").trim();
      if (!v) return;
      text.value = "";
      try { window.sessionStorage.setItem("assure_last_intent", v); } catch (_) {}
      compareDataLoaded = false;
      compareClear();
      // A new intent invalidates any in-flight compare — abort both streams.
      if (SHELL.streams.compareA) { try { SHELL.streams.compareA.abort(); } catch (_) {} }
      setShell("streams.compareA", null);
      if (SHELL.streams.compareB) { try { SHELL.streams.compareB.abort(); } catch (_) {} }
      setShell("streams.compareB", null);
      if (SHELL.streams.draft) { try { SHELL.streams.draft.abort(); } catch (_) {} }
      setShell("streams.draft", null);
      beginIntentCompile(v);
    }

    var PREVIEW_TARGET = "claude";                       // for /api/preview only (bare slug)
    var COMPILE_MODEL  = "anthropic/claude-sonnet-4-5";  // for /draft/stream only (full id)

    function getCompilerAskEl()    { return document.getElementById("compiler-ask"); }
    function getCompilerPromptEl() { return document.getElementById("compiler-prompt"); }
    function getCompilerRouteEl()  { return document.getElementById("compiler-route"); }
    function populateCompilerAsk(text) {
      var el = getCompilerAskEl();
      if (el) el.textContent = text || "";
    }
    function setCompilerPrompt(text) {
      var el = getCompilerPromptEl();
      if (el) el.textContent = text || "";
    }
    function populateCompilerRoute(text) {
      var el = getCompilerRouteEl();
      if (el) el.textContent = text || "";
    }
    function renderCompilerRouteFrom(j) {
      var route = (j && j.target_ai) ? String(j.target_ai) : "";
      var intent = (j && j.intent) ? String(j.intent) : "";
      populateCompilerRoute(intent ? (intent + (route ? " \u00b7 " + route : "")) : route);
    }

    function beginIntentCompile(raw) {
      pendingIntent = raw;
      intentPanelOpen = true;
      lastCompile = null;
      // The compiler panel lives in the new COMPILER tab (right pane).
      setMode("compiler");
      populateCompilerAsk(raw);
      setCompilerPrompt("Compiling\u2026");
      populateCompilerRoute("");
      // Preview + draft stream run in parallel; do not wait for preview.
      // Payload is exactly {task, target_ai} — the backend calls
      // detect_intent(task) when intent is absent (web.py:1142), so we
      // intentionally omit the intent key.
      jsonPost("/api/preview", { task: raw, target_ai: PREVIEW_TARGET })
        .then(function (resp) {
          if (!resp.ok) throw new Error("preview HTTP " + resp.status);
          return resp.json();
        })
        .then(function (j) {
          lastCompile = j || null;
          if (j && typeof j.prompt === "string" && j.prompt.length > 0) {
            setCompilerPrompt(j.prompt);
          } else {
            setCompilerPrompt("(compiler unavailable)");
          }
          renderCompilerRouteFrom(j);
        })
        .catch(function (err) {
          setCompilerPrompt("(compiler unavailable)");
          try { console.error("[shell] preview error:", err && err.message ? err.message : err); } catch (_) {}
        });
      // Fire the draft/stream now, in parallel.
      runPendingIntent();
    }

    function runPendingIntent() {
      if (!pendingIntent) return;
      var raw = pendingIntent;
      intentPanelOpen = false;
      renderIntentSummary(raw);
      runInProgress = true;
      runDraft(raw); // ORIGINAL raw ask, NOT the compiled prompt
    }

    function runAnyIntent(raw) {
      if (!raw) return;
      intentPanelOpen = false;
      clearIntentSlot();
      runInProgress = true;
      runDraft(raw);
    }

    function cancelIntent() {
      if (pendingIntent && text) text.value = pendingIntent;
      pendingIntent = null;
      intentPanelOpen = false;
      lastCompile = null;
      clearIntentSlot();
    }

    function expandIntentPanel() {
      if (!pendingIntent) return;
      intentPanelOpen = true;
      setMode("compiler");
      populateCompilerAsk(pendingIntent);
      if (lastCompile && typeof lastCompile.prompt === "string" && lastCompile.prompt.length > 0) {
        setCompilerPrompt(lastCompile.prompt);
        renderCompilerRouteFrom(lastCompile);
      } else {
        setCompilerPrompt("(compiler unavailable)");
      }
    }

    function clearIntentSlot() {
      if (!intentPanelSlot) return;
      while (intentPanelSlot.firstChild) intentPanelSlot.removeChild(intentPanelSlot.firstChild);
    }

    function makeIntentPanelShell(titleText) {
      clearIntentSlot();
      var panel = document.createElement("div");
      panel.className = "intent-panel";
      var title = document.createElement("h3");
      title.className = "intent-panel-title";
      title.textContent = titleText;
      panel.appendChild(title);
      return panel;
    }

    function appendIntentSection(panel, labelText, bodyEl) {
      var section = document.createElement("div");
      section.className = "intent-section";
      var label = document.createElement("span");
      label.className = "intent-section-label";
      label.textContent = labelText;
      section.appendChild(label);
      section.appendChild(bodyEl);
      panel.appendChild(section);
    }

    function makeCancelButton() {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "intent-action cancel";
      b.textContent = "Cancel";
      b.addEventListener("click", cancelIntent);
      return b;
    }

    function makeActionButton(label, handler) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "intent-action";
      b.textContent = label;
      if (label === "Run" || label === "Run anyway") b.classList.add("primary");
      b.addEventListener("click", handler);
      return b;
    }

    function renderIntentLoading(raw) {
      if (!intentPanelSlot) return;
      var panel = makeIntentPanelShell("INTENT COMPILATION");
      var loading = document.createElement("p");
      loading.className = "intent-loading";
      loading.textContent = "Compiling intent\u2026";
      panel.appendChild(loading);
      var footer = document.createElement("div");
      footer.className = "intent-actions";
      footer.appendChild(makeCancelButton());
      panel.appendChild(footer);
      intentPanelSlot.appendChild(panel);
    }

    function renderIntentFailure(raw) {
      if (!intentPanelSlot) return;
      var panel = makeIntentPanelShell("INTENT COMPILATION");
      var msg = document.createElement("p");
      msg.className = "intent-failure";
      msg.textContent = "Could not compile intent.";
      panel.appendChild(msg);
      var footer = document.createElement("div");
      footer.className = "intent-actions";
      footer.appendChild(makeActionButton("Run anyway", function () { runAnyIntent(raw); }));
      footer.appendChild(makeCancelButton());
      panel.appendChild(footer);
      intentPanelSlot.appendChild(panel);
    }

    function renderIntentPanel(raw, data, health) {
      if (!intentPanelSlot) return;
      healthSnapshot = (health && typeof health === "object") ? health : null;
      var panel = makeIntentPanelShell("INTENT COMPILATION");
      var askBody = document.createElement("blockquote");
      askBody.className = "intent-ask";
      askBody.textContent = raw;
      appendIntentSection(panel, "YOUR ASK", askBody);
      var promptBody = document.createElement("pre");
      promptBody.className = "intent-prompt";
      promptBody.textContent = (data && data.prompt) || "";
      appendIntentSection(panel, "COMPILED PROMPT", promptBody);
      // ROUTED TO — shown only when /health supplies orchestrator_models.
      var om = healthSnapshot ? healthSnapshot.orchestrator_models : null;
      if (om && typeof om === "object") {
        var routedBody = document.createElement("div");
        routedBody.className = "intent-routed";
        var any = false;
        for (var kk in om) {
          if (Object.prototype.hasOwnProperty.call(om, kk) && om[kk]) {
            var row = document.createElement("div");
            row.textContent = String(kk) + ": " + String(om[kk]);
            routedBody.appendChild(row);
            any = true;
          }
        }
        if (any) appendIntentSection(panel, "ROUTED TO", routedBody);
      }
      var checksBody = document.createElement("p");
      checksBody.className = "intent-checks";
      checksBody.textContent = "Z3 numeric \u00b7 Red-Hat \u00b7 Provenance \u00b7 Confidence";
      appendIntentSection(panel, "VERIFICATION CHECKS", checksBody);
      var footer = document.createElement("div");
      footer.className = "intent-actions";
      footer.appendChild(makeCancelButton());
      footer.appendChild(makeActionButton("Run", runPendingIntent));
      panel.appendChild(footer);
      intentPanelSlot.appendChild(panel);
    }

    function renderIntentSummary(raw) {
      if (!intentPanelSlot) return;
      clearIntentSlot();
      var bar = document.createElement("div");
      bar.className = "intent-summary";
      var textEl = document.createElement("span");
      textEl.className = "intent-summary-text";
      textEl.textContent = "\u2713 Intent compiled \u00b7 4 checks";
      var viewBtn = document.createElement("button");
      viewBtn.type = "button";
      viewBtn.className = "intent-summary-view";
      viewBtn.textContent = "view";
      viewBtn.addEventListener("click", expandIntentPanel);
      bar.appendChild(textEl);
      bar.appendChild(viewBtn);
      intentPanelSlot.appendChild(bar);
    }

    // ---------------------------------------------------------------
    // Two independent tab groups:
    //   left  — Sources | Compiler | Pipeline | History  (generation)
    //   right — Evidence | Compare                       (verification)
    // ---------------------------------------------------------------
    var LAST_INTENT_KEY = "assure_last_intent";
    var PINS_KEY = "assure_pins";

    var leftSourcesEl  = document.getElementById("left-sources");
    var leftCompilerEl = document.getElementById("left-compiler");
    var leftPipelineEl = document.getElementById("left-pipeline");
    var leftHistoryEl  = document.getElementById("left-history");
    var evidenceModeEl = document.getElementById("right-evidence");
    var compareModeEl  = document.getElementById("right-compare");
    var compareBodyEl  = document.getElementById("compare-body");
    var evidenceBodyEl = document.getElementById("evidence-body");
    var intentPanelSlot = document.getElementById("intent-panel-slot");
    var pinnedListEl   = document.getElementById("pinned-list"); // removed; helpers no-op
    var compareInFlight = false;
    var compareDataLoaded = false;
    var lastCompareJdfA = null;     // most recent JDF rendered into column A
    var lastCompareJdfB = null;     // most recent JDF rendered into column B
    var compareStreamsDone = 0;     // number of compare streams finished/errored

    var LEFT_TABPANE = {
      sources:   leftSourcesEl,
      compiler:  leftCompilerEl,
      pipeline:  leftPipelineEl,
      history:   leftHistoryEl,
    };
    var RIGHT_TABPANE = {
      evidence: evidenceModeEl,
      compare:  compareModeEl,
    };

    function openLeftPane() { body.classList.remove("collapsed"); }
    function openRightPane() { openRight(); }

    function _syncTabActive(list, attr, activeName) {
      list.forEach(function (t) {
        if (t.getAttribute(attr) === activeName) {
          t.classList.add("is-active");
          t.setAttribute("aria-selected", "true");
        } else {
          t.classList.remove("is-active");
          t.setAttribute("aria-selected", "false");
        }
      });
    }

    function leftGroupSetTab(name) {
      var panels = LEFT_TABPANE;
      if (!Object.prototype.hasOwnProperty.call(panels, name)) {
        var keys = Object.keys(panels);
        name = keys.length ? keys[0] : name;
      }
      Object.keys(panels).forEach(function (k) {
        if (panels[k]) panels[k].style.display = (k === name) ? "block" : "none";
      });
      _syncTabActive(document.querySelectorAll("[data-left-tab]"), "data-left-tab", name);
      openLeftPane();
      return name;
    }

    function rightGroupSetTab(name) {
      var panels = RIGHT_TABPANE;
      if (!Object.prototype.hasOwnProperty.call(panels, name)) {
        var keys = Object.keys(panels);
        name = keys.length ? keys[0] : name;
      }
      Object.keys(panels).forEach(function (k) {
        if (panels[k]) panels[k].style.display = (k === name) ? "block" : "none";
      });
      _syncTabActive(document.querySelectorAll("[data-right-tab]"), "data-right-tab", name);
      openRight();
      return name;
    }

    // Backward-compatible dispatch used by existing flows.
    function setMode(name) {
      if (name === "evidence" || name === "compare") {
        rightGroupSetTab(name);
        return;
      }
      leftGroupSetTab(name);
    }

    function setCompareDisabled(disabled) {
      compareInFlight = !!disabled;
      document.querySelectorAll("[data-right-tab=\"compare\"]").forEach(function (t) {
        if (disabled) t.classList.add("is-disabled");
        else          t.classList.remove("is-disabled");
      });
    }

    document.querySelectorAll("[data-left-tab]").forEach(function (t) {
      t.addEventListener("click", function () {
        leftGroupSetTab(t.getAttribute("data-left-tab"));
      });
    });
    document.querySelectorAll("[data-right-tab]").forEach(function (t) {
      t.addEventListener("click", function () {
        if (t.classList.contains("is-disabled")) return;
        var name = rightGroupSetTab(t.getAttribute("data-right-tab"));
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
    function compareColumnShell(key, modelId) {
      // key: "claude" | "deepseek" — used for the column title.
      var title = (key === "claude") ? "CLAUDE" : "DEEPSEEK";
      var col = document.createElement("div");
      col.className = "compare-col";

      var head = document.createElement("div");
      head.className = "compare-col-head";
      var t1 = document.createElement("div"); t1.className = "compare-col-title"; t1.textContent = title;
      var t2 = document.createElement("div"); t2.className = "compare-col-model"; t2.textContent = modelId || "\u2014";
      head.appendChild(t1); head.appendChild(t2);
      col.appendChild(head);

      var body = document.createElement("div");
      body.className = "compare-col-body";
      body.textContent = "\u2026";
      col.appendChild(body);

      var acts = document.createElement("div");
      acts.className = "compare-col-actions";
      var btnAccept = document.createElement("button");
      btnAccept.type = "button";
      btnAccept.className = "compare-btn";
      btnAccept.textContent = "Accept";
      btnAccept.setAttribute("disabled", "disabled");
      btnAccept.addEventListener("click", function () { acceptCompareColumn(col); });
      var btnPin = document.createElement("button");
      btnPin.type = "button";
      btnPin.className = "compare-btn";
      btnPin.textContent = "Pin";
      btnPin.addEventListener("click", function () {
        var doc = col.__jdf;
        var pinText = (doc && doc.draft_text) ? doc.draft_text
          : (doc && Array.isArray(doc.body) ? JSON.stringify(doc.body) : "");
        appendPin(pinText || (modelId + " \u2014 nothing to pin yet"), modelId);
      });
      acts.appendChild(btnAccept);
      acts.appendChild(btnPin);
      col.appendChild(acts);

      col.__key = key;
      col.__modelId = modelId;
      col.__jdf = null;
      col.__rendered = false;
      col.__textNode = null;
      col.__acceptBtn = btnAccept;
      col.__body = body;
      return col;
    }

    function compareAppendToken(col, delta) {
      if (!col || col.__rendered || !col.__body) return;
      if (!col.__textNode) {
        col.__body.textContent = "";
        col.__textNode = document.createTextNode("");
        col.__body.appendChild(col.__textNode);
      }
      col.__textNode.nodeValue += delta;
    }

    function compareShowError(col, msg) {
      if (!col || !col.__body) return;
      col.__body.classList.add("is-error");
      col.__body.textContent = (msg || "Compare stream failed.");
      if (col.__acceptBtn) col.__acceptBtn.setAttribute("disabled", "disabled");
    }

    function acceptCompareColumn(col) {
      var doc = (col && col.__jdf) || null;
      if (!docSurface) return;
      // 1. Clear the center document.
      clearDocument();
      if (docEmpty) docEmpty.style.display = "none";
      if (!doc || !doc.body || !Array.isArray(doc.body)) {
        setMode("pipeline");
        return;
      }
      // Re-render the JDF object fresh into the center (targetEl null →
      // center path, which re-wires draftEl + currentJdfDocument so all
      // event listeners + interactions work). Do NOT paste text or copy
      // the column's innerHTML.
      renderJdfDocument(doc);
      applyConfidenceSpans(doc);
      addEvidenceChips(doc);
      var oldErrs = docSurface.querySelectorAll(".doc-error");
      for (var i = 0; i < oldErrs.length; i++) oldErrs[i].remove();
      docSurface.scrollTop = 0;
      setMode("pipeline");
    }

    function compareStreamFinished() {
      compareStreamsDone += 1;
      // Only re-enable the tab once BOTH streams finish (or error). A
      // failing stream must not block the healthy sibling.
      if (compareStreamsDone >= 2) setCompareDisabled(false);
    }
    function compareStreamSide(col, modelId, storeKey) {
      // storeKey: "A" | "B" — writes lastCompareJdfA/B.
      var controller = new AbortController();
      var intent;
      try { intent = window.sessionStorage.getItem(LAST_INTENT_KEY); } catch (_) { intent = null; }
      if (!intent) { compareShowError(col, "No stored intent."); return controller; }

      var parser = parseSseLoop(
        function (event, data) {
          if (event === "[DONE]") return;
          if (!data || typeof data !== "object") return;
          var t = data.type || event;
          if (t === "token" && typeof data.delta === "string") {
            compareAppendToken(col, data.delta);
          } else if (t === "status") {
            if (data.stage === "model" && data.model && col.__modelId) {
              var mt = col.querySelector(".compare-col-model");
              if (mt) mt.textContent = data.model;
            }
          } else if (t === "compiled") {
            var doc = data.document;
            if (doc && doc.body && Array.isArray(doc.body)) {
              col.__jdf = doc;
              if (storeKey === "A") lastCompareJdfA = doc;
              else lastCompareJdfB = doc;
              col.__rendered = true;
              renderJdfDocument(doc, col.__body);
              if (col.__acceptBtn) col.__acceptBtn.removeAttribute("disabled");
            }
          } else if (t === "verified") {
            if (data.document) {
              applyConfidenceSpans(data.document, col.__body);
              addEvidenceChips(data.document, col.__body);
            }
          } else if (t === "error") {
            compareShowError(col, data.error || "Compare stream error.");
          }
        },
        function () { compareStreamFinished(); },
        function (err) {
          compareShowError(col, String(err && err.message ? err.message : err));
          compareStreamFinished();
        }
      );

      ensureProjectId()
        .then(function (projectId) {
          var url = "/api/projects/" + encodeURIComponent(projectId) + "/draft/stream";
          return fetch(url, {
            method: "POST",
            signal: controller.signal,
            headers: { "Content-Type": "application/json", "Accept": "text/event-stream, application/json" },
            body: JSON.stringify({ intent: intent, compileType: "full", target_ai: modelId, substrate_file_ids: sourceIds }),
          });
        })
        .then(function (resp) {
          if (!resp.ok) {
            return resp.text().then(function (t) { throw new Error(t || ("HTTP " + resp.status)); });
          }
          if (!resp.body) throw new Error("Response body unavailable.");
          var reader = resp.body.getReader();
          var decoder = new TextDecoder("utf-8");
          function loop() {
            return reader.read().then(function (chunk) {
              if (chunk.done) { parser.end(); return; }
              var str = decoder.decode(chunk.value || new Uint8Array(0), { stream: true });
              parser.feed(str);
              return loop();
            });
          }
          return loop();
        })
        .catch(function (err) {
          if (err && err.name === "AbortError") { compareStreamFinished(); return; }
          compareShowError(col, String(err && err.message ? err.message : err));
          compareStreamFinished();
        });

      return controller;
    }

    // ---------------------------------------------------------------
    // Compare — two parallel draft/stream SSE requests (Claude + DeepSeek)
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
      // Abort any prior compare streams before starting fresh.
      if (SHELL.streams.compareA) { try { SHELL.streams.compareA.abort(); } catch (_) {} }
      setShell("streams.compareA", null);
      if (SHELL.streams.compareB) { try { SHELL.streams.compareB.abort(); } catch (_) {} }
      setShell("streams.compareB", null);

      setCompareDisabled(true);
      compareStreamsDone = 0;
      compareClear();
      lastCompareJdfA = null;
      lastCompareJdfB = null;

      var grid = document.createElement("div");
      grid.className = "compare-grid";
      compareBodyEl.appendChild(grid);

      var colA = compareColumnShell("claude", "anthropic/claude-sonnet-4-5");
      var colB = compareColumnShell("deepseek", "deepseek/deepseek-chat");
      grid.appendChild(colA);
      grid.appendChild(colB);

      compareDataLoaded = true;         // do not refire on tab re-click
      setShell("streams.compareA", compareStreamSide(colA, "anthropic/claude-sonnet-4-5", "A"));
      setShell("streams.compareB", compareStreamSide(colB, "deepseek/deepseek-chat", "B"));
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

    function findJdfNodeById(nodeId, tree) {
      var root = tree || currentJdfDocument;
      if (!root) return null;
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
      return find(root.body || []);
    }

    function handleConfidenceClick(e) {
      var span = e.currentTarget;
      var nodeId = span.getAttribute("data-node-id");
      if (!nodeId) return;
      // Resolve the owning tree: a compare column keeps its own doc on
      // __jdfDoc; otherwise fall back to the center/accepted document.
      var host = span.closest(".compare-col-body, .doc-draft, .doc-surface");
      var tree = (host && host.__jdfDoc) ? host.__jdfDoc : currentJdfDocument;
      var node = findJdfNodeById(nodeId, tree);
      renderConfidenceEvidence(span, node);
      currentEvidence = { kind: "confidence", nodeId: nodeId, data: {} };
      openRight();
      setMode("evidence");
    }

    function renderConfidenceEvidence(span, node) {
      if (!evidenceBodyEl) return;
      var nodeId = span ? span.getAttribute("data-node-id") : "";
      var scoreRaw = span ? span.getAttribute("data-score") : "";
      var score = parseFloat(scoreRaw);
      if (isNaN(score)) score = 0;

      function field(label, value) {
        var s = String(value == null ? "" : value);
        if (!s) return null;
        var f = document.createElement("div");
        f.className = "evidence-field";
        var l = document.createElement("div");
        l.className = "evidence-label";
        l.textContent = label;
        var v = document.createElement("div");
        v.className = "evidence-value";
        v.textContent = s;
        f.appendChild(l);
        f.appendChild(v);
        return f;
      }

      while (evidenceBodyEl.firstChild) evidenceBodyEl.removeChild(evidenceBodyEl.firstChild);

      var prov = node && node.meta && node.meta.provenance;
      var provs = Array.isArray(prov) ? prov : (prov ? [prov] : []);
      var p0 = provs[0] || null;

      var header = document.createElement("div");
      header.className = "evidence-header";
      if (!p0) {
        header.textContent = "Evidence \u00b7 no source matched";
        evidenceBodyEl.appendChild(header);
        var empty = document.createElement("div");
        empty.className = "evidence-content";
        var emptyMsg = document.createElement("p");
        emptyMsg.className = "evidence-value";
        emptyMsg.textContent = "The generated text did not match any sentence in the uploaded sources.";
        empty.appendChild(emptyMsg);
        evidenceBodyEl.appendChild(empty);
        return;
      }

      var srcName = String(p0.source_name || "");
      var pageRaw = p0.page_number;
      var pageStr = (pageRaw != null && pageRaw !== "") ? String(pageRaw) : "";
      header.textContent = "Evidence \u00b7 " + (srcName || "source") +
        (pageStr ? " \u00b7 page " + pageStr : "");
      evidenceBodyEl.appendChild(header);

      var content = document.createElement("div");
      content.className = "evidence-content";

      var excerpt = String(p0.excerpt || p0.extracted_quote || "");
      if (excerpt) {
        var quote = document.createElement("blockquote");
        quote.className = "evidence-blockquote";
        quote.textContent = excerpt;
        content.appendChild(quote);
      }

      var f;
      if ((f = field("Source", srcName))) content.appendChild(f);
      if (pageStr && (f = field("Page", pageStr))) content.appendChild(f);
      if ((f = field("Rule", p0.rule))) content.appendChild(f);
      if ((f = field("Confidence", p0.confidence))) content.appendChild(f);
      evidenceBodyEl.appendChild(content);

      var foot = document.createElement("div");
      foot.className = "evidence-footer";
      var scoreText = (score <= 1) ? (Math.round(score * 100) + "%") : String(score);
      foot.textContent = "Verification score: " + scoreText;
      evidenceBodyEl.appendChild(foot);
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
