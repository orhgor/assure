(function (global) {
  "use strict";

  var z3Chart = null;
  var rhChart = null;
  var booted = false;
  var lastZ3Rows = [];
  var lastVelRows = [];
  var rangeBound = false;

  function tx(key, fallback) {
    if (typeof global.__assureT === "function") return global.__assureT(key, fallback);
    return fallback;
  }

  async function fetchJson(url) {
    var res = await global.fetch(url, { credentials: "same-origin" });
    return res.json();
  }

  function parseAuditDate(value) {
    if (!value) return 0;
    var t = Date.parse(String(value));
    return Number.isFinite(t) ? t : 0;
  }

  function sliceZ3Rows(rows, days) {
    if (!rows.length) return [];
    var n = Number(days) || 14;
    var latest = 0;
    rows.forEach(function (r) {
      var t = parseAuditDate(r.audit_date);
      if (t > latest) latest = t;
    });
    if (!latest) return rows.slice();
    var cutoff = latest - n * 86400000;
    return rows.filter(function (r) {
      return parseAuditDate(r.audit_date) >= cutoff;
    });
  }

  function chartOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { font: { size: 11 }, color: "#6b7280", maxRotation: 0 },
        },
        y: {
          beginAtZero: true,
          max: 100,
          grid: { color: "rgba(17, 24, 39, 0.08)" },
          ticks: { stepSize: 25, font: { size: 11 }, color: "#6b7280" },
        },
      },
    };
  }

  function renderVelocityTable(rows) {
    var el = document.getElementById("chart-velocity-table");
    if (!el) return;
    if (!rows.length) {
      el.textContent = tx("analytics.table.empty", "No compliance data yet.");
      return;
    }
    el.innerHTML =
      "<table><thead><tr><th>" +
      tx("analytics.table.project", "Project") +
      "</th><th>" +
      tx("analytics.table.signoffs", "Sign-offs") +
      "</th><th>" +
      tx("analytics.table.locked", "Locked") +
      "</th></tr></thead><tbody>" +
      rows
        .map(function (r) {
          return (
            "<tr><td>" +
            (r.title || r.project_id) +
            "</td><td>" +
            r.total_sign_offs +
            "</td><td>" +
            (r.is_locked ? tx("analytics.locked.yes", "Yes") : tx("analytics.locked.no", "No")) +
            "</td></tr>"
          );
        })
        .join("") +
      "</tbody></table>";
  }

  function paintZ3Chart(z3Rows) {
    var z3Ctx = document.getElementById("chart-z3-health");
    if (!z3Ctx || !global.Chart) return;
    var rangeEl = document.getElementById("analytics-z3-range");
    var days = rangeEl ? rangeEl.value : "14";
    var sliced = sliceZ3Rows(z3Rows, days);
    var passRates = sliced.map(function (r) {
      return Number(r.pass_rate_pct || 0);
    });
    var labels = sliced.map(function (r) {
      return r.audit_date;
    });
    if (z3Chart) z3Chart.destroy();
    z3Chart = new global.Chart(z3Ctx, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: tx("analytics.chart.z3_series", "Z3 pass rate %"),
            data: passRates,
            backgroundColor: "#0a0a0a",
            borderRadius: 4,
            borderSkipped: false,
            maxBarThickness: 48,
          },
        ],
      },
      options: chartOptions(),
    });
  }

  function bindRange() {
    if (rangeBound) return;
    var rangeEl = document.getElementById("analytics-z3-range");
    if (!rangeEl) return;
    rangeBound = true;
    rangeEl.addEventListener("change", function () {
      paintZ3Chart(lastZ3Rows);
    });
  }

  async function render() {
    var z3Ctx = document.getElementById("chart-z3-health");
    if (!z3Ctx) return;
    var started = performance.now();
    var z3 = await fetchJson("/api/analytics/z3-health");
    var redhat = await fetchJson("/api/analytics/redhat-critiques");
    var velocity = await fetchJson("/api/analytics/compliance-velocity");

    var z3Rows = (z3 && z3.rows) || [];
    lastZ3Rows = z3Rows;
    var passRates = z3Rows.map(function (r) {
      return Number(r.pass_rate_pct || 0);
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
    lastVelRows = velRows;
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
      bindRange();
      paintZ3Chart(z3Rows);
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
                backgroundColor: ["#0a0a0a", "#6b7280", "#9ca3af", "#d1d5db"],
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: true, position: "bottom", labels: { boxWidth: 10, font: { size: 11 } } } },
          },
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
