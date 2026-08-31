(function () {
  "use strict";

  var root = document.documentElement;
  var appOrigin = (root.getAttribute("data-app-origin") || "").trim();
  var localOrigin = (root.getAttribute("data-local-origin") || "http://127.0.0.1:8765").trim();
  var target = appOrigin || localOrigin;

  document.querySelectorAll("[data-cta='app']").forEach(function (el) {
    var path = el.getAttribute("data-cta-path") || "";
    var href = target.replace(/\/$/, "") + path;
    el.setAttribute("href", href);
    if (appOrigin) {
      el.setAttribute("rel", "noopener");
    }
  });

  var year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());
})();
