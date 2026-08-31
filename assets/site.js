(function () {
  "use strict";

  var root = document.documentElement;
  var appOrigin = (root.getAttribute("data-app-origin") || "").trim();
  var localOrigin = (root.getAttribute("data-local-origin") || "http://127.0.0.1:8765").trim();
  var target = appOrigin || localOrigin;

  document.querySelectorAll("[data-download]").forEach(function (el) {
    var os = el.getAttribute("data-download") || "";
    var url = (root.getAttribute("data-download-" + os) || "").trim();
    if (url) {
      el.setAttribute("href", url);
      el.setAttribute("rel", "noopener");
      el.removeAttribute("aria-disabled");
      el.classList.remove("is-disabled");
      return;
    }
    el.classList.add("is-disabled");
    el.setAttribute("aria-disabled", "true");
    el.setAttribute("href", "#download-note");
    el.addEventListener("click", function (e) {
      e.preventDefault();
      var note = document.getElementById("download-note");
      if (note && typeof note.focus === "function") note.focus();
    });
  });

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

  root.classList.add("js");

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function revealOnScroll() {
    var nodes = document.querySelectorAll(".reveal");
    if (!nodes.length) return;
    if (reduceMotion || !("IntersectionObserver" in window)) {
      nodes.forEach(function (el) {
        el.classList.add("is-visible");
      });
      return;
    }
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-visible");
          io.unobserve(entry.target);
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.08 }
    );
    nodes.forEach(function (el) {
      io.observe(el);
    });
  }

  function typeHeroLine() {
    var el = document.querySelector(".hero-typed");
    if (!el) return;
    var full = (el.getAttribute("data-type") || el.textContent || "").replace(/\s+/g, " ").trim();
    if (!full) return;
    if (reduceMotion) {
      el.textContent = full;
      return;
    }
    el.textContent = "";
    el.classList.add("is-typing");
    var i = 0;
    function tick() {
      if (i >= full.length) {
        el.classList.remove("is-typing");
        return;
      }
      i += 1;
      el.textContent = full.slice(0, i);
      window.setTimeout(tick, 42);
    }
    window.setTimeout(tick, 400);
  }

  revealOnScroll();
  typeHeroLine();
})();
