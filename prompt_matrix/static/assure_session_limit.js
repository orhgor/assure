(function (global) {
  "use strict";

  var LIMIT = 15;
  var STORAGE_KEY = "assure_session_compiles";

  function t(key, fallback, vars) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, vars || {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback || key;
  }

  function $(id) {
    return document.getElementById(id);
  }

  function getCount() {
    try {
      var raw = sessionStorage.getItem(STORAGE_KEY);
      var n = parseInt(raw || "0", 10);
      return Number.isFinite(n) && n > 0 ? n : 0;
    } catch (_) {
      return 0;
    }
  }

  function setCount(n) {
    try {
      sessionStorage.setItem(STORAGE_KEY, String(n));
    } catch (_) {}
  }

  function remaining() {
    return Math.max(0, LIMIT - getCount());
  }

  function isLimited() {
    return getCount() >= LIMIT;
  }

  function increment() {
    var next = getCount() + 1;
    setCount(next);
    applyUi();
    return next;
  }

  function tryConsume() {
    if (isLimited()) {
      applyUi();
      return false;
    }
    increment();
    return true;
  }

  function setButtonLimited(btn, limited) {
    if (!btn) return;
    btn.disabled = !!limited;
    if (limited) {
      btn.setAttribute("data-session-limited", "1");
      btn.textContent = t("safeguard.session.limit_reached", "Session Limit Reached");
      btn.setAttribute(
        "data-tooltip",
        t("safeguard.session.tooltip", "Session compile limit reached. Use Send feedback to tell us what you need.")
      );
      btn.setAttribute("data-i18n-tooltip", "safeguard.session.tooltip");
      btn.classList.add("is-session-limited");
    } else {
      btn.removeAttribute("data-session-limited");
      btn.classList.remove("is-session-limited");
    }
  }

  function restoreCompileButton(btn) {
    if (!btn || btn.getAttribute("data-session-limited") !== "1") return;
    btn.disabled = false;
    btn.removeAttribute("data-session-limited");
    btn.classList.remove("is-session-limited");
    btn.textContent = t("generate.compile", "Compile Document");
    btn.setAttribute("data-i18n", "generate.compile");
    btn.setAttribute(
      "data-tooltip",
      t("tooltip.compile", "Stream a draft, compile JDF nodes, and infer locks")
    );
    btn.setAttribute("data-i18n-tooltip", "tooltip.compile");
  }

  function restoreInquireButton(btn) {
    if (!btn || btn.getAttribute("data-session-limited") !== "1") return;
    btn.disabled = false;
    btn.removeAttribute("data-session-limited");
    btn.classList.remove("is-session-limited");
    btn.textContent = t("jdf.inquire", "Refactor Node");
    btn.setAttribute("data-i18n", "jdf.inquire");
    btn.removeAttribute("data-tooltip");
    btn.removeAttribute("data-i18n-tooltip");
  }

  function applyUi() {
    var badge = $("session-compile-limit");
    var limited = isLimited();
    var left = remaining();

    if (badge) {
      if (limited) {
        badge.hidden = false;
        badge.textContent = t("safeguard.session.limit_reached", "Session Limit Reached");
        badge.setAttribute("data-i18n", "safeguard.session.limit_reached");
        badge.setAttribute(
          "data-tooltip",
          t("safeguard.session.tooltip", "Session compile limit reached. Use Send feedback to tell us what you need.")
        );
        badge.setAttribute("data-i18n-tooltip", "safeguard.session.tooltip");
      } else if (left < LIMIT) {
        badge.hidden = false;
        badge.textContent = t("safeguard.session.remaining", "{n} compiles remaining", { n: left });
        badge.setAttribute("data-i18n", "safeguard.session.remaining");
        badge.setAttribute("data-i18n-vars", JSON.stringify({ n: left }));
        badge.removeAttribute("data-tooltip");
        badge.removeAttribute("data-i18n-tooltip");
      } else {
        badge.hidden = true;
        badge.textContent = "";
      }
    }

    var compileBtn = $("generate-compile-btn");
    var inquireBtn = $("btn-inquire");
    var demoBtn = $("demo-redhat-btn");

    if (limited) {
      setButtonLimited(compileBtn, true);
      setButtonLimited(inquireBtn, true);
      if (demoBtn) demoBtn.disabled = true;
    } else {
      restoreCompileButton(compileBtn);
      restoreInquireButton(inquireBtn);
      if (demoBtn) demoBtn.disabled = false;
    }
  }

  function init() {
    applyUi();
  }

  global.AssureSessionLimit = {
    LIMIT: LIMIT,
    getCount: getCount,
    remaining: remaining,
    isLimited: isLimited,
    increment: increment,
    tryConsume: tryConsume,
    applyUi: applyUi,
    init: init,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(window);
