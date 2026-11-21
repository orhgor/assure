/**
 * gtag-init.js — Google Analytics config moved from inline script in gtag.html
 */
(function () {
  "use strict";

  window.dataLayer = window.dataLayer || [];
  function gtag() {
    dataLayer.push(arguments);
  }
  gtag("js", new Date());
  gtag("config", "G-54F5NE9Y0P");

  // Expose for compatibility
  window.gtag = gtag;
})();