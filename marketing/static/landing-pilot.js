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

  [sourceAInput, sourceBInput].forEach(function (input) {
    input.addEventListener("input", function () {
      var a = Number(sourceAInput.value) || 0;
      var b = Number(sourceBInput.value) || 0;
      updateDeltaLocal(a, b);
    });
  });

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

document.addEventListener("DOMContentLoaded", function () {
  bindZ3Recalc();
});
