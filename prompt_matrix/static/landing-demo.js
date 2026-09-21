(function () {
  "use strict";

  function t(key, fallback) {
    if (typeof window.__assureT === "function") {
      return window.__assureT(key, fallback);
    }
    var strings = window.__assureLandingStrings || {};
    return strings[key] || fallback || key;
  }

  var SETS = ["journalism", "academic", "legal"];

  var docBody;
  var astCards;
  var sweep;
  var commandPrompt;
  var compileBtn;
  var typeTimer = null;
  var activeSet = "legal";
  var reduceMotion = false;

  function typeOnce(el, text, speed) {
    clearInterval(typeTimer);
    el.textContent = "";
    el.classList.remove("blink");
    var i = 0;
    typeTimer = setInterval(function () {
      el.textContent = text.slice(0, i + 1);
      i += 1;
      if (i >= text.length) {
        clearInterval(typeTimer);
        el.classList.add("blink");
      }
    }, speed);
  }

  function paraHtml(setKey, index) {
    return t("landing.demo." + setKey + ".p" + index, "");
  }

  function cardText(setKey, index) {
    return t("landing.demo." + setKey + ".c" + index, "");
  }

  function render(setKey, animate) {
    activeSet = setKey;
    var paras = [paraHtml(setKey, 1), paraHtml(setKey, 2), paraHtml(setKey, 3)];
    docBody.innerHTML = paras
      .map(function (p) {
        return "<p>" + p + "</p>";
      })
      .join("");

    astCards.innerHTML = "";
    var okFlags = [true, false, true];
    okFlags.forEach(function (ok, i) {
      var card = document.createElement("div");
      card.className = "lw-ast-card" + (ok ? "" : " flaw");
      var label = document.createElement("div");
      label.textContent = cardText(setKey, i + 1);
      card.appendChild(label);

      if (ok) {
        var badge = document.createElement("span");
        badge.className = "lw-badge ok";
        badge.textContent = t("landing.demo.badge.locked", "Verified");
        card.appendChild(badge);
      }
      astCards.appendChild(card);

      if (!ok) {
        var pop = document.createElement("div");
        pop.className = "lw-popover";
        pop.innerHTML =
          '<div class="lw-popover-head">' +
          '<svg class="lw-warn-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">' +
          '<path d="M8 1.5 L15 14 H1 Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>' +
          '<rect x="7.3" y="6" width="1.4" height="4.2" rx="0.7" fill="currentColor"/>' +
          '<rect x="7.3" y="11" width="1.4" height="1.4" rx="0.7" fill="currentColor"/>' +
          "</svg>" +
          '<span class="lw-popover-title"></span>' +
          "</div>" +
          '<p class="lw-popover-body"></p>' +
          '<button type="button" class="lw-refine-btn"></button>';
        pop.querySelector(".lw-popover-title").textContent = t(
          "landing.demo." + setKey + ".err.title",
          ""
        );
        pop.querySelector(".lw-popover-body").innerHTML = t(
          "landing.demo." + setKey + ".err.body",
          ""
        );
        pop.querySelector(".lw-refine-btn").textContent = t(
          "landing.demo.refine",
          "Refine"
        );
        pop.querySelector(".lw-refine-btn").addEventListener("click", function () {
          var sandbox = document.getElementById("sandbox");
          if (sandbox) sandbox.scrollIntoView({ behavior: "smooth" });
        });
        card.style.position = "relative";
        card.appendChild(pop);
      }
    });

    var prompt = t("landing.demo." + setKey + ".prompt", "");

    if (animate && !reduceMotion) {
      sweep.classList.remove("run");
      void sweep.offsetWidth;
      sweep.classList.add("run");

      astCards.querySelectorAll(".lw-ast-card").forEach(function (el, i) {
        el.classList.remove("show");
        setTimeout(function () {
          el.classList.add("show");
        }, 500 + i * 180);
      });
      astCards.querySelectorAll(".lw-badge.ok").forEach(function (el, i) {
        el.classList.remove("show");
        setTimeout(function () {
          el.classList.add("show");
        }, 700 + i * 180);
      });
      var popEl = astCards.querySelector(".lw-popover");
      if (popEl) {
        popEl.classList.remove("show");
        setTimeout(function () {
          popEl.classList.add("show");
        }, 1500);
      }
      setTimeout(function () {
        typeOnce(commandPrompt, prompt, 28);
      }, 2100);
    } else {
      astCards.querySelectorAll(".lw-ast-card").forEach(function (el) {
        el.classList.add("show");
      });
      astCards.querySelectorAll(".lw-badge.ok").forEach(function (el) {
        el.classList.add("show");
      });
      var popStatic = astCards.querySelector(".lw-popover");
      if (popStatic) popStatic.classList.add("show");
      clearInterval(typeTimer);
      commandPrompt.textContent = prompt;
      commandPrompt.classList.add("blink");
    }
  }

  function init() {
    docBody = document.getElementById("lw-doc-body");
    astCards = document.getElementById("lw-ast-cards");
    sweep = document.getElementById("lw-sweep");
    commandPrompt = document.getElementById("lw-command-prompt");
    compileBtn = document.getElementById("lw-compile-btn");
    if (!docBody || !astCards || !sweep || !commandPrompt) return;

    reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    var tabs = document.querySelectorAll(".lw-tab-btn");
    tabs.forEach(function (btn) {
      btn.addEventListener("click", function () {
        tabs.forEach(function (b) {
          b.classList.remove("active");
          b.setAttribute("aria-selected", "false");
        });
        btn.classList.add("active");
        btn.setAttribute("aria-selected", "true");
        render(btn.getAttribute("data-set"), true);
      });
    });

    if (compileBtn) {
      compileBtn.addEventListener("click", function () {
        render(activeSet, true);
      });
    }

    if (SETS.indexOf(activeSet) === -1) activeSet = "legal";
    render("legal", !reduceMotion);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
