(function (global) {
  "use strict";

  function setControlBusy(el, on) {
    if (!el) return;
    var busy = Boolean(on);
    el.classList.toggle("is-busy", busy);
    if ("disabled" in el) el.disabled = busy;
    if (busy) el.setAttribute("aria-busy", "true");
    else el.removeAttribute("aria-busy");
  }

  function t(key, fallback) {
    if (global.state && global.state.strings && global.state.strings[key]) {
      return global.state.strings[key];
    }
    var el = document.querySelector('[data-i18n="' + key + '"]');
    if (el && el.textContent) return el.textContent.trim();
    return fallback || key;
  }

  function bindTesterFeedback() {
    var btn = document.getElementById("tester-feedback-btn");
    var modal = document.getElementById("tester-feedback-modal");
    var textarea = document.getElementById("tester-feedback-text");
    var submitBtn = document.getElementById("tester-feedback-submit");
    var cancelBtn = document.getElementById("tester-feedback-cancel");
    if (!btn || !modal || !textarea || !submitBtn || !cancelBtn) return;

    var nativeFetch = global.fetch.bind(global);
    var lastFocus = null;

    function openModal() {
      lastFocus = document.activeElement;
      modal.hidden = false;
      textarea.value = "";
      textarea.focus();
    }

    function closeModal() {
      modal.hidden = true;
      if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
    }

    btn.addEventListener("click", openModal);
    cancelBtn.addEventListener("click", closeModal);
    modal.querySelectorAll("[data-close]").forEach(function (el) {
      el.addEventListener("click", closeModal);
    });
    modal.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") closeModal();
    });

    submitBtn.addEventListener("click", function () {
      var text = String(textarea.value || "").trim();
      if (!text) return;
      btn.disabled = true;
      submitBtn.disabled = true;
      nativeFetch("/api/tester-feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text, page: location.pathname }),
      })
        .then(function (res) {
          if (!res.ok) throw new Error("bad status");
          closeModal();
          window.alert(t("tester.feedback.thanks", "Thank you! Your feedback was sent."));
        })
        .catch(function () {
          window.alert(t("tester.feedback.error", "Could not send feedback. Please try again."));
        })
        .finally(function () {
          btn.disabled = false;
          submitBtn.disabled = false;
        });
    });
  }

  global.AssureUI = { setControlBusy: setControlBusy };

  document.addEventListener("DOMContentLoaded", bindTesterFeedback);
})(window);
