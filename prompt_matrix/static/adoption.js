(function (global) {
  "use strict";

  function $(id) {
    return document.getElementById(id);
  }

  function t(key, fallback, vars) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, vars || {});
    }
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    var s = fallback || key;
    if (vars) {
      Object.keys(vars).forEach(function (k) {
        s = s.replace(new RegExp("\\{" + k + "\\}", "g"), String(vars[k]));
      });
    }
    return s;
  }

  function projectId() {
    return global.__ASSURE_PROJECT_ID__ || "default";
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  var AssureAdoption = {
    lockCandidates: [],

    init: function () {
      this.bindLockInference();
      this.bindCitationDrawer();
      this.bindAuditConsole();
    },

    bindLockInference: function () {
      var self = this;
      var fileInput = $("file");
      var taskInput = $("task");
      var runBtn = $("infer-locks-btn");
      var acceptBtn = $("accept-all-locks");

      var trigger = function () {
        var text = "";
        if (global.state && global.state.fileText) text = global.state.fileText;
        if (!text && taskInput) text = taskInput.value || "";
        if (text.trim().length >= 20) self.runLockInference(text);
      };

      if (fileInput) {
        fileInput.addEventListener("change", function () {
          window.setTimeout(trigger, 400);
        });
      }
      if (runBtn) runBtn.addEventListener("click", trigger);
      if (acceptBtn) {
        acceptBtn.addEventListener("click", function () {
          self.acceptAllLocks();
        });
      }
    },

    runLockInference: function (text) {
      var self = this;
      var banner = $("lock-inference-banner");
      var list = $("lock-checklist");
      var countEl = $("lock-count");
      if (!banner || !list) return;

      banner.hidden = false;
      list.innerHTML = "<p class=\"hint\">" + escapeHtml(t("adoption.locks.scanning", "Scanning substrate…")) + "</p>";

      fetch("/api/projects/" + encodeURIComponent(projectId()) + "/infer-locks", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: text,
          pdf_base64: (global.state && global.state.uploadPdfBase64) || null,
          filename: (global.state && global.state.uploadMeta && global.state.uploadMeta.filename) || null,
        }),
      })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok, data: data };
          });
        })
        .then(function (result) {
          if (!result.ok) {
            throw new Error((result.data && result.data.error) || t("adoption.locks.failed", "Lock inference failed."));
          }
          self.lockCandidates = result.data.candidates || [];
          self.renderLockChecklist();
          if (countEl) countEl.textContent = String(self.lockCandidates.length);
          if (result.data.has_visual_content && global.AssureToast) {
            global.AssureToast.show(
              t("adoption.locks.vision", "Visual PDF detected — using Gemini for chart extraction."),
              "info"
            );
          }
        })
        .catch(function (err) {
          list.innerHTML =
            "<p class=\"hint bad\">" + escapeHtml(String(err.message || err)) + "</p>";
          if (countEl) countEl.textContent = "0";
        });
    },

    renderLockChecklist: function () {
      var list = $("lock-checklist");
      if (!list) return;
      if (!this.lockCandidates.length) {
        list.innerHTML =
          "<p class=\"hint\">" + escapeHtml(t("adoption.locks.none", "No high-confidence metrics found.")) + "</p>";
        return;
      }
      list.innerHTML = "";
      this.lockCandidates.forEach(function (item, idx) {
        var row = document.createElement("label");
        row.className = "lock-check-row";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = true;
        cb.dataset.idx = String(idx);
        var label = document.createElement("span");
        var conf = Math.round((item.confidence || 0) * 100);
        label.textContent =
          (item.entity || "Entity") +
          " · " +
          (item.metric || "Metric") +
          " = " +
          item.value +
          (item.unit ? " " + item.unit : "") +
          " (" +
          conf +
          "%)";
        row.appendChild(cb);
        row.appendChild(label);
        list.appendChild(row);
      });
    },

    acceptAllLocks: function () {
      var self = this;
      var list = $("lock-checklist");
      if (!list) return;
      var selected = [];
      list.querySelectorAll("input[type=checkbox]").forEach(function (cb) {
        if (!cb.checked) return;
        var idx = parseInt(cb.dataset.idx, 10);
        if (!isNaN(idx) && self.lockCandidates[idx]) selected.push(self.lockCandidates[idx]);
      });
      if (!selected.length) {
        if (global.AssureToast) global.AssureToast.show(t("adoption.locks.pick_one", "Select at least one lock."), "error");
        return;
      }
      fetch("/api/projects/" + encodeURIComponent(projectId()) + "/apply-locks", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidates: selected }),
      })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok, data: data };
          });
        })
        .then(function (result) {
          if (!result.ok) throw new Error((result.data && result.data.error) || "Apply failed");
          if (global.__assureJdf && result.data.document) {
            global.__assureJdf.tree = result.data.document;
            global.__assureJdf.render();
          } else if (global.__assureJdf && result.data.truth_ledger) {
            global.__assureJdf.tree.truth_ledger = result.data.truth_ledger;
            global.__assureJdf.render();
          }
          if (global.AssureToast) {
            global.AssureToast.show(
              t("adoption.locks.applied", "Locks applied to truth ledger."),
              "success"
            );
          }
          var banner = $("lock-inference-banner");
          if (banner) banner.hidden = true;
        })
        .catch(function (err) {
          if (global.AssureToast) global.AssureToast.show(String(err.message || err), "error");
        });
    },

    bindCitationDrawer: function () {
      var drawer = $("citation-drawer");
      var closeBtn = $("citation-drawer-close");
      var backdrop = $("citation-drawer-backdrop");
      if (closeBtn) {
        closeBtn.addEventListener("click", function () {
          AssureAdoption.closeCitationDrawer();
        });
      }
      if (backdrop) {
        backdrop.addEventListener("click", function () {
          AssureAdoption.closeCitationDrawer();
        });
      }
      document.addEventListener("click", function (e) {
        var pill = e.target.closest(".truth-pill.inline-citation");
        if (!pill) return;
        e.preventDefault();
        var raw = pill.getAttribute("data-provenance") || "{}";
        try {
          AssureAdoption.showCitationDrawer(JSON.parse(raw));
        } catch (_) {
          AssureAdoption.showCitationDrawer({});
        }
      });
      if (!drawer) return;
    },

    showCitationDrawer: function (provenance) {
      var drawer = $("citation-drawer");
      var body = $("citation-drawer-body");
      var backdrop = $("citation-drawer-backdrop");
      if (!drawer || !body) return;
      var p = provenance || {};
      var unknown = t("adoption.citation.unknown", "No source available");
      var name = p.source_name || p.source_file || unknown;
      var page = p.page_number || p.page_or_timestamp || "N/A";
      var quote = p.extracted_quote || p.exact_quote || unknown;
      body.innerHTML =
        "<p><strong>" +
        escapeHtml(t("adoption.citation.file", "File")) +
        ":</strong> " +
        escapeHtml(name) +
        "</p>" +
        "<p><strong>" +
        escapeHtml(t("adoption.citation.page", "Page")) +
        ":</strong> " +
        escapeHtml(page) +
        "</p>" +
        "<p><strong>" +
        escapeHtml(t("adoption.citation.quote", "Quote")) +
        ":</strong> \"" +
        escapeHtml(quote) +
        "\"</p>";
      drawer.classList.add("open");
      drawer.setAttribute("aria-hidden", "false");
      if (backdrop) backdrop.hidden = false;
    },

    closeCitationDrawer: function () {
      var drawer = $("citation-drawer");
      var backdrop = $("citation-drawer-backdrop");
      if (drawer) {
        drawer.classList.remove("open");
        drawer.setAttribute("aria-hidden", "true");
      }
      if (backdrop) backdrop.hidden = true;
    },

    bindAuditConsole: function () {
      var self = this;
      var settingsBtn = $("export-audit-btn");
      var deckBtn = $("btn-audit-manifest");
      var modal = $("audit-manifest-modal");
      var exportNow = $("audit-manifest-export");
      var cancelBtn = $("audit-manifest-cancel");
      var closeBtn = $("audit-manifest-close");

      function openModal() {
        if (modal && typeof modal.showModal === "function") {
          if (!modal.open) modal.showModal();
          return;
        }
        self.exportAuditManifest();
      }

      function closeModal() {
        if (modal && modal.open && typeof modal.close === "function") modal.close();
      }

      if (settingsBtn) settingsBtn.addEventListener("click", openModal);
      if (deckBtn) deckBtn.addEventListener("click", openModal);
      if (exportNow) {
        exportNow.addEventListener("click", function () {
          self.exportAuditManifest().then(function (ok) {
            if (ok) closeModal();
          });
        });
      }
      if (cancelBtn) cancelBtn.addEventListener("click", closeModal);
      if (closeBtn) closeBtn.addEventListener("click", closeModal);

      document.addEventListener("assure:tool", function (ev) {
        if (ev.detail && (ev.detail.tool === "settings" || ev.detail.tool === "audit")) {
          AssureAdoption.previewAuditManifest();
        }
      });
      document.addEventListener("assure:view", function (ev) {
        if (ev.detail && ev.detail.view === "settings") {
          AssureAdoption.previewAuditManifest();
        }
      });
    },

    previewAuditManifest: function () {
      var pre = $("audit-preview");
      if (!pre) return;
      pre.textContent = t("adoption.audit.loading", "Loading audit manifest…");
      fetch("/api/projects/" + encodeURIComponent(projectId()) + "/export-audit", {
        credentials: "same-origin",
      })
        .then(function (res) {
          return res.json();
        })
        .then(function (data) {
          if (data && data.manifest) {
            pre.textContent = JSON.stringify(data.manifest, null, 2);
          } else {
            pre.textContent = t("adoption.audit.empty", "No audit data yet.");
          }
        })
        .catch(function () {
          pre.textContent = t("adoption.audit.failed", "Could not load audit manifest.");
        });
    },

    auditFilename: function () {
      var stamp = new Date().toISOString().slice(0, 10);
      var slug = String(projectId() || "default").replace(/[^a-zA-Z0-9_-]+/g, "-");
      return "audit_manifest_" + slug + "_" + stamp + ".json";
    },

    exportAuditManifest: function () {
      var filename = this.auditFilename();
      return fetch("/api/projects/" + encodeURIComponent(projectId()) + "/export-audit", {
        credentials: "same-origin",
      })
        .then(function (res) {
          return res.json().then(function (data) {
            if (!res.ok) throw new Error((data && data.error) || t("adoption.audit.failed", "Could not load audit manifest."));
            return data;
          });
        })
        .then(function (data) {
          var manifest = (data && data.manifest) || {};
          var blob = new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" });
          var url = URL.createObjectURL(blob);
          var a = document.createElement("a");
          a.href = url;
          a.download = filename;
          a.click();
          URL.revokeObjectURL(url);
          var pre = $("audit-preview");
          if (pre) pre.textContent = JSON.stringify(manifest, null, 2);
          if (global.AssureToast) {
            global.AssureToast.show(
              t("audit.toast_exported", "Audit Manifest exported: {filename}", { filename: filename }) +
                " " +
                t("audit.toast_share", "Share this file with your compliance team or auditor."),
              "success"
            );
          }
          return true;
        })
        .catch(function (err) {
          if (global.AssureToast) global.AssureToast.show(String(err.message || err), "error");
          return false;
        });
    },
  };

  global.AssureAdoption = AssureAdoption;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      AssureAdoption.init();
    });
  } else {
    AssureAdoption.init();
  }
})(window);
