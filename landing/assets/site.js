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
    el.setAttribute("href", "#download-modal");
    el.addEventListener("click", function (e) {
      e.preventDefault();
      openWaitlist();
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

  var waitlistLastFocus = null;

  function openWaitlist() {
    var modal = document.getElementById("download-modal");
    if (!modal) return;
    waitlistLastFocus = document.activeElement;
    modal.hidden = false;
    document.body.classList.add("waitlist-open");
    var name = document.getElementById("waitlist-name");
    if (name) name.focus();
  }

  function closeWaitlist() {
    var modal = document.getElementById("download-modal");
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("waitlist-open");
    if (waitlistLastFocus && typeof waitlistLastFocus.focus === "function") {
      waitlistLastFocus.focus();
    }
  }

  function bindWaitlist() {
    var modal = document.getElementById("download-modal");
    var form = document.getElementById("waitlist-form");
    var done = document.getElementById("waitlist-done");
    var err = document.getElementById("waitlist-error");
    document.querySelectorAll("[data-waitlist-open]").forEach(function (el) {
      el.addEventListener("click", function (e) {
        e.preventDefault();
        openWaitlist();
      });
    });
    if (!modal) return;
    modal.querySelectorAll("[data-waitlist-close]").forEach(function (el) {
      el.addEventListener("click", function () {
        closeWaitlist();
      });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !modal.hidden) closeWaitlist();
    });
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (err) {
        err.hidden = true;
        err.textContent = "";
      }
      var nameEl = document.getElementById("waitlist-name");
      var emailEl = document.getElementById("waitlist-email");
      var name = nameEl ? String(nameEl.value || "").trim() : "";
      var email = emailEl ? String(emailEl.value || "").trim() : "";
      fetch(target.replace(/\/$/, "") + "/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, email: email }),
      })
        .then(function (res) {
          return res.json().catch(function () {
            return {};
          }).then(function (data) {
            return { ok: res.ok, data: data };
          });
        })
        .then(function (result) {
          if (!result.ok) {
            var msg = (result.data && result.data.error) || "Could not join the list. Try again.";
            throw new Error(msg);
          }
          form.hidden = true;
          if (done) done.hidden = false;
        })
        .catch(function (error) {
          if (err) {
            err.hidden = false;
            err.textContent = (error && error.message) || "Could not join the list. Try again.";
          }
        });
    });
  }

  revealOnScroll();
  typeHeroLine();
  bindWaitlist();
})();
