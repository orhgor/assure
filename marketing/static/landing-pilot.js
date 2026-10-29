(function () {
  "use strict";

  var demoTrigger = null;
  var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function demoFocusables(modal) {
    return Array.prototype.filter.call(modal.querySelectorAll(FOCUSABLE), function (el) {
      return el.getClientRects().length > 0;
    });
  }

  function demoKeys(e) {
    var modal = document.getElementById("download-modal");
    if (!modal || modal.hidden) return;
    if (e.key === "Escape") {
      e.preventDefault();
      closeDemo();
      return;
    }
    if (e.key !== "Tab") return;
    var f = demoFocusables(modal);
    if (!f.length) {
      e.preventDefault();
      return;
    }
    var first = f[0];
    var last = f[f.length - 1];
    var active = document.activeElement;
    if (e.shiftKey && (active === first || !modal.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && (active === last || !modal.contains(active))) {
      e.preventDefault();
      first.focus();
    }
  }

  function openDemo(trigger) {
    var modal = document.getElementById("download-modal");
    if (!modal || !modal.hidden) return;
    demoTrigger = trigger || document.activeElement;
    modal.hidden = false;
    document.body.classList.add("demo-open");
    document.addEventListener("keydown", demoKeys);
    var email = document.getElementById("demo-email");
    if (email && email.getClientRects().length) {
      email.focus();
    } else {
      var f = demoFocusables(modal);
      if (f.length) f[0].focus();
    }
  }

  function closeDemo() {
    var modal = document.getElementById("download-modal");
    if (!modal) return;
    var wasOpen = !modal.hidden;
    modal.hidden = true;
    document.body.classList.remove("demo-open");
    document.removeEventListener("keydown", demoKeys);
    if (wasOpen && demoTrigger && typeof demoTrigger.focus === "function") demoTrigger.focus();
    demoTrigger = null;
    var wrap = document.getElementById("pilot-form-wrap");
    var form = document.getElementById("demo-form");
    var done = document.getElementById("demo-done");
    if (wrap) wrap.hidden = false;
    if (form) form.hidden = false;
    if (done) {
      done.hidden = true;
      done.classList.remove("is-success");
    }
  }

  function bindDemo() {
    var modal = document.getElementById("download-modal");
    var form = document.getElementById("demo-form");
    var done = document.getElementById("demo-done");
    var wrap = document.getElementById("pilot-form-wrap");
    var err = document.getElementById("demo-error");
    var strings = window.__assureLandingStrings || {};
    var errorText =
      strings["landing.pilot.error"] ||
      (err && err.getAttribute("data-error-text")) ||
      "Could not send your request. Please try again.";
    document.querySelectorAll("[data-demo-open]").forEach(function (el) {
      el.addEventListener("click", function (e) {
        e.preventDefault();
        openDemo(el);
      });
    });
    if (!modal) return;
    modal.querySelectorAll("[data-demo-close]").forEach(function (el) {
      el.addEventListener("click", closeDemo);
    });
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (err) {
        err.hidden = true;
        err.textContent = "";
      }
      var nameEl = document.getElementById("demo-name");
      var emailEl = document.getElementById("demo-email");
      var workflowEl = document.getElementById("demo-workflow");
      var company = nameEl ? String(nameEl.value || "").trim() : "";
      var email = emailEl ? String(emailEl.value || "").trim() : "";
      var workflow = workflowEl ? String(workflowEl.value || "").trim() : "";
      var name = workflow ? company + " / " + workflow : company;
      fetch("/contact-submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, email: email, company: company, message: workflow ? "Primary document workflow: " + workflow : "" }),
      })
        .then(function (res) {
          return res.json().catch(function () {
            return {};
          }).then(function (data) {
            return { ok: res.ok, data: data };
          });
        })
        .then(function (result) {
          if (!result.ok) {
            throw new Error(errorText);
          }
          form.hidden = true;
          if (wrap) wrap.hidden = true;
          if (done) {
            done.hidden = false;
            done.classList.add("is-success");
          }
        })
        .catch(function (error) {
          if (err) {
            err.hidden = false;
            err.textContent = errorText;
          }
        });
    });
  }

  function bindRoleTabs() {
    var tabs = Array.prototype.slice.call(document.querySelectorAll("[data-role-tab]"));
    if (!tabs.length) return;
    function activate(id) {
      tabs.forEach(function (other) {
        var selected = other.getAttribute("data-role-tab") === id;
        other.classList.toggle("is-active", selected);
        other.setAttribute("aria-selected", selected ? "true" : "false");
      });
      document.querySelectorAll("[data-role-panel]").forEach(function (panel) {
        var match = panel.getAttribute("data-role-panel") === id;
        panel.classList.toggle("is-active", match);
        panel.hidden = !match;
      });
    }
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        activate(tab.getAttribute("data-role-tab"));
      });
    });
  }

  bindDemo();
  bindRoleTabs();
})();
