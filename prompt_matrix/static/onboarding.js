(function (global) {
  "use strict";

  var ONBOARDING_KEY = "assure_onboarding_complete";
  var DISCLAIMER_KEY = "assure_disclaimer_ack";
  var STEPS = [
    {
      selector: "#generate-intent",
      i18n: "coachmark.tour.step1",
      fallback: "Start here – type your intent",
      prepare: function () {
        if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
          global.AssureNav.switchView("generate", { replaceHash: false, persist: true });
        }
      },
    },
    {
      selector: "#generate-compile-btn",
      i18n: "coachmark.tour.step2",
      fallback: "Click Assemble to compile",
    },
    {
      selector: "#jdf-render-target",
      i18n: "coachmark.tour.step3",
      fallback: "See verification results on the canvas",
    },
  ];

  var state = {
    index: 0,
    callout: null,
    activeEl: null,
    resizeHandler: null,
  };

  function t(key, fallback) {
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback || key;
  }

  function waitForElement(selector, timeout) {
    timeout = timeout == null ? 10000 : timeout;
    return new Promise(function (resolve, reject) {
      var el = document.querySelector(selector);
      if (el) {
        resolve(el);
        return;
      }
      var done = false;
      var timer = window.setTimeout(function () {
        if (done) return;
        done = true;
        observer.disconnect();
        window.clearInterval(poll);
        reject(new Error("waitForElement timeout: " + selector));
      }, timeout);

      var observer = new MutationObserver(function () {
        var node = document.querySelector(selector);
        if (!node || done) return;
        done = true;
        window.clearTimeout(timer);
        window.clearInterval(poll);
        observer.disconnect();
        resolve(node);
      });
      observer.observe(document.documentElement, {
        childList: true,
        subtree: true,
        attributes: true,
      });

      var poll = window.setInterval(function () {
        var node = document.querySelector(selector);
        if (!node || done) return;
        done = true;
        window.clearTimeout(timer);
        observer.disconnect();
        window.clearInterval(poll);
        resolve(node);
      }, 120);
    });
  }

  function forceTour() {
    try {
      return new URLSearchParams(window.location.search).get("onboarding") === "true";
    } catch (_) {
      return false;
    }
  }

  function isComplete() {
    if (forceTour()) return false;
    try {
      return localStorage.getItem(ONBOARDING_KEY) === "1";
    } catch (_) {
      return false;
    }
  }

  var disclaimerWaiters = [];

  function isDisclaimerAcked() {
    try {
      return localStorage.getItem(DISCLAIMER_KEY) === "1";
    } catch (_) {
      return false;
    }
  }

  function hideDisclaimerGate() {
    var gate = document.getElementById("disclaimer-gate");
    if (gate) gate.hidden = true;
    document.body.classList.remove("disclaimer-gate-open");
  }

  function showDisclaimerGate() {
    var gate = document.getElementById("disclaimer-gate");
    if (!gate) return;
    gate.hidden = false;
    document.body.classList.add("disclaimer-gate-open");
    var ackBtn = document.getElementById("disclaimer-ack");
    if (ackBtn) ackBtn.focus();
  }

  function ackDisclaimer() {
    try {
      localStorage.setItem(DISCLAIMER_KEY, "1");
    } catch (_) {}
    hideDisclaimerGate();
    var waiters = disclaimerWaiters.slice();
    disclaimerWaiters = [];
    waiters.forEach(function (fn) {
      fn(true);
    });
  }

  function ensureAck() {
    if (isDisclaimerAcked()) {
      hideDisclaimerGate();
      return Promise.resolve(true);
    }
    showDisclaimerGate();
    return new Promise(function (resolve) {
      disclaimerWaiters.push(resolve);
    });
  }


  function completeOnboarding() {
    try {
      localStorage.setItem(ONBOARDING_KEY, "1");
    } catch (_) {}
    teardown();
  }

  function teardown() {
    if (state.activeEl) {
      state.activeEl.classList.remove("onboarding-highlight");
      state.activeEl = null;
    }
    if (state.callout) {
      state.callout.remove();
      state.callout = null;
    }
    if (state.resizeHandler) {
      window.removeEventListener("resize", state.resizeHandler);
      window.removeEventListener("scroll", state.resizeHandler, true);
      state.resizeHandler = null;
    }
    document.body.classList.remove("onboarding-active");
  }

  function clampCalloutToViewport(callout) {
    if (!callout) return;
    var margin = 8;
    var height = callout.offsetHeight || callout.getBoundingClientRect().height;
    var width = callout.offsetWidth || callout.getBoundingClientRect().width;
    var maxTop = Math.max(margin, window.innerHeight - height - margin);
    var maxLeft = Math.max(margin, window.innerWidth - width - margin);
    var top = parseFloat(callout.style.top);
    var left = parseFloat(callout.style.left);
    if (!isFinite(top)) top = margin;
    if (!isFinite(left)) left = margin;
    callout.style.top = Math.min(Math.max(margin, top), maxTop) + "px";
    callout.style.left = Math.min(Math.max(margin, left), maxLeft) + "px";
  }

  function positionCallout(target, callout) {
    if (!target || !callout) return;
    var rect = target.getBoundingClientRect();
    var mobile = window.matchMedia("(max-width: 768px)").matches;
    var gap = 10;
    var margin = 8;
    var left;
    var top;

    callout.style.maxWidth = mobile ? "260px" : "280px";

    if (mobile) {
      left = Math.max(margin, rect.left);
      top = rect.bottom + gap;
      if (top + callout.offsetHeight > window.innerHeight - margin) {
        top = Math.max(margin, rect.top - callout.offsetHeight - gap);
      }
    } else {
      left = rect.left + rect.width / 2 - callout.offsetWidth / 2;
      top = rect.top - callout.offsetHeight - gap;
      if (top < margin) {
        top = rect.bottom + gap;
      }
      left = Math.max(margin, Math.min(left, window.innerWidth - callout.offsetWidth - margin));
      if (top + callout.offsetHeight > window.innerHeight - margin) {
        top = Math.max(margin, window.innerHeight - callout.offsetHeight - margin);
      }
    }

    callout.style.left = left + "px";
    callout.style.top = top + "px";
    clampCalloutToViewport(callout);
  }

  function buildCallout(stepIndex, total) {
    var callout = document.createElement("div");
    callout.className = "onboarding-callout coachmark-tour";
    callout.setAttribute("role", "status");
    callout.setAttribute("aria-live", "polite");

    var step = STEPS[stepIndex];
    var text = document.createElement("p");
    text.className = "onboarding-callout-text";
    text.textContent = t(step.i18n, step.fallback);
    callout.appendChild(text);

    var actions = document.createElement("div");
    actions.className = "onboarding-callout-actions";

    var skipBtn = document.createElement("button");
    skipBtn.type = "button";
    skipBtn.className = "btn btn-outline btn-sm onboarding-skip";
    skipBtn.textContent = t("onboarding.skip", "Skip tour");
    skipBtn.addEventListener("click", completeOnboarding);
    actions.appendChild(skipBtn);

    var nextBtn = document.createElement("button");
    nextBtn.type = "button";
    nextBtn.className = "btn btn-primary btn-sm onboarding-next";
    var isLast = stepIndex >= total - 1;
    nextBtn.textContent = isLast
      ? t("onboarding.done", "Got it")
      : t("onboarding.next", "Next");
    nextBtn.addEventListener("click", function () {
      if (isLast) {
        completeOnboarding();
      } else {
        showStep(stepIndex + 1);
      }
    });
    actions.appendChild(nextBtn);

    callout.appendChild(actions);
    return callout;
  }

  function showStep(stepIndex) {
    if (stepIndex >= STEPS.length) {
      completeOnboarding();
      return;
    }

    var step = STEPS[stepIndex];
    var run = function () {
      waitForElement(step.selector, 8000)
        .then(function (el) {
          if (isComplete()) return;

          if (state.activeEl) {
            state.activeEl.classList.remove("onboarding-highlight");
          }
          state.activeEl = el;
          state.index = stepIndex;
          document.body.classList.add("onboarding-active");
          el.classList.add("onboarding-highlight");
          el.scrollIntoView({ block: "nearest", behavior: "smooth" });

          if (state.callout) state.callout.remove();
          state.callout = buildCallout(stepIndex, STEPS.length);
          document.body.appendChild(state.callout);
          positionCallout(el, state.callout);

          if (!state.resizeHandler) {
            state.resizeHandler = function () {
              if (state.activeEl && state.callout) {
                positionCallout(state.activeEl, state.callout);
              }
            };
            window.addEventListener("resize", state.resizeHandler);
            window.addEventListener("scroll", state.resizeHandler, true);
          }
        })
        .catch(function () {
          if (stepIndex + 1 < STEPS.length) {
            showStep(stepIndex + 1);
          } else {
            completeOnboarding();
          }
        });
    };

    if (typeof step.prepare === "function") {
      step.prepare();
      window.requestAnimationFrame(run);
      return;
    }
    run();
  }

  function maybeStart() {
    if (isComplete()) return;
    ensureAck().then(function () {
      if (isComplete()) return;
      waitForElement("#assure-app", 10000)
        .then(function () {
          return waitForElement("#panel-draft", 10000);
        })
        .then(function () {
          if (!isComplete()) showStep(0);
        })
        .catch(function () {});
    });
  }

  function init() {
    var ackBtn = document.getElementById("disclaimer-ack");
    if (ackBtn) {
      ackBtn.addEventListener("click", ackDisclaimer);
    }
    if (!isDisclaimerAcked()) {
      showDisclaimerGate();
    }
    if (isComplete() && isDisclaimerAcked()) return;
    maybeStart();
    document.addEventListener("assure:view", function () {
      if (isComplete() || !state.callout || !state.activeEl) return;
      window.requestAnimationFrame(function () {
        positionCallout(state.activeEl, state.callout);
      });
    });
    document.addEventListener("assure:i18n", function () {
      if (!state.callout) return;
      var textEl = state.callout.querySelector(".onboarding-callout-text");
      var step = STEPS[state.index];
      if (textEl && step) {
        textEl.textContent = t(step.i18n, step.fallback);
      }
      var skipBtn = state.callout.querySelector(".onboarding-skip");
      var nextBtn = state.callout.querySelector(".onboarding-next");
      if (skipBtn) skipBtn.textContent = t("onboarding.skip", "Skip tour");
      if (nextBtn) {
        nextBtn.textContent =
          state.index >= STEPS.length - 1
            ? t("onboarding.done", "Got it")
            : t("onboarding.next", "Next");
      }
    });
  }

  global.AssureOnboarding = {
    init: init,
    completeOnboarding: completeOnboarding,
    waitForElement: waitForElement,
  };
  global.AssureDisclaimer = {
    isAcked: isDisclaimerAcked,
    ensureAck: ensureAck,
    ack: ackDisclaimer,
    KEY: DISCLAIMER_KEY,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(window);
