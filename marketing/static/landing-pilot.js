(function () {
  "use strict";

  function openDemo() {
    var modal = document.getElementById("download-modal");
    if (!modal) return;
    modal.hidden = false;
    document.body.classList.add("demo-open");
    var email = document.getElementById("demo-email");
    if (email) email.focus();
  }

  function closeDemo() {
    var modal = document.getElementById("download-modal");
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("demo-open");
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
    document.querySelectorAll("[data-demo-open]").forEach(function (el) {
      el.addEventListener("click", function (e) {
        e.preventDefault();
        openDemo();
      });
    });
    if (!modal) return;
    modal.querySelectorAll("[data-demo-close]").forEach(function (el) {
      el.addEventListener("click", closeDemo);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !modal.hidden) closeDemo();
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
            throw new Error((result.data && result.data.error) || "Could not join the list. Try again.");
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

  bindDemo();
  bindRoleTabs();
})();
