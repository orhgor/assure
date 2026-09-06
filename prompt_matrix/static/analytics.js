(function (global) {
  "use strict";

  async function fetchJson(url) {
    var res = await global.fetch(url, { credentials: "same-origin" });
    return res.json();
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

  async function boot() {
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
        ? (passRates.reduce(function (a, b) {
            return a + b;
          }, 0) /
            passRates.length).toFixed(1)
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
      var z3Ctx = document.getElementById("chart-z3-health");
      if (z3Ctx) {
        new global.Chart(z3Ctx, {
          type: "bar",
          data: {
            labels: labels,
            datasets: [{ label: "Z3 pass rate %", data: passRates, backgroundColor: "#4caf50" }],
          },
        });
      }
      var rhCtx = document.getElementById("chart-redhat");
      var rhRows = (redhat && redhat.rows) || [];
      if (rhCtx) {
        new global.Chart(rhCtx, {
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
                backgroundColor: ["#ef5350", "#ffb300", "#42a5f5", "#ab47bc"],
              },
            ],
          },
        });
      }
    }

    renderVelocityTable(velRows);
    if (performance.now() - started > 2000) {
      console.warn("Analytics dashboard load exceeded 2s");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})(window);
