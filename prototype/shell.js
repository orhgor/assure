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
    // status{stage:"entailment"} (draft.py:1996) — the source-check pass that
    // runs between Math Check and the `verified` frame. Ignored until 2026-09-22,
    // so the line sat at Anchor while the longest step of the run was in flight.
    "Entailment",
    "Verify",
    "Complete",
  ];
  var STORAGE_KEY = "assure_project";

  // ---------------------------------------------------------------
  // The entry gate. POST /auth sets an HttpOnly `assure_shell_key`
  // cookie for this origin, so every same-origin call — fetch, <script>,
  // SSE — carries the key without JavaScript ever holding it. Nothing is
  // persisted client-side: a localStorage copy outlives the session and
  // any script on the page can read it. A 401 is not one thing: which door
  // refused it — and so where the reader is sent — is decided in
  // `bounceOnUnauthorized` below, never by the status alone.
  // ---------------------------------------------------------------
  (function dropLegacyAccessKey() {
    // Browsers that loaded the shell before the cookie-only gate still hold a
    // readable copy; drop it rather than keep using it.
    try { window.localStorage.removeItem("assure_shell_key"); } catch (_) {}
  })();

  // A same-origin 401 has three causes and they need three different answers:
  //
  //   the gate refused it   no/rotated `assure_shell_key`; only the key page can
  //                         help                                          -> /auth
  //   no session            the request reached the app, so the key is fine and
  //                         Clerk is the fix                             -> /signin
  //   a session, still 401  not about the session at all: there is nothing to
  //                         bounce to. Say so and leave the page alone.
  //
  // The third case is the loop. A shell that rendered fail-open fires a 401, the
  // old handler sent the reader to /auth, the key reloaded the shell, and the
  // same 401 fired again — Clerk never appeared.
  (function bounceOnUnauthorized() {
    var raw = window.fetch;
    if (typeof raw !== "function") return;

    // The gate answers a keyless /api/* call with 401 + this header
    // (dev-server.py:_deny). The app's own 401 is JSON only, so the header says
    // which of the two doors refused.
    var GATE_REALM = "assure-shell";

    function refusedByGate(resp) {
      try {
        var header = resp.headers.get("WWW-Authenticate");
        return Boolean(header) && header.indexOf(GATE_REALM) !== -1;
      } catch (_) { return false; }
    }

    function leaveFor(target) {
      try { window.location.replace(target); } catch (_) {}
    }

    // Not decoration: a 401 this code refuses to bounce on is a failure the
    // reader would otherwise watch repeat with no explanation.
    function surface(message) {
      try { console.error("[auth] " + message); } catch (_) {}
      try {
        var el = document.getElementById("shell-auth-error");
        if (!el) {
          el = document.createElement("div");
          el.id = "shell-auth-error";
          el.setAttribute("role", "alert");
          el.style.cssText =
            "position:fixed;left:0;right:0;top:0;z-index:2147483647;padding:10px 16px;" +
            "background:#7A1F1F;color:#FFFFFF;" +
            "font:13px/1.5 -apple-system,BlinkMacSystemFont,\"Inter\",\"Segoe UI\",sans-serif;";
          (document.body || document.documentElement).appendChild(el);
        }
        el.textContent = message;
      } catch (_) {}
    }

    // What the session endpoint says, in four values:
    //   "gate"    the question itself was refused — the key cookie is gone
    //   "none"    no session
    //   "session" a session is live, so a 401 elsewhere is not about the session
    //   "unknown" the question could not be asked
    // `/api/auth/me` is public (cloud_auth.py:PUBLIC_API), so under clerk-only a
    // visitor with no session gets 200 with an empty `user_id`, not a 401 — the
    // empty id is what reports "no session".
    function askSession() {
      var probe;
      try { probe = raw.call(window, "/api/auth/me", { cache: "no-store" }); }
      catch (_) { return Promise.resolve("unknown"); }
      return probe.then(function (resp) {
        if (resp && resp.status === 401) return refusedByGate(resp) ? "gate" : "none";
        if (!resp || !resp.ok) return "unknown";
        return resp.json().then(
          function (body) { return body && body.user_id ? "session" : "none"; },
          function () { return "unknown"; }
        );
      }, function () { return "unknown"; });
    }

    window.fetch = function (input, init) {
      var url = typeof input === "string" ? input : ((input && input.url) || "");
      var sameOrigin = url.charAt(0) === "/" || url.indexOf(window.location.origin) === 0;
      return raw.call(this, input, init).then(function (resp) {
        if (!resp || resp.status !== 401 || !sameOrigin) return resp;
        // The caller always gets its own response back: this wrapper decides
        // where the reader goes, never what their code sees.
        if (refusedByGate(resp)) { leaveFor("/auth"); return resp; }
        return askSession().then(function (state) {
          if (state === "none" || state === "gate") { leaveFor("/signin"); return resp; }
          surface(state === "session"
            ? "This request was refused (401) while your session is live, so it is not a sign-in problem. Retry; if it persists the app logs carry the reason."
            : "This request was refused (401) and the sign-in state could not be checked. Retry; if it persists the app logs carry the reason.");
          return resp;
        }, function () { return resp; });
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
  var _syncDocStateFn = null;
  var _syncDockSubmitFn = null;
  var inspectorCompareActive = false;

  // ---------------------------------------------------------------
  // Locale. Every string this shell shows is addressed by catalog key
  // (``prompt_matrix/i18n.py``) — the same keys and the same ``data-i18n``
  // convention the app UI applies — so a label the reader sees is one string in
  // every locale instead of English here and translated there. The catalog
  // arrives from ``GET /api/i18n`` (the session's locale, the same response the
  // app reads); until it does, the English in the markup renders, exactly as it
  // did before this existed. A string the JS composes — the counter line's
  // numbers — is assembled from catalog keys through ``_tf``; the English here
  // is the fallback for a catalog that cannot be fetched, never the text.
  // ---------------------------------------------------------------
  var SHELL_I18N = { locale: "en", strings: {} };

  function _t(key, fallback) {
    var strings = SHELL_I18N.strings || {};
    if (Object.prototype.hasOwnProperty.call(strings, key)) {
      var val = strings[key];
      if (val !== "" && val != null) return String(val);
    }
    return (fallback !== undefined && fallback !== "") ? fallback : key;
  }
  function _tf(key, fallback, vars) {
    var text = _t(key, fallback);
    Object.keys(vars || {}).forEach(function (name) {
      text = text.split("{" + name + "}").join(String(vars[name]));
    });
    return text;
  }
  // The markup's own strings, in the same three shapes the app applies
  // (templates/index.html:applyI18n). An element with children is left alone:
  // its text is its markup.
  function _applyI18n(root) {
    var scope = root || document;
    scope.querySelectorAll("[data-i18n]").forEach(function (el) {
      if (el.children.length) return;
      el.textContent = _t(el.getAttribute("data-i18n"), el.textContent);
    });
    scope.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
      el.setAttribute("placeholder",
        _t(el.getAttribute("data-i18n-placeholder"), el.getAttribute("placeholder") || ""));
    });
    scope.querySelectorAll("[data-i18n-aria]").forEach(function (el) {
      el.setAttribute("aria-label",
        _t(el.getAttribute("data-i18n-aria"), el.getAttribute("aria-label") || ""));
    });
  }
  function _loadI18n() {
    return window.fetch("/api/i18n", { cache: "no-store" })
      .then(function (resp) { return resp.ok ? resp.json() : null; })
      .then(function (payload) {
        if (!payload || typeof payload !== "object") return;
        SHELL_I18N.locale = String(payload.locale || "en");
        SHELL_I18N.strings = payload.strings || {};
        document.documentElement.lang = SHELL_I18N.locale;
        _applyI18n();
        // The counter line is composed from keys when it renders, so it is
        // painted again once the catalog is in hand.
        if (_syncCountersFn) _syncCountersFn();
      })
      .catch(function () {});
  }

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
      // The column's state cards follow the mode: a document clears the
      // empty-project card, and a verdict is never overwritten by it.
      if (_syncDocStateFn) _syncDocStateFn();
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
      // The first source is what the empty-project card was waiting for, and
      // the dock's Submit follows the same list.
      if (_syncDocStateFn) _syncDocStateFn();
      if (_syncDockSubmitFn) _syncDockSubmitFn();
    } else if (path === "project.id") {
      try { window.localStorage.setItem(STORAGE_KEY, value); } catch (_) {}
    } else if (path === "project.title") {
      if (projectCurrentNameEl) projectCurrentNameEl.textContent = value || "workspace";
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
      // drawer. The click handlers write it BEFORE the node id, so it belongs
      // to a node that is not selected yet and repaints nothing; the node id
      // write then paints once, with the payload already in place. Re-clicking
      // the item that is already selected changes only this input, so this
      // write is what repaints. A confidence span writes null here: it opens
      // the paragraph's own panel, and nothing else.
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
      // Persistence is the user's choice, not the renderer's: writing here would
      // stamp a preference on the first paint of every window, and the narrow
      // first paint below could then never be chosen again.
    } else if (path === "ui.layout.rightCollapsed") {
      if (value) docBodyEl.classList.add("right-hidden");
      else       docBodyEl.classList.remove("right-hidden");
      _setRightPaneHidden(Boolean(value));   // §3: inert/aria-hidden + tabindex fallback
    } else if (path === "ui.modal") {
      var layer = document.getElementById("modal-layer");
      if (!layer) return;
      if (!value) {
        _closeAboutModal(layer);   // hands focus back to the mark that opened it
        layer.hidden = true;
        layer.innerHTML = "";
        return;
      }
      layer.hidden = false;
      if (value === "about") {
        _openAboutModal(layer);
      } else if (value === "shortcuts") {
        // Another dialog is taking the layer over: release the About trap, but
        // do not pull focus to a mark that is no longer why the layer is up.
        _closeAboutModal(layer, false);
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

  // ---- About dialog --------------------------------------------------
  // The mark in the header opens the one dialog in the shell that is read
  // rather than acted on, so it owns its dismissal: Esc from inside it, or a
  // click on the scrim outside it — there is no corner close button to hunt
  // for. Focus moves in on open, cycles inside while it is up, and returns to
  // the mark on close; a dialog that traps focus and then drops it leaves a
  // keyboard user at the top of the document.
  //
  // The markup is <template id="about-template"> in index.html: the copy sits
  // with the rest of the shell's text instead of inside a JS string.
  var aboutTriggerEl = null;

  function _aboutDialog() {
    return document.querySelector("#modal-layer .about-modal");
  }

  function _aboutTabStops(dialog) {
    var els = dialog.querySelectorAll(
      "a[href], button:not([disabled]), input:not([disabled]), select, textarea"
    );
    var out = [];
    for (var i = 0; i < els.length; i++) {
      if (els[i].getAttribute("tabindex") === "-1") continue;
      out.push(els[i]);
    }
    return out;
  }

  // Bound to the dialog rather than to document. The shell's global shortcuts
  // live on document, so stopping propagation at the dialog keeps them from
  // firing behind an open dialog without unregistering anything.
  function _aboutKeydown(e) {
    e.stopPropagation();
    if (e.key === "Escape") {
      e.preventDefault();
      setShell("ui.modal", null);
      return;
    }
    if (e.key !== "Tab") return;
    var dialog = _aboutDialog();
    if (!dialog) return;
    var stops = _aboutTabStops(dialog);
    if (!stops.length) { e.preventDefault(); return; }
    var first = stops[0];
    var last = stops[stops.length - 1];
    var active = document.activeElement;
    if (active === dialog || !dialog.contains(active)) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
    } else if (e.shiftKey && active === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  function _aboutLayerClick(e) {
    var layer = document.getElementById("modal-layer");
    if (e.target === layer) setShell("ui.modal", null);
  }

  function _openAboutModal(layer) {
    var tpl = document.getElementById("about-template");
    if (!tpl || !tpl.content) return;
    layer.innerHTML = "";
    layer.appendChild(tpl.content.cloneNode(true));
    var dialog = _aboutDialog();
    if (!dialog) return;
    // The opener is the mark itself: it is the only thing that opens this
    // dialog, and on engines that do not focus a button on click the active
    // element would otherwise be <body>.
    aboutTriggerEl = document.getElementById("about-btn");
    dialog.setAttribute("tabindex", "-1");
    dialog.focus();
    dialog.addEventListener("keydown", _aboutKeydown);
    layer.addEventListener("click", _aboutLayerClick);
  }

  function _closeAboutModal(layer, restoreFocus) {
    var trigger = aboutTriggerEl;
    aboutTriggerEl = null;
    if (!trigger || restoreFocus === false) return;
    if (typeof trigger.focus === "function") trigger.focus();
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

  // A4 — the compile state the AI view reads, derived rather than
  // remembered. Two facts make it: which model the last dispatch ran on, and
  // whether the document on screen came out of a compile. The model is a fact
  // of this session — the draft stream's own frame writes `__lastRunModel` —
  // and the compile is a fact of the document, read from the same revision
  // list the version chip navigates.
  //
  // After a reload the model is read back from the compile the project
  // stored: GET /api/projects/<id>/files serves it as
  // manifest.lastCompiledRoute (db/project_files.py, off
  // last_compiled_json.gate.measure), and _hydrateDocument below writes it
  // into __lastRunModel. COMPILE_MODEL_UNKNOWN is what the row reads only for
  // a compile that predates the gate block or a manifest that failed to load.
  var NO_COMPILE = "No compile for this document";
  var COMPILE_MODEL_UNKNOWN = "Compiled \u00b7 model not carried by the document";

  // The compile this document descends from: a revision that ran the
  // pipeline, at or before the version on screen. A rewrite or a restore
  // after it does not un-compile the document.
  function _compiledVersionOnScreen() {
    var versions = SHELL.document.versions || {};
    var list = versions.list || [];
    var current = versions.current;
    for (var i = 0; i < list.length; i++) {
      var v = list[i];
      if (!v || String(v.mutation_type || "") !== "compile") continue;
      if (current == null || (v.version || 0) <= current) return true;
    }
    return false;
  }

  // A4: what ROUTED TO reads when it has no model to name.
  function _compilerRouteEmptyCopy() {
    return _compiledVersionOnScreen() ? COMPILE_MODEL_UNKNOWN : NO_COMPILE;
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
    // Every state says what it is. "Awaiting route" described nothing the
    // reader could act on, and beside an open document it read as though the
    // compile had never run: the section names the compile instead — the model
    // when this session knows it, and the absence of a compile when there is
    // none for this document (A4).
    var routeEl = document.getElementById("compiler-route");
    if (routeEl && !SHELL.compiler.route) routeEl.textContent = _compilerRouteEmptyCopy();
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
    // The shell's strings are catalog keys; load the visitor's locale once and
    // apply it (and repaint the one line the JS composes).
    _loadI18n();
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
    // Poll GET /api/tasks/<id> until the worker reports a terminal state.
    // Ingest is queued since 2026-09-22 (PARSE_ASYNC / SUBSTRATE_ASYNC_UPLOAD):
    // the upload route answers 202 {task_id, job_id} and the vault row exists
    // only once the parse worker has run. Resolves with the task body; the
    // ingest_jobs row (body.job) beats the expiring Celery result when the two
    // disagree, which is what the server already does for `status`.
    function _awaitTask(taskId, onTick) {
      var started = Date.now();
      var limitMs = 15 * 60 * 1000; // scanned PDFs OCR at ~3 s/page
      var ticks = 0;
      // 1 s for the first ten polls (text PDFs finish in ~1 s), then 2 s, then
      // 5 s: a scan OCRs for minutes and each poll is a DB read on the server.
      function delay() { ticks += 1; return ticks <= 10 ? 1000 : ticks <= 40 ? 2000 : 5000; }
      return new Promise(function (resolve, reject) {
        function tick() {
          fetch("/api/tasks/" + encodeURIComponent(taskId), { headers: { Accept: "application/json" } })
            .then(function (r) { return r.json().catch(function () { return {}; }); })
            .then(function (body) {
              var st = String((body && body.status) || "pending").toLowerCase();
              if (typeof onTick === "function") { try { onTick(body); } catch (_) {} }
              if (st === "success") return resolve(body);
              if (st === "failure" || st === "skipped") {
                var job = body.job || {};
                var res = body.result || {};
                return reject(new Error(job.error || body.error || res.error || res.reason || ("ingest " + st)));
              }
              if (Date.now() - started > limitMs) return reject(new Error("Still processing after 15 minutes; check the Processing panel."));
              setTimeout(tick, delay());
            })
            .catch(function (err) {
              if (Date.now() - started > limitMs) return reject(err);
              setTimeout(tick, Math.max(2000, delay()));
            });
        }
        tick();
      });
    }
    function _pendingSourceRow(name) {
      var el = document.getElementById("source-list");
      if (!el) return null;
      var row = document.createElement("div");
      row.className = "source-item is-pending";
      row.textContent = name + " \u2014 processing\u2026";
      el.appendChild(row);
      return row;
    }
    function handleSourceFile(file) {
      if (!file) return;
      var name = file.name || "source.txt";
      if (!/\.(pdf|txt|md|csv|json)$/i.test(name)) {
        sourceUploadError(
          "Only .pdf, .txt, .md, .csv and .json are accepted here. DOCX is not wired."
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
          if (!r.ok || r.j.ok === false) {
            throw new Error(r.j.error || ("HTTP " + r.status));
          }
          if (r.status === 202 && r.j.task_id) {
            // Queued: the row lands when the worker finishes. Show the file as
            // processing meanwhile and resolve to the vault entry it produced.
            var pending = _pendingSourceRow(name);
            return _awaitTask(r.j.task_id, function (body) {
              var stage = body && body.job && body.job.stage;
              if (pending && stage) pending.textContent = name + " \u2014 " + stage + "\u2026";
            }).then(function (body) {
              if (pending && pending.parentNode) pending.parentNode.removeChild(pending);
              var res = (body && body.result) || {};
              var entry = res.entry || {};
              var job = (body && body.job) || {};
              return { id: entry.id || job.substrate_file_id, entry: entry };
            }, function (err) {
              // The row stays where the file was listed and turns into the
              // failure, so the reader sees which upload failed and why in one
              // place rather than a vanished row and a bare message below.
              var msg = String(err && err.message ? err.message : err);
              if (pending && pending.parentNode) {
                pending.className = "source-item is-error";
                pending.textContent = name + " \u2014 " + msg;
                err = new Error(msg);
                err.__shownInRow = true;
              }
              throw err;
            });
          }
          return r.j;
        })
        .then(function (j) {
          if (!j || !j.id) throw new Error("No file id returned.");
          var newId = String(j.id);
          setShell("sources", SHELL.sources.concat([newId]));
          appendSourceItem(name, newId);
          // The task is terminal before the vault row is always visible to the
          // manifest read (the worker commits after it reports); one re-read a
          // second later closes that window without polling.
          _refreshManifest().then(function () {
            var listed = (manifestRows || []).some(function (f) {
              return String(f && f.id) === newId;
            });
            if (!listed) setTimeout(_refreshManifest, 1000);
          });
        })
        .catch(function (err) {
          if (err && err.__shownInRow) {
            try { console.error("[shell] source upload:", err.message); } catch (_) {}
            return;
          }
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
    // The dock panel is the shell's only notice surface: rows are direct-child
    // divs (the contract #dock-search-results is styled against).
    function _jdfPanel() {
      return document.getElementById("dock-search-results");
    }
    function _jdfHideIfEmpty() {
      var panel = _jdfPanel();
      if (panel && !panel.children.length) panel.hidden = true;
    }
    function _jdfRow(text, isErr) {
      var panel = _jdfPanel();
      if (!panel) return null;
      panel.hidden = false;
      var row = document.createElement("div");
      row.className = "dock-toast" + (isErr ? " is-error" : "");
      var label = document.createElement("span");
      label.className = "dock-toast-text";
      label.textContent = text;
      row.appendChild(label);
      var dismiss = document.createElement("button");
      dismiss.type = "button";
      dismiss.className = "dock-toast-dismiss";
      dismiss.setAttribute("aria-label", "Dismiss");
      dismiss.textContent = "\u00d7";
      dismiss.addEventListener("click", function () {
        _jdfClearProgress();
        if (row.parentNode) row.parentNode.removeChild(row);
        _jdfHideIfEmpty();
      });
      row.appendChild(dismiss);
      panel.appendChild(row);
      return row;
    }
    function jdfMessage(text, isErr) {
      _jdfRow(text, isErr);
    }
    // The ingest's progress line is a state, not a log entry. It used to be
    // appended like a result, so "Converting → Chunking → Indexing…" sat above
    // the outcome for the rest of the session. It is now the one row the
    // terminal frame replaces: cleared when the ingest answers, cleared by a
    // 30s backstop if the answer never comes, and dismissible by hand.
    var jdfProgressTimer = null;
    function _jdfClearProgress() {
      if (jdfProgressTimer) { clearTimeout(jdfProgressTimer); jdfProgressTimer = null; }
      var panel = _jdfPanel();
      if (!panel) return;
      var rows = panel.querySelectorAll(".is-progress");
      for (var i = 0; i < rows.length; i++) {
        if (rows[i].parentNode) rows[i].parentNode.removeChild(rows[i]);
      }
      _jdfHideIfEmpty();
    }
    function jdfProgress(text) {
      _jdfClearProgress();
      var row = _jdfRow(text, false);
      if (!row) return;
      row.classList.add("is-progress");
      jdfProgressTimer = setTimeout(function () {
        jdfProgressTimer = null;
        _jdfClearProgress();
      }, 30000);
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
        jdfProgress("Converting \u2192 Chunking \u2192 Indexing\u2026");
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
            // The ingest answered: the progress line is over, whatever it says.
            _jdfClearProgress();
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
            _jdfClearProgress();
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
    // The pane state is written to localStorage here — at the controls the user
    // pressed — and nowhere else. setShell only paints.
    function _persistPaneState() {
      try {
        localStorage.setItem("assure.left_collapsed", SHELL.ui.layout.leftCollapsed ? "1" : "0");
        localStorage.setItem("assure.right_collapsed", SHELL.ui.layout.rightCollapsed ? "1" : "0");
      } catch (_) {}
    }
    if (leftCollapse) {
      leftCollapse.addEventListener("click", function () {
        setShell("ui.layout.leftCollapsed", !SHELL.ui.layout.leftCollapsed);
        _persistPaneState();
      });
    }
    var rightClose = document.getElementById("right-close");
    function closeRight() { setShell("ui.layout.rightCollapsed", true); _persistPaneState(); }
    if (rightClose) rightClose.addEventListener("click", closeRight);
    function openLeft() { setShell("ui.layout.leftCollapsed", false); _persistPaneState(); }
    // D1: below 900px the pane is an overlay and its own header (which carries
    // the collapse button) travels with it, so the rail is the only control that
    // can be reached while the pane is shut. These are the openers.
    var leftOpenBtn = document.getElementById("left-open");
    if (leftOpenBtn) {
      leftOpenBtn.addEventListener("click", function () {
        setShell("ui.layout.leftCollapsed", !SHELL.ui.layout.leftCollapsed);
        _persistPaneState();
      });
    }
    var rightOpenBtn = document.getElementById("right-open");
    if (rightOpenBtn) {
      rightOpenBtn.addEventListener("click", function () {
        if (SHELL.ui.layout.rightCollapsed) openRight();
        else closeRight();
      });
    }

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
        _persistPaneState();
      } else if (k === "." || k === ">") {
        e.preventDefault();
        var bothCollapsed =
          SHELL.ui.layout.leftCollapsed &&
          SHELL.ui.layout.rightCollapsed;
        var target = !bothCollapsed;
        setShell("ui.layout.leftCollapsed",  target);
        setShell("ui.layout.rightCollapsed", target);
        _persistPaneState();
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

    // The About mark opens the About dialog. The `?` key keeps opening the
    // shortcut list it has always opened; the two are separate affordances
    // and the dialog that opens is the one the user asked for.
    var aboutBtn = document.getElementById("about-btn");
    if (aboutBtn) {
      aboutBtn.addEventListener("click", function () { setShell("ui.modal", "about"); });
    }

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
    // Anchor, Verify — while the stream still reports its eight finer steps.
    // The finer steps remain the state (STAGE_ORDER above is their order);
    // the four rows are the view, and they are what the header's progress
    // line reads. A stage ticks when every step under it has landed.
    // ---------------------------------------------------------------
    var STAGE_GROUPS = [
      { name: "Retrieve", steps: ["Preparing"] },
      { name: "Draft",    steps: ["Drafting", "Lock Inference"] },
      { name: "Anchor",   steps: ["Compile", "Math Check"] },
      { name: "Verify",   steps: ["Entailment", "Verify", "Complete"] },
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
    // A4 — a document that came out of a compile has run all four stages. The
    // rows are that compile's own record, so they are ticked from the
    // document's revisions rather than left grey beside a compiled document.
    function markAllStagesDone() {
      for (var i = 0; i < STAGE_GROUPS.length; i++) {
        for (var j = 0; j < STAGE_GROUPS[i].steps.length; j++) {
          stageState[STAGE_GROUPS[i].steps[j]] = "done";
        }
      }
      _renderStages();
    }
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
      // The in-flight state, as an element rather than as a CSS `::before`: a
      // generated string cannot carry `data-i18n`, so the one mark that says the
      // column is working was the one string on the surface that stayed English
      // in every locale. It is the first child, and `renderJdfDocument` clears
      // the column when the document lands, so the marker goes with the draft.
      var marker = document.createElement("p");
      marker.className = "doc-stream-marker";
      marker.setAttribute("data-i18n", "doc.state.compiling");
      marker.textContent = _t("doc.state.compiling", "Compiling\u2026");
      draftEl.appendChild(marker);
      docSurface.appendChild(draftEl);
      return draftEl;
    }
    function appendDraftText(delta) {
      var el = ensureDraftArea();
      if (!el) return;
      var text = el.querySelector(".doc-draft-text");
      if (!text) {
        text = document.createElement("div");
        text.className = "doc-draft-text";
        el.appendChild(text);
      }
      text.textContent += delta;
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
    // draft the server would not keep. Replace it with one card, in the frame
    // the refusal arrives in — the swap is a single DOM change, so nothing of
    // the discarded draft is ever painted next to the card.
    //
    // The card is not an error frame: it is the verdict, and it says what the
    // refusal means for the document ("Nothing was saved"). The server's own
    // message stays on the card's title and in the console, so the reason is
    // still readable without a second copy of the same sentence on screen.
    //
    // One column, one card, one renderer. The column holds no document in three
    // states — refused, halted, and empty — and _renderStateCard draws all three
    // so a second card mechanism cannot appear beside the first. A stream in
    // flight is the fourth: mid-flight text is not output, so the surface carries
    // data-mode="streaming" while the tokens arrive.
    //
    // Each card states two things, and the order is the point: what was written
    // (nothing), then what happened. A refusal is the one state where the reader
    // must not be left wondering whether half a document is sitting in their
    // project, so "Nothing was saved." is the card's first line in every card of
    // this family, in ink and at reading weight; the sentence that explains it
    // follows in the muted body type. The server's own message stays on the
    // card's title and in the console, so the reason is still readable without a
    // second copy of the same sentence on screen.
    var REFUSAL_LEAD = "Nothing was saved.";
    var REFUSAL_DETAIL = "This document could not be grounded in the source. " +
                         "The project's stored document is unchanged.";
    var HALT_LEAD = "Nothing was saved.";
    var HALT_DETAIL = "This compile stopped before the document was verified. " +
                      "The project's stored document is unchanged.";
    var PREREQ_LEAD = "Add a source to compile.";
    var PREREQ_DETAIL = "Assure grounds every claim against the source you provide.";
    // The source list is fetched on load and after a project switch; until that
    // answer lands, an empty SHELL.sources means "not known yet", not "none".
    var _sourcesLoaded = false;

    function _hasAttachedSource() {
      return Boolean((SHELL.sources || []).length);
    }
    function _clearStateCards() {
      if (!docSurface) return;
      var stale = docSurface.querySelectorAll(".doc-refusal, .doc-halt, .doc-prereq");
      for (var i = 0; i < stale.length; i++) stale[i].remove();
    }
    // The streamed draft goes first, always: whatever the column is about to
    // say, it can never say it beside half a document.
    function _renderStateCard(cls, leadKey, leadText, detailKey, detailText, message, mode) {
      if (draftEl && draftEl.parentNode) draftEl.parentNode.removeChild(draftEl);
      draftEl = null;
      if (!docSurface) {
        setShell("document.mode", "empty");
        return;
      }
      _clearStateCards();
      var card = document.createElement("div");
      card.className = cls;
      card.setAttribute("role", "status");
      if (message) card.title = String(message);
      var line = document.createElement("p");
      line.className = "doc-state-line";
      line.setAttribute("data-i18n", leadKey);
      line.textContent = _t(leadKey, leadText);
      card.appendChild(line);
      if (detailText) {
        var detail = document.createElement("p");
        detail.className = "doc-state-detail";
        detail.setAttribute("data-i18n", detailKey);
        detail.textContent = _t(detailKey, detailText);
        card.appendChild(detail);
      }
      docSurface.appendChild(card);
      setShell("document.mode", mode);
    }
    function _showRefusalCard(message) {
      _renderStateCard("doc-refusal", "doc.refusal.lead", REFUSAL_LEAD,
                       "doc.refusal.detail", REFUSAL_DETAIL, message, "refused");
    }
    // A compile that stopped without a verdict: the stream ended, or failed,
    // before the server said the run was over. The streamed text goes with it —
    // a client must never be left reading half a document that no refusal and no
    // `verified` frame ever claimed.
    function _showHaltCard(message) {
      _renderStateCard("doc-halt", "doc.halt.lead", HALT_LEAD,
                       "doc.halt.detail", HALT_DETAIL, message, "failed");
    }
    // The empty project: there is nothing to compile from, so the column names
    // the missing source instead of sitting blank behind a button that refuses
    // without a reason. It stands only while no document does — a document whose
    // source was removed afterwards keeps its column.
    function _syncDocState() {
      var mode = SHELL.document.mode;
      if (docSurface) docSurface.setAttribute("data-mode", mode);
      if (mode === "refused" || mode === "failed") return;
      if (mode !== "empty" || !_sourcesLoaded || _hasAttachedSource()) {
        _clearStateCards();
        return;
      }
      if (docSurface && docSurface.querySelector(".doc-prereq")) return;
      _renderStateCard("doc-prereq", "doc.prereq.lead", PREREQ_LEAD,
                       "doc.prereq.detail", PREREQ_DETAIL, "", "empty");
    }
    _syncDocStateFn = _syncDocState;
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
      var existing = docSurface ? docSurface.querySelectorAll(".doc-error, .doc-refusal, .doc-halt") : [];
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
          // A4: the revision list is what says whether the document on screen
          // came out of a compile, so the AI view is re-read when it arrives.
          _refreshCompilerState();
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
      // The audit map is part of the document, not of the pane: a paragraph
      // carrying a finding keeps its `--contradicted` left rule at rest, so the
      // audit is visible without opening anything. renderJdfNode is the only
      // place a wrapper is built, so every path — first paint, a re-render, a
      // version jump — takes the class from here.
      //
      // A finding a revision has answered keeps a mark, not the alarm: the
      // paragraph is still where the warning was, so it stays findable, but
      // `--contradicted` says the claim is wrong now, and a remediated one is
      // exactly the paragraph that is not (models/jdf.py:
      // resolve_findings_on_rewrite writes the resolution). Two classes because
      // the two states say different things.
      if (_nodeHasFindings(node)) wrapper.classList.add(_nodeHasOpenFindings(node)
        ? "has-finding"
        : "has-remediated-finding");
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
        // A1 — the model writes markdown, and the document shows it as such:
        // the paragraphs go through the same renderer the Red-Hat findings do
        // (`_renderFindingMarkdown`), so `**weight**`, `*emphasis*`, backticks
        // and `-`/`1.` lists arrive as elements instead of as their markers.
        // A div, not a p: a paragraph can be a run of blocks. The confidence
        // spans are wrapped on top of this by applyConfidenceSpans, which is
        // why the renderer takes a channel — it knows the source offsets the
        // spans were measured in (see _mdEmit).
        el = document.createElement("div");
        el.className = "jdf-p";
        el.appendChild(_renderFindingMarkdown(node.content || ""));
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
      // "no" is the check saying the sentence does not carry the claim — a
      // finding, not a missing check. The label used to read "Not verified",
      // which is what a reader would take for "nobody looked".
      no: "Not carried by the source",
      unverified: "Source check failed",
    };
    var _ENTAILMENT_KEYS = {
      yes: "entailment.yes",
      partial: "entailment.partial",
      no: "entailment.no",
      unverified: "entailment.unverified",
    };
    // Verdict -> the document's own state vocabulary, so a per-citation verdict
    // and the paragraph's margin mark are the same five states with the same
    // glyphs and the same words.
    var _ENTAILMENT_STATE = {
      yes: "supported",
      partial: "partial",
      no: "unsupported",
      unverified: "anchored",
    };
    function _entailmentLabelOf(verdict) {
      var key = _ENTAILMENT_KEYS[verdict];
      return key ? _t(key, _ENTAILMENT_LABELS[verdict]) : "";
    }
    // The check's per-citation record, aligned to the provenance rows it was
    // written for. `meta.provenance.entailment.citations[k]` is the verdict on
    // the sentence row `k` cites — the entailment pass walks the same rows in the
    // same order — and the two are matched by the sentence text itself, so a
    // verdict can never be shown against a sentence it was not written about. A
    // row with no record gets `null`, never a default: the check may have read
    // fewer rows than the paragraph carries, and that gap is reported as a gap.
    function _citationVerdicts(node, prov) {
      var out = [];
      var ent = _entailmentFor(node, (prov && prov[0]) || null);
      var recs = (ent && Array.isArray(ent.citations)) ? ent.citations : [];
      for (var i = 0; i < (prov || []).length; i++) {
        var rec = recs[i];
        if (!rec || typeof rec !== "object") { out.push(null); continue; }
        var rowText = String((prov[i] && (prov[i].extracted_quote || prov[i].excerpt)) || "");
        var recText = String(rec.source || "");
        if (recText && rowText && recText !== rowText) { out.push(null); continue; }
        out.push(rec);
      }
      return out;
    }
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
      if (!_entailmentFor(node, provItem)) {
        // No verdict was ever recorded: a fetched anchor before its check,
        // or a tree from before the entailment pass. "Source check failed"
        // would claim a check that never ran.
        return _t("entailment.unrecorded", "Anchored \u00b7 not yet verified") +
          (pageStr ? " \u00b7 " + _tf("evidence.page", "page {page}", { page: pageStr }) : "");
      }
      return _entailmentLabelOf(_entailmentVerdict(node, provItem)) +
        (pageStr ? " \u00b7 " + _tf("evidence.page", "page {page}", { page: pageStr }) : "");
    }
    // The page a provenance row names. ``page_number`` is the field the JDF model
    // and the served tree carry; the compile's citation rows are stamped with
    // ``page`` (the name the substrate rows use) and models.jdf folds one into the
    // other, so a row on this side may carry either — the live `verified` frame
    // streams the pre-persist tree, which still says ``page``. A row with neither
    // renders no page rather than a "0".
    function _rowPage(row) {
      if (!row || typeof row !== "object") return "";
      var p = row.page_number;
      if (p == null || p === "") p = row.page;
      if (p == null || p === "") return "";
      return String(p);
    }
    // The counters the gate reports, derived from the same tree the shell
    // renders, on the server's own rule (services/audit_summary._provenance_counts):
    // `anchored` is the TOTAL of the eligible paragraphs that cite a source
    // sentence, with the verdict buckets beside it. `supported` is the grounding
    // number — the source carries the claim, wholly or in part — so `partial` is
    // a bucket INSIDE it, not a sibling; counting only "yes" reported a memo whose
    // every paragraph its sources carry in part as supported 0. `unsupported` is
    // the source denying the claim, `unverified` a check that could not be made,
    // and a paragraph never checked is neither (the server's unreported
    // `unchecked`). The four displayed values are therefore a breakdown, not a
    // partition — see `_renderCounters`.
    function _derivedCounts(doc) {
      var counts = {
        eligible: 0, anchored: 0, supported: 0, partial: 0,
        unanchored: 0, unsupported: 0, unverified: 0,
      };
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
          counts.eligible++;
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
          if (isAnchored) counts.anchored++;
          else counts.unanchored++;
          // The verdict is read for every eligible paragraph, anchored or not:
          // the server counts it that way, and the verdict lives at
          // node.meta.provenance.entailment, which a payload carrying no anchor
          // row still carries. Read raw rather than through `_entailmentVerdict`,
          // whose label fallback turns "never checked" into "unverified" — that
          // is the server's unreported `unchecked`, not a failed check.
          var ent = _entailmentFor(node, prov[0] || null);
          var verdict = ent ? String(ent.verdict || "").toLowerCase() : "";
          var contradicted = !!(ent && ent.contradicted);
          if (verdict === "yes" || verdict === "partial") counts.supported++;
          if (verdict === "partial") counts.partial++;
          // The server counts a paragraph unsupported when the verdict is "no" OR
          // when any citation of it was contradicted (audit_summary
          // _is_contradicted), because a paragraph can be carried in part AND
          // contain a contradiction. This mirror counted only the verdict, so the
          // same document showed a lower "not supported" number once derived in
          // the browser - and the derived path is what a first paint, a cold shell
          // or a version jump takes. The number that went missing is the one the
          // page promises.
          if (verdict === "no" || contradicted) counts.unsupported++;
          else if (verdict === "unverified") counts.unverified++;
        }
      }
      return counts;
    }
    // Claims the entailment check did not support (the verdict "no" — a source
    // that denies the claim), from whichever source is authoritative: the
    // `verified` frame's persisted provenance_stats (DB parity with the gate block
    // the honesty test reads) when it carries the number, the rendered tree
    // otherwise. Same rule, same count.
    function _contradictedClaims(stats, doc) {
      if (stats && typeof stats === "object" && typeof stats.unsupported === "number") {
        return stats.unsupported;
      }
      return _derivedCounts(doc || SHELL.document.current || null).unsupported;
    }
    // The four counters, read from whichever source is authoritative: the
    // server's persisted provenance_stats (DB parity with the gate) when present,
    // the rendered tree otherwise. Both carry the anchor as a TOTAL with its
    // verdict buckets beside it — `derived.anchored` is that same total
    // (`_derivedCounts`) — so the tiles are a breakdown of the document, not a
    // partition: `partial` sits inside `supported`.
    //
    // `unsupported` is the one bucket that is a finding rather than a state: the
    // source check read the cited sentence against the claim and did not find the
    // claim there. It is reported whether or not a stats payload names it, from
    // the same tree the tiles' other three numbers come from, because a
    // paragraph the source denies must not go missing from the summary that
    // reports the document's verification state.
    function _counterBuckets(stats, derived) {
      var eligible = _num(stats, derived, "eligible");
      var supported = _num(stats, derived, "supported");
      var partial = _num(stats, derived, "partial");
      var unsupported = _num(stats, derived, "unsupported");
      var anchored = (stats && typeof stats.anchored === "number")
        ? stats.anchored
        : (derived ? derived.anchored : 0);
      // Unanchored is what the anchor did not reach. Both sources report it
      // directly; the subtraction is only for a payload that carries neither.
      var reportsUnanchored =
        (stats && typeof stats.unanchored === "number") ||
        (derived && typeof derived.unanchored === "number");
      var unanchored = reportsUnanchored
        ? _num(stats, derived, "unanchored")
        : Math.max(0, eligible - anchored);
      return {
        eligible: eligible,
        anchored: anchored,
        supported: supported,
        partial: partial,
        unsupported: unsupported,
        unanchored: unanchored,
      };
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
        // `derived.anchored` is the same total the server sends (see
        // `_derivedCounts`), so the note speaks about it directly.
        anchored = derived.anchored;
        supported = derived.supported;
        eligible = derived.eligible || anchored;
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
    // ---------------------------------------------------------------
    // 2C.6 — where the source a paragraph cites came from. The counters above
    // partition the eligible paragraphs by the document's own state (anchored /
    // supported / partial / unanchored); this row partitions the same paragraphs
    // by ORIGIN, which is the one thing a fetched page changes: uploaded file or
    // fetched page. Two numbers can therefore carry the word "anchored" on one
    // screen — this row's are the ones with a parenthetical, and it is labelled
    // for the question it answers.
    //
    // The source list is what defines "fetched" (the /substrate row's fetched_url
    // tag), so until that list has been read the row stays hidden: an unread list
    // is not an empty one, and reporting 0 fetched would be a claim about data
    // the shell has not seen.
    // ---------------------------------------------------------------
    var __sourceOriginsLoaded = false;
    var __fetchedSourceIds = {};

    function _fetchedSourceIndex(rows) {
      var map = {};
      (rows || []).forEach(function (f) {
        var host = String((f && f.fetched_url) || "");
        if (host && f.id) map[String(f.id)] = host;
      });
      return map;
    }

    // A paragraph is anchored (fetched) only when every quote it carries came
    // from a fetched row; anchored to an uploaded source AND a fetched one counts
    // as uploaded, because the source it rests on is one the user supplied.
    function _originSplit(doc) {
      var out = { uploaded: 0, fetched: 0, unanchored: 0 };
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
          var prov = node.provenance;
          if (!Array.isArray(prov)) prov = prov ? [prov] : [];
          var quoted = [];
          for (var p = 0; p < prov.length; p++) {
            var row = prov[p];
            if (row && typeof row === "object" &&
                String(row.extracted_quote || "").trim()) quoted.push(row);
          }
          if (!quoted.length) { out.unanchored++; continue; }
          var allFetched = quoted.every(function (row) {
            return Boolean(__fetchedSourceIds[String(row.source_id || "")]);
          });
          if (allFetched) out.fetched++;
          else out.uploaded++;
        }
      }
      return out;
    }

    function _renderOriginSplit(buckets) {
      var el = document.getElementById("counter-origin-line");
      if (!el) return;
      var doc = SHELL.document.current;
      var hasDoc = Boolean(doc && Array.isArray(doc.body) && doc.body.length);
      if (!hasDoc || !buckets || !buckets.eligible || !__sourceOriginsLoaded) {
        el.hidden = true;
        return;
      }
      var split = _originSplit(doc);
      el.textContent = "Anchored (uploaded) " + split.uploaded +
        " \u00b7 Anchored (fetched) " + split.fetched +
        " \u00b7 Unanchored " + split.unanchored;
      el.hidden = false;
    }

    function _num(stats, derived, key) {
      if (stats && typeof stats[key] === "number") return stats[key];
      if (derived && typeof derived[key] === "number") return derived[key];
      return 0;
    }
    function _renderCounters(stats, derived) {
      var b = _counterBuckets(stats, derived);
      _setCounter("count-anchored",    b.anchored);
      _setCounter("count-supported",   b.supported);
      _setCounter("count-unsupported", b.unsupported);
      _setCounter("count-unanchored",  b.unanchored);
      var legend = document.getElementById("counter-legend");
      var line = document.getElementById("counter-line");
      var doc = SHELL.document.current;
      // The line describes a compiled document, so it needs a document with
      // content: a blank tree is still "no document yet".
      var hasDoc = Boolean(doc && Array.isArray(doc.body) && doc.body.length);
      // The unsupported tile's own weight. Same glyph the paragraph carries in
      // its margin (`_ANCHOR_STATES.unsupported`), so the tile and the document
      // point at each other, and the sentence under the number says what the
      // number is: this is the one counter that is a finding, not a state.
      var alarm = hasDoc && b.unsupported > 0;
      var unsupportedCell = document.getElementById("counter-unsupported-cell");
      var unsupportedNote = document.getElementById("counter-note-unsupported");
      if (unsupportedCell) unsupportedCell.classList.toggle("is-alarm", alarm);
      if (unsupportedNote) unsupportedNote.hidden = !alarm;
      // Partial rides under Supported, not beside it: it counts inside Supported
      // (services/audit_summary._provenance_counts), and a fifth tile for it read
      // as a fifth bucket.
      var partialValue = document.getElementById("count-supported-partial");
      var partialLine = document.getElementById("counter-sub-supported");
      if (partialValue) partialValue.textContent = String(b.partial);
      if (partialLine) partialLine.hidden = !(hasDoc && b.eligible > 0);
      if (!hasDoc || !b.eligible) {
        if (line) { line.textContent = ""; line.hidden = true; }
        if (legend) legend.hidden = true;
        _renderOriginSplit(null);
        return;
      }
      var n = (SHELL.sources && SHELL.sources.length) || 0;
      if (line) {
        // The line is composed from catalog keys — never from English joined
        // here — so the words translate with the numbers this code supplies.
        // "1 source" and "N sources" are two keys because not every locale
        // inflects the count the same way.
        var from = _tf(
          n === 1 ? "counter.line.sources_one" : "counter.line.sources_many",
          n === 1 ? "Compiled from 1 source" : "Compiled from {sources} sources",
          { sources: n }
        );
        var text = _tf(
          "counter.line",
          "{from} \u00b7 {anchored} anchored of {eligible} eligible \u00b7 " +
            "{supported} supported ({partial} in part) \u00b7 {unanchored} unanchored",
          {
            from: from,
            anchored: b.anchored,
            eligible: b.eligible,
            supported: b.supported,
            partial: b.partial,
            unanchored: b.unanchored,
          }
        );
        // The finding is named in the line too, and only when there is one: a
        // "0 not supported" clause would report a clean document in the same
        // breath as a dirty one.
        if (b.unsupported > 0) {
          text += " \u00b7 " + _tf("counter.line.unsupported",
            "{unsupported} not supported", { unsupported: b.unsupported });
        }
        line.textContent = text;
        line.hidden = false;
      }
      if (legend) legend.hidden = false;
      _renderOriginSplit(b);
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
        applyAnchorStates(doc);
        addEvidenceChips(doc);
        _loadVersionHistory(projectId, { current: null });
        _refreshSignoff(projectId);
        // Prefer the server's persisted provenance_stats (the same numbers the
        // export and the gate read). Only when the document carries none does
        // _syncGroundingNotices derive the count locally, on the verdict rule.
        _syncGroundingNotices((doc.meta && doc.meta.provenance_stats) || null);
        _applyRightView();
        // ROUTED TO across a reload. The document does not carry the route and the
        // draft stream that did is long gone, so it is read from the compile the
        // project stored — the gate block's measure. Without this the row reads
        // COMPILE_MODEL_UNKNOWN beside a document that was in fact compiled.
        fetch("/api/projects/" + encodeURIComponent(projectId) + "/files")
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (res) {
            var route = (res && res.manifest && res.manifest.lastCompiledRoute) || null;
            var model = route && route.model ? String(route.model) : "";
            if (model && !__lastRunModel) {
              __lastRunModel = model;
              _renderCompilerRoute();
            }
          })
          .catch(function () { /* the pane keeps its own empty state */ });
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

    // A3 — where the editor sits. Under the paragraph it edits, in flow: it
    // used to be sticky at the node's top, so the paragraph it was about
    // scrolled underneath it and the reader typed over the text they were
    // rewriting. In the paragraph's own row nothing is covered — the original
    // stays where it was, above the box, and the box pushes what follows down.
    function _placeNodeRephrase(wrapper, editor) {
      var body = wrapper.querySelector(".jdf-p, .jdf-h2, .jdf-callout") || wrapper;
      var host = body.parentNode || wrapper;
      host.insertBefore(editor, body.nextSibling);
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
      _placeNodeRephrase(wrapper, editor);

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
    // The locator. A finding names the paragraph it is about (`node_id` on the
    // annotation, written by models/jdf.py:attach_redhat_annotation), so acting
    // on one puts that paragraph on screen: the caller writes the selection,
    // which paints .is-selected, and this brings the node into view and marks
    // it for 2.4 s with the `--contradicted` rule (.jdf-node.is-located,
    // shell.css) so the eye lands on the paragraph rather than at the end of a
    // scroll. Scrolling lives here rather than in the selection writer because
    // selecting a paragraph by clicking it should not move the document under
    // the reader.
    //
    // The mark is transient because at rest a paragraph that carries a finding
    // already keeps the same rule (.has-finding): what the click has to add is
    // "this one, now", and a third permanent state would say nothing the map
    // does not. One mark at a time — a second click moves it rather than
    // stacking.
    var LOCATE_MARK_MS = 2400;
    var __locateMarkTimer = null;
    var __locatedNodeEl = null;
    function _nodeWrapper(nodeId) {
      if (!nodeId || !draftEl) return null;
      return draftEl.querySelector('.jdf-node[data-node-id="' + nodeId + '"]');
    }
    // A paragraph carries a finding when one is placed on it: every finding in
    // the box lives in `annotations.redhat` of the node its audit ran on, and
    // carries that node's id from this release onward.
    function _nodeHasFindings(node) {
      var f = node && node.annotations && node.annotations.redhat;
      return Boolean(f && f.length);
    }
    // A finding nothing has answered. `status` is the only thing that separates
    // the two states: the pane's card prints the resolution line, and the node's
    // map mark keeps the contradicted rule only while one is still open.
    function _nodeHasOpenFindings(node) {
      var f = (node && node.annotations && node.annotations.redhat) || [];
      for (var i = 0; i < f.length; i++) {
        if (f[i] && String(f[i].status || "open") === "open") return true;
      }
      return false;
    }
    function _markLocated(el) {
      if (__locateMarkTimer) { clearTimeout(__locateMarkTimer); __locateMarkTimer = null; }
      if (__locatedNodeEl && __locatedNodeEl !== el) {
        __locatedNodeEl.classList.remove("is-located");
        __locatedNodeEl = null;
      }
      __locatedNodeEl = el;
      el.classList.add("is-located");
      __locateMarkTimer = setTimeout(function () {
        __locateMarkTimer = null;
        if (__locatedNodeEl) {
          __locatedNodeEl.classList.remove("is-located");
          __locatedNodeEl = null;
        }
      }, LOCATE_MARK_MS);
    }
    function _locateNode(nodeId) {
      var wrapper = _nodeWrapper(nodeId);
      if (!wrapper) return;
      // Align the paragraph's top with the top of the visible area, not its
      // centre: an audit reads downward from the paragraph it was sent to, and
      // a centred paragraph leaves the rest of the finding's prose below the
      // fold. `start` is the scroller's own edge, so no magic offset.
      try { wrapper.scrollIntoView({ block: "start" }); } catch (_) {}
      _markLocated(wrapper);
      _collapseOverlayRightPane();
    }
    // Landing on the paragraph is only half of showing it. Below the shell's own
    // overlay breakpoint the right pane is a drawer lying over the document
    // (shell.css: .pane-right is position:absolute in the <=900px block), so a
    // marked paragraph can be behind it — at 375px the drawer is 320px wide on a
    // 375px viewport and the paragraph's own centre sits under it. In that mode
    // the pane is collapsed, through the shell's own state path so its state and
    // the screen cannot disagree, and only AFTER the mark: LOCATE_MARK_MS (2400)
    // runs far past the drawer's 200ms transition, so the paragraph is on screen
    // and marked for the rest of the flash. Above the breakpoint the pane is a
    // track beside the document and nothing is collapsed.
    //
    // Overlay mode is read from the pane's own computed position, never from a
    // width repeated here: the media query in shell.css stays the single
    // declaration of where the breakpoint is, and if it moves this follows. The
    // reader re-opens the pane from the rail; the locator does not re-open it.
    //
    // INTERIM — this is a stopgap, not the fix, and it is named here so it does
    // not become the design by default. The collapse solves the occlusion by
    // hiding the finding the reader just clicked to read, which is exactly the
    // thing they asked to see. The replacement is a bottom sheet below 640px
    // (peek/half/full snap points, drag handle, keyboard and screen-reader
    // affordances); it is deferred, not forgotten — docs/deferred.md, "Evidence
    // pane as a bottom sheet below 640px". Until that lands this keeps the
    // marked paragraph visible at the width where the overlay would cover it.
    function _collapseOverlayRightPane() {
      var pane = _rightPane();
      if (!pane || SHELL.ui.layout.rightCollapsed) return;
      var overlay = false;
      try { overlay = getComputedStyle(pane).position === "absolute"; } catch (_) {}
      if (overlay) setShell("ui.layout.rightCollapsed", true);
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
          if (host && editor && !host.contains(editor)) _placeNodeRephrase(host, editor);
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
    // The pane's idle state still shows the action; this is what the disabled
    // button says underneath.
    var REDHAT_SELECT_FIRST = "Select a paragraph first";
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
      var token = String(reason || "").trim();
      // A truncated answer is refused, not recorded: the model hit its output
      // ceiling, so what came back was a fragment. The sentence for it is
      // addressed through the catalog here rather than in the map above, which
      // is built before /api/i18n has answered.
      if (token === "redhat_truncated") {
        return _t(
          "redhat.reason.truncated",
          "The audit ran out of room before it finished — no finding was saved. Run it again."
        );
      }
      return REDHAT_REASON_TEXT[token] ||
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

    // ---------------------------------------------------------------
    // Phase F1 — the finding is model markdown.
    //
    // The audit is prose from a reasoning model: it arrives with `**` around
    // its verdicts, `#` above its headings, `-`/`•`/`1.` in front of its
    // lists and `>` in front of the sentence it quotes. Setting all of that
    // as one text node showed the reader the model's own markup, so it is
    // parsed into elements instead — never stripped, so the weight the model
    // gave a phrase survives.
    //
    // Built with createElement/textContent only: the finding is untrusted
    // text and nothing here goes through innerHTML, so a finding cannot
    // introduce markup of its own.
    // ---------------------------------------------------------------
    var REDHAT_MODE_SOURCED = "Source-audited";
    var REDHAT_MODE_UNSOURCED = "Self-critique — no source anchored";

    // The audit's own test, mirrored: routers/draft.py:_anchoring_provenance_row
    // returns the first provenance row whose extracted_quote is non-empty, and
    // that row decides which prompt ran — with a sentence, the claim is checked
    // against it; without one, the review is of the claim alone. The pane names
    // the finding with the same distinction the pipeline made.
    function _redhatFindingMode(node) {
      var prov = (node && node.provenance) || (node && node.meta && node.meta.provenance) || [];
      if (!Array.isArray(prov)) prov = prov ? [prov] : [];
      for (var i = 0; i < prov.length; i++) {
        var row = prov[i];
        if (row && typeof row === "object" && String(row.extracted_quote || "").trim()) {
          return REDHAT_MODE_SOURCED;
        }
      }
      return REDHAT_MODE_UNSOURCED;
    }

    // `**` is consumed with one space after it, as the strip has always done
    // it, with the source index of every surviving character kept beside it.
    // That index is what lets the document's own offsets survive the markup
    // becoming elements: a run knows which characters of the paragraph's text
    // the server measured its confidence spans in.
    function _stripMarks(value) {
      var s = String(value == null ? "" : value);
      var out = "";
      var map = [];
      var i = 0;
      while (i < s.length) {
        if (s.charAt(i) === "*") {
          while (i < s.length && s.charAt(i) === "*") i++;
          if (s.charAt(i) === " ") i++;
          continue;
        }
        out += s.charAt(i);
        map.push(i);
        i++;
      }
      return { text: out, map: map };
    }

    // Text nodes carry no marker: a `*` that survived the inline pass is an
    // unbalanced marker, and the pane never shows it as prose punctuation.
    function _redhatMdText(value) {
      return document.createTextNode(_stripMarks(value).text);
    }

    // One inline run. Without a channel it is the text node it always was;
    // with one, the run is written where the spans cover it, `base` being the
    // run's own offset in the node's source text — the coordinate the spans
    // were computed in. Text outside every span is untouched.
    function _mdEmit(parent, value, ctx, base) {
      if (!ctx) { parent.appendChild(_redhatMdText(value)); return; }
      var stripped = _stripMarks(value);
      var out = stripped.text;
      var i = 0;
      while (i < out.length) {
        var span = ctx.covering(base + stripped.map[i]);
        var j = i + 1;
        while (j < out.length && ctx.covering(base + stripped.map[j]) === span) j++;
        var chunk = out.slice(i, j);
        if (span) {
          var holder = ctx.wrap(span);
          holder.textContent = chunk;
          parent.appendChild(holder);
        } else {
          parent.appendChild(document.createTextNode(chunk));
        }
        i = j;
      }
    }

    // Inline markdown — `**strong**`, `*em*`, `` `code` `` — inside a block
    // element. Emphasis must open and close on the same text; `_` is left out
    // deliberately, because source filenames and node ids carry it. `base` is
    // where `text` starts in the node's source text; a finding passes neither
    // it nor a channel, and gets the text nodes it always got.
    function _redhatMdInline(el, text, ctx, base) {
      var s = String(text == null ? "" : text);
      var at = base || 0;
      var re = /\*\*([\s\S]+?)\*\*|\*([^*\n]+?)\*|`([^`]+?)`/g;
      var last = 0;
      var m;
      while ((m = re.exec(s)) !== null) {
        if (m.index > last) _mdEmit(el, s.slice(last, m.index), ctx, at + last);
        var strong = m[1] !== undefined;
        var em = !strong && m[2] !== undefined;
        var node = document.createElement(strong ? "strong" : (em ? "em" : "code"));
        if (ctx) _mdEmit(node, strong ? m[1] : (em ? m[2] : m[3]), ctx, at + m.index + (strong ? 2 : 1));
        else node.textContent = m[1] || m[2] || m[3] || "";
        el.appendChild(node);
        last = re.lastIndex;
      }
      if (last < s.length) _mdEmit(el, s.slice(last), ctx, at + last);
      return el;
    }

    // Block markdown → a fragment of elements. Headings, ordered and unordered
    // lists (their indented continuation lines belong to the item), fenced code
    // and quoted sentences each get their own element; everything else is a
    // paragraph. `ctx` is the document's span channel (see _mdEmit): with it,
    // every run is written with the offset it came from, so the paragraph's
    // confidence spans are wrapped as the blocks are built. A finding passes
    // none, and this renders exactly as it always did.
    function _renderFindingMarkdown(text, ctx) {
      var frag = document.createDocumentFragment();
      var lines = String(text == null ? "" : text).replace(/\r\n?/g, "\n").split("\n");
      // Where each line starts in the source text: the coordinate space the
      // document's span offsets live in.
      var starts = [];
      var cursor = 0;
      for (var ln = 0; ln < lines.length; ln++) { starts.push(cursor); cursor += lines[ln].length + 1; }
      var para = [];   // [{text, start}]
      var i = 0;

      function indentOf(line) { var m = /^\s*/.exec(line); return m ? m[0].length : 0; }
      // A line with the whitespace in front of it accounted for.
      function trimmedAt(idx) {
        return { text: lines[idx].trim(), start: starts[idx] + indentOf(lines[idx]) };
      }
      // A block's lines, joined the way the model meant them — one space
      // between them — each still carrying its own offset in the source.
      function writeRun(el, parts) {
        for (var k = 0; k < parts.length; k++) {
          if (k) el.appendChild(document.createTextNode(" "));
          _redhatMdInline(el, parts[k].text, ctx, parts[k].start);
        }
      }
      function flushParagraph() {
        if (!para.length) return;
        var p = document.createElement("p");
        p.className = "redhat-md-p";
        writeRun(p, para);
        frag.appendChild(p);
        para = [];
      }
      function listBody(raw) {
        return String(raw == null ? "" : raw).trim().replace(/^>\s?/, "");
      }
      // A continuation line is indented under its marker; it belongs to the
      // item, not to a new paragraph.
      function indented(line) { return /^\s{2,}\S/.test(line); }
      // One list, items and all. Markdown lets an item wrap onto indented
      // lines and lets a blank line sit between two items — both stay inside
      // the list, so the model's `1. … 2. …` reads as one list and not as one
      // list per item. Only a blank line before something that is not another
      // item ends it.
      function consumeList(itemRe, tag, className) {
        flushParagraph();
        var list = document.createElement(tag);
        list.className = className;
        while (i < lines.length) {
          var m = itemRe.exec(lines[i].trim());
          if (!m) {
            if (!lines[i].trim()) {
              var next = i + 1;
              while (next < lines.length && !lines[next].trim()) next++;
              if (next < lines.length && itemRe.test(lines[next].trim())) { i = next; continue; }
            }
            break;
          }
          var line0 = trimmedAt(i);
          var parts = [{ text: m[1], start: line0.start + (line0.text.length - m[1].length) }];
          i++;
          while (i < lines.length && indented(lines[i])) {
            var body = listBody(lines[i]);
            var bodyLine = trimmedAt(i);
            parts.push({ text: body, start: bodyLine.start + (bodyLine.text.length - body.length) });
            i++;
          }
          var li = document.createElement("li");
          writeRun(li, parts);
          list.appendChild(li);
        }
        frag.appendChild(list);
      }

      while (i < lines.length) {
        var t = lines[i].trim();
        if (t.indexOf("```") === 0) {
          flushParagraph();
          var code = [];
          i++;
          while (i < lines.length && lines[i].trim().indexOf("```") !== 0) { code.push(lines[i]); i++; }
          i++;   // the closing fence
          var pre = document.createElement("pre");
          pre.className = "redhat-md-pre";
          var preCode = document.createElement("code");
          // A fence is verbatim: its text is written as it arrived, so a
          // confidence span over one is not wrapped here — nothing inside a
          // code block is re-shaped, markers included.
          preCode.textContent = code.join("\n");
          pre.appendChild(preCode);
          frag.appendChild(pre);
          continue;
        }
        if (!t) { flushParagraph(); i++; continue; }
        var h = /^#{1,6}\s+(.*)$/.exec(t);
        if (h) {
          flushParagraph();
          var head = document.createElement("div");
          head.className = "redhat-md-h";
          var headLine = trimmedAt(i);
          _redhatMdInline(head, h[1], ctx, headLine.start + (headLine.text.length - h[1].length));
          frag.appendChild(head);
          i++;
          continue;
        }
        if (/^[-*•+]\s+/.test(t)) {
          consumeList(/^[-*•+]\s+(.*)$/, "ul", "redhat-md-ul");
          continue;
        }
        if (/^\d{1,3}[.)]\s+/.test(t)) {
          consumeList(/^\d{1,3}[.)]\s+(.*)$/, "ol", "redhat-md-ol");
          continue;
        }
        if (/^>\s?/.test(t)) {
          flushParagraph();
          var quote = document.createElement("blockquote");
          quote.className = "redhat-md-quote";
          var quoted = t.replace(/^>\s?/, "");
          var quoteLine = trimmedAt(i);
          _redhatMdInline(quote, quoted, ctx, quoteLine.start + (quoteLine.text.length - quoted.length));
          frag.appendChild(quote);
          i++;
          continue;
        }
        var proseLine = trimmedAt(i);
        para.push(proseLine);
        i++;
      }
      flushParagraph();
      return frag;
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
      // A Red-Hat finding is the flag, open or answered: the chip's class
      // carries which (shell.css .chip-redhat.open / .resolved), and the flag
      // itself is the same mark either way — the document's note that a warning
      // stood on this paragraph.
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

    // ---------------------------------------------------------------
    // Evidence colouring, read at conversational distance. The 1px confidence
    // underline stays as the sub-claim signal it is; the *paragraph's* state is
    // carried by a left rule, a tint, and a chip, because a reader standing back
    // could not see the underline at all.
    //
    // One table for the five states, and the table is the whole state surface:
    // the margin mark, the chip, the paragraph's accessible name, the pane's
    // legend and the drawer all read their wording from it, so a state cannot be
    // drawn one way in the document and named another way in the pane.
    //
    // Why five and not four: "the source denies this" and "nobody checked this"
    // are different findings with different consequences for a reader signing
    // the memo, and `_anchorStateOf` used to render both as the same grey
    // `anchored` chip — every verdict it did not recognise fell through to
    // `anchored`. A contradiction is not an absence. `unsupported` is that
    // verdict (the entailment check answered `no`, or a citation was flagged
    // contradicted), and `anchored` shrinks to what it always meant: cited, with
    // no confirmation recorded.
    //
    // Colour is the fourth channel, never the first: this audience prints in
    // black and white and a meaningful share of readers are colour-blind. Every
    // state is separable by its glyph (`✓ ~ ✗ ? —`), by its rule pattern
    // (solid / dashed / doubled / dotted / dotted+wash), and by its word.
    var _ANCHOR_STATES = {
      supported:   { glyph: "\u2713", key: "state.supported",
                     fallback: "Supported",
                     hintKey: "state.hint.supported",
                     hint: "The source states this claim, wholly." },
      partial:     { glyph: "~", key: "state.partial",
                     fallback: "Partial",
                     hintKey: "state.hint.partial",
                     hint: "The source supports this claim only in part." },
      unsupported: { glyph: "\u2717", key: "state.unsupported",
                     fallback: "Contradicted",
                     hintKey: "state.hint.unsupported",
                     hint: "The source check did not find this claim in the sentence it cites." },
      anchored:    { glyph: "?", key: "state.anchored",
                     fallback: "Not confirmed",
                     hintKey: "state.hint.anchored",
                     hint: "This paragraph cites a source, and no source check has confirmed it." },
      unanchored:  { glyph: "\u2014", key: "state.unanchored",
                     fallback: "No source",
                     hintKey: "state.hint.unanchored",
                     hint: "No source sentence was matched to this paragraph." },
    };
    function _anchorStateName(state) {
      var spec = _ANCHOR_STATES[state];
      return spec ? _t(spec.key, spec.fallback) : "";
    }
    function _anchorStateWords(state) {
      var spec = _ANCHOR_STATES[state];
      return spec ? spec.glyph + " " + _anchorStateName(state) : "";
    }
    function _anchorStateHint(state) {
      var spec = _ANCHOR_STATES[state];
      return spec ? _t(spec.hintKey, spec.hint) : "";
    }
    function _anchorStateOf(node) {
      if (!node || String(node.type || "") !== "paragraph") return null;
      if (_anchorContentTokens(node.content) < _ANCHOR_WORD_FLOOR) return null;
      var prov = node.provenance;
      if (!Array.isArray(prov)) prov = prov ? [prov] : [];
      var anchored = false;
      for (var p = 0; p < prov.length; p++) {
        var row = prov[p];
        if (row && typeof row === "object" &&
            String(row.extracted_quote || "").trim()) { anchored = true; break; }
      }
      if (!anchored) return "unanchored";
      // The raw verdict, not `_entailmentVerdict`'s label fallback: the two
      // fields the server's own counter reads (audit_summary._is_contradicted)
      // are the verdict string and the citation's contradicted flag, and this
      // mirror has to read the same two or the margin would disagree with the
      // tile beside it.
      var ent = _entailmentFor(node, prov[0] || null);
      var verdict = ent ? String(ent.verdict || "").toLowerCase() : "";
      var contradicted = Boolean(ent && ent.contradicted);
      if (verdict === "no" || contradicted) return "unsupported";
      if (verdict === "yes") return "supported";
      if (verdict === "partial") return "partial";
      return "anchored";
    }
    function applyAnchorStates(doc, targetEl) {
      if (!doc || !Array.isArray(doc.body)) return;
      var scope = targetEl || document;
      for (var s = 0; s < doc.body.length; s++) {
        var section = doc.body[s];
        if (!section || typeof section !== "object") continue;
        var group = [section];
        if (Array.isArray(section.children)) group = group.concat(section.children);
        for (var g = 0; g < group.length; g++) {
          var node = group[g];
          if (!node || !node.id) continue;
          var state = _anchorStateOf(node);
          if (!state) continue;
          var el = scope.querySelector('.jdf-node[data-node-id="' + node.id + '"] .jdf-p');
          if (!el) continue;
          el.classList.add("anchor-state", "anchor-" + state);
          // The state is on the element, not only in the class list: the pane,
          // a test and the export's own reading of the DOM ask what state this
          // paragraph is in, and a class name is a styling detail.
          el.setAttribute("data-anchor-state", state);
          if (el.querySelector(".anchor-chip")) continue;
          var chip = document.createElement("span");
          chip.className = "anchor-chip anchor-chip-" + state;
          chip.setAttribute("role", "note");
          chip.setAttribute("aria-label", _anchorStateName(state) + " \u2014 " +
                            _anchorStateHint(state));
          chip.setAttribute("title", _anchorStateHint(state));
          chip.textContent = _anchorStateWords(state);
          // A paragraph is markdown blocks now, so the chip goes into the first
          // of them: it reads at the start of the paragraph's first line, the
          // way it did when the paragraph was one text node.
          var chipHost = el.querySelector(".redhat-md-p, .redhat-md-h, .redhat-md-ul > li, .redhat-md-ol > li, .redhat-md-quote") || el;
          if (chipHost.firstChild) chipHost.insertBefore(chip, chipHost.firstChild);
          else chipHost.appendChild(chip);
        }
      }
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

      // The channel the markdown renderer writes through. `covering` names the
      // span that owns a source character; the spans are sorted by start and
      // asked in the order the text is written, so one cursor serves the whole
      // node. `wrap` builds the span element: the same classes, the same
      // accessible name and the same two handlers the tail-built markup used
      // to carry.
      function spanChannel(nodeId, nodeSpans, prov0) {
        var cursor = 0;
        function bandOf(score) {
          return (score > 0.8) ? "high" : (score >= 0.4) ? "medium" : "low";
        }
        return {
          covering: function (src) {
            while (cursor < nodeSpans.length && nodeSpans[cursor].end <= src) cursor++;
            if (cursor < nodeSpans.length && nodeSpans[cursor].start <= src) return nodeSpans[cursor];
            return null;
          },
          wrap: function (sp) {
            var el = document.createElement("span");
            el.className = "conf-span";
            if (prov0) {
              if (sp.score > 0.8) el.className += " conf-green";
              else if (sp.score >= 0.4) el.className += " conf-yellow";
              else el.className += " conf-red";
            }
            el.setAttribute("data-node-id", nodeId);
            el.setAttribute("data-score", String(sp.score));
            el.setAttribute("role", "button");
            el.setAttribute("tabindex", "0");
            // §4 A7: the span is a click target, so it is authored as a control —
            // role, tab stop and a name that states its confidence band. The
            // neutral wording is deliberate; persuasive phrasing goes to the
            // voice pass.
            el.setAttribute("aria-label", "Confidence span: " + (prov0 ? bandOf(sp.score) : "no source matched"));
            el.addEventListener("click", handleConfidenceClick);
            el.addEventListener("keydown", _confSpanKeydown);   // §4 A7
            return el;
          },
        };
      }

      var nodeIds = Object.keys(byNode);
      for (var n = 0; n < nodeIds.length; n++) {
        var nodeId = nodeIds[n];
        var wrapper = scopeEl.querySelector('.jdf-node[data-node-id="' + nodeId + '"]');
        if (!wrapper) continue;
        var textEl = wrapper.querySelector(".jdf-p, .jdf-h2, .jdf-callout");
        if (!textEl) continue;

        // No tooltip: the 1px semantic underline is the signal (Phase F), and
        // the drawer behind a click carries source, page and quote. Only the
        // node's first provenance row is needed, to band the span.
        var provNode = findJdfNodeById(nodeId, doc);
        var provMeta = provNode ? (provNode.meta && provNode.meta.provenance) : null;
        var provList = Array.isArray(provMeta) ? provMeta : (provMeta ? [provMeta] : []);
        var prov0 = provList[0] || null;

        // The text the server measured, not what is on screen: the offsets are
        // into the node's own content, and the paragraph's markdown has already
        // become elements by the time this runs.
        var text = provNode
          ? String(provNode.type === "section" ? (provNode.title || "") : (provNode.content || ""))
          : "";
        if (!text) text = textEl.textContent || "";
        if (text.length === 0) continue;

        // Keep only in-range spans, ASCENDING: the renderer writes the text
        // front to back, so its cursor walks the same way.
        var nodeSpans = [];
        for (var s = 0; s < byNode[nodeId].length; s++) {
          var cand = byNode[nodeId][s];
          var cs = parseInt(cand.startChar, 10);
          var ce = parseInt(cand.endChar, 10);
          if (isNaN(cs) || isNaN(ce) || cs < 0 || ce > text.length || cs >= ce) continue;
          nodeSpans.push({ start: cs, end: ce, score: cand.score });
        }
        if (nodeSpans.length === 0) continue;
        nodeSpans.sort(function (a, b) { return a.start - b.start; });

        // One pass, one renderer: the same markdown the document's paragraphs
        // are drawn with, told where the spans sit so the wrappers land on the
        // right characters. A paragraph can be a run of blocks, and a span that
        // crosses a block boundary comes out as one wrapper per block — never
        // an inline box dragged around a list.
        textEl.textContent = "";
        var channel = spanChannel(nodeId, nodeSpans, prov0);
        if (String((provNode || {}).type || "") === "paragraph") {
          textEl.appendChild(_renderFindingMarkdown(text, channel));
        } else {
          _redhatMdInline(textEl, text, channel, 0);
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
        } else if (stage === "entailment") {
          // The source check has started, so Math Check has returned. Its
          // SKIPPED/ran verdict travels only on the `verified` frame below,
          // which rewrites the state then; until it does, "done" is what the
          // stream has reported (the check returned) and never a pass.
          if (stageState["Math Check"] !== "skipped") markDone("Math Check");
          transitionTo("Entailment");
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
      } else if (event === "usage" && data && typeof data === "object") {
        // The model that ANSWERED, once the provider has. The
        // status{stage:"model"} frame above is emitted before the call, so it can
        // only carry the model this process asked for; this frame arrives after,
        // and its serving_model is what the provider reported
        // (draft.py `_stream_model`, from the response's own `model`).
        if (data.serving_model) {
          __lastRunModel = String(data.serving_model);
          _renderCompilerRoute();
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
            applyAnchorStates(vdoc);

            // Unclear whether provenance_stats lives top-level or nested —
            // check the SSE payload; use the real location.
            var stats = data.provenance_stats || null;
            if (!stats && data.document && data.document.meta) {
              stats = data.document.meta.provenance_stats || null;
            }

            // Verified frame: the persisted stats are authoritative (DB parity
            // with the gate block the honesty test reads).
            _syncGroundingNotices(stats);
            // The verdict this frame carries, held for the `complete` frame
            // below: that frame ends the run, and a run that finished is not a
            // document whose claims held — the revision is saved either way.
            verifiedContradictions = _contradictedClaims(stats, vdoc);
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
        // The run finished. A refused compile ends with this same frame carrying
        // ok:false — and the error branch above has already marked the stage that
        // failed, so only a run that succeeded ticks the stages off: otherwise a
        // refusal leaves "Draft ✗" sitting above "Verify ✓". What a finished run
        // owes the UI either way (progress bar, run flag, intent slot) is outside
        // the test: failed or not, the run is over.
        var refused = Boolean(data && data.ok === false);
        if (!refused) {
          // Compile can still be .active when the "running math check" status
          // frame never arrived, because transitionTo("Verify") only marks stages
          // STRICTLY before the index it is leaving behind. Close it out
          // explicitly so no stage dot keeps pulsing after a finished run.
          markDone("Compile");
          markDone("Verify");
          markActive("Complete");
          markDone("Complete");
          // The run finished. That is all this frame's ok says: a run whose
          // claims a source denied still finishes — and is still saved as a
          // revision — so a checkmark over the `verified` frame's contradicted
          // count would report a verification the entailment layer refused. The
          // bar names the count instead, and ticks only on none: "not supported"
          // is what the bucket holds (verdict "no"), stated no more specifically
          // than the frame's own aggregation distinguishes.
          if (intentSummaryTextEl) {
            intentSummaryTextEl.textContent = verifiedContradictions > 0
              ? "Compiled \u2014 " + verifiedContradictions + " claims not supported"
              : "\u2713 Intent compiled \u00b7 checks run in the pipeline";
          }
        }
        _endProgress();
        _refreshManifest();
        _setRunInProgress(false);
        // A finished run is a finished run: no progress line outlives the frame
        // that ends it.
        _jdfClearProgress();
        // A pass leaves the bar standing as the run's verdict; a refusal is a
        // verdict too, and it arrives as its own card, so the bar goes.
        if (refused) clearIntentSlot();
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
        // The validator's refusal is a verdict on the document, not a transport
        // error: it gets the refusal card, and no error frame is left in the pane.
        // Any other failure (an empty draft, a model error, a parse failure) is a
        // halt: it gets the halt card, which discards the streamed text the same
        // way, so a failed run never leaves a draft standing as the document.
        if (data && Number(data.http_status) === 422) _showRefusalCard(msg);
        else _showHaltCard(msg);
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
      return jsonPost("/api/projects", { title: "workspace" })
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
      if (!active) { if (projectCurrentNameEl) projectCurrentNameEl.textContent = "workspace"; return; }
      fetch("/api/projects")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          var projects = (j && j.projects) || [];
          var found = null;
          for (var i = 0; i < projects.length; i++) { if (projects[i].id === active) { found = projects[i]; break; } }
          if (found && projectCurrentNameEl) projectCurrentNameEl.textContent = found.title || "workspace";
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
          // Only the sources the vault marks `included` ground a compile: the
          // list route returns every row so the SOURCES pane can show and toggle
          // them, and SHELL.sources is the grounding list the compiles post AND
          // the checkbox state this pane derives (see _isSourceIncluded), so an
          // excluded row here would be posted as grounding and drawn as ticked.
          setShell("sources", rows
            .filter(function (f) { return f.included !== false; })
            .map(function (f) { return f.id; }));
          // 2C.6: which of those sources came from a fetched page — the
          // same rows carry the fetched_url tag. The origin row is hidden
          // until this list has been read, so an unread list never reads
          // as "no fetched sources".
          __sourceOriginsLoaded = true;
          __fetchedSourceIds = _fetchedSourceIndex(rows);
          _refreshCounters();
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
          // The answer is in: an empty list now means "this project has none",
          // which is what the column's empty-project card waits for.
          _sourcesLoaded = true;
          _syncDocState();
        })
      .catch(function () {
        // A failed read is not a source list. The card stays away rather than
        // claiming the project has no source on a failed request.
        _sourcesLoaded = false;
        _syncDocState();
      });
    }
    // ---------------------------------------------------------------
    // SOURCES — the OMP manifest: filename, size, indexed date, and after a
    // compile the anchors each source contributed. Read from the same vault
    // route the Sources tab lists, so the two surfaces cannot disagree.
    //
    // A5: this row counts SOURCES and their anchors; the right pane counts
    // PARAGRAPHS. It used to call both of them "anchored", which put one word
    // on two meanings in one screen, so the word now belongs to the paragraph
    // counters alone and this row says what it counts.
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
        // The row is addressable: an Evidence drawer's citation jumps here when
        // the Sources tab has no row to land on (same id, one vault list).
        row.setAttribute("data-source-id", String(f.id || ""));
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
          seen.textContent = "1 source contributes " + counts.anchored +
            (counts.anchored === 1 ? " anchor" : " anchors") +
            (counts.total === counts.anchored ? "" : " of " + counts.total);
          row.appendChild(seen);
        }
        el.appendChild(row);
      });
    }
    _renderManifestFn = _renderManifest;
    // ---------------------------------------------------------------
    // From a citation to the file it came from.
    //
    // `cited_id` is `S<N>` — the sentence's number in the map the compile built
    // (`routers/draft.build_sentence_map` over the numbered source blocks), which
    // is the position the model wrote in `[S<N>]`. The shell has no sentence-map
    // surface, so the control can honestly offer the two things it does have: the
    // number itself, and the source row for the file that sentence came from.
    // Both routes end in `_locateSource`, which opens the Sources tab that lists
    // the file and flashes the row — the same landing mark the document locator
    // uses (`_markLocated`), so a jump reads the same wherever it is made from.
    // ---------------------------------------------------------------
    function _citationJumpTitle(citedId, sourceName, page) {
      var parts = [];
      if (citedId) parts.push(citedId);
      if (sourceName) parts.push(sourceName);
      if (page) parts.push(_tf("evidence.page", "page {page}", { page: page }));
      return parts.join(" \u00b7 ") + " \u2014 " +
        _t("evidence.citation.jump", "show this source in the Sources list");
    }
    // Which vault row a citation came from. The compile's own rows carry
    // `source_id`, but the *persisted* tree is written through the JDF model,
    // whose provenance row keeps `source_name` and drops the id — so a memo
    // reloaded from the project would show every citation as unlinkable. The
    // row names the file; the vault list is the authority on which file that is,
    // so the name resolves to the id. Two rows sharing a filename are ambiguous
    // and resolve to nothing, rather than to the wrong source.
    function _sourceIdForRow(row) {
      var direct = String((row && row.source_id) || "");
      if (direct) return direct;
      var name = String((row && row.source_name) || "").trim();
      if (!name) return "";
      var hits = (manifestRows || []).filter(function (f) {
        return String((f && f.filename) || "").trim() === name;
      });
      return hits.length === 1 ? String(hits[0].id || "") : "";
    }
    function _wireSourceJump(btn, sourceId, citedId) {
      var sid = String(sourceId || "");
      btn.setAttribute("data-source-id", sid);
      if (citedId) btn.setAttribute("data-citation", String(citedId));
      if (!sid) {
        // Nothing in the Sources list answers to this citation: the control
        // stays, and says which list it could not open.
        btn.disabled = true;
        btn.title = _t("evidence.source.unknown",
          "This citation names no source the Sources list carries, so it cannot be opened there.");
        return;
      }
      btn.addEventListener("click", function (e) {
        if (e && e.stopPropagation) e.stopPropagation();
        _locateSource(sid);
      });
    }
    // Open the pane that lists the sources, select it, and flash the row. Nothing
    // is asserted about the source beyond it being the one the row names.
    function _locateSource(sourceId) {
      var sid = String(sourceId || "");
      if (!sid) return;
      if (typeof openLeft === "function") openLeft();
      if (typeof leftGroupSetTab === "function") leftGroupSetTab("sources");
      // The Sources tab's rows are built from the vault list; the manifest row in
      // the Compiler tab carries the same id, so either surface can take the
      // landing mark and the first one present wins.
      var target = document.querySelector('#source-list [data-source-id="' + sid + '"]') ||
                   document.querySelector('#source-manifest [data-source-id="' + sid + '"]');
      if (!target) return;
      try { target.scrollIntoView({ block: "center", behavior: "smooth" }); } catch (_) {}
      // The document locator's own mark and its own timer: a jump reads the same
      // wherever it is made from.
      _markLocated(target);
    }
    // The counter line counts sources, so it is re-rendered whenever the
    // source list or the document changes.
    function _refreshCounters() {
      var doc = SHELL.document.current;
      _renderCounters((doc && doc.meta && doc.meta.provenance_stats) || null,
                      doc ? _derivedCounts(doc) : null);
    }
    _syncCountersFn = _refreshCounters;
    // Resolves once `manifestRows` has been re-read (or the read failed), so a
    // caller that needs to know whether a row landed can look after it settles.
    function _refreshManifest() {
      var pid = _sourceProjectId();
      if (!pid) return Promise.resolve();
      return fetch("/api/projects/" + encodeURIComponent(pid) + "/substrate")
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
      if (projectCurrentNameEl) projectCurrentNameEl.textContent = title || "workspace";
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
      var name = window.prompt("New project name", "workspace");
      if (name === null) return;
      var title = String(name || "").trim() || "workspace";
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
    // (sign-offs are fetched by the project-restore path below; the
    //  unconditional call here was a duplicate request on every boot)

    // S1: restore persisted pane collapse state (default false → both panes
    // visible on first-ever load), then render. _applyRightView() draws the
    // right pane's inner state independent of the collapse classes.
    // D1: at the widths where a pane can only be an overlay, an open pane covers
    // the document it exists to serve — so the first paint of a narrow window
    // starts with both closed. A stored preference still wins: the user's own
    // last choice is never overridden, only the viewport's first paint chosen.
    var __storedLeft = null, __storedRight = null;
    try {
      __storedLeft  = localStorage.getItem("assure.left_collapsed");
      __storedRight = localStorage.getItem("assure.right_collapsed");
    } catch (_) {}
    var __narrow = false;
    try { __narrow = window.matchMedia("(max-width: 900px)").matches; } catch (_) {}
    SHELL.ui.layout.leftCollapsed  = (__storedLeft  === null) ? __narrow : (__storedLeft  === "1");
    SHELL.ui.layout.rightCollapsed = (__storedRight === null) ? __narrow : (__storedRight === "1");
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

    // Export (top bar) → the export pair for the active project: the audit PDF
    // and the .jdf sidecar that carries the provenance, each node's verification
    // state, the source manifest, the version chain and the drafting model.
    // Disabled until _canExport() (see _syncExportEnabled), which the document
    // paths re-evaluate; the click re-reads the same state instead of dead-ending
    // in a console.warn.
    exportBtnEl = document.getElementById("export-btn");
    if (exportBtnEl) {
      exportBtnEl.addEventListener("click", function () {
        if (!_canExport()) { _syncExportEnabled(); return; }
        // 2D.1: one download holding both files, so the readable dossier and the
        // verifiable one cannot be separated. `format=jdf` serves the sidecar alone.
        window.location.href =
          "/api/projects/" + encodeURIComponent(_exportProjectId()) + "/export?format=bundle";
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
      // A run ends in exactly two ways: the server says so (a `complete` frame —
      // success or refusal), or it does not. The second is a halt, and it must
      // not leave the streamed draft on the column: `ended` records the server's
      // verdict so a halt cannot pass for one, and every path that ends the
      // stream short routes through haltDraftStream.
      var ended = false;
      function haltDraftStream(message) {
        if (ended) return;
        ended = true;
        var active = findActiveStage() || STAGE_ORDER[Math.max(0, currentStageIndex)];
        markFailed(active);
        _endProgress();
        _setRunInProgress(false);
        // The run is over either way: drop the success bar left by the intent
        // compile and bring the retained stage rows back into view, so the
        // failed row is visible instead of a stale "Intent compiled" slot
        // hiding the stages — the same stale-slot defect the handleEvent error
        // branch fixed.
        clearIntentSlot();
        leftGroupSetTab("compiler");
        _showHaltCard(String(message || ""));
      }
      var parser = parseSseLoop(
        function (event, data) {
          if (event === "complete") ended = true;
          // A refusal is a verdict, and it arrives with its own card; the
          // `complete` frame that follows carries ok:false (routers/draft.py).
          if (event === "error" && data && Number(data.http_status) === 422) ended = true;
          handleEvent(event, data);
        },
        function () {
          // The stream closed without saying how the run ended: no verdict, no
          // verified document. Clear the column instead of leaving a draft that
          // nothing claimed.
          haltDraftStream("The compile stream ended before the run finished.");
        },
        function (err) {
          haltDraftStream(String(err && err.message ? err.message : err));
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
          if (!started) resetStages();
          haltDraftStream(String(err && err.message ? err.message : err));
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
    // Claims the entailment check contradicted, as reported by the last
    // `verified` frame. The `complete` frame that ends the run carries ok:true
    // and nothing about the claims, so the intent bar reads this count before it
    // may tick — see handleEvent.
    var verifiedContradictions = 0;

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
      // Enter in the dock input reaches this without the button, so the same
      // precondition stands here: no source, no compile. The button is already
      // disabled and carries the reason (the empty-project card says the rest).
      if (!(SHELL.sources || []).length) {
        _syncDockSubmit();
        return;
      }
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
    // ROUTED TO — the model this compile was routed to, taken from the draft
    // stream itself. /api/compile-system answers the system message and the answer
    // shape, not the model, so it can supply no runtime fact. The suffix is the model and nothing else:
    // the Red-Hat pass is a stage of the pipeline, reported by its own row and its
    // own pane, so "Red-Hat skipped" beside the model read as a second thing the
    // request had been routed to.
    function _renderCompilerRoute() {
      populateCompilerRoute(__lastRunModel || "");
    }

    // A4: re-read the state the AI view shows. Called when the pane is opened
    // and when the document's revision list lands.
    function _refreshCompilerState() {
      var doc = SHELL.document.current || null;
      var hasDoc = Boolean(doc && Array.isArray(doc.body) && doc.body.length);
      populateCompilerRoute(hasDoc ? (__lastRunModel || "") : "");
      if (hasDoc && _compiledVersionOnScreen()) markAllStagesDone();
      else resetStages();
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
      // The ask goes with it: the compile system message is static except for the
      // answer-shape block, which follows the ask (web.py, services/answer_shape).
      jsonPost("/api/compile-system", { intent: raw })
        .then(function (res) { return res.json(); })
        .then(function (j) {
          if (!j || typeof j !== "object") return;
          // Remembered so re-opening the COMPILER tab (expandIntentPanel)
          // can restore the prompt that was actually compiled against.
          lastCompile = j;
          if (typeof j.prompt === "string" && j.prompt.length > 0) {
            setCompilerPrompt(j.prompt);
          }
          // The prompt came back; the run has not. The validator's verdict
          // arrives on the draft stream, and a document that will be refused
          // streams exactly like one that will pass — so the status line reads
          // as in-flight here and stays that way. The checkmark is written only
          // by the `complete` frame that carries the pass (handleEvent).
          if (intentSummaryTextEl) {
            intentSummaryTextEl.textContent = "Compiling\u2026";
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
      // Not a result: the prompt may still be in flight, and the validator's
      // verdict has not arrived. The bar reads the same through the whole
      // in-flight window and is flipped to the checkmark by the passing
      // `complete` frame, or replaced by the refusal card.
      textEl.textContent = "Compiling\u2026";
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
        var opened = leftGroupSetTab(t.getAttribute("data-left-tab"));
        // A4: the AI view reads the compile state when it is opened, so it can
        // never sit on a stale "Awaiting route" beside a document that has
        // been compiled.
        if (opened === "compiler") _refreshCompilerState();
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
      // key: "claude" | "secondary" — used for the column title.
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
      applyAnchorStates(doc);
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
              applyAnchorStates(data.document, col.__body);
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

      // The pair is a REQUEST, not a report: each id goes out as `target_ai`
      // on the draft stream, and the column's model line is overwritten by the
      // status{stage:"model"} frame the server answers with (compareStreamSide),
      // so the label never outlives the route. The server does publish its own
      // pair (routers/health.py `/health`: `stack`, `orchestrator_models` when
      // the free stack is on), but that blueprint is not under /api/ and the
      // shell's dev-server proxies /api/* only; /api/health (web.py) carries
      // neither field. Checked 2026-09-22 — until a proxied route serves the
      // pair, these two ids are the defaults the production stack is configured
      // with (llm/orchestrator.py PRODUCTION_MODEL_PAIRS).
      var colA = compareColumnShell("claude", "anthropic/claude-sonnet-4-5");
      var colB = compareColumnShell("secondary", "openrouter/qwen/qwen3-next-80b-a3b-instruct");
      grid.appendChild(colA);
      grid.appendChild(colB);

      compareDataLoaded = true;         // do not refire on tab re-click
      setShell("streams.compareA", compareStreamSide(colA, "anthropic/claude-sonnet-4-5"));
      setShell("streams.compareB", compareStreamSide(colB, "openrouter/qwen/qwen3-next-80b-a3b-instruct"));
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
        _locateNode(nodeId);
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
      // action it appended had no route to the screen.
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
      // §6: state in, one paint out. A span opens the paragraph's own panel,
      // so any drawer payload left by an earlier chip click is cleared first
      // and the node id write below is the click's single render.
      setShell("ui.selection.evidence", null);
      setShell("ui.selection.nodeId", nodeId);
      openRight();
      setMode("evidence");
    }

    // §6: demoted — no longer a writer. renderEvidencePanel owns the pane and
    // paints the paragraph panel (verdict header, reasoning, excerpt, fields).
    // The score footer that used to be appended under it is gone: it read
    // "Ledger check score: N%", naming a term the reader was never given and
    // labelling a span's numeric lock check as though it were grounding. The
    // pane says which state the paragraph is in, the counters carry the
    // distribution, and the Math check (Z3) tab is where a ledger score
    // belongs. `data-score` stays on the span, where it bands the underline.

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
      // The Red-Hat pane's whole content is its one action, so it is rendered
      // with no node rather than replaced by a hint: the button is disabled and
      // says why. A pane whose only control disappears is a pane a reader
      // cannot tell from a broken one.
      if (tab === "redhat") { renderRedhatPanel(null); return; }
      var body = z3ModeEl;
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
      // three states have: an explicitly opened drawer (the user named that
      // item) beats the paragraph panel; with nothing selected the pane is
      // idle. The other writer is a builder this one calls — it never clears
      // the pane.
      //
      // The spec's `drawer > panel > idle` line describes this order, which is
      // why it is expressed here rather than in the dispatch: the drawer's
      // caller is an event handler (a chip click), never _applyRightView, so
      // there was no dispatcher-side precedence to reorder — the call sites
      // that produced the extra builds were the handlers themselves.
      if (opts && opts.drawer) { renderEvidenceDrawer(opts.drawer); return; }
      // Nothing selected: the pane's content is the counters above this body,
      // so the body stays empty rather than announcing an empty pane.
      if (!node) return;
      var prov = node.provenance;
      if (!Array.isArray(prov)) prov = (node.meta && node.meta.provenance);
      if (!Array.isArray(prov)) prov = prov ? [prov] : [];
      var p0 = prov[0] || null;
      // No cited sentence resolved: the paragraph is unanchored, and that is
      // this pane's message. It used to be reached only when the *verdict* was
      // absent too, so a paragraph with no citation but a recorded verdict
      // (an `unverified` from a check that never had a sentence to read, or a
      // stale `no`) fell through to the paragraph panel and rendered as though
      // it had provenance. Nothing was matched to the claim, so there is nothing
      // to quote; the drawer says the claim is not traceable and, when a verdict
      // exists, names it.
      if (!p0) {
        // 2B/2C: unanchored is not "nothing to say". The drawer names the
        // state, the model names the gap, and the two ways to close it sit
        // under it — upload a document, or fetch an allowlisted page.
        _renderUnanchoredDrawer(node);
        return;
      }
      var pageStr = _rowPage(p0);
      // The verdict block: the paragraph's state, in the same glyph and the same
      // word the margin chip uses, with the check's own label under it. The two
      // surfaces name one state once — a reader who has learned the marks in the
      // document reads the pane without a second legend.
      var state = _anchorStateOf(node);
      var header = document.createElement("div");
      header.className = "evidence-header evidence-verdict" +
        (state ? " anchor-" + state : "");
      var badge = document.createElement("span");
      badge.className = "evidence-verdict-badge";
      badge.setAttribute("aria-hidden", "true");
      badge.textContent = state ? _ANCHOR_STATES[state].glyph : "\u00b7";
      var headText = document.createElement("span");
      headText.className = "evidence-verdict-text";
      var stateName = document.createElement("span");
      stateName.className = "evidence-verdict-state";
      stateName.textContent = state ? _anchorStateName(state) : "";
      var stateLabel = document.createElement("span");
      stateLabel.className = "evidence-verdict-label";
      // Label from the entailment verdict, not from the anchor's presence.
      stateLabel.textContent = _entailmentLabel(node, p0, pageStr);
      headText.appendChild(stateName);
      headText.appendChild(stateLabel);
      header.appendChild(badge);
      header.appendChild(headText);
      evidenceBodyEl.appendChild(header);
      var content = document.createElement("div");
      content.className = "evidence-content";
      // The check's one-sentence reason, directly under the label. Absent when
      // the check produced none — never filled in with a made-up justification.
      var reasoning = _entailmentReasoning(node, p0);
      if (reasoning) {
        var reasonEl = document.createElement("p");
        reasonEl.className = "evidence-value evidence-reason";
        reasonEl.textContent = reasoning;
        content.appendChild(reasonEl);
      }
      // The paragraph's own citations, counted from the rows about to be drawn —
      // not from a stat, so the tally and the list under it cannot disagree.
      var verdicts = _citationVerdicts(node, prov);
      _renderCitationBreakdown(content, prov, verdicts);
      function field(label, value) {
        var s = String(value == null ? "" : value);
        if (!s) return;
        var f = document.createElement("div");
        f.className = "evidence-field";
        var l = document.createElement("div"); l.className = "evidence-label"; l.textContent = label;
        var v = document.createElement("div"); v.className = "evidence-value"; v.textContent = s;
        f.appendChild(l); f.appendChild(v); content.appendChild(f);
      }
      // One block per cited sentence, in the order the compile stored them: the
      // paragraph's first citation first, the rest below. The quote is read from
      // ``extracted_quote`` — the source sentence the compile stamped
      // (routers/draft.py:attach_citations_to_tree) — and not from
      // ``meta.provenance.excerpt``, which falls back to the claim's own text
      // when no row carried a sentence and would present the claim as its source.
      //
      // The block is the pane's unit of work, and the quote is its subject: the
      // sentence the claim rests on is set larger and in ink, and the metadata
      // that identifies it (source, page, citation id) sits under it rather than
      // above it. The citation id is a control, not a label: it names the
      // sentence's own number and takes the reader to that source in the Sources
      // list, which is the only place the shell can show them the file it came
      // from.
      var index = 0;
      for (var r = 0; r < prov.length; r++) {
        var row = prov[r];
        if (!row || typeof row !== "object") continue;
        var quote = String(row.extracted_quote || row.excerpt || "");
        var srcName = String(row.source_name || "");
        var rowPage = _rowPage(row);
        var citedId = String(row.cited_id || "");
        if (!quote && !srcName && !rowPage && !citedId) continue;
        index++;
        var card = document.createElement("article");
        card.className = "citation";
        if (quote) {
          var q = document.createElement("blockquote");
          q.className = "citation-quote evidence-blockquote";
          q.textContent = quote;
          card.appendChild(q);
        }
        var head = document.createElement("div");
        head.className = "citation-head";
        var ord = document.createElement("span");
        ord.className = "citation-index";
        ord.setAttribute("aria-hidden", "true");
        ord.textContent = String(index);
        head.appendChild(ord);
        var jumpId = _sourceIdForRow(row);
        if (citedId) {
          var idBtn = document.createElement("button");
          idBtn.type = "button";
          idBtn.className = "citation-id";
          // The id the model wrote, shown as the label the paragraph carried:
          // "S1228" is stored, "[S1228]" is what the citation looked like.
          idBtn.textContent = citedId.charAt(0) === "[" ? citedId : "[" + citedId + "]";
          idBtn.title = _citationJumpTitle(citedId, srcName, rowPage);
          _wireSourceJump(idBtn, jumpId, citedId);
          head.appendChild(idBtn);
        }
        if (srcName) {
          var srcBtn = document.createElement("button");
          srcBtn.type = "button";
          srcBtn.className = "citation-source";
          srcBtn.textContent = srcName;
          srcBtn.title = _t("evidence.source.jump", "Show this source in the Sources list");
          _wireSourceJump(srcBtn, jumpId, citedId);
          head.appendChild(srcBtn);
        }
        if (rowPage) {
          var pageEl = document.createElement("span");
          pageEl.className = "citation-page";
          pageEl.textContent = _tf("evidence.page", "page {page}", { page: rowPage });
          head.appendChild(pageEl);
        }
        // This sentence's own verdict, when the check recorded one — the same
        // chip the paragraph wears, one row down. Absent when the check wrote no
        // record for this row: the badge is never filled in with the paragraph's
        // aggregate, which would put a verdict on a sentence nobody read.
        var rec = verdicts[r] || null;
        if (rec) {
          var rv = String(rec.verdict || "").toLowerCase();
          var rowState = _ENTAILMENT_STATE[rv] || null;
          if (rowState) {
            var vBadge = document.createElement("span");
            vBadge.className = "anchor-chip anchor-chip-" + rowState + " citation-verdict";
            vBadge.setAttribute("role", "note");
            vBadge.setAttribute("title", String(rec.reasoning || _anchorStateHint(rowState)));
            vBadge.textContent = _ANCHOR_STATES[rowState].glyph + " " + _anchorStateName(rowState);
            head.appendChild(vBadge);
          }
        }
        card.appendChild(head);
        var extra = [];
        if (row.rule) extra.push(String(row.rule));
        if (row.confidence) extra.push(_t("jdf.provenance.confidence", "Confidence") + " " + row.confidence);
        if (extra.length) {
          var metaEl = document.createElement("div");
          metaEl.className = "citation-meta";
          metaEl.textContent = extra.join(" \u00b7 ");
          card.appendChild(metaEl);
        }
        content.appendChild(card);
      }
      evidenceBodyEl.appendChild(content);
    }
    // The pane's count of what it is about to show. Every number is read off the
    // rows: citations the compile resolved to a sentence, citations it could not
    // resolve (a row with no sentence — it still cites, it just has nothing to
    // quote), the sources those sentences came from, and the pages they were on.
    // Nothing here is inferred from the verdict.
    function _renderCitationBreakdown(content, prov, verdicts) {
      var rows = [];
      for (var i = 0; i < (prov || []).length; i++) {
        var row = prov[i];
        if (row && typeof row === "object") rows.push(row);
      }
      if (!rows.length) return;
      var withQuote = 0, sources = [], pages = [], checked = 0, notCarried = 0;
      rows.forEach(function (row, i) {
        if (String(row.extracted_quote || row.excerpt || "").trim()) withQuote++;
        var name = String(row.source_name || "");
        if (name && sources.indexOf(name) === -1) sources.push(name);
        var page = _rowPage(row);
        if (page && pages.indexOf(page) === -1) pages.push(page);
        var rec = (verdicts || [])[i];
        if (rec) {
          checked++;
          var v = String(rec.verdict || "").toLowerCase();
          if (v === "no" || rec.contradicted) notCarried++;
        }
      });
      var el = document.createElement("div");
      el.className = "evidence-breakdown";
      function part(text) {
        var s = document.createElement("span");
        s.className = "evidence-breakdown-item";
        s.textContent = text;
        el.appendChild(s);
      }
      part(_tf(rows.length === 1 ? "evidence.cited_one" : "evidence.cited_many",
        rows.length === 1 ? "{n} cited sentence" : "{n} cited sentences",
        { n: rows.length }));
      if (withQuote !== rows.length) {
        part(_tf("evidence.cited_unresolved", "{n} not resolved to a sentence",
          { n: rows.length - withQuote }));
      }
      // The check reads its own number of sentences and may record fewer
      // verdicts than the paragraph has citations; both numbers are shown, and
      // the difference is left visible rather than smoothed over.
      if (checked) {
        part(_tf("evidence.checked", "{n} checked against the source", { n: checked }));
      }
      if (notCarried) {
        var alarm = document.createElement("span");
        alarm.className = "evidence-breakdown-item is-alarm";
        alarm.textContent = _tf("evidence.notcarried",
          "{n} the cited sentence does not carry", { n: notCarried });
        el.appendChild(alarm);
      }
      if (sources.length) {
        part(_tf(sources.length === 1 ? "evidence.sources_one" : "evidence.sources_many",
          sources.length === 1 ? "{n} source" : "{n} sources", { n: sources.length }));
      }
      if (pages.length) {
        part(_tf("evidence.pages", "pages {pages}", { pages: pages.join(", ") }));
      }
      content.appendChild(el);
    }
    function renderZ3Panel(node) {
      var el = z3ModeEl; if (!el) return;
      while (el.firstChild) el.removeChild(el.firstChild);
      var z3 = (node.annotations && node.annotations.z3) || [];
      var wrap = document.createElement("div"); wrap.className = "evidence-content";
      if (!z3.length) {
        // An empty list is not a pass. The `verified` frame of the run on screen
        // said whether Math Check ran or was SKIPPED (no labelled figure to
        // check), and the Stages row keeps that verdict (markSkipped); the tab
        // reads the same state, so a check that never ran is named as such
        // rather than implied clean. After a reload the stage state is the
        // document's (markAllStagesDone) and carries no skip reason, so the
        // sentence claims only what the node records: no mismatch on it.
        var p = document.createElement("p"); p.className = "evidence-value";
        if (stageState["Math Check"] === "skipped") {
          p.textContent = _tf("z3.skipped", "Math Check skipped: {reason}",
            { reason: stageReason["Math Check"] || "no metrics to check" });
        } else {
          p.textContent = _t("z3.none", "No mismatches for this node.");
        }
        wrap.appendChild(p);
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
      // and an unauditable node — one with no text field, or no node at all —
      // is disabled and told why, immediately below. No enabled button ever
      // bails silently, and a disabled one is never silent either.
      runBtn.disabled = Boolean(__redhatRunningNodeId) || !_redhatAuditable(node);
      if (__redhatRunningNodeId && !runningHere) {
        runBtn.title = "A Red-Hat run is in progress on another node.";
      }
      runBtn.addEventListener("click", function () {
        if (node && node.id) _runRedhatAudit(node.id);
      });
      wrap.appendChild(runBtn);
      if (!node) {
        var selectEl = document.createElement("p");
        selectEl.className = "evidence-value redhat-unavailable";
        selectEl.textContent = REDHAT_SELECT_FIRST;
        wrap.appendChild(selectEl);
      } else if (node.id && !_redhatAuditable(node)) {
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
          // Phase F3 — what the audit actually did, named on the finding it
          // produced: a claim checked against its source sentence, or a review
          // of the claim alone.
          var modeEl = document.createElement("div");
          modeEl.className = "redhat-finding-mode";
          modeEl.textContent = _redhatFindingMode(node);
          li.appendChild(modeEl);
          if (r.severity) {
            var sevEl = document.createElement("span");
            sevEl.className = "redhat-finding-severity";
            sevEl.textContent = String(r.severity);
            li.appendChild(sevEl);
          }
          var bodyEl = document.createElement("div");
          bodyEl.className = "redhat-finding-body";
          if (text) bodyEl.appendChild(_renderFindingMarkdown(text));
          li.appendChild(bodyEl);
          // A finding a revision answered says so, on the finding, and the
          // paragraph it was raised on no longer shows it any other way: that
          // paragraph has been rewritten, so what remains of the warning is this
          // card. The resolution is the annotation's own record
          // (models/jdf.py:resolve_findings_on_rewrite writes it when a rewrite
          // closes a finding), never a run report about it, and it names both the
          // revision that acted and how — "surgical_rewrite" — because the actor
          // is the person who pressed Apply, whom the app cannot name and must not
          // pretend to. Oldest revisions predate the field and simply have none.
          if (r.status === "resolved" && (r.resolved_by_revision_id || r.resolved_by_version != null)) {
            var byEl = document.createElement("div");
            byEl.className = "redhat-finding-resolved";
            var by = r.resolved_by_version != null
              ? ("v" + r.resolved_by_version)
              : String(r.resolved_by_revision_id || "");
            var how = String(r.resolved_by_mutation_type || "").trim();
            byEl.textContent = _tf("redhat.finding.resolved", "Remediated by {by}{how}", {
              by: by,
              how: how ? " \u00b7 " + how : "",
            });
            li.appendChild(byEl);
          }
          // A finding is already an instruction: click seeds the existing
          // rephrase editor rather than opening a second rewrite path.
          if (text) {
            var applyFinding = function () {
              if (!node || !node.id) return;
              // The paragraph the finding names, when it is still on screen;
              // the node it is placed on otherwise (findings written before
              // `node_id` existed carry only their placement).
              var target = _nodeWrapper(r.node_id) ? r.node_id : node.id;
              setShell("ui.selection.nodeId", target);
              _attachNodeRephrase(target, text);
              _locateNode(target);
            };
            li.title = "Rephrase this paragraph with this finding";
            li.addEventListener("click", applyFinding);
            // The same action as a visible control: a row that is only
            // clickable is an affordance nobody is told about, and the About
            // copy (Step 8) says "Click Apply". One handler, two entry points.
            var applyBtn = document.createElement("button");
            applyBtn.type = "button";
            applyBtn.className = "evidence-action redhat-finding-apply";
            applyBtn.textContent = _t("redhat.finding.apply", "Apply");
            applyBtn.title = li.title;
            applyBtn.addEventListener("click", function (ev) {
              ev.stopPropagation();   // the row's own handler would fire twice
              applyFinding();
            });
            li.appendChild(applyBtn);
          }
          list.appendChild(li);
        });
        wrap.appendChild(list);
      } else if (node) {
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
      // Phase F2 — which pane-mode is shown is this function's decision; HOW it
      // is laid out is shell.css's (#pane-right is the three-row grid and
      // .pane-mode is its third row). Writing "block" here would pin the inline
      // value over that rule and collapse the body back to its content height,
      // so the visible pane reverts to the stylesheet's flex column.
      if (inspectorCompareActive) {
        if (rightModeToggleEl) rightModeToggleEl.style.display = "none";
        if (insp) insp.style.display = "none";
        if (cmp) cmp.style.display = "";
        if (compareToggleEl) compareToggleEl.classList.add("is-active");
        return;
      }
      if (rightModeToggleEl) rightModeToggleEl.style.display = "";
      if (compareToggleEl) compareToggleEl.classList.remove("is-active");
      if (insp) insp.style.display = "";
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
      // z3/cite chip. A payload that belongs to another node paints nothing:
      // it is stale by definition.
      var sel = SHELL.ui.selection || {};
      var ev = sel.evidence;
      var opts = (ev && ev.nodeId === node.id && ev.kind !== "confidence")
        ? { drawer: ev }
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
    // and clears it; this builds the drawer body into the pane it was given, so
    // what it appends stays on screen instead of being wiped by the dispatcher's
    // repaint in the same click.
    function renderEvidenceDrawer(ev) {
      if (!evidenceBodyEl) return;
      var header = document.createElement("div");
      header.className = "evidence-header evidence-verdict";
      header.textContent = _t(ev.kind === "z3" ? "evidence.drawer.z3" : "evidence.drawer.cite",
        ev.kind === "z3" ? "Math check" : "Cited sentence") +
        " \u00b7 " + _t("evidence.drawer.node", "node") + " " + ev.nodeId;
      evidenceBodyEl.appendChild(header);
      var content = document.createElement("div");
      content.className = "evidence-content";
      // The three field labels, in the drawer's own vocabulary — the same words
      // the paragraph panel uses for the same things, from the same keys.
      function drawerField(label, value) {
        var s = String(value == null ? "" : value);
        if (!s) return;
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
        content.appendChild(f);
      }
      if (ev.kind === "z3") {
        var statusField = document.createElement("div");
        statusField.className = "evidence-field";
        var statusLabel = document.createElement("div");
        statusLabel.className = "evidence-label";
        statusLabel.textContent = _t("evidence.field.status", "Status");
        var statusValue = document.createElement("div");
        statusValue.className = "evidence-value";
        var statusBadge = document.createElement("span");
        statusBadge.className = "evidence-status " + ev.data.status;
        statusBadge.textContent = ev.data.status || "";
        statusValue.appendChild(statusBadge);
        statusField.appendChild(statusLabel);
        statusField.appendChild(statusValue);
        content.appendChild(statusField);
        drawerField(_t("evidence.field.canonical_key", "Canonical key"), ev.data.canonical_key);
        drawerField(_t("evidence.field.message", "Message"), ev.data.message);
      } else if (ev.kind === "cite") {
        // The same unit the paragraph panel draws, one row deep: the sentence the
        // claim cites is the block's subject, and the citation id under it is the
        // control that opens its source. A chip is a citation, so it gets the
        // citation treatment rather than three labelled fields.
        var card = document.createElement("article");
        card.className = "citation";
        if (ev.data.extracted_quote) {
          var quoteEl = document.createElement("blockquote");
          quoteEl.className = "citation-quote evidence-blockquote";
          quoteEl.textContent = ev.data.extracted_quote;
          card.appendChild(quoteEl);
        }
        var head = document.createElement("div");
        head.className = "citation-head";
        var cited = String(ev.data.cited_id || "");
        var sourceId = _sourceIdForRow(ev.data);
        if (cited) {
          var idBtn = document.createElement("button");
          idBtn.type = "button";
          idBtn.className = "citation-id";
          idBtn.textContent = cited.charAt(0) === "[" ? cited : "[" + cited + "]";
          idBtn.title = _citationJumpTitle(cited, String(ev.data.source_name || ""),
                                           _rowPage(ev.data));
          _wireSourceJump(idBtn, sourceId, cited);
          head.appendChild(idBtn);
        }
        if (ev.data.source_name) {
          var srcBtn = document.createElement("button");
          srcBtn.type = "button";
          srcBtn.className = "citation-source";
          srcBtn.textContent = ev.data.source_name;
          srcBtn.title = _t("evidence.source.jump", "Show this source in the Sources list");
          _wireSourceJump(srcBtn, sourceId, cited);
          head.appendChild(srcBtn);
        }
        var drawerPage = _rowPage(ev.data);
        if (drawerPage) {
          var pageEl = document.createElement("span");
          pageEl.className = "citation-page";
          pageEl.textContent = _tf("evidence.page", "page {page}", { page: drawerPage });
          head.appendChild(pageEl);
        }
        if (head.childNodes.length) card.appendChild(head);
        if (card.childNodes.length) content.appendChild(card);
      }
      evidenceBodyEl.appendChild(content);
    }

    // ---------------------------------------------------------------
    // 2B — the unanchored drawer; 2C — the allowlisted fetch that can ground it.
    //
    // The unanchored state used to say "No source matched this paragraph" and
    // stop. It now says four things: that the claim is not grounded in any
    // uploaded source, what is missing (one sentence from the model), what would
    // ground it (the document category), and the two ways to act on that — upload
    // a document, or search the authoritative domains and fetch one page.
    //
    // Two rules this code keeps:
    //   * a failed gap call shows the first line and the upload button, and
    //     nothing else. No invented reason, no placeholder analysis.
    //   * a fetched page is only ever *matched* against the claim. Nothing here
    //     rewrites the paragraph, and nothing here paints it verified — the
    //     anchoring gate on the server decides, and the counters report it.
    // ---------------------------------------------------------------
    var __gapToken = 0;
    var __gapCache = {};          // nodeId -> analysis | null (null = it failed)
    var __retrievalCards = {};    // nodeId -> the last card set for that node
    var __fetchNotes = {};        // nodeId -> the last fetch's outcome line
    var __retrievalBusy = false;

    function _gapAnalysisFor(nodeId) {
      if (Object.prototype.hasOwnProperty.call(__gapCache, nodeId)) {
        return Promise.resolve(__gapCache[nodeId]);
      }
      return ensureProjectId()
        .then(function (pid) {
          return jsonPost("/api/projects/" + encodeURIComponent(pid) +
                          "/nodes/" + encodeURIComponent(nodeId) + "/gap-analysis", {});
        })
        .then(function (resp) { return resp.json().catch(function () { return {}; }); })
        .then(function (j) {
          // The three lines or nothing: a payload missing one of them is treated
          // as the failure it is, so a partial answer is never rendered.
          var analysis = (j && j.ok && j.missing && j.grounded_by && j.search_query) ? j : null;
          __gapCache[nodeId] = analysis;
          return analysis;
        })
        .catch(function () {
          __gapCache[nodeId] = null;
          return null;
        });
    }

    function _renderUnanchoredDrawer(node) {
      // The same header shape the panel uses: the state's glyph and name first,
      // then the sentence that says what the state means. The sentence is the
      // drawer's own, and it says the plain thing — the claim is not traceable to
      // any uploaded source — rather than implying a check that failed or a
      // citation that was lost. `node.provenance` is empty for this paragraph;
      // nothing was matched, and that is the whole finding.
      var header = document.createElement("div");
      header.className = "evidence-header evidence-verdict gap-claim-header anchor-unanchored";
      var badge = document.createElement("span");
      badge.className = "evidence-verdict-badge";
      badge.setAttribute("aria-hidden", "true");
      badge.textContent = _ANCHOR_STATES.unanchored.glyph;
      var headText = document.createElement("span");
      headText.className = "evidence-verdict-text";
      var stateName = document.createElement("span");
      stateName.className = "evidence-verdict-state";
      stateName.textContent = _anchorStateName("unanchored");
      var stateLabel = document.createElement("span");
      stateLabel.className = "evidence-verdict-label";
      stateLabel.textContent = _t("gap.heading",
        "No sentence in the uploaded sources could be traced to this claim.");
      headText.appendChild(stateName);
      headText.appendChild(stateLabel);
      header.appendChild(badge);
      header.appendChild(headText);
      evidenceBodyEl.appendChild(header);
      var content = document.createElement("div");
      content.className = "evidence-content gap-content";
      evidenceBodyEl.appendChild(content);

      // A verdict recorded for a paragraph with no citation is still a fact about
      // it: name it, rather than let an empty pane imply no check ever ran.
      // `_entailmentLabel` is the pane's one spelling of a verdict.
      if (_entailmentFor(node, null)) {
        var verdictEl = document.createElement("p");
        verdictEl.className = "evidence-value gap-verdict";
        verdictEl.textContent = _t("gap.verdict", "Source check") + ": " +
          _entailmentLabel(node, null, "");
        content.appendChild(verdictEl);
      }

      var token = ++__gapToken;
      var nodeId = String((node && node.id) || "");
      var status = document.createElement("p");
      status.className = "evidence-value gap-status";
      status.textContent = _t("gap.reading", "Reading why this claim is unanchored\u2026");
      content.appendChild(status);

      function stillHere() {
        return token === __gapToken &&
          String((SHELL.ui.selection || {}).nodeId || "") === nodeId;
      }
      function uploadButton() {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "evidence-action gap-upload";
        btn.textContent = _t("gap.action.upload", "Upload a document instead");
        btn.addEventListener("click", function () {
          leftGroupSetTab("sources");
          var input = document.getElementById("source-file-input");
          if (input) input.click();
        });
        return btn;
      }
      function note() {
        var stored = __fetchNotes[nodeId];
        if (!stored) return null;
        var el = document.createElement("p");
        el.className = "evidence-value gap-fetch-note" + (stored.ok ? "" : " gap-error");
        el.textContent = stored.text;
        return el;
      }

      _gapAnalysisFor(nodeId).then(function (analysis) {
        if (!stillHere()) return;
        if (status.parentNode) status.parentNode.removeChild(status);
        var actions = document.createElement("div");
        actions.className = "gap-actions";
        if (analysis) {
          _renderGapLines(content, analysis);
        }
        var upload = uploadButton();
        if (!analysis) {
          // Failure path: the first line and the upload button. Nothing else,
          // because a reason the model did not give is not a reason.
          actions.appendChild(upload);
          content.appendChild(actions);
          var outcome = note();
          if (outcome) content.appendChild(outcome);
          return;
        }
        var searchBtn = document.createElement("button");
        searchBtn.type = "button";
        searchBtn.className = "evidence-action primary gap-search";
        searchBtn.textContent = _t("gap.action.search", "Search authoritative sources");
        searchBtn.addEventListener("click", function () {
          _runAuthoritativeSearch(nodeId, analysis.search_query, content, searchBtn);
        });
        actions.appendChild(searchBtn);
        actions.appendChild(upload);
        content.appendChild(actions);
        var stored = note();
        if (stored) content.appendChild(stored);
        if (__retrievalCards[nodeId]) {
          _renderRetrievalCards(nodeId, __retrievalCards[nodeId], content);
        }
      });
    }

    // The model's three lines, labelled with the drawer's own questions. Nothing
    // is paraphrased and nothing is added; the search query is shown as the text
    // the search runs, so the reader can see what was asked on their behalf.
    function _renderGapLines(content, analysis) {
      function field(label, value, cls) {
        var f = document.createElement("div");
        f.className = "evidence-field gap-field" + (cls ? " " + cls : "");
        var l = document.createElement("div");
        l.className = "evidence-label";
        l.textContent = label;
        var v = document.createElement("div");
        v.className = "evidence-value";
        v.textContent = String(value || "");
        f.appendChild(l);
        f.appendChild(v);
        content.appendChild(f);
      }
      field(_t("gap.field.missing", "What is missing"), analysis.missing);
      field(_t("gap.field.grounded_by", "What would ground it"), analysis.grounded_by);
      field(_t("gap.field.query", "Search query"), analysis.search_query, "gap-query");
    }

    function _runAuthoritativeSearch(nodeId, query, content, button) {
      var label = _t("gap.action.search", "Search authoritative sources");
      if (button) { button.disabled = true; button.textContent = _t("gap.action.searching", "Searching\u2026"); }
      var results = content.querySelector(".gap-results");
      if (!results) {
        results = document.createElement("div");
        results.className = "gap-results";
        content.appendChild(results);
      }
      while (results.firstChild) results.removeChild(results.firstChild);
      var pending = document.createElement("p");
      pending.className = "empty-hint";
      pending.textContent = _t("gap.search.running", "Searching the authoritative domains\u2026");
      results.appendChild(pending);

      function restore() {
        if (button) { button.disabled = false; button.textContent = label; }
      }
      function fail(message) {
        restore();
        while (results.firstChild) results.removeChild(results.firstChild);
        var err = document.createElement("p");
        err.className = "evidence-value gap-error";
        err.textContent = message;
        results.appendChild(err);
      }

      ensureProjectId()
        .then(function (pid) {
          return jsonPost("/api/projects/" + encodeURIComponent(pid) + "/nodes/" +
                          encodeURIComponent(nodeId) + "/retrieval/search", { query: query });
        })
        .then(function (resp) {
          return resp.json().catch(function () { return {}; })
            .then(function (j) { return { status: resp.status, j: j || {} }; });
        })
        .then(function (r) {
          restore();
          if (!r.j || !r.j.ok) {
            fail((r.j && r.j.error) || _tf("gap.search.refused",
              "The search was refused (HTTP {status}).", { status: r.status }));
            return;
          }
          __retrievalCards[nodeId] = r.j;
          while (results.firstChild) results.removeChild(results.firstChild);
          if (!r.j.cards || !r.j.cards.length) {
            var none = document.createElement("p");
            none.className = "empty-hint";
            none.textContent = _t("gap.search.none", "No allowlisted source came back for this query.");
            results.appendChild(none);
            return;
          }
          _renderCardsInto(results, nodeId, r.j.cards);
        })
        .catch(function (err) {
          fail(_tf("gap.search.failed", "The search failed: {error}",
                 { error: String((err && err.message) || err) }));
        });
    }

    // Three cards at most, the host named and never the full URL: the decision
    // the reader is making is "is this an authority for this claim", and the host
    // is what answers it. The URL travels only in the fetch request.
    function _renderRetrievalCards(nodeId, result, content) {
      var results = content.querySelector(".gap-results");
      if (!results) {
        results = document.createElement("div");
        results.className = "gap-results";
        content.appendChild(results);
      }
      _renderCardsInto(results, nodeId, result.cards || []);
    }

    function _renderCardsInto(host, nodeId, cards) {
      cards.forEach(function (card) {
        var el = document.createElement("div");
        el.className = "gap-card";
        el.setAttribute("data-host", String(card.host || ""));
        var hostEl = document.createElement("div");
        hostEl.className = "gap-card-host";
        hostEl.textContent = String(card.host || "");
        var titleEl = document.createElement("div");
        titleEl.className = "gap-card-title";
        titleEl.textContent = String(card.title || card.host || "");
        var snipEl = document.createElement("div");
        snipEl.className = "gap-card-snippet";
        snipEl.textContent = String(card.snippet || "");
        var row = document.createElement("div");
        row.className = "gap-card-actions";
        var fetchBtn = document.createElement("button");
        fetchBtn.type = "button";
        fetchBtn.className = "evidence-action primary gap-fetch";
        fetchBtn.textContent = _t("gap.card.fetch", "Fetch and verify");
        fetchBtn.addEventListener("click", function () {
          _fetchAndVerify(nodeId, card, el, row);
        });
        var rejectBtn = document.createElement("button");
        rejectBtn.type = "button";
        rejectBtn.className = "evidence-action gap-reject";
        rejectBtn.textContent = _t("gap.card.reject", "Reject");
        rejectBtn.addEventListener("click", function () { _rejectResult(nodeId, card, el); });
        row.appendChild(fetchBtn);
        row.appendChild(rejectBtn);
        el.appendChild(hostEl);
        el.appendChild(titleEl);
        el.appendChild(snipEl);
        el.appendChild(row);
        host.appendChild(el);
      });
    }

    function _fetchAndVerify(nodeId, card, cardEl, row) {
      if (__retrievalBusy) return;
      __retrievalBusy = true;
      var btns = cardEl.querySelectorAll("button");
      for (var i = 0; i < btns.length; i++) btns[i].disabled = true;
      var state = document.createElement("p");
      state.className = "empty-hint gap-fetch-state";
      state.textContent = _tf("gap.fetch.fetching", "Fetching {host}\u2026", { host: card.host });
      cardEl.appendChild(state);

      function release() {
        __retrievalBusy = false;
        for (var i = 0; i < btns.length; i++) btns[i].disabled = false;
      }
      ensureProjectId()
        .then(function (pid) {
          return jsonPost("/api/projects/" + encodeURIComponent(pid) + "/nodes/" +
                          encodeURIComponent(nodeId) + "/retrieval/fetch",
                          { url: card.url, host: card.host, title: card.title });
        })
        .then(function (resp) {
          return resp.json().catch(function () { return {}; })
            .then(function (j) { return { status: resp.status, j: j || {} }; });
        })
        .then(function (r) {
          release();
          if (!r.j || !r.j.ok) {
            var refused = (r.j && r.j.error) ||
              _tf("gap.fetch.refused", "The fetch was refused (HTTP {status}).",
                  { status: r.status });
            state.className = "evidence-value gap-error";
            state.textContent = refused;
            __fetchNotes[nodeId] = { ok: false, text: refused };
            return;
          }
          var j = r.j;
          var host = String((j.source && j.source.fetched_url) || card.host || "");
          var when = String((j.page && j.page.retrieved_on) || "");
          var verdict = String((j.entailment && j.entailment.verdict) || "");
          var outcome = !j.anchored
            ? _t("gap.outcome.unanchored", "this page did not ground the claim")
            : (verdict === "yes"
              ? _t("gap.outcome.yes", "the claim now cites this page, and the source check verified it")
              : (verdict === "partial"
                ? _t("gap.outcome.partial", "the claim cites this page; the source supports it only in part")
                : (verdict === "no"
                  ? _t("gap.outcome.no", "the claim cites this page; the source check did not confirm it")
                  : (verdict === "unverified"
                    ? _t("gap.outcome.unverified", "the claim cites this page; the source check could not be made")
                    : _t("gap.outcome.cited", "the claim now cites this page")))));
          var retrieved = when
            ? _tf("gap.fetch.retrieved", " (retrieved {when})", { when: when })
            : "";
          var text = _tf("gap.fetch.done", "Fetched: {host}{retrieved} \u00b7 {outcome}",
                         { host: host, retrieved: retrieved, outcome: outcome });
          if (j.page && j.page.instruction_like) {
            // The same scan an upload gets, reported the same way: the page is a
            // source to quote, and the reader is told it also contains
            // instruction-like text.
            text += _t("gap.fetch.instruction_like",
              " \u00b7 contains instruction-like content (treated as data)");
          }
          __fetchNotes[nodeId] = { ok: true, text: text };
          state.className = "evidence-value gap-fetch-note";
          state.textContent = text;
          // Adopt the server's tree: the gate ran there, and the counters have to
          // report what it decided rather than what this pane hoped for.
          _applyFetchedDocument(j.document, j.stats);
        })
        .catch(function (err) {
          release();
          var message = "The fetch failed: " + String((err && err.message) || err);
          state.className = "evidence-value gap-error";
          state.textContent = message;
          __fetchNotes[nodeId] = { ok: false, text: message };
        });
    }

    function _rejectResult(nodeId, card, cardEl) {
      // A rejection is a decision about one card, not about the claim: the claim
      // stays unanchored, the audit trail records who was offered it, and the
      // search stays available.
      __fetchNotes[nodeId] = { ok: true, text: _tf("gap.reject.note", "Rejected {host} \u00b7 logged", { host: card.host }) };
      var stored = __retrievalCards[nodeId];
      if (stored && stored.cards) {
        stored.cards = stored.cards.filter(function (c) { return c.url !== card.url; });
      }
      if (cardEl && cardEl.parentNode) {
        var results = cardEl.parentNode;
        cardEl.parentNode.removeChild(cardEl);
        // The card goes and the decision stays on screen: a rejected result that
        // simply vanishes reads as a failed click.
        var note = document.createElement("p");
        note.className = "empty-hint gap-reject-note";
        note.textContent = _tf("gap.reject.note", "Rejected {host} \u00b7 logged", { host: card.host });
        results.appendChild(note);
      }
      ensureProjectId()
        .then(function (pid) {
          return jsonPost("/api/projects/" + encodeURIComponent(pid) + "/nodes/" +
                          encodeURIComponent(nodeId) + "/retrieval/reject",
                          { url: card.url, host: card.host, title: card.title,
                            reason: "rejected in the evidence drawer" });
        })
        .catch(function () { /* the local removal stands; the log is best effort */ });
    }

    // The fetch route returns the tree the gate just produced. Everything the
    // document surface shows is re-derived from it — chips, counters, sources —
    // so the pane cannot keep a state the server did not produce.
    function _applyFetchedDocument(doc, stats) {
      if (!doc || !Array.isArray(doc.body)) return;
      setShell("document.mode", "ready");
      setShell("document.current", doc);
      renderJdfDocument(doc);
      applyConfidenceSpans(doc);
      applyAnchorStates(doc);
      addEvidenceChips(doc);
      _renderCounters(stats || null, _derivedCounts(doc));
      _loadProjectSourceList(_sourceProjectId());
      _applyRightView();
    }

    // renderEvidenceFooter and performGrounding are gone with the /ground path.
    // The footer existed to carry "Ground with sources", which searched the web,
    // had a model rewrite the paragraph from the snippets and persisted that —
    // no source row, no gate re-run — so a claim it "grounded" was supported by
    // nothing; it had only stopped looking unanchored. That contradicts the
    // product's disclosure model. Grounding an unanchored claim is the 2C drawer
    // (search the authoritative domains, fetch, re-run the gate) or Surgical
    // Edit: user-initiated, verifiable, versioned.

    // Revise with LLM / Dismiss were removed with the Red-Hat drawer route.
    // Revise's only terminal state was alert("Accept revision not fully
    // wired…"); the wired rewrite path is _attachNodeRephrase. Dismiss made
    // no request at all and mutated the drawer's copy of the finding, so it
    // implied a closable finding that nothing could close.

    // Send is only meaningful with a non-empty ask AND a source to ground it in:
    // a compile with no source attached has nothing to ground against, and the
    // server refuses it before its first stage (routers/draft.py, reason
    // `no_source_attached`). The dock does not offer a run that cannot be
    // grounded, and the hint is the button's own label — disabled is the state,
    // the label says what it waits for.
    var NO_SOURCE_HINT = "Add a source to enable the compile";
    function _syncDockSubmit() {
      if (!submit || !text) return;
      // §5 row 3: a draft in flight is the dock's loading state. The class
      // and aria-busy are the only surface runInProgress has ever had.
      submit.classList.toggle("is-running", Boolean(runInProgress));
      if (runInProgress) submit.setAttribute("aria-busy", "true");
      else               submit.removeAttribute("aria-busy");
      var hasSource = Boolean((SHELL.sources || []).length);
      submit.disabled = !String(text.value || "").trim() || !hasSource;
      if (hasSource) {
        submit.removeAttribute("title");
        submit.setAttribute("aria-label", "Submit");
      } else {
        submit.title = NO_SOURCE_HINT;
        submit.setAttribute("aria-label", NO_SOURCE_HINT);
      }
    }
    _syncDockSubmitFn = _syncDockSubmit;
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
