/**
 * site-init.js — Initialization logic moved from inline scripts
 * Reads values from data-attributes on <html> set by base.html
 */
(function () {
  "use strict";

  // Read page config from <html> data attributes
  var html = document.documentElement;
  var pageData = {
    locale: html.getAttribute("data-locale") || "en",
    pageSuffix: html.getAttribute("data-page-suffix") || "",
    localePaths: {},
  };

  // Parse locale paths from JSON attribute
  var localePathsAttr = html.getAttribute("data-locale-paths");
  if (localePathsAttr) {
    try {
      pageData.localePaths = JSON.parse(localePathsAttr);
    } catch (e) {
      console.warn("Failed to parse locale paths:", e);
    }
  }

  // Expose for other scripts
  window.__ASSURE_PAGE_DATA = pageData;

  // Mobile menu toggle
  function initMobileMenu() {
    var btn = document.getElementById("mobile-menu-btn");
    var menu = document.getElementById("mobile-menu");
    if (!btn || !menu) {
      // Retry after a short delay if elements not ready
      setTimeout(initMobileMenu, 50);
      return;
    }

    // Prevent double initialization
    if (btn.dataset.initialized === "true") {
      return;
    }
    btn.dataset.initialized = "true";

    function setOpen(open) {
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      menu.hidden = !open;
      document.body.style.overflow = open ? "hidden" : "";
    }

    // Ensure initial state
    menu.hidden = true;
    btn.setAttribute("aria-expanded", "false");

    btn.addEventListener("click", function () {
      setOpen(btn.getAttribute("aria-expanded") !== "true");
    });

    menu.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        setOpen(false);
      });
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && btn.getAttribute("aria-expanded") === "true") {
        setOpen(false);
        btn.focus();
      }
    });
  }

  // Feature flag for conflict-pulse animation
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.documentElement.classList.add('fx');
  }

  // Initialize mobile menu immediately (script is at end of body, DOM should be ready)
  initMobileMenu();
  syncPrivacyToc();

  // Also initialize on DOMContentLoaded as fallback
  document.addEventListener("DOMContentLoaded", function () {
    initMobileMenu();
    syncPrivacyToc();
  });

  // On desktop, <details> is open by default (via HTML attribute).
  // On mobile (<900px), close it so the summary toggle works.
  function syncPrivacyToc() {
    var el = document.querySelector(".privacy-toc-wrap");
    if (!el || !window.matchMedia) return;
    var mq = window.matchMedia("(max-width: 899px)");
    function apply() {
      if (mq.matches) {
        el.removeAttribute("open");
      } else {
        el.setAttribute("open", "");
      }
    }
    apply();
    if (mq.addEventListener) mq.addEventListener("change", apply);
    else if (mq.addListener) mq.addListener(apply);
  }
})();
