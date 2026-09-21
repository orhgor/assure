/**
 * ProjectFileManager — per-project source.md + last compiled JDF AST.
 * Active project path lives in localStorage so a refresh lands on the same file
 * with the previous compile still on the canvas.
 */
(function (global) {
  "use strict";

  var ACTIVE_KEY = "assure_active_project";
  var SOURCE_TIMER_MS = 800;
  var _sourceTimer = null;

  function $(id) {
    return global.document.getElementById(id);
  }

  function currentProjectId() {
    return global.__ASSURE_PROJECT_ID__ || "default";
  }

  function remember(projectId) {
    var id = projectId || "default";
    try {
      global.localStorage.setItem(ACTIVE_KEY, id);
    } catch (_) {}
    return id;
  }

  function restoreActiveId() {
    try {
      var url = new URL(global.location.href);
      var fromUrl = url.searchParams.get("project");
      if (fromUrl) {
        remember(fromUrl);
        return fromUrl;
      }
    } catch (_) {}
    try {
      return global.localStorage.getItem(ACTIVE_KEY) || "default";
    } catch (_) {
      return "default";
    }
  }

  function applySource(sourceMd) {
    var el = $("generate-intent");
    if (!el) return;
    el.value = sourceMd || "";
  }

  function applyCompiledToCanvas(manifest) {
    var jdf = global.__assureJdf;
    if (!jdf || !manifest) return false;
    var nodes = manifest.lastCompiledOutput;
    if (!Array.isArray(nodes) || !nodes.length) return false;
    var hasBody = jdf.tree && Array.isArray(jdf.tree.body) && jdf.tree.body.length;
    if (hasBody) return false;
    jdf.tree = {
      document_id: manifest.document_id || jdf.tree.document_id || "doc-" + currentProjectId(),
      meta: Object.assign({}, jdf.tree.meta || {}, manifest.meta || {}),
      truth_ledger: manifest.truth_ledger || jdf.tree.truth_ledger || {},
      body: nodes,
    };
    var spans = (jdf.tree.meta && jdf.tree.meta.confidenceSpans) || [];
    if (typeof jdf.setConfidenceSpans === "function") {
      jdf.setConfidenceSpans(spans, { render: false });
    } else {
      jdf.confidenceSpans = spans;
    }
    if (typeof jdf.render === "function") jdf.render();
    if (typeof jdf._setDirty === "function") jdf._setDirty(false);
    return true;
  }

  function fetchFiles(projectId) {
    var id = projectId || currentProjectId();
    return fetch("/api/projects/" + encodeURIComponent(id) + "/files", {
      credentials: "same-origin",
    }).then(function (r) {
      return r.json();
    });
  }

  function hydrate(projectId) {
    var id = projectId || currentProjectId();
    remember(id);
    return fetchFiles(id).then(function (data) {
      if (!data || !data.ok) return data;
      applySource(data.source_md || "");
      if (!(data.source_md || "").trim() && global.AssureUnsaved && typeof global.AssureUnsaved.restoreDraftIfEmpty === "function") {
        global.AssureUnsaved.restoreDraftIfEmpty();
      }
      var restored = applyCompiledToCanvas(data.manifest || {});
      if (restored && global.AssureToast) {
        var msg = "Restored the last compiled document.";
        if (typeof global.__assureT === "function") {
          msg = global.__assureT("projects.files.restored", msg);
        }
        global.AssureToast.show(msg, "info");
      }
      return data;
    });
  }

  function saveSource(sourceMd, projectId) {
    var id = projectId || currentProjectId();
    return fetch("/api/projects/" + encodeURIComponent(id) + "/files", {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_md: sourceMd || "" }),
    }).then(function (r) {
      return r.json();
    });
  }

  function saveSourceDebounced() {
    var el = $("generate-intent");
    var text = el ? el.value : "";
    if (_sourceTimer) global.clearTimeout(_sourceTimer);
    _sourceTimer = global.setTimeout(function () {
      _sourceTimer = null;
      saveSource(text).catch(function () {});
    }, SOURCE_TIMER_MS);
  }

  function saveCompiled(documentOrNodes, projectId) {
    var id = projectId || currentProjectId();
    var payload = { lastCompiledOutput: documentOrNodes };
    if (documentOrNodes && !Array.isArray(documentOrNodes) && typeof documentOrNodes === "object") {
      payload.lastCompiledOutput = documentOrNodes.body || documentOrNodes.lastCompiledOutput || [];
      payload.document = documentOrNodes;
    }
    return fetch("/api/projects/" + encodeURIComponent(id) + "/files", {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (r) {
      return r.json();
    });
  }

  function bindSourceInput() {
    var el = $("generate-intent");
    if (!el || el.dataset.fileManagerBound === "1") return;
    el.dataset.fileManagerBound = "1";
    el.addEventListener("input", saveSourceDebounced);
  }

  var ProjectFileManager = {
    ACTIVE_KEY: ACTIVE_KEY,
    remember: remember,
    restoreActiveId: restoreActiveId,
    fetchFiles: fetchFiles,
    hydrate: hydrate,
    applySource: applySource,
    applyCompiledToCanvas: applyCompiledToCanvas,
    saveSource: saveSource,
    saveSourceDebounced: saveSourceDebounced,
    saveCompiled: saveCompiled,
    bindSourceInput: bindSourceInput,
  };

  global.AssureProjectFileManager = ProjectFileManager;
})(typeof window !== "undefined" ? window : this);
