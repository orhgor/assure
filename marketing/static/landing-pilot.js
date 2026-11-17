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
      var submitBtn = form.querySelector('button[type="submit"]');
      var originalBtnText = submitBtn ? submitBtn.textContent : "";
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "Sending...";
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
          if (res.status === 429) {
            var retryAfter = res.headers.get("Retry-After") || 60;
            throw { status: 429, retryAfter: retryAfter, message: "Too many requests. Please wait " + retryAfter + "s and try again." };
          }
          if (!res.ok) throw { status: res.status, message: "Server error: " + res.status };
          return res.json();
        })
        .then(function (data) {
          if (data.ok || data.status === "ok") {
            form.hidden = true;
            if (wrap) wrap.hidden = true;
            if (done) {
              done.hidden = false;
              done.classList.add("is-success");
            }
          } else {
            throw { message: data.error || "Could not send your request. Please try again." };
          }
        })
        .catch(function (err) {
          if (err) {
            err.hidden = false;
            err.textContent = err?.message || errorText;
          }
        })
        .finally(function () {
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = originalBtnText;
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
  bindZ3Recalc();
})();

function bindZ3Recalc() {
  var btn = document.getElementById("z3-recalculate-btn");
  var sourceAInput = document.getElementById("proof-source-a-value");
  var sourceBInput = document.getElementById("proof-source-b-value");
  var deltaValue = document.getElementById("proof-delta-value");
  var deltaCurrency = document.getElementById("proof-delta-currency");
  var z3ResultText = document.getElementById("z3-result-text");

  if (!btn || !sourceAInput || !sourceBInput) return;

  function formatNumber(n) {
    return n.toLocaleString();
  }

  function updateDeltaLocal(a, b) {
    var delta = b - a;
    var currency = deltaCurrency ? deltaCurrency.textContent : "EUR";
    var sign = delta > 0 ? "+" : "";
    if (deltaValue) deltaValue.textContent = sign + formatNumber(delta);
    if (z3ResultText) {
      if (delta !== 0) {
        z3ResultText.textContent = "Export locked — " + sign + formatNumber(delta) + " " + currency + " overage detected. A person or an agent decides, and the file can still be downloaded.";
      } else {
        z3ResultText.textContent = "No conflict — values agree. Export proceeds.";
      }
    }
  }

  // Local instant update on input change
  [sourceAInput, sourceBInput].forEach(function (input) {
    input.addEventListener("input", function () {
      var a = Number(sourceAInput.value) || 0;
      var b = Number(sourceBInput.value) || 0;
      updateDeltaLocal(a, b);
    });
  });

  // Server-side Z3 demo call on button click
  btn.addEventListener("click", function () {
    var a = Number(sourceAInput.value) || 0;
    var b = Number(sourceBInput.value) || 0;
    var currency = "EUR";

    btn.disabled = true;
    var originalText = btn.textContent;
    btn.textContent = "Verifying...";

    fetch("/api/z3-demo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sourceA: { label: "Contract Cap", value: a, currency: currency },
        sourceB: { label: "Amount Billed", value: b, currency: currency }
      })
    })
      .then(function (res) {
        if (res.status === 429) {
          var retryAfter = res.headers.get("Retry-After") || 60;
          throw { status: 429, retryAfter: retryAfter, message: "Too many requests. Please wait " + retryAfter + "s and try again." };
        }
        if (!res.ok) throw { status: res.status, message: "Server error: " + res.status };
        return res.json();
      })
      .then(function (data) {
        if (data.ok) {
          if (deltaValue) deltaValue.textContent = (data.delta.value > 0 ? "+" : "") + formatNumber(data.delta.value);
          if (deltaCurrency) deltaCurrency.textContent = data.delta.currency;
          if (z3ResultText) z3ResultText.textContent = data.message;
          if (data.delta.conflict) {
            z3ResultText.style.color = "var(--error, #dc2626)";
          } else {
            z3ResultText.style.color = "var(--success, #16a34a)";
          }
        } else {
          // Server returned ok: false
          if (z3ResultText) z3ResultText.textContent = data.error || "Verification failed. Showing local calculation.";
          updateDeltaLocal(a, b);
        }
      })
      .catch(function (err) {
        if (err?.status === 429 && err?.retryAfter) {
          if (z3ResultText) z3ResultText.textContent = err.message;
          z3ResultText.style.color = "var(--warning, #d97706)";
        } else {
          if (z3ResultText) z3ResultText.textContent = "Verification service unavailable. Showing local calculation.";
          z3ResultText.style.color = "var(--warning, #d97706)";
        }
        updateDeltaLocal(a, b);
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = originalText;
      });
  });
}
