/**
 * Single source of truth for Founder Workbench vs legacy compiler shell.
 * Default: founder ON unless localStorage is explicitly "0" or legacy URL.
 * Never reloads or overwrites "0" (legacy Playwright tests set "0" in prime_page).
 */
(function (global) {
  "use strict";

  var STORAGE_KEY = "assure_founder_workbench";

  function readFlag() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch (_) {
      return null;
    }
  }

  function isLegacyUrl() {
    try {
      var params = new URLSearchParams(location.search);
      if (params.get("legacy") === "1") return true;
      if (params.get("view") === "projects" || params.get("tool") === "projects") return true;
      var hash = (location.hash || "").replace(/^#/, "");
      if (hash.indexOf("view=") === 0 || hash.indexOf("tool=") === 0) {
        var hashParams = new URLSearchParams(hash);
        var named = hashParams.get("view") || hashParams.get("tool");
        if (named === "projects") return true;
      }
    } catch (_) {}
    return false;
  }

  /** Founder mode unless flag is exactly "0" or legacy URL. */
  function isFounderMode() {
    if (isLegacyUrl()) return false;
    return readFlag() !== "0";
  }

  function persistDefaultFlag() {
    if (readFlag() !== null) return;
    try {
      localStorage.setItem(STORAGE_KEY, "1");
    } catch (_) {}
  }

  function applyBodyClasses() {
    var founder = isFounderMode();
    document.body.classList.toggle("founder-workbench", founder);
    document.body.classList.toggle("legacy-workbench", !founder);
    document.body.classList.toggle("founder-mode-active", founder);
    applyAppRootClasses();
  }

  function applyAppRootClasses() {
    var founder = isFounderMode();
    var app = document.getElementById("assure-app");
    if (app) app.classList.toggle("founder-mode-active", founder);
  }

  function init() {
    persistDefaultFlag();
    applyBodyClasses();
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", applyAppRootClasses, { once: true });
    }
  }

  global.AssureFounderMode = {
    STORAGE_KEY: STORAGE_KEY,
    isEnabled: isFounderMode,
    isLegacyUrl: isLegacyUrl,
    applyBodyClasses: applyBodyClasses,
    applyAppRootClasses: applyAppRootClasses,
    init: init,
  };

  init();
})(window);
