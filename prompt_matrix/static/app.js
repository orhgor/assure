(function () {
  "use strict";

  var STORAGE_GEMINI = "assure_gemini_key";
  var STORAGE_CLAUDE = "assure_claude_key";
  var SETUP_GUIDE_URL = "https://getassureai.com/app";
  var nativeFetch = window.fetch.bind(window);

  var MODEL_LABELS = {
    gemini: "Gemini",
    deepseek: "DeepSeek",
    claude: "Claude",
    kimi: "Kimi",
    ollama: "Ollama",
  };

  function t(key, fallback) {
    if (window.state && window.state.strings && window.state.strings[key]) {
      return window.state.strings[key];
    }
    return fallback || key;
  }

  function getGeminiKey() {
    try {
      return localStorage.getItem(STORAGE_GEMINI) || "";
    } catch (_) {
      return "";
    }
  }

  function getClaudeKey() {
    try {
      return localStorage.getItem(STORAGE_CLAUDE) || "";
    } catch (_) {
      return "";
    }
  }

  function hasAnyBrowserKey() {
    return Boolean(getGeminiKey().trim() || getClaudeKey().trim());
  }

  function providerHeaders() {
    var headers = {};
    var gemini = getGeminiKey().trim();
    var claude = getClaudeKey().trim();
    if (gemini) headers["X-Gemini-Key"] = gemini;
    if (claude) headers["X-Claude-Key"] = claude;
    return headers;
  }

  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var promise = Promise.resolve([input, init || {}]);

    if (url.indexOf("/api/") !== -1) {
      promise = promise.then(function (pair) {
        var next = pair[1] ? Object.assign({}, pair[1]) : {};
        var headers = new Headers(next.headers || {});
        var keys = providerHeaders();
        Object.keys(keys).forEach(function (name) {
          if (!headers.has(name)) headers.set(name, keys[name]);
        });
        next.headers = headers;
        return [pair[0], next];
      });

      if (typeof window.getClerkToken === "function") {
        promise = window.getClerkToken().then(function (token) {
          return promise.then(function (pair) {
            if (!token) return pair;
            var next = pair[1] ? Object.assign({}, pair[1]) : {};
            var headers = new Headers(next.headers || {});
            if (!headers.has("Authorization")) {
              headers.set("Authorization", "Bearer " + token);
            }
            next.headers = headers;
            return [pair[0], next];
          });
        }).catch(function () {
          return promise;
        });
      }
    }

    return promise.then(function (pair) {
      return nativeFetch(pair[0], pair[1]).then(function (res) {
        if (url.indexOf("/api/") !== -1 && (res.status === 401 || res.status === 403)) {
          var authPath = url.indexOf("/api/auth/") !== -1;
          if (!authPath) {
            res.clone().text().then(function (body) {
              var lower = (body || "").toLowerCase();
              var clerkBlock = lower.indexOf("sign in") !== -1;
              if (!clerkBlock) {
                showProviderAuthError();
              }
            }).catch(function () {});
          }
        }
        return res;
      });
    });
  };

  function ensureSettingsButton() {
    if (document.getElementById("settings-open")) return;
    var meta = document.querySelector(".header-meta");
    if (!meta) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "meta-link settings-link";
    btn.id = "settings-open";
    btn.setAttribute("data-i18n", "settings.button");
    btn.textContent = t("settings.button", "Settings");
    btn.title = t("settings.button", "Settings");
    meta.insertBefore(btn, meta.firstChild);
    btn.addEventListener("click", function () {
      openSettingsModal(false);
    });
  }

  function showProviderAuthError() {
    var el = document.getElementById("status");
    if (!el) return;
    el.className = "status bad";
    el.innerHTML =
      'Invalid API key. Verify your credentials in the <a href="' +
      SETUP_GUIDE_URL +
      '" target="_blank" rel="noopener noreferrer" style="text-decoration:underline;">Setup Guide</a>.';
  }

  function openSettingsModal(showBanner) {
    var modal = document.getElementById("settings-modal");
    if (!modal) return;
    var gemini = document.getElementById("settings-gemini-key");
    var claude = document.getElementById("settings-claude-key");
    if (gemini) gemini.value = getGeminiKey();
    if (claude) claude.value = getClaudeKey();
    var banner = document.getElementById("settings-keys-banner");
    if (banner) {
      if (showBanner) {
        banner.hidden = false;
        banner.innerHTML =
          'Please add a Gemini or Claude API key to run verification. Need a key? <a href="' +
          SETUP_GUIDE_URL +
          '" target="_blank" rel="noopener noreferrer" style="text-decoration:underline;">See the setup guide</a>.';
      } else {
        banner.hidden = true;
      }
    }
    if (typeof modal.showModal === "function") modal.showModal();
  }

  function closeSettingsModal() {
    var modal = document.getElementById("settings-modal");
    if (modal && typeof modal.close === "function") modal.close();
  }

  function saveSettingsKeys() {
    var gemini = document.getElementById("settings-gemini-key");
    var claude = document.getElementById("settings-claude-key");
    try {
      localStorage.setItem(STORAGE_GEMINI, gemini ? gemini.value.trim() : "");
      localStorage.setItem(STORAGE_CLAUDE, claude ? claude.value.trim() : "");
    } catch (_) {}
    closeSettingsModal();
  }

  function needsBrowserKeysForRun() {
    var localEl = document.getElementById("local");
    if (localEl && localEl.checked) return false;
    var target = window.state && window.state.target;
    if (target === "ollama" || target === "cursor") return false;
    return true;
  }

  function requireBrowserKeysOrOpenSettings() {
    if (!needsBrowserKeysForRun()) return true;
    if (hasAnyBrowserKey()) return true;
    openSettingsModal(true);
    return false;
  }

  function auditCounts(data) {
    var grounded = 0;
    var inferred = 0;
    if (data) {
      var g = data.grounded_spans || (data.quality && data.quality.grounded_spans) || [];
      var i = data.inferred_spans || (data.quality && data.quality.inferred_spans) || [];
      grounded = Array.isArray(g) ? g.length : 0;
      inferred = Array.isArray(i) ? i.length : 0;
    }
    if (!grounded && !inferred) {
      grounded = document.querySelectorAll("#answer .grounded, #reply-content .grounded").length;
      inferred = document.querySelectorAll("#answer .inferred, #reply-content .inferred").length;
    }
    return { grounded: grounded, inferred: inferred };
  }

  function formatGroundCount(data) {
    var counts = auditCounts(data);
    if (counts.grounded + counts.inferred > 0) {
      return counts.grounded + " grounded, " + counts.inferred + " inferred";
    }
    return "";
  }

  function paintModelAttribution(data) {
    var strip = document.getElementById("trust-strip");
    if (!strip) return;
    strip.innerHTML = "";
    var used = (data && data.models_used) || [];
    if (!Array.isArray(used) || !used.length) {
      strip.hidden = true;
      return;
    }
    used.forEach(function (id) {
      var li = document.createElement("li");
      var pill = document.createElement("span");
      pill.className = "model-chip";
      pill.textContent = MODEL_LABELS[id] || id;
      li.appendChild(pill);
      strip.appendChild(li);
    });
    strip.hidden = false;
  }

  function confidenceScoreClass(value) {
    // Score buckets mirror the badge thresholds: >=80 good, >=60 warn,
    // anything else danger; unknown is muted, never a fabricated 0.
    if (value == null) return "quality-chip--muted";
    var n = Number(value);
    if (n >= 80) return "quality-chip--good";
    if (n >= 60) return "quality-chip--warn";
    return "quality-chip--danger";
  }

  function makeConfidenceChip(label, value) {
    var chip = document.createElement("span");
    chip.className = "quality-chip " + confidenceScoreClass(value);
    chip.textContent =
      label + " " + (value == null ? "—" : Math.round(Number(value)));
    return chip;
  }

  function redhatSummary(data) {
    // Red-Hat status chip: open findings are a danger chip and a warning
    // note; a clean pass with any quality data is a pass chip. Unknown
    // (no Red-Hat information at all) renders nothing — no fake "clear".
    var open =
      data && data.redhat_open_count != null
        ? Number(data.redhat_open_count)
        : data && data.redhat_open != null
        ? Number(data.redhat_open)
        : null;
    if (open != null && open > 0) {
      return { chip: "Red-Hat " + open + " open", cls: "quality-chip--danger", note: open + " open Red-Hat finding(s)" };
    }
    if (open != null && open === 0) {
      return { chip: "Red-Hat clear", cls: "quality-chip--state-pass", note: null };
    }
    return null;
  }

  function updateGroundDisplay(data) {
    var line = document.getElementById("confidence-line");
    if (!line) return;
    var groundText = formatGroundCount(data);

    // Provenance ground count stays its own signal; the quality layers,
    // gate, and Red-Hat status render as separate chips so parse quality
    // never reads as a verification result.
    var hasQuality =
      data &&
      (data.parse_quality != null ||
        data.structured_quality != null ||
        data.verification_quality != null ||
        data.document_quality != null ||
        data.confidence != null ||
        data.gate_status != null ||
        data.redhat_open_count != null ||
        (data.missing_assets && data.missing_assets.length) ||
        (data.lowConfidenceNodes && data.lowConfidenceNodes.length));

    line.textContent = "";
    var base = (data && data.confidence_text && String(data.confidence_text).trim()) || "";
    if (groundText || base || hasQuality) {
      var lead = document.createElement("span");
      lead.className = "confidence-line__lead";
      lead.textContent = "Confidence";
      line.appendChild(lead);
    }
    if (groundText) {
      var ground = document.createElement("span");
      ground.className = "quality-chip quality-chip--muted";
      ground.textContent = groundText;
      line.appendChild(ground);
    } else if (base && !/0 claims were checked/i.test(base)) {
      var baseChip = document.createElement("span");
      baseChip.className = "quality-chip quality-chip--muted";
      baseChip.textContent = base;
      line.appendChild(baseChip);
    }
    if (!hasQuality) {
      var renderedAny = line.children && line.children.length > 1;
      line.hidden = !renderedAny;
      return;
    }

    var rh = redhatSummary(data);
    if (rh) {
      var rhChip = document.createElement("span");
      rhChip.className = "quality-chip " + rh.cls;
      rhChip.textContent = rh.chip;
      line.appendChild(rhChip);
    }
    if (data.gate_status) {
      var gateState = String(data.gate_status).toLowerCase();
      var gate = document.createElement("span");
      gate.className =
        "quality-chip " +
        (gateState === "pass" || gateState === "passing"
          ? "quality-chip--state-pass"
          : gateState === "blocked" || gateState === "fail"
          ? "quality-chip--state-blocked"
          : "quality-chip--state-review");
      gate.textContent = "Gate " + String(data.gate_status);
      line.appendChild(gate);
    }

    line.appendChild(makeConfidenceChip("Overall", data.document_quality != null ? data.document_quality : data.confidence));
    line.appendChild(makeConfidenceChip("Parse", data.parse_quality));
    line.appendChild(makeConfidenceChip("Structured", data.structured_quality));
    line.appendChild(makeConfidenceChip("Verification", data.verification_quality));

    var lowCount =
      data.low_confidence_count != null
        ? Number(data.low_confidence_count)
        : data.lowConfidenceNodes
        ? data.lowConfidenceNodes.length
        : 0;
    var lowPages =
      data.low_confidence_page_count != null
        ? Number(data.low_confidence_page_count)
        : data.lowConfidencePages
        ? data.lowConfidencePages.length
        : 0;
    var notes = [];
    if (lowCount > 0) notes.push(lowCount + " low-confidence node(s)");
    if (lowPages > 0) notes.push(lowPages + " low-confidence page(s)");
    if (rh && rh.note) notes.push(rh.note);
    var missingAssets = data.missing_assets || data.missingAssets || [];
    var kinds = [];
    for (var i = 0; i < missingAssets.length; i++) {
      var kind = missingAssets[i] && (missingAssets[i].kind || missingAssets[i]);
      if (kind) kinds.push(String(kind));
    }
    if (kinds.length) notes.push("no " + kinds.join(", ") + " in parse");
    for (var n = 0; n < notes.length; n++) {
      var warn = document.createElement("span");
      warn.className = "quality-chip quality-chip--warn";
      warn.textContent = notes[n];
      line.appendChild(warn);
    }

    // Accessible one-line summary of what the chips show.
    line.setAttribute("role", "status");
    line.setAttribute(
      "aria-label",
      "Confidence. " + (line.textContent || "").trim()
    );
    line.hidden = false;
  }

  function bindSettingsModal() {
    var save = document.getElementById("settings-save");
    var close = document.getElementById("settings-close");
    if (save) save.addEventListener("click", saveSettingsKeys);
    if (close) close.addEventListener("click", closeSettingsModal);
  }

  function bindFeedbackThumbs(sendFeedbackFn) {
    var up = document.getElementById("feedback-thumb-up");
    var down = document.getElementById("feedback-thumb-down");
    if (!up || !down || typeof sendFeedbackFn !== "function") return;
    function onVote(rating) {
      sendFeedbackFn(rating).then(function () {
        var row = document.getElementById("feedback-thumbs");
        var thanks = document.getElementById("feedback-thanks");
        if (row) row.hidden = true;
        if (thanks) {
          thanks.hidden = false;
          thanks.textContent = t("feedback.thanks_full", "Thanks for your feedback!");
        }
      });
    }
    up.addEventListener("click", function () { onVote(1); });
    down.addEventListener("click", function () { onVote(0); });
  }

  window.AssureKeys = {
    getGeminiKey: getGeminiKey,
    getClaudeKey: getClaudeKey,
    hasAnyBrowserKey: hasAnyBrowserKey,
    requireBrowserKeysOrOpenSettings: requireBrowserKeysOrOpenSettings,
    openSettingsModal: openSettingsModal,
  };

  window.AssureUI = window.AssureUI || {};
  window.AssureUI.paintModelAttribution = paintModelAttribution;
  window.AssureUI.updateGroundDisplay = updateGroundDisplay;
  window.AssureUI.bindFeedbackThumbs = bindFeedbackThumbs;

  /** Progressive Generate pipeline — delegates to generate.js */
  window.startDraftStream = function () {
    if (window.AssureGenerate && typeof window.AssureGenerate.startDraftStream === "function") {
      window.AssureGenerate.startDraftStream();
    }
  };

  document.addEventListener("DOMContentLoaded", function () {
    ensureSettingsButton();
    bindSettingsModal();
  });
})();
