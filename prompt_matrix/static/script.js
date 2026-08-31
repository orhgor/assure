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

  global.AssureUI = { setControlBusy: setControlBusy };
})(window);
