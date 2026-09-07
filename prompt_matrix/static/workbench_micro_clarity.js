/**
 * Draft character counter and micro-clarity helpers.
 */
(function (global) {
  "use strict";

  function t(key, fallback, params) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, params || {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback;
  }

  function countWords(text) {
    var trimmed = String(text || "").trim();
    if (!trimmed) return 0;
    return trimmed.split(/\s+/).filter(Boolean).length;
  }

  function updateCounter() {
    var input = document.getElementById("generate-intent");
    var counter = document.getElementById("generate-intent-counter");
    if (!input || !counter) return;
    var text = input.value || "";
    var words = countWords(text);
    var chars = text.length;
    counter.textContent = t(
      "draft.char_counter",
      "{words} words · {chars} characters · Markdown & plain text supported",
      { words: String(words), chars: String(chars) }
    );
  }

  document.addEventListener("DOMContentLoaded", function () {
    var input = document.getElementById("generate-intent");
    if (!input) return;
    input.addEventListener("input", updateCounter);
    updateCounter();
    document.addEventListener("assure:i18n-ready", updateCounter);
  });

  global.AssureMicroClarity = { updateCounter: updateCounter };
})(window);
