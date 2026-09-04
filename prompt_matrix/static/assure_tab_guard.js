(function (global) {
  "use strict";

  var CHANNEL_NAME = "assure_workbench_state";
  var PING_TIMEOUT_MS = 400;

  function showBlockingModal() {
    var modal = document.getElementById("tab-lockout-modal");
    if (!modal) return;
    modal.hidden = false;
    document.body.classList.add("tab-lockout-active");
  }

  function bindChannel() {
    if (typeof BroadcastChannel === "undefined") return;
    var channel = new BroadcastChannel(CHANNEL_NAME);
    var isEstablished = false;

    channel.onmessage = function (ev) {
      var msg = ev && ev.data;
      if (msg === "ping") {
        if (isEstablished) channel.postMessage("pong");
        return;
      }
      if (msg === "pong" && !isEstablished) {
        showBlockingModal();
      }
    };

    channel.postMessage("ping");
    global.setTimeout(function () {
      isEstablished = true;
    }, PING_TIMEOUT_MS);
  }

  function init() {
    bindChannel();
  }

  global.AssureTabGuard = { init: init };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(window);
