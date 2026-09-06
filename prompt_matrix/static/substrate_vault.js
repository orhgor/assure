/**
 * Substrate Vault: list, upload, delete, and include/exclude the source
 * files grounding this project's compiles. Talks to
 * /api/projects/<id>/substrate[/upload|/<file_id>].
 *
 * Status is not a polled async state — Textract runs synchronously inside
 * the upload request, so a row only ever exists once extraction already
 * succeeded. "Processing" is purely the optimistic row shown while that
 * POST is in flight.
 */
(function (global) {
  "use strict";

  var doc = global.document;
  var COLLAPSE_KEY = "assure_vault_collapsed";
  var IMAGE_EXT = ["png", "jpg", "jpeg", "tif", "tiff", "gif"];
  var SHEET_EXT = ["csv", "xlsx", "xls"];

  function $(id) {
    return doc.getElementById(id);
  }

  function projectId() {
    return global.__ASSURE_PROJECT_ID__ || "default";
  }

  function t(key, fallback, params) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, params || {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    if (!params) return fallback;
    return fallback.replace(/\{(\w+)\}/g, function (_, name) {
      return String(params[name] != null ? params[name] : "");
    });
  }

  function toast(message, kind) {
    if (global.AssureToast && typeof global.AssureToast.show === "function") {
      global.AssureToast.show(message, kind || "info");
    }
  }

  function formatBytes(bytes) {
    var n = Number(bytes) || 0;
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function extOf(filename) {
    var parts = String(filename || "").split(".");
    return parts.length > 1 ? parts.pop().toLowerCase() : "";
  }

  function iconFor(filename) {
    var ext = extOf(filename);
    if (IMAGE_EXT.indexOf(ext) >= 0) return "🖼️";
    if (SHEET_EXT.indexOf(ext) >= 0) return "📊";
    return "📄";
  }

  function truncateName(name, max) {
    name = String(name || "");
    if (name.length <= max) return name;
    var ext = name.lastIndexOf(".") > 0 ? name.slice(name.lastIndexOf(".")) : "";
    var base = ext ? name.slice(0, -ext.length) : name;
    var keep = Math.max(4, max - ext.length - 1);
    return base.slice(0, keep) + "…" + ext;
  }

  async function apiJson(url, options) {
    var res = await global.fetch(url, options);
    var data = await res.json().catch(function () {
      return {};
    });
    return { ok: res.ok && data.ok !== false, status: res.status, data: data };
  }

  var Vault = {
    files: [],
    _optimisticSeq: 0,
    _bound: false,

    listEl: null,
    emptyEl: null,
    countEl: null,
    uploadBtn: null,
    fileInput: null,
    statusEl: null,
    detailsEl: null,
    searchEl: null,
    searchQuery: "",

    formatBytes: formatBytes,

    /** Ids of files currently checked "include in compile" — read by generate.js. */
    selectedIncludedIds: function () {
      return this.files
        .filter(function (f) {
          return f.included !== false && !f._optimistic;
        })
        .map(function (f) {
          return f.id;
        });
    },

    fetchList: async function () {
      try {
        var result = await apiJson(
          "/api/projects/" + encodeURIComponent(projectId()) + "/substrate",
          { credentials: "same-origin" }
        );
        if (result.ok) {
          this.files = (result.data && result.data.files) || [];
          this.render();
        }
      } catch (_) {
        /* Vault list is best-effort; upload flow still works without it. */
      }
    },

    _updateNavBadge: function (count) {
      var badge = $("nav-sources-badge");
      if (!badge) return;
      var included = this.selectedIncludedIds().length;
      var n = included || count || 0;
      if (n > 0) {
        badge.hidden = false;
        badge.textContent = String(n);
        badge.setAttribute("aria-hidden", "false");
      } else {
        badge.hidden = true;
        badge.textContent = "";
        badge.setAttribute("aria-hidden", "true");
      }
    },

    _matchesSearch: function (file) {
      var q = (this.searchQuery || "").trim().toLowerCase();
      if (!q) return true;
      return String(file.filename || "").toLowerCase().indexOf(q) >= 0;
    },

    render: function () {
      if (!this.listEl) return;
      var self = this;
      this.listEl.innerHTML = "";
      var real = this.files.filter(function (f) {
        return !f._optimistic;
      });

      if (this.countEl) {
        if (real.length) {
          this.countEl.hidden = false;
          this.countEl.textContent = String(real.length);
        } else {
          this.countEl.hidden = true;
        }
      }
      this._updateNavBadge(real.length);
      if (this.emptyEl) {
        this.emptyEl.hidden = this.files.length > 0;
      }

      this.files.forEach(function (file) {
        if (!self._matchesSearch(file)) return;
        self.listEl.appendChild(self._renderRow(file));
      });
    },

    _renderRow: function (file) {
      var self = this;
      var li = doc.createElement("li");
      li.className = "substrate-file-row";
      li.dataset.fileId = file.id;
      if (file._optimistic) li.classList.add("is-processing");
      if (file._failed) li.classList.add("is-failed");

      var checkbox = doc.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "substrate-file-checkbox";
      checkbox.checked = file.included !== false;
      checkbox.disabled = !!file._optimistic;
      checkbox.setAttribute(
        "aria-label",
        t("substrate.vault.include", "Include {filename} in compilation", {
          filename: file.filename,
        })
      );
      checkbox.addEventListener("click", function (e) {
        e.stopPropagation();
      });
      checkbox.addEventListener("change", function () {
        self.setIncluded(file.id, checkbox.checked);
      });

      var main = doc.createElement("div");
      main.className = "substrate-file-main";

      var nameRow = doc.createElement("div");
      nameRow.className = "substrate-file-name-row";
      var icon = doc.createElement("span");
      icon.className = "substrate-file-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = iconFor(file.filename);
      var name = doc.createElement("span");
      name.className = "substrate-file-name";
      name.textContent = truncateName(file.filename, 28);
      name.title = file.filename || "";
      nameRow.appendChild(icon);
      nameRow.appendChild(name);

      var metaRow = doc.createElement("div");
      metaRow.className = "substrate-file-meta-row";

      var status = doc.createElement("span");
      status.className = "substrate-file-status";
      if (file._optimistic) {
        status.classList.add("status-processing");
        status.textContent = "⏳ " + t("substrate.vault.processing", "Processing");
      } else if (file._failed) {
        status.classList.add("status-failed");
        status.textContent = "❌ " + t("substrate.vault.failed", "Failed");
      } else {
        status.classList.add("status-verified");
        status.textContent = "✅ " + t("substrate.vault.verified", "Verified");
      }
      metaRow.appendChild(status);

      if (!file._optimistic) {
        var size = doc.createElement("span");
        size.className = "substrate-file-size";
        size.textContent = formatBytes(file.file_size_bytes);
        metaRow.appendChild(size);
      }

      if (file.claims_count) {
        var claims = doc.createElement("span");
        claims.className = "substrate-file-claims";
        claims.textContent = t("substrate.vault.claims_count", "{count} claims", {
          count: file.claims_count,
        });
        metaRow.appendChild(claims);
      }

      main.appendChild(nameRow);
      main.appendChild(metaRow);

      var del = doc.createElement("button");
      del.type = "button";
      del.className = "substrate-file-delete";
      del.setAttribute(
        "aria-label",
        t("substrate.vault.delete", "Delete {filename}", { filename: file.filename })
      );
      del.innerHTML = "&times;";
      del.hidden = !!file._optimistic;
      del.addEventListener("click", function (e) {
        e.stopPropagation();
        self.remove(file.id, file.filename);
      });

      li.appendChild(checkbox);
      li.appendChild(main);
      li.appendChild(del);

      if (!file._optimistic) {
        li.addEventListener("click", function () {
          self.focusFile(file.id);
        });
      }

      return li;
    },

    focusFile: function (fileId) {
      this.listEl.querySelectorAll(".substrate-file-row").forEach(function (row) {
        row.classList.toggle("is-focused", row.dataset.fileId === fileId);
      });
      doc.dispatchEvent(
        new CustomEvent("assure:substrate:focus", { detail: { fileId: fileId } })
      );
    },

    setIncluded: async function (fileId, included) {
      var file = this.files.filter(function (f) {
        return f.id === fileId;
      })[0];
      if (file) file.included = included;
      try {
        await apiJson(
          "/api/projects/" +
            encodeURIComponent(projectId()) +
            "/substrate/" +
            encodeURIComponent(fileId),
          {
            method: "PATCH",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ included: !!included }),
          }
        );
      } catch (_) {
        toast(t("error.server", "Something went wrong. Try again."), "error");
      }
    },

    remove: async function (fileId, filename) {
      var msg = t("substrate.vault.delete_confirm", 'Delete "{filename}" from the Substrate Vault?', {
        filename: filename || "",
      });
      if (!global.confirm(msg)) return;
      try {
        var result = await apiJson(
          "/api/projects/" +
            encodeURIComponent(projectId()) +
            "/substrate/" +
            encodeURIComponent(fileId),
          { method: "DELETE", credentials: "same-origin" }
        );
        if (!result.ok) throw new Error((result.data && result.data.error) || "delete failed");
        this.files = this.files.filter(function (f) {
          return f.id !== fileId;
        });
        this.render();
      } catch (_) {
        toast(t("error.server", "Something went wrong. Try again."), "error");
      }
    },

    upload: async function (file) {
      if (!file) return;
      var self = this;
      var tempId = "optimistic-" + ++this._optimisticSeq;
      var optimisticRow = {
        id: tempId,
        filename: file.name,
        file_size_bytes: file.size,
        included: true,
        claims_count: 0,
        _optimistic: true,
      };
      this.files.unshift(optimisticRow);
      this.render();
      if (this.statusEl) {
        this.statusEl.textContent = t("substrate.vault.uploading", "Uploading…");
      }

      var form = new global.FormData();
      form.append("file", file);
      try {
        var res = await global.fetch(
          "/api/projects/" + encodeURIComponent(projectId()) + "/substrate/upload",
          { method: "POST", credentials: "same-origin", body: form }
        );
        var data = await res.json().catch(function () {
          return {};
        });
        if (res.status === 202 && data.task_id) {
          await self.pollUploadStatus(data.task_id, optimisticRow, file);
          return;
        }
        if (!res.ok || data.ok === false) {
          throw new Error(data.error || t("error.server", "Something went wrong. Try again."));
        }
        self._applyUploadedFile(optimisticRow, file, data);
      } catch (err) {
        var failIdx = this.files.indexOf(optimisticRow);
        if (failIdx >= 0) this.files.splice(failIdx, 1);
        toast((err && err.message) || t("error.server", "Something went wrong. Try again."), "error");
      } finally {
        if (this.statusEl) this.statusEl.textContent = "";
        this.render();
      }
    },

    _applyUploadedFile: function (optimisticRow, file, data) {
      var idx = this.files.indexOf(optimisticRow);
      if (idx >= 0) {
        this.files[idx] = {
          id: data.id,
          filename: data.filename || file.name,
          file_size_bytes: data.size_bytes || file.size,
          included: true,
          claims_count: 0,
        };
      }
      toast(t("substrate.vault.verified", "Verified"), "success");
      if (data.text) {
        global.fetch("/api/omp/remember", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            key: "substrate:" + projectId() + ":" + (data.id || file.name),
            content: String(data.text).slice(0, 4000),
            tags: ["substrate", projectId(), data.filename || file.name],
          }),
        }).catch(function () {});
      }
    },

    pollUploadStatus: async function (taskId, optimisticRow, file) {
      var self = this;
      var res = await global.fetch("/api/tasks/" + encodeURIComponent(taskId), {
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      var data = await res.json();
      var status = String(data.status || "").toLowerCase();

      if (status === "pending" || status === "processing") {
        if (self.statusEl) {
          self.statusEl.textContent = t("substrate.vault.processing", "Processing");
        }
        await new Promise(function (resolve) {
          global.setTimeout(resolve, 2000);
        });
        return self.pollUploadStatus(taskId, optimisticRow, file);
      }
      if (status === "success") {
        var entry = (data.result && data.result.entry) || data.result || {};
        self._applyUploadedFile(optimisticRow, file, entry);
        return;
      }
      throw new Error((data.error || data.result && data.result.error) || "Upload processing failed");
    },

    triggerUpload: function () {
      if (this.fileInput) this.fileInput.click();
    },

    bindCollapse: function () {
      var self = this;
      if (!this.detailsEl) return;
      try {
        var stored = global.localStorage.getItem(COLLAPSE_KEY);
        if (stored === "1") this.detailsEl.open = false;
        else if (stored === "0") this.detailsEl.open = true;
        else this.detailsEl.open = false;
      } catch (_) {}
      this.detailsEl.addEventListener("toggle", function () {
        try {
          global.localStorage.setItem(COLLAPSE_KEY, self.detailsEl.open ? "0" : "1");
        } catch (_) {}
      });
    },

    bind: function () {
      if (this._bound) return;
      this._bound = true;
      var self = this;
      this.listEl = $("substrate-vault-list");
      this.emptyEl = $("substrate-vault-empty");
      this.countEl = $("substrate-vault-count");
      this.uploadBtn = $("substrate-vault-upload-btn");
      this.fileInput = $("substrate-vault-file-input");
      this.statusEl = $("substrate-vault-upload-status");
      this.detailsEl = $("substrate-vault");
      this.searchEl = $("substrate-vault-search");

      this.bindCollapse();

      if (this.searchEl) {
        this.searchEl.addEventListener("input", function () {
          self.searchQuery = self.searchEl.value || "";
          self.render();
        });
      }

      if (this.uploadBtn) {
        this.uploadBtn.addEventListener("click", function () {
          self.triggerUpload();
        });
      }
      if (this.fileInput) {
        this.fileInput.addEventListener("change", function () {
          var file = this.files && this.files[0];
          this.value = "";
          if (file) self.upload(file);
        });
      }

      doc.addEventListener("assure:i18n", function () {
        self.render();
      });

      this.fetchList();
    },
  };

  function init() {
    Vault.bind();
  }

  if (doc.readyState === "loading") {
    doc.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  global.AssureSubstrateVault = Vault;
})(typeof window !== "undefined" ? window : this);
