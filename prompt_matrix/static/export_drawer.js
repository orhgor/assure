/**
 * Founder workbench — Export drawer with save-before-PDF flow.
 */
(function (global) {
  "use strict";

  var pdfBusy = false;

  function $(id) {
    return document.getElementById(id);
  }

  function translate(key, fallback, params) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, params || {});
    }
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback || key;
  }

  function toast(message, kind) {
    if (global.AssureToast && typeof global.AssureToast.show === "function") {
      global.AssureToast.show(message, kind || "info");
    }
  }

  function projectId() {
    if (global.AssureFounderMode && typeof global.AssureFounderMode.getWorkspaceId === "function") {
      return global.AssureFounderMode.getWorkspaceId();
    }
    return global.__ASSURE_PROJECT_ID__ || "founder";
  }

  function panel() {
    return $("drawer-export");
  }

  function currentJdfDocument() {
    var api = global.AssureTiptapEditor;
    var ed = api && api.getEditor && api.getEditor();
    if (!api || !ed || !api.tiptapToJdf) return null;
    var base =
      (global.AssureFounderDraft && global.AssureFounderDraft.getDraftTree && global.AssureFounderDraft.getDraftTree()) ||
      {
        document_id: "doc-" + projectId(),
        meta: { project_id: projectId() },
        truth_ledger: {},
        body: [],
      };
    try {
      return api.tiptapToJdf(ed.getJSON(), base);
    } catch (_) {
      return null;
    }
  }

  function downloadJdf() {
    var doc = currentJdfDocument();
    if (!doc) {
      toast(translate("founder.export.jdf_error", "Could not read the draft."), "error");
      return;
    }
    var blob = new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "assure_draft.jdf";
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function setPdfBusy(on) {
    pdfBusy = !!on;
    var btn = panel() && panel().querySelector(".drawer-export-pdf");
    var spin = panel() && panel().querySelector(".drawer-export-pdf-spinner");
    if (btn) btn.disabled = pdfBusy;
    if (spin) spin.hidden = !pdfBusy;
  }

  function exportVerifiedPdf() {
    if (pdfBusy) return;
    var doc = currentJdfDocument();
    if (!doc) {
      toast(translate("founder.export.pdf_error", "Could not read the draft."), "error");
      return;
    }
    setPdfBusy(true);
    var pid = projectId();
    fetch("/api/projects/" + encodeURIComponent(pid) + "/draft", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: doc }),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok && data.ok !== false, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok) {
          toast(
            (result.data && result.data.error) ||
              translate("founder.export.save_error", "Could not save draft before export."),
            "error"
          );
          return;
        }
        global.location.href = "/api/projects/" + encodeURIComponent(pid) + "/export?format=pdf";
      })
      .catch(function () {
        toast(translate("founder.export.save_error", "Could not save draft before export."), "error");
      })
      .finally(function () {
        setPdfBusy(false);
      });
  }

  function renderShell() {
    var el = panel();
    if (!el || el.dataset.bound === "1") return;
    el.dataset.bound = "1";
    el.innerHTML =
      '<p class="hint drawer-export-lead">' +
      translate(
        "founder.export.drawer_lead",
        "Download your working draft or generate a verified PDF from the current editor state."
      ) +
      "</p>" +
      '<button type="button" class="btn btn-outline btn-sm drawer-export-jdf">' +
      translate("founder.export.jdf_btn", "Download Working File (.jdf)") +
      "</button>" +
      '<button type="button" class="btn btn-primary btn-sm drawer-export-pdf">' +
      translate("founder.export.pdf_btn", "Generate Verified Dossier (.pdf)") +
      "</button>" +
      '<span class="drawer-export-pdf-spinner founder-drawer-loading hint" hidden aria-live="polite">' +
      translate("founder.export.pdf_saving", "Saving draft…") +
      "</span>";
    el.querySelector(".drawer-export-jdf").addEventListener("click", downloadJdf);
    el.querySelector(".drawer-export-pdf").addEventListener("click", exportVerifiedPdf);
  }

  function activate() {
    renderShell();
  }

  function openExportDrawer() {
    if (global.AssureWorkbenchPanes && typeof global.AssureWorkbenchPanes.openDrawer === "function") {
      global.AssureWorkbenchPanes.openDrawer("export");
      return;
    }
    activate();
  }

  function init() {
    var btn = $("btn-export-dossier");
    if (btn) {
      btn.addEventListener("click", function (e) {
        if (
          document.body.classList.contains("founder-workbench") &&
          !document.body.classList.contains("legacy-workbench")
        ) {
          e.preventDefault();
          openExportDrawer();
        }
      });
    }
  }

  global.AssureExportDrawer = {
    activate: activate,
    open: openExportDrawer,
    exportVerifiedPdf: exportVerifiedPdf,
    downloadJdf: downloadJdf,
    init: init,
  };
  document.addEventListener("DOMContentLoaded", init);
})(window);
