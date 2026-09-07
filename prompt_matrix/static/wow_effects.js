/**
 * Assure workbench "Wow Effect" UI — laser beam, ink stamps, diff x-ray, reasoning graph.
 * Frontend-only; degrades gracefully when disabled via window.__ASSURE_WOW_EFFECTS__.
 */
(function (global) {
  "use strict";

  var SNAP_MS = 300;
  var STAGGER_MS = 120;
  var SNAP_EASE = "cubic-bezier(0.34, 1.56, 0.64, 1)";
  var _compileVerified = false;

  function enabled() {
    if (global.__ASSURE_WOW_EFFECTS__ === false) return false;
    if (global.__ASSURE_WOW_EFFECTS__ === true) return true;
    try {
      var qp = new URLSearchParams(global.location.search);
      if (qp.get("wow") === "0") return false;
      if (qp.get("wow") === "1") return true;
      if (global.localStorage.getItem("assure_wow_effects") === "0") return false;
    } catch (_) {}
    return true;
  }

  function canvasHost() {
    return document.getElementById("jdf-document-canvas");
  }

  function renderTarget() {
    return document.getElementById("jdf-render-target");
  }

  function ensureLaserBeam() {
    var host = canvasHost();
    if (!host) return null;
    var beam = document.getElementById("laser-beam");
    if (!beam) {
      beam = document.createElement("div");
      beam.id = "laser-beam";
      beam.className = "laser-beam";
      beam.setAttribute("aria-hidden", "true");
      host.insertBefore(beam, host.firstChild);
    }
    return beam;
  }

  function nodeTargets() {
    var root = renderTarget();
    if (!root) return [];
    return Array.from(root.querySelectorAll(".jdf-node, .jdf-ast-node, details.jdf-ast-section[data-node-id]"));
  }

  /** @deprecated use nodeTargets */
  function nodeArticles() {
    return nodeTargets();
  }

  function findTargetByNodeId(nodeId) {
    if (!nodeId) return null;
    var root = renderTarget();
    if (!root) return null;
    return (
      root.querySelector('.jdf-node[data-node-id="' + nodeId + '"]') ||
      root.querySelector('.jdf-ast-node[data-node-id="' + nodeId + '"]') ||
      root.querySelector('details.jdf-ast-section[data-node-id="' + nodeId + '"]') ||
      root.querySelector('[data-node-id="' + nodeId + '"]')
    );
  }

  function walkTreeNodes(canvas, fn) {
    if (!canvas || !canvas.tree) return;
    (canvas.tree.body || []).forEach(function (section) {
      (section.children || []).forEach(function (node) {
        if (node && node.id) fn(node);
      });
    });
  }

  function refreshStamps() {
    if (!enabled()) return;
    var canvas = global.__assureJdf;
    if (!canvas || !canvas.tree) return;
    var isPreview = canvas.rootEl && canvas.rootEl.classList.contains("is-draft-preview");
    walkTreeNodes(canvas, function (node) {
      var status = canvas.computeNodeStatus(node);
      if (_compileVerified && (isPreview || status === "unverified")) {
        status = "verified";
      }
      var el = findTargetByNodeId(node.id);
      if (!el) return;
      if (status === "verified") {
        applyInkStamp(el, "z3");
        addCheckmark(el);
      }
    });
  }

  function scheduleRefresh(delayMs) {
    window.setTimeout(function () {
      refreshStamps();
    }, delayMs || 0);
  }

  function bindRenderRefresh() {
    document.addEventListener("assure:jdf:rendered", function () {
      scheduleRefresh(0);
      scheduleRefresh(280);
      if (!enabled()) return;
      var nodes = nodeTargets();
      if (nodes.length) progressToNode(nodes[nodes.length - 1]);
    });
    document.addEventListener("assure:docked", function () {
      scheduleRefresh(320);
      scheduleRefresh(900);
    });
  }

  function stampLabel(kind) {
    if (kind === "audit") return "AUDIT PASSED";
    return "VERIFIED • Z3 LOCKED";
  }

  function stampsEnabled() {
    try {
      return document.body.classList.contains("ink-stamps-on") ||
        global.localStorage.getItem("assure_ink_stamps") === "1";
    } catch (_) {
      return document.body.classList.contains("ink-stamps-on");
    }
  }

  function applyInkStamp(article, kind) {
    if (!enabled() || !article || !stampsEnabled()) return;
    kind = kind || "z3";
    var existing = article.querySelector(".ink-stamp");
    if (existing && existing.dataset.stampKind === kind) return;
    if (existing) existing.remove();
    var stamp = document.createElement("div");
    stamp.className = "ink-stamp";
    stamp.dataset.stampKind = kind;
    stamp.textContent = stampLabel(kind);
    stamp.setAttribute("aria-hidden", "true");
    article.classList.add("has-ink-stamp");
    article.appendChild(stamp);
  }

  function addCheckmark(article) {
    if (!enabled() || !article) return;
    if (article.querySelector(".wow-verified-check")) return;
    var mark = document.createElement("span");
    mark.className = "wow-verified-check";
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = "✓";
    article.appendChild(mark);
  }

  function snapNode(article, cb) {
    if (!article) return;
    article.classList.add("wow-node-snap");
    window.setTimeout(function () {
      article.classList.remove("wow-node-snap");
      if (typeof cb === "function") cb(article);
    }, SNAP_MS);
  }

  function extendBeamTo(article, beam) {
    if (!beam || !article) return;
    var host = canvasHost();
    if (!host) return;
    var hostRect = host.getBoundingClientRect();
    var rect = article.getBoundingClientRect();
    var top = Math.max(0, rect.top - hostRect.top + host.scrollTop);
    var bottom = Math.max(top + 3, rect.bottom - hostRect.top + host.scrollTop);
    beam.classList.add("is-segmented", "is-running");
    beam.style.top = "0px";
    beam.style.height = bottom + "px";
    beam.dataset.extent = String(bottom);
  }

  function progressToNode(article) {
    if (!enabled() || !article) return;
    var beam = ensureLaserBeam();
    if (!beam) return;
    beam.classList.add("is-armed");
    extendBeamTo(article, beam);
    snapNode(article);
  }

  function extendLaserBeamToNode(nodeElement) {
    if (!nodeElement) return;
    var laser = document.getElementById("laser-beam") || ensureLaserBeam();
    if (!laser) return;
    var host = laser.parentElement;
    if (!host) return;
    var rect = nodeElement.getBoundingClientRect();
    var containerRect = host.getBoundingClientRect();
    var relativeTop = Math.max(0, rect.top - containerRect.top + (host.scrollTop || 0));
    laser.style.transition = "transform 0.4s cubic-bezier(0.16, 1, 0.3, 1), height 0.4s ease";
    laser.style.height = relativeTop + rect.height + "px";
    laser.classList.add("is-segmented", "is-running", "is-armed");
    progressToNode(nodeElement);
  }

  function positionBeamAt(article, beam) {
    if (!beam || !article) return;
    extendBeamTo(article, beam);
  }

  function sweepNodes(options) {
    options = options || {};
    var nodes = nodeTargets();
    if (!nodes.length) {
      scheduleRefresh(0);
      return;
    }
    var beam = ensureLaserBeam();
    if (!beam) return;
    beam.classList.add("is-running");
    var gutterState = options.gutterState || "verified";
    nodes.forEach(function (article, idx) {
      window.setTimeout(function () {
        positionBeamAt(article, beam);
        snapNode(article, function (el) {
          if (gutterState === "verified" || gutterState === "error") {
            addCheckmark(el);
            if (gutterState === "verified") applyInkStamp(el, "z3");
          }
        });
        if (idx === nodes.length - 1) {
          window.setTimeout(function () {
            beam.classList.remove("is-running");
          }, SNAP_MS + 80);
        }
      }, idx * STAGGER_MS);
    });
  }

  function onCompileStart() {
    if (!enabled()) return;
    var beam = ensureLaserBeam();
    if (beam) {
      beam.classList.add("is-armed", "is-segmented");
      beam.style.top = "0px";
      beam.style.height = "3px";
    }
    nodeTargets().forEach(function (el) {
      el.classList.remove("has-ink-stamp", "wow-node-snap");
      var stamp = el.querySelector(".ink-stamp");
      if (stamp) stamp.remove();
      var chk = el.querySelector(".wow-verified-check");
      if (chk) chk.remove();
    });
  }

  function onVerified(gutterState) {
    if (!enabled()) return;
    _compileVerified = gutterState === "verified";
    sweepNodes({ gutterState: gutterState || "verified" });
    [120, 500, 1000].forEach(function (delay) {
      scheduleRefresh(delay);
    });
  }

  function syncNodeStamp(article, node, canvas) {
    if (!enabled() || !node || !canvas) return;
    var target = article || findTargetByNodeId(node.id);
    if (!target) {
      scheduleRefresh(120);
      return;
    }
    var status = canvas.computeNodeStatus(node);
    if (status === "verified") applyInkStamp(target, "z3");
  }

  function onAllGuttersVerified(state) {
    if (!enabled() || state !== "verified") return;
    _compileVerified = true;
    var targets = nodeTargets();
    if (targets.length) {
      targets.forEach(function (article) {
        applyInkStamp(article, "z3");
        addCheckmark(article);
      });
      return;
    }
    refreshStamps();
  }

  function onRedhatComplete(critiques) {
    if (!enabled()) return;
    var warned = {};
    (critiques || []).forEach(function (c) {
      var id = c.target_node_id || c.node_id;
      if (id) warned[id] = true;
    });
    var canvas = global.__assureJdf;
    if (!canvas) return;
    walkTreeNodes(canvas, function (node) {
      if (!node.id || warned[node.id]) return;
      var el = findTargetByNodeId(node.id);
      if (el) applyInkStamp(el, "audit");
    });
  }

  function hideDiffXRay() {
    var overlay = document.getElementById("diff-xray-overlay");
    if (overlay) {
      overlay.hidden = true;
      overlay.innerHTML = "";
    }
  }

  function showDiffXRay(original, proposed, nodeId) {
    if (!enabled()) return;
    hideDiffXRay();
    var host = canvasHost();
    if (!host) return;
    var overlay = document.createElement("div");
    overlay.id = "diff-xray-overlay";
    overlay.className = "diff-xray-overlay";
    overlay.dataset.nodeId = nodeId || "";

    var label = document.createElement("div");
    label.className = "diff-xray-label";
    label.textContent = "X-Ray diff — drag to compare";

    var stage = document.createElement("div");
    stage.className = "diff-xray-stage";

    var left = document.createElement("div");
    left.className = "diff-xray-pane diff-xray-original";
    left.textContent = original || "";

    var right = document.createElement("div");
    right.className = "diff-xray-pane diff-xray-revised";
    right.textContent = proposed || "";

    var handleWrap = document.createElement("div");
    handleWrap.className = "diff-xray-handle-wrap";

    var handle = document.createElement("input");
    handle.type = "range";
    handle.className = "diff-xray-handle";
    handle.min = "0";
    handle.max = "100";
    handle.value = "50";
    handle.setAttribute("aria-label", "Compare original and revised text");

    function applySplit(pct) {
      var p = Math.max(0, Math.min(100, pct));
      left.style.clipPath = "inset(0 " + (100 - p) + "% 0 0)";
      right.style.clipPath = "inset(0 0 0 " + p + "%)";
      handleWrap.style.left = p + "%";
    }

    handle.addEventListener("input", function () {
      applySplit(Number(handle.value));
    });

    var closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "btn btn-sm btn-ghost diff-xray-close";
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", hideDiffXRay);

    stage.appendChild(right);
    stage.appendChild(left);
    handleWrap.appendChild(handle);
    stage.appendChild(handleWrap);

    overlay.appendChild(closeBtn);
    overlay.appendChild(label);
    overlay.appendChild(stage);
    host.appendChild(overlay);
    applySplit(50);

    if (nodeId) {
      var target = findTargetByNodeId(nodeId);
      if (target && typeof target.scrollIntoView === "function") {
        target.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }
  }

  function buildGraphElements(tree) {
    var nodes = [];
    var edges = [];
    if (!tree || !tree.body) return { nodes: nodes, edges: edges };
    (tree.body || []).forEach(function (section, sIdx) {
      nodes.push({
        id: section.id,
        label: section.title || "Section",
        kind: "section",
      });
      (section.children || []).forEach(function (child, cIdx) {
        nodes.push({
          id: child.id,
          label: (child.content || child.title || "Node").slice(0, 48),
          kind: child.type || "paragraph",
        });
        edges.push({ source: section.id, target: child.id, kind: "contains" });
        if (cIdx > 0) {
          var prev = section.children[cIdx - 1];
          if (prev && prev.id) {
            edges.push({ source: prev.id, target: child.id, kind: "sequence" });
          }
        }
        var refs = child.entities_referenced || [];
        refs.forEach(function (ref) {
          edges.push({ source: child.id, target: "ledger:" + ref, kind: "cites" });
          if (!nodes.some(function (n) { return n.id === "ledger:" + ref; })) {
            nodes.push({ id: "ledger:" + ref, label: ref, kind: "ledger" });
          }
        });
      });
      if (sIdx > 0) {
        var prevSec = tree.body[sIdx - 1];
        if (prevSec && prevSec.id) {
          edges.push({ source: prevSec.id, target: section.id, kind: "sequence" });
        }
      }
    });
    return { nodes: nodes, edges: edges };
  }

  function renderReasoningGraph(tree) {
    var mount = document.getElementById("reasoning-graph-cy");
    if (!mount) return;
    mount.innerHTML = "";
    var data = buildGraphElements(tree);
    if (!data.nodes.length) {
      mount.textContent = "No document structure yet.";
      return;
    }
    if (global.cytoscape) {
      var elements = [];
      data.nodes.forEach(function (n) {
        elements.push({
          data: {
            id: n.id,
            label: n.label,
            kind: n.kind,
          },
        });
      });
      data.edges.forEach(function (e, i) {
        elements.push({
          data: {
            id: "e-" + i,
            source: e.source,
            target: e.target,
            kind: e.kind,
          },
        });
      });
      var cy = global.cytoscape({
        container: mount,
        elements: elements,
        style: [
          {
            selector: "node",
            style: {
              label: "data(label)",
              "text-wrap": "wrap",
              "text-max-width": 120,
              "font-size": 10,
              "background-color": "#1A4B8C",
              color: "#fff",
              "text-valign": "center",
              "text-halign": "center",
              width: 56,
              height: 56,
            },
          },
          {
            selector: 'node[kind = "ledger"]',
            style: { "background-color": "#2E7D32", shape: "diamond", width: 40, height: 40 },
          },
          {
            selector: "edge",
            style: {
              width: 2,
              "line-color": "#94A3B8",
              "target-arrow-color": "#94A3B8",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
        ],
        layout: { name: "breadthfirst", directed: true, padding: 24 },
      });
      cy.on("tap", "node", function (evt) {
        var id = evt.target.id();
        if (id.indexOf("ledger:") === 0) return;
        document.dispatchEvent(
          new CustomEvent("assure:jdf:selected", { detail: { nodeId: id } })
        );
        var canvas = global.__assureJdf;
        if (canvas && typeof canvas.selectNodeForRefine === "function") {
          canvas.selectNodeForRefine(id, { toast: false });
        }
        var el = document.querySelector('.jdf-node[data-node-id="' + id + '"]');
        if (el && typeof el.scrollIntoView === "function") {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          el.classList.add("cross-flash");
          window.setTimeout(function () {
            el.classList.remove("cross-flash");
          }, 900);
        }
      });
      mount._cy = cy;
      return;
    }
    var list = document.createElement("ul");
    list.className = "reasoning-graph-fallback";
    data.nodes.forEach(function (n) {
      var li = document.createElement("li");
      li.textContent = (n.kind === "section" ? "§ " : "• ") + n.label;
      li.dataset.nodeId = n.id;
      li.addEventListener("click", function () {
        var canvas = global.__assureJdf;
        if (canvas && typeof canvas.selectNodeForRefine === "function") {
          canvas.selectNodeForRefine(n.id, { toast: false });
        }
      });
      list.appendChild(li);
    });
    mount.appendChild(list);
  }

  function toggleReasoningDrawer(forceOpen) {
    var drawer = document.getElementById("reasoning-graph-drawer");
    var btn = document.getElementById("wow-reasoning-graph-btn");
    if (!drawer) return;
    var open = typeof forceOpen === "boolean" ? forceOpen : drawer.hidden;
    drawer.hidden = !open;
    if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      var tree = global.__assureJdf && global.__assureJdf.tree;
      renderReasoningGraph(tree);
    }
  }

  function bindReasoningToggle() {
    var btn = document.getElementById("wow-reasoning-graph-btn");
    var close = document.getElementById("reasoning-graph-close");
    if (btn && !btn.dataset.wowBound) {
      btn.dataset.wowBound = "1";
      btn.addEventListener("click", function () {
        toggleReasoningDrawer();
      });
    }
    if (close && !close.dataset.wowBound) {
      close.dataset.wowBound = "1";
      close.addEventListener("click", function () {
        toggleReasoningDrawer(false);
      });
    }
    document.addEventListener("assure:jdf:rendered", function () {
      var drawer = document.getElementById("reasoning-graph-drawer");
      if (drawer && !drawer.hidden) {
        renderReasoningGraph(global.__assureJdf && global.__assureJdf.tree);
      }
    });
  }

  function init() {
    var stampToggle = document.getElementById("ink-stamp-toggle");
    if (stampToggle && !stampToggle.dataset.wowBound) {
      stampToggle.dataset.wowBound = "1";
      try {
        stampToggle.checked = localStorage.getItem("assure_ink_stamps") === "1";
      } catch (_) {}
      document.body.classList.toggle("ink-stamps-on", stampToggle.checked);
      stampToggle.addEventListener("change", function () {
        document.body.classList.toggle("ink-stamps-on", stampToggle.checked);
        try {
          localStorage.setItem("assure_ink_stamps", stampToggle.checked ? "1" : "0");
        } catch (_) {}
        if (stampToggle.checked) refreshStamps();
      });
    }
    if (!enabled()) return;
    ensureLaserBeam();
    bindReasoningToggle();
    bindRenderRefresh();
    if (global.lucide && typeof global.lucide.createIcons === "function") {
      try {
        if (document.querySelector("i[data-lucide]")) {
          global.lucide.createIcons();
        }
      } catch (_) {}
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  function lucideIconEl(doc, name) {
    doc = doc || document;
    var svg = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "lucide-icon");
    svg.setAttribute("aria-hidden", "true");
    var use = doc.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#icon-" + name);
    svg.appendChild(use);
    return svg;
  }

  global.AssureLucideIcon = lucideIconEl;

  global.AssureWowEffects = {
    enabled: enabled,
    onCompileStart: onCompileStart,
    onVerified: onVerified,
    progressToNode: progressToNode,
    extendBeamTo: extendBeamTo,
    extendLaserBeamToNode: extendLaserBeamToNode,
    applyInkStamp: applyInkStamp,
    syncNodeStamp: syncNodeStamp,
    onAllGuttersVerified: onAllGuttersVerified,
    onRedhatComplete: onRedhatComplete,
    showDiffXRay: showDiffXRay,
    hideDiffXRay: hideDiffXRay,
    toggleReasoningDrawer: toggleReasoningDrawer,
    renderReasoningGraph: renderReasoningGraph,
    refreshStamps: refreshStamps,
  };
})(typeof window !== "undefined" ? window : globalThis);
