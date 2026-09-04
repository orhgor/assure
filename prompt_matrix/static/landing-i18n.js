(function () {
  "use strict";

  var strings = window.__assureLandingStrings || {};

  function t(key, fallback) {
    return (strings && strings[key]) || fallback || key;
  }

  function applyI18n() {
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      el.textContent = t(el.getAttribute("data-i18n"), el.textContent);
    });
    document.querySelectorAll("[data-i18n-html]").forEach(function (el) {
      el.innerHTML = t(el.getAttribute("data-i18n-html"), el.innerHTML);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
      el.placeholder = t(el.getAttribute("data-i18n-placeholder"), el.placeholder);
    });
    document.querySelectorAll("[data-i18n-aria]").forEach(function (el) {
      el.setAttribute("aria-label", t(el.getAttribute("data-i18n-aria"), el.getAttribute("aria-label") || ""));
    });
  }

  function initLocale() {
    var select = document.getElementById("landing-lang");
    if (!select) return;
    select.addEventListener("change", function () {
      var u = new URL(window.location.href);
      u.searchParams.set("lang", select.value);
      window.location.href = u.pathname + u.search + u.hash;
    });
  }

  function initNavMenu() {
    var toggle = document.getElementById("nav-menu-toggle");
    var links = document.querySelector(".nav-links");
    if (!toggle || !links) return;
    toggle.addEventListener("click", function () {
      var collapsed = links.classList.toggle("is-collapsed");
      toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    });
    if (window.matchMedia("(max-width: 640px)").matches) {
      links.classList.add("is-collapsed");
      toggle.setAttribute("aria-expanded", "false");
    }
  }

  function initAdvancedToggle() {
    var btn = document.getElementById("sandbox-advanced-toggle");
    var drawer = document.getElementById("advanced-model-drawer");
    if (!btn || !drawer) return;
    btn.addEventListener("click", function () {
      var open = drawer.classList.toggle("hidden") === false;
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    applyI18n();
    initLocale();
    initNavMenu();
    initAdvancedToggle();
  });

  window.__assureT = t;
  window.__assureTf = function (key, fallback) {
    return t(key, fallback);
  };
})();
