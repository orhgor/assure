(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var body = document.body;

    var leftCollapse = document.getElementById("left-collapse");
    if (leftCollapse) {
      leftCollapse.addEventListener("click", function () {
        body.classList.toggle("collapsed");
      });
    }

    var rightClose = document.getElementById("right-close");
    var paneRight = document.getElementById("pane-right");
    function openRight() {
      body.classList.remove("right-hidden");
    }
    function closeRight() {
      body.classList.add("right-hidden");
    }
    if (rightClose) {
      rightClose.addEventListener("click", closeRight);
    }

    var railBtns = document.querySelectorAll("[data-rail-btn]");
    railBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var kind = btn.getAttribute("data-rail-btn");
        if (kind === "activity") {
          openRight();
        } else if (kind === "history") {
          body.classList.toggle("collapsed");
        }
      });
    });

    var wrap = document.getElementById("dock-input-wrap");
    var text = document.getElementById("dock-text");
    if (wrap && text) {
      text.addEventListener("focus", function () {
        wrap.classList.add("focused");
      });
      text.addEventListener("blur", function () {
        wrap.classList.remove("focused");
      });
      text.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
          e.preventDefault();
          text.value = "";
        }
      });
    }

    var submit = document.getElementById("dock-submit");
    if (submit && text) {
      submit.addEventListener("click", function () {
        text.value = "";
      });
    }
  });
})();
