(function () {
  "use strict";

  function openWaitlist() {
    var modal = document.getElementById("download-modal");
    if (!modal) return;
    modal.hidden = false;
    document.body.classList.add("waitlist-open");
    var email = document.getElementById("waitlist-email");
    if (email) email.focus();
  }

  function closeWaitlist() {
    var modal = document.getElementById("download-modal");
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("waitlist-open");
    var wrap = document.getElementById("pilot-form-wrap");
    var form = document.getElementById("waitlist-form");
    var done = document.getElementById("waitlist-done");
    if (wrap) wrap.hidden = false;
    if (form) form.hidden = false;
    if (done) done.hidden = true;
  }

  function bindWaitlist() {
    var modal = document.getElementById("download-modal");
    var form = document.getElementById("waitlist-form");
    var done = document.getElementById("waitlist-done");
    var wrap = document.getElementById("pilot-form-wrap");
    var err = document.getElementById("waitlist-error");
    document.querySelectorAll("[data-waitlist-open]").forEach(function (el) {
      el.addEventListener("click", function (e) {
        e.preventDefault();
        openWaitlist();
      });
    });
    if (!modal) return;
    modal.querySelectorAll("[data-waitlist-close]").forEach(function (el) {
      el.addEventListener("click", closeWaitlist);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !modal.hidden) closeWaitlist();
    });
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (err) {
        err.hidden = true;
        err.textContent = "";
      }
      var nameEl = document.getElementById("waitlist-name");
      var emailEl = document.getElementById("waitlist-email");
      var workflowEl = document.getElementById("waitlist-workflow");
      var company = nameEl ? String(nameEl.value || "").trim() : "";
      var email = emailEl ? String(emailEl.value || "").trim() : "";
      var workflow = workflowEl ? String(workflowEl.value || "").trim() : "";
      var name = workflow ? company + " — " + workflow : company;
      fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, email: email, workflow: workflow, company: company }),
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
            throw new Error((result.data && result.data.error) || "Could not join the list. Try again.");
          }
          form.hidden = true;
          if (wrap) wrap.hidden = true;
          if (done) done.hidden = false;
        })
        .catch(function (error) {
          if (err) {
            err.hidden = false;
            err.textContent = (error && error.message) || "Could not join the list. Try again.";
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

  bindWaitlist();
  bindRoleTabs();
})();
