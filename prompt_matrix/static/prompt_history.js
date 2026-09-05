/**
 * Recent compile prompts in the left pane. localStorage only — no network.
 */
(function (global) {
  "use strict";

  var KEY = "assure_prompt_history";
  var MAX = 5;

  function t(key, fallback) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback || key;
  }

  function load() {
    try {
      var raw = localStorage.getItem(KEY);
      var list = raw ? JSON.parse(raw) : [];
      return Array.isArray(list) ? list : [];
    } catch (_) {
      return [];
    }
  }

  function save(list) {
    try {
      localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX)));
    } catch (_) {}
  }

  function render() {
    var host = document.getElementById("generate-prompt-history");
    if (!host) return;
    var list = load();
    host.innerHTML = "";
    if (!list.length) {
      var empty = document.createElement("p");
      empty.className = "hint";
      empty.setAttribute("data-i18n", "generate.recent_empty");
      empty.textContent = t("generate.recent_empty", "No recent prompts yet.");
      host.appendChild(empty);
      return;
    }
    list.forEach(function (item, idx) {
      var row = document.createElement("div");
      row.className = "prompt-history-row";
      var text = document.createElement("button");
      text.type = "button";
      text.className = "prompt-history-text btn-ghost";
      text.textContent = (item.text || "").slice(0, 120);
      text.title = item.text || "";
      text.setAttribute("aria-label", t("generate.reuse", "Reuse") + ": " + (item.text || ""));
      text.addEventListener("click", function () {
        reuse(idx);
      });
      var meta = document.createElement("span");
      meta.className = "prompt-history-meta hint";
      if (item.status === "ok") {
        meta.textContent = t("generate.history_ok", "Compiled") +
          (item.nodes != null ? " · " + item.nodes : "");
      } else if (item.status === "fail") {
        meta.textContent = t("generate.history_fail", "Failed");
      } else {
        meta.textContent = t("generate.history_pending", "Working…");
      }
      var reuseBtn = document.createElement("button");
      reuseBtn.type = "button";
      reuseBtn.className = "btn btn-outline btn-sm";
      reuseBtn.setAttribute("data-i18n", "generate.reuse");
      reuseBtn.textContent = t("generate.reuse", "Reuse");
      reuseBtn.addEventListener("click", function () {
        reuse(idx);
      });
      row.appendChild(text);
      row.appendChild(meta);
      row.appendChild(reuseBtn);
      host.appendChild(row);
    });
  }

  function push(text, extra) {
    var trimmed = (text || "").trim();
    if (!trimmed) return;
    var list = load().filter(function (item) {
      return item.text !== trimmed;
    });
    list.unshift({
      text: trimmed,
      at: Date.now(),
      model: (extra && extra.model) || "",
      cycle: (extra && extra.cycle) || "",
      status: (extra && extra.status) || "pending",
      nodes: extra && extra.nodes != null ? extra.nodes : null,
    });
    save(list);
    render();
  }

  function markLatest(patch) {
    var list = load();
    if (!list.length) return;
    list[0] = Object.assign({}, list[0], patch || {});
    save(list);
    render();
  }

  function reuse(idx) {
    var list = load();
    var item = list[idx];
    if (!item) return;
    var intent = document.getElementById("generate-intent");
    if (intent) {
      intent.value = item.text;
      intent.focus();
    }
  }

  function bind() {
    render();
    // Rows are built in JS, so a language switch has to rebuild them.
    document.addEventListener("assure:i18n", render);
  }

  global.AssurePromptHistory = {
    bind: bind,
    push: push,
    load: load,
    render: render,
    markLatest: markLatest,
    reuse: reuse,
  };
})(typeof window !== "undefined" ? window : this);
