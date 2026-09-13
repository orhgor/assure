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
    var docEmpty = docSurface ? docSurface.querySelector(".doc-empty") : null;
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
      runDraft(v);
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
