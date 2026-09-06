/**
 * Lightweight toast notifications (delegates to AssureToast when present).
 */
(function (global) {
  "use strict";

  var CONTAINER_ID = "assure-toast-stack";

  function ensureContainer() {
    var el = global.document.getElementById(CONTAINER_ID);
    if (el) return el;
    el = global.document.createElement("div");
    el.id = CONTAINER_ID;
    el.className = "toast-stack";
    el.setAttribute("aria-live", "polite");
    global.document.body.appendChild(el);
    return el;
  }

  function show(message, kind, durationMs) {
    if (global.AssureToast && typeof global.AssureToast.show === "function") {
      global.AssureToast.show(message, kind || "info", durationMs);
      return;
    }
    var stack = ensureContainer();
    var toast = global.document.createElement("div");
    toast.className = "toast toast-" + (kind || "info");
    toast.textContent = String(message || "");
    stack.appendChild(toast);
    requestAnimationFrame(function () {
      toast.classList.add("is-visible");
    });
    var ms = durationMs == null ? 3200 : durationMs;
    global.setTimeout(function () {
      toast.classList.remove("is-visible");
      global.setTimeout(function () {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 280);
    }, ms);
  }

  global.AssureToastLite = { show: show };
  if (!global.AssureToast) {
    global.AssureToast = { show: show };
  }
})(window);
