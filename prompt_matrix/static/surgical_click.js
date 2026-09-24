/**
 * Surgical sandwich menu: Refine / Ground (submenu) / History / Delete.
 */
(function (global) {
  "use strict";

  function $(id) {
    return global.document.getElementById(id);
  }

  function t(key, fallback, vars) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, vars || {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback || key;
  }

  function projectId() {
    return (global.__assureJdf && global.__assureJdf.projectId) || global.__ASSURE_PROJECT_ID__ || "default";
  }

  function flattenNodes(tree) {
    var out = [];
    ((tree && tree.body) || []).forEach(function (section) {
      if (section) out.push(section);
      ((section && section.children) || []).forEach(function (child) {
        if (child) out.push(child);
      });
    });
    return out;
  }

  function showConflictModal(clicker, opts) {
    var modal = $("node-conflict-modal");
    if (!modal) {
      if (global.confirm(t("conflict.body", "This node was changed by another user. Reload or overwrite?"))) {
        var canvas = global.__assureJdf;
        if (canvas && typeof canvas.refreshCanvas === "function") canvas.refreshCanvas();
      }
      clicker.setBusy(false);
      return;
    }
    modal.hidden = false;
    clicker.setBusy(false);
    document.body.classList.add("tab-lockout-active");
    function close() {
      modal.hidden = true;
      document.body.classList.remove("tab-lockout-active");
    }
    var reloadBtn = $("node-conflict-reload");
    var overwriteBtn = $("node-conflict-overwrite");
    var backdrop = $("node-conflict-backdrop");
    function onReload() {
      close();
      clicker.setBusy(false);
      var canvas = global.__assureJdf;
      if (canvas && typeof canvas.refreshCanvas === "function") canvas.refreshCanvas();
    }
    function onOverwrite() {
      close();
      clicker.postRefine(Object.assign({}, opts, { overwrite: true }));
    }
    if (reloadBtn) reloadBtn.onclick = onReload;
    if (overwriteBtn) overwriteBtn.onclick = onOverwrite;
    if (backdrop) backdrop.onclick = onReload;
  }

  function neighborContext(tree, nodeId) {
    var nodes = flattenNodes(tree);
    var idx = -1;
    var i;
    for (i = 0; i < nodes.length; i += 1) {
      if (nodes[i] && nodes[i].id === nodeId) {
        idx = i;
        break;
      }
    }
    function brief(node) {
      if (!node) return null;
      return {
        id: node.id,
        type: node.type,
        summary: String(node.content || node.title || "").slice(0, 240),
      };
    }
    return {
      preceding: idx > 0 ? brief(nodes[idx - 1]) : null,
      succeeding: idx >= 0 && idx + 1 < nodes.length ? brief(nodes[idx + 1]) : null,
    };
  }

  var SurgicalClick = {
    nodeId: null,
    _bound: false,

    bind: function () {
      if (this._bound) return;
      this._bound = true;
      var self = this;
      var pop = $("jdf-surgical-popover");
      var refineBtn = $("surgical-refine-ai-btn");
      var groundWrap = $("surgical-ground-wrap");
      var groundBtn = $("surgical-ground-btn");
      var submenu = $("surgical-ground-submenu");
      var form = $("jdf-surgical-popover-form");
      var cancel = $("surgical-refine-cancel");
      var canvasRoot = $("jdf-render-target");
      var histBtn = $("surgical-history-btn");
      var delBtn = $("surgical-delete-btn");

      if (refineBtn) {
        refineBtn.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          self.showInstructionForm();
        });
      }
      function openSub() {
        if (!submenu || !groundWrap) return;
        submenu.hidden = false;
        groundWrap.classList.add("is-open");
        if (groundBtn) groundBtn.setAttribute("aria-expanded", "true");
      }
      function closeSub() {
        if (!submenu || !groundWrap) return;
        submenu.hidden = true;
        groundWrap.classList.remove("is-open");
        if (groundBtn) groundBtn.setAttribute("aria-expanded", "false");
      }
      self._closeGroundSub = closeSub;
      if (groundBtn) {
        groundBtn.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          openSub();
        });
      }
      if (groundWrap) {
        groundWrap.addEventListener("mouseenter", openSub);
      }
      ["surgical-ground-auto", "surgical-ground-search", "surgical-ground-llm"].forEach(function (id) {
        var el = $(id);
        if (!el) return;
        el.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          self.postGround(el.getAttribute("data-ground-mode") || "auto");
        });
      });
      if (histBtn) {
        histBtn.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          var canvas = global.__assureJdf;
          if (canvas && typeof canvas.openNodeHistoryModal === "function" && self.nodeId) {
            canvas.openNodeHistoryModal(self.nodeId);
          }
          self.close();
        });
      }
      if (delBtn) {
        delBtn.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          self.deleteCurrent();
        });
      }
      if (form) {
        form.addEventListener("submit", function (e) {
          e.preventDefault();
          self.submitRefine();
        });
      }
      if (cancel) {
        cancel.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          self.resetActions();
        });
      }
      if (canvasRoot) {
        canvasRoot.addEventListener("mouseover", function (e) {
          var hit = e.target.closest && e.target.closest("[data-node-id]");
          if (!hit || !canvasRoot.contains(hit)) return;
          if (hit.classList.contains("verification-gutter")) return;
          var id = hit.getAttribute("data-node-id");
          if (!id) return;
          self.showFloatingBar(id, hit);
        });
        canvasRoot.addEventListener("mouseleave", function (e) {
          var bar = $("jdf-floating-bar");
          if (!bar || bar.hidden) return;
          var rel = e.relatedTarget;
          if (rel && (bar.contains(rel) || (rel.closest && rel.closest("[data-node-id]")))) return;
          self.hideFloatingBar();
        }, true);
        canvasRoot.addEventListener("click", function (e) {
          if (e.target.closest && e.target.closest("summary")) {
            e.preventDefault();
          }
          var hit = e.target.closest && e.target.closest("[data-node-id]");
          if (!hit || !canvasRoot.contains(hit)) return;
          var id = hit.getAttribute("data-node-id");
          if (!id) return;
          e.preventDefault();
          e.stopPropagation();
          self.open(id, e.clientX, e.clientY);
        });
      }
      global.document.addEventListener("click", function (e) {
        if (!pop || pop.hidden) return;
        if (self._openedAt && Date.now() - self._openedAt < 250) return;
        if (e.target.closest && e.target.closest("#jdf-surgical-popover")) return;
        if (e.target.closest && e.target.closest("[data-node-id]")) return;
        self.close();
      });
      global.document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") self.close();
      });
    },

    open: function (nodeId, clientX, clientY) {
      var pop = $("jdf-surgical-popover");
      if (!pop || !nodeId) return;
      this.nodeId = nodeId;
      this.resetActions();
      var canvas = global.__assureJdf;
      if (canvas && typeof canvas.selectNodeForRefine === "function") {
        canvas.selectNodeForRefine(nodeId, { toast: false, skipRender: true, skipViewSwitch: true });
      }
      pop.hidden = false;
      this._openedAt = Date.now();
      var pad = 8;
      var w = pop.offsetWidth || 280;
      var h = pop.offsetHeight || 180;
      var x = Math.min(Math.max(pad, clientX || pad), window.innerWidth - w - pad);
      var y = Math.min(Math.max(pad, (clientY || pad) + 8), window.innerHeight - h - pad);
      pop.style.left = x + "px";
      pop.style.top = y + "px";
    },

    close: function () {
      var pop = $("jdf-surgical-popover");
      if (pop) pop.hidden = true;
      if (this._closeGroundSub) this._closeGroundSub();
      this.resetActions();
      this.nodeId = null;
    },

    showFloatingBar: function (nodeId, anchorEl) {
      var bar = $("jdf-floating-bar");
      if (!bar || !anchorEl) return;
      this.nodeId = nodeId;
      bar.hidden = false;
      bar.removeAttribute("hidden");
      bar.classList.remove("is-visible");
      var rect = anchorEl.getBoundingClientRect();
      var pad = 8;
      var w = bar.offsetWidth || 320;
      var x = Math.min(Math.max(pad, rect.right - w), window.innerWidth - w - pad);
      var y = Math.max(pad, rect.top - 44);
      bar.style.left = x + "px";
      bar.style.top = y + "px";
      requestAnimationFrame(function () {
        bar.classList.add("is-visible");
      });
      this._bindFloatingBar();
    },

    hideFloatingBar: function () {
      var bar = $("jdf-floating-bar");
      if (!bar) return;
      bar.classList.remove("is-visible");
      bar.hidden = true;
    },

    _floatingBound: false,
    _bindFloatingBar: function () {
      if (this._floatingBound) return;
      this._floatingBound = true;
      var self = this;
      var map = [
        ["floating-rewrite-btn", function () {
          self.open(self.nodeId, window.innerWidth / 2, window.innerHeight / 2);
          self.showInstructionForm();
        }],
        ["floating-ground-btn", function () {
          self.open(self.nodeId, window.innerWidth / 2, window.innerHeight / 2);
          self.postGround("auto");
        }],
        ["floating-history-btn", function () {
          var canvas = global.__assureJdf;
          if (canvas && typeof canvas.openNodeHistoryModal === "function" && self.nodeId) {
            canvas.openNodeHistoryModal(self.nodeId);
          }
          self.hideFloatingBar();
        }],
        ["floating-delete-btn", function () {
          self.open(self.nodeId, window.innerWidth / 2, window.innerHeight / 2);
          self.deleteCurrent();
          self.hideFloatingBar();
        }],
      ];
      map.forEach(function (pair) {
        var el = $(pair[0]);
        if (!el) return;
        el.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          pair[1]();
        });
      });
    },

    resetActions: function () {
      var actions = $("jdf-surgical-popover-actions");
      var form = $("jdf-surgical-popover-form");
      var area = $("surgical-refine-instruction");
      if (actions) actions.hidden = false;
      if (form) form.hidden = true;
      if (area) area.value = "";
      if (this._closeGroundSub) this._closeGroundSub();
    },

    showInstructionForm: function () {
      var actions = $("jdf-surgical-popover-actions");
      var form = $("jdf-surgical-popover-form");
      var area = $("surgical-refine-instruction");
      if (actions) actions.hidden = true;
      if (form) form.hidden = false;
      if (area) {
        area.focus();
        area.placeholder = t(
          "surgical.click.instruction_placeholder",
          "Rewrite this to be more neutral"
        );
      }
    },

    setBusy: function (on) {
      var pop = $("jdf-surgical-popover");
      if (pop) pop.classList.toggle("is-busy", !!on);
      [
        "surgical-refine-ai-btn",
        "surgical-ground-btn",
        "surgical-ground-auto",
        "surgical-ground-search",
        "surgical-ground-llm",
        "surgical-history-btn",
        "surgical-delete-btn",
        "surgical-refine-apply",
      ].forEach(function (id) {
        var el = $(id);
        if (el) el.disabled = !!on;
      });
    },

    deleteCurrent: function () {
      var canvas = global.__assureJdf;
      var nodeId = this.nodeId;
      if (!canvas || !canvas.tree || !nodeId) return;
      (canvas.tree.body || []).forEach(function (section) {
        if (!section || !section.children) return;
        section.children = section.children.filter(function (child) {
          return !child || child.id !== nodeId;
        });
      });
      if (typeof canvas._setDirty === "function") canvas._setDirty(true);
      if (typeof canvas.render === "function") canvas.render();
      if (typeof canvas.saveDocument === "function") {
        canvas.saveDocument("NODE_DELETE", { target_node_id: nodeId });
      }
      this.close();
    },

    submitRefine: function () {
      var area = $("surgical-refine-instruction");
      var instruction = ((area && area.value) || "").trim();
      if (!instruction) {
        if (global.AssureToast) {
          global.AssureToast.show(t("refine.no_intent", "Enter a refinement instruction first."), "error");
        }
        return;
      }
      this.postRefine({ user_instruction: instruction, ground_from_vault: false });
    },

    groundFromVault: function () {
      var ids =
        global.AssureSubstrateVault && typeof global.AssureSubstrateVault.selectedIncludedIds === "function"
          ? global.AssureSubstrateVault.selectedIncludedIds()
          : [];
      this.postRefine({
        user_instruction: t(
          "surgical.click.vault_instruction",
          "Rewrite this node so every claim is grounded in the Substrate Vault excerpts."
        ),
        ground_from_vault: true,
        substrate_file_ids: ids,
      });
    },

    openDiff: function (nodeId, originalText, proposedText, payload) {
      var canvas = global.__assureJdf;
      if (!canvas) return;
      canvas._pendingDiff = {
        nodeId: nodeId,
        original: originalText,
        proposed: proposedText,
        payload: payload,
        surgical: true,
      };
      if (typeof canvas.showRevisionDiff === "function") {
        canvas.showRevisionDiff(originalText, proposedText, { sideBySideDiff: true });
      }
      this.close();
      if (global.AssureToast) {
        global.AssureToast.show(
          t("jdf.diff.review", "Review the proposed changes, then Accept or Reject."),
          "info"
        );
      }
    },

    postGround: function (mode) {
      // ``POST .../nodes/<id>/ground`` no longer exists (the route was removed
      // with the old grounding service), so every Ground click answered 404
      // with a "Grounding failed." toast. The surviving grounding path is the
      // refine route with ``ground_from_vault``: the node is rewritten against
      // the Substrate Vault excerpts and comes back as a Proposal for review.
      // The submenu's Search/LLM modes had no backing route of their own; all
      // three take this path and the mode is kept only for the audit trail.
      var nodeId = this.nodeId;
      var canvas = global.__assureJdf;
      if (!nodeId || !canvas || !canvas.tree) {
        if (global.AssureToast) {
          global.AssureToast.show(t("surgical.click.no_node", "Select a compiled node first."), "error");
        }
        return;
      }
      var ids =
        global.AssureSubstrateVault && typeof global.AssureSubstrateVault.selectedIncludedIds === "function"
          ? global.AssureSubstrateVault.selectedIncludedIds()
          : [];
      this.postRefine({
        user_instruction: t(
          "surgical.click.vault_instruction",
          "Rewrite this node so every claim is grounded in the Substrate Vault excerpts."
        ),
        ground_from_vault: true,
        substrate_file_ids: ids,
        ground_mode: mode || "auto",
      });
    },

    postRefine: function (opts) {
      var self = this;
      var nodeId = this.nodeId;
      var canvas = global.__assureJdf;
      if (!nodeId || !canvas || !canvas.tree) {
        if (global.AssureToast) {
          global.AssureToast.show(t("surgical.click.no_node", "Select a compiled node first."), "error");
        }
        return;
      }
      var nodeBefore = canvas.getNodeById(nodeId);
      var originalText = nodeBefore
        ? String(nodeBefore.content || nodeBefore.title || "")
        : "";
      this.setBusy(true);
      // Held still for the duration: the call takes seconds, and a keystroke
      // landing in that window is overwritten by the reply. Released in every
      // exit path below.
      if (global.AssureSurgicalDiff) {
        global.AssureSurgicalDiff.setNodeLocked(nodeId, true);
      }
      var body = {
        node_id: nodeId,
        user_instruction: opts.user_instruction || "",
        ground_from_vault: !!opts.ground_from_vault,
        substrate_file_ids: opts.substrate_file_ids || [],
        context: neighborContext(canvas.tree, nodeId),
        document: canvas.tree,
        expected_version: canvas.tree && canvas.tree.meta ? canvas.tree.meta.version : undefined,
      };
      if (body.expected_version == null && canvas.documentVersion != null) {
        body.expected_version = canvas.documentVersion;
      }
      if (opts.overwrite) delete body.expected_version;
      fetch("/api/projects/" + encodeURIComponent(projectId()) + "/refine-node", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then(function (res) {
          return res.json().then(function (data) {
            return { ok: res.ok, data: data };
          });
        })
        .then(function (pack) {
          self.setBusy(false);
          if (global.AssureSurgicalDiff) {
            global.AssureSurgicalDiff.setNodeLocked(nodeId, false);
          }
          if (!pack.ok || !pack.data || pack.data.ok === false) {
            if (pack.data && pack.data.latest_version != null) {
              showConflictModal(self, opts);
              return;
            }
            if (global.AssureToast) {
              global.AssureToast.show(
                String((pack.data && pack.data.error) || t("surgical.click.failed", "Refine failed.")),
                "error"
              );
            }
            self.resetActions();
            return;
          }
          var proposedText = "";
          if (pack.data.node) {
            proposedText = String(pack.data.node.content || pack.data.node.title || "");
          } else if (pack.data.document) {
            var updated = canvas.getNodeById(nodeId);
            proposedText = updated
              ? String(updated.content || updated.title || "")
              : originalText;
          }
          self.openDiff(nodeId, originalText, proposedText, pack.data);
        })
        .catch(function (err) {
          self.setBusy(false);
          if (global.AssureToast) {
            global.AssureToast.show(String((err && err.message) || err), "error");
          }
          self.resetActions();
        });
    },
  };

  function init() {
    SurgicalClick.bind();
  }

  if (global.document.readyState === "loading") {
    global.document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  global.AssureSurgicalClick = SurgicalClick;
})(typeof window !== "undefined" ? window : this);
