(function () {
  "use strict";

  var STAGE_ORDER = [
    // B2: the stage's display name is also its key (`stageRow` matches
    // `[data-stage]`, index.html authors the row). The SSE frame value stays
    // the server's `preflight` (handleEvent below) — this is the label only.
    "Preparing",
    "Drafting",
    "Lock Inference",
    "Compile",
    "Math Check",
    "Verify",
    "Complete",
  ];
  var STORAGE_KEY = "assure_project";

  // ---------------------------------------------------------------
  // The entry gate. /auth leaves the key in localStorage and the POST
  // sets the cookie; here we put it on every same-origin call as
  // `X-Shell-Key` so the API is closed even if the cookie is dropped.
  // A 401 means the key is wrong or was rotated — go back to the gate.
  // ---------------------------------------------------------------
  var ACCESS_KEY_STORAGE = "assure_shell_key";

  function accessKey() {
    try { return window.localStorage.getItem(ACCESS_KEY_STORAGE) || ""; } catch (_) { return ""; }
  }

  (function attachAccessKey() {
    var raw = window.fetch;
    if (typeof raw !== "function") return;
    window.fetch = function (input, init) {
      var url = typeof input === "string" ? input : ((input && input.url) || "");
      var sameOrigin = url.charAt(0) === "/" || url.indexOf(window.location.origin) === 0;
      var key = accessKey();
      if (key && sameOrigin) {
        init = init || {};
        var hdrs = init.headers;
        if (typeof Headers !== "undefined" && hdrs instanceof Headers) {
          if (!hdrs.has("X-Shell-Key")) hdrs.set("X-Shell-Key", key);
        } else if (Object.prototype.toString.call(hdrs) === "[object Array]") {
          var present = false;
          for (var i = 0; i < hdrs.length; i++) {
            if (String(hdrs[i][0]).toLowerCase() === "x-shell-key") present = true;
          }
          if (!present) hdrs.push(["X-Shell-Key", key]);
        } else {
          var merged = {};
          if (hdrs) {
            for (var k in hdrs) {
              if (Object.prototype.hasOwnProperty.call(hdrs, k)) merged[k] = hdrs[k];
            }
          }
          if (!Object.prototype.hasOwnProperty.call(merged, "X-Shell-Key")) merged["X-Shell-Key"] = key;
          init.headers = merged;
        }
      }
      return raw.call(this, input, init).then(function (resp) {
        if (resp && resp.status === 401 && sameOrigin) {
          try { window.location.replace("/auth"); } catch (_) {}
        }
        return resp;
      });
    };
  })();

  // ---------------------------------------------------------------
  // SSE failure instrumentation (staging diagnostics). Logs only —
  // no retry/behavior change, no UI, no toasts.
  // "tokensReceived" is the number of characters decoded from the SSE
  // body before the stream failed (a token proxy, uniform across all
  // four streaming call sites).
  // ---------------------------------------------------------------
  function logSseFailure(endpoint, startedAt, tokensReceived, err, wasAbort) {
    var failedAt = Date.now();
    var payload = {
      endpoint: endpoint,
      startedAt: new Date(startedAt).toISOString(),
      failedAt: new Date(failedAt).toISOString(),
      durationMs: Math.max(0, Math.round(failedAt - startedAt)),
      tokensReceived: tokensReceived,
      errorName: (err && err.name) || null,
      errorMessage: (err && err.message) || (err ? String(err) : "unknown"),
      wasAbort: !!wasAbort,
    };
    try { console.warn("[sse-failure]", payload); } catch (_) {}
  }

  // ---------------------------------------------------------------
  // SHELL — single source of truth for UI state (pure refactor base).
  // Subsystems migrate onto this one at a time; none are migrated yet.
  // ---------------------------------------------------------------
  var SHELL = {
    project:  { id: null, title: "" },
    sources:  [],
    streams:  { draft: null, compareA: null, compareB: null },
    compare:  { a: null, b: null, inflight: false, loaded: false },
    document: { current: null, mode: "empty", versions: { list: [], current: null }, signoff: { status: "draft" } },
    compiler: { ask: "", prompt: "", route: "" },
    ui: {
      leftTab: "sources",
      rightTab: "evidence",
      selection: { nodeId: null, evidence: null },
      modal: null,
      layout: { leftWidth: 280, rightWidth: 320, leftCollapsed: false, rightCollapsed: false },
    },
  };

  var compilerAskEl = null;
  var compilerPromptEl = null;
  var compilerRouteEl = null;
  var projectCurrentNameEl = null;
  var leftSourcesEl = null;
  var leftCompilerEl = null;
  var leftHistoryEl = null;
  var leftReferencesEl = null;
  var leftTemplatesEl = null;
  var evidenceModeEl = null;
  var compareModeEl = null;
  var exportBtnEl = null;
  var docBodyEl = null;
  var versionChipEl = null;
  var versionPrevEl = null;
  var versionNextEl = null;
  var versionLabelEl = null;
  var versionDropdownEl = null;
  var z3ModeEl = null;
  var redhatModeEl = null;
  var progressEl = null;
  var rightInspectorEl = null;
  var compareToggleEl = null;
  var rightModeToggleEl = null;
  var _applyRightViewFn = null;
  var _renderManifestFn = null;
  var _syncCountersFn = null;
  var inspectorCompareActive = false;

  function setShell(path, value) {
    var parts = path.split(".");
    var target = SHELL;
    for (var i = 0; i < parts.length - 1; i++) {
      if (!target[parts[i]]) target[parts[i]] = {};
      target = target[parts[i]];
    }
    var leaf = parts[parts.length - 1];
    // The previous value travels with the write: the two paths that own the
    // right pane repaint only when their value actually changed (§6), and only
    // the writer knows what it replaced.
    var prev = target[leaf];
    target[leaf] = value;
    if (typeof _syncShellPathToDom === "function") {
      _syncShellPathToDom(path, value, prev);
    }
  }

  function _syncShellPathToDom(path, value, prev) {
    if (path === "document.current") {
      // The document is rendered by renderJdfDocument; the caller sets current
      // explicitly. What follows it is Export's availability and the SOURCES
      // manifest (its "anchored N of M" is read off the document).
      _syncExportEnabled();
      if (_renderManifestFn) _renderManifestFn();
    } else if (path === "document.mode") {
      // The mode is the other half of "there is a document" (_canExport), and
      // the manifest's counters follow the document too.
      _syncExportEnabled();
      if (_renderManifestFn) _renderManifestFn();
    } else if (path === "document.signoff.status") {
      var el = document.getElementById("signoff-indicator");
      if (!el) return;
      if (value === "signed") {
        el.textContent = "\u25cf Signed";
        el.classList.add("is-signed");
      } else {
        el.textContent = "\u25cf Draft";
        el.classList.remove("is-signed");
      }
    } else if (path === "compiler.ask") {
      if (compilerAskEl) compilerAskEl.textContent = value;
      _syncCompilerSections();
    } else if (path === "compiler.prompt") {
      if (compilerPromptEl) compilerPromptEl.textContent = value;
      _syncCompilerSections();
    } else if (path === "compiler.route") {
      if (compilerRouteEl) compilerRouteEl.textContent = value;
      _syncCompilerSections();
    } else if (path === "sources") {
      // The grounding list is what every compile posts as substrate_file_ids
      // and what the SOURCES manifest counts, so the pane follows any write
      // to it (upload, ingest, remove).
      _syncCompilerSections();
      if (_renderManifestFn) _renderManifestFn();
      if (_syncCountersFn) _syncCountersFn();
    } else if (path === "project.id") {
      try { window.localStorage.setItem(STORAGE_KEY, value); } catch (_) {}
    } else if (path === "project.title") {
      if (projectCurrentNameEl) projectCurrentNameEl.textContent = value || "Untitled";
    } else if (path === "ui.leftTab") {
      var lp = { sources: leftSourcesEl, compiler: leftCompilerEl, history: leftHistoryEl, references: leftReferencesEl, templates: leftTemplatesEl };
      Object.keys(lp).forEach(function (k) {
        if (lp[k]) lp[k].style.display = (k === value) ? "block" : "none";
      });
      document.querySelectorAll("[data-left-tab]").forEach(function (t) {
        if (t.getAttribute("data-left-tab") === value) {
          t.classList.add("is-active");
          t.setAttribute("aria-selected", "true");
        } else {
          t.classList.remove("is-active");
          t.setAttribute("aria-selected", "false");
        }
      });
      setShell("ui.layout.leftCollapsed", false);
    } else if (path === "ui.rightTab") {
      document.querySelectorAll("[data-right-tab]").forEach(function (t) {
        if (t.getAttribute("data-right-tab") === value) {
          t.classList.add("is-active");
          t.setAttribute("aria-selected", "true");
        } else {
          t.classList.remove("is-active");
          t.setAttribute("aria-selected", "false");
        }
      });
      setShell("ui.layout.rightCollapsed", false);
      // §6: the pane is a function of (tab, selected node, evidence payload).
      // Re-writing the tab it already has replaces no input, so it repaints
      // nothing — a click that also writes a selection must not pay for a
      // second build of the same surface. Compare is the exception: leaving it
      // is a change even when the tab name is unchanged.
      if (value === prev && !inspectorCompareActive) return;
      inspectorCompareActive = false;
      if (_applyRightViewFn) _applyRightViewFn();
    } else if (path === "ui.selection.nodeId") {
      var prevSel = document.querySelector(".doc-draft .jdf-node.is-selected");
      if (prevSel) prevSel.classList.remove("is-selected");
      if (value) {
        var selEl = document.querySelector('.doc-draft .jdf-node[data-node-id="' + String(value) + '"]');
        if (selEl) selEl.classList.add("is-selected");
      }
      // Same guard, same reason: re-selecting the node that is already
      // selected replaces nothing the pane reads. Callers that need the side
      // effects for an unchanged id (the doc surface's rephrase editor and
      // node history) call them directly.
      if (value === prev && !inspectorCompareActive) return;
      if (typeof _loadNodeHistory === "function") _loadNodeHistory(value);
      if (typeof _attachNodeRephrase === "function") _attachNodeRephrase(value);
      inspectorCompareActive = false;
      if (_applyRightViewFn) _applyRightViewFn();
    } else if (path === "ui.selection.evidence") {
      // §6: the evidence payload is the pane's third input — a z3/cite chip's
      // drawer, or a confidence span's ledger tail. The click handlers write it
      // BEFORE the node id, so it belongs to a node that is not selected yet
      // and repaints nothing; the node id write then paints once, with the
      // payload already in place. Re-clicking the item that is already
      // selected changes only this input, so this write is what repaints.
      var sel = SHELL.ui.selection || {};
      var onScreen = (value && value.nodeId === sel.nodeId) ||
                     (!value && prev && prev.nodeId === sel.nodeId);
      if (onScreen && _applyRightViewFn) _applyRightViewFn();
    } else if (path === "ui.layout.leftWidth") {
      document.documentElement.style.setProperty("--left-w", value + "px");
      // The resizer is an interactive separator, so its value has to be in the
      // accessibility tree, not only in the grid.
      var rl = document.getElementById("resize-left");
      if (rl) rl.setAttribute("aria-valuenow", String(value));
    } else if (path === "ui.layout.rightWidth") {
      document.documentElement.style.setProperty("--right-w", value + "px");
      var rr = document.getElementById("resize-right");
      if (rr) rr.setAttribute("aria-valuenow", String(value));
    } else if (path === "ui.layout.leftCollapsed") {
      if (value) docBodyEl.classList.add("collapsed");
      else       docBodyEl.classList.remove("collapsed");
      try { localStorage.setItem("assure.left_collapsed", value ? "1" : "0"); } catch (_) {}
    } else if (path === "ui.layout.rightCollapsed") {
      if (value) docBodyEl.classList.add("right-hidden");
      else       docBodyEl.classList.remove("right-hidden");
      _setRightPaneHidden(Boolean(value));   // §3: inert/aria-hidden + tabindex fallback
      try { localStorage.setItem("assure.right_collapsed", value ? "1" : "0"); } catch (_) {}
    } else if (path === "ui.modal") {
      var layer = document.getElementById("modal-layer");
      if (!layer) return;
      if (!value) {
        layer.hidden = true;
        layer.innerHTML = "";
        return;
      }
      layer.hidden = false;
      if (value === "shortcuts") {
        layer.innerHTML =
          '<div class="modal">' +
            '<div class="modal-header">' +
              '<h2>Keyboard shortcuts</h2>' +
              '<button type="button" id="modal-close" aria-label="Close shortcut list">✕</button>' +
            '</div>' +
            '<ul class="shortcut-list">' +
              '<li><kbd>Cmd+B</kbd> Toggle left pane</li>' +
              '<li><kbd>Cmd+J</kbd> Toggle right pane</li>' +
              '<li><kbd>Cmd+.</kbd> Focus mode</li>' +
              '<li><kbd>?</kbd> This dialog</li>' +
              '<li><kbd>Enter</kbd> Submit ask</li>' +
              '<li><kbd>Esc</kbd> Cancel / close</li>' +
            '</ul>' +
          '</div>';
        document.getElementById("modal-close").addEventListener(
          "click", function () { setShell("ui.modal", null); }
        );
      } else {
        layer.innerHTML = "<div class='modal'>Modal: " + value + "</div>";
      }
    }
  }

  // ---- Right-pane reachability (§3) --------------------------------
  // body.right-hidden hides the pane with `visibility: hidden`, which takes
  // the whole subtree out of the tab order and the accessibility tree, so
  // revealing the pane never handed focus to anything reachable. The hidden
  // state is made explicit here and focus is moved on reveal.
  //
  // `inert` is used where the engine supports it. No browser target is
  // stated anywhere in this repo (grep "inert" and any browserslist both
  // return nothing), so the explicit fallback — aria-hidden plus
  // tabindex="-1" on every focusable descendant, restored on reveal — is
  // applied as well; it also keeps the subtree out of the tab order if the
  // visibility rule is ever refactored.
  var rightPaneEl = null;
  var rightPaneTabStops = [];
  function _rightPane() {
    if (!rightPaneEl) rightPaneEl = document.getElementById("pane-right");
    return rightPaneEl;
  }
  function _setRightPaneHidden(hidden) {
    var pane = _rightPane();
    if (!pane) return;
    if ("inert" in pane) pane.inert = Boolean(hidden);
    if (hidden) {
      pane.setAttribute("aria-hidden", "true");
      rightPaneTabStops = [];
      var els = pane.querySelectorAll("a[href], button, input, select, textarea, [tabindex]");
      for (var i = 0; i < els.length; i++) {
        rightPaneTabStops.push([els[i], els[i].getAttribute("tabindex")]);
        els[i].setAttribute("tabindex", "-1");
      }
    } else {
      pane.removeAttribute("aria-hidden");
      for (var j = 0; j < rightPaneTabStops.length; j++) {
        var pair = rightPaneTabStops[j];
        if (pair[1] === null) pair[0].removeAttribute("tabindex");
        else pair[0].setAttribute("tabindex", pair[1]);
      }
      rightPaneTabStops = [];
    }
  }
  function _focusRightPaneDefault() {
    var pane = _rightPane();
    if (!pane) return;
    var target = pane.querySelector("[data-right-tab].is-active") ||
                 pane.querySelector("[data-right-tab]");
    if (target && typeof target.focus === "function") target.focus();
  }
  function _focusRedhatRun() {
    var pane = _rightPane();
    if (!pane) return;
    var run = pane.querySelector(".redhat-run");
    // §3: the run button is the target when it can take focus. While a run is
    // in flight that button is deliberately disabled (a second run is refused)
    // and a disabled control cannot be focused, so the fallback is the pane's
    // active tab — always reachable, and the one control that swaps the pane.
    if (run && !run.disabled && typeof run.focus === "function") { run.focus(); return; }
    _focusRightPaneDefault();
  }
  function openRight() {
    // Focus is handed over only when this call is what revealed the pane —
    // a compile that merely re-expands an already-open pane must not steal
    // focus from the dock.
    var wasCollapsed = Boolean(SHELL.ui.layout.rightCollapsed);
    setShell("ui.layout.rightCollapsed", false);
    if (wasCollapsed) _focusRightPaneDefault();
  }

  // B4: Export is a top-bar action that had nothing to act on until a project
  // document was loaded, and the click reached a console.warn and stopped. The
  // shell's pattern for a control whose action is unavailable is to disable it
  // and sync that from state — the dock's Submit (_syncDockSubmit), the version
  // stepper, the Red-Hat run button — rather than to raise a message from the
  // top bar (jdfMessage writes into the dock's search panel). So Export is
  // disabled while it cannot export, and enabled again when it can.
  function _exportProjectId() {
    var id = SHELL.project.id;
    if (!id) {
      try { id = window.localStorage.getItem(STORAGE_KEY); } catch (_) { id = null; }
    }
    return id || "";
  }
  function _canExport() {
    // The document is what there is to export; the id is what the export URL
    // is addressed to. Both, or the button has nothing to do.
    return Boolean(SHELL.document.current) &&
           SHELL.document.mode === "ready" &&
           Boolean(_exportProjectId());
  }
  function _syncExportEnabled() {
    if (!exportBtnEl) return;
    exportBtnEl.disabled = !_canExport();
  }

  // The compiler pane's sections own their empty state: a section with no
  // value is hidden (data-state="empty"), never rendered as a dash. ROUTED TO
  // is the one section that is always present — before a compile it says so.
  function _syncCompilerSections() {
    var askSection = document.getElementById("section-ask");
    if (askSection) {
      askSection.setAttribute("data-state", (SHELL.compiler.ask || "") ? "ready" : "empty");
    }
    var promptSection = document.getElementById("section-prompt");
    if (promptSection) {
      promptSection.setAttribute("data-state", (SHELL.compiler.prompt || "") ? "ready" : "empty");
    }
    var routeSection = document.getElementById("section-route");
    if (routeSection) {
      routeSection.setAttribute("data-state", (SHELL.compiler.route || "") ? "ready" : "awaiting");
    }
    // Before a compile the section says where it stands rather than sitting
    // empty: "Awaiting route" is a state, and it is muted.
    var routeEl = document.getElementById("compiler-route");
    if (routeEl && !SHELL.compiler.route) routeEl.textContent = "Awaiting route";
  }
  var DRAFT_TYPE = "full";

  document.addEventListener("DOMContentLoaded", function () {
    var body = document.body;
    docBodyEl = body;
    // Restore persisted pane widths (before first render) so the grid
    // reflects them from the start.
    try {
      var lw = parseInt(localStorage.getItem("assure.left_w"), 10);
      if (!isNaN(lw)) SHELL.ui.layout.leftWidth  = Math.min(600, Math.max(200, lw));
      var rw = parseInt(localStorage.getItem("assure.right_w"), 10);
      if (!isNaN(rw)) SHELL.ui.layout.rightWidth = Math.min(720, Math.max(320, rw));
    } catch (_) {}
    setShell("ui.layout.leftWidth",  SHELL.ui.layout.leftWidth);
    setShell("ui.layout.rightWidth", SHELL.ui.layout.rightWidth);
    var docSurface = document.querySelector(".doc-surface");
    progressEl = document.getElementById("app-progress");
    compilerAskEl    = document.getElementById("compiler-ask");
    compilerPromptEl = document.getElementById("compiler-prompt");
    compilerRouteEl  = document.getElementById("compiler-route");
    versionChipEl     = document.getElementById("version-chip");
    versionPrevEl     = document.getElementById("version-prev");
    versionNextEl     = document.getElementById("version-next");
    versionLabelEl    = document.getElementById("version-label");
    versionDropdownEl = document.getElementById("version-dropdown");
    _syncCompilerSections();
    if (versionPrevEl) versionPrevEl.addEventListener("click", function () { _versionStep(-1); });
    if (versionNextEl) versionNextEl.addEventListener("click", function () { _versionStep(1); });
    if (versionLabelEl) versionLabelEl.addEventListener("click", function () {
      if (versionDropdownEl) versionDropdownEl.hidden = !versionDropdownEl.hidden;
      versionLabelEl.setAttribute("aria-expanded",
        versionDropdownEl && !versionDropdownEl.hidden ? "true" : "false");
    });
    var wrap = document.getElementById("dock-input-wrap");
    var text = document.getElementById("dock-text");
    var submit = document.getElementById("dock-submit");

    // ---------------------------------------------------------------
    // Source Vault upload (SOURCES tab) — .txt / .md only locally
    // ---------------------------------------------------------------
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
    function _sourceProjectId() {
      try {
        return SHELL.project.id || window.localStorage.getItem(STORAGE_KEY) || "";
      } catch (_) { return SHELL.project.id || ""; }
    }
    function _isSourceIncluded(id) {
      var src = SHELL.sources || [];
      return src.some(function (s) { return String(s) === String(id); });
    }
    function _patchSourceIncluded(id, included, checkboxEl) {
      fetch("/api/projects/" + encodeURIComponent(_sourceProjectId()) + "/substrate/" + encodeURIComponent(id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ included: included }),
      })
        .then(function (r) {
          if (!r.ok) throw new Error("PATCH substrate " + r.status);
          var src = (SHELL.sources || []).map(String);
          if (included) {
            if (src.indexOf(String(id)) < 0) src.push(String(id));
          } else {
            src = src.filter(function (s) { return s !== String(id); });
          }
          setShell("sources", src);
        })
        .catch(function (err) {
          if (checkboxEl) checkboxEl.checked = !included;
          try { console.warn("[shell] source toggling failed:", err); } catch (_) {}
        });
    }
    function _removeSource(id, row) {
      if (!window.confirm("Remove this source?")) return;
      fetch("/api/projects/" + encodeURIComponent(_sourceProjectId()) + "/substrate/" + encodeURIComponent(id), {
        method: "DELETE",
      })
        .then(function (r) {
          if (!r.ok) throw new Error("DELETE substrate " + r.status);
          setShell("sources", (SHELL.sources || []).map(String).filter(function (s) { return s !== String(id); }));
          _refreshManifest();
          if (row && row.parentNode) row.parentNode.removeChild(row);
        })
        .catch(function (err) {
          try { console.warn("[shell] source remove failed:", err); } catch (_) {}
        });
    }
    function _buildSourceRow(name, id, row) {
      var rowEl = document.createElement("div");
      rowEl.className = "source-item";
      rowEl.setAttribute("data-source-id", String(id || ""));
      var label = document.createElement("label");
      label.className = "source-include";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.setAttribute("data-source-id", String(id || ""));
      cb.checked = _isSourceIncluded(id);
      label.appendChild(cb);
      var nameEl = document.createElement("span");
      nameEl.textContent = name || "";
      var flagEl = _buildSourceFlag(row);
      var rm = document.createElement("button");
      rm.type = "button";
      rm.className = "source-remove";
      rm.setAttribute("aria-label", "Remove source");
      rm.textContent = "\u00d7";
      cb.addEventListener("change", function () { _patchSourceIncluded(id, cb.checked, cb); });
      rm.addEventListener("click", function () { _removeSource(id, rowEl); });
      rowEl.appendChild(label);
      rowEl.appendChild(nameEl);
      if (flagEl) rowEl.appendChild(flagEl);
      rowEl.appendChild(rm);
      return rowEl;
    }
    function appendSourceItem(name, id) {
      var el = document.getElementById("source-list");
      if (!el) return;
      el.appendChild(_buildSourceRow(name, id));
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
      // Upload through the ownership-gated vault route, not /api/substrate:
      // that one is the edge Worker's text ingest and is gated on a shared
      // secret the browser cannot hold. The server reads .txt/.md directly.
      ensureProjectId()
        .then(function (pid) {
          var fd = new FormData();
          fd.append("file", file, name);
          return fetch("/api/projects/" + encodeURIComponent(pid) + "/substrate/upload", {
            method: "POST",
            body: fd,
          }).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (j) {
              return { status: resp.status, ok: resp.ok, j: j || {} };
            });
          });
        })
        .then(function (r) {
          if (r.status === 202 && r.j.task_id) return r.j; // queued async
          if (!r.ok || r.j.ok === false) {
            throw new Error(r.j.error || ("HTTP " + r.status));
          }
          return r.j;
        })
        .then(function (j) {
          if (!j || !j.id) throw new Error("No file id returned.");
          setShell("sources", SHELL.sources.concat([String(j.id)]));
          appendSourceItem(name, String(j.id));
          _refreshManifest();
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
    // JDF ingest + search (MVP). No toast helper exists — use panel div.
    // FIX 1 made the routes project-scoped: /api/projects/<id>/jdf/*
    // ---------------------------------------------------------------
    function jdfMessage(text, isErr) {
      var panel = document.getElementById("dock-search-results");
      if (!panel) return;
      panel.hidden = false;
      var row = document.createElement("div");
      row.textContent = text;
      if (isErr) { try { row.style.color = "#e5484d"; } catch (_) {} }
      panel.appendChild(row);
    }
    // The dock's ingest and search are project-scoped server routes, and the
    // tenant "default" project is not the session project: a compile mints
    // `shell-proto-XXXXXX` via ensureProjectId(), so an upload that fell back
    // to "default" would index text (and, server-side, attach the substrate
    // entry) to a project the source panel, the compile and the search never
    // look at. Resolve the session project the same way the compile does.
    function jdfProject() {
      return ensureProjectId().then(function (pid) {
        return { pid: pid, base: "/api/projects/" + encodeURIComponent(pid) + "/jdf" };
      });
    }
    var jdfIngestBtn = document.getElementById("dock-ingest");
    var jdfIngestFile = document.getElementById("dock-ingest-file");
    if (jdfIngestBtn && jdfIngestFile) {
      jdfIngestBtn.addEventListener("click", function () { jdfIngestFile.click(); });
      jdfIngestFile.addEventListener("change", function () {
        var f = jdfIngestFile.files && jdfIngestFile.files[0];
        if (!f) return;
        var panel = document.getElementById("dock-search-results");
        if (panel) { panel.hidden = false; panel.innerHTML = ""; }
        jdfMessage("Converting → Chunking → Indexing…", false);
        var fd = new FormData();
        fd.append("file", f);
        var ingestPid = "";
        jdfProject()
          .then(function (p) {
            ingestPid = p.pid;
            return fetch(p.base + "/ingest", { method: "POST", body: fd });
          })
          .then(function (res) {
            return res.json().catch(function () { return {}; }).then(function (j) {
              return { ok: res.ok, status: res.status, j: j };
            });
          })
          .then(function (r) {
            if (r.ok && r.j && r.j.ok) {
              jdfMessage((r.j.chunks_stored || 0) + " figures found in " + f.name, false);
              // The ingest scan flags instruction-like source content; the
              // source still ingests, and the SOURCES manifest labels it.
              if (r.j.instruction_like) {
                jdfMessage(f.name + " \u2014 " + (r.j.instruction_flag_label || ""), false);
              }
              // The ingest also lands a substrate entry for this project, so
              // re-read the project's sources from the server: SHELL.sources is
              // what the next compile posts as substrate_file_ids and what the
              // compiler summary counts. Ids come from /substrate verbatim.
              _loadProjectSourceList(ingestPid);
              // New sources change the grounding surface the counters speak
              // about — re-derive it instead of leaving a stale snapshot.
              _syncGroundingNotices(null);
            } else {
              jdfMessage(String((r.j && r.j.error) || ("Ingest failed (HTTP " + r.status + ")")), true);
            }
            jdfIngestFile.value = "";
          })
          .catch(function (err) {
            jdfMessage(String(err && err.message ? err.message : err), true);
            jdfIngestFile.value = "";
          });
      });
    }
    var jdfSearch = document.getElementById("dock-search");
    if (jdfSearch) {
      jdfSearch.addEventListener("keydown", function (e) {
        if (e.key !== "Enter") return;
        var q = jdfSearch.value.trim();
        var panel = document.getElementById("dock-search-results");
        if (panel) { panel.hidden = false; panel.innerHTML = ""; }
        if (!q) { jdfMessage("Enter a query", false); return; }
        jdfProject()
          .then(function (p) {
            return fetch(p.base + "/search", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ query: q }),
            });
          })
          .then(function (res) {
            return res.json().catch(function () { return {}; }).then(function (j) {
              return { ok: res.ok, status: res.status, j: j };
            });
          })
          .then(function (r) {
            if (!r.ok) {
              jdfMessage(String((r.j && r.j.error) || ("Search failed (HTTP " + r.status + ")")), true);
              return;
            }
            var results = (r.j && r.j.results) || [];
            if (!results.length) { jdfMessage("No matches", false); return; }
            results.forEach(function (it) {
              if (!panel) return;
              // Clicking a hit opens it in the editor as a rephrase-able node.
              // Deliberately a direct-child <div>: several E2E specs select
              // `#dock-search-results > div`, so the element type is contract.
              var row = document.createElement("div");
              row.className = "dock-search-hit";
              row.setAttribute("role", "button");
              row.setAttribute("tabindex", "0");
              row.setAttribute("aria-label", "Open this result in the editor");
              row.style.cursor = "pointer";
              row.__hit = it;
              var head = document.createElement("div");
              head.textContent = "• " + String(it.doc_id || "doc");
              var snippet = String(it.text || "");
              var bodyEl = document.createElement("div");
              bodyEl.textContent = snippet.length > 160 ? snippet.slice(0, 160) + "…" : snippet;
              row.appendChild(head);
              row.appendChild(bodyEl);
              function activateHit() {
                _openJdfSearchResultInEditor(row.__hit, { query: q });
              }
              row.addEventListener("click", activateHit);
              row.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activateHit(); }
              });
              panel.appendChild(row);
            });
          })
          .catch(function (err) {
            jdfMessage(String(err && err.message ? err.message : err), true);
          });
      });
    }

    // ---------------------------------------------------------------
    // Left / right pane toggles (from phase 1)
    // ---------------------------------------------------------------
    var leftCollapse = document.getElementById("left-collapse");
    if (leftCollapse) {
      leftCollapse.addEventListener("click", function () {
        setShell("ui.layout.leftCollapsed", !SHELL.ui.layout.leftCollapsed);
      });
    }
    var rightClose = document.getElementById("right-close");
    function closeRight() { setShell("ui.layout.rightCollapsed", true); }
    if (rightClose) rightClose.addEventListener("click", closeRight);
    function openLeft() { setShell("ui.layout.leftCollapsed", false); }

    // Cmd+B / Cmd+J collapse toggles (workbench shortcuts). Input guard:
    // never toggle while the user is typing in a field.
    document.addEventListener("keydown", function (e) {
      var t = e.target;
      if (t && (t.isContentEditable
                || t.tagName === "INPUT"
                || t.tagName === "TEXTAREA")) return;
      if (!(e.metaKey || e.ctrlKey)) return;
      if (e.shiftKey) return;   // L1c owns Cmd+Shift+*
      var k = (e.key || "").toLowerCase();
      if (k === "b") {
        e.preventDefault();
        setShell("ui.layout.leftCollapsed", !SHELL.ui.layout.leftCollapsed);
      } else if (k === "." || k === ">") {
        e.preventDefault();
        var bothCollapsed =
          SHELL.ui.layout.leftCollapsed &&
          SHELL.ui.layout.rightCollapsed;
        var target = !bothCollapsed;
        setShell("ui.layout.leftCollapsed",  target);
        setShell("ui.layout.rightCollapsed", target);
      } else if (k === "j") {
        e.preventDefault();
        // §3: a keyboard reveal must hand focus to the pane, so this goes
        // through the opener rather than toggling the class directly.
        if (SHELL.ui.layout.rightCollapsed) openRight();
        else closeRight();
      }
    });

    // ESC closes any open modal. Separate listener — the Cmd/B handler
    // guards on metaKey/ctrlKey and would never see bare ESC.
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      var t = e.target;
      if (t && (t.isContentEditable
                || t.tagName === "INPUT"
                || t.tagName === "TEXTAREA")) return;
      if (SHELL.ui.modal) {
        e.preventDefault();
        setShell("ui.modal", null);
      }
    });

    // "?" opens the keyboard-shortcuts modal. Separate listener — the
    // Cmd/B handler requires metaKey and the ESC handler only closes.
    // Guarded so the char still types normally in editable fields.
    document.addEventListener("keydown", function (e) {
      if (e.key !== "?") return;
      var t = e.target;
      if (t && (t.isContentEditable
                || t.tagName === "INPUT"
                || t.tagName === "TEXTAREA")) return;
      e.preventDefault();
      setShell("ui.modal", "shortcuts");
    });

    // ---------------------------------------------------------------
    // Pane resizers (left / center / right). Width lives in
    // SHELL.ui.layout and is reflected by CSS variables; no library.
    // ---------------------------------------------------------------
    var _railFallback = 48;
    function _railPx() {
      var raw = parseInt(window.getComputedStyle(document.documentElement).getPropertyValue("--rail-w"), 10);
      return isNaN(raw) ? _railFallback : raw;
    }
    function _wireResizer(el, side) {
      if (!el) return;
      var startX = 0, startWidth = 0, dragging = false;
      var MIN_LEFT = 200, MIN_RIGHT = 320, MIN_CENTER = 400;

      el.addEventListener("pointerdown", function (e) {
        dragging = true;
        startX = e.clientX;
        startWidth = side === "left" ? SHELL.ui.layout.leftWidth : SHELL.ui.layout.rightWidth;
        el.classList.add("dragging");
        document.body.classList.add("resizing");
        try { el.setPointerCapture(e.pointerId); } catch (_) {}
        e.preventDefault();
      });

      el.addEventListener("pointermove", function (e) {
        if (!dragging) return;
        var delta = e.clientX - startX;
        var railTotal = 2 * _railPx();  // two rails (left + right)
        var other = side === "left" ? SHELL.ui.layout.rightWidth : SHELL.ui.layout.leftWidth;
        var maxForSide = window.innerWidth - railTotal - other - MIN_CENTER;
        var next;
        if (side === "left") {
          next = Math.min(maxForSide, Math.max(MIN_LEFT, startWidth + delta));
          setShell("ui.layout.leftWidth", next);
        } else {
          next = Math.min(maxForSide, Math.max(MIN_RIGHT, startWidth - delta));
          setShell("ui.layout.rightWidth", next);
        }
      });

      el.addEventListener("pointerup", function (e) {
        if (!dragging) return;
        dragging = false;
        el.classList.remove("dragging");
        document.body.classList.remove("resizing");
        try { el.releasePointerCapture(e.pointerId); } catch (_) {}
        try {
          localStorage.setItem(
            side === "left" ? "assure.left_w" : "assure.right_w",
            String(side === "left" ? SHELL.ui.layout.leftWidth : SHELL.ui.layout.rightWidth)
          );
        } catch (_) {}
      });

      el.addEventListener("pointercancel", function () {
        dragging = false;
        el.classList.remove("dragging");
        document.body.classList.remove("resizing");
      });

      // Keyboard accessibility: arrow keys 8px (32px with Shift).
      el.addEventListener("keydown", function (e) {
        var step = e.shiftKey ? 32 : 8;
        var cur = side === "left" ? SHELL.ui.layout.leftWidth : SHELL.ui.layout.rightWidth;
        var next = cur;
        if (e.key === "ArrowLeft")  next = side === "left" ? cur - step : cur + step;
        if (e.key === "ArrowRight") next = side === "left" ? cur + step : cur - step;
        // Clamp keyboard moves to the same min/max as pointer drags.
        var railTotal = 2 * _railPx();
        var other = side === "left" ? SHELL.ui.layout.rightWidth : SHELL.ui.layout.leftWidth;
        var maxForSide = window.innerWidth - railTotal - other - MIN_CENTER;
        var minForSide = side === "left" ? MIN_LEFT : MIN_RIGHT;
        next = Math.min(maxForSide, Math.max(minForSide, next));
        if (next !== cur) {
          e.preventDefault();
          setShell(side === "left" ? "ui.layout.leftWidth" : "ui.layout.rightWidth", next);
          try {
            localStorage.setItem(
              side === "left" ? "assure.left_w" : "assure.right_w", String(next)
            );
          } catch (_) {}
        }
      });
    }
    _wireResizer(document.getElementById("resize-left"),  "left");
    _wireResizer(document.getElementById("resize-right"), "right");

    // Rail icons open the correct tab in the left (generation) or right
    // (verification) column. leftGroupSetTab / rightGroupSetTab are declared
    // below (hoisted). Theme + settings buttons are untouched.
    document.querySelectorAll("[data-rail-btn]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var kind = btn.getAttribute("data-rail-btn");
        if (kind === "folder")        leftGroupSetTab("sources");
        else if (kind === "sparkle")  leftGroupSetTab("compiler");
        else if (kind === "history")  leftGroupSetTab("history");
        else if (kind === "shield")   rightGroupSetTab("evidence");
        else if (kind === "swap")     _toggleCompareView();
      });
    });


    // ---------------------------------------------------------------
    // Docked input focus styles (phase 1)
    // ---------------------------------------------------------------
    if (wrap && text) {
      text.addEventListener("focus", function () { wrap.classList.add("focused"); });
      text.addEventListener("blur", function () { wrap.classList.remove("focused"); });
      // YOUR ASK mirrors the dock: with no compile yet the pane still shows the
      // intent as typed rather than an empty section.
      text.addEventListener("input", function () {
        populateCompilerAsk(String(text.value || ""));
      });
    }

    // ---------------------------------------------------------------
    // Stages. The pane shows the pipeline as four stages — Retrieve, Draft,
    // Anchor, Verify — while the stream still reports its seven finer steps.
    // The finer steps remain the state (STAGE_ORDER above is their order);
    // the four rows are the view, and they are what the header's progress
    // line reads. A stage ticks when every step under it has landed.
    // ---------------------------------------------------------------
    var STAGE_GROUPS = [
      { name: "Retrieve", steps: ["Preparing"] },
      { name: "Draft",    steps: ["Drafting", "Lock Inference"] },
      { name: "Anchor",   steps: ["Compile", "Math Check"] },
      { name: "Verify",   steps: ["Verify", "Complete"] },
    ];
    var STAGE_PROGRESS = { Retrieve: 0.25, Draft: 0.5, Anchor: 0.75, Verify: 1 };
    var stageState = {};    // finer step -> "active" | "done" | "failed" | "skipped"
    var stageReason = {};   // finer step -> why it was skipped

    function stageRow(name) {
      return document.querySelector('.stage-row[data-stage="' + name + '"]');
    }
    function _groupState(group) {
      var active = false, failed = false, landed = 0;
      for (var i = 0; i < group.steps.length; i++) {
        var s = stageState[group.steps[i]];
        if (s === "active") active = true;
        else if (s === "failed") failed = true;
        else if (s === "done" || s === "skipped") landed++;
      }
      if (failed) return "failed";
      if (active) return "active";
      if (landed === group.steps.length) return "done";
      return "idle";
    }
    // The 2px line under the header: how far the run has got, in ink. No
    // spinner, no overlay, no skeleton — the line and the ticks are the
    // whole progress signal.
    function _syncProgress() {
      if (!progressEl) return;
      var frac = 0;
      for (var i = 0; i < STAGE_GROUPS.length; i++) {
        var state = _groupState(STAGE_GROUPS[i]);
        if (state === "done" || state === "active") {
          frac = STAGE_PROGRESS[STAGE_GROUPS[i].name];
        }
      }
      var bar = progressEl.firstElementChild;
      if (bar) bar.style.transform = "scaleX(" + frac + ")";
    }
    function _renderStages() {
      for (var i = 0; i < STAGE_GROUPS.length; i++) {
        var group = STAGE_GROUPS[i];
        var row = stageRow(group.name);
        if (!row) continue;
        var state = _groupState(group);
        row.classList.remove("active", "done", "failed", "skipped");
        if (state !== "idle") row.classList.add(state);
        var notes = [];
        for (var j = 0; j < group.steps.length; j++) {
          var why = stageReason[group.steps[j]];
          if (why) notes.push(group.steps[j] + " skipped: " + why);
        }
        if (notes.length) row.setAttribute("title", notes.join("; "));
        else row.removeAttribute("title");
      }
      _syncProgress();
    }
    function _startProgress() {
      if (!progressEl) return;
      progressEl.hidden = false;
      var bar = progressEl.firstElementChild;
      if (bar) bar.style.transform = "scaleX(0)";
    }
    function _endProgress() {
      if (!progressEl) return;
      // Hold the full line for a beat so the finish is visible, then clear it.
      setTimeout(function () {
        if (progressEl) progressEl.hidden = true;
      }, 700);
    }
    function resetStages() {
      stageState = {};
      stageReason = {};
      _renderStages();
      if (progressEl) progressEl.hidden = true;
    }
    function findActiveStage() {
      for (var i = STAGE_ORDER.length - 1; i >= 0; i--) {
        if (stageState[STAGE_ORDER[i]] === "active") return STAGE_ORDER[i];
      }
      return null;
    }
    function markDone(name)   { stageState[name] = "done"; _renderStages(); }
    function markActive(name) { stageState[name] = "active"; _renderStages(); }
    function markFailed(name) { stageState[name] = "failed"; _renderStages(); }
    // A stage that had nothing to check is neither done nor failed: a green
    // dot would claim a pass that never ran (Math Check with 0 metrics).
    function markSkipped(name, reason) {
      stageState[name] = "skipped";
      if (reason) stageReason[name] = reason;
      _renderStages();
    }

    // ---------------------------------------------------------------
    // Document area helpers
    // ---------------------------------------------------------------
    var draftEl = null;
    function ensureDraftArea() {
      setShell("document.mode", "streaming");
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
    // A refused compile (HTTP 422, nothing persisted) streams its draft tokens
    // before the server can judge the finished document, but the refusal is the
    // verdict on exactly that text: leaving it on the canvas would render the
    // draft the server would not keep. Drop it, keep the refusal.
    function _discardStreamedDraft() {
      if (draftEl && draftEl.parentNode) draftEl.parentNode.removeChild(draftEl);
      draftEl = null;
      setShell("document.mode", "empty");
    }
    // The ingest scan's verdict on a source, as the SOURCES label. The label
    // itself comes from the server (services/compile_guard.SOURCE_FLAG_LABEL);
    // the hover detail names the phrases that matched — evidence, not a score.
    function _sourceFlagLabel(row) {
      if (!row || !row.instruction_like) return "";
      return String(row.instruction_flag_label || "");
    }
    function _buildSourceFlag(row) {
      var label = _sourceFlagLabel(row);
      if (!label) return null;
      var hits = (row && Array.isArray(row.instruction_hits)) ? row.instruction_hits : [];
      var el = document.createElement("span");
      el.className = "source-flag";
      el.textContent = label;
      el.title = hits.length ? "Matched: " + hits.join(", ") : label;
      return el;
    }
    function clearDocument() {
      setShell("document.mode", "empty");
      if (draftEl && draftEl.parentNode) draftEl.parentNode.removeChild(draftEl);
      draftEl = null;
      var existing = docSurface ? docSurface.querySelectorAll(".doc-error") : [];
      for (var i = 0; i < existing.length; i++) existing[i].remove();
      if (versionChipEl) versionChipEl.hidden = true;
      SHELL.document.versions = { list: [], current: null };
    }

    function _loadVersionHistory(projectId, opts) {
      fetch("/api/projects/" + encodeURIComponent(projectId) + "/history")
        .then(function (r) { return r.ok ? r.json() : {}; })
        .then(function (res) {
          var list = (res && res.revisions) || [];
          list.sort(function (a, b) { return (b.version || 0) - (a.version || 0); });
          SHELL.document.versions = SHELL.document.versions || { list: [], current: null };
          SHELL.document.versions.list = list;
          if (list.length) {
            if (opts && opts.current === "latest") {
              SHELL.document.versions.current = list[0].version;
            } else if (opts && opts.current != null) {
              SHELL.document.versions.current = opts.current;
            } else if (!SHELL.document.versions.current) {
              SHELL.document.versions.current = list[0].version;
            }
          }
          _renderVersionChip();
        })
        .catch(function () {});
    }
    function _loadNodeHistory(nodeId) {
      var details = document.getElementById("right-node-history");
      var list = document.getElementById("right-node-history-list");
      if (!details || !list) return;
      if (!nodeId) {
        list.innerHTML = "";
        details.open = false;
        return;
      }
      var pid = _sourceProjectId && _sourceProjectId() || (function () {
        try { return window.localStorage.getItem(STORAGE_KEY) || ""; } catch (_) { return ""; }
      })();
      fetch("/api/projects/" + encodeURIComponent(pid) + "/nodes/" + encodeURIComponent(nodeId) + "/history")
        .then(function (r) { return r.ok ? r.json() : {}; })
        .then(function (j) {
          var l = document.getElementById("right-node-history-list");
          if (!l) return;
          l.innerHTML = "";
          var revs = (j && j.revisions) || [];
          if (!revs.length) {
            l.innerHTML = "<li class='empty-hint'>No prior revisions.</li>";
            return;
          }
          revs.forEach(function (r) {
            var li = document.createElement("li");
            li.textContent = "v" + r.version + " \u00b7 " + (r.timestamp || "\u2014");
            l.appendChild(li);
          });
        })
        .catch(function () {
          var l = document.getElementById("right-node-history-list");
          if (l) l.innerHTML = "<li class='empty-hint'>No prior revisions.</li>";
        });
    }
    function _renderVersionChip() {
      var versions = SHELL.document.versions || { list: [], current: null };
      var list = versions.list || [];
      if (!versionChipEl || list.length < 2) {
        if (versionChipEl) versionChipEl.hidden = true;
        if (versionDropdownEl) versionDropdownEl.hidden = true;
        return;
      }
      versionChipEl.hidden = false;
      var current = versions.current;
      var idx = -1;
      for (var i = 0; i < list.length; i++) { if (list[i].version === current) { idx = i; break; } }
      if (idx < 0) idx = 0;
      if (versionLabelEl) {
        versionLabelEl.textContent = "v" + current + " of " + list.length;
        // "v18 of 18" is what the eye needs; the accessible name has to name
        // the thing it navigates, so it does not read as a bare "18 of 18".
        versionLabelEl.setAttribute("aria-label",
          "document version " + current + " of " + list.length);
      }
      if (versionPrevEl) versionPrevEl.disabled = (idx >= list.length - 1);
      if (versionNextEl) versionNextEl.disabled = (idx <= 0);
      if (versionDropdownEl) {
        while (versionDropdownEl.firstChild) versionDropdownEl.removeChild(versionDropdownEl.firstChild);
        for (var j = 0; j < list.length; j++) {
          var rr = list[j];
          var row = document.createElement("button");
          row.type = "button";
          row.className = "version-row" + (rr.version === current ? " is-current" : "");
          var label = rr.change_summary || ("v" + rr.version);
          var t = _projectRelativeTime(rr.created_at || rr.timestamp);
          row.textContent = "v" + rr.version + " · " + label + " · " + t;
          row.setAttribute("data-version", String(rr.version));
          row.addEventListener("click", function () {
            _jumpToVersion(parseInt(this.getAttribute("data-version"), 10));
          });
          versionDropdownEl.appendChild(row);
        }
      }
    }
    function _versionStep(dir) {
      var versions = SHELL.document.versions || { list: [], current: null };
      var list = versions.list || [];
      var idx = -1;
      for (var i = 0; i < list.length; i++) { if (list[i].version === versions.current) { idx = i; break; } }
      if (idx < 0) return;
      var target = null;
      if (dir < 0 && idx < list.length - 1) target = list[idx + 1];   // ◀ older = higher index (lower version number)
      if (dir > 0 && idx > 0) target = list[idx - 1];                  // ▶ newer = lower index (higher version number)
      if (target) _jumpToVersion(target.version);
    }
    function _jumpToVersion(n) {
      var pid = _activeProjectId();
      if (!pid) return;
      SHELL.document.versions.current = n;
      fetch("/api/projects/" + encodeURIComponent(pid) + "/jdf?version=" + encodeURIComponent(n))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (res) {
          var doc = res && res.document;
          if (!doc) return;
          renderJdfDocument(doc);
          setShell("document.mode", "ready");
          setShell("document.current", doc);
          _renderVersionChip();
          if (versionDropdownEl) versionDropdownEl.hidden = true;
        })
        .catch(function () {});
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
        // The dot colour tracks the span score, which comes from the numeric
        // truth-ledger check (0.92 = the figure matches a locked value,
        // 0.5 = nothing numeric was checkable, 0.25/0.15 = contradicts the
        // locked value). It is not source verification.
        { dot: "green",  label: "Figure matches ledger lock" },
        { dot: "yellow", label: "Not numerically checked" },
        { dot: "red",    label: "Contradicts ledger lock" },
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

    // ---------------------------------------------------------------
    // Grounding banner. It describes live document state, so it is derived
    // here and re-derived whenever the document changes (verified frame,
    // ingest, node rewrite) instead of being a write-once snapshot that
    // drifts from the cite chips and the source summary.
    // Anchoring mirrors prompt_matrix/services/audit_summary.py
    // (_provenance_counts): paragraph nodes with >= _MIN_CLAIM_TOKENS content
    // tokens (models/jdf.py _tokenize — tokens are >2 chars and not stopwords,
    // counted as a set). The two layers stay separate: `anchored` is the
    // grounding — the node's provenance row carries the matched source sentence
    // (_anchoring_quote) — and `supported` is what the entailment check made of
    // it (verdict "yes"). When the caller supplies the server's
    // provenance_stats, those win (DB parity with the persisted gate).
    // ---------------------------------------------------------------
    var _ANCHOR_STOPWORDS = {
      a: 1, an: 1, the: 1, of: 1, and: 1, or: 1, to: 1, in: 1, on: 1, for: 1,
      is: 1, are: 1, was: 1, were: 1, be: 1, by: 1, with: 1, as: 1, at: 1,
      this: 1, that: 1, it: 1, its: 1, from: 1, but: 1, shall: 1, will: 1,
      may: 1, any: 1, all: 1,
    };
    // Same constant as the server: models/jdf._MIN_CLAIM_TOKENS ==
    // _MIN_ANCHOR_OVERLAP == 4. The old hardcoded 8 was the pre-fix paragraph
    // floor; a policy whose sentences run 6-7 content tokens (the demo
    // fixture) was excluded by it, so the fallback counted 0 anchored on a
    // document the server had just grounded.
    var _ANCHOR_WORD_FLOOR = 4;
    // models/jdf._tokenize returns a set, so the floor compares UNIQUE content
    // tokens: counting duplicates would let one repeated word clear a floor
    // the server never accepted.
    function _anchorContentTokens(content) {
      var words = String(content == null ? "" : content)
        .replace(/[^\w\s]/g, " ").toLowerCase().split(/\s+/);
      var seen = {};
      var n = 0;
      for (var i = 0; i < words.length; i++) {
        var w = words[i];
        if (w.length > 2 && !_ANCHOR_STOPWORDS[w] && !seen[w]) { seen[w] = 1; n++; }
      }
      return n;
    }
    // ---------------------------------------------------------------
    // Entailment verdict. A lexical anchor is not a verified claim: it only
    // says the wording overlaps a source sentence (models/jdf.py still anchors
    // "by wording similarity, not claim truthfulness"). The gate is the
    // verdict the server's entailment check persisted on the node at
    // node.meta.provenance.entailment:
    //   "yes"        -> the source states the claim
    //   "partial"    -> the source supports it only in part
    //   "no"         -> the source contradicts it / lacks the figure
    //   "unverified" -> the check did not produce a verdict (including a
    //                   failed call, which persists the failure reason)
    // A missing verdict is therefore never upgraded to "yes" here, and the
    // label never falls back to "the provenance dict exists".
    // ---------------------------------------------------------------
    var _ENTAILMENT_LABELS = {
      yes: "Verified against policy",
      partial: "Partial match",
      no: "Not verified",
      unverified: "Source check failed",
    };
    function _entailmentFor(node, provItem) {
      if (provItem && typeof provItem === "object" &&
          provItem.entailment && typeof provItem.entailment === "object") {
        return provItem.entailment;
      }
      var metaProv = node && node.meta && node.meta.provenance;
      if (metaProv && !Array.isArray(metaProv) && typeof metaProv === "object" &&
          metaProv.entailment && typeof metaProv.entailment === "object") {
        return metaProv.entailment;
      }
      return null;
    }
    // Unknown strings are treated as "unverified": a verdict the shell cannot
    // read must not render as verification.
    function _entailmentVerdict(node, provItem) {
      var ent = _entailmentFor(node, provItem);
      var v = ent ? String(ent.verdict || "").toLowerCase() : "";
      return Object.prototype.hasOwnProperty.call(_ENTAILMENT_LABELS, v) ? v : "unverified";
    }
    // One sentence, or "" when the check produced none. Never invented here.
    function _entailmentReasoning(node, provItem) {
      var ent = _entailmentFor(node, provItem);
      return (ent && ent.reasoning) ? String(ent.reasoning) : "";
    }
    // "Source check failed · page N" / "Verified in source · page N" — the page
    // suffix only when the anchor carried one.
    function _entailmentLabel(node, provItem, pageStr) {
      return _ENTAILMENT_LABELS[_entailmentVerdict(node, provItem)] +
        (pageStr ? " \u00b7 page " + pageStr : "");
    }
    // The two numbers the gate reports, derived from the same tree the shell
    // renders: `anchored` (a matched source sentence exists) and `supported`
    // (the entailment check said yes). Kept apart so a document that quotes its
    // sources is not called ungrounded, and a document whose every quote was
    // refused is not called verified.
    function _derivedCounts(doc) {
      var counts = { anchored: 0, supported: 0, partial: 0, unverified: 0 };
      var sections = (doc && Array.isArray(doc.body)) ? doc.body : [];
      for (var s = 0; s < sections.length; s++) {
        if (!sections[s] || typeof sections[s] !== "object") continue;
        var group = [sections[s]];
        if (Array.isArray(sections[s].children)) group = group.concat(sections[s].children);
        for (var g = 0; g < group.length; g++) {
          var node = group[g];
          if (!node || typeof node !== "object") continue;
          if (String(node.type || "") !== "paragraph") continue;
          if (_anchorContentTokens(node.content) < _ANCHOR_WORD_FLOOR) continue;
          // Python treats [] as falsy; JS does not. Payloads carry
          // provenance: [] for unanchored paragraphs, so mirror
          // audit_summary._anchoring_quote: a row carrying the matched source
          // sentence. meta.provenance.excerpt is NOT that — it falls back to
          // the claim text itself.
          var prov = node.provenance;
          if (!Array.isArray(prov)) prov = prov ? [prov] : [];
          var isAnchored = false;
          for (var p = 0; p < prov.length; p++) {
            var row = prov[p];
            if (row && typeof row === "object" &&
                String(row.extracted_quote || "").trim()) { isAnchored = true; break; }
          }
          if (!isAnchored) continue;
          counts.anchored++;
          // The verdict buckets the anchored claims exactly as the server's
          // _provenance_counts does: yes -> supported, partial -> partial,
          // everything else (no, unverified) -> unverified.
          var verdict = _entailmentVerdict(node, prov[0] || null);
          if (verdict === "yes") counts.supported++;
          else if (verdict === "partial") counts.partial++;
          else counts.unverified++;
        }
      }
      return counts;
    }
    // "N of M claims cite a source, but the entailment check verified none of
    // them" — the honest state after the count stopped folding the verdict into
    // the anchor. Rendered beside the ungrounded banner, never instead of it:
    // the banner stays exactly `anchored == 0`.
    function _syncEntailmentNote(stats, derived) {
      var existing = document.querySelectorAll(".doc-entailment-note");
      for (var i = 0; i < existing.length; i++) {
        if (existing[i].parentNode) existing[i].parentNode.removeChild(existing[i]);
      }
      var anchored, supported, eligible;
      if (stats && typeof stats === "object" && typeof stats.supported === "number" &&
          typeof stats.anchored === "number") {
        anchored = stats.anchored;
        supported = stats.supported;
        eligible = (typeof stats.eligible === "number") ? stats.eligible : anchored;
      } else {
        anchored = derived.anchored;
        supported = derived.supported;
        eligible = anchored;
      }
      if (anchored === 0 || supported > 0) return;
      var note = document.createElement("div");
      note.className = "doc-entailment-note";
      note.textContent = "Grounded, not verified — " + anchored +
        " of " + eligible + " claims cite an uploaded source, and the source " +
        "check verified none of them. Review before use.";
      _insertDocNotice(note);
    }
    // The note sits at the top of the document column, in flow: found first, it
    // pushes the document down instead of covering its first lines. It is the
    // only body-level notice the document carries.
    function _insertDocNotice(el) {
      var column = document.querySelector(".doc-draft") ||
                   document.querySelector(".doc-surface");
      if (!column) return;
      column.insertBefore(el, column.firstChild);
    }

    // The grounding numbers and the not-verified note — no banner. A compile
    // that anchors nothing is refused server-side (HTTP 422, nothing persisted),
    // so `anchored == 0` no longer describes a document the shell can hold; the
    // verdict is reported by the refusal itself and the counters stay honest.
    function _syncGroundingNotices(stats) {
      var derived = null;
      if (!(stats && typeof stats === "object" && typeof stats.anchored === "number")) {
        var doc = SHELL.document.current;
        if (!doc || !Array.isArray(doc.body)) {
          _renderCounters(null, null);
          return;
        }
        derived = _derivedCounts(doc);
      }
      _syncEntailmentNote(stats, derived || { anchored: 0, supported: 0 });
      _renderCounters(stats, derived);
    }

    // The right pane's 2x2 counters. The server's persisted stats win when
    // they are present (DB parity with the gate); otherwise the numbers come
    // from the same tree the shell renders. Zero reads muted, a live number
    // reads ink, and a changed numeral fades in over 200ms — no slide.
    function _setCounter(id, value) {
      var el = document.getElementById(id);
      if (!el) return;
      var next = String(value);
      if (el.textContent === next) return;
      el.textContent = next;
      if (value) el.classList.remove("is-zero");
      else el.classList.add("is-zero");
      el.classList.remove("is-updating");
      void el.offsetWidth;                     // restart the fade
      el.classList.add("is-updating");
      setTimeout(function () { el.classList.remove("is-updating"); }, 220);
    }
    function _num(stats, derived, key) {
      if (stats && typeof stats[key] === "number") return stats[key];
      if (derived && typeof derived[key] === "number") return derived[key];
      return 0;
    }
    function _renderCounters(stats, derived) {
      _setCounter("count-anchored",   _num(stats, derived, "anchored"));
      _setCounter("count-supported",  _num(stats, derived, "supported"));
      _setCounter("count-partial",    _num(stats, derived, "partial"));
      _setCounter("count-unverified", _num(stats, derived, "unverified"));
      var line = document.getElementById("counter-line");
      if (!line) return;
      var n = (SHELL.sources && SHELL.sources.length) || 0;
      // The line describes a compiled document, so it needs a document with
      // content: a blank tree is still "no document yet".
      var doc = SHELL.document.current;
      var hasDoc = Boolean(doc && Array.isArray(doc.body) && doc.body.length);
      if (hasDoc && n > 0) {
        line.textContent = "Compiled from " + n + " source" + (n === 1 ? "" : "s");
        line.hidden = false;
      } else {
        line.textContent = "";
        line.hidden = true;
      }
    }

    // ---------------------------------------------------------------
    // Document hydration. The server owns the persisted JDF for a project:
    // GET /api/projects/<pid>/jdf -> {ok, document:{document_id, meta,
    // truth_ledger, body}}. A cold shell holds no document at all, and the
    // routes that take a `document` (rewrite/inquire) require a well-formed
    // JDFDocumentTree — posting {body: []} fails with
    // "1 validation error for JDFDocumentTree document_id Field required".
    // So read the real document back instead of seeding an empty one.
    // ---------------------------------------------------------------
    function _documentIdFor(projectId) { return "doc-" + projectId; }
    function _blankDocument(projectId) {
      // Same id/meta the server mints for an unsaved project
      // (db/jdf_repository.empty_document), so a first write is the same doc.
      return {
        document_id: _documentIdFor(projectId),
        meta: { project_id: projectId },
        truth_ledger: {},
        body: [],
      };
    }
    var __hydrating = {};
    // Resolves with the document the shell should work against. Adopts the
    // fetched document as SHELL.document.current unless the shell already
    // holds nodes of its own (in-flight edits win over the last save).
    function _hydrateProjectDocument(projectId) {
      var local = SHELL.document.current;
      function fallback() {
        if (local && Array.isArray(local.body)) {
          if (!local.document_id) local.document_id = _documentIdFor(projectId);
          return local;
        }
        return _blankDocument(projectId);
      }
      if (!projectId) return Promise.resolve(fallback());
      if (__hydrating[projectId]) return __hydrating[projectId];
      var p = fetch("/api/projects/" + encodeURIComponent(projectId) + "/jdf")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (res) {
          var doc = (res && res.document) || null;
          if (!doc || !Array.isArray(doc.body)) return fallback();
          if (!doc.document_id) doc.document_id = _documentIdFor(projectId);
          if (!doc.meta) doc.meta = { project_id: projectId };
          var localHasNodes = !!(local && Array.isArray(local.body) && local.body.length);
          if (localHasNodes) {
            if (!local.document_id) local.document_id = _documentIdFor(projectId);
            return local;
          }
          setShell("document.current", doc);
          return doc;
        })
        .catch(function () { return fallback(); })
        .then(function (doc) {
          delete __hydrating[projectId];
          return doc;
        });
      __hydrating[projectId] = p;
      return p;
    }
    // Reload path: re-render the persisted document, its version stepper and
    // the right pane, so a refresh does not drop back to the first-run state.
    function _restoreProjectDocument(projectId) {
      if (!projectId) return Promise.resolve(false);
      return _hydrateProjectDocument(projectId).then(function (doc) {
        if (!doc || !Array.isArray(doc.body) || !doc.body.length) return false;
        // A compile started before the boot GET resolved owns the surface now.
        if (SHELL.streams.draft) return false;
        setShell("document.mode", "ready");
        setShell("document.current", doc);
        renderJdfDocument(doc);
        // The persisted tree is the audited document: it carries
        // meta.confidenceSpans (document level) plus per-node
        // meta.confidenceSpans / meta.provenance, so the boot renderer paints
        // the same overlay the verified SSE frame and acceptCompareColumn do
        // (same two calls, same order). Reload is no longer second class: the
        // highlights and the evidence chips survive it without a fresh
        // compile. Nothing is fabricated — a node without real spans renders
        // unhighlighted.
        applyConfidenceSpans(doc);
        addEvidenceChips(doc);
        _loadVersionHistory(projectId, { current: null });
        _refreshSignoff(projectId);
        // Prefer the server's persisted provenance_stats (the same numbers the
        // export and the gate read). Only when the document carries none does
        // _syncGroundingNotices derive the count locally, on the verdict rule.
        _syncGroundingNotices((doc.meta && doc.meta.provenance_stats) || null);
        _applyRightView();
        return true;
      });
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
    }

    // ---------------------------------------------------------------
    // Inline node rephrase: rewrite only the selected paragraph.
    // ---------------------------------------------------------------
    var __rephraseBusy = false;
    function _replaceNodeInTree(nodes, nodeId, newNode) {
      if (!Array.isArray(nodes)) return false;
      for (var i = 0; i < nodes.length; i++) {
        if (nodes[i] && nodes[i].id === nodeId) { nodes[i] = newNode; return true; }
        if (nodes[i] && nodes[i].children && _replaceNodeInTree(nodes[i].children, nodeId, newNode)) return true;
      }
      return false;
    }
    function _rephraseRenderNode(nodeId, newNode) {
      if (!draftEl || !newNode || !newNode.type) return;
      var old = draftEl.querySelector('.jdf-node[data-node-id="' + nodeId + '"]');
      if (!old) return;
      var fresh = renderJdfNode(newNode);
      if (!fresh) return;
      if (old.classList.contains("is-selected")) fresh.classList.add("is-selected");
      if (old.classList.contains("is-rephrasing")) fresh.classList.add("is-rephrasing");
      try { addEvidenceChips({ body: [newNode] }, fresh); } catch (_) {}
      old.replaceWith(fresh);
    }
    function _removeNodeRephrase() {
      if (!draftEl) return;
      var existing = draftEl.querySelector(".node-rephrase");
      if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
    }

    // ---------------------------------------------------------------
    // Search → editor bridge. A JDF search hit is a jdf-cli chunk
    // ({doc_id, doc_hash, chunk_idx, text, meta}) with no app node id, so the
    // id is derived deterministically — the same hit always maps to the same
    // node — and namespaced `src-` to stay clear of server node ids. `doc_id`
    // is a raw filename, so every id fragment is slugged: all node lookups in
    // this file interpolate ids into querySelector unescaped.
    // ---------------------------------------------------------------
    function _slugNodeIdPart(raw) {
      var s = String(raw == null ? "" : raw).toLowerCase().replace(/[^a-z0-9]+/g, "-");
      s = s.replace(/^-+/, "").replace(/-+$/, "");
      return s.slice(0, 48) || "x";
    }
    function _jdfHitNodeId(hit) {
      var idx = (hit && hit.chunk_idx != null) ? hit.chunk_idx
        : ((hit && hit.meta && hit.meta.id != null) ? hit.meta.id : "0");
      var id = "src-" + _slugNodeIdPart(hit && hit.doc_id) + "-" + _slugNodeIdPart(idx);
      var hash = _slugNodeIdPart((hit && hit.doc_hash) || "");
      return hash === "x" ? id : id + "-" + hash.slice(0, 6);
    }
    // Content nodes belong in a section's `children`: _replaceNodeInTree and
    // findJdfNodeById recurse body -> children, and aperture.py /
    // jdf_repository._find_block_json_path assume `body` holds blocks only.
    function _targetSectionForInsert(doc) {
      var sections = (doc && doc.body) || [];
      var last = null;
      for (var i = 0; i < sections.length; i++) if (sections[i]) last = sections[i];
      var selId = SHELL.ui.selection.nodeId;
      if (!selId) return last;
      for (var k = 0; k < sections.length; k++) {
        var sec = sections[k];
        if (!sec) continue;
        if (sec.id === selId) return sec;
        var kids = sec.children;
        if (kids && Array.isArray(kids)) {
          for (var m = 0; m < kids.length; m++) if (kids[m] && kids[m].id === selId) return sec;
        }
      }
      return last;
    }
    function _openJdfSearchResultInEditor(hit, opts) {
      var text = String((hit && hit.text) || "").trim();
      if (!text) { jdfMessage("Cannot open this result", true); return null; }
      if (!draftEl) ensureDraftArea();
      if (!draftEl) { jdfMessage("Cannot open this result", true); return null; }
      // The rewrite POSTs SHELL.document.current back to the server, where it
      // is parsed as a JDFDocumentTree (document_id required). Adopt the
      // project's persisted document — real id, real body — before splicing
      // the hit in, so a cold shell never posts a bare {body: []}.
      return _hydrateProjectDocument(_activeProjectId()).then(function (doc) {
        return _insertSearchResultNode(hit, text, doc, opts);
      });
    }

    function _insertSearchResultNode(hit, text, doc, opts) {
      var nodeId = _jdfHitNodeId(hit);

      var created = false;
      if (!findJdfNodeById(nodeId, doc)) {
        var node = {
          id: nodeId,
          type: "paragraph",
          content: text,
          meta: {
            provenance: [{
              kind: "jdf_search",
              doc_id: (hit && hit.doc_id) || "",
              doc_hash: (hit && hit.doc_hash) || "",
              chunk_idx: (hit && hit.chunk_idx != null) ? hit.chunk_idx : null,
              chunk_id: (hit && hit.meta && hit.meta.id != null) ? hit.meta.id : "",
              query: (opts && opts.query) || "",
            }],
          },
        };
        var section = _targetSectionForInsert(doc);
        if (!section) {
          section = { id: "sec-src-inbox", type: "section", title: "Imported Sources", children: [] };
          if (!Array.isArray(doc.body)) doc.body = [];
          doc.body.push(section);
        }
        if (!Array.isArray(section.children)) section.children = [];
        section.children.push(node);
        var host = section.id
          ? draftEl.querySelector('.jdf-node[data-node-id="' + section.id + '"]')
          : null;
        var fresh = host ? renderJdfNode(node) : null;
        if (fresh) host.appendChild(fresh);
        else renderJdfDocument(doc);   // section not on screen yet (cold shell)
        created = true;
      }

      var selector = '.jdf-node[data-node-id="' + nodeId + '"]';
      var wrapper = draftEl.querySelector(selector);
      if (!wrapper) {
        // The tree holds the node but the DOM does not (e.g. the draft was
        // cleared while SHELL.document.current survived). Re-derive the DOM
        // from the tree rather than failing.
        renderJdfDocument(doc);
        wrapper = draftEl ? draftEl.querySelector(selector) : null;
      }
      if (!wrapper) { jdfMessage("Cannot open this result", true); return null; }
      setShell("ui.selection.nodeId", nodeId);
      _attachNodeRephrase(nodeId);
      try { wrapper.scrollIntoView({ block: "center" }); } catch (_) {}
      return { nodeId: nodeId, created: created };
    }

    function _attachNodeRephrase(nodeId, initialValue) {
      _removeNodeRephrase();
      __rephraseBusy = false;
      if (!nodeId || !draftEl) return;
      var wrapper = draftEl.querySelector('.jdf-node[data-node-id="' + nodeId + '"]');
      if (!wrapper) return;
      var editor = document.createElement("div");
      editor.className = "node-rephrase";
      // A textarea, not an input: a Red-Hat finding is multi-line prose and the
      // rephrase instruction has to reach the server with its newlines intact
      // (an <input type="text"> flattens them).
      var input = document.createElement("textarea");
      input.rows = 3;
      input.placeholder = "Rephrase this paragraph…";
      input.setAttribute("aria-label", "Rephrase this paragraph");
      // A Red-Hat finding is already phrased as an instruction, so opening
      // the editor from one seeds the field instead of asking for it again.
      if (initialValue) input.value = String(initialValue);
      var submit = document.createElement("button");
      submit.type = "button"; submit.setAttribute("data-action", "submit"); submit.textContent = "Rewrite";
      var cancel = document.createElement("button");
      cancel.type = "button"; cancel.setAttribute("data-action", "cancel"); cancel.textContent = "Cancel";
      editor.appendChild(input); editor.appendChild(submit); editor.appendChild(cancel);
      wrapper.insertBefore(editor, wrapper.firstChild);

      function doSubmit() {
        var v = input.value || "";
        if (!v.trim() || __rephraseBusy) return;
        _submitRephrase(nodeId, v.trim(), editor, input);
      }
      input.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { e.preventDefault(); _removeNodeRephrase(); }
        // Enter is a newline here — the newlines are the point. Cmd/Ctrl+Enter
        // submits, and Rewrite works as before.
        else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          doSubmit();
        }
      });
      submit.addEventListener("click", doSubmit);
      cancel.addEventListener("click", function () { _removeNodeRephrase(); });
      input.focus();
    }
    function _handleRephraseFrame(frame, cb) {
      if (!frame) return;
      var ev = ""; var dataStr = "";
      frame.split(/\r?\n/).forEach(function (line) {
        if (line.indexOf("event:") === 0) ev = line.slice(6).trim();
        else if (line.indexOf("data:") === 0) dataStr += line.slice(5).trim();
      });
      if (!ev || !dataStr) return;
      var data = null;
      try { data = JSON.parse(dataStr); } catch (_) { return; }
      if (!data || typeof data !== "object") return;
      if (ev === "jdf_node_ready" && data.node) {
        cb("node", data.node);
      } else if (ev === "complete") {
        if (data.ok) cb("ok", null);
        else cb("error", (data.error) || "Rewrite failed");
      } else if (ev === "token") {
        cb("token", data.delta != null ? String(data.delta) : "");
      }
    }

    function _submitRephrase(nodeId, promptText, editor, inputEl) {
      var pid = _activeProjectId();
      if (!pid) return;
      __rephraseBusy = true;
      if (inputEl) inputEl.disabled = true;
      var wrapper = draftEl && draftEl.querySelector('.jdf-node[data-node-id="' + nodeId + '"]');
      if (wrapper) wrapper.classList.add("is-rephrasing");
      var doc = SHELL.document.current || null;
      // The server parses this as a JDFDocumentTree: document_id is required,
      // and an id-less tree is rejected with a validation error.
      if (doc && !doc.document_id) doc.document_id = _documentIdFor(pid);
      var body = {
        user_intent: promptText,
        target_node_id: nodeId,
        project_id: pid,
        substrate_file_ids: (SHELL.sources || []).slice(),
        run_redhat: false,
        document: doc,
      };
      var pendingNode = null;
      var blamed = null;
      var sseStartedAt = Date.now();
      var sseTokens = 0;
      var sseEndpoint = "/api/projects/" + encodeURIComponent(pid) + "/inquire/stream";

      function finish(success, node) {
        __rephraseBusy = false;
        var w = draftEl && draftEl.querySelector('.jdf-node[data-node-id="' + nodeId + '"]');
        if (w) w.classList.remove("is-rephrasing");
        if (success && node) {
          if (SHELL.document.current) _replaceNodeInTree(SHELL.document.current.body || [], nodeId, node);
          _rephraseRenderNode(nodeId, node);
          var fresh = draftEl && draftEl.querySelector('.jdf-node[data-node-id="' + nodeId + '"]');
          if (fresh) {
            fresh.classList.add("rh-fade");
            setTimeout(function () { fresh.classList.remove("rh-fade"); }, 900);
          }
          _removeNodeRephrase();
          // The rewritten node carries its own provenance (or none): the
          // counters must follow it, not the last verified snapshot.
          _syncGroundingNotices(null);
          var pid2 = _activeProjectId();
          if (pid2) { _loadNodeHistory(nodeId); _loadVersionHistory(pid2, { current: "latest" }); }
        } else {
          var orig = (SHELL.document.current) ? findJdfNodeById(nodeId, SHELL.document.current) : null;
          if (orig) _rephraseRenderNode(nodeId, orig);
          // _rephraseRenderNode swaps the wrapper, which detaches the editor
          // (taking any pending prompt and error with it). Move it back onto
          // the fresh wrapper so the user can read the failure and retry.
          var host = draftEl && draftEl.querySelector('.jdf-node[data-node-id="' + nodeId + '"]');
          if (host && editor && !host.contains(editor)) host.insertBefore(editor, host.firstChild);
          if (inputEl) inputEl.disabled = false;
          if (editor) {
            var prior = editor.querySelector(".node-rephrase-error");
            if (prior) editor.removeChild(prior);
            var errEl = document.createElement("div");
            errEl.className = "node-rephrase-error";
            errEl.textContent = blamed || "Rewrite failed.";
            editor.appendChild(errEl);
            if (inputEl) inputEl.focus();
          }
        }
      }

      jsonPost(sseEndpoint, body).then(function (resp) {
        if (!resp.ok || !resp.body) {
          blamed = "Rewrite failed (" + resp.status + ")";
          logSseFailure(sseEndpoint, sseStartedAt, sseTokens, new Error("HTTP " + resp.status + " — no body"), false);
          finish(false, null);
          return;
        }
        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var buffer = "";
        function pump() {
          return reader.read().then(function (result) {
            if (result.done) { finish(!!pendingNode, pendingNode); return; }
            var str = decoder.decode(result.value, { stream: true });
            sseTokens += str.length;
            buffer += str;
            var frames = buffer.split(/\n\n/);
            buffer = frames.pop();
            for (var i = 0; i < frames.length; i++) {
              (function (f) {
                _handleRephraseFrame(f, function (kind, val) {
                  if (kind === "node") pendingNode = val;
                  else if (kind === "error") { blamed = val; logSseFailure(sseEndpoint, sseStartedAt, sseTokens, new Error(String(val)), false); }
                });
              })(frames[i]);
            }
            return pump();
          }).catch(function (e) {
            blamed = (e && e.message) || "Stream error";
            logSseFailure(sseEndpoint, sseStartedAt, sseTokens, e, !!(e && e.name === "AbortError"));
            finish(false, null);
          });
        }
        return pump();
      }).catch(function (e) {
        blamed = (e && e.message) || "Network error";
        logSseFailure(sseEndpoint, sseStartedAt, sseTokens, e, !!(e && e.name === "AbortError"));
        finish(false, null);
      });
    }

    // ---------------------------------------------------------------
    // Red-Hat audit trigger — explicit, per-node, re-runnable. Its own
    // parser pair rather than parseSseLoop: parseSseLoop's two consumers
    // feed different endpoints and it carries the compile stage machine,
    // which this stream does not speak.
    // ---------------------------------------------------------------
    // One audit at a time, and the run belongs to the node it audits: holding
    // the node id (not a bare flag) is what keeps a second node's pane from
    // claiming the run, its counter, or the error of a node it never touched.
    // __redhatTimer stays a single interval because there is only ever one run.
    var __redhatRunningNodeId = null;
    var __redhatStartedAt = 0;
    var __redhatTimer = null;
    var __redhatError = null;   // { nodeId, message } — read only by its own node
    var __redhatLastRun = null; // { nodeId, status, count, seconds } — written by finish()

    // The audit takes the node's text: RedhatPayload.draft_text is
    // min_length 1 and apply_redhat_critiques_to_tree attaches findings to any
    // node id. Of the JDF node types (prompt_matrix/models/jdf.py) only
    // paragraph, callout and signature carry `content`; section, table, image
    // and checkbox do not, so they are not auditable and say so.
    var REDHAT_AUDITABLE_TYPES = { paragraph: true, callout: true, signature: true };
    var REDHAT_NOT_AUDITABLE =
      "Red-Hat audits paragraph, callout and signature nodes with text.";
    function _redhatAuditable(node) {
      if (!node || !node.id) return false;
      if (!REDHAT_AUDITABLE_TYPES[node.type]) return false;
      return String(node.content || "").trim().length > 0;
    }
    // Server reasons → one sentence a reader can act on. The raw text the
    // server sends (a pydantic dump carries field paths and URLs) goes to
    // console.error, never into the pane.
    var REDHAT_REASON_TEXT = {
      conflict: "Another edit landed while the audit ran — the finding was not saved.",
      RevisionConflict: "Another edit landed while the audit ran — the finding was not saved.",
      ValidationError: "The document was rejected before the audit ran.",
      OperationalError: "The audit could not run on this node.",
      TimeoutError: "The audit timed out before it finished.",
      ConnectionError: "The audit could not reach the model provider.",
    };
    function _redhatReasonText(reason) {
      return REDHAT_REASON_TEXT[String(reason || "").trim()] ||
        "The audit could not run on this node.";
    }
    // A persist failure is not an audit failure: the run happened, the write
    // did not. Only a version race has its own sentence; every other exception
    // class the route can raise reads the same.
    function _redhatSaveFailedText(reason) {
      return String(reason || "") === "conflict"
        ? "Another edit landed while the audit ran — the finding was not saved."
        : "The finding could not be saved.";
    }
    // Error frames and HTTP bodies carry prose ("Invalid document: <pydantic>")
    // or {"error": "<prose>"}. Pull the token out; a token that is not a known
    // reason falls through to the generic sentence.
    function _redhatErrorToken(raw) {
      var t = String(raw == null ? "" : raw);
      try {
        var parsed = JSON.parse(t);
        if (parsed && typeof parsed === "object") {
          t = String(parsed.error || parsed.detail || t);
        }
      } catch (_) {}
      return t;
    }

    function _redhatElapsedSeconds() {
      if (!__redhatRunningNodeId) return 0;
      return Math.max(0, Math.round((Date.now() - __redhatStartedAt) / 1000));
    }
    function _redhatStopTimer() {
      if (__redhatTimer) { clearInterval(__redhatTimer); __redhatTimer = null; }
    }
    // The route emits one status frame and then ~35 s of model silence. A
    // disabled button with no motion reads as broken, so the counter is the
    // run's only proof of life until the finding lands.
    function _redhatTick() {
      if (!__redhatRunningNodeId) { _redhatStopTimer(); return; }
      var selId = SHELL.ui.selection ? SHELL.ui.selection.nodeId : null;
      if (selId !== __redhatRunningNodeId) return; // another node's pane has nothing to tick
      var s = _redhatElapsedSeconds();
      var btn = redhatModeEl ? redhatModeEl.querySelector(".redhat-run") : null;
      if (btn) {
        btn.textContent = "Running Red-Hat… (" + s + "s)";
        btn.classList.add("is-running");
        btn.setAttribute("aria-busy", "true");
      }
      var statusEl = redhatModeEl ? redhatModeEl.querySelector(".redhat-status") : null;
      if (statusEl) statusEl.textContent = "Running… " + s + " s";
    }
    function _redhatFindingText(r) {
      if (!r) return "";
      if (typeof r === "string") return r;
      // The audit route writes {title, content, model}; the annotation writer
      // writes {id, text, status}. Read text, then content.
      return String(r.text || r.content || "").trim();
    }

    function _handleRedhatFrame(frame, cb) {
      if (!frame) return;
      var ev = ""; var dataStr = "";
      frame.split(/\r?\n/).forEach(function (line) {
        if (line.indexOf("event:") === 0) ev = line.slice(6).trim();
        else if (line.indexOf("data:") === 0) dataStr += line.slice(5).trim();
      });
      // _done_sse() is a bare "data: [DONE]" with no event: line, so it lands
      // here and falls out. The run ends on reader completion, not on it.
      if (!ev || !dataStr) return;
      var data = null;
      try { data = JSON.parse(dataStr); } catch (_) { return; }
      if (!data || typeof data !== "object") return;
      if (ev === "status") {
        if (data.stage === "persist_failed") {
          // Node-scoped only: the audit ran, the write did not land. detail is
          // a dict — {"reason": "conflict", "latest_version": n} for a version
          // race, {"reason": "<ExceptionClass>", "message": str(exc)[:240]}
          // for anything else. The message is the server's own explanation: it
          // reaches console.error, while the pane gets a sentence (F4/F8).
          var detail = data.detail;
          var msg = (detail && (detail.message || detail.reason)) || "unknown";
          cb("persist_failed", {
            reason: (detail && typeof detail === "object" && detail.reason != null)
              ? String(detail.reason) : "",
            message: String(msg),
          });
        } else if (typeof data.message === "string" &&
                   data.message.indexOf("Running Stress Test") === 0) {
          cb("started", data.message);
        }
      } else if (ev === "audit_complete") {
        cb("audit_complete", data);
      } else if (ev === "complete") {
        cb("complete", data);
      } else if (ev === "error") {
        cb("error", data);
      }
    }

    function _runRedhatAudit(nodeId) {
      // One run at a time: while a run is in flight every node's button is
      // disabled, so this guard is unreachable from the UI.
      if (__redhatRunningNodeId) return;
      var pid = _activeProjectId();
      var doc = SHELL.document.current || null;
      var startNode = doc ? findJdfNodeById(nodeId, doc) : null;
      if (!pid || !doc || !startNode) {
        // No silent bail even here: if a node is on screen, it says why.
        if (startNode) {
          __redhatError = { nodeId: nodeId, message: _redhatReasonText("") };
          renderRedhatPanel(startNode);
        }
        return;
      }
      // Never a silent bail: a node the audit cannot read says why, in its pane.
      if (!_redhatAuditable(startNode)) {
        __redhatError = { nodeId: nodeId, message: REDHAT_NOT_AUDITABLE };
        renderRedhatPanel(startNode);
        return;
      }
      var draftText = String(startNode.content || "").trim();
      // RedhatPayload requires draft_text (min_length 1) and a document; the
      // document is parsed as a JDFDocumentTree, so document_id is required.
      if (!doc.document_id) doc.document_id = _documentIdFor(pid);
      var body = {
        draft_text: draftText,
        document: doc,
        target_node_id: nodeId,
      };
      var sseEndpoint = "/api/projects/" + encodeURIComponent(pid) + "/draft/redhat/stream";
      var sseStartedAt = Date.now();
      var sseTokens = 0;
      var controller = new AbortController();
      var deadlineTimer = null;
      var deadlineHit = false;
      var completed = false; // audit_complete arrived → the finding is persisted
      // The server APPENDS a redhat annotation (models/jdf.py:545), so the
      // node's array is cumulative across runs of this session. The pane's
      // "Last run" line is about THIS run: what it added, not the running total.
      var findingsBefore = (startNode.annotations && Array.isArray(startNode.annotations.redhat))
        ? startNode.annotations.redhat.length : 0;
      __redhatRunningNodeId = nodeId;
      // A new run clears this node's error only: another node's failure is
      // still that node's to show.
      if (__redhatError && __redhatError.nodeId === nodeId) __redhatError = null;
      __redhatStartedAt = Date.now();
      _redhatStopTimer();
      __redhatTimer = setInterval(_redhatTick, 1000);
      // The pane owns the findings, so a run always reveals it.
      setShell("ui.rightTab", "redhat");
      renderRedhatPanel(startNode);
      _focusRedhatRun();   // §3: a run reveals the pane, so hand focus to its own action

      function finish(ok, why) {
        // The latch names the run that owns the UI. A late callback from a run
        // that already ended must not close out — or overwrite the pane of —
        // whichever run is in flight now. Truthiness would let it: the newer
        // run's id would be sitting there, non-null, so this stale callback
        // would clear it. Only the run whose id matches may finish.
        if (__redhatRunningNodeId !== nodeId) return;
        __redhatRunningNodeId = null;
        _redhatStopTimer();
        if (deadlineTimer) { clearTimeout(deadlineTimer); deadlineTimer = null; }
        if (!ok && why) console.error("[redhat] run ended without a finding:", why);
        var doneNode = SHELL.document.current ? findJdfNodeById(nodeId, SHELL.document.current) : null;
        var found = (doneNode && doneNode.annotations && Array.isArray(doneNode.annotations.redhat))
          ? doneNode.annotations.redhat : [];
        // What THIS run added. A shorter array than we started with has no
        // truthful non-negative reading, so it clamps to "nothing new".
        var added = Math.max(0, found.length - findingsBefore);
        __redhatLastRun = {
          nodeId: nodeId,
          status: ok ? "done" : "failed",
          count: added,
          seconds: Math.max(0, Math.round((Date.now() - __redhatStartedAt) / 1000)),
        };
        var selId = SHELL.ui.selection ? SHELL.ui.selection.nodeId : null;
        var selNode = (selId && SHELL.document.current)
          ? findJdfNodeById(selId, SHELL.document.current) : null;
        if (selNode) renderRedhatPanel(selNode);
        else { _setInspectorPane(SHELL.ui.rightTab || "evidence"); _renderInspectorIdlePane(); }
      }

      // Hard deadline (60 s; the measured worst case is 45 s). A stalled stream
      // must return the UI within a minute instead of never. The parser never
      // flushes its buffer, so a partial frame is discarded — but if
      // audit_complete already arrived, the finding is kept and the state is
      // "done".
      deadlineTimer = setTimeout(function () {
        deadlineHit = true;
        try { controller.abort(); } catch (_) {}
        finish(completed, completed ? null : "timeout");
      }, 60000);

      jsonPost(sseEndpoint, body, null, controller.signal).then(function (resp) {
        if (!resp.ok || !resp.body) {
          var httpStatus = resp.status;
          return resp.text().then(function (t) {
            if (deadlineHit) return; // the deadline already closed this run out
            // The raw body (a pydantic dump carries field paths and URLs) is
            // for the console; the pane reads a sentence.
            console.error("[redhat] HTTP " + httpStatus, t);
            __redhatError = { nodeId: nodeId, message: _redhatReasonText(_redhatErrorToken(t)) };
            logSseFailure(sseEndpoint, sseStartedAt, sseTokens, new Error("HTTP " + httpStatus + " — no body"), false);
            finish(false, "http " + httpStatus);
          });
        }
        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var buffer = "";
        function pump() {
          return reader.read().then(function (result) {
            if (result.done) {
              // The deadline may already have closed this run out (it aborts
              // the stream, and an aborted reader can still resolve done).
              if (deadlineHit) return;
              finish(completed, completed ? null : "ended early");
              return;
            }
            var str = decoder.decode(result.value, { stream: true });
            sseTokens += str.length;
            buffer += str;
            var frames = buffer.split(/\n\n/);
            buffer = frames.pop();
            for (var i = 0; i < frames.length; i++) {
              (function (f) {
                _handleRedhatFrame(f, function (kind, val) {
                  if (kind === "started") {
                    _redhatTick();
                  } else if (kind === "persist_failed") {
                    // The audit ran; the write did not land. A version race and
                    // any other exception class read differently in the pane;
                    // the server's own text does not (it goes to the console).
                    var reason = (val && val.reason) || "";
                    console.error("[redhat] persist_failed reason=" + reason, (val && val.message) || "");
                    __redhatError = { nodeId: nodeId, message: _redhatSaveFailedText(reason) };
                  } else if (kind === "audit_complete") {
                    // Update only the audited node: the payload carries the
                    // whole tree, and swapping it in wholesale would clobber
                    // unrelated in-session edits.
                    var target = (val && val.document) ? findJdfNodeById(nodeId, val.document) : null;
                    if (target && SHELL.document.current) {
                      _replaceNodeInTree(SHELL.document.current.body || [], nodeId, target);
                      _rephraseRenderNode(nodeId, target);
                    }
                    completed = true;
                  } else if (kind === "error") {
                    var msg = String((val && val.error) || "unknown error");
                    console.error("[redhat] error frame", msg);
                    __redhatError = { nodeId: nodeId, message: _redhatReasonText(_redhatErrorToken(msg)) };
                    logSseFailure(sseEndpoint, sseStartedAt, sseTokens, new Error(msg), false);
                  } else if (kind === "complete") {
                    if (val && val.ok === false && !(__redhatError && __redhatError.nodeId === nodeId)) {
                      console.error("[redhat] complete not ok", String(val.error || ""));
                      __redhatError = { nodeId: nodeId, message: _redhatReasonText(_redhatErrorToken(val.error || "")) };
                    }
                  }
                });
              })(frames[i]);
            }
            return pump();
          }).catch(function (e) {
            // Our own deadline already closed this run out; the state it wrote
            // (done, or failed on timeout) is the truthful one.
            if (deadlineHit) return;
            var why = (e && e.message) || "stream error";
            console.error("[redhat] stream interrupted", why);
            __redhatError = { nodeId: nodeId, message: _redhatReasonText(_redhatErrorToken(why)) };
            logSseFailure(sseEndpoint, sseStartedAt, sseTokens, e, !!(e && e.name === "AbortError"));
            finish(false, "stream: " + why);
          });
        }
        return pump();
      }).catch(function (e) {
        if (deadlineHit) return;
        var why = (e && e.message) || "network error";
        console.error("[redhat] request failed", why);
        __redhatError = { nodeId: nodeId, message: _redhatReasonText(_redhatErrorToken(why)) };
        logSseFailure(sseEndpoint, sseStartedAt, sseTokens, e, !!(e && e.name === "AbortError"));
        finish(false, "request: " + why);
      });
    }

    // Monochrome text glyphs only: the shell spends no colour on emoji, so a
    // chip reads in the same palette as the rule it sits in.
    function getChipIcon(kind, status) {
      if (kind === "z3") return status === "pass" ? "\u2713" : "!";
      else if (kind === "cite") return "\u00a7";
      // A Red-Hat finding has no closing state (see Dismiss removal): the
      // check-mark branch had no writer, so the chip is always the flag.
      else if (kind === "redhat") return "\u2691";
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
            // §4 A4: the chip's only content is its emoji glyph. The accessible
            // name is the tooltip string, verbatim — the wording is server-authored
            // finding prose and is flagged for the voice pass, not rewritten here.
            chip.setAttribute("aria-label", chip.title || "");
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
          chip.setAttribute("aria-label", chip.title || "");   // §4 A4
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
            chip.setAttribute("aria-label", chip.title || "");   // §4 A4
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

    // Persisted trees denormalize the spans onto the nodes too
    // (build_confidence_spans writes node.meta.confidenceSpans). Rebuild the
    // document-level array from those copies when the document-level one is
    // absent, so a hydrated document highlights exactly like the live payload.
    // Never invents a score: a node without spans contributes nothing.
    function _nodeConfidenceSpans(doc) {
      var out = [];
      function walk(node) {
        if (!node) return;
        var own = node.meta && (node.meta.confidenceSpans || node.meta.confidence_spans);
        if (Array.isArray(own)) {
          for (var i = 0; i < own.length; i++) out.push(own[i]);
        }
        if (node.children && Array.isArray(node.children)) {
          for (var c = 0; c < node.children.length; c++) walk(node.children[c]);
        }
      }
      var body = (doc && doc.body) || [];
      for (var b = 0; b < body.length; b++) walk(body[b]);
      return out;
    }

    function applyConfidenceSpans(doc, targetEl) {
      if (!doc) return;
      // Confidence spans use field names startChar / endChar / nodeId
      // (NOT start / end / node_id). Prefer camelCase, fall back to
      // snake_case; both duplicate the same array on the live payload.
      // GET /api/projects/<pid>/jdf returns the persisted tree, which carries
      // the same array under meta.confidenceSpans (document level) and, for
      // nodes with spans, under node.meta.confidenceSpans — so a boot-hydrated
      // document is renderable with no fresh compile.
      var spans = doc.confidenceSpans || doc.confidence_spans ||
                  (doc.meta && (doc.meta.confidenceSpans || doc.meta.confidence_spans));
      if (!Array.isArray(spans) || spans.length === 0) spans = _nodeConfidenceSpans(doc);
      if (!Array.isArray(spans) || spans.length === 0) return;
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

        // No tooltip: the 1px semantic underline is the signal (Phase F), and
        // the drawer behind a click carries source, page and quote. Only the
        // node's first provenance row is needed, to band the span.
        var provNode = findJdfNodeById(nodeId, doc);
        var provMeta = provNode ? (provNode.meta && provNode.meta.provenance) : null;
        var provList = Array.isArray(provMeta) ? provMeta : (provMeta ? [provMeta] : []);
        var prov0 = provList[0] || null;

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
          // §4 A7: the span is a click target, so it is authored as a control —
          // role, tab stop and a name that states its confidence band. The
          // neutral wording is deliberate; persuasive phrasing goes to the
          // voice pass.
          var band = !prov0 ? "no source matched"
                   : (sp.score > 0.8) ? "high"
                   : (sp.score >= 0.4) ? "medium" : "low";
          var wrapped = '<span class="' + confClass + '" data-node-id="' + escapeAttr(nodeId) +
            '" data-score="' + escapeAttr(String(sp.score)) +
            '" role="button" tabindex="0" aria-label="' + escapeAttr("Confidence span: " + band) + '">' +
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
          createdSpans[csp].addEventListener("keydown", _confSpanKeydown);   // §4 A7
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
    function handleEvent(event, data) {
      if (event === "[DONE]") return;
      if (event === "status" && data && typeof data === "object") {
        var stage = data.stage;
        if (stage === "preflight") {
          transitionTo("Preparing");
        } else if (stage === "model") {
          markDone("Preparing");
          transitionTo("Drafting");
          // status{stage:"model"} carries the model the compile path routed to
          // (draft.py:543-546). This frame is the only client-reachable source
          // for ROUTED TO; /api/compile-system answers {prompt} alone.
          if (data.model) {
            __lastRunModel = String(data.model);
            _renderCompilerRoute();
          }
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
      } else if (event === "redhat" && data && typeof data === "object") {
        // Red-Hat pipeline stage state for this compile (ran | skipped | failed).
        var _rh = (data && data.redhat && typeof data.redhat === "object") ? data.redhat : data;
        __lastRunRedhat = _rh.status ? String(_rh.status) : "";
        _renderCompilerRoute();
        var selNodeId = SHELL.ui.selection ? SHELL.ui.selection.nodeId : null;
        var selNode = (selNodeId && SHELL.document.current) ?
          findJdfNodeById(selNodeId, SHELL.document.current) : null;
        renderRedhatPanel(selNode || null);
      } else if (event === "compiled") {
        markDone("Lock Inference");
        transitionTo("Compile");
        _clearCompilerPromptIfStale();
        if (data && data.document) {
          var cdoc = data.document;
          if (cdoc && cdoc.body && Array.isArray(cdoc.body)) {
            setShell("document.mode", "streaming");
            renderJdfDocument(cdoc);
          } else {
            // Parse failure: never replace the document with raw text.
            try { console.error("[shell] compiled event missing parseable doc.body"); } catch (_) {}
          }
        }
      } else if (event === "verified") {
        // A zero-check Math Check is not a pass: the server reports SKIPPED
        // when no "key: value" metric was in the draft, and a green done dot
        // would claim a verification that never ran.
        var _z3r = (data && (data.z3_results || data.z3 || null)) || {};
        var _z3s = String((data && data.z3_status) || _z3r.status || "").toUpperCase();
        if (_z3s === "SKIPPED") markSkipped("Math Check", _z3r.skip_reason || "no metrics to check");
        else markDone("Math Check");
        transitionTo("Verify");
        if (data && data.document) {
          setShell("document.current", data.document);
          setShell("document.mode", "ready");
          var vdoc = data.document;
          if (vdoc && vdoc.body && Array.isArray(vdoc.body)) {
            addEvidenceChips(vdoc);
            applyConfidenceSpans(vdoc);

            // Unclear whether provenance_stats lives top-level or nested —
            // check the SSE payload; use the real location.
            var stats = data.provenance_stats || null;
            if (!stats && data.document && data.document.meta) {
              stats = data.document.meta.provenance_stats || null;
            }

            // Verified frame: the persisted stats are authoritative (DB parity
            // with the gate block the honesty test reads).
            _syncGroundingNotices(stats);
            // Verification laser: one horizontal sweep across the rendered
            // document, per compile. Anchored to .doc-draft (not .doc-surface,
            // which also holds the empty hero).
            if (window.runLaserSweep) {
              runLaserSweep(document.querySelector(".doc-draft"));
            }
          } else {
            try { console.error("[shell] verified event missing parseable doc.body"); } catch (_) {}
          }
          var _vid = _activeProjectId();
          if (_vid) _loadVersionHistory(_vid, { current: "latest" });
        }
      } else if (event === "complete") {
        // The run finished: Compile can still be .active when the
        // "running math check" status frame never arrived, because
        // transitionTo("Verify") only marks stages STRICTLY before the
        // index it is leaving behind. Close it out explicitly so no
        // stage dot keeps pulsing after a finished run.
        markDone("Compile");
        markDone("Verify");
        markActive("Complete");
        markDone("Complete");
        _endProgress();
        _refreshManifest();
        _setRunInProgress(false);
        clearIntentSlot();
        _clearCompilerPromptIfStale();
        // The run is over: bring the retained stage rows back into view.
        // beginIntentCompile had forced the left pane onto the COMPILER tab.
        leftGroupSetTab("compiler");
      } else if (event === "error") {
        var active = findActiveStage() || STAGE_ORDER[
          (currentStageIndex >= 0) ? currentStageIndex : 0
        ];
        markFailed(active);
        _endProgress();
        _setRunInProgress(false);
        // A failed run is still an ended run: drop the success bar left by
        // the intent compile and bring the retained stage rows back into
        // view, so the failed row is visible instead of a stale
        // "\u2713 Intent compiled" slot hiding the stages.
        clearIntentSlot();
        leftGroupSetTab("compiler");
        var msg = (data && data.error) ? data.error : (data ? JSON.stringify(data) : "unknown error");
        if (data && Number(data.http_status) === 422) _discardStreamedDraft();
        appendDocError(msg);
        try { console.error("[shell] error event:", msg); } catch (_) {}
      }
    }

    // ---------------------------------------------------------------
    // Project bootstrap + stream execution
    // ---------------------------------------------------------------
    function jsonPost(url, bodyObj, extraHeaders, signal) {
      var hdrs = { "Content-Type": "application/json", "Accept": "application/json, text/event-stream" };
      if (extraHeaders) {
        for (var k in extraHeaders) if (Object.prototype.hasOwnProperty.call(extraHeaders, k)) hdrs[k] = extraHeaders[k];
      }
      var init = {
        method: "POST",
        headers: hdrs,
        body: JSON.stringify(bodyObj),
      };
      if (signal) init.signal = signal;
      return fetch(url, init);
    }

    function ensureProjectId() {
      try {
        var existing = SHELL.project.id || null;
        if (!existing) {
          try { existing = window.localStorage.getItem(STORAGE_KEY); } catch (_) { existing = null; }
        }
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
          setShell("project.id", id);
          return id;
        });
    }

    // ---------------------------------------------------------------
    // Project switcher (top bar dropdown)
    // ---------------------------------------------------------------
    var projectSwitcherBtn = document.getElementById("project-switcher");
    var projectSwitcherPanel = document.getElementById("project-switcher-panel");
    projectCurrentNameEl = document.getElementById("project-current-name");
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
          var active = SHELL.project.id || "";
          if (!active) { try { active = window.localStorage.getItem(STORAGE_KEY) || ""; } catch (_) {} }
          projects.forEach(function (p) {
            var row = document.createElement("div");
            row.className = "project-row" + (p.id === active ? " is-active" : "");
            row.setAttribute("data-project-id", p.id);
            var title = document.createElement("span");
            title.className = "project-row-title";
            title.textContent = p.title || p.id;
            var meta = document.createElement("span");
            meta.className = "project-row-meta";
            meta.textContent = (p.source_count || 0) + " sources \u00b7 last modified " + _projectRelativeTime(p.updated_at);
            row.title = p.id;
            row.addEventListener("click", function () { _switchProject(p.id, p.title); });

            var actions = document.createElement("span");
            actions.className = "project-row-actions";
            var renameBtn = document.createElement("button");
            renameBtn.type = "button";
            renameBtn.className = "project-rename";
            renameBtn.title = "Rename";
            renameBtn.setAttribute("aria-label", "Rename project");
            renameBtn.textContent = "\u270e";
            renameBtn.addEventListener("click", function (ev) {
              ev.stopPropagation();
              _renameProject(p.id, p.title || p.id, title);
            });
            var deleteBtn = document.createElement("button");
            deleteBtn.type = "button";
            deleteBtn.className = "project-delete";
            deleteBtn.title = "Delete";
            deleteBtn.setAttribute("aria-label", "Delete project");
            deleteBtn.textContent = "\u00d7";
            deleteBtn.addEventListener("click", function (ev) {
              ev.stopPropagation();
              _deleteProject(p.id, row);
            });
            actions.appendChild(renameBtn);
            actions.appendChild(deleteBtn);

            row.appendChild(title);
            row.appendChild(meta);
            row.appendChild(actions);
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
    function _activeProjectId() {
      try { return SHELL.project.id || window.localStorage.getItem(STORAGE_KEY) || ""; } catch (_) { return SHELL.project.id || ""; }
    }
    function _renameProject(id, currentTitle, titleEl) {
      var name = window.prompt("Rename project:", currentTitle);
      if (name === null) return;
      name = String(name || "").trim();
      if (!name) return;
      fetch("/api/projects/" + encodeURIComponent(id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: name }),
      })
        .then(function (r) {
          if (!r.ok) throw new Error("projects PATCH " + r.status);
          if (titleEl) titleEl.textContent = name;
          if (id === _activeProjectId()) setShell("project.title", name);
        })
        .catch(function (err) { try { console.warn("[shell] rename project failed:", err); } catch (_) {} });
    }
    function _deleteProject(id, row) {
      if (!window.confirm("Delete this project? This cannot be undone.")) return;
      var wasActive = id === _activeProjectId();
      fetch("/api/projects/" + encodeURIComponent(id), { method: "DELETE" })
        .then(function (r) {
          if (!r.ok) throw new Error("projects DELETE " + r.status);
          if (row && row.parentNode) row.parentNode.removeChild(row);
          if (wasActive) {
            fetch("/api/projects")
              .then(function (resp) { return resp.ok ? resp.json() : { projects: [] }; })
              .then(function (j) {
                var projects = (j && j.projects) || [];
                if (projects.length) {
                  _switchProject(projects[0].id, projects[0].title);
                } else {
                  _createNewProject();
                }
              })
              .catch(function () { _createNewProject(); });
          }
        })
        .catch(function (err) { try { console.warn("[shell] delete project failed:", err); } catch (_) {} });
    }
    function _refreshProjectName() {
      var active = SHELL.project.id || "";
      if (!active) { try { active = window.localStorage.getItem(STORAGE_KEY) || ""; } catch (_) {} }
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
    function _refreshSignoff(projectId) {
      var id = projectId || SHELL.project.id || null;
      if (!id) { try { id = window.localStorage.getItem(STORAGE_KEY) || null; } catch (_) { id = null; } }
      if (!id) return;
      fetch("/api/projects/" + encodeURIComponent(id) + "/sign-offs")
        .then(function (r) { return r.ok ? r.json() : {}; })
        .then(function (j) {
          var signed = (j.sign_offs || []).some(function (s) { return s.status === "approved"; });
          setShell("document.signoff.status", signed ? "signed" : "draft");
        })
        .catch(function () {});
    }
    function _loadProjectSourceList(id) {
      if (!id) return;
      fetch("/api/projects/" + encodeURIComponent(id) + "/substrate")
        .then(function (r) { return r.ok ? r.json() : { files: [] }; })
        .then(function (j) {
          var rows = (j && j.files) || [];
          setShell("sources", rows.map(function (f) { return f.id; }));
          var el = document.getElementById("source-list");
          if (el) {
            while (el.firstChild) el.removeChild(el.firstChild);
            rows.forEach(function (f) {
              el.appendChild(_buildSourceRow(f.filename || "", f.id, f));
            });
          }
          // The manifest reads the same response: filename, size, indexed date.
          manifestRows = rows;
          _renderManifest();
        })
      .catch(function () {});
    }
    // ---------------------------------------------------------------
    // SOURCES — the OMP manifest: filename, size, indexed date, and after a
    // compile each source's "anchored N of M". Read from the same vault route
    // the Sources tab lists, so the two surfaces cannot disagree.
    // ---------------------------------------------------------------
    var manifestRows = [];
    function _manifestSize(bytes) {
      var b = Number(bytes) || 0;
      if (b < 1024) return b + " B";
      if (b < 1024 * 1024) return (b / 1024).toFixed(1) + " KB";
      return (b / (1024 * 1024)).toFixed(1) + " MB";
    }
    // created_at is written by SQLite as "YYYY-MM-DD HH:MM:SS" in UTC. The
    // date is read off the string, never re-derived through the local clock.
    function _manifestDate(raw) {
      var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(raw || ""));
      if (!m) return "";
      var months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
      return parseInt(m[3], 10) + " " + months[parseInt(m[2], 10) - 1] + " " + m[1];
    }
    function _anchoredBySource(doc) {
      var by = {};
      var sections = (doc && Array.isArray(doc.body)) ? doc.body : [];
      for (var s = 0; s < sections.length; s++) {
        if (!sections[s] || typeof sections[s] !== "object") continue;
        var group = [sections[s]];
        if (Array.isArray(sections[s].children)) group = group.concat(sections[s].children);
        for (var g = 0; g < group.length; g++) {
          var node = group[g];
          if (!node || String(node.type || "") !== "paragraph") continue;
          var prov = node.provenance;
          if (!Array.isArray(prov)) prov = prov ? [prov] : [];
          for (var i = 0; i < prov.length; i++) {
            var row = prov[i];
            if (!row || typeof row !== "object") continue;
            var sid = String(row.source_id || "");
            if (!sid) continue;
            var bucket = by[sid] || (by[sid] = { anchored: 0, total: 0 });
            bucket.total++;
            if (String(row.extracted_quote || "").trim()) bucket.anchored++;
          }
        }
      }
      return by;
    }
    function _renderManifest() {
      var el = document.getElementById("source-manifest");
      if (!el) return;
      var by = _anchoredBySource(SHELL.document.current);
      while (el.firstChild) el.removeChild(el.firstChild);
      manifestRows.forEach(function (f) {
        var row = document.createElement("div");
        row.className = "manifest-row";
        var name = document.createElement("span");
        name.className = "manifest-name";
        name.textContent = f.filename || f.id || "";
        var meta = document.createElement("span");
        meta.className = "manifest-meta";
        meta.textContent = [_manifestSize(f.file_size_bytes), _manifestDate(f.created_at)]
          .filter(Boolean).join(" \u00b7 ");
        row.appendChild(name);
        row.appendChild(meta);
        // The ingest scan's verdict sits beside the source's own metadata, not
        // in place of it: the source is still a source.
        var flag = _buildSourceFlag(f);
        if (flag) row.appendChild(flag);
        var counts = by[String(f.id)];
        if (counts && counts.total > 0) {
          var seen = document.createElement("span");
          seen.className = "manifest-count";
          seen.textContent = "anchored " + counts.anchored + " of " + counts.total;
          row.appendChild(seen);
        }
        el.appendChild(row);
      });
    }
    _renderManifestFn = _renderManifest;
    // The counter line counts sources, so it is re-rendered whenever the
    // source list or the document changes.
    function _refreshCounters() {
      var doc = SHELL.document.current;
      _renderCounters((doc && doc.meta && doc.meta.provenance_stats) || null,
                      doc ? _derivedCounts(doc) : null);
    }
    _syncCountersFn = _refreshCounters;
    function _refreshManifest() {
      var pid = _sourceProjectId();
      if (!pid) return;
      fetch("/api/projects/" + encodeURIComponent(pid) + "/substrate")
        .then(function (r) { return r.ok ? r.json() : { files: [] }; })
        .then(function (j) {
          manifestRows = (j && j.files) || [];
          _renderManifest();
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
      // §5 row 3: a switched-away run is over, so the dock must stop claiming
      // it is running (the aborted stream's own error path may never arrive).
      _setRunInProgress(false);
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
            setShell("document.mode", "ready");
            setShell("document.current", doc);
            renderJdfDocument(doc);
            // The counters describe the document now on screen. Without this
            // the previous project's numbers carried over. A target with no
            // persisted stats derives locally, on the same verdict rule.
            _syncGroundingNotices((doc.meta && doc.meta.provenance_stats) || null);
          } else {
            resetStages();
            clearDocument();
          }
          _loadVersionHistory(id, { current: null });
          _refreshSignoff(id);
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
          setShell("project.id", id);
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
    _refreshSignoff();

    // S1: restore persisted pane collapse state (default false → both panes
    // visible on first-ever load), then render. _applyRightView() draws the
    // right pane's inner state independent of the collapse classes.
    try {
      SHELL.ui.layout.leftCollapsed  = localStorage.getItem("assure.left_collapsed")  === "1";
      SHELL.ui.layout.rightCollapsed = localStorage.getItem("assure.right_collapsed") === "1";
    } catch (_) {}
    setShell("ui.layout.leftCollapsed",  SHELL.ui.layout.leftCollapsed);
    setShell("ui.layout.rightCollapsed", SHELL.ui.layout.rightCollapsed);
    _applyRightView();

    // T2: populate SHELL.sources on init so compiles after reload carry
    // real substrate_file_ids (S0.5 — currently via sourceIds, now SHELL.sources).
    var initId = SHELL.project.id;
    if (!initId) {
      try { initId = window.localStorage.getItem(STORAGE_KEY); } catch (_) { initId = null; }
    }
    if (initId) {
      _loadProjectSourceList(initId);
      // Reload: the server still holds the document, the version list and the
      // drafting state. Draw them back instead of leaving the first-run empty
      // hero in place.
      _restoreProjectDocument(initId);
    }

    // Export (top bar) → audit PDF download for the active project. Disabled
    // until _canExport() (see _syncExportEnabled), which the document paths
    // re-evaluate; the click re-reads the same state instead of dead-ending in
    // a console.warn.
    exportBtnEl = document.getElementById("export-btn");
    if (exportBtnEl) {
      exportBtnEl.addEventListener("click", function () {
        if (!_canExport()) { _syncExportEnabled(); return; }
        window.location.href =
          "/api/projects/" + encodeURIComponent(_exportProjectId()) + "/export?format=audit-pdf";
      });
      _syncExportEnabled();
    }

    function runDraft(intent) {
      // Abort any previous center draft stream, then start a fresh one.
      if (SHELL.streams.draft) { try { SHELL.streams.draft.abort(); } catch (_) {} }
      setShell("streams.draft", new AbortController());
      var thisRequest = SHELL.streams.draft;
      resetStages();
      _startProgress();
      clearDocument();
      currentStageIndex = -1;
      __lastRunModel = "";
      __lastRunRedhat = "";
      // Opens the right pane when a compile starts. Note: this
      // re-expands a user-collapsed pane on every compile. If that
      // feels wrong in use, change to: only expand when
      // SHELL.ui.layout.rightCollapsed is false OR when the user is
      // in Compare mode.
      // Note: this re-expands the right pane even during focus mode
      // (both panes collapsed). If that feels wrong in use, gate
      // this call on: !(SHELL.ui.layout.leftCollapsed &&
      // SHELL.ui.layout.rightCollapsed).
      openRight();
      var parser = parseSseLoop(
        handleEvent,
        function () {},
        function (err) {
          var active = findActiveStage() || STAGE_ORDER[Math.max(0, currentStageIndex)];
          markFailed(active);
          _setRunInProgress(false);
          // A parse error also ends the run: drop the success bar left by the
          // intent compile and bring the retained stage rows back into view,
          // so the failed row is visible instead of a stale
          // "Intent compiled" slot hiding the stages — the same
          // stale-slot defect the handleEvent error branch fixed.
          clearIntentSlot();
          leftGroupSetTab("compiler");
          appendDocError(String(err && err.message ? err.message : err));
          logSseFailure(sseEndpoint, sseStartedAt, sseTokens, err, false);
          try { console.error("[shell] stream parse error:", err); } catch (_) {}
        }
      );
      var started = false;
      var sseEndpoint = "";
      var sseStartedAt = Date.now();
      var sseTokens = 0;
      return ensureProjectId()
        .then(function (projectId) {
          var url = "/api/projects/" + encodeURIComponent(projectId) + "/draft/stream";
          sseEndpoint = url;
          return fetch(url, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Accept": "text/event-stream, application/json",
            },
            body: JSON.stringify({ intent: intent, compileType: DRAFT_TYPE, substrate_file_ids: SHELL.sources }),
            signal: SHELL.streams.draft.signal,
          });
        })
        .then(function (resp) {
          started = true;
          if (!resp.ok) {
            return resp.text().then(function (t) {
              logSseFailure(sseEndpoint, sseStartedAt, sseTokens, new Error("HTTP " + resp.status), false);
              try { var j = JSON.parse(t); handleEvent("error", j); return; }
              catch (_) { handleEvent("error", { ok: false, error: t || "HTTP " + resp.status }); }
            });
          }
          if (!resp.body) {
            logSseFailure(sseEndpoint, sseStartedAt, sseTokens, new Error("Response body unavailable."), false);
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
              sseTokens += str.length;
              parser.feed(str);
              return loop();
            }).catch(function (err) {
              // Abort is intentional — exit quietly, no error event.
              if (err && err.name === "AbortError") { logSseFailure(sseEndpoint, sseStartedAt, sseTokens, err, true); return; }
              throw err;
            });
          }
          return loop();
        })
        .catch(function (err) {
          // A clean abort must not surface as an error or rewrite the canvas.
          if (err && err.name === "AbortError") {
            logSseFailure(sseEndpoint, sseStartedAt, sseTokens, err, true);
            return;
          }
          logSseFailure(sseEndpoint, sseStartedAt, sseTokens, err, false);
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
    var lastCompile = null;       // last /api/compile-system response
    var runInProgress = false;    // a draft/stream is actively running

    // §5 row 3: the dock's running state follows the draft flag, so every
    // write to it goes through here and the surface cannot drift.
    function _setRunInProgress(v) {
      runInProgress = Boolean(v);
      _syncDockSubmit();
    }

    function submitIntent() {
      if (!text) return;
      var v = String(text.value || "").trim();
      if (!v) return;
      text.value = "";
      _syncDockSubmit();
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

    function populateCompilerAsk(v)  { setShell("compiler.ask", v || ""); }
    function setCompilerPrompt(v)    { setShell("compiler.prompt", v || ""); }
    function populateCompilerRoute(v){ setShell("compiler.route", v || ""); }
    // The compile-system fetch is the only writer of this placeholder, and it
    // races the draft stream. If the pipeline finishes first, clear it so the
    // panel cannot sit on "Compiling…" forever.
    function _clearCompilerPromptIfStale() {
      if (SHELL.compiler.prompt === "Compiling\u2026") setCompilerPrompt("");
    }
    // ROUTED TO — the model this compile was routed to plus the Red-Hat pass
    // state, both taken from the draft stream itself. /api/compile-system
    // answers {prompt} only (web.py:1180-1187), so it can supply neither.
    function _renderCompilerRoute() {
      var parts = [];
      if (__lastRunModel) parts.push(__lastRunModel);
      if (__lastRunRedhat) parts.push("Red-Hat " + __lastRunRedhat);
      populateCompilerRoute(parts.join(" \u00b7 "));
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
      // /api/compile-system takes no body and answers {prompt} (web.py).
      jsonPost("/api/compile-system", {})
        .then(function (res) { return res.json(); })
        .then(function (j) {
          if (!j || typeof j !== "object") return;
          // Remembered so re-opening the COMPILER tab (expandIntentPanel)
          // can restore the prompt that was actually compiled against.
          lastCompile = j;
          if (typeof j.prompt === "string" && j.prompt.length > 0) {
            setCompilerPrompt(j.prompt);
          }
          // The bar is painted before this promise settles, so the tick only
          // becomes true once the prompt actually came back. No count and no
          // check result: the pipeline stages report those.
          if (intentSummaryTextEl) {
            intentSummaryTextEl.textContent = "\u2713 Intent compiled \u00b7 checks run in the pipeline";
          }
        })
        .catch(function (err) {
          setCompilerPrompt("(compiler unavailable)");
          if (intentSummaryTextEl) {
            intentSummaryTextEl.textContent = "Intent compiler unavailable \u00b7 checks run in the pipeline";
          }
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
      _setRunInProgress(true);
      runDraft(raw); // ORIGINAL raw ask, NOT the compiled prompt
    }

    function runAnyIntent(raw) {
      if (!raw) return;
      intentPanelOpen = false;
      clearIntentSlot();
      _setRunInProgress(true);
      runDraft(raw);
    }

    function cancelIntent() {
      if (pendingIntent && text) text.value = pendingIntent;
      _syncDockSubmit();
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
      } else {
        setCompilerPrompt("(compiler unavailable)");
      }
      _renderCompilerRoute();
    }

    function clearIntentSlot() {
      if (!intentPanelSlot) return;
      while (intentPanelSlot.firstChild) intentPanelSlot.removeChild(intentPanelSlot.firstChild);
      intentSummaryTextEl = null;
    }

    // The intent bar's text node, so beginIntentCompile can flip it once the
    // compile-system promise settles (the bar is drawn before that).
    var intentSummaryTextEl = null;
    function renderIntentSummary(raw) {
      if (!intentPanelSlot) return;
      clearIntentSlot();
      var bar = document.createElement("div");
      bar.className = "intent-summary";
      var textEl = document.createElement("span");
      textEl.className = "intent-summary-text";
      // Not a result: the intent prompt may still be in flight, and the
      // verification checks run (or skip) later in the pipeline, where the
      // per-stage rows report their real state.
      textEl.textContent = "\u2026 Compiling intent";
      intentSummaryTextEl = textEl;
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

    leftSourcesEl  = document.getElementById("left-sources");
    leftCompilerEl = document.getElementById("left-compiler");
    leftHistoryEl  = document.getElementById("left-history");
    leftReferencesEl = document.getElementById("left-references");
    leftTemplatesEl = document.getElementById("left-templates");
    evidenceModeEl = document.getElementById("right-evidence");
    compareModeEl  = document.getElementById("right-compare");
    z3ModeEl         = document.getElementById("right-z3");
    redhatModeEl     = document.getElementById("right-redhat");
    rightInspectorEl = document.getElementById("right-inspector");
    compareToggleEl  = document.getElementById("compare-toggle");
    rightModeToggleEl = document.querySelector("#pane-right .mode-toggle");
    _applyRightViewFn = _applyRightView;
    var compareBodyEl  = document.getElementById("compare-body");
    var evidenceBodyEl = evidenceModeEl;
    if (docSurface) docSurface.addEventListener("click", function (e) {
      // The node is read from whatever carries data-node-id (a node wrapper,
      // an evidence chip, or a confidence span) — the earlier .jdf-span /
      // .jdf-chip guards matched nothing, because neither class exists.
      var nodeEl = e.target.closest("[data-node-id]");
      var nid = nodeEl ? nodeEl.getAttribute("data-node-id") : null;
      // A confidence span and an evidence chip own their click: each writes its
      // own evidence payload before the node id, so neither goes through this
      // selection. Any other target is a plain paragraph selection, and the
      // payload written for the last chip or span must not outlive it — the
      // pane would otherwise keep a drawer that belongs to the node the user
      // just re-selected. The clear follows the node id so the id's paint is
      // the one that already shows the panel (shell.js:_syncShellPathToDom).
      if (!e.target.closest(".conf-span") && !e.target.closest(".chip")) {
        setShell("ui.selection.nodeId", nid);
        setShell("ui.selection.evidence", null);
        // A plain paragraph is a selection like a confidence span's or a
        // chip's, so it reveals the pane and names its tab exactly the way
        // those two handlers do (handleConfidenceClick, handleChipClick:
        // openRight() then setMode("evidence")). Writing the selection alone
        // left the pane collapsed, and left it on whatever tab it already had
        // when it was open. Inside this guard, like the writes above: a span
        // and a chip own their own click, so a Red-Hat chip must still leave
        // the pane on Red-Hat rather than have this handler name Evidence.
        if (nid) {
          openRight();
          setMode("evidence");
        }
      }
      if (nid) {
        if (typeof _attachNodeRephrase === "function") _attachNodeRephrase(nid);
        if (typeof _loadNodeHistory === "function") _loadNodeHistory(nid);
      }
    });
    if (compareToggleEl) compareToggleEl.addEventListener("click", function () {
      if (compareInFlight) return;
      _toggleCompareView();
    });
    var intentPanelSlot = document.getElementById("intent-panel-slot");
    var pinnedListEl   = document.getElementById("pinned-list"); // removed; helpers no-op
    var compareInFlight = false;
    var compareDataLoaded = false;
    var compareStreamsDone = 0;     // number of compare streams finished/errored

    var LEFT_TABPANE = {
      sources:   leftSourcesEl,
      compiler:  leftCompilerEl,
      history:   leftHistoryEl,
      references: leftReferencesEl,
      templates:  leftTemplatesEl,
    };
    var RIGHT_TABPANE = {
      evidence: evidenceModeEl,
      z3:     z3ModeEl,
      redhat: redhatModeEl,
    };

    function leftGroupSetTab(name) {
      var resolved = LEFT_TABPANE[name] ? name : "sources";
      setShell("ui.leftTab", resolved);
      return resolved;
    }

    function rightGroupSetTab(name) {
      var resolved = RIGHT_TABPANE[name] ? name : "evidence";
      setShell("ui.rightTab", resolved);
      return resolved;
    }

    // Backward-compatible dispatch used by existing flows.
    function setMode(name) {
      if (name === "compare") { _toggleCompareView(); return; }
      if (name === "evidence") { rightGroupSetTab("evidence"); return; }
      leftGroupSetTab(name);
    }

    function setCompareDisabled(disabled) {
      compareInFlight = !!disabled;
      if (compareToggleEl) {
        if (disabled) compareToggleEl.classList.add("is-disabled");
        else          compareToggleEl.classList.remove("is-disabled");
      }
    }

    document.querySelectorAll("[data-left-tab]").forEach(function (t) {
      t.addEventListener("click", function () {
        leftGroupSetTab(t.getAttribute("data-left-tab"));
      });
    });
    document.querySelectorAll("[data-right-tab]").forEach(function (t) {
      t.addEventListener("click", function () {
        if (t.classList.contains("is-disabled")) return;
        rightGroupSetTab(t.getAttribute("data-right-tab"));
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
      if (!doc || !doc.body || !Array.isArray(doc.body)) {
        setMode("compiler");
        return;
      }
      setShell("document.mode", "ready");
      setShell("document.current", doc);
      // Re-render the JDF object fresh into the center (targetEl null →
      // center path, which re-wires draftEl so all event listeners +
      // interactions work). Do NOT paste text or copy the column's innerHTML.
      renderJdfDocument(doc);
      applyConfidenceSpans(doc);
      addEvidenceChips(doc);
      var oldErrs = docSurface.querySelectorAll(".doc-error");
      for (var i = 0; i < oldErrs.length; i++) oldErrs[i].remove();
      docSurface.scrollTop = 0;
      setMode("compiler");
    }

    function compareStreamFinished() {
      compareStreamsDone += 1;
      // Only re-enable the tab once BOTH streams finish (or error). A
      // failing stream must not block the healthy sibling.
      if (compareStreamsDone >= 2) setCompareDisabled(false);
    }
    function compareStreamSide(col, modelId) {
      var controller = new AbortController();
      var intent;
      try { intent = window.sessionStorage.getItem(LAST_INTENT_KEY); } catch (_) { intent = null; }
      if (!intent) { compareShowError(col, "No stored intent."); return controller; }

      var sseEndpoint = "";
      var sseStartedAt = Date.now();
      var sseTokens = 0;
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
            logSseFailure(sseEndpoint, sseStartedAt, sseTokens, new Error(data.error || "Compare stream error."), false);
            compareShowError(col, data.error || "Compare stream error.");
          }
        },
        function () { compareStreamFinished(); },
        function (err) {
          logSseFailure(sseEndpoint, sseStartedAt, sseTokens, err, false);
          compareShowError(col, String(err && err.message ? err.message : err));
          compareStreamFinished();
        }
      );

      ensureProjectId()
        .then(function (projectId) {
          var url = "/api/projects/" + encodeURIComponent(projectId) + "/draft/stream";
          sseEndpoint = url;
          return fetch(url, {
            method: "POST",
            signal: controller.signal,
            headers: { "Content-Type": "application/json", "Accept": "text/event-stream, application/json" },
            body: JSON.stringify({ intent: intent, compileType: "full", target_ai: modelId, substrate_file_ids: SHELL.sources }),
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
              sseTokens += str.length;
              parser.feed(str);
              return loop();
            });
          }
          return loop();
        })
        .catch(function (err) {
          if (err && err.name === "AbortError") { logSseFailure(sseEndpoint, sseStartedAt, sseTokens, err, true); compareStreamFinished(); return; }
          logSseFailure(sseEndpoint, sseStartedAt, sseTokens, err, false);
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

      var grid = document.createElement("div");
      grid.className = "compare-grid";
      compareBodyEl.appendChild(grid);

      var colA = compareColumnShell("claude", "anthropic/claude-sonnet-4-5");
      var colB = compareColumnShell("deepseek", "deepseek/deepseek-chat");
      grid.appendChild(colA);
      grid.appendChild(colB);

      compareDataLoaded = true;         // do not refire on tab re-click
      setShell("streams.compareA", compareStreamSide(colA, "anthropic/claude-sonnet-4-5"));
      setShell("streams.compareB", compareStreamSide(colB, "deepseek/deepseek-chat"));
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
    function handleChipClick(e) {
      var chip = e.currentTarget;
      var nodeId = chip.getAttribute("data-node-id");
      var kind = chip.getAttribute("data-kind");
      var index = parseInt(chip.getAttribute("data-index"), 10);
      if (!SHELL.document.current || !nodeId || !kind || isNaN(index)) return;
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
      var node = findNode(SHELL.document.current.body || []);
      if (!node) return;
      // A Red-Hat chip is a selector, not a drawer opener: the Red-Hat pane
      // owns the findings list, and the drawer write below was cleared by
      // _applyRightView in the same click.
      if (kind === "redhat") {
        if (!node.annotations || !node.annotations.redhat || !node.annotations.redhat[index]) return;
        setShell("ui.selection.nodeId", nodeId);
        setShell("ui.rightTab", "redhat");
        return;
      }
      var evidence = null;
      if (kind === "z3" && node.annotations && node.annotations.z3 && node.annotations.z3[index]) {
        evidence = { kind: "z3", nodeId: nodeId, data: node.annotations.z3[index] };
      } else if (kind === "cite" && node.provenance && node.provenance[index]) {
        evidence = { kind: "cite", nodeId: nodeId, data: node.provenance[index] };
      }
      if (!evidence) return;
      // §6: as with a confidence span, the payload goes in first so the node id
      // write below paints it. The drawer used to be built right here and then
      // wiped by _applyRightView in the same click — which is also why its
      // "Ground with sources" action had no route to the screen.
      setShell("ui.selection.evidence", evidence);
      setShell("ui.selection.nodeId", nodeId);
      openRight();
      setMode("evidence");
    }

    function findJdfNodeById(nodeId, tree) {
      var root = tree || SHELL.document.current;
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

    // §4 A7: the confidence span is a click target, so Enter and Space must
    // activate it exactly as a click does. Space is prevented from scrolling.
    function _confSpanKeydown(e) {
      var k = e.key;
      if (k !== "Enter" && k !== " " && k !== "Spacebar") return;
      e.preventDefault();
      handleConfidenceClick(e);
    }

    function handleConfidenceClick(e) {
      var span = e.currentTarget;
      var nodeId = span.getAttribute("data-node-id");
      if (!nodeId) return;
      // §6: state in, one paint out. The payload is written first, while
      // another node (or none) is still selected, so it repaints nothing; the
      // node id write below is the click's single render, and the pane draws
      // the paragraph panel with this span's ledger tail already attached. The
      // second writer that used to clear and repaint the pane here is gone.
      setShell("ui.selection.evidence", {
        kind: "confidence",
        nodeId: nodeId,
        data: { score: span.getAttribute("data-score") },
      });
      setShell("ui.selection.nodeId", nodeId);
      openRight();
      setMode("evidence");
    }

    // §6: demoted — no longer a writer. renderEvidencePanel owns the pane and
    // paints the paragraph panel (verdict header, reasoning, excerpt, fields);
    // this appends the clicked span's ledger tail under it. The score is the
    // span's own ledger signal, carried on the selection payload, so the span
    // element is never read back out of the DOM. The empty-source wording is
    // the panel's now (one surface, one wording) — the two used to differ only
    // here, which is the kind of drift a second writer causes.
    function renderConfidenceEvidence(ev) {
      if (!evidenceBodyEl) return;
      var score = parseFloat(ev && ev.data ? ev.data.score : "");
      if (isNaN(score)) score = 0;
      var foot = document.createElement("div");
      foot.className = "evidence-footer";
      foot.textContent = "Ledger check score: " +
        ((score <= 1) ? (Math.round(score * 100) + "%") : String(score));
      evidenceBodyEl.appendChild(foot);
    }

    // Single owner of pane reveal. index.html authors `hidden` on all three
    // bodies while `.pane-body` sets `display: flex`, so clearing only the
    // inline display (as this used to) leaves the pane invisible even when
    // the tab strip has flipped is-active. Set both, together.
    function _setInspectorPane(active) {
      var panes = { evidence: evidenceModeEl, z3: z3ModeEl, redhat: redhatModeEl };
      Object.keys(panes).forEach(function (k) {
        if (!panes[k]) return;
        var on = (k === active);
        panes[k].hidden = !on;
        panes[k].style.display = on ? "block" : "none";
      });
    }
    // No node selected: the active tab still owns the pane, so keep it
    // revealed with a styled hint rather than a blank (or stale) body. The
    // real panel renderers are deliberately not called with a null node.
    // §6: the evidence body's idle state is a node-less call of the pane's one
    // writer, so #right-evidence has exactly one writer whether or not a node
    // is selected. Z3 and Red-Hat are separate bodies with their own hint.
    function _renderInspectorIdlePane() {
      var tab = SHELL.ui.rightTab || "evidence";
      if (tab === "evidence") { renderEvidencePanel(null); return; }
      var body = (tab === "z3") ? z3ModeEl : redhatModeEl;
      if (!body) return;
      while (body.firstChild) body.removeChild(body.firstChild);
      body.appendChild(_idleHint(tab));
    }
    function _idleHint(tab) {
      var hint = document.createElement("p");
      hint.className = "empty-hint";
      hint.textContent = (tab === "z3")
        ? "No numbers checked yet"
        : "Run an audit on a paragraph";
      return hint;
    }
    function renderEvidencePanel(node, opts) {
      if (!evidenceBodyEl) return;
      while (evidenceBodyEl.firstChild) evidenceBodyEl.removeChild(evidenceBodyEl.firstChild);
      // §6: the ONE writer of #right-evidence, in the precedence the pane's
      // four states have: an explicitly opened drawer (the user named that
      // item) beats the paragraph panel; the panel carries a clicked span's
      // ledger tail; with nothing selected the pane is idle. The other writers
      // are builders this one calls — neither clears the pane any more.
      //
      // The spec's `drawer > panel > confidence > idle` line describes this
      // order, which is why it is expressed here rather than in the dispatch:
      // two of the four writers are reached from event handlers (a chip, a
      // confidence span), never from _applyRightView, so there was no
      // dispatcher-side precedence to reorder — the call sites that produced
      // the extra builds were the handlers themselves.
      if (opts && opts.drawer) { renderEvidenceDrawer(opts.drawer); return; }
      // Nothing selected: the pane's content is the counters above this body,
      // so the body stays empty rather than announcing an empty pane.
      if (!node) return;
      // The clicked span's ledger tail is appended wherever this function
      // exits: the score is a ledger signal, so it survives a paragraph whose
      // source did not match. `tail` runs at most once per call.
      var tail = (opts && opts.confidence) ? function () {
        renderConfidenceEvidence(opts.confidence);
      } : null;
      var prov = node.provenance;
      if (!Array.isArray(prov)) prov = (node.meta && node.meta.provenance);
      if (!Array.isArray(prov)) prov = prov ? [prov] : [];
      var p0 = prov[0] || null;
      var ent = _entailmentFor(node, p0);
      var header = document.createElement("div");
      header.className = "evidence-header";
      if (!p0 && !ent) {
        header.textContent = "Evidence · no source matched";
        evidenceBodyEl.appendChild(header);
        var empty = document.createElement("div");
        empty.className = "evidence-content";
        var emptyMsg = document.createElement("p");
        emptyMsg.className = "evidence-value";
        emptyMsg.textContent = "No source matched this paragraph";
        empty.appendChild(emptyMsg);
        evidenceBodyEl.appendChild(empty);
        if (tail) tail();
        return;
      }
      var srcName = String((p0 && p0.source_name) || "");
      var pageStr = (p0 && p0.page_number != null && p0.page_number !== "")
        ? String(p0.page_number) : "";
      // Label from the entailment verdict, not from the anchor's presence.
      header.textContent = _entailmentLabel(node, p0, pageStr);
      evidenceBodyEl.appendChild(header);
      var content = document.createElement("div");
      content.className = "evidence-content";
      // The check's one-sentence reason, directly under the label. Absent when
      // the check produced none — never filled in with a made-up justification.
      var reasoning = _entailmentReasoning(node, p0);
      if (reasoning) {
        var reasonEl = document.createElement("p");
        reasonEl.className = "evidence-value";
        reasonEl.textContent = reasoning;
        content.appendChild(reasonEl);
      }
      if (!p0) { evidenceBodyEl.appendChild(content); if (tail) tail(); return; }
      var excerpt = String(p0.excerpt || p0.extracted_quote || "");
      if (excerpt) {
        var quote = document.createElement("blockquote");
        quote.className = "evidence-blockquote";
        quote.textContent = excerpt;
        content.appendChild(quote);
      }
      function field(label, value) {
        var s = String(value == null ? "" : value);
        if (!s) return;
        var f = document.createElement("div");
        f.className = "evidence-field";
        var l = document.createElement("div"); l.className = "evidence-label"; l.textContent = label;
        var v = document.createElement("div"); v.className = "evidence-value"; v.textContent = s;
        f.appendChild(l); f.appendChild(v); content.appendChild(f);
      }
      field("Source", srcName);
      if (pageStr) field("Page", pageStr);
      field("Rule", p0.rule);
      field("Confidence", p0.confidence);
      evidenceBodyEl.appendChild(content);
      if (tail) tail();
    }
    function renderZ3Panel(node) {
      var el = z3ModeEl; if (!el) return;
      while (el.firstChild) el.removeChild(el.firstChild);
      var z3 = (node.annotations && node.annotations.z3) || [];
      var wrap = document.createElement("div"); wrap.className = "evidence-content";
      if (!z3.length) {
        var p = document.createElement("p"); p.className = "evidence-value";
        p.textContent = "No Z3 findings for this node."; wrap.appendChild(p);
        el.appendChild(wrap); return;
      }
      var list = document.createElement("ul");
      z3.forEach(function (z) {
        var li = document.createElement("li");
        var parts = [];
        if (z.status) parts.push(z.status);
        if (z.canonical_key) parts.push(z.canonical_key);
        if (z.message) parts.push(z.message);
        li.textContent = parts.join(" — ");
        list.appendChild(li);
      });
      wrap.appendChild(list); el.appendChild(wrap);
    }
    // Dead since the Red-Hat pane moved to __redhatLastRun: this file no longer
    // reads it (the finding list is node.annotations.redhat). Not removed in
    // this commit — see follow-ups.
    // ROUTED TO inputs for the last draft run (left COMPILER pane). Runtime
    // facts from the stream — reset at run start so the pane can never show a
    // previous run's model or Red-Hat pass state.
    var __lastRunModel = "";
    var __lastRunRedhat = "";
    function renderRedhatPanel(node) {
      var el = redhatModeEl; if (!el) return;
      while (el.firstChild) el.removeChild(el.firstChild);
      var wrap = document.createElement("div"); wrap.className = "evidence-content";

      // One run action per node, first element of the pane. Nothing else can
      // stand in for it: a compile never runs the audit.
      var runBtn = document.createElement("button");
      runBtn.type = "button";
      runBtn.className = "evidence-action primary redhat-run";
      var runningHere = Boolean(__redhatRunningNodeId) && Boolean(node) &&
        node.id === __redhatRunningNodeId;
      runBtn.textContent = runningHere
        ? "Running Red-Hat… (" + _redhatElapsedSeconds() + "s)"
        : "Run Red-Hat";
      // §5 row 11: the run's only visible state used to be its label; carry it
      // as a class plus aria-busy so assistive tech sees the run too.
      if (runningHere) {
        runBtn.classList.add("is-running");
        runBtn.setAttribute("aria-busy", "true");
      }
      // Any in-flight run locks every node's button (a second run is refused),
      // and an unauditable node — one with no text field — is disabled and told
      // why, immediately below. No enabled button ever bails silently.
      runBtn.disabled = Boolean(__redhatRunningNodeId) || !_redhatAuditable(node);
      if (__redhatRunningNodeId && !runningHere) {
        runBtn.title = "A Red-Hat run is in progress on another node.";
      }
      runBtn.addEventListener("click", function () {
        if (node && node.id) _runRedhatAudit(node.id);
      });
      wrap.appendChild(runBtn);
      if (node && node.id && !_redhatAuditable(node)) {
        var whyEl = document.createElement("p");
        whyEl.className = "evidence-value redhat-unavailable";
        whyEl.textContent = REDHAT_NOT_AUDITABLE;
        wrap.appendChild(whyEl);
      }

      // Source of truth: node.annotations.redhat — the finding list is the
      // node's own annotations, never a run's report about them.
      var findings = (node && node.annotations && node.annotations.redhat) || [];
      if (findings.length) {
        var list = document.createElement("ul");
        findings.forEach(function (r) {
          if (!r) return;
          var li = document.createElement("li");
          li.className = "redhat-finding";
          var text = _redhatFindingText(r);
          var parts = [];
          if (r.severity) parts.push(String(r.severity));
          if (text) parts.push(text);
          li.textContent = parts.join(" — ");
          // A finding is already an instruction: click seeds the existing
          // rephrase editor rather than opening a second rewrite path.
          if (text) {
            li.title = "Rephrase this paragraph with this finding";
            li.addEventListener("click", function () {
              if (!node || !node.id) return;
              setShell("ui.selection.nodeId", node.id);
              _attachNodeRephrase(node.id, text);
            });
          }
          list.appendChild(li);
        });
        wrap.appendChild(list);
      } else {
        var p0 = document.createElement("p"); p0.className = "evidence-value";
        p0.textContent = runningHere
          ? "Running Red-Hat audit…"
          : "No Red-Hat findings for this node.";
        wrap.appendChild(p0);
      }

      // The three failure modes stay distinct — a persist conflict, an error
      // frame, and a broken read are different problems with different fixes —
      // but an error belongs to the node it happened on: another node's pane
      // shows neither the message nor a stale copy of it.
      if (__redhatError && node && __redhatError.nodeId === node.id) {
        var errEl = document.createElement("p");
        errEl.className = "evidence-value redhat-error";
        errEl.textContent = __redhatError.message;
        wrap.appendChild(errEl);
      }

      // The status line describes the run for THIS node, and nothing else: a
      // compile's pass state cannot stand in (it is not a run of this trigger),
      // and a run on another node is not this node's run. Three truthful
      // states: running here, a finished run here, or no line at all.
      var lastRun = (__redhatLastRun && node && __redhatLastRun.nodeId === node.id)
        ? __redhatLastRun : null;
      var statusText = runningHere
        ? ("Running… " + _redhatElapsedSeconds() + " s")
        : (lastRun
          ? (lastRun.status === "done"
            ? (lastRun.count === 0
              ? ("Last run: no new findings, " + lastRun.seconds + "s")
              : ("Last run: " + lastRun.count + " finding" + (lastRun.count === 1 ? "" : "s") +
                 ", " + lastRun.seconds + "s"))
            : "Last run: failed")
          : "");
      if (statusText) {
        var statusEl = document.createElement("p");
        statusEl.className = "empty-hint redhat-status";
        statusEl.textContent = statusText;
        wrap.appendChild(statusEl);
      }
      el.appendChild(wrap);
    }
    function _applyRightView() {
      var insp = rightInspectorEl;
      var cmp = compareModeEl;
      if (inspectorCompareActive) {
        if (rightModeToggleEl) rightModeToggleEl.style.display = "none";
        if (insp) insp.style.display = "none";
        if (cmp) cmp.style.display = "block";
        if (compareToggleEl) compareToggleEl.classList.add("is-active");
        return;
      }
      if (rightModeToggleEl) rightModeToggleEl.style.display = "";
      if (compareToggleEl) compareToggleEl.classList.remove("is-active");
      if (insp) insp.style.display = "block";
      if (cmp) cmp.style.display = "none";
      var nodeId = SHELL.ui.selection ? SHELL.ui.selection.nodeId : null;
      if (!nodeId) {
        _setInspectorPane(SHELL.ui.rightTab || "evidence");
        _renderInspectorIdlePane();
        return;
      }
      var node = (SHELL.document.current) ? findJdfNodeById(nodeId, SHELL.document.current) : null;
      if (!node) {
        _setInspectorPane(SHELL.ui.rightTab || "evidence");
        _renderInspectorIdlePane();
        return;
      }
      // §6: the pane's third input rides along with the selection, so this
      // dispatcher renders the whole view in one pass — the drawer for a
      // z3/cite chip, the ledger tail for a confidence span. A payload that
      // belongs to another node paints nothing: it is stale by definition.
      var sel = SHELL.ui.selection || {};
      var ev = sel.evidence;
      var opts = (ev && ev.nodeId === node.id)
        ? (ev.kind === "confidence" ? { confidence: ev } : { drawer: ev })
        : null;
      renderEvidencePanel(node, opts);
      renderZ3Panel(node);
      renderRedhatPanel(node);
      _setInspectorPane(SHELL.ui.rightTab || "evidence");
    }
    function _toggleCompareView() {
      inspectorCompareActive = !inspectorCompareActive;
      if (inspectorCompareActive && !compareDataLoaded) runCompare();
      _applyRightView();
    }

    // §6: demoted — no longer a writer. renderEvidencePanel owns #right-evidence
    // and clears it; this builds the drawer body into the pane it was given,
    // which is why the "Ground with sources" action it appends now stays on
    // screen instead of being wiped by the dispatcher's repaint in the same
    // click.
    function renderEvidenceDrawer(ev) {
      if (!evidenceBodyEl) return;
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
      evidenceBodyEl.appendChild(content);
      renderEvidenceFooter(ev);
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
      // No Red-Hat branch: the pane owns the findings and their one wired
      // action (click a finding → prefilled rephrase). The drawer copy wrote
      // ui.selection.evidence, which the same click cleared in _applyRightView.
      evidenceBodyEl.appendChild(footer);
    }

    function performGrounding(nodeId) {
      ensureProjectId().then(function (projectId) {
        return jsonPost("/api/projects/" + projectId + "/nodes/" + nodeId + "/ground", { mode: "auto" });
      }).then(function (resp) {
        if (!resp.ok) throw new Error("Ground failed: " + resp.status);
        return resp.json();
      }).then(function (result) {
        alert("Grounding complete. Node updated with source citation.");
        setMode("compiler");
      }).catch(function (err) {
        alert("Grounding error: " + (err.message || err));
      });
    }

    // Revise with LLM / Dismiss were removed with the Red-Hat drawer route.
    // Revise's only terminal state was alert("Accept revision not fully
    // wired…"); the wired rewrite path is _attachNodeRephrase. Dismiss made
    // no request at all and mutated the drawer's copy of the finding, so it
    // implied a closable finding that nothing could close.

    // Send is only meaningful with a non-empty ask. The click/keydown paths
    // keep their own guards (submitIntent) as defense.
    function _syncDockSubmit() {
      if (!submit || !text) return;
      // §5 row 3: a draft in flight is the dock's loading state. The class
      // and aria-busy are the only surface runInProgress has ever had.
      submit.classList.toggle("is-running", Boolean(runInProgress));
      if (runInProgress) submit.setAttribute("aria-busy", "true");
      else               submit.removeAttribute("aria-busy");
      submit.disabled = !String(text.value || "").trim();
    }
    if (text) {
      text.addEventListener("input", _syncDockSubmit);
      text.addEventListener("keyup", _syncDockSubmit);
      text.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
          e.preventDefault();
          submitIntent();
        }
      });
    }
    _syncDockSubmit();
    if (submit) {
      submit.addEventListener("click", function () { submitIntent(); });
    }
  });
})();
