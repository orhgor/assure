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
      var existing = docSurface ? docSurface.querySelectorAll(".doc-error") : [];
      for (var i = 0; i < existing.length; i++) existing[i].remove();
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
      } else if (event === "verified") {
        markDone("Math Check");
        transitionTo("Verify");
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
    var compareBodyEl  = document.getElementById("compare-body");
    var pinnedListEl   = document.getElementById("pinned-list");
    var modeTabs = document.querySelectorAll(".mode-tab");
    var compareInFlight = false;

    function setMode(name) {
      if (!pipelineModeEl || !compareModeEl) return;
      if (name === "compare") {
        pipelineModeEl.style.display = "none";
        compareModeEl.style.display  = "block";
      } else {
        compareModeEl.style.display  = "none";
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
        if (name === "compare") runCompare();
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
