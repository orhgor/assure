(function () {
  "use strict";

  var gate = window.AssureAuditGate;
  var PROGRESS_STEPS = gate ? gate.progressSteps() : [
    "Compiling JDF AST…",
    "Inferring truth-ledger locks…",
    "Running Z3 verification…",
    "Red-Hat adversarial audit…",
  ];

  function $(id) {
    return document.getElementById(id);
  }

  function t(key, fallback, vars) {
    if (typeof window.__assureTf === "function") {
      return window.__assureTf(key, fallback, vars || {});
    }
    if (typeof window.__assureT === "function") {
      return window.__assureT(key, fallback);
    }
    return fallback || key;
  }

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text || "";
    return div.innerHTML;
  }

  function nodeLabel(node) {
    if (!node) return "Node";
    if (node.type === "section") return "Section";
    var content = String(node.content || node.title || "");
    if (/\$|\d+%/.test(content)) return "Claim";
    return "Paragraph";
  }

  function nodeDetail(node) {
    var text = String(node.content || node.title || "").trim();
    if (text.length > 120) return text.slice(0, 117) + "…";
    return text;
  }

  function renderNodeTree(nodes) {
    if (!nodes || !nodes.length) {
      return '<div class="jdf-node"><strong>[Node #01]</strong> No nodes compiled.</div>';
    }
    return nodes
      .map(function (node, idx) {
        var num = String(idx + 1).padStart(2, "0");
        var label = nodeLabel(node);
        var detail = escapeHtml(nodeDetail(node));
        var suffix = "";
        if (node.annotations && node.annotations.redhat && node.annotations.redhat.text) {
          suffix = " Red-Hat flagged.";
        } else if (node.annotations && node.annotations.z3 && node.annotations.z3.status === "violation") {
          suffix = " Z3 violation detected.";
        } else if (label === "Claim") {
          suffix = " Symbolically validated.";
        } else {
          suffix = " Isolated into JDF AST.";
        }
        return (
          '<div class="jdf-node"><strong>[Node #' +
          num +
          " - " +
          label +
          "]</strong> " +
          detail +
          suffix +
          "</div>"
        );
      })
      .join("");
  }

  function setProgress(active, stepIndex, label) {
    var progress = $("sandbox-progress");
    var fill = $("sandbox-progress-fill");
    var labelEl = $("sandbox-progress-label");
    if (progress) progress.classList.toggle("hidden", !active);
    if (fill) {
      var pct = active ? Math.min(100, ((stepIndex + 1) / PROGRESS_STEPS.length) * 100) : 0;
      fill.style.width = pct + "%";
    }
    if (labelEl && label) labelEl.textContent = label;
  }

  function animateProgress() {
    var step = 0;
    setProgress(true, step, PROGRESS_STEPS[0]);
    return window.setInterval(function () {
      step = Math.min(step + 1, PROGRESS_STEPS.length - 1);
      setProgress(true, step, PROGRESS_STEPS[step]);
    }, 450);
  }

  function updateBadges(data) {
    if (!gate) return;
    gate.updateZ3Badge($("sandbox-z3-badge"), data);
    gate.updateRedhatBadge($("sandbox-redhat-badge"), data);
  }

  function updateGateSummary(data) {
    if (!gate) return;
    gate.updateGateBanner($("sandbox-gate-summary"), $("sandbox-gate-text"), data);
  }

  function runSandboxTest() {
    var inputEl = $("sandbox-input");
    var output = $("sandbox-output");
    var tree = $("sandbox-node-tree");
    var runBtn = $("sandbox-run-btn");
    var text = (inputEl && inputEl.value) || "";
    text = text.trim();

    if (!text) {
      window.alert("Please enter some text to test.");
      return;
    }

    if (runBtn) runBtn.disabled = true;
    var timer = animateProgress();
    if (output) output.classList.add("hidden");
    var gateEl = $("sandbox-gate-summary");
    if (gateEl) gateEl.classList.add("hidden");

    fetch("/api/sandbox/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text }),
    })
      .then(function (res) {
        return res.json().then(function (body) {
          return { ok: res.ok, body: body };
        });
      })
      .then(function (result) {
        window.clearInterval(timer);
        setProgress(false, 0, "");

        if (!result.ok) {
          window.alert(result.body.error || "Sandbox verification failed.");
          return;
        }

        var data = result.body;
        updateBadges(data);
        if (tree) tree.innerHTML = renderNodeTree(data.nodes || []);

        if (output) {
          output.classList.remove("hidden");
          output.style.opacity = "0";
          window.setTimeout(function () {
            output.style.opacity = "1";
          }, 50);
        }

        updateGateSummary(data);

        var bridge = $("workspace-bridge");
        if (bridge) bridge.classList.remove("hidden");
      })
      .catch(function (err) {
        window.clearInterval(timer);
        setProgress(false, 0, "");
        window.alert(err && err.message ? err.message : "Network error during sandbox audit.");
      })
      .finally(function () {
        if (runBtn) runBtn.disabled = false;
      });
  }

  function toggleAdvancedSettings() {
    var drawer = $("advanced-model-drawer");
    if (drawer) drawer.classList.toggle("hidden");
  }

  function sendToWorkbench() {
    var text = ($("sandbox-input") && $("sandbox-input").value) || "";
    text = text.trim();
    if (!text) {
      window.alert("Please enter some text to test.");
      return;
    }
    var modelEl = $("model-select");
    var model = modelEl ? modelEl.value : "claude";
    try {
      sessionStorage.setItem("assure_landing_draft", text);
      sessionStorage.setItem("assure_landing_model", model);
    } catch (_) {}
    window.location.href = "/app?mode=workbench&import=latest";
  }

  document.addEventListener("DOMContentLoaded", function () {
    var runBtn = $("sandbox-run-btn");
    if (runBtn) runBtn.addEventListener("click", runSandboxTest);
    var advToggle = $("sandbox-advanced-toggle");
    if (advToggle) advToggle.addEventListener("click", toggleAdvancedSettings);
    var sendBtn = $("sandbox-send-workbench");
    if (sendBtn) sendBtn.addEventListener("click", sendToWorkbench);
    var gateText = $("sandbox-gate-text");
    if (gateText && !gateText.textContent.trim()) {
      gateText.textContent = t(
        "audit.gate.banner_pending",
        "Pre-Flight Gate — audit must pass before export."
      );
    }
  });
})();
