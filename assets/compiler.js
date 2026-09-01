(function () {
  "use strict";

  var INTENTS = ["research", "design", "comparison", "debug", "analysis"];
  var FALLBACK = "research";
  var KEYWORDS = {
    comparison: [
      "versus",
      "compared to",
      "compare",
      "difference between",
      "differences",
      "better than",
      "trade-off",
      "tradeoff",
      " vs ",
      " vs.",
      " or ",
    ],
    debug: [
      "traceback",
      "stack trace",
      "stacktrace",
      "exception",
      "typeerror",
      "nullpointer",
      "segfault",
      "bug",
      "error",
      "broken",
      "crash",
      "failing",
      "doesn't work",
      "does not work",
      "won't start",
      "fix this",
    ],
    design: [
      "architecture",
      "wireframe",
      "mockup",
      "layout",
      "ux ",
      " ui",
      "user interface",
      "design a",
      "redesign",
      "prototype",
    ],
    analysis: [
      "analyze",
      "analyse",
      "breakdown",
      "metrics",
      "trend",
      "dataset",
      "spreadsheet",
      "kpi",
      "cohort",
      "root cause",
    ],
    research: [
      "literature",
      "paper",
      "study",
      "what is",
      "how does",
      "survey",
      "evidence",
      "cite",
    ],
  };

  var ROLES = {
    research: "a senior research analyst who cites sources and flags uncertainty",
    design: "a staff product and systems designer",
    comparison: "a principal engineer writing a decision record",
    debug: "a debugging specialist who reasons from evidence",
    analysis: "a quantitative analyst",
  };

  var FORMATS = {
    research:
      "Thesis. Then VERIFIED FINDINGS (Directly from uploaded context). Then INFERRED GAPS (Logical inference; requires manual validation). Then OPEN QUESTIONS. No invented stats, dates, or publication names. Domain and case come only from the uploaded files and the user's task.",
    design: "Cover goals, constraints, the proposed design, tradeoffs, and one concrete next step.",
    comparison: "Use a comparison table, then a recommendation, then the conditions that would change it.",
    debug: "List hypotheses ranked by likelihood, how to confirm each, and the first command or code change to try.",
    analysis: "State the method, the results, the limitations, and what the numbers do not prove.",
  };

  var STRUCTURES = {
    claude:
      "<role>\n{{ role }}\n</role>\n\n<instructions>\n{{ task }}\n\nProduce the answer in this format:\n{{ format }}\n</instructions>\n\n<thinking>\nWork through the task step by step. Flag uncertainty instead of guessing.\n</thinking>\n",
    gemini:
      "Role: {{ role }}\n\nTask:\n{{ task }}\n\nOutput constraints:\n- Follow format: {{ format }}\n- Do not wrap the whole answer in JSON unless that format asks for a JSON object.\n- Every claim must come from the task or the context. If unverified, write exactly: Data not available.\n",
    deepseek:
      "## System Prompt\nYou are {{ role }}. Answer directly. Prefer precise technical language.\n\n## User Request\n{{ task }}\n\n## Output Format\n{{ format }}\n\n## Self-Correction\nBefore finishing, re-check:\n1. Answered the question that was asked?\n2. Invented facts, APIs, or numbers?\n3. Output shape matches the format?\nIf any check fails, rewrite.\n",
  };

  var HISTORY_KEY = "assure_compiler_history";
  var MAX_HISTORY = 6;
  var EMPTY = "Type a question to see the compiled prompt.";
  var TOO_LONG = "Keep this preview under 8,000 characters. Long files belong in the app on your machine.";

  function detectIntent(text) {
    var blob = " " + String(text || "").toLowerCase().replace(/\n/g, " ") + " ";
    if (!blob.trim()) return FALLBACK;
    var scores = { research: 0, design: 0, comparison: 0, debug: 0, analysis: 0 };
    Object.keys(KEYWORDS).forEach(function (name) {
      KEYWORDS[name].forEach(function (word) {
        if (blob.indexOf(word) !== -1) scores[name] += 1;
      });
    });
    var best = FALLBACK;
    INTENTS.forEach(function (name) {
      var a = scores[name];
      var b = scores[best];
      var aTie = name === FALLBACK ? -1 : 0;
      var bTie = best === FALLBACK ? -1 : 0;
      if (a > b || (a === b && aTie > bTie)) best = name;
    });
    if (scores[best] === 0) return FALLBACK;
    return best;
  }

  function renderDialect(task, target, intent) {
    var structure = STRUCTURES[target] || STRUCTURES.gemini;
    var role = ROLES[intent] || ROLES.research;
    var format = FORMATS[intent] || FORMATS.research;
    return structure
      .replace(/\{\{\s*role\s*\}\}/g, role)
      .replace(/\{\{\s*task\s*\}\}/g, task)
      .replace(/\{\{\s*format\s*\}\}/g, format)
      .replace(/\{\{\s*output_format\s*\}\}/g, format);
  }

  function compile(task, target, intentChoice) {
    var trimmed = String(task || "").trim();
    if (trimmed.length < 3) return { prompt: EMPTY, intent: FALLBACK };
    if (trimmed.length > 8000) return { prompt: TOO_LONG, intent: FALLBACK };
    var intent = intentChoice && intentChoice !== "auto" ? intentChoice : detectIntent(trimmed);
    if (INTENTS.indexOf(intent) === -1) intent = FALLBACK;
    var safeTarget = STRUCTURES[target] ? target : "gemini";
    return { prompt: renderDialect(trimmed, safeTarget, intent).trim() + "\n", intent: intent };
  }

  function loadHistory() {
    try {
      var raw = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
      return Array.isArray(raw) ? raw : [];
    } catch (_) {
      return [];
    }
  }

  function saveHistory(task, target, intent) {
    var trimmed = String(task || "").trim();
    if (!trimmed) return;
    var history = loadHistory();
    history = history.filter(function (item) {
      return item.task !== trimmed;
    });
    history.unshift({ task: trimmed, target: target, intent: intent });
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, MAX_HISTORY)));
  }

  function renderHistory(container, restore) {
    if (!container) return;
    var history = loadHistory();
    container.innerHTML = "";
    if (!history.length) {
      var empty = document.createElement("p");
      empty.className = "compiler-history-empty";
      empty.textContent = "Recent compiles on this browser stay here.";
      container.appendChild(empty);
      return;
    }
    var list = document.createElement("ul");
    list.className = "compiler-history-list";
    history.forEach(function (item, index) {
      var li = document.createElement("li");
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "compiler-history-btn";
      btn.textContent = (item.task || "Untitled").slice(0, 60);
      btn.addEventListener("click", function () {
        restore(history[index]);
      });
      li.appendChild(btn);
      list.appendChild(li);
    });
    container.appendChild(list);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var input = document.getElementById("demo-input");
    var output = document.getElementById("demo-output");
    var targetSelect = document.getElementById("demo-target");
    var intentSelect = document.getElementById("demo-intent");
    var badge = document.getElementById("demo-intent-badge");
    var historyEl = document.getElementById("demo-history");
    var form = document.getElementById("demo-compiler");
    if (form) {
      form.addEventListener("submit", function (e) {
        e.preventDefault();
      });
    }

    var timer = null;
    var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function paint() {
      var target = targetSelect ? targetSelect.value : "gemini";
      var choice = intentSelect ? intentSelect.value : "auto";
      var result = compile(input.value, target, choice);
      output.textContent = result.prompt;
      if (badge) {
        badge.textContent = result.intent;
      }
      if (result.prompt !== EMPTY && result.prompt !== TOO_LONG) {
        saveHistory(input.value, target, result.intent);
        renderHistory(historyEl, restore);
      }
    }

    function restore(item) {
      if (!item) return;
      input.value = item.task || "";
      if (targetSelect && item.target) targetSelect.value = item.target;
        if (intentSelect && item.intent) intentSelect.value = item.intent;
      paint();
    }

    function schedule() {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(paint, reduceMotion ? 0 : 150);
    }

    input.addEventListener("input", schedule);
    if (targetSelect) targetSelect.addEventListener("change", paint);
    if (intentSelect) intentSelect.addEventListener("change", paint);

    if (!input.value) input.value = "Is this paper any good?";
    paint();
    renderHistory(historyEl, restore);
  });
})();
