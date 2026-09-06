/**
 * Decision Provenance panel — why a verified node was verified.
 */
(function (global) {
  "use strict";

  function t(key, fallback, vars) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, vars || {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback || key;
  }

  function esc(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function $(id) {
    return document.getElementById(id);
  }

  function renderTarget() {
    return document.getElementById("jdf-render-target");
  }

  var Panel = {
    openNodeId: null,
    _bound: false,

    init: function () {
      if (this._bound) return;
      this._bound = true;
      var closeBtn = $("provenance-panel-close");
      if (closeBtn) {
        closeBtn.addEventListener("click", function () {
          Panel.close();
        });
      }
      document.addEventListener("click", function (ev) {
        var btn = ev.target.closest(".provenance-info-btn");
        if (btn) {
          ev.preventDefault();
          ev.stopPropagation();
          var nodeId = btn.getAttribute("data-node-id") || "";
          Panel.open(nodeId);
          return;
        }
        if (ev.target.closest("#provenance-panel-drawer")) return;
      });
      document.addEventListener("assure:jdf:rendered", function () {
        Panel.syncInfoButtons();
      });
      document.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape") Panel.close();
      });
    },

    syncInfoButtons: function () {
      var root = renderTarget();
      if (!root) return;
      var canvas = global.__assureJdf;
      if (!canvas || typeof canvas.computeNodeStatus !== "function") return;
      var isPreview = canvas.rootEl && canvas.rootEl.classList.contains("is-draft-preview");
      root.querySelectorAll(".jdf-node, .jdf-ast-node, details.jdf-ast-section[data-node-id]").forEach(
        function (el) {
          var nodeId = el.getAttribute("data-node-id");
          if (!nodeId) return;
          var node = canvas.getNodeById && canvas.getNodeById(nodeId);
          if (!node) return;
          var docNode = null;
          var compiled = global.compiledDocument;
          if (compiled && compiled.body) {
            (compiled.body || []).forEach(function (sec) {
              (sec.children || []).forEach(function (child) {
                if (child && String(child.id) === nodeId) docNode = child;
              });
            });
          }
          var status = canvas.computeNodeStatus(node);
          var meta = (docNode && docNode.meta) || node.meta || {};
          var verified =
            status === "verified" ||
            (isPreview && (meta.provenance || el.querySelector(".ink-stamp")));
          var existing = el.querySelector(".provenance-info-btn");
          if (!verified) {
            if (existing) existing.remove();
            return;
          }
          if (existing) return;
          var btn = document.createElement("button");
          btn.type = "button";
          btn.className = "provenance-info-btn";
          btn.setAttribute("data-node-id", nodeId);
          btn.setAttribute(
            "aria-label",
            t("jdf.provenance.open", "View verification provenance")
          );
          btn.title = t("jdf.provenance.open", "View verification provenance");
          btn.textContent = "ⓘ";
          el.classList.add("has-provenance-info");
          el.appendChild(btn);
        }
      );
    },

    resolveProvenance: function (node) {
      if (!node) return null;
      var meta = node.meta || {};
      if (meta.provenance) return meta.provenance;
      var list = node.provenance || [];
      if (list.length && list[0]) {
        var row = list[0];
        return {
          source_id: row.source_id || "",
          source_name: row.source_name || "",
          page_number: row.page_number || "",
          excerpt: row.extracted_quote || "",
          rule: "ledger_check",
          confidence: 0.85,
          verified_at: "",
        };
      }
      return null;
    },

    open: function (nodeId) {
      if (!nodeId) return;
      var canvas = global.__assureJdf;
      var node = canvas && canvas.getNodeById ? canvas.getNodeById(nodeId) : null;
      var compiled = global.compiledDocument;
      if (compiled && compiled.body) {
        (compiled.body || []).forEach(function (sec) {
          (sec.children || []).forEach(function (child) {
            if (child && String(child.id) === nodeId) {
              node = Object.assign({}, child, { meta: child.meta || {} });
            }
          });
        });
      }
      var prov = this.resolveProvenance(node);
      this.openNodeId = nodeId;
      var drawer = $("provenance-panel-drawer");
      var body = $("provenance-panel-body");
      if (!drawer || !body) return;
      drawer.hidden = false;
      document.body.classList.add("provenance-panel-open");
      if (!prov) {
        body.innerHTML =
          '<p class="hint provenance-empty">' +
          esc(t("jdf.provenance.empty", "No provenance recorded for this node yet.")) +
          "</p>";
        return;
      }
      var conf = Math.round(Number(prov.confidence || 0) * 100);
      var confClass =
        conf >= 85 ? "is-high" : conf >= 60 ? "is-mid" : "is-low";
      var page =
        prov.page_number !== undefined && prov.page_number !== null && prov.page_number !== ""
          ? String(prov.page_number)
          : "—";
      body.innerHTML =
        '<dl class="provenance-dl">' +
        '<dt data-i18n="jdf.provenance.source">' +
        esc(t("jdf.provenance.source", "Source document")) +
        "</dt><dd>" +
        esc(prov.source_name || "—") +
        "</dd>" +
        '<dt data-i18n="jdf.provenance.page">' +
        esc(t("jdf.provenance.page", "Page")) +
        "</dt><dd>" +
        esc(page) +
        "</dd>" +
        "</dl>" +
        '<blockquote class="provenance-excerpt">' +
        esc(prov.excerpt || "") +
        "</blockquote>" +
        '<p class="provenance-rule-label" data-i18n="jdf.provenance.rule">' +
        esc(t("jdf.provenance.rule", "Z3 rule applied")) +
        "</p>" +
        '<pre class="provenance-rule"><code>' +
        esc(prov.rule || "ledger_check") +
        "</code></pre>" +
        '<div class="provenance-confidence">' +
        '<span data-i18n="jdf.provenance.confidence">' +
        esc(t("jdf.provenance.confidence", "Confidence")) +
        "</span>" +
        '<div class="provenance-confidence-bar ' +
        confClass +
        '"><span style="width:' +
        conf +
        '%"></span></div>' +
        '<span class="provenance-confidence-pct">' +
        conf +
        "%</span></div>" +
        '<p class="provenance-verified-at"><span data-i18n="jdf.provenance.verified_at">' +
        esc(t("jdf.provenance.verified_at", "Verified at")) +
        "</span>: " +
        esc(prov.verified_at || "—") +
        "</p>" +
        (prov.source_id
          ? '<button type="button" class="btn btn-outline btn-sm" id="provenance-view-source-btn" data-source-id="' +
            esc(prov.source_id) +
            '">' +
            esc(t("jdf.provenance.view_source", "View Full Source")) +
            "</button>"
          : "");
      var viewBtn = $("provenance-view-source-btn");
      if (viewBtn) {
        viewBtn.addEventListener("click", function () {
          Panel.viewFullSource(viewBtn.getAttribute("data-source-id"));
        });
      }
    },

    viewFullSource: function (sourceId) {
      if (!sourceId) return;
      var projectId = global.__ASSURE_PROJECT_ID__ || "default";
      if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
        global.AssureNav.switchView("library", { replaceHash: false, persist: true });
      }
      fetch("/api/projects/" + encodeURIComponent(projectId) + "/substrate/" + encodeURIComponent(sourceId), {
        credentials: "same-origin",
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (!data.ok || !data.file) return;
          document.dispatchEvent(
            new CustomEvent("assure:substrate:focus", {
              detail: { sourceId: sourceId, filename: data.file.filename },
            })
          );
          if (global.AssureToast) {
            global.AssureToast.show(
              t("jdf.provenance.source_opened", "Opened {name} in Sources.", {
                name: data.file.filename || sourceId,
              }),
              "success"
            );
          }
        })
        .catch(function () {});
    },

    close: function () {
      var drawer = $("provenance-panel-drawer");
      if (drawer) drawer.hidden = true;
      document.body.classList.remove("provenance-panel-open");
      this.openNodeId = null;
    },
  };

  global.AssureProvenancePanel = Panel;
  document.addEventListener("DOMContentLoaded", function () {
    Panel.init();
  });
  document.addEventListener("assure:docked", function () {
    window.setTimeout(function () {
      Panel.syncInfoButtons();
    }, 400);
  });
  document.addEventListener("assure:jdf:rendered", function () {
    window.setTimeout(function () {
      Panel.syncInfoButtons();
    }, 120);
  });
})(window);
