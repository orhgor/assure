(function (global) {
  "use strict";

  var CHANNEL_NAME = "assure_workbench_state";
  var PING_TIMEOUT_MS = 400;
  var TAB_ID = "t" + Math.random().toString(36).slice(2) + String(Date.now());
  var channel = null;
  var isEstablished = false;
  var establishTimer = null;

  function isActiveDocument() {
    if (document.prerendering) return false;
    if (document.visibilityState === "hidden") return false;
    return true;
  }

  function showBlockingModal() {
    var modal = document.getElementById("tab-lockout-modal");
    if (!modal) return;
    modal.hidden = false;
    document.body.classList.add("tab-lockout-active");
  }

  function hideBlockingModal() {
    var modal = document.getElementById("tab-lockout-modal");
    if (modal) modal.hidden = true;
    document.body.classList.remove("tab-lockout-active");
  }

  function post(type) {
    if (!channel) return;
    try {
      channel.postMessage({ type: type, tabId: TAB_ID });
    } catch (_) {}
  }

  function messageType(data) {
    if (data == null) return "";
    if (typeof data === "string") return data;
    if (typeof data === "object") return data.type || "";
    return "";
  }

  function messageTabId(data) {
    if (data && typeof data === "object") return data.tabId || "";
    return "";
  }

  function teardown() {
    if (establishTimer) {
      clearTimeout(establishTimer);
      establishTimer = null;
    }
    isEstablished = false;
    if (channel) {
      try {
        channel.close();
      } catch (_) {}
      channel = null;
    }
  }

  function startPing() {
    post("ping");
    establishTimer = global.setTimeout(function () {
      isEstablished = true;
    }, PING_TIMEOUT_MS);
  }

  function bindChannel() {
    if (typeof BroadcastChannel === "undefined") return;
    teardown();
    channel = new BroadcastChannel(CHANNEL_NAME);

    channel.onmessage = function (ev) {
      var data = ev && ev.data;
      if (messageTabId(data) === TAB_ID) return;
      var type = messageType(data);
      if (type === "ping") {
        if (isEstablished && isActiveDocument()) post("pong");
        return;
      }
      if (type === "pong" && !isEstablished) {
        showBlockingModal();
        return;
      }
      if (type === "claim" && isEstablished) {
        showBlockingModal();
      }
    };

    if (document.prerendering) {
      document.addEventListener(
        "prerenderingchange",
        function onReady() {
          document.removeEventListener("prerenderingchange", onReady);
          startPing();
        },
        { once: true }
      );
      return;
    }
    startPing();
  }

  function claimThisTab() {
    hideBlockingModal();
    isEstablished = true;
    post("claim");
  }

  function bindModal() {
    var btn = document.getElementById("tab-lockout-continue");
    if (!btn || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", claimThisTab);
  }

  function init() {
    bindModal();
    bindChannel();
    global.addEventListener("pagehide", teardown);
    global.addEventListener("beforeunload", teardown);
    global.addEventListener("pageshow", function (e) {
      if (e.persisted) bindChannel();
    });
  }

  global.AssureTabGuard = { init: init, claimThisTab: claimThisTab };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(window);
