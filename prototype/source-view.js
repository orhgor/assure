/* Source view — the uploaded document, rendered from its jdf-cli JDF by
 * jdf.js (prototype/vendor/jdfjs, 0.2.5), with the shell's own quiet page
 * nav and zoom, an overlay box for "Show in source", and a selection hook
 * for "Ask about this" / "Compile this selection".
 *
 * A classic script, no framework: the shell (shell.js) is one too. jdf.js is
 * an ES module; index.html imports it and exposes the API as `window.JDFjs`
 * before this file runs (see the `<script type="module">` there).
 *
 * The overlay is ours, not jdf.js's. jdf.js 0.2.5 renders each page in a
 * `.jdfjs-page-wrapper[data-page-index]` whose width/height it sets to the
 * *rendered* box (viewer.ts applyZoom: the `.jdfjs-page` inside is scaled
 * with `transform: scale(zoom)` from its top-left corner and the wrapper is
 * sized to `page × zoom`). A box for a relative bbox [x0,y0,x1,y1] (0–1 of
 * the page) is therefore `left = x0·w, top = y0·h, width = (x1−x0)·w,
 * height = (y1−y0)·h` in the wrapper's own pixels, where w/h are the page's
 * nominal size (`.jdfjs-page` style width / min-height) times the zoom —
 * not the wrapper's offsetHeight, which grows when elements overflow the
 * page. Zoom and container resize change w/h, so the overlay re-lays out
 * from a ResizeObserver on the wrapper. When jdf.js exposes `highlight()` /
 * `data-jdf-id` this module is the only file that changes.
 *
 * Every anchor the backend writes carries `source_span.bbox` relative 0–1 and
 * a 1-based `page` (docs/parsure-ui.md, "Source view"). Element ids are never
 * derived here: the shell asks `…/source.json?text=&page=` and passes on what
 * came back, `[]` included.
 */
