/**
 * Substrate Vault prompt cards (Library tab).
 * IndexedDB store; localStorage fallback. Inserts at the TipTap cursor.
 */
(function (global) {
  "use strict";

  var DB_NAME = "assure_substrate_vault";
  var STORE = "prompts";
  var DB_VERSION = 1;
  var LS_KEY = "assure_vault_prompts_v1";
  var CLASSES = ["research", "design", "comparison"];
  var _memory = [];
  var _editingId = null;
  var _useMemory = false;

  function t(key, fallback, vars) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, vars || {});
    }
    var s = fallback || key;
    if (vars) {
      Object.keys(vars).forEach(function (k) {
        s = s.replace(new RegExp("\\{" + k + "\\}", "g"), String(vars[k]));
      });
    }
    return s;
  }

  function newId() {
    if (global.crypto && typeof global.crypto.randomUUID === "function") {
      return global.crypto.randomUUID();
    }
    return "vp-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function nowIso() {
    return new Date().toISOString();
  }

  function normalizeClass(value) {
    var c = String(value || "research").toLowerCase();
    return CLASSES.indexOf(c) >= 0 ? c : "research";
  }

  function normalizeRecord(row) {
    if (!row || typeof row !== "object") return null;
    return {
      id: String(row.id || newId()),
      name: String(row.name || "").trim() || t("vault.prompts.untitled", "Untitled prompt"),
      class: normalizeClass(row.class),
      content: String(row.content || ""),
      version: Math.max(1, parseInt(row.version, 10) || 1),
      createdAt: String(row.createdAt || nowIso()),
      history: Array.isArray(row.history) ? row.history : [],
    };
  }

  function openDb() {
    return new Promise(function (resolve, reject) {
      if (!global.indexedDB) {
        reject(new Error("no-idb"));
        return;
      }
      var req = global.indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = function (event) {
        var db = event.target.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: "id" });
        }
      };
      req.onsuccess = function () {
        resolve(req.result);
      };
      req.onerror = function () {
        reject(req.error || new Error("idb-open"));
      };
    });
  }

  function lsRead() {
    try {
      var raw = global.localStorage.getItem(LS_KEY);
      var parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed.map(normalizeRecord).filter(Boolean) : [];
    } catch (_) {
      return _memory.slice();
    }
  }

  function lsWrite(rows) {
    _memory = rows.slice();
    try {
      global.localStorage.setItem(LS_KEY, JSON.stringify(rows));
    } catch (_) {}
  }

  function all() {
    if (_useMemory) {
      return Promise.resolve(lsRead());
    }
    return openDb()
      .then(function (db) {
        return new Promise(function (resolve, reject) {
          var tx = db.transaction(STORE, "readonly");
          var req = tx.objectStore(STORE).getAll();
          req.onsuccess = function () {
            var rows = (req.result || []).map(normalizeRecord).filter(Boolean);
            resolve(rows);
          };
          req.onerror = function () {
            reject(req.error);
          };
        });
      })
      .catch(function () {
        _useMemory = true;
        return lsRead();
      });
  }

  function put(record) {
    var row = normalizeRecord(record);
    if (_useMemory) {
      var rows = lsRead().filter(function (item) {
        return item.id !== row.id;
      });
      rows.push(row);
      lsWrite(rows);
      return Promise.resolve(row);
    }
    return openDb()
      .then(function (db) {
        return new Promise(function (resolve, reject) {
          var tx = db.transaction(STORE, "readwrite");
          tx.objectStore(STORE).put(row);
          tx.oncomplete = function () {
            resolve(row);
          };
          tx.onerror = function () {
            reject(tx.error);
          };
        });
      })
      .catch(function () {
        _useMemory = true;
        return put(row);
      });
  }

  function remove(id) {
    if (_useMemory) {
      lsWrite(
        lsRead().filter(function (item) {
          return item.id !== id;
        })
      );
      return Promise.resolve();
    }
    return openDb()
      .then(function (db) {
        return new Promise(function (resolve, reject) {
          var tx = db.transaction(STORE, "readwrite");
          tx.objectStore(STORE).delete(id);
          tx.oncomplete = function () {
            resolve();
          };
          tx.onerror = function () {
            reject(tx.error);
          };
        });
      })
      .catch(function () {
        _useMemory = true;
        return remove(id);
      });
  }

  function insertIntoEditor(text) {
    function paste() {
      var tiptap = global.AssureTiptapEditor;
      if (tiptap && typeof tiptap.insertAtCursor === "function" && tiptap.insertAtCursor(text)) {
        return true;
      }
      var ta = document.getElementById("generate-intent");
      if (!ta) return false;
      var start = typeof ta.selectionStart === "number" ? ta.selectionStart : ta.value.length;
      var end = typeof ta.selectionEnd === "number" ? ta.selectionEnd : start;
      ta.value = ta.value.slice(0, start) + text + ta.value.slice(end);
      var pos = start + String(text).length;
      ta.focus();
      if (ta.setSelectionRange) ta.setSelectionRange(pos, pos);
      return true;
    }
    if (global.AssureNav && typeof global.AssureNav.switchView === "function") {
      global.AssureNav.switchView("generate");
    }
    window.setTimeout(function () {
      var ok = paste();
      if (!ok) ok = paste();
      if (global.AssureToast) {
        global.AssureToast.show(
          ok
            ? t("vault.prompts.inserted", "Inserted into the editor.")
            : t("vault.prompts.no_editor", "Open Compile to insert at the cursor."),
          ok ? "success" : "info"
        );
      }
    }, 80);
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function classLabel(cls) {
    return t("vault.prompts.class." + cls, cls);
  }

  function cardHtml(row) {
    var editing = _editingId === row.id;
    var preview = (row.content || "").trim().slice(0, 160);
    if (editing) {
      return (
        '<article class="vault-prompt-card is-editing" data-id="' +
        escapeHtml(row.id) +
        '">' +
        '<label class="vault-field">' +
        escapeHtml(t("vault.prompts.name", "Name")) +
        '<input type="text" class="form-control vault-name" value="' +
        escapeHtml(row.name) +
        '"></label>' +
        '<label class="vault-field">' +
        escapeHtml(t("vault.prompts.class", "Class")) +
        '<select class="select-control vault-class">' +
        CLASSES.map(function (cls) {
          return (
            '<option value="' +
            cls +
            '"' +
            (cls === row.class ? " selected" : "") +
            ">" +
            escapeHtml(classLabel(cls)) +
            "</option>"
          );
        }).join("") +
        "</select></label>" +
        '<label class="vault-field">' +
        escapeHtml(t("vault.prompts.content", "Content")) +
        '<textarea class="form-control vault-content" rows="6">' +
        escapeHtml(row.content) +
        "</textarea></label>" +
        '<div class="vault-card-actions">' +
        '<button type="button" class="btn btn-primary btn-sm" data-act="save">' +
        escapeHtml(t("vault.prompts.save", "Save")) +
        "</button>" +
        '<button type="button" class="btn btn-outline btn-sm" data-act="cancel">' +
        escapeHtml(t("vault.prompts.cancel", "Cancel")) +
        "</button>" +
        "</div></article>"
      );
    }
    return (
      '<article class="vault-prompt-card" data-id="' +
      escapeHtml(row.id) +
      '">' +
      "<h3>" +
      escapeHtml(row.name) +
      "</h3>" +
      '<p class="vault-meta"><span class="vault-class-pill">' +
      escapeHtml(classLabel(row.class)) +
      "</span> · " +
      escapeHtml(t("vault.prompts.version", "Version {n}", { n: String(row.version) })) +
      "</p>" +
      '<p class="vault-preview">' +
      escapeHtml(preview || t("vault.prompts.empty_content", "No content yet.")) +
      "</p>" +
      '<div class="vault-card-actions">' +
      '<button type="button" class="btn btn-outline btn-sm" data-act="edit">' +
      escapeHtml(t("vault.prompts.edit", "Edit")) +
      "</button>" +
      '<button type="button" class="btn btn-outline btn-sm" data-act="version">' +
      escapeHtml(t("vault.prompts.save_version", "Save Version")) +
      "</button>" +
      '<button type="button" class="btn btn-primary btn-sm" data-act="insert">' +
      escapeHtml(t("vault.prompts.insert", "Insert into Editor")) +
      "</button>" +
      "</div></article>"
    );
  }

  function bindGrid(root, rows) {
    root.querySelectorAll(".vault-prompt-card").forEach(function (card) {
      var id = card.getAttribute("data-id");
      var row = rows.filter(function (item) {
        return item.id === id;
      })[0];
      card.addEventListener("click", function (event) {
        var btn = event.target.closest("[data-act]");
        if (!btn || !row) return;
        var act = btn.getAttribute("data-act");
        if (act === "edit") {
          _editingId = id;
          render();
        } else if (act === "cancel") {
          _editingId = null;
          render();
        } else if (act === "save") {
          row.name = (card.querySelector(".vault-name") || {}).value || row.name;
          row.class = normalizeClass((card.querySelector(".vault-class") || {}).value);
          row.content = (card.querySelector(".vault-content") || {}).value || "";
          _editingId = null;
          put(row).then(render);
        } else if (act === "version") {
          var snapshot = {
            version: row.version,
            content: row.content,
            name: row.name,
            class: row.class,
            savedAt: nowIso(),
          };
          row.history = (row.history || []).concat([snapshot]);
          row.version = row.version + 1;
          put(row).then(render);
        } else if (act === "insert") {
          insertIntoEditor(row.content || "");
        }
      });
    });
  }

  function render() {
    var grid = document.getElementById("vault-prompt-grid");
    if (!grid) return;
    all().then(function (rows) {
      rows.sort(function (a, b) {
        return String(b.createdAt).localeCompare(String(a.createdAt));
      });
      if (!rows.length && _editingId !== "new") {
        grid.innerHTML =
          '<p class="hint" data-i18n="vault.prompts.empty">' +
          escapeHtml(t("vault.prompts.empty", "No saved prompts yet. Add one to insert into the editor.")) +
          "</p>";
        return;
      }
      var html = "";
      if (_editingId === "new") {
        html += cardHtml({
          id: "new",
          name: "",
          class: "research",
          content: "",
          version: 1,
          createdAt: nowIso(),
          history: [],
        });
      }
      html += rows
        .map(function (row) {
          return cardHtml(row);
        })
        .join("");
      grid.innerHTML = html;
      bindGrid(grid, rows);
      var newCard = grid.querySelector('[data-id="new"]');
      if (newCard) {
        newCard.addEventListener("click", function (event) {
          var btn = event.target.closest("[data-act]");
          if (!btn) return;
          if (btn.getAttribute("data-act") === "cancel") {
            _editingId = null;
            render();
            return;
          }
          if (btn.getAttribute("data-act") !== "save") return;
          var record = {
            id: newId(),
            name: (newCard.querySelector(".vault-name") || {}).value,
            class: (newCard.querySelector(".vault-class") || {}).value,
            content: (newCard.querySelector(".vault-content") || {}).value,
            version: 1,
            createdAt: nowIso(),
            history: [],
          };
          _editingId = null;
          put(record).then(render);
        });
      }
    });
  }

  function bind() {
    var addBtn = document.getElementById("vault-prompt-add");
    if (addBtn && !addBtn.dataset.bound) {
      addBtn.dataset.bound = "1";
      addBtn.addEventListener("click", function () {
        _editingId = "new";
        render();
      });
    }
    document.addEventListener("assure:view", function (event) {
      if (event.detail && event.detail.view === "library") render();
    });
    document.addEventListener("assure:i18n", function () {
      var view = document.getElementById("view-library");
      if (view && !view.hidden) render();
    });
  }

  global.AssureVaultPrompts = {
    render: render,
    bind: bind,
    insertIntoEditor: insertIntoEditor,
    put: put,
    all: all,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})(typeof window !== "undefined" ? window : this);
