/**
 * JDF ↔ TipTap mapping and canvas editor.
 * Depends on window.AssureTiptap from tiptap.bundle.js.
 * Falls back silently if the bundle is missing — jdf_canvas keeps DOM render.
 */
(function (global) {
  "use strict";

  function purifyHtml(html) {
    if (global.DOMPurify && typeof global.DOMPurify.sanitize === "function") {
      return global.DOMPurify.sanitize(String(html || ""), {
        ALLOWED_TAGS: ["p", "span", "br", "strong", "em", "u", "a", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr", "td", "th", "img"],
        ALLOWED_ATTR: ["href", "title", "rel", "src", "alt", "class"],
      });
    }
    return String(html || "");
  }

  function purifyTiptapDoc(node) {
    if (!node || typeof node !== "object") return node;
    if (Array.isArray(node)) return node.map(purifyTiptapDoc);
    var copy = {};
    Object.keys(node).forEach(function (key) {
      var val = node[key];
      if (key === "text" && typeof val === "string") copy[key] = purifyHtml(val);
      else if (val && typeof val === "object") copy[key] = purifyTiptapDoc(val);
      else copy[key] = val;
    });
    return copy;
  }

  function newNodeId(prefix) {
    return (prefix || "n") + "-" + Math.random().toString(36).slice(2, 10);
  }

  function textContent(node) {
    if (!node) return "";
    if (node.type === "text") return node.text || "";
    return (node.content || []).map(textContent).join("");
  }

  function paragraphJson(text) {
    if (!text) return { type: "jdfParagraph", content: [] };
    return {
      type: "jdfParagraph",
      content: [{ type: "text", text: String(text) }],
    };
  }

  function gutterForNode(jdfNode, isPreview) {
    if (isPreview) return "unverified";
    var canvas = global.__assureJdf;
    if (canvas && typeof canvas.computeNodeStatus === "function") {
      return canvas.computeNodeStatus(jdfNode);
    }
    var ann = (jdfNode && jdfNode.annotations) || {};
    var hasRedhat = Array.isArray(ann.redhat) && ann.redhat.length > 0;
    var hasZ3Fail =
      Array.isArray(ann.z3) &&
      ann.z3.some(function (z) {
        return z.status === "violation";
      });
    if (hasZ3Fail) return "error";
    if (hasRedhat) return "warning";
    return "verified";
  }

  function jdfToTiptap(tree, isPreview) {
    tree = tree || {};
    var title = (tree.meta && tree.meta.title) || "Untitled";
    var content = [
      {
        type: "heading",
        attrs: { level: 2 },
        content: title ? [{ type: "text", text: title }] : [],
      },
    ];
    (tree.body || []).forEach(function (section) {
      content.push({
        type: "heading",
        attrs: { level: 3, sectionId: section.id || "" },
        content: section.title ? [{ type: "text", text: section.title }] : [],
      });
      (section.children || []).forEach(function (node) {
        var ntype = node.type || "paragraph";
        if (ntype === "callout") {
          content.push({
            type: "jdfCallout",
            attrs: {
              nodeId: node.id || "",
              gutter: gutterForNode(node, isPreview),
              calloutTitle: node.title || "",
            },
            content: node.content ? [{ type: "text", text: node.content }] : [],
          });
          return;
        }
        if (ntype === "table") {
          content.push({
            type: "jdfTable",
            attrs: {
              nodeId: node.id || "",
              gutter: gutterForNode(node, isPreview),
              payload: JSON.stringify(node),
            },
          });
          return;
        }
        var para = paragraphJson(node.content || "");
        para.attrs = {
          nodeId: node.id || "",
          gutter: gutterForNode(node, isPreview),
          cacheHit: !!(node.meta && node.meta.cache_hit),
          metaJson: JSON.stringify(node.meta || {}),
        };
        content.push(para);
      });
    });
    return { type: "doc", content: content };
  }

  function lookupOld(tree, nodeId) {
    if (!nodeId || !tree) return null;
    var found = null;
    (tree.body || []).forEach(function (sec) {
      (sec.children || []).forEach(function (n) {
        if (n.id === nodeId) found = n;
      });
    });
    return found;
  }

  function lookupSection(tree, sectionId) {
    if (!sectionId || !tree) return null;
    var found = null;
    (tree.body || []).forEach(function (sec) {
      if (sec.id === sectionId) found = sec;
    });
    return found;
  }

  function tiptapToJdf(doc, previousTree) {
    previousTree = previousTree || { body: [], meta: {}, truth_ledger: {} };
    var meta = Object.assign({}, previousTree.meta || {});
    var body = [];
    var current = null;

    function ensureSection() {
      if (current) return;
      current = {
        type: "section",
        id: newNodeId("sec"),
        title: "Section",
        children: [],
        annotations: { redhat: [], z3: [] },
        meta: {},
      };
      body.push(current);
    }

    (doc.content || []).forEach(function (node) {
      if (node.type === "heading" && node.attrs && node.attrs.level === 2) {
        meta.title = textContent(node) || "Untitled";
        return;
      }
      if (node.type === "heading") {
        var sid = (node.attrs && node.attrs.sectionId) || "";
        var prevSec = lookupSection(previousTree, sid);
        current = {
          type: "section",
          id: sid || newNodeId("sec"),
          title: textContent(node) || "Section",
          children: [],
          annotations: (prevSec && prevSec.annotations) || { redhat: [], z3: [] },
          meta: Object.assign({}, (prevSec && prevSec.meta) || {}),
        };
        body.push(current);
        return;
      }
      ensureSection();
      if (node.type === "jdfTable") {
        var payload = {};
        try {
          payload = JSON.parse((node.attrs && node.attrs.payload) || "{}");
        } catch (_) {
          payload = {};
        }
        if (!payload.id) payload.id = (node.attrs && node.attrs.nodeId) || newNodeId("tbl");
        current.children.push(payload);
        return;
      }
      var nodeId = (node.attrs && node.attrs.nodeId) || newNodeId("p");
      var old = lookupOld(previousTree, nodeId);
      var isCallout = node.type === "jdfCallout";
      var metaFromAttr = {};
      try {
        metaFromAttr = JSON.parse((node.attrs && node.attrs.metaJson) || "{}") || {};
      } catch (_) {
        metaFromAttr = {};
      }
      var child;
      if (isCallout) {
        child = {
          type: "callout",
          id: nodeId,
          variant: (node.attrs && node.attrs.calloutVariant) || (old && old.variant) || "insight",
          title: (node.attrs && node.attrs.calloutTitle) || (old && old.title) || "",
          content: textContent(node),
          annotations: (old && old.annotations) || { redhat: [], z3: [] },
          meta: Object.assign({}, (old && old.meta) || {}, metaFromAttr),
        };
      } else {
        child = {
          type: "paragraph",
          id: nodeId,
          content: textContent(node),
          entities_referenced: (old && old.entities_referenced) || [],
          annotations: (old && old.annotations) || { redhat: [], z3: [] },
          meta: Object.assign({}, (old && old.meta) || {}, metaFromAttr),
          provenance: (old && old.provenance) || [],
        };
      }
      current.children.push(child);
    });

    return {
      document_id: previousTree.document_id,
      meta: meta,
      truth_ledger: previousTree.truth_ledger || {},
      body: body,
    };
  }

  function makeNodeView(kind) {
    return function (props) {
      var node = props.node;
      var article = document.createElement("article");
      article.className = "jdf-node jdf-node-" + (kind === "callout" ? "callout" : "paragraph");
      article.dataset.nodeId = node.attrs.nodeId || "";
      var gutter = document.createElement("div");
      gutter.className = "verification-gutter " + (node.attrs.gutter || "unverified");
      gutter.setAttribute("data-node-id", node.attrs.nodeId || "");
      var body = document.createElement("div");
      body.className = "jdf-node-body";
      if (node.attrs.cacheHit) {
        var cacheBadge = document.createElement("span");
        cacheBadge.className =
          "inline-flex items-center text-xs text-yellow-600 bg-yellow-50 px-1.5 py-0.5 rounded-full ml-2 assure-cache-badge";
        cacheBadge.textContent = "⚡ Cached";
        cacheBadge.setAttribute("title", "Loaded from memory");
        article.appendChild(gutter);
        article.appendChild(cacheBadge);
        article.appendChild(body);
      } else if (kind === "callout" && node.attrs.calloutTitle) {
        var cap = document.createElement("strong");
        cap.className = "jdf-callout-title";
        cap.textContent = node.attrs.calloutTitle;
        article.appendChild(gutter);
        article.appendChild(cap);
        article.appendChild(body);
      } else {
        article.appendChild(gutter);
        article.appendChild(body);
      }
      article.addEventListener("click", function (e) {
        if (e.target.closest && e.target.closest(".lock-glyph-host")) return;
        var canvas = global.__assureJdf;
        if (!canvas || (canvas.rootEl && canvas.rootEl.classList.contains("is-draft-preview"))) return;
        var id = article.dataset.nodeId;
        if (!id) return;
        if (global.AssureSurgicalClick && typeof global.AssureSurgicalClick.open === "function") {
          e.preventDefault();
          e.stopPropagation();
          global.AssureSurgicalClick.open(id, e.clientX, e.clientY);
          return;
        }
        if (typeof canvas.selectNodeForRefine === "function") {
          canvas.selectNodeForRefine(id, { toast: false, skipRender: true });
        }
      });
      article.addEventListener("contextmenu", function (e) {
        var canvas = global.__assureJdf;
        if (!canvas || (canvas.rootEl && canvas.rootEl.classList.contains("is-draft-preview"))) return;
        e.preventDefault();
        var id = article.dataset.nodeId;
        if (id && typeof canvas.openNodeMenu === "function") {
          canvas.openNodeMenu(id, e.clientX, e.clientY);
        }
      });
      return {
        dom: article,
        contentDOM: body,
        update: function (updated) {
          if (updated.type !== node.type) return false;
          article.dataset.nodeId = updated.attrs.nodeId || "";
          gutter.className = "verification-gutter " + (updated.attrs.gutter || "unverified");
          gutter.setAttribute("data-node-id", updated.attrs.nodeId || "");
          var existingBadge = article.querySelector(".assure-cache-badge");
          if (updated.attrs.cacheHit && !existingBadge) {
            var cacheBadge = document.createElement("span");
            cacheBadge.className =
              "inline-flex items-center text-xs text-yellow-600 bg-yellow-50 px-1.5 py-0.5 rounded-full ml-2 assure-cache-badge";
            cacheBadge.textContent = "⚡ Cached";
            cacheBadge.setAttribute("title", "Loaded from memory");
            article.insertBefore(cacheBadge, body);
          } else if (!updated.attrs.cacheHit && existingBadge) {
            existingBadge.remove();
          }
          node = updated;
          return true;
        },
      };
    };
  }

  function defineExtensions(T) {
    var JdfParagraph = T.Node.create({
      name: "jdfParagraph",
      group: "block",
      content: "inline*",
      addAttributes: function () {
        return {
          nodeId: {
            default: "",
            parseHTML: function (el) {
              return el.getAttribute("data-node-id") || "";
            },
            renderHTML: function (attrs) {
              return attrs.nodeId ? { "data-node-id": attrs.nodeId } : {};
            },
          },
          gutter: { default: "unverified" },
          cacheHit: { default: false },
          metaJson: {
            default: "{}",
            parseHTML: function (el) {
              return el.getAttribute("data-meta-json") || "{}";
            },
            renderHTML: function (attrs) {
              return attrs.metaJson ? { "data-meta-json": attrs.metaJson } : {};
            },
          },
        };
      },
      parseHTML: function () {
        return [{ tag: "article.jdf-node-paragraph" }];
      },
      renderHTML: function (_ref) {
        return ["article", T.mergeAttributes(_ref.HTMLAttributes, { class: "jdf-node jdf-node-paragraph" }), 0];
      },
      addNodeView: function () {
        return makeNodeView("paragraph");
      },
    });

    var JdfCallout = T.Node.create({
      name: "jdfCallout",
      group: "block",
      content: "inline*",
      addAttributes: function () {
        return {
          nodeId: { default: "" },
          gutter: { default: "unverified" },
          calloutTitle: { default: "" },
        };
      },
      parseHTML: function () {
        return [{ tag: "article.jdf-node-callout" }];
      },
      renderHTML: function (_ref) {
        return ["article", T.mergeAttributes(_ref.HTMLAttributes, { class: "jdf-node jdf-node-callout" }), 0];
      },
      addNodeView: function () {
        return makeNodeView("callout");
      },
    });

    var JdfTable = T.Node.create({
      name: "jdfTable",
      group: "block",
      atom: true,
      addAttributes: function () {
        return {
          nodeId: { default: "" },
          gutter: { default: "unverified" },
          payload: { default: "{}" },
        };
      },
      parseHTML: function () {
        return [{ tag: "article.jdf-node-table" }];
      },
      renderHTML: function (_ref) {
        return ["article", T.mergeAttributes(_ref.HTMLAttributes, { class: "jdf-node jdf-node-table" })];
      },
      addNodeView: function () {
        return function (props) {
          var article = document.createElement("article");
          article.className = "jdf-node jdf-node-table";
          article.dataset.nodeId = props.node.attrs.nodeId || "";
          var gutter = document.createElement("div");
          gutter.className = "verification-gutter " + (props.node.attrs.gutter || "unverified");
          gutter.setAttribute("data-node-id", props.node.attrs.nodeId || "");
          var body = document.createElement("div");
          body.className = "jdf-node-body";
          body.textContent = "Table";
          try {
            var payload = JSON.parse(props.node.attrs.payload || "{}");
            if (payload.caption) body.textContent = payload.caption;
            else if (payload.content) body.textContent = String(payload.content).slice(0, 240);
          } catch (_) {}
          article.appendChild(gutter);
          article.appendChild(body);
          return { dom: article };
        };
      },
    });

    var confidencePluginKey = new T.PluginKey("confidenceDecorations");

    var ConfidenceDecorations = T.Extension.create({
      name: "confidenceDecorations",
      addProseMirrorPlugins: function () {
        var key = confidencePluginKey;
        return [
          new T.Plugin({
            key: key,
            state: {
              init: function (_cfg, state) {
                return buildConfidenceSet(
                  T,
                  state.doc,
                  global._assureConfidenceSpans || [],
                  confidenceOverlayEnabled
                );
              },
              apply: function (tr, old, _oldState, newState) {
                if (tr.docChanged || tr.getMeta(key)) {
                  return buildConfidenceSet(
                    T,
                    newState.doc,
                    global._assureConfidenceSpans || [],
                    confidenceOverlayEnabled
                  );
                }
                return old.map(tr.mapping, tr.doc);
              },
            },
            props: {
              decorations: function (state) {
                return key.getState(state);
              },
            },
          }),
        ];
      },
    });

    var LockDecorations = T.Extension.create({
      name: "lockDecorations",
      addProseMirrorPlugins: function () {
        var key = new T.PluginKey("lockDecorations");
        return [
          new T.Plugin({
            key: key,
            state: {
              init: function (_cfg, state) {
                return buildLockSet(T, state.doc);
              },
              apply: function (tr, old) {
                if (!tr.docChanged) return old;
                return buildLockSet(T, tr.doc);
              },
            },
            props: {
              decorations: function (state) {
                return key.getState(state);
              },
            },
          }),
        ];
      },
    });

    var HeadingId = T.Extension.create({
      name: "headingId",
      addGlobalAttributes: function () {
        return [
          {
            types: ["heading"],
            attributes: {
              sectionId: {
                default: "",
                parseHTML: function (el) {
                  return el.getAttribute("data-section-id") || "";
                },
                renderHTML: function (attrs) {
                  return attrs.sectionId ? { "data-section-id": attrs.sectionId } : {};
                },
              },
            },
          },
        ];
      },
    });

    return {
      JdfParagraph: JdfParagraph,
      JdfCallout: JdfCallout,
      JdfTable: JdfTable,
      LockDecorations: LockDecorations,
      ConfidenceDecorations: ConfidenceDecorations,
      HeadingId: HeadingId,
      confidencePluginKey: confidencePluginKey,
    };
  }

  function classForConfidenceScore(score) {
    var highlighter = global.AssureConfidenceHighlighter;
    if (highlighter && typeof highlighter.classForScore === "function") {
      return highlighter.classForScore(score);
    }
    var n = Number(score);
    if (!Number.isFinite(n)) return "bg-yellow-200";
    if (n > 0.8) return "bg-green-200";
    if (n >= 0.4) return "bg-yellow-200";
    return "bg-red-200";
  }

  var confidenceOverlayEnabled = true;
  try {
    if (global.localStorage && global.localStorage.getItem("assure_confidence_overlay") === "0") {
      confidenceOverlayEnabled = false;
    }
  } catch (_) {}

  function normalizeConfidenceSpan(span) {
    var start = Number(span.startChar != null ? span.startChar : span.start);
    var end = Number(span.endChar != null ? span.endChar : span.end);
    var score = Number(span.score);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
    return {
      start: start,
      end: end,
      score: Number.isFinite(score) ? score : 0.55,
      nodeId: span.nodeId || span.node_id || "",
    };
  }

  function buildConfidenceSet(T, doc, spans, enabled) {
    if (!enabled || !spans || !spans.length) return T.DecorationSet.empty;
    var byNode = {};
    var globalSpans = [];
    (spans || []).forEach(function (raw) {
      var span = normalizeConfidenceSpan(raw);
      if (!span) return;
      if (span.nodeId) {
        if (!byNode[span.nodeId]) byNode[span.nodeId] = [];
        byNode[span.nodeId].push(span);
      } else {
        globalSpans.push(span);
      }
    });
    var decorations = [];
    doc.descendants(function (node, pos) {
      if (node.type.name !== "jdfParagraph" && node.type.name !== "jdfCallout") return;
      var nid = (node.attrs && node.attrs.nodeId) || "";
      var nodeSpans = nid && byNode[nid] ? byNode[nid] : globalSpans;
      if (!nodeSpans.length) return;
      var blockStart = pos + 1;
      var textLen = node.textContent.length;
      nodeSpans.forEach(function (span) {
        var from = blockStart + Math.max(0, Math.min(textLen, span.start));
        var to = blockStart + Math.max(from - blockStart, Math.min(textLen, span.end));
        if (to > from) {
          decorations.push(
            T.Decoration.inline(from, to, {
              class: classForConfidenceScore(span.score) + " assure-confidence-mark",
            })
          );
        }
      });
    });
    return decorations.length ? T.DecorationSet.create(doc, decorations) : T.DecorationSet.empty;
  }

  function buildLockSet(T, doc) {
    var canvas = global.__assureJdf;
    var ledger = (canvas && canvas.tree && canvas.tree.truth_ledger) || {};
    var keys = Object.keys(ledger).sort(function (a, b) {
      return String(ledger[b]).length - String(ledger[a]).length;
    });
    var decorations = [];
    if (!keys.length) return T.DecorationSet.empty;
    doc.descendants(function (node, pos) {
      if (!node.isText) return;
      var text = node.text || "";
      keys.forEach(function (k) {
        var needle = String(ledger[k]);
        if (!needle) return;
        var idx = 0;
        while (idx < text.length) {
          var found = text.indexOf(needle, idx);
          if (found < 0) break;
          var from = pos + found;
          var to = from + needle.length;
          decorations.push(
            T.Decoration.inline(from, to, {
              class: "truth-pill inline-citation interactive-element truth-pass lock-glyph-host",
              "data-lock-key": k,
              title: "\uD83D\uDD12 " + k + " = " + needle,
            })
          );
          decorations.push(
            T.Decoration.widget(to, function () {
              var span = document.createElement("span");
              span.className = "lock-glyph";
              span.setAttribute("aria-hidden", "true");
              span.textContent = "\uD83D\uDD12";
              return span;
            })
          );
          idx = found + needle.length;
        }
      });
    });
    return T.DecorationSet.create(doc, decorations);
  }

  var editor = null;
  var saveTimer = null;
  var confidencePluginKeyRef = null;

  function applyConfidenceToTipTap() {
    if (!editor || editor.isDestroyed || !confidencePluginKeyRef) return;
    try {
      var tr = editor.state.tr.setMeta(confidencePluginKeyRef, { refresh: true });
      editor.view.dispatch(tr);
    } catch (_) {}
  }

  function destroy() {
    if (saveTimer) {
      clearTimeout(saveTimer);
      saveTimer = null;
    }
    if (editor) {
      editor.destroy();
      editor = null;
    }
  }

  function mount(opts) {
    var T = global.AssureTiptap;
    if (!T || !T.Editor) return null;
    opts = opts || {};
    var rootEl = opts.rootEl;
    var tree = opts.tree;
    var canvas = opts.canvas;
    var editable = !!opts.editable;
    var isPreview = !editable;
    if (!rootEl) return null;

    var json = jdfToTiptap(tree, isPreview);

    if (editor) {
      canvas._tiptapSyncing = true;
      editor.setEditable(editable);
      editor.commands.setContent(purifyTiptapDoc(json), false);
      canvas._tiptapSyncing = false;
      rootEl.classList.toggle("is-tiptap", true);
      applyConfidenceToTipTap();
      return editor;
    }

    var ext = defineExtensions(T);
    confidencePluginKeyRef = ext.confidencePluginKey;
    rootEl.innerHTML = "";
    var host = document.createElement("div");
    host.className = "jdf-tiptap-host";
    rootEl.appendChild(host);

    try {
      editor = new T.Editor({
      element: host,
      editable: editable,
      extensions: [
        T.StarterKit.configure({
          paragraph: false,
          heading: { levels: [2, 3] },
          bulletList: false,
          orderedList: false,
          listItem: false,
          codeBlock: false,
          blockquote: false,
          horizontalRule: false,
        }),
        ext.HeadingId,
        ext.JdfParagraph,
        ext.JdfCallout,
        ext.JdfTable,
        ext.LockDecorations,
        ext.ConfidenceDecorations,
      ],
      content: purifyTiptapDoc(json),
      editorProps: {
        attributes: { class: "jdf-tiptap-doc", role: "tree" },
      },
      onUpdate: function () {
        if (!canvas || canvas._tiptapSyncing) return;
        if (rootEl.classList.contains("is-draft-preview")) return;
        var next = tiptapToJdf(editor.getJSON(), canvas.tree);
        canvas.tree.meta = next.meta;
        canvas.tree.body = next.body;
        if (typeof canvas._setDirty === "function") canvas._setDirty(true);
        if (saveTimer) clearTimeout(saveTimer);
        saveTimer = setTimeout(function () {
          if (typeof canvas.saveDocument === "function") {
            canvas.saveDocument("MANUAL_TOUCHUP");
          }
        }, 900);
      },
    });
      rootEl.classList.add("is-tiptap");
      if (typeof global.initializeEditorSyncBridge === "function") {
        global.initializeEditorSyncBridge(editor);
      }
      if (typeof global.initializeAstSerializer === "function") {
        global.initializeAstSerializer(editor);
      }
      applyConfidenceToTipTap();
      return editor;
    } catch (err) {
      editor = null;
      rootEl.classList.remove("is-tiptap");
      throw err;
    }
  }

  global.AssureTiptapEditor = {
    mount: mount,
    destroy: destroy,
    jdfToTiptap: jdfToTiptap,
    tiptapToJdf: tiptapToJdf,
    applyConfidenceToTipTap: applyConfidenceToTipTap,
    setConfidenceSpans: function (spans) {
      global._assureConfidenceSpans = Array.isArray(spans) ? spans : [];
      applyConfidenceToTipTap();
    },
    setOverlayEnabled: function (on) {
      confidenceOverlayEnabled = !!on;
      applyConfidenceToTipTap();
    },
    getEditor: function () {
      return editor;
    },
    getSelectedTextRange: function () {
      if (!editor) {
        return { from: 0, to: 0, text: "", empty: true };
      }
      var sel = editor.state.selection;
      var from = sel.from;
      var to = sel.to;
      var empty = !!sel.empty || from === to;
      var text = empty ? "" : editor.state.doc.textBetween(from, to, "\n");
      return { from: from, to: to, text: text, empty: empty };
    },
    insertAtCursor: function (text) {
      var raw = text == null ? "" : String(text);
      if (!raw) return false;
      if (!editor || editor.isDestroyed) return false;
      var nodes = raw.split(/\n/).map(function (line) {
        var node = { type: "jdfParagraph", attrs: { nodeId: "", gutter: "unverified" }, content: [] };
        if (line) node.content = [{ type: "text", text: line }];
        return node;
      });
      try {
        var before = editor.getText();
        editor.chain().focus().insertContent(nodes).run();
        return editor.getText() !== before;
      } catch (_) {
        return false;
      }
    },
  };
})(typeof window !== "undefined" ? window : this);
