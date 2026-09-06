(function (global) {
  "use strict";

  function t(key, fallback, vars) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, vars || {});
    }
    return fallback || key;
  }

  function projectId() {
    return global.__ASSURE_PROJECT_ID__ || "default";
  }

  function $(id) {
    return document.getElementById(id);
  }

  function setLockedUi(locked, meta) {
    var banner = $("compliance-lock-banner");
    var lockBtn = $("compliance-lock-btn");
    if (banner) {
      if (locked && meta) {
        banner.hidden = false;
        banner.textContent = t(
          "compliance.lock.banner",
          "🔒 Locked by {user} at {time}",
          { user: meta.locked_by || "—", time: meta.locked_at || "—" }
        );
      } else {
        banner.hidden = true;
      }
    }
    if (lockBtn) lockBtn.disabled = !!locked;
    document.body.classList.toggle("assure-doc-locked", !!locked);
  }

  function disableEditControls(disabled) {
    var selectors = [
      "#save-status",
      ".jdf-node-fab",
      "#jdf-import-pdf-btn",
      "#jdf-import-config-btn",
      "#version-restore-btn",
    ];
    selectors.forEach(function (sel) {
      document.querySelectorAll(sel).forEach(function (el) {
        el.disabled = disabled;
        if (disabled) el.setAttribute("aria-disabled", "true");
        else el.removeAttribute("aria-disabled");
      });
    });
  }

  function refreshLockState() {
    var pid = projectId();
    if (!pid) return;
    fetch("/api/projects/" + encodeURIComponent(pid) + "/lock")
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var locked = !!(data && data.locked);
        setLockedUi(locked, data.lock || null);
        disableEditControls(locked);
      })
      .catch(function () {});
  }

  function lockDocument() {
    var pid = projectId();
    if (!pid) return;
    fetch("/api/projects/" + encodeURIComponent(pid) + "/lock", { method: "POST" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data && data.ok) {
          if (global.AssureToast) {
            global.AssureToast.show(
              t("compliance.lock.ok", "Document locked"),
              "success"
            );
          }
          refreshLockState();
        }
      })
      .catch(function () {});
  }

  function submitSignOff(status) {
    var pid = projectId();
    var nameEl = $("signoff-reviewer-name");
    var commentEl = $("signoff-comment");
    var body = {
      status: status,
      reviewer_name: nameEl ? nameEl.value.trim() : "",
      comment: commentEl ? commentEl.value.trim() : "",
    };
    fetch("/api/projects/" + encodeURIComponent(pid) + "/sign-off", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data && data.ok && global.AssureToast) {
          global.AssureToast.show(t("compliance.signoff.ok", "Sign-off recorded"), "success");
        }
        loadSignOffs();
      })
      .catch(function () {});
  }

  function loadSignOffs() {
    var pid = projectId();
    var list = $("signoff-list");
    if (!list || !pid) return;
    fetch("/api/projects/" + encodeURIComponent(pid) + "/sign-offs")
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var items = (data && data.sign_offs) || [];
        if (!items.length) {
          list.innerHTML = "<li class=\"muted\">—</li>";
          return;
        }
        list.innerHTML = items
          .map(function (so) {
            return (
              "<li><strong>" +
              (so.reviewer_name_display || so.reviewer_id) +
              "</strong> — " +
              so.status +
              " <span class=\"muted\">" +
              (so.timestamp || "") +
              "</span>" +
              (so.comment ? "<br><em>" + so.comment + "</em>" : "") +
              "</li>"
            );
          })
          .join("");
      })
      .catch(function () {});
  }

  function loadDecisionLog() {
    var pid = projectId();
    var timeline = $("decision-log-timeline");
    if (!timeline || !pid) return;
    var tag = "project:" + pid;
    fetch("/api/omp/memories?tags=" + encodeURIComponent(tag))
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var memories = (data && data.memories) || data || [];
        if (!Array.isArray(memories) || !memories.length) {
          timeline.innerHTML =
            "<p class=\"muted\">" +
            t("compliance.decision_log.empty", "No decision memories for this project yet.") +
            "</p>";
          return;
        }
        timeline.innerHTML = memories
          .map(function (m) {
            var key = m.key || m.id || "memory";
            var content = (m.content || m.summary || "").slice(0, 280);
            var ts = m.created_at || m.timestamp || "";
            return (
              "<article class=\"decision-log-item\"><h4>" +
              key +
              "</h4><p>" +
              content +
              "</p><time class=\"muted\">" +
              ts +
              "</time></article>"
            );
          })
          .join("");
      })
      .catch(function () {
        timeline.innerHTML =
          "<p class=\"muted\">" +
          t("compliance.decision_log.empty", "No decision memories for this project yet.") +
          "</p>";
      });
  }

  function importConfigFile(file) {
    var pid = projectId();
    if (!pid || !file) return;
    var fd = new FormData();
    fd.append("file", file);
    fetch("/api/projects/" + encodeURIComponent(pid) + "/import-config", {
      method: "POST",
      body: fd,
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data && data.ok) {
          if (global.AssureToast) {
            global.AssureToast.show(t("jdf.import.config_ok", "Config imported"), "success");
          }
          if (global.AssureJdfCanvas && global.AssureJdfCanvas.reloadDocument) {
            global.AssureJdfCanvas.reloadDocument();
          } else {
            location.reload();
          }
        }
      })
      .catch(function () {});
  }

  function bindUi() {
    var lockBtn = $("compliance-lock-btn");
    if (lockBtn) lockBtn.addEventListener("click", lockDocument);

    var approveBtn = $("signoff-approve-btn");
    var rejectBtn = $("signoff-reject-btn");
    if (approveBtn) approveBtn.addEventListener("click", function () { submitSignOff("approved"); });
    if (rejectBtn) rejectBtn.addEventListener("click", function () { submitSignOff("rejected"); });

    var decisionBtn = $("decision-log-open-btn");
    var decisionPanel = $("decision-log-panel");
    if (decisionBtn && decisionPanel) {
      decisionBtn.addEventListener("click", function () {
        decisionPanel.hidden = !decisionPanel.hidden;
        if (!decisionPanel.hidden) loadDecisionLog();
      });
    }

    var signoffBtn = $("signoff-open-btn");
    var signoffPanel = $("signoff-panel");
    if (signoffBtn && signoffPanel) {
      signoffBtn.addEventListener("click", function () {
        signoffPanel.hidden = !signoffPanel.hidden;
        if (!signoffPanel.hidden) loadSignOffs();
      });
    }

    var configBtn = $("jdf-import-config-btn");
    var configInput = $("jdf-import-config-input");
    if (configBtn && configInput) {
      configBtn.addEventListener("click", function () { configInput.click(); });
      configInput.addEventListener("change", function () {
        if (configInput.files && configInput.files[0]) {
          importConfigFile(configInput.files[0]);
          configInput.value = "";
        }
      });
    }

    refreshLockState();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindUi);
  } else {
    bindUi();
  }

  global.AssureCompliance = {
    refreshLockState: refreshLockState,
    loadDecisionLog: loadDecisionLog,
    loadSignOffs: loadSignOffs,
  };
})(window);
