(function (global) {
  "use strict";

  var z3Chart = null;
  var rhChart = null;
  var booted = false;

  async function fetchJson(url) {
    var res = await global.fetch(url, { credentials: "same-origin" });
    return res.json();
  }

  function chartOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } },
      },
    };
  }

  function renderVelocityTable(rows) {
    var el = document.getElementById("chart-velocity-table");
    if (!el) return;
    if (!rows.length) {
      el.textContent = "No compliance data yet.";
      return;
    }
    el.innerHTML =
      "<table><thead><tr><th>Project</th><th>Sign-offs</th><th>Locked</th></tr></thead><tbody>" +
      rows
        .map(function (r) {
          return (
            "<tr><td>" +
            (r.title || r.project_id) +
            "</td><td>" +
            r.total_sign_offs +
            "</td><td>" +
            (r.is_locked ? "Yes" : "No") +
            "</td></tr>"
          );
        })
        .join("") +
      "</tbody></table>";
  }

  async function render() {
    var z3Ctx = document.getElementById("chart-z3-health");
    if (!z3Ctx) return;
    var started = performance.now();
    var z3 = await fetchJson("/api/analytics/z3-health");
    var redhat = await fetchJson("/api/analytics/redhat-critiques");
    var velocity = await fetchJson("/api/analytics/compliance-velocity");

    var z3Rows = (z3 && z3.rows) || [];
    var passRates = z3Rows.map(function (r) {
      return Number(r.pass_rate_pct || 0);
    });
    var labels = z3Rows.map(function (r) {
      return r.audit_date;
    });

    var totalAudits = z3Rows.reduce(function (sum, r) {
      return sum + Number(r.total_checks || 0);
    }, 0);
    var avgPass =
      passRates.length > 0
        ? (
            passRates.reduce(function (a, b) {
              return a + b;
            }, 0) / passRates.length
          ).toFixed(1)
        : "—";

    var velRows = (velocity && velocity.rows) || [];
    var avgSignoffs =
      velRows.length > 0
        ? (
            velRows.reduce(function (sum, r) {
              return sum + Number(r.total_sign_offs || 0);
            }, 0) / velRows.length
          ).toFixed(1)
        : "0";

    var kpiPass = document.getElementById("kpi-z3-pass");
    var kpiTotal = document.getElementById("kpi-total-audits");
    var kpiVel = document.getElementById("kpi-velocity");
    if (kpiPass) kpiPass.textContent = avgPass + (avgPass === "—" ? "" : "%");
    if (kpiTotal) kpiTotal.textContent = String(totalAudits);
    if (kpiVel) kpiVel.textContent = String(avgSignoffs);

    if (global.Chart) {
      if (z3Chart) z3Chart.destroy();
      z3Chart = new global.Chart(z3Ctx, {
        type: "bar",
        data: {
          labels: labels,
          datasets: [{ label: "Z3 pass rate %", data: passRates, backgroundColor: "#1A4B8C" }],
        },
        options: chartOptions(),
      });
      var rhCtx = document.getElementById("chart-redhat");
      var rhRows = (redhat && redhat.rows) || [];
      if (rhCtx) {
        if (rhChart) rhChart.destroy();
        rhChart = new global.Chart(rhCtx, {
          type: "doughnut",
          data: {
            labels: rhRows.map(function (r) {
              return r.critique_category;
            }),
            datasets: [
              {
                data: rhRows.map(function (r) {
                  return r.frequency;
                }),
                backgroundColor: ["#1A4B8C", "#2e7d32", "#64748b", "#0d2b45"],
              },
            ],
          },
          options: chartOptions(),
        });
      }
    }

    renderVelocityTable(velRows);
    booted = true;
    if (performance.now() - started > 2000) {
      console.warn("Analytics dashboard load exceeded 2s");
    }
  }

  function bootIfStandalone() {
    if (!document.getElementById("chart-z3-health")) return;
    if (document.getElementById("view-analytics") && document.getElementById("assure-app")) {
      return;
    }
    render();
  }

  global.AssureAnalytics = {
    render: render,
    booted: function () {
      return booted;
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootIfStandalone);
  } else {
    bootIfStandalone();
  }
})(window);
