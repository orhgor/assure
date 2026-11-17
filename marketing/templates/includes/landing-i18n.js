/**
 * landing-i18n.js — Client-side i18n for landing pages
 * Reads window.__assureLandingStrings and applies data-i18n / data-i18n-html
 */
(function () {
  'use strict';

  var strings = window.__assureLandingStrings || {};

  function t(key, fallback) {
    return (strings && strings[key]) || fallback || key;
  }

  function interpolate(s, vars) {
    if (!s || !vars) return s;
    Object.keys(vars).forEach(function (k) {
      s = String(s).replace(new RegExp('\\{' + k + '\\}', 'g'), String(vars[k]));
    });
    return s;
  }

  function applyI18n() {
    // Text content
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      if (el.childElementCount) return; // structured parents keep their markup
      var key = el.getAttribute('data-i18n');
      var fb = el.textContent;
      el.textContent = t(key, fb);
    });
    // HTML content
    document.querySelectorAll('[data-i18n-html]').forEach(function (el) {
      var key = el.getAttribute('data-i18n-html');
      var fb = el.innerHTML;
      el.innerHTML = t(key, fb);
    });
    // Placeholders
    document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      var key = el.getAttribute('data-i18n-placeholder');
      var fb = el.placeholder;
      el.placeholder = t(key, fb);
    });
    // Aria labels
    document.querySelectorAll('[data-i18n-aria]').forEach(function (el) {
      var key = el.getAttribute('data-i18n-aria');
      var fb = el.getAttribute('aria-label') || '';
      el.setAttribute('aria-label', t(key, fb));
    });
    // Label attributes
    document.querySelectorAll('[data-i18n-label]').forEach(function (el) {
      var key = el.getAttribute('data-i18n-label');
      var fb = el.getAttribute('data-label') || '';
      el.setAttribute('data-label', t(key, fb));
    });
  }

  function interpolate(s, vars) {
    if (!s || !vars) return s;
    Object.keys(vars).forEach(function (k) {
      s = String(s).replace(new RegExp('\\{' + k + '\\}', 'g'), String(vars[k]));
    });
    return s;
  }

  function t(key, fallback) {
    return (strings && strings[key]) || fallback || key;
  }

  // Expose for inline scripts
  window.__assureT = t;
  window.__assureTf = function (key, fallback, vars) {
    return interpolate(t(key, fallback), vars);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyI18n);
  } else {
    applyI18n();
  }
})();