(function () {
  "use strict";

  var FLASH_MS = 2400;

  // ---------------------------------------------------------------------
  // Pure: bbox → pixel box. `bbox` is [x0, y0, x1, y1] (or {x0,y0,x1,y1}),
  // relative 0–1 of the page; `rect` is {width, height} of the rendered
  // page in px. Coordinates are clamped to the page and the corners are
  // ordered, so a bbox written x1<x0 still yields a positive box. Returns
  // null for anything that is not four finite numbers.
  // Unit-tested in node by tests/test_source_view_bbox.py.
  // ---------------------------------------------------------------------
  function bboxToPx(bbox, rect) {
    if (!bbox || !rect) return null;
    var b = Array.isArray(bbox)
      ? bbox
      : [bbox.x0, bbox.y0, bbox.x1, bbox.y1];
    if (b.length < 4) return null;
    var nums = [];
    for (var i = 0; i < 4; i++) {
      var v = Number(b[i]);
      if (!isFinite(v)) return null;
      nums.push(Math.max(0, Math.min(1, v)));
    }
    var x0 = Math.min(nums[0], nums[2]), x1 = Math.max(nums[0], nums[2]);
    var y0 = Math.min(nums[1], nums[3]), y1 = Math.max(nums[1], nums[3]);
    var w = Number(rect.width) || 0, h = Number(rect.height) || 0;
    if (w <= 0 || h <= 0) return null;
    return {
      left: x0 * w,
      top: y0 * h,
      width: (x1 - x0) * w,
      height: (y1 - y0) * h,
    };
  }

  // ---------------------------------------------------------------------
  // State: one open view at a time (the shell has one source sheet).
  // ---------------------------------------------------------------------
  var S = {
    container: null,   // the host the shell hands us
    root: null,        // .sv-root we build inside it
    stage: null,       // .sv-stage — jdf.js embeds here
    nav: null,         // .sv-nav — our page/zoom controls
    viewer: null,      // jdf.js JDFViewerInstance
    url: "",
    opts: {},
    pageCount: 0,
    page: 0,           // 0-based current page (jdf.js's onPageChange)
    zoom: 1,
    marks: [],         // [{wrapper, box, layer, bbox, ro, timer}]
    selCbs: [],
    selTimer: null,
    lastSel: "",
    docListeners: false,
    navUntil: 0,       // until when an explicit goToPage outranks the observer
    mode: "jdf",       // "jdf" (jdf.js re-render) | "pdf" (original in an iframe) | "image" (page images + field overlay)
    fieldMarks: [],    // showFields() boxes, kept apart from the one highlight
    io: null,
  };

  function _t(key, fallback) {
    var fn = S.opts && typeof S.opts.t === "function" ? S.opts.t : null;
    if (fn) { try { var v = fn(key, fallback); if (v) return String(v); } catch (_) {} }
    return fallback;
  }
  function _tf(key, fallback, vars) {
    var text = _t(key, fallback);
    Object.keys(vars || {}).forEach(function (name) {
      text = text.split("{" + name + "}").join(String(vars[name]));
    });
    return text;
  }

  function _api() {
    return (typeof window !== "undefined" && window.JDFjs && typeof window.JDFjs.embed === "function") ? window.JDFjs : null;
  }
  // index.html's module tag sets window.JDFjs then fires "jdfjs-ready"; a
  // caller who arrives first waits for it (or gives up after 8 s).
  function _whenReady() {
    if (_api()) return Promise.resolve(_api());
    return new Promise(function (resolve, reject) {
      var done = false;
      function ok() { if (done) return; done = true; clearTimeout(timer); window.removeEventListener("jdfjs-ready", ok); resolve(_api()); }
      var timer = setTimeout(function () {
        if (done) return;
        done = true;
        window.removeEventListener("jdfjs-ready", ok);
        if (_api()) resolve(_api()); else reject(new Error("jdf.js did not load"));
      }, 8000);
      window.addEventListener("jdfjs-ready", ok);
    });
  }

  function _el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function _btn(cls, label, title) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.textContent = label;
    if (title) { b.title = title; b.setAttribute("aria-label", title); }
    return b;
  }

  // ---------------------------------------------------------------------
  // The nav: "Page n of N", ‹ ›, − +. Ours, because jdf.js's toolbar is off
  // (the shell's chrome is quieter than the viewer's).
  // ---------------------------------------------------------------------
  function _buildNav() {
    var nav = _el("div", "sv-nav");
    nav.setAttribute("role", "toolbar");
    nav.setAttribute("aria-label", _t("shell.source_view.nav", "Source pages"));
    var prev = _btn("sv-btn sv-prev", "‹", _t("shell.source_view.prev", "Previous page"));
    var label = _el("span", "sv-page-label");
    label.setAttribute("aria-live", "polite");
    var next = _btn("sv-btn sv-next", "›", _t("shell.source_view.next", "Next page"));
    var zoomOut = _btn("sv-btn sv-zoom-out", "−", _t("shell.source_view.zoom_out", "Zoom out"));
    var zoomIn = _btn("sv-btn sv-zoom-in", "+", _t("shell.source_view.zoom_in", "Zoom in"));
    prev.addEventListener("click", function () { goToPage(S.page); });          // page is 0-based; goToPage takes 1-based
    next.addEventListener("click", function () { goToPage(S.page + 2); });
    zoomOut.addEventListener("click", function () { zoomBy(-0.15); });
    zoomIn.addEventListener("click", function () { zoomBy(0.15); });
    nav.appendChild(prev);
    nav.appendChild(label);
    nav.appendChild(next);
    var sep = _el("span", "sv-nav-sep");
    sep.setAttribute("aria-hidden", "true");
    nav.appendChild(sep);
    nav.appendChild(zoomOut);
    nav.appendChild(zoomIn);
    S.nav = nav;
    return nav;
  }
  function _paintNav() {
    if (!S.nav) return;
    var label = S.nav.querySelector(".sv-page-label");
    if (label) {
      label.textContent = S.pageCount
        ? _tf("shell.source_view.page_of", "Page {n} of {total}", { n: S.page + 1, total: S.pageCount })
        : "";
    }
    var prev = S.nav.querySelector(".sv-prev"), next = S.nav.querySelector(".sv-next");
    if (prev) prev.disabled = S.page <= 0;
    if (next) next.disabled = S.page >= S.pageCount - 1;
  }

  // ---------------------------------------------------------------------
  // open(container, url, opts) → Promise<viewer>
  //   opts: { zoom, t (i18n fn), onPageChange(page1), onLoad(doc), onError(err) }
  // ---------------------------------------------------------------------
  function open(container, url, opts) {
    destroy();
    S.container = typeof container === "string" ? document.querySelector(container) : container;
    if (!S.container) return Promise.reject(new Error("source view: no container"));
    S.url = String(url || "");
    S.opts = opts || {};
    S.mode = "jdf";
    S.zoom = 1;
    S.page = 0;
    S.pageCount = 0;
    while (S.container.firstChild) S.container.removeChild(S.container.firstChild);
    S.root = _el("div", "sv-root");
    S.root.appendChild(_buildNav());
    S.stage = _el("div", "sv-stage");
    S.stage.setAttribute("data-source-url", S.url);
    S.root.appendChild(S.stage);
    S.container.appendChild(S.root);
    _paintNav();
    _bindDocListeners();
    var thisUrl = S.url;
    return _whenReady().then(function (api) {
      if (S.url !== thisUrl || !S.stage) return null;   // closed or reopened meanwhile
      return new Promise(function (resolve, reject) {
        var settled = false;
        // Fit-width, but the reader's −/+ must stick. jdf.js 0.2.5 `fit:
        // "fit-width"` recomputes the zoom on every ResizeObserver tick
        // (viewer.ts applyFit), and a wider page changes the pages box's
        // scroll geometry, which fires the observer — so a user zoom was
        // undone within a frame (smoke, 2026-09-28). `fit: "manual"` with a
        // requested zoom above any fit caps to `containerWidth / pageWidth`
        // until setZoom() is called, then keeps the user's value: fit-width
        // on open and on host resize, manual after −/+.
        api.embed(S.stage, thisUrl, {
          zoom: 3,
          fit: "manual",
          toolbar: false,
          sidebar: false,
          darkMode: "light",
          width: "100%",
          height: "100%",
          onPageChange: function (idx) {
            // jdf.js reports the most visible page from an IntersectionObserver;
            // during the smooth scroll of an explicit goToPage the old page is
            // still the most visible one, so the label flipped back for a frame
            // (390 px smoke, 2026-09-28). The requested page holds until the
            // scroll has had time to land; after that the observer is the truth.
            if (Date.now() < S.navUntil && Number(idx) !== S.page) return;
            S.page = Number(idx) || 0;
            _paintNav();
            if (typeof S.opts.onPageChange === "function") S.opts.onPageChange(S.page + 1);
          },
          // onLoad fires inside embed(), before embed's own promise settles:
          // the viewer instance is not known yet, so the count is recorded
          // here and the open resolves below, once S.viewer is set (a resolve
          // from here handed the shell a null viewer — probe, 2026-09-28).
          onLoad: function (doc) {
            S.pageCount = doc && Array.isArray(doc.pages) ? doc.pages.length : 0;
            if (!S.pageCount) S.pageCount = _countPages();
            _paintNav();
            if (typeof S.opts.onLoad === "function") S.opts.onLoad(doc);
          },
          onError: function (err) {
            if (typeof S.opts.onError === "function") S.opts.onError(err);
            if (!settled) { settled = true; reject(err); }
          },
        }).then(function (viewer) {
          if (S.url !== thisUrl) { try { viewer.destroy(); } catch (_) {} return; }
          S.viewer = viewer;
          S.zoom = viewer.getZoom ? viewer.getZoom() : 1;
          if (!S.pageCount) S.pageCount = _countPages();
          _paintNav();
          if (!settled) { settled = true; resolve(viewer); }
        }).catch(function (err) {
          if (!settled) { settled = true; reject(err); }
        });
      });
    });
  }
  // ---------------------------------------------------------------------
  // openOriginal(container, spec, opts) → Promise<viewer>
  // The uploaded file itself instead of the JDF re-render (user request
  // 2026-10-01: "dosyanın content'ini çirkin görmek istemiyorum").
  //   spec.mode "pdf":   the stored original PDF in the browser's own viewer
  //                      (an <iframe>, `#page=N` to jump) — a digital PDF is
  //                      its own best rendering; no boxes are drawn on it.
  //   spec.mode "image": one <img> per page (spec.pages [{page, url}] — the
  //                      stored page rasters, or the original image upload),
  //                      each in a `.jdfjs-page-wrapper[data-page-index]` so
  //                      highlight(), selection and showFields() work on it.
  // A minimal viewer object stands in for jdf.js so goToPage / zoom / isOpen
  // behave the same in every mode.
  // ---------------------------------------------------------------------
  function openOriginal(container, spec, opts) {
    destroy();
    S.container = typeof container === "string" ? document.querySelector(container) : container;
    if (!S.container) return Promise.reject(new Error("source view: no container"));
    spec = spec || {};
    S.opts = opts || {};
    S.mode = spec.mode === "pdf" ? "pdf" : "image";
    S.url = String(spec.key || spec.pdfUrl || (spec.pages && spec.pages[0] && spec.pages[0].url) || "");
    S.zoom = 1;
    S.page = 0;
    while (S.container.firstChild) S.container.removeChild(S.container.firstChild);
    S.root = _el("div", "sv-root sv-original sv-mode-" + S.mode);
    S.root.appendChild(_buildNav());
    S.stage = _el("div", "sv-stage");
    S.stage.setAttribute("data-source-url", S.url);
    S.root.appendChild(S.stage);
    S.container.appendChild(S.root);
    _bindDocListeners();
    if (S.mode === "pdf") {
      var pdfUrl = String(spec.pdfUrl || "");
      S.pageCount = Number(spec.pageCount) || 0;
      var frame = _el("iframe", "sv-pdf-frame");
      frame.title = _t("shell.source_view.original_pdf", "Original PDF");
      frame.src = pdfUrl + "#view=FitH";
      S.stage.appendChild(frame);
      S.viewer = {
        goToPage: function (idx) { frame.src = pdfUrl + "#page=" + (Number(idx) + 1) + "&view=FitH"; },
        getZoom: function () { return 1; },
        setZoom: function () {},
        destroy: function () { frame.src = "about:blank"; },
      };
      if (S.nav) {
        // The browser's PDF viewer has its own zoom; ours would do nothing.
        Array.prototype.forEach.call(S.nav.querySelectorAll(".sv-zoom-out, .sv-zoom-in, .sv-nav-sep"), function (b) { b.hidden = true; });
      }
      _paintNav();
      return new Promise(function (resolve) {
        var done = false;
        function ok() { if (done) return; done = true; resolve(S.viewer); }
        frame.addEventListener("load", ok);
        setTimeout(ok, 1500);   // a PDF plugin may never fire load on the frame
      });
    }
    var pages = Array.isArray(spec.pages) ? spec.pages.slice() : [];
    pages.sort(function (a, b) { return (Number(a.page) || 0) - (Number(b.page) || 0); });
    S.pageCount = pages.length;
    var loads = pages.map(function (p, i) {
      var wrapper = _el("div", "jdfjs-page-wrapper sv-img-page");
      wrapper.setAttribute("data-page-index", String((Number(p.page) || i + 1) - 1));
      var img = _el("img", "sv-page-img");
      img.alt = _tf("shell.source_view.page_of", "Page {n} of {total}", { n: Number(p.page) || i + 1, total: pages.length });
      img.decoding = "async";
      var loaded = new Promise(function (resolve) {
        img.addEventListener("load", function () { resolve(true); });
        img.addEventListener("error", function () { wrapper.classList.add("is-broken"); resolve(false); });
      });
      img.src = String(p.url || "");
      wrapper.appendChild(img);
      S.stage.appendChild(wrapper);
      return loaded;
    });
    S.viewer = {
      goToPage: function (idx) {
        var w = _wrapperFor(Number(idx) + 1);
        if (w) { try { w.scrollIntoView({ block: "start", behavior: "smooth" }); } catch (_) {} }
      },
      getZoom: function () { return S.zoom; },
      setZoom: function (z) {
        Array.prototype.forEach.call(S.stage ? S.stage.querySelectorAll(".sv-img-page") : [], function (w) {
          w.style.width = Math.round(z * 100) + "%";
        });
      },
      destroy: function () {},
    };
    if (typeof IntersectionObserver !== "undefined") {
      var io = new IntersectionObserver(function (entries) {
        if (Date.now() < S.navUntil) return;
        var best = null;
        entries.forEach(function (e) { if (e.isIntersecting && (!best || e.intersectionRatio > best.intersectionRatio)) best = e; });
        if (!best) return;
        S.page = Number(best.target.getAttribute("data-page-index")) || 0;
        _paintNav();
        if (typeof S.opts.onPageChange === "function") S.opts.onPageChange(S.page + 1);
      }, { root: S.stage, threshold: [0.25, 0.5, 0.75] });
      Array.prototype.forEach.call(S.stage.querySelectorAll(".sv-img-page"), function (w) { io.observe(w); });
      S.io = io;
    }
    _paintNav();
    return Promise.all(loads).then(function () {
      if (Array.isArray(spec.fields)) showFields(spec.fields);
      return S.viewer;
    });
  }

  // ---------------------------------------------------------------------
  // showFields(fields) — every read field drawn where the page states it: a
  // box at its `source_span.bbox` (relative 0–1, 1-based `page`) and a tag
  // with the label and the value, on the image pages only (a native PDF
  // viewer cannot carry an overlay). A field without a box is not placed —
  // no position is invented for it. Returns the number placed.
  // ---------------------------------------------------------------------
  function showFields(fields) {
    hideFields();
    if (!S.stage || S.mode === "pdf") return 0;
    var placed = 0;
    (fields || []).forEach(function (f) {
      if (!f || typeof f !== "object") return;
      var span = f.source_span && typeof f.source_span === "object" ? f.source_span : {};
      var bbox = span.bbox || f.bbox || null;
      var page1 = Number(span.page != null ? span.page : f.page);
      if (!bbox || !page1) return;
      var wrapper = _wrapperFor(page1);
      if (!wrapper) return;
      var value = f.value != null && f.value !== "" ? String(f.value) : String(f.raw || "");
      if (!value) return;
      var layer = _layerFor(wrapper);
      var box = _el("div", "sv-field" + ((f.handwritten || span.handwritten) ? " is-handwritten" : "") + (f.review_required ? " needs-review" : ""));
      var label = String(f.label || f.name || "");
      box.title = (label ? label + ": " : "") + value;
      var tag = _el("span", "sv-field-tag", label || value);
      box.appendChild(tag);
      if (typeof S.opts.onFieldClick === "function") {
        box.addEventListener("click", function (e) { e.stopPropagation(); S.opts.onFieldClick(f); });
      }
      layer.appendChild(box);
      var mark = { wrapper: wrapper, layer: layer, box: box, bbox: bbox, ro: null, timer: null, field: true };
      _place(mark);
      if (typeof ResizeObserver !== "undefined") {
        mark.ro = new ResizeObserver(function () { _place(mark); });
        mark.ro.observe(wrapper);
      }
      S.fieldMarks.push(mark);
      placed++;
    });
    if (S.root) S.root.classList.toggle("has-fields", placed > 0);
    return placed;
  }
  function hideFields() {
    (S.fieldMarks || []).forEach(function (m) {
      if (m.ro) { try { m.ro.disconnect(); } catch (_) {} }
      if (m.box && m.box.parentNode) m.box.parentNode.removeChild(m.box);
    });
    S.fieldMarks = [];
    if (S.root) S.root.classList.remove("has-fields");
  }
  function mode() { return S.mode || "jdf"; }

  function _countPages() {
    return S.stage ? S.stage.querySelectorAll(".jdfjs-page-wrapper[data-page-index]").length : 0;
  }

  function isOpen() { return Boolean(S.viewer && S.stage); }
  function currentPage() { return S.page + 1; }
  function pageCount() { return S.pageCount; }

  // 1-based, as every anchor's `page` is.
  function goToPage(page1) {
    if (!S.viewer) return false;
    var idx = Math.max(0, Math.min((S.pageCount || 1) - 1, (Number(page1) || 1) - 1));
    try { S.viewer.goToPage(idx); } catch (_) { return false; }
    S.page = idx;
    S.navUntil = Date.now() + 700;
    _paintNav();
    return true;
  }
  function zoomBy(delta) {
    if (!S.viewer) return;
    var z = (S.viewer.getZoom ? S.viewer.getZoom() : S.zoom) + delta;
    setZoom(z);
  }
  function setZoom(z) {
    if (!S.viewer) return;
    S.zoom = Math.max(0.25, Math.min(3, Number(z) || 1));
    try { S.viewer.setZoom(S.zoom); } catch (_) {}
    _relayoutAll();
  }

  // ---------------------------------------------------------------------
  // Overlay. One layer per page wrapper (absolute, inset 0, pointer-events
  // none); boxes inside it are positioned from bboxToPx against the page's
  // rendered size. Re-laid out on the wrapper's ResizeObserver (zoom, host
  // resize) — the wrapper is what applyZoom resizes.
  // ---------------------------------------------------------------------
  function _wrapperFor(page1) {
    if (!S.stage) return null;
    return S.stage.querySelector('.jdfjs-page-wrapper[data-page-index="' + ((Number(page1) || 1) - 1) + '"]');
  }
  // The rendered page box: nominal page px (jdf.js writes them inline on
  // .jdfjs-page) × the zoom the wrapper shows, read off the wrapper's own
  // width so we never disagree with what is on screen.
  function pageRect(wrapper) {
    if (!wrapper) return null;
    var pageEl = wrapper.querySelector(".jdfjs-page");
    var nominalW = pageEl ? parseFloat(pageEl.style.width || "0") : 0;
    var nominalH = pageEl ? parseFloat(pageEl.style.minHeight || "0") : 0;
    if (!nominalW || !nominalH) {
      return { width: wrapper.clientWidth || parseFloat(wrapper.style.width || "0"), height: wrapper.clientHeight || parseFloat(wrapper.style.height || "0") };
    }
    // The zoom on screen is the page's own `transform: scale(z)` (applyZoom
    // writes it). The wrapper's clientWidth is NOT it: jdfjs.css caps the
    // wrapper at max-width: 100%, so after a zoom-in the page overflows a
    // wrapper that still measures the host's width (probe, 2026-09-28).
    var zoom = 0;
    var m = /scale\(([\d.]+)\)/.exec(pageEl.style.transform || "");
    if (m) zoom = parseFloat(m[1]);
    if (!zoom) { var sw = parseFloat(wrapper.style.width || "0"); if (sw) zoom = sw / nominalW; }
    if (!zoom) zoom = (wrapper.clientWidth || nominalW) / nominalW;
    return { width: nominalW * zoom, height: nominalH * zoom, zoom: zoom };
  }
  function _layerFor(wrapper) {
    var layer = wrapper.querySelector(":scope > .sv-overlay");
    if (!layer) {
      layer = _el("div", "sv-overlay");
      layer.setAttribute("aria-hidden", "true");
      wrapper.appendChild(layer);
    }
    return layer;
  }
  function _place(mark) {
    var rect = pageRect(mark.wrapper);
    var px = bboxToPx(mark.bbox, rect);
    if (!px) { mark.box.hidden = true; return; }
    mark.box.hidden = false;
    mark.box.style.left = px.left + "px";
    mark.box.style.top = px.top + "px";
    mark.box.style.width = Math.max(2, px.width) + "px";
    mark.box.style.height = Math.max(2, px.height) + "px";
  }
  function _relayoutAll() { S.marks.forEach(_place); }

  // highlight({page, bbox, flash}) → the mark, or null when the page is not
  // rendered. The box flashes for FLASH_MS then keeps a thin outline until
  // clear(). A second highlight replaces the first (one "shown" thing at a
  // time is what the reader asked for).
  function highlight(spec) {
    if (!spec || !S.stage) return null;
    clear();
    var page1 = Number(spec.page) || 1;
    if (S.mode === "pdf") { goToPage(page1); return null; }
    var wrapper = _wrapperFor(page1);
    if (!wrapper) return null;
    goToPage(page1);
    if (!spec.bbox) return null;          // page only: navigated, nothing to box
    var layer = _layerFor(wrapper);
    var box = _el("div", "sv-mark is-flash");
    box.setAttribute("data-page", String(page1));
    layer.appendChild(box);
    var mark = { wrapper: wrapper, layer: layer, box: box, bbox: spec.bbox, ro: null, timer: null };
    _place(mark);
    if (typeof ResizeObserver !== "undefined") {
      mark.ro = new ResizeObserver(function () { _place(mark); });
      mark.ro.observe(wrapper);
    }
    mark.timer = setTimeout(function () {
      box.classList.remove("is-flash");
      box.classList.add("is-outline");
      mark.timer = null;
    }, spec.flash === false ? 0 : FLASH_MS);
    S.marks.push(mark);
    // Bring the box itself into view once the page scroll has landed.
    setTimeout(function () {
      try { if (!box.hidden) box.scrollIntoView({ block: "center", behavior: "smooth" }); } catch (_) {}
    }, 60);
    return mark;
  }
  function clear() {
    S.marks.forEach(function (m) {
      if (m.timer) clearTimeout(m.timer);
      if (m.ro) { try { m.ro.disconnect(); } catch (_) {} }
      if (m.box && m.box.parentNode) m.box.parentNode.removeChild(m.box);
    });
    S.marks = [];
  }

  // ---------------------------------------------------------------------
  // Selection → {text, page, bbox_rel, rect}. Fires on mouseup / touchend /
  // selectionchange (debounced) when a non-collapsed selection starts and
  // ends inside ONE page wrapper of our stage. bbox_rel is the selection's
  // bounding rect relative to that page — a hint for the field filter, not
  // an element id.
  // ---------------------------------------------------------------------
  function onSelection(cb) {
    if (typeof cb === "function") S.selCbs.push(cb);
    return function () { S.selCbs = S.selCbs.filter(function (f) { return f !== cb; }); };
  }
  function _wrapperOfNode(node) {
    if (!node || !S.stage) return null;
    var el = node.nodeType === 1 ? node : node.parentElement;
    if (!el || !S.stage.contains(el)) return null;
    return el.closest(".jdfjs-page-wrapper[data-page-index]");
  }
  function currentSelection() {
    var sel = window.getSelection ? window.getSelection() : null;
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
    var text = String(sel.toString() || "").replace(/\s+/g, " ").trim();
    if (!text) return null;
    var a = _wrapperOfNode(sel.anchorNode), f = _wrapperOfNode(sel.focusNode);
    if (!a || !f || a !== f) return null;
    var page1 = (Number(a.getAttribute("data-page-index")) || 0) + 1;
    var range = sel.getRangeAt(0);
    var r = range.getBoundingClientRect();
    var pr = a.getBoundingClientRect();
    var prect = pageRect(a) || { width: pr.width, height: pr.height };
    var bbox = null;
    if (prect.width > 0 && prect.height > 0 && r.width >= 0) {
      bbox = [
        Math.max(0, Math.min(1, (r.left - pr.left) / prect.width)),
        Math.max(0, Math.min(1, (r.top - pr.top) / prect.height)),
        Math.max(0, Math.min(1, (r.right - pr.left) / prect.width)),
        Math.max(0, Math.min(1, (r.bottom - pr.top) / prect.height)),
      ];
    }
    return { text: text, page: page1, bbox_rel: bbox, rect: { left: r.left, top: r.top, width: r.width, height: r.height, bottom: r.bottom, right: r.right } };
  }
  function _fireSelection() {
    if (!S.selCbs.length || !S.stage) return;
    var cur = currentSelection();
    var key = cur ? cur.page + "|" + cur.text : "";
    if (!cur) { S.lastSel = ""; return; }
    if (key === S.lastSel) return;
    S.lastSel = key;
    S.selCbs.forEach(function (cb) { try { cb(cur); } catch (_) {} });
  }
  function _onMouseUp() { setTimeout(_fireSelection, 0); }
  function _onSelectionChange() {
    if (S.selTimer) clearTimeout(S.selTimer);
    S.selTimer = setTimeout(_fireSelection, 220);
  }
  function _bindDocListeners() {
    if (S.docListeners) return;
    document.addEventListener("mouseup", _onMouseUp);
    document.addEventListener("touchend", _onMouseUp);
    document.addEventListener("selectionchange", _onSelectionChange);
    S.docListeners = true;
  }
  function _unbindDocListeners() {
    if (!S.docListeners) return;
    document.removeEventListener("mouseup", _onMouseUp);
    document.removeEventListener("touchend", _onMouseUp);
    document.removeEventListener("selectionchange", _onSelectionChange);
    S.docListeners = false;
  }

  function destroy() {
    clear();
    hideFields();
    if (S.io) { try { S.io.disconnect(); } catch (_) {} S.io = null; }
    S.mode = "jdf";
    if (S.viewer) { try { S.viewer.destroy(); } catch (_) {} }
    S.viewer = null;
    if (S.root && S.root.parentNode) S.root.parentNode.removeChild(S.root);
    S.root = null; S.stage = null; S.nav = null;
    S.url = ""; S.pageCount = 0; S.page = 0; S.lastSel = "";
    _unbindDocListeners();
  }

  window.SourceView = {
    open: open,
    openOriginal: openOriginal,
    showFields: showFields,
    hideFields: hideFields,
    mode: mode,
    destroy: destroy,
    isOpen: isOpen,
    goToPage: goToPage,
    currentPage: currentPage,
    pageCount: pageCount,
    setZoom: setZoom,
    zoomBy: zoomBy,
    highlight: highlight,
    clear: clear,
    onSelection: onSelection,
    currentSelection: currentSelection,
    bboxToPx: bboxToPx,
    pageRect: pageRect,
    FLASH_MS: FLASH_MS,
  };
})();
