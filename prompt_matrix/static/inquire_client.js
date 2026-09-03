(function (global) {
  "use strict";

  function parseSseChunk(buffer) {
    var events = [];
    var parts = buffer.split("\n\n");
    var remainder = parts.pop() || "";
    parts.forEach(function (block) {
      if (!block.trim()) return;
      var eventName = "message";
      var dataLine = "";
      block.split("\n").forEach(function (line) {
        if (line.indexOf("event:") === 0) eventName = line.slice(6).trim();
        if (line.indexOf("data:") === 0) dataLine += line.slice(5).trim();
      });
      if (!dataLine) return;
      try {
        events.push({ event: eventName, data: JSON.parse(dataLine) });
      } catch (_) {
        events.push({ event: eventName, data: { raw: dataLine } });
      }
    });
    return { events: events, remainder: remainder };
  }

  function streamInquire(projectId, payload, handlers) {
    var controller = new AbortController();
    var url = "/api/projects/" + encodeURIComponent(projectId) + "/inquire/stream";
    var finished = false;

    fetch(url, {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, (handlers && handlers.headers) || {}),
      body: JSON.stringify(payload || {}),
      signal: controller.signal,
    })
      .then(function (res) {
        if (!res.ok || !res.body) {
          throw new Error("Stream failed (" + res.status + ")");
        }
        var reader = res.body.getReader();
        var decoder = new TextDecoder();
        var buffer = "";

        function pump() {
          return reader.read().then(function (result) {
            if (result.done) {
              if (!finished && handlers && handlers.onComplete) handlers.onComplete({ ok: true });
              return;
            }
            buffer += decoder.decode(result.value, { stream: true });
            var parsed = parseSseChunk(buffer);
            buffer = parsed.remainder;
            parsed.events.forEach(function (frame) {
              if (handlers && typeof handlers["on" + frame.event.replace(/_/g, "")] === "function") {
                handlers["on" + frame.event.replace(/_/g, "")](frame.data);
              } else if (handlers && typeof handlers.onEvent === "function") {
                handlers.onEvent(frame.event, frame.data);
              }
              if (frame.event === "complete") {
                finished = true;
                if (handlers && handlers.onComplete) handlers.onComplete(frame.data);
              }
            });
            return pump();
          });
        }
        return pump();
      })
      .catch(function (err) {
        if (handlers && handlers.onError) handlers.onError(err);
      });

    return {
      abort: function () {
        controller.abort();
      },
    };
  }

  function flattenNodes(tree) {
    var nodes = [];
    (tree.body || []).forEach(function (section) {
      (section.children || []).forEach(function (child) {
        nodes.push({ section: section, node: child });
      });
    });
    return nodes;
  }

  function renderTree(container, tree, opts) {
    if (!container) return;
    container.innerHTML = "";
    (tree.body || []).forEach(function (section) {
      var sec = document.createElement("section");
      sec.className = "jdf-section";
      var h = document.createElement("h3");
      h.className = "jdf-section-title";
      h.textContent = section.title || "Section";
      sec.appendChild(h);
      (section.children || []).forEach(function (node) {
        var el = document.createElement("article");
        el.className = "jdf-node jdf-node-" + (node.type || "paragraph");
        el.dataset.nodeId = node.id;
        if (opts && opts.selectedId === node.id) el.classList.add("is-selected");
        if (node.type === "callout") {
          el.innerHTML =
            '<span class="jdf-callout-label">' +
            (node.variant || "callout") +
            "</span><strong>" +
            (node.title || "") +
            "</strong><p>" +
            (node.content || "") +
            "</p>";
        } else if (node.type === "table") {
          el.textContent = (node.caption || "Table") + " (" + ((node.rows || []).length) + " rows)";
        } else {
          el.textContent = node.content || "";
        }
        el.addEventListener("mouseenter", function () {
          el.classList.add("is-hover");
        });
        el.addEventListener("mouseleave", function () {
          el.classList.remove("is-hover");
        });
        el.addEventListener("click", function () {
          if (opts && typeof opts.onSelect === "function") opts.onSelect(node.id, node);
        });
        sec.appendChild(el);
      });
      container.appendChild(sec);
    });
  }

  function renderPreview(container, tree, liveText) {
    if (!container) return;
    var lines = [];
    if (liveText) lines.push(liveText);
    flattenNodes(tree).forEach(function (item) {
      var n = item.node;
      if (n.type === "paragraph") lines.push(n.content || "");
      if (n.type === "callout") lines.push("[" + (n.variant || "callout") + "] " + (n.content || ""));
    });
    container.textContent = lines.join("\n\n");
  }

  function loadDocument(projectId) {
    return fetch("/api/projects/" + encodeURIComponent(projectId) + "/jdf")
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        return (data && data.document) || { document_id: "doc-" + projectId, body: [], truth_ledger: {} };
      });
  }

  function saveDocument(projectId, document, meta) {
    return fetch("/api/projects/" + encodeURIComponent(projectId) + "/jdf", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        Object.assign(
          {
            document: document,
            mutation_type: "manual_save",
          },
          meta || {}
        )
      ),
    }).then(function (res) {
      return res.json();
    });
  }

  function pickEl(primaryId, fallbackId) {
    return document.getElementById(primaryId) || (fallbackId ? document.getElementById(fallbackId) : null);
  }

  function initWorkbench(options) {
    var projectId = (options && options.projectId) || global.__ASSURE_PROJECT_ID__ || "default";
    var tree = (options && options.document) || global.__INITIAL_JDF__ || { document_id: "doc-" + projectId, body: [], truth_ledger: {} };
    var selectedId = null;
    var liveText = "";
    var activeStream = null;

    var treeEl = pickEl("jdf-render-target", "jdf-tree");
    var previewEl = document.getElementById("jdf-live-preview");
    var intentEl = pickEl("inquiry-input", "jdf-intent");
    var statusEl = document.getElementById("jdf-stream-status");

    function paint() {
      renderTree(treeEl, tree, {
        selectedId: selectedId,
        onSelect: function (id) {
          selectedId = id;
          paint();
        },
      });
      renderPreview(previewEl, tree, liveText);
    }

    paint();

    var inquireBtn = pickEl("btn-inquire", "jdf-inquire");
    if (inquireBtn) {
      inquireBtn.addEventListener("click", function () {
        if (activeStream) activeStream.abort();
        liveText = "";
        if (statusEl) statusEl.textContent = "Streaming…";
        var redhatToggle = pickEl("toggle-redhat", "jdf-redhat");
        activeStream = streamInquire(
          projectId,
          {
            user_intent: (intentEl && intentEl.value) || "Revise document",
            target_node_id: selectedId,
            run_redhat: !!(redhatToggle && redhatToggle.checked),
            document: tree,
          },
          {
            onstatus: function (data) {
              if (statusEl) statusEl.textContent = data.stage || "working";
            },
            ontoken: function (data) {
              liveText += data.delta || "";
              renderPreview(previewEl, tree, liveText);
            },
            ontruthcheck: function (data) {
              if (statusEl) statusEl.textContent = "Truth: " + (data.status || "");
            },
            onjdfnodeready: function (data) {
              if (data && data.node && selectedId) {
                flattenNodes(tree).forEach(function (item) {
                  if (item.node.id === selectedId) {
                    Object.assign(item.node, data.node);
                  }
                });
                liveText = "";
                paint();
              }
            },
            oncomplete: function (data) {
              if (statusEl) statusEl.textContent = data && data.ok ? "Complete" : "Failed";
              activeStream = null;
              saveDocument(projectId, tree, { mutation_type: "post_inquire", target_node_id: selectedId });
            },
            onError: function (err) {
              if (statusEl) statusEl.textContent = String(err);
              activeStream = null;
            },
          }
        );
      });
    }

    var saveBtn = document.getElementById("jdf-save");
    if (saveBtn) {
      saveBtn.addEventListener("click", function () {
        saveDocument(projectId, tree, { mutation_type: "manual_save", target_node_id: selectedId }).then(function () {
          if (statusEl) statusEl.textContent = "Saved";
        });
      });
    }

    if (!global.__INITIAL_JDF__ || !(global.__INITIAL_JDF__.body || []).length) {
      loadDocument(projectId).then(function (doc) {
        tree = doc;
        paint();
      });
    }

    return {
      getTree: function () {
        return tree;
      },
      setTree: function (next) {
        tree = next;
        paint();
      },
      abort: function () {
        if (activeStream) activeStream.abort();
      },
    };
  }

  global.AssureInquire = {
    streamInquire: streamInquire,
    renderTree: renderTree,
    renderPreview: renderPreview,
    loadDocument: loadDocument,
    saveDocument: saveDocument,
    initWorkbench: initWorkbench,
  };
})(window);
