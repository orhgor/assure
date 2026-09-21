(function () {
  "use strict";
  function runLaserSweep(containerEl) {
    if (!containerEl) return;
    var el = document.createElement("div");
    el.className = "verification-laser";
    // Height of the rendered document. .doc-draft is stable —
    // pane resize changes width only.
    var h = containerEl.scrollHeight || containerEl.offsetHeight || 600;
    el.style.setProperty("--laser-distance", h + "px");
    containerEl.appendChild(el);
    el.addEventListener("animationend", function () { el.remove(); });
    // Safety net in case animationend is missed.
    setTimeout(function () { if (el.parentNode) el.remove(); }, 2500);
  }
  window.runLaserSweep = runLaserSweep;
})();
