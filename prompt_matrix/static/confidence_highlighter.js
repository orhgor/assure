/**
 * ConfidenceHighlighter — split AST text into Z3/ledger spans.
 * Classes: bg-green-200 (>0.8), bg-yellow-200 (0.4–0.8), bg-red-200 (<0.4).
 * When disabled, the host is plain text (no background classes).
 */
(function (global) {
  "use strict";

  function classForScore(score) {
    var n = Number(score);
    if (!Number.isFinite(n)) return "bg-yellow-200";
    if (n > 0.8) return "bg-green-200";
    if (n >= 0.4) return "bg-yellow-200";
    return "bg-red-200";
  }

  function normalize(span) {
    var start = Number(span.startChar != null ? span.startChar : span.start);
    var end = Number(span.endChar != null ? span.endChar : span.end);
    var score = Number(span.score);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
    return {
      start: start,
      end: end,
      score: Number.isFinite(score) ? score : 0.55,
      source: span.source ? String(span.source) : "",
      reason: span.reason ? String(span.reason) : "",
    };
  }

  function paint(host, text, spans, enabled) {
    if (!host) return;
    var raw = text == null ? "" : String(text);
    host.textContent = "";
    if (!enabled || !spans || !spans.length) {
      host.textContent = raw;
      return;
    }
    var marks = [];
    (spans || []).forEach(function (span) {
      var item = normalize(span);
      if (item) marks.push(item);
    });
    marks.sort(function (a, b) {
      return a.start - b.start || b.end - a.end || a.score - b.score;
    });
    var cursor = 0;
    var n = raw.length;
    marks.forEach(function (mark) {
      var start = Math.max(cursor, Math.min(n, mark.start));
      var end = Math.max(start, Math.min(n, mark.end));
      if (end <= cursor) return;
      if (start > cursor) {
        host.appendChild(document.createTextNode(raw.slice(cursor, start)));
      }
      var el = document.createElement("span");
      el.className = classForScore(mark.score);
      el.setAttribute("data-confidence-score", String(mark.score));
      if (mark.source) el.setAttribute("data-confidence-source", mark.source);
      if (mark.reason) {
        el.setAttribute("data-confidence-reason", mark.reason);
        el.setAttribute("title", mark.reason);
      }
      el.textContent = raw.slice(start, end);
      if (mark.reason) {
        var tip = document.createElement("button");
        tip.type = "button";
        tip.className = "z3-reason-icon";
        tip.setAttribute("aria-label", "Why this score");
        tip.setAttribute("title", mark.reason);
        tip.textContent = "i";
        el.appendChild(tip);
      }
      host.appendChild(el);
      cursor = end;
    });
    if (cursor < n) {
      host.appendChild(document.createTextNode(raw.slice(cursor)));
    }
    if (!host.childNodes.length) {
      host.textContent = raw;
    }
  }

  global.AssureConfidenceHighlighter = {
    classForScore: classForScore,
    paint: paint,
  };
})(typeof window !== "undefined" ? window : this);
