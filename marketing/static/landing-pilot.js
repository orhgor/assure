function pageLocale() {
  var html = document.documentElement;
  return (html.getAttribute("data-locale") || html.getAttribute("lang") || "en").toLowerCase();
}

function z3ErrorMessage(kind, detail) {
  if (pageLocale().indexOf("tr") === 0) {
    if (kind === "rate") {
      return "Çok fazla istek gönderildi. Lütfen " + detail + " saniye bekleyip yeniden deneyin.";
    }
    if (kind === "server") return "Sunucu hatası: " + detail;
    if (kind === "verify") return "Doğrulama başarısız. Yerel hesap gösteriliyor.";
    return "Doğrulama hizmeti kullanılamıyor. Yerel hesap gösteriliyor.";
  }
  if (kind === "rate") {
    return "Too many requests. Please wait " + detail + "s and try again.";
  }
  if (kind === "server") return "Server error: " + detail;
  if (kind === "verify") return "Verification failed. Showing local calculation.";
  return "Verification service unavailable. Showing local calculation.";
}

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
        z3ResultText.textContent = "Mismatch marked: the invoice is " + sign + formatNumber(delta) + " " + currency + " over the contract cap. A person reviews and decides.";
      } else {
        z3ResultText.textContent = "No mismatch: the two figures agree.";
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
    btn.textContent = "Replaying...";

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
          throw { status: 429, retryAfter: retryAfter, message: z3ErrorMessage("rate", retryAfter) };
        }
        if (!res.ok) throw { status: res.status, message: z3ErrorMessage("server", res.status) };
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
          if (z3ResultText) z3ResultText.textContent = data.error || z3ErrorMessage("verify");
          updateDeltaLocal(a, b);
        }
      })
      .catch(function (err) {
        if (err && err.status === 429 && err.retryAfter) {
          if (z3ResultText) z3ResultText.textContent = err.message;
          z3ResultText.style.color = "var(--warning, #d97706)";
        } else {
          if (z3ResultText) z3ResultText.textContent = z3ErrorMessage("unavailable");
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

function bindMobileMenu() {
  var btn = document.getElementById("mobile-menu-btn");
  var menu = document.getElementById("mobile-menu");
  if (!btn || !menu) return;
  function setOpen(open) {
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    menu.hidden = !open;
  }
  btn.addEventListener("click", function () {
    setOpen(btn.getAttribute("aria-expanded") !== "true");
  });
  menu.querySelectorAll("a").forEach(function (link) {
    link.addEventListener("click", function () {
      setOpen(false);
    });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !menu.hidden) {
      setOpen(false);
      btn.focus();
    }
  });
}

function bindPocForm() {
  var f = document.getElementById('poc-form');
  if (!f) return;
  f.addEventListener('submit', function (e) {
    e.preventDefault();
    var d = new FormData(f);
    var msg = 'Proof of concept request. Documents: ' + (d.get('pair') || '') + '; can share 5-10 sample cases: ' + d.get('cases') + '; findings review: ' + d.get('review');
    var btn = f.querySelector('button[type=submit]');
    if (btn) btn.disabled = true;
    fetch('/contact-submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Proof of concept request', email: d.get('email'), company: '', message: msg })
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var p = document.createElement('p');
      p.setAttribute('role', 'status');
      p.textContent = f.dataset.done;
      f.replaceWith(p);
    }).catch(function () {
      if (btn) btn.disabled = false;
      var p = f.querySelector('.form-error');
      if (!p) {
        p = document.createElement('p');
        p.className = 'form-error';
        p.setAttribute('role', 'alert');
        f.appendChild(p);
      }
      p.textContent = f.dataset.error;
    });
  });
}

document.addEventListener("DOMContentLoaded", function () {
  bindZ3Recalc();
  bindPocForm();
});