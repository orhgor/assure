/**
 * 3-step new project wizard (Type → Sources → Prompt).
 */
(function (global) {
  "use strict";

  function $(id) {
    return global.document.getElementById(id);
  }

  function t(key, fallback, vars) {
    if (typeof global.__assureTf === "function") {
      return global.__assureTf(key, fallback, vars || {});
    }
    if (typeof global.__assureT === "function") {
      return global.__assureT(key, fallback);
    }
    return fallback || key;
  }

  function escHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  var Wizard = {
    step: 1,
    templates: [],
    prompts: [],
    selectedTemplateId: "blank",
    pendingFiles: [],
    _bound: false,

    init: function () {
      if (this._bound) return;
      this._bound = true;
      var modal = $("new-project-wizard");
      if (!modal) return;
      var self = this;
      $("wizard-backdrop") &&
        $("wizard-backdrop").addEventListener("click", function () {
          self.close();
        });
      $("wizard-back-btn") &&
        $("wizard-back-btn").addEventListener("click", function () {
          self.prevStep();
        });
      $("wizard-next-btn") &&
        $("wizard-next-btn").addEventListener("click", function () {
          self.nextStep();
        });
      $("wizard-create-btn") &&
        $("wizard-create-btn").addEventListener("click", function () {
          self.finish();
        });
      $("wizard-skip-link") &&
        $("wizard-skip-link").addEventListener("click", function (e) {
          e.preventDefault();
          self.skip();
        });
      var drop = $("wizard-dropzone");
      var input = $("wizard-file-input");
      var browse = $("wizard-browse-btn");
      if (browse && input) {
        browse.addEventListener("click", function () {
          input.click();
        });
      }
      if (drop) {
        drop.addEventListener("dragover", function (e) {
          e.preventDefault();
          drop.classList.add("is-dragover");
        });
        drop.addEventListener("dragleave", function () {
          drop.classList.remove("is-dragover");
        });
        drop.addEventListener("drop", function (e) {
          e.preventDefault();
          drop.classList.remove("is-dragover");
          self.addFiles(e.dataTransfer && e.dataTransfer.files);
        });
        drop.addEventListener("click", function (e) {
          if (e.target === browse || (browse && browse.contains(e.target))) return;
          if (input) input.click();
        });
      }
      if (input) {
        input.addEventListener("change", function () {
          self.addFiles(input.files);
          input.value = "";
        });
      }
      var sel = $("wizard-prompt-select");
      if (sel) {
        sel.addEventListener("change", function () {
          self.onPromptSelect();
        });
      }
    },

    open: function () {
      this.step = 1;
      this.pendingFiles = [];
      this.selectedTemplateId = "blank";
      var modal = $("new-project-wizard");
      if (!modal) return;
      modal.hidden = false;
      global.document.body.classList.add("wizard-open");
      this.renderStep();
      this.loadCatalogs();
    },

    close: function () {
      var modal = $("new-project-wizard");
      if (modal) modal.hidden = true;
      global.document.body.classList.remove("wizard-open");
    },

    skip: function () {
      this.close();
      var form = $("projects-new-form");
      var newBtn = $("projects-new-btn");
      if (form && newBtn) {
        form.hidden = false;
        newBtn.hidden = true;
        var input = $("projects-new-input");
        if (input) input.focus();
      }
    },

    loadCatalogs: function () {
      var self = this;
      Promise.all([
        fetch("/api/project-templates", { credentials: "same-origin" }).then(function (r) {
          return r.json();
        }),
        fetch("/api/prompts", { credentials: "same-origin" }).then(function (r) {
          return r.json();
        }),
      ])
        .then(function (pair) {
          self.templates = (pair[0] && pair[0].templates) || [];
          self.prompts = (pair[1] && pair[1].prompts) || [];
          self.renderTemplates();
          self.renderPromptSelect();
        })
        .catch(function () {
          self.templates = [{ id: "blank", name: "Blank", default_prompt: "", jdf_structure: { body: [] } }];
          self.renderTemplates();
        });
    },

    renderTemplates: function () {
      var grid = $("wizard-template-grid");
      if (!grid) return;
      var self = this;
      grid.innerHTML = this.templates
        .map(function (tpl) {
          var active = tpl.id === self.selectedTemplateId ? " is-selected" : "";
          return (
            '<button type="button" class="wizard-template-card' +
            active +
            '" data-template-id="' +
            escHtml(tpl.id) +
            '" role="option" aria-selected="' +
            (active ? "true" : "false") +
            '">' +
            '<span class="wizard-template-name">' +
            escHtml(tpl.name) +
            "</span></button>"
          );
        })
        .join("");
      grid.querySelectorAll("[data-template-id]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          self.selectedTemplateId = btn.getAttribute("data-template-id") || "blank";
          self.renderTemplates();
          var tpl = self.templates.find(function (x) {
            return x.id === self.selectedTemplateId;
          });
          var titleInput = $("wizard-title-input");
          var promptArea = $("wizard-prompt-text");
          if (titleInput && !titleInput.value.trim() && tpl) {
            titleInput.value = tpl.name;
          }
          if (promptArea && tpl && tpl.default_prompt && !promptArea.value.trim()) {
            promptArea.value = tpl.default_prompt;
          }
        });
      });
    },

    renderPromptSelect: function () {
      var sel = $("wizard-prompt-select");
      if (!sel) return;
      var custom = sel.querySelector('option[value=""]');
      sel.innerHTML = "";
      if (custom) sel.appendChild(custom);
      else {
        var opt = global.document.createElement("option");
        opt.value = "";
        opt.textContent = t("wizard.prompt.custom", "Custom prompt");
        sel.appendChild(opt);
      }
      this.prompts.forEach(function (p) {
        var o = global.document.createElement("option");
        o.value = p.id;
        o.textContent = p.name;
        sel.appendChild(o);
      });
    },

    onPromptSelect: function () {
      var sel = $("wizard-prompt-select");
      var area = $("wizard-prompt-text");
      if (!sel || !area) return;
      var pid = sel.value;
      if (!pid) return;
      var row = this.prompts.find(function (p) {
        return p.id === pid;
      });
      if (row && row.content) area.value = row.content;
    },

    addFiles: function (fileList) {
      if (!fileList) return;
      var i;
      for (i = 0; i < fileList.length; i += 1) {
        this.pendingFiles.push(fileList[i]);
      }
      this.renderFileList();
    },

    renderFileList: function () {
      var list = $("wizard-file-list");
      if (!list) return;
      list.innerHTML = this.pendingFiles
        .map(function (f, idx) {
          return (
            '<li><span>' +
            escHtml(f.name) +
            '</span> <button type="button" class="btn btn-sm btn-outline" data-remove-idx="' +
            idx +
            '">×</button></li>'
          );
        })
        .join("");
      var self = this;
      list.querySelectorAll("[data-remove-idx]").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          var idx = parseInt(btn.getAttribute("data-remove-idx"), 10);
          self.pendingFiles.splice(idx, 1);
          self.renderFileList();
        });
      });
    },

    renderStep: function () {
      var steps = [1, 2, 3];
      steps.forEach(function (n) {
        var el = $("wizard-step-" + (n === 1 ? "type" : n === 2 ? "sources" : "prompt"));
        if (el) el.hidden = n !== this.step;
      }, this);
      var label = $("wizard-step-label");
      if (label) {
        var keys = [
          ["wizard.step.type", "Step 1 of 3 — Choose a template"],
          ["wizard.step.sources", "Step 2 of 3 — Add sources"],
          ["wizard.step.prompt", "Step 3 of 3 — Set your prompt"],
        ];
        var pair = keys[this.step - 1] || keys[0];
        label.textContent = t(pair[0], pair[1]);
      }
      var back = $("wizard-back-btn");
      var next = $("wizard-next-btn");
      var create = $("wizard-create-btn");
      if (back) back.hidden = this.step <= 1;
      if (next) next.hidden = this.step >= 3;
      if (create) create.hidden = this.step < 3;
    },

    nextStep: function () {
      if (this.step < 3) {
        this.step += 1;
        if (this.step === 3) {
          var tpl = this.templates.find(function (x) {
            return x.id === this.selectedTemplateId;
          }, this);
          var titleInput = $("wizard-title-input");
          var promptArea = $("wizard-prompt-text");
          if (titleInput && !titleInput.value.trim() && tpl) titleInput.value = tpl.name;
          if (promptArea && tpl && tpl.default_prompt && !promptArea.value.trim()) {
            promptArea.value = tpl.default_prompt;
          }
        }
        this.renderStep();
      }
    },

    prevStep: function () {
      if (this.step > 1) {
        this.step -= 1;
        this.renderStep();
      }
    },

    uploadFiles: function (projectId) {
      if (!this.pendingFiles.length) return Promise.resolve();
      var uploads = this.pendingFiles.map(function (file) {
        var fd = new FormData();
        fd.append("file", file);
        return fetch("/api/projects/" + encodeURIComponent(projectId) + "/substrate/upload", {
          method: "POST",
          credentials: "same-origin",
          body: fd,
        });
      });
      return Promise.all(uploads);
    },

    finish: function () {
      var titleInput = $("wizard-title-input");
      var promptArea = $("wizard-prompt-text");
      var sel = $("wizard-prompt-select");
      var title = titleInput ? titleInput.value.trim() : "";
      if (!title) {
        if (titleInput) titleInput.focus();
        return;
      }
      if (
        global.AssureUnsaved &&
        !global.AssureUnsaved.confirmLeave(
          "unsaved.switch_project",
          "You have unsaved changes. Switching projects will lose them. Continue?"
        )
      ) {
        return;
      }
      var createBtn = $("wizard-create-btn");
      if (createBtn) createBtn.disabled = true;
      var payload = {
        title: title,
        template_id: this.selectedTemplateId,
        prompt: promptArea ? promptArea.value.trim() : "",
        prompt_id: sel && sel.value ? sel.value : null,
      };
      var self = this;
      fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(payload),
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (!data.ok) {
            if (global.AssureToast) {
              global.AssureToast.show(data.error || t("projects.create_failed", "Could not create project."), "error");
            }
            if (createBtn) createBtn.disabled = false;
            return;
          }
          return self.uploadFiles(data.id).then(function () {
            self.close();
            if (createBtn) createBtn.disabled = false;
            if (global.AssureProjects && typeof global.AssureProjects.switchTo === "function") {
              global.AssureProjects.switchTo(data.id, data.title, { skipUnsaved: true });
            }
            var intent = $("generate-intent");
            if (intent && data.prompt) intent.value = data.prompt;
            if (global.AssureProjectFileManager && typeof global.AssureProjectFileManager.hydrate === "function") {
              global.AssureProjectFileManager.hydrate(data.id).catch(function () {});
            }
            if (global.AssureFirstCompileCoachmark && typeof global.AssureFirstCompileCoachmark.check === "function") {
              global.AssureFirstCompileCoachmark.check(true);
            }
          });
        })
        .catch(function () {
          if (createBtn) createBtn.disabled = false;
        });
    },
  };

  global.AssureNewProjectWizard = Wizard;
  global.document.addEventListener("DOMContentLoaded", function () {
    Wizard.init();
  });
})(window);
