/* bookery lesson runtime.
 *
 * Renders one lesson payload (embedded as #lesson-data) in two modes that
 * share the same slide markup:
 *   - Study:   a scrolling column of slide cards with the book's passages
 *              one click away, a table of contents and a progress meter.
 *   - Present: one slide at a time on a fixed 1600x900 stage, keyboard and
 *              swipe navigation, builds (figure notes, steps) on "next",
 *              and a speaker-notes drawer.
 *
 * Chapter-specific widgets register with Bookery.widget(name, mount) before
 * Bookery.boot() runs. Everything else here is book-agnostic.
 */
(function () {
  "use strict";

  var Bookery = (window.Bookery = window.Bookery || {});
  var registry = {};
  var errors = [];
  Bookery.widget = function (name, mount) { registry[name] = mount; };

  // ---------------------------------------------------------------------
  // DOM + text helpers
  // ---------------------------------------------------------------------

  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "html") el.innerHTML = v;
        else if (k === "text") el.textContent = v;
        else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
        else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
        else el.setAttribute(k, v === true ? "" : v);
      }
    }
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function append(el) {
    for (var i = 1; i < arguments.length; i++) {
      var child = arguments[i];
      if (child === null || child === undefined || child === false) continue;
      if (Array.isArray(child)) { child.forEach(function (c) { append(el, c); }); continue; }
      el.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
  }
  function svg(tag, attrs) {
    var el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (var k in attrs || {}) if (attrs[k] !== null && attrs[k] !== undefined) el.setAttribute(k, attrs[k]);
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var glossary = {};   // term -> plain definition, for the hover on [[term]]
  var terms = {};      // term -> its glossary entry, for the slide that teaches it

  var sawMath = false; // md() met real math, not just "$300"; the self-test reports it
  function tex(src, display) {
    sawMath = true;
    if (window.katex) {
      try {
        var out = window.katex.renderToString(src, { displayMode: display, throwOnError: true, output: "mathml", strict: "ignore" });
        return display ? '<span class="bk-math-block">' + out + "</span>" : out;
      } catch (e) {
        errors.push("math: " + (e && e.message ? e.message : e) + " in " + src);
      }
    }
    return '<span class="bk-tex-fallback">' + esc(src) + "</span>";
  }

  // Inline markup: **bold**, *italic*, `code`, [[term]] / [[term|label]],
  // math as \( \) \[ \] $$ $$ or $ $ (pandoc's rule, so "$2 and $10" stays
  // money), and a blank line for a paragraph break.
  var RE_MATH = /\\\[([\s\S]+?)\\\]|\$\$([\s\S]+?)\$\$|\\\(([\s\S]+?)\\\)|\$(?![\s\d])((?:\\\$|[^$\n])+?)(?<!\s)\$(?!\d)/g;
  function md(text) {
    if (text === null || text === undefined) return "";
    var s = String(text), out = "", last = 0, m;
    RE_MATH.lastIndex = 0;
    while ((m = RE_MATH.exec(s))) {
      out += inline(s.slice(last, m.index));
      if (m[1] !== undefined) out += tex(m[1], true);
      else if (m[2] !== undefined) out += tex(m[2], true);
      else out += tex(m[3] !== undefined ? m[3] : m[4], false);
      last = RE_MATH.lastIndex;
    }
    return out + inline(s.slice(last));
  }
  function inline(s) {
    return esc(s)
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<i>$2</i>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, function (_, term, label) {
        var def = glossary[term.trim().toLowerCase()];
        return '<span class="bk-term" tabindex="0" title="' + esc(def || "") + '">' + (label || term) + "</span>";
      })
      .replace(/\n\s*\n/g, "<br><br>");
  }
  function mdEl(tag, text, cls) { return h(tag, { class: cls, html: md(text) }); }

  function mulberry(seed) {
    var a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function hash(str) { var x = 2166136261; for (var i = 0; i < str.length; i++) { x ^= str.charCodeAt(i); x = Math.imul(x, 16777619); } return x >>> 0; }
  function shuffle(arr, seedStr) {
    var r = mulberry(hash(seedStr)), a = arr.slice();
    for (var i = a.length - 1; i > 0; i--) { var j = Math.floor(r() * (i + 1)); var t = a[i]; a[i] = a[j]; a[j] = t; }
    return a;
  }
  function fmt(v, decimals) {
    if (typeof v !== "number" || !isFinite(v)) return "–";
    var d = decimals === undefined ? (Math.abs(v) >= 100 || Number.isInteger(v) ? 0 : 2) : decimals;
    return v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  }

  var ICON = {
    play: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>',
    prev: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3 5 8l5 5" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
    next: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
    close: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3.5 3.5 9 9m0-9-9 9" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
    full: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>',
  };

  // ---------------------------------------------------------------------
  // Per-viewer progress (a convenience; the page works without it)
  // ---------------------------------------------------------------------

  var storeKey = "";
  var progress = { seen: {}, results: {} };
  function loadProgress() {
    try { var raw = localStorage.getItem(storeKey); if (raw) progress = Object.assign(progress, JSON.parse(raw)); } catch (e) { /* storage blocked */ }
  }
  function saveProgress() {
    try { localStorage.setItem(storeKey, JSON.stringify(progress)); } catch (e) { /* storage blocked */ }
  }
  function markSeen(id) {
    if (progress.seen[id]) return;
    progress.seen[id] = 1; saveProgress(); updateMeter();
  }
  function record(slideId, ok) {
    if (!(slideId in progress.results)) { progress.results[slideId] = !!ok; saveProgress(); updateMeter(); }
  }

  // ---------------------------------------------------------------------
  // Payload
  // ---------------------------------------------------------------------

  var D, L, flat = [], bySlide = {};
  var ACTIVE = { quiz: 1, predict: 1, steps: 1, flashcards: 1, match: 1, sort: 1, explore: 1, custom: 1 };
  var RECALL = { quiz: 1, predict: 1, match: 1, sort: 1 };

  function layoutOf(s) {
    if (s.layout && s.layout !== "auto") return s.layout;
    if (s.widget) return "widget";
    if (s.figure) return "figure";
    if (s.stats) return "stats";
    if (s.quote) return "quote";
    if (s.columns) return "columns";
    if (s.points) return "points";
    return "statement";
  }
  // A custom widget marked static is a drawn figure: visual, nothing to do.
  function isTurn(s) { return !!(s.widget && ACTIVE[s.widget.type] && !(s.widget.type === "custom" && s.widget.static)); }

  // ---------------------------------------------------------------------
  // Slide rendering (shared by both modes)
  // ---------------------------------------------------------------------

  function renderSlide(s, mode) {
    var layout = layoutOf(s);
    var ctx = { mode: mode, slide: s, builds: [] };
    var el = h("article", { class: "bk-slide is-" + layout, "data-slide": s.id });
    el._ctx = ctx;

    if (layout === "title") {
      append(el, [
        h("div", { class: "bk-kicker", text: D.book.title + (D.book.author ? " · " + D.book.author : "") }),
        h("div", { class: "bk-hero-numeral", "aria-hidden": "true", text: String(D.chapter.number) }),
        h("div", { class: "bk-hero-chapter", text: "Chapter " + D.chapter.number }),
        mdEl("h2", s.title, "bk-title"),
        s.text ? mdEl("p", s.text, "bk-text") : null,
      ]);
      return el;
    }

    var top = h("div", { class: "bk-card-top" },
      h("span", { class: "bk-kicker", text: s.kicker || (layout === "section" ? "Section" : "") }),
      isTurn(s) && !/^your turn$/i.test(s.kicker || "") ? h("span", { class: "bk-turn-tag", text: "Your turn" }) : null);
    append(el, top);
    append(el, mdEl("h2", s.title, "bk-title"));

    var lead = [];
    if (s.text) lead.push(mdEl("p", s.text, "bk-text"));
    (s.teaches || []).forEach(function (t) { var g = terms[t.toLowerCase()]; if (g) lead.push(defineCard(g)); });
    if (s.points) lead.push(h("ul", { class: "bk-points" }, s.points.map(function (p) { return mdEl("li", p); })));

    // On the stage a definition card stacked over a widget or a row of
    // stats runs off the bottom, so a slide that teaches puts its text
    // beside them instead.
    var beside = mode === "present" && (s.teaches || []).length > 0 && lead.length > 0;
    var body = h("div", { class: "bk-body" });
    if (layout === "figure") {
      var fig = D.figures[s.figure.key] || {};
      var stacked = fig.w && fig.h && fig.w / fig.h > (mode === "present" ? 3 : 1.25);
      if (!lead.length) stacked = true;
      append(body, h("div", { class: "bk-split" + (stacked ? " is-stacked" : "") },
        lead.length ? h("div", { class: "bk-body" }, lead) : null,
        renderFigure(s.figure, ctx)));
    } else if (layout === "stats") {
      var stats = h("div", { class: "bk-stats" }, s.stats.map(function (st) {
        return h("div", { class: "bk-stat" },
          mdEl("div", st.value, "bk-stat-value"),
          mdEl("div", st.label, "bk-stat-label"),
          st.context ? mdEl("div", st.context, "bk-stat-context") : null);
      }));
      if (beside) append(body, h("div", { class: "bk-split is-widget" }, h("div", { class: "bk-body" }, lead), stats));
      else { append(body, stats); append(body, lead); }
    } else if (layout === "quote") {
      // The page comes from the quoted block, so a citation can't drift from it.
      var qsrc = s.quote.block && D.sources[s.quote.block];
      var cite = [s.quote.cite, qsrc && qsrc.page !== null && qsrc.page !== undefined ? "p. " + qsrc.page : null].filter(Boolean).join(", ");
      append(body, h("blockquote", { class: "bk-quote" },
        mdEl("p", "“" + s.quote.text.replace(/^["“]|["”]$/g, "") + "”"),
        cite ? h("cite", { text: cite }) : null));
      append(body, lead);
    } else if (layout === "columns") {
      append(body, lead);
      append(body, h("div", { class: "bk-columns" }, s.columns.map(function (c) {
        return h("div", { class: "bk-col" },
          mdEl("h3", c.title),
          c.text ? mdEl("p", c.text, "bk-text") : null,
          c.points ? h("ul", { class: "bk-points" }, c.points.map(function (p) { return mdEl("li", p); })) : null);
      })));
    } else if (layout === "widget") {
      if (beside && s.widget.wide) {
        // A wide widget (a pipeline, a long table) keeps the stage's width;
        // its text and new terms share one row above it.
        var wcards = lead.filter(function (n) { return n.classList.contains("bk-define"); });
        var wsaid = lead.filter(function (n) { return wcards.indexOf(n) < 0; });
        append(body, h("div", { class: "bk-split is-widget is-terms" }, h("div", { class: "bk-body" }, wsaid), h("div", { class: "bk-terms" }, wcards)));
        append(body, renderWidget(s.widget, ctx));
      } else if (beside) {
        append(body, h("div", { class: "bk-split is-widget" },
          h("div", { class: "bk-body" }, lead),
          renderWidget(s.widget, ctx)));
      } else {
        append(body, lead);
        append(body, renderWidget(s.widget, ctx));
      }
    } else {
      // A text slide that teaches: its point on the left and the new terms
      // on the right, rather than one column that runs off the stage.
      var cards = lead.filter(function (n) { return n.classList.contains("bk-define"); });
      var said = lead.filter(function (n) { return cards.indexOf(n) < 0; });
      if (beside && cards.length && said.length) {
        append(body, h("div", { class: "bk-split is-widget is-terms" }, h("div", { class: "bk-body" }, said), h("div", { class: "bk-terms" }, cards)));
      } else {
        append(body, lead);
      }
    }
    append(el, body);

    if (mode === "study") {
      if (s.deeper) append(el, mdEl("p", s.deeper, "bk-deeper"));
      if (s.beyond) append(el, h("aside", { class: "bk-beyond" }, h("span", { class: "bk-label", text: "Beyond the book" }), mdEl("p", s.beyond)));
      var passages = renderPassages(s);
      if (passages) append(el, h("details", { class: "bk-sources" },
        h("summary", { text: "From the book · " + pagesOf(s) }), passages));
    } else if (layout !== "section") {
      // The page's foot: which book, and which of its pages this slide condenses.
      append(el, h("div", { class: "bk-folio", "aria-hidden": "true" },
        h("span", { text: D.book.title + " · Chapter " + D.chapter.number }),
        h("span", { text: pageRange(s) })));
    }
    return el;
  }

  // A term meets the reader here first: what it means, then the term at
  // work in one concrete case. Later slides only underline it.
  function defineCard(g) {
    return h("div", { class: "bk-define" },
      h("div", { class: "bk-define-head" },
        h("span", { class: "bk-define-tag", text: "New term" }),
        mdEl("strong", g.term, "bk-define-term")),
      mdEl("p", g.def, "bk-define-def"),
      g.example ? h("p", { class: "bk-define-ex" }, h("span", { class: "bk-define-ex-label", text: "Example" }), mdEl("span", g.example)) : null);
  }

  function pagesOf(s) {
    var n = (s.sources || []).filter(function (id) { return D.sources[id]; }).length;
    var label = n + (n === 1 ? " passage" : " passages");
    var pages = pageRange(s);
    return pages ? label + ", " + pages : label;
  }

  // "p. 41" or "pp. 41–43, 47": runs of consecutive pages collapse to a range.
  function pageRange(s) {
    var pages = [];
    (s.sources || []).forEach(function (id) {
      var src = D.sources[id];
      if (src && typeof src.page === "number" && pages.indexOf(src.page) < 0) pages.push(src.page);
    });
    if (!pages.length) return "";
    pages.sort(function (a, b) { return a - b; });
    var runs = [], start = pages[0], prev = pages[0];
    pages.slice(1).concat([null]).forEach(function (p) {
      if (p === prev + 1) { prev = p; return; }
      runs.push(start === prev ? String(start) : start + "–" + prev);
      start = prev = p;
    });
    return (pages.length === 1 ? "p. " : "pp. ") + runs.join(", ");
  }

  function renderPassages(s) {
    var ids = (s.sources || []).filter(function (id) { return D.sources[id]; });
    if (!ids.length) return null;
    return h("div", { class: "bk-passages" }, ids.map(function (id) {
      var src = D.sources[id];
      var where = [src.page !== null && src.page !== undefined ? "p. " + src.page : null, src.section].filter(Boolean).join(" · ");
      return h("div", { class: "bk-passage" }, where ? h("span", { class: "bk-where", text: where }) : null, h("p", { text: src.text }));
    }));
  }

  function renderFigure(spec, ctx) {
    var f = D.figures[spec.key];
    if (!f) return h("div", { class: "bk-error", text: "Missing figure " + spec.ref });
    var frame = h("div", { class: "bk-figure-frame", role: "button", tabindex: "0", "aria-label": "Enlarge " + (f.label || "figure") },
      h("img", { src: f.src, alt: spec.alt || f.caption || f.label || "Figure", width: f.w, height: f.h, loading: "eager" }));
    var pins = [], items = [];
    (spec.notes || []).forEach(function (n, i) {
      var pin = h("span", { class: "bk-pin", style: { left: n.x * 100 + "%", top: n.y * 100 + "%" }, text: String(i + 1) });
      pins.push(pin); frame.appendChild(pin);
      items.push(h("li", null, h("b", { text: String(i + 1) }), mdEl("span", n.text)));
    });
    function zoom() { lightbox(f); }
    frame.addEventListener("click", zoom);
    frame.addEventListener("keydown", function (e) { if (e.key === "Enter") zoom(); });
    var label = [f.label, f.page !== null && f.page !== undefined ? "p. " + f.page : null].filter(Boolean).join(" · ");
    var el = h("figure", { class: "bk-figure" + (items.length ? " has-notes" : "") }, frame,
      h("figcaption", null, label ? h("span", { class: "bk-where", text: label }) : null, mdEl("span", spec.caption || f.caption)),
      items.length ? h("ol", { class: "bk-notes" }, items) : null);
    // The frame's width follows the image's aspect, so a height cap narrows
    // the frame instead of letterboxing the image and misplacing the pins.
    if (f.w && f.h) el.style.setProperty("--ar", String(f.w / f.h));
    if (ctx.mode === "present" && pins.length) {
      // Notes appear one per "next", so a presenter can walk the figure.
      var shown = 0;
      pins.forEach(function (p, i) { p.classList.add("is-hidden"); items[i].classList.add("is-hidden"); });
      ctx.builds.push(function () {
        if (shown >= pins.length) return false;
        pins[shown].classList.remove("is-hidden"); items[shown].classList.remove("is-hidden"); shown++;
        return true;
      });
    }
    return el;
  }

  function lightbox(f) {
    var box = h("div", { class: "bk-lightbox", onclick: function () { box.remove(); } }, h("img", { src: f.src, alt: f.caption || "" }));
    document.body.appendChild(box);
  }

  // Numbers a widget read off the book's images (`transcribed`): the
  // images themselves, one click away, so a reader can check every value.
  function renderTranscribed(keys) {
    var figs = keys.map(function (k) { return D.figures[k]; }).filter(Boolean);
    if (!figs.length) return null;
    var what = figs.length === 1 ? "the book's figure" : "the book's " + figs.length + " figures";
    return h("details", { class: "bk-sources bk-transcribed" },
      h("summary", { text: "Numbers read from " + what + " · check them" }),
      h("div", { class: "bk-thumbs" }, figs.map(function (f) {
        var label = [f.label, f.page !== null && f.page !== undefined ? "p. " + f.page : null].filter(Boolean).join(" · ");
        return h("button", { class: "bk-thumb", type: "button", "aria-label": "Enlarge " + (f.label || f.caption || "figure"), onclick: function () { lightbox(f); } },
          h("img", { src: f.src, alt: f.caption || f.label || "Figure", width: f.w, height: f.h, loading: "lazy" }),
          h("span", { text: label || f.caption || "" }));
      })));
  }

  // ---------------------------------------------------------------------
  // Widgets
  // ---------------------------------------------------------------------

  var W = {};

  function renderWidget(spec, ctx) {
    var el = h("div", { class: "bk-widget bk-w-" + spec.type + (spec.static ? " is-static" : "") });
    var api = {
      h: h, svg: svg, md: md, mdEl: mdEl, chart: drawChart, fmt: fmt, shuffle: shuffle, rng: mulberry,
      mode: ctx.mode, slideId: ctx.slide.id,
      record: function (ok) { record(ctx.slide.id, ok); },
      onNext: function (fn) { ctx.builds.push(fn); },
      figure: function (ref) { return D.figures[ref] || null; },
    };
    try {
      if (spec.caption && spec.type !== "custom") append(el, mdEl("p", spec.caption, "bk-w-caption"));
      if (spec.type === "custom") {
        var mount = registry[spec.module];
        if (!mount) throw new Error("custom widget '" + spec.module + "' is not registered");
        append(el, mdEl("p", spec.caption, "bk-w-caption"));
        var host = h("div", { class: "bk-custom" });
        el.appendChild(host);
        mount(host, spec.props || {}, api);
      } else {
        W[spec.type](el, spec, api);
      }
      if (ctx.mode === "study" && spec.transcribed) append(el, renderTranscribed(spec.transcribed));
    } catch (e) {
      var msg = "widget " + spec.type + (spec.module ? ":" + spec.module : "") + " on " + ctx.slide.id + ": " + (e && e.message ? e.message : e);
      errors.push(msg);
      el.appendChild(h("div", { class: "bk-error", text: msg }));
    }
    return el;
  }

  W.quiz = function (el, spec, api) {
    var keys = "ABCDEF";
    var multi = !!spec.multi;
    var chosen = {};
    var feedback = h("div");
    var buttons = spec.options.map(function (o, i) {
      var why = h("div", { class: "bk-why", hidden: true, html: md(o.why) });
      var b = h("button", { class: "bk-option", type: "button", "aria-pressed": "false" },
        h("span", { class: "bk-option-key", text: keys[i] }), h("span", null, mdEl("span", o.text), why));
      b._why = why;
      b.addEventListener("click", function () {
        if (multi) { chosen[i] = !chosen[i]; b.classList.toggle("is-picked", chosen[i]); b.setAttribute("aria-pressed", String(!!chosen[i])); }
        else { chosen = {}; chosen[i] = true; reveal(); }
      });
      return b;
    });
    function reveal() {
      var allRight = true;
      buttons.forEach(function (b, i) {
        var o = spec.options[i];
        b.disabled = true;
        b.classList.remove("is-picked");
        if (o.correct) b.classList.add("is-right");
        else if (chosen[i]) b.classList.add("is-wrong");
        if (!!o.correct !== !!chosen[i]) allRight = false;
        if (chosen[i] || o.correct) b._why.hidden = false;
      });
      api.record(allRight);
      feedback.replaceChildren(h("div", { class: "bk-feedback " + (allRight ? "is-good" : "is-bad") },
        h("b", { text: allRight ? "Right." : "Not quite." }),
        spec.explain ? mdEl("p", spec.explain) : null),
        h("div", { class: "bk-row" }, h("button", { class: "bk-btn bk-btn-quiet", type: "button", text: "Try again", onclick: reset })));
    }
    function reset() {
      chosen = {};
      buttons.forEach(function (b) { b.disabled = false; b.className = "bk-option"; b._why.hidden = true; b.setAttribute("aria-pressed", "false"); });
      feedback.replaceChildren(check);
    }
    var check = multi ? h("div", { class: "bk-row" }, h("button", { class: "bk-btn bk-btn-turn", type: "button", text: "Check", onclick: reveal })) : "";
    append(el, [mdEl("p", spec.question, "bk-question"), h("div", { class: "bk-options" }, buttons), feedback]);
    feedback.replaceChildren(check);
  };

  W.predict = function (el, spec, api) {
    var inp = spec.input;
    append(el, mdEl("p", spec.question, "bk-question"));
    var result = h("div");
    var unit = inp.unit || "";
    if (inp.kind === "slider") {
      var mid = inp.value !== undefined ? inp.value : (inp.min + inp.max) / 2;
      var id = "pr-" + api.slideId + "-" + api.mode;
      var range = h("input", { type: "range", id: id, min: inp.min, max: inp.max, step: inp.step || 1, value: mid });
      var val = h("span", { class: "bk-slider-value", text: fmt(mid, inp.decimals) + unit });
      range.addEventListener("input", function () { val.textContent = fmt(+range.value, inp.decimals) + unit; });
      var lock = h("button", { class: "bk-btn bk-btn-turn", type: "button", text: "Lock in my guess" });
      append(el, h("div", { class: "bk-slider" },
        h("div", { class: "bk-slider-head" }, h("label", { for: id, text: inp.label || "Your guess" }), val),
        range, h("div", { class: "bk-scale-ends" }, h("span", { text: fmt(inp.min, inp.decimals) + unit }), h("span", { text: fmt(inp.max, inp.decimals) + unit }))));
      append(el, h("div", { class: "bk-row" }, lock), result);
      lock.addEventListener("click", function () {
        var guess = +range.value, ans = spec.answer, span = inp.max - inp.min;
        var tol = spec.tolerance !== undefined ? spec.tolerance : span * 0.1;
        var close = Math.abs(guess - ans) <= tol;
        range.disabled = true; lock.disabled = true;
        api.record(close);
        function pos(v) { return ((v - inp.min) / span) * 100 + "%"; }
        result.replaceChildren(
          h("div", { class: "bk-scale" }, h("div", { class: "bk-scale-line" }),
            h("div", { class: "bk-scale-mark is-guess", style: { left: pos(guess) } }, h("span", { text: "you " + fmt(guess, inp.decimals) + unit }), h("i")),
            h("div", { class: "bk-scale-mark is-answer", style: { left: pos(ans), top: "26px" } }, h("i", { style: { marginTop: "-9px" } }), h("span", { text: "book " + fmt(ans, inp.decimals) + unit }))),
          h("div", { class: "bk-feedback " + (close ? "is-good" : "is-neutral") },
            h("b", { text: close ? "Close." : "The book says " + fmt(ans, inp.decimals) + unit + "." }),
            mdEl("p", spec.reveal)));
      });
    } else {
      var opts = inp.options.map(function (o) {
        var b = h("button", { class: "bk-option", type: "button" }, h("span", { class: "bk-option-key", text: "·" }), mdEl("span", o));
        b.addEventListener("click", function () {
          var ok = o === spec.answer;
          opts.forEach(function (x, i) {
            x.disabled = true;
            if (inp.options[i] === spec.answer) x.classList.add("is-right");
            else if (x === b) x.classList.add("is-wrong");
          });
          api.record(ok);
          result.replaceChildren(h("div", { class: "bk-feedback " + (ok ? "is-good" : "is-neutral") },
            h("b", { text: ok ? "Good call." : "The book found: " + spec.answer }), mdEl("p", spec.reveal)));
        });
        return b;
      });
      append(el, h("div", { class: "bk-options" }, opts), result);
    }
  };

  W.steps = function (el, spec, api) {
    var i = 0;
    var track = h("div", { class: "bk-steps-track", role: "tablist" });
    var view = h("div", { class: "bk-step", "aria-live": "polite" });
    var prev = h("button", { class: "bk-btn", type: "button", html: ICON.prev + "Back", "aria-label": "Previous step" });
    var next = h("button", { class: "bk-btn bk-btn-turn", type: "button", html: "Next" + ICON.next, "aria-label": "Next step" });
    var dots = spec.steps.map(function (st, k) {
      var d = h("button", { type: "button", "aria-label": "Step " + (k + 1) + ": " + st.title, onclick: function () { go(k); } });
      track.appendChild(d); return d;
    });
    function go(k) {
      i = Math.max(0, Math.min(spec.steps.length - 1, k));
      var st = spec.steps[i];
      var fig = st.figure ? renderFigure({ key: st.figure.key, ref: st.figure.ref, caption: st.figure.caption }, { mode: "study", builds: [] }) : null;
      view.replaceChildren();
      append(view, [h("span", { class: "bk-step-n", text: "Step " + (i + 1) + " of " + spec.steps.length }), mdEl("h3", st.title), st.text ? mdEl("p", st.text) : null, fig]);
      dots.forEach(function (d, k2) { d.classList.toggle("is-done", k2 <= i); });
      prev.disabled = i === 0; next.disabled = i === spec.steps.length - 1;
    }
    prev.addEventListener("click", function () { go(i - 1); });
    next.addEventListener("click", function () { go(i + 1); });
    api.onNext(function () { if (i < spec.steps.length - 1) { go(i + 1); return true; } return false; });
    append(el, h("div", { class: "bk-steps" }, track, view, h("div", { class: "bk-row" }, prev, next)));
    go(0);
  };

  W.flashcards = function (el, spec, api) {
    var i = 0, flipped = false;
    var front = h("div", { class: "bk-flash-face is-front" });
    var back = h("div", { class: "bk-flash-face is-back" });
    var card = h("button", { class: "bk-flash", type: "button", "aria-label": "Flip card" }, front, back);
    var count = h("span", { class: "bk-flash-count" });
    function show() {
      var c = spec.cards[i];
      front.innerHTML = md(c.front); back.innerHTML = md(c.back);
      card.classList.toggle("is-flipped", flipped);
      count.textContent = "Card " + (i + 1) + " of " + spec.cards.length + (flipped ? "" : " · tap to flip");
    }
    card.addEventListener("click", function () { flipped = !flipped; show(); });
    var prev = h("button", { class: "bk-btn", type: "button", html: ICON.prev + "Prev", onclick: function () { i = (i - 1 + spec.cards.length) % spec.cards.length; flipped = false; show(); } });
    var next = h("button", { class: "bk-btn", type: "button", html: "Next" + ICON.next, onclick: function () { i = (i + 1) % spec.cards.length; flipped = false; show(); } });
    api.onNext(function () {
      if (!flipped) { flipped = true; show(); return true; }
      if (i < spec.cards.length - 1) { i++; flipped = false; show(); return true; }
      return false;
    });
    append(el, h("div", { class: "bk-card3d" }, card), h("div", { class: "bk-row" }, prev, next, count));
    show();
  };

  W.match = function (el, spec, api) {
    var lefts = spec.pairs.map(function (p, i) { return { i: i, text: p.left }; });
    var rights = shuffle(spec.pairs.map(function (p, i) { return { i: i, text: p.right }; }), api.slideId);
    var active = null, done = 0, misses = 0;
    var status = h("div");
    var L = lefts.map(function (x) {
      var b = h("button", { class: "bk-tile", type: "button", html: md(x.text) });
      b.addEventListener("click", function () {
        if (b.disabled) return;
        L.forEach(function (o) { o.classList.remove("is-active"); });
        b.classList.add("is-active"); active = x.i;
      });
      return b;
    });
    var R = rights.map(function (x) {
      var b = h("button", { class: "bk-tile", type: "button", html: md(x.text) });
      b.addEventListener("click", function () {
        if (b.disabled || active === null) return;
        if (x.i === active) {
          b.classList.add("is-done"); b.disabled = true;
          L[active].classList.remove("is-active"); L[active].classList.add("is-done"); L[active].disabled = true;
          b.insertAdjacentHTML("afterbegin", '<span class="bk-tag">' + (active + 1) + "</span>");
          L[active].insertAdjacentHTML("afterbegin", '<span class="bk-tag">' + (active + 1) + "</span>");
          active = null; done++;
          if (done === spec.pairs.length) {
            api.record(misses === 0);
            status.replaceChildren(h("div", { class: "bk-feedback is-good" }, h("b", { text: misses ? "All matched (" + misses + " miss" + (misses > 1 ? "es" : "") + ")." : "All matched, no misses." }), spec.explain ? mdEl("p", spec.explain) : null));
          }
        } else {
          misses++;
          b.classList.add("is-miss");
          setTimeout(function () { b.classList.remove("is-miss"); }, 600);
        }
      });
      return b;
    });
    if (spec.prompt) append(el, mdEl("p", spec.prompt, "bk-question"));
    append(el, h("div", { class: "bk-match" }, h("div", { class: "bk-match-col" }, L), h("div", { class: "bk-match-col" }, R)), status,
      h("p", { class: "bk-hint", text: "Pick an item on the left, then its match on the right." }));
  };

  W.sort = function (el, spec, api) {
    var items = shuffle(spec.items.map(function (it, i) { return Object.assign({ i: i }, it); }), api.slideId);
    var place = {}, active = null;
    var pool = h("div", { class: "bk-sort-pool", "aria-label": "Unsorted items" });
    var result = h("div");
    var bucketEls = {};
    var pills = {};
    items.forEach(function (it) {
      var p = h("button", { class: "bk-pill", type: "button", html: md(it.text) });
      p.addEventListener("click", function () {
        if (p.disabled) return;
        if (place[it.i] !== undefined) { delete place[it.i]; pool.appendChild(p); active = null; layout(); return; }
        Object.keys(pills).forEach(function (k) { pills[k].classList.remove("is-active"); });
        p.classList.add("is-active"); active = it.i; layout();
      });
      pills[it.i] = p; pool.appendChild(p);
    });
    var buckets = h("div", { class: "bk-sort-buckets" }, spec.buckets.map(function (name) {
      var list = h("div", { class: "bk-options" });
      var b = h("div", { class: "bk-bucket", role: "button", tabindex: "0", "aria-label": "Put the selected item in " + name }, mdEl("h4", name), list);
      function drop() {
        if (active === null) return;
        place[active] = name; list.appendChild(pills[active]); pills[active].classList.remove("is-active"); active = null; layout();
      }
      b.addEventListener("click", function (e) { if (e.target.closest(".bk-pill")) return; drop(); });
      b.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); drop(); } });
      bucketEls[name] = b;
      return b;
    }));
    var check = h("button", { class: "bk-btn bk-btn-turn", type: "button", text: "Check", disabled: true });
    function layout() {
      var placed = Object.keys(place).length;
      check.disabled = placed < items.length;
      Object.keys(bucketEls).forEach(function (n) { bucketEls[n].classList.toggle("is-ready", active !== null); });
      pool.hidden = placed === items.length;
    }
    check.addEventListener("click", function () {
      var wrong = [];
      items.forEach(function (it) {
        var ok = place[it.i] === it.bucket;
        pills[it.i].classList.add(ok ? "is-right" : "is-wrong"); pills[it.i].disabled = true;
        if (!ok) wrong.push(it);
      });
      check.disabled = true;
      api.record(wrong.length === 0);
      result.replaceChildren(h("div", { class: "bk-feedback " + (wrong.length ? "is-bad" : "is-good") },
        h("b", { text: wrong.length ? wrong.length + " misplaced." : "All sorted correctly." }),
        wrong.map(function (it) { return mdEl("p", "**" + it.text + "** → " + it.bucket + (it.why ? ". " + it.why : "")); }),
        spec.explain ? mdEl("p", spec.explain) : null));
    });
    if (spec.prompt) append(el, mdEl("p", spec.prompt, "bk-question"));
    append(el, pool, buckets, h("div", { class: "bk-row" }, check), result);
    layout();
  };

  var MATH_NAMES = ["min", "max", "abs", "pow", "sqrt", "exp", "log", "round", "floor", "ceil", "sign", "clamp", "PI", "E"];
  var MATH_VALUES = [Math.min, Math.max, Math.abs, Math.pow, Math.sqrt, Math.exp, Math.log, Math.round, Math.floor, Math.ceil, Math.sign,
    function (v, a, b) { return Math.min(b, Math.max(a, v)); }, Math.PI, Math.E];
  function compile(expr, names) {
    var fn = new Function(names.concat(MATH_NAMES).join(","), '"use strict"; return (' + expr + ");");
    return function (vals) { return fn.apply(null, vals.concat(MATH_VALUES)); };
  }

  W.explore = function (el, spec, api) {
    var names = spec.params.map(function (p) { return p.id; });
    var values = spec.params.map(function (p) { return p.value; });
    var outs = spec.outputs.map(function (o) { return { o: o, f: compile(o.expr, names), el: h("b") }; });
    var verdicts = (spec.verdicts || []).map(function (v) { return { v: v, f: compile(v.when, names) }; });
    var verdictEl = h("div", { class: "bk-verdict", "aria-live": "polite" });
    var plotEl = h("div");
    var plotLines = spec.plot ? spec.plot.y.map(function (y) { return { y: y, f: compile(y.expr, names) }; }) : [];
    var controls = spec.params.map(function (p, k) {
      var id = "ex-" + api.slideId + "-" + p.id + "-" + api.mode;
      var r = h("input", { type: "range", id: id, min: p.min, max: p.max, step: p.step || (p.max - p.min) / 100, value: p.value });
      var v = h("span", { class: "bk-slider-value", text: fmt(p.value, p.decimals) + (p.unit || "") });
      r.addEventListener("input", function () { values[k] = +r.value; v.textContent = fmt(values[k], p.decimals) + (p.unit || ""); update(); });
      return h("div", { class: "bk-slider" }, h("div", { class: "bk-slider-head" }, h("label", { for: id, html: md(p.label || p.id) }), v), r);
    });
    function update() {
      outs.forEach(function (x) {
        var y = x.f(values);
        x.el.textContent = (x.o.prefix || "") + (typeof y === "number" ? fmt(y, x.o.decimals) : String(y)) + (x.o.unit || "");
      });
      var hit = null;
      for (var i = 0; i < verdicts.length; i++) { if (verdicts[i].f(values)) { hit = verdicts[i].v.text; break; } }
      verdictEl.hidden = !hit; verdictEl.innerHTML = hit ? md(hit) : "";
      if (spec.plot) drawPlot();
    }
    function drawPlot() {
      var pi = names.indexOf(spec.plot.over), p = spec.params[pi];
      var lo = spec.plot.min !== undefined ? spec.plot.min : p.min, hi = spec.plot.max !== undefined ? spec.plot.max : p.max;
      var n = 48, xs = [];
      for (var i = 0; i <= n; i++) xs.push(lo + ((hi - lo) * i) / n);
      var series = plotLines.map(function (pl) {
        return {
          name: pl.y.name, points: xs.map(function (x) {
            var vals = values.slice(); vals[pi] = x; return [x, pl.f(vals)];
          }),
        };
      });
      drawChart(plotEl, {
        chart: "line", smooth: true, series: series, marker: values[pi],
        x: { label: spec.plot.xLabel || stripMd(p.label || p.id) }, y: { label: spec.plot.yLabel || "", min: spec.plot.yMin, max: spec.plot.yMax },
        hideTable: true,
      });
    }
    // Without a plot the controls get the full width: pair the sliders up
    // rather than stretching each 0-5 range across the whole stage.
    var many = !spec.plot && controls.length >= 3;
    // Three or more sliders outgrow the plot beside them, so the verdict
    // reads the plot from underneath instead of lengthening their column.
    var under = spec.plot && controls.length >= 3;
    var sliders = h("div", { class: "bk-sliders" + (many ? " is-paired" : "") }, controls);
    var left = h("div", { class: "bk-explore-controls" }, sliders, h("div", { class: "bk-outputs" }, outs.map(function (x) {
      return h("div", { class: "bk-output" }, h("span", { html: md(x.o.label) }), x.el);
    })), under ? null : verdictEl);
    var right = spec.plot ? h("div", { class: "bk-explore-plot" }, plotEl, under ? verdictEl : null) : null;
    append(el, h("div", { class: "bk-explore" + (spec.plot ? "" : " is-single") }, left, right));
    if (spec.note) append(el, mdEl("p", spec.note, "bk-chart-note"));
    update();
  };

  function stripMd(s) { return String(s).replace(/[*`$\\]/g, ""); }

  W.table = function (el, spec) {
    var numeric = spec.columns.map(function (_, ci) {
      return spec.rows.every(function (r) { return typeof r[ci] === "number" || /^[-−]?[\d.,]+%?$/.test(String(r[ci]).trim()) || r[ci] === "" || r[ci] === null; });
    });
    var table = h("table", { class: "bk-table" },
      spec.note ? h("caption", { html: md(spec.note) }) : null,
      h("thead", null, h("tr", null, spec.columns.map(function (c, ci) { return h("th", { class: numeric[ci] ? "is-num" : null, html: md(c) }); }))),
      h("tbody", null, spec.rows.map(function (r, ri) {
        return h("tr", { class: spec.highlight === ri ? "is-hl" : null }, r.map(function (c, ci) {
          return h("td", { class: numeric[ci] ? "is-num" : null, html: typeof c === "number" ? fmt(c, spec.decimals) : md(c === null ? "–" : c) });
        }));
      })));
    append(el, h("div", { class: "bk-table-wrap" }, table));
  };

  W.chart = function (el, spec) { drawChart(el, spec); };

  // ---------------------------------------------------------------------
  // Charts: bar / line / scatter in plain SVG, sized to a 720-wide viewBox
  // so present mode scales type with the chart.
  // ---------------------------------------------------------------------

  var tip = null;
  function showTip(evt, html) {
    if (!tip) { tip = h("div", { class: "bk-tip", role: "status" }); document.body.appendChild(tip); }
    tip.innerHTML = html; tip.hidden = false;
    var x = evt.clientX + 14, y = evt.clientY + 14;
    var r = tip.getBoundingClientRect();
    if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - 14;
    if (y + r.height > innerHeight - 8) y = evt.clientY - r.height - 14;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  function hideTip() { if (tip) tip.hidden = true; }

  function niceTicks(lo, hi, count) {
    if (lo === hi) { hi = lo + 1; }
    var span = hi - lo, step = Math.pow(10, Math.floor(Math.log10(span / count)));
    var err = (count * step) / span;
    if (err <= 0.15) step *= 10; else if (err <= 0.35) step *= 5; else if (err <= 0.75) step *= 2;
    var start = Math.floor(lo / step) * step, end = Math.ceil(hi / step) * step, ticks = [];
    for (var v = start; v <= end + step / 2; v += step) ticks.push(+v.toFixed(10));
    return ticks;
  }
  function wrapLabel(text, maxChars) {
    var words = String(text).split(/\s+/), lines = [""];
    words.forEach(function (w) {
      var cur = lines[lines.length - 1];
      if ((cur + " " + w).trim().length > maxChars && cur) lines.push(w); else lines[lines.length - 1] = (cur + " " + w).trim();
    });
    if (lines.length > 2) { lines = [lines[0], lines.slice(1).join(" ")]; if (lines[1].length > maxChars) lines[1] = lines[1].slice(0, maxChars - 1) + "…"; }
    return lines;
  }
  function seriesColor(i) { return "var(--series-" + ((i % 4) + 1) + ")"; }

  var clipCount = 0;
  function drawChart(container, spec) {
    container.replaceChildren();
    var kind = spec.chart || "bar";
    var series = spec.series.map(function (s, i) { return Object.assign({ idx: i, on: true }, s); });
    var unit = (spec.y && spec.y.unit) || "";
    var wrap = h("div", { class: "bk-chart" });
    var plot = h("div");
    var legend = series.length > 1 ? h("div", { class: "bk-legend" }) : null;
    var table = h("div", { hidden: true });
    if (legend) series.forEach(function (s) {
      var b = h("button", { type: "button", "aria-pressed": "true" }, h("i", { style: { background: seriesColor(s.idx) } }), s.name);
      b.addEventListener("click", function () {
        if (series.filter(function (x) { return x.on; }).length === 1 && s.on) return;
        s.on = !s.on; b.setAttribute("aria-pressed", String(s.on)); render();
      });
      legend.appendChild(b);
    });
    append(wrap, [legend, plot]);
    if (spec.note) append(wrap, mdEl("p", spec.note, "bk-chart-note"));
    if (spec.derived) append(wrap, mdEl("p", "Computed from the book's data: " + spec.derived, "bk-derived"));
    if (!spec.hideTable) {
      var tbtn = h("button", { class: "bk-btn bk-btn-quiet", type: "button", text: "Show data" });
      tbtn.addEventListener("click", function () { table.hidden = !table.hidden; tbtn.textContent = table.hidden ? "Show data" : "Hide data"; });
      append(wrap, [h("div", { class: "bk-row" }, tbtn), table]);
      buildTable();
    }
    container.appendChild(wrap);
    container._redraw = render;
    render();

    function buildTable() {
      var cols, rows;
      if (kind === "scatter") {
        cols = ["Series", (spec.x && spec.x.label) || "x", (spec.y && spec.y.label) || "y", "Label"];
        rows = [];
        series.forEach(function (s) { s.points.forEach(function (p) { rows.push([s.name, p[0], p[1], p[2] || ""]); }); });
      } else {
        cols = [(spec.x && spec.x.label) || ""].concat(series.map(function (s) { return s.name; }));
        rows = spec.x.values.map(function (xv, i) { return [xv].concat(series.map(function (s) { return s.values[i]; })); });
      }
      W.table(table, { columns: cols, rows: rows });
    }

    function render() {
      plot.replaceChildren();
      // The viewBox follows the rendered width, so chart text keeps a
      // readable size (12 units ≈ 12.5px studying, 20px on the stage) and a
      // narrow chart gets narrower bands rather than smaller type. Before the
      // chart is in the page there is no width yet; it is redrawn on mount.
      var px = container.clientWidth;
      var font = container.closest(".bk-canvas") ? 20 : 12.5;
      var Wd = px ? Math.max(300, Math.min(960, Math.round((px * 12) / font))) : 720;
      var Ht = container._fitHt || spec.height || 340, m = { l: 58, r: 18, t: 18, b: kind === "bar" ? 58 : 48 };
      container._units = { ht: Ht, scale: px ? px / Wd : 1 };
      // Bar labels that can't fit their band even when wrapped and shrunk
      // are drawn slanted, with the bottom margin grown to hold them.
      var slant = false;
      if (kind === "bar") {
        var band0 = (Wd - m.l - m.r) / spec.x.values.length;
        var widest = Math.max.apply(null, spec.x.values.map(function (c) {
          return Math.max.apply(null, wrapLabel(c, Math.max(6, Math.floor(band0 / 7))).map(function (ln) { return ln.length; }));
        }));
        if (widest * 0.6 * 9 > band0 - 4) {
          slant = true;
          var longestLabel = Math.min(18, Math.max.apply(null, spec.x.values.map(function (c) { return String(c).length; })));
          m.b = 34 + Math.ceil(longestLabel * 0.6 * 11 * 0.64);
        }
      }
      var on = series.filter(function (s) { return s.on; });
      var ys = [], xsNum = [];
      on.forEach(function (s) {
        if (kind === "scatter") s.points.forEach(function (p) { xsNum.push(p[0]); ys.push(p[1]); });
        else if (s.values) s.values.forEach(function (v) { if (typeof v === "number") ys.push(v); });
      });
      if (kind === "line") (spec.x.values || []).forEach(function (v) { if (typeof v === "number") xsNum.push(v); });
      if (kind === "line" && !spec.x.values) on.forEach(function (s) { (s.points || []).forEach(function (p) { xsNum.push(p[0]); ys.push(p[1]); }); });
      var yMin = spec.y && spec.y.min !== undefined ? spec.y.min : Math.min(0, Math.min.apply(null, ys));
      var yMax = spec.y && spec.y.max !== undefined ? spec.y.max : Math.max.apply(null, ys);
      var yt = niceTicks(yMin, yMax, 5);
      yMin = Math.min(yMin, yt[0]); yMax = Math.max(yMax, yt[yt.length - 1]);
      var ih = Ht - m.t - m.b, iw = Wd - m.l - m.r;
      function Y(v) { return m.t + ih - ((v - yMin) / (yMax - yMin)) * ih; }
      var root = svg("svg", { viewBox: "0 0 " + Wd + " " + Ht, role: "img", "aria-label": spec.title || spec.caption || "Chart" });
      yt.forEach(function (t) {
        root.appendChild(svg("line", { x1: m.l, x2: Wd - m.r, y1: Y(t), y2: Y(t), class: t === 0 ? "bk-base" : "bk-grid" }));
        root.appendChild(svg("text", { x: m.l - 8, y: Y(t) + 4, "text-anchor": "end", "font-size": 12 }, fmt(t) + (unit && t === yt[yt.length - 1] ? unit : "")));
      });
      if (spec.y && spec.y.label) root.appendChild(svg("text", { x: 14, y: m.t + ih / 2, transform: "rotate(-90 14 " + (m.t + ih / 2) + ")", "text-anchor": "middle", "font-size": 12, class: "bk-axis-title" }, spec.y.label));
      if (spec.x && spec.x.label) root.appendChild(svg("text", { x: m.l + iw / 2, y: Ht - 6, "text-anchor": "middle", "font-size": 12, class: "bk-axis-title" }, spec.x.label));

      if (kind === "bar") {
        var cats = spec.x.values, band = iw / cats.length;
        var bw = Math.min(28, (band * 0.72) / on.length);
        cats.forEach(function (c, ci) {
          var cx = m.l + band * ci + band / 2;
          if (slant) {
            var lbl = String(c).length > 18 ? String(c).slice(0, 17) + "…" : String(c), ly = m.t + ih + 14;
            root.appendChild(svg("text", { x: cx + 4, y: ly, "text-anchor": "end", "font-size": 11, transform: "rotate(-40 " + (cx + 4) + " " + ly + ")" }, lbl));
          } else {
            var lines = wrapLabel(c, Math.max(6, Math.floor(band / 7)));
            var longest = Math.max.apply(null, lines.map(function (ln) { return ln.length; }));
            var lfs = Math.max(9, Math.min(12, (band - 4) / (longest * 0.6)));
            lines.forEach(function (ln, li) { root.appendChild(svg("text", { x: cx, y: m.t + ih + 18 + li * 14, "text-anchor": "middle", "font-size": lfs }, ln)); });
          }
          on.forEach(function (s, si) {
            var v = s.values[ci]; if (typeof v !== "number") return;
            var x = cx - (on.length * bw) / 2 + si * bw + 1, w = bw - 2;
            var y0 = Y(Math.max(0, yMin)), y1 = Y(v), top = Math.min(y0, y1), hgt = Math.max(1, Math.abs(y0 - y1));
            var r = Math.min(4, hgt, w / 2), up = v >= 0;
            var d = up
              ? "M" + x + "," + (top + hgt) + "V" + (top + r) + "Q" + x + "," + top + " " + (x + r) + "," + top + "H" + (x + w - r) + "Q" + (x + w) + "," + top + " " + (x + w) + "," + (top + r) + "V" + (top + hgt) + "Z"
              : "M" + x + "," + top + "V" + (top + hgt - r) + "Q" + x + "," + (top + hgt) + " " + (x + r) + "," + (top + hgt) + "H" + (x + w - r) + "Q" + (x + w) + "," + (top + hgt) + " " + (x + w) + "," + (top + hgt - r) + "V" + top + "Z";
            var dim = spec.highlight !== undefined && spec.highlight !== ci;
            var bar = svg("path", { d: d, fill: seriesColor(s.idx), opacity: dim ? 0.35 : 1 });
            root.appendChild(bar);
            if (spec.labels) root.appendChild(svg("text", { x: x + w / 2, y: up ? top - 5 : top + hgt + 13, "text-anchor": "middle", "font-size": 11 }, fmt(v) + unit));
            var hit = svg("rect", { x: x - 2, y: m.t, width: w + 4, height: ih, class: "bk-hit", tabindex: "0" });
            var tipHtml = "<div>" + esc(s.name) + "</div><div>" + esc(c) + ": <b>" + fmt(v) + esc(unit) + "</b></div>";
            hit.addEventListener("mousemove", function (e) { showTip(e, tipHtml); });
            hit.addEventListener("mouseleave", hideTip);
            hit.addEventListener("focus", function () { var r2 = hit.getBoundingClientRect(); showTip({ clientX: r2.right, clientY: r2.top }, tipHtml); });
            hit.addEventListener("blur", hideTip);
            root.appendChild(hit);
          });
        });
      } else {
        var xMin = spec.x && spec.x.min !== undefined ? spec.x.min : Math.min.apply(null, xsNum);
        var xMax = spec.x && spec.x.max !== undefined ? spec.x.max : Math.max.apply(null, xsNum);
        var xt = niceTicks(xMin, xMax, 6);
        if (spec.x.min === undefined) xMin = Math.min(xMin, xt[0]);
        if (spec.x.max === undefined) xMax = Math.max(xMax, xt[xt.length - 1]);
        var X = function (v) { return m.l + ((v - xMin) / (xMax - xMin || 1)) * iw; };
        xt.forEach(function (t) { if (t >= xMin && t <= xMax) root.appendChild(svg("text", { x: X(t), y: m.t + ih + 18, "text-anchor": "middle", "font-size": 12 }, fmt(t))); });
        if (spec.marker !== undefined) root.appendChild(svg("line", { x1: X(spec.marker), x2: X(spec.marker), y1: m.t, y2: m.t + ih, stroke: "var(--turn-ink)", "stroke-width": 2 }));
        // A fixed y range (an explorable's yMax) cuts off whatever runs past
        // it; clip the series to the plot so a steep curve can't cross the
        // legend and the slide.
        var clipId = "bk-clip-" + (++clipCount), pad = 7;
        root.appendChild(svg("clipPath", { id: clipId }, svg("rect", { x: m.l - pad, y: m.t - pad, width: iw + 2 * pad, height: ih + 2 * pad })));
        var layer = svg("g", { "clip-path": "url(#" + clipId + ")" });
        root.appendChild(layer);
        on.forEach(function (s) {
          var pts = kind === "line" && spec.x.values ? spec.x.values.map(function (xv, i) { return [xv, s.values[i]]; }) : s.points;
          pts = pts.filter(function (p) { return typeof p[1] === "number" && isFinite(p[1]); });
          var color = seriesColor(s.idx);
          if (kind === "line") {
            layer.appendChild(svg("path", { d: pts.map(function (p, i) { return (i ? "L" : "M") + X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1); }).join(""), fill: "none", stroke: color, "stroke-width": 2.5, "stroke-linejoin": "round", "stroke-linecap": "round" }));
          }
          if (kind === "scatter" || !spec.smooth) pts.forEach(function (p) {
            var dot = svg("circle", { cx: X(p[0]), cy: Y(p[1]), r: kind === "scatter" ? 5.5 : 4.5, fill: color, stroke: "var(--surface)", "stroke-width": 2 });
            layer.appendChild(dot);
            if (p[2]) layer.appendChild(svg("text", { x: X(p[0]) + 8, y: Y(p[1]) - 7, "font-size": 11 }, p[2]));
            var tipHtml = "<div>" + esc(s.name) + (p[2] ? " · " + esc(p[2]) : "") + "</div><div>" + esc((spec.x && spec.x.label) || "x") + " " + fmt(p[0]) + " → <b>" + fmt(p[1]) + esc(unit) + "</b></div>";
            var hit = svg("circle", { cx: X(p[0]), cy: Y(p[1]), r: 12, class: "bk-hit" });
            hit.addEventListener("mousemove", function (e) { showTip(e, tipHtml); });
            hit.addEventListener("mouseleave", hideTip);
            layer.appendChild(hit);
          });
          if (spec.smooth && spec.marker !== undefined) {
            var best = pts.reduce(function (a, p) { return Math.abs(p[0] - spec.marker) < Math.abs(a[0] - spec.marker) ? p : a; }, pts[0]);
            if (best) layer.appendChild(svg("circle", { cx: X(best[0]), cy: Y(best[1]), r: 5, fill: color, stroke: "var(--surface)", "stroke-width": 2 }));
          }
        });
      }
      plot.appendChild(root);
    }
  }
  Bookery.chart = drawChart;

  // ---------------------------------------------------------------------
  // Study mode
  // ---------------------------------------------------------------------

  var meterFill, meterText, tocLinks = {};

  function updateMeter() {
    if (!meterFill) return;
    var seen = flat.filter(function (s) { return progress.seen[s.id]; }).length;
    meterFill.style.width = (flat.length ? (seen / flat.length) * 100 : 0) + "%";
    var checks = flat.filter(function (s) { return s.widget && RECALL[s.widget.type]; });
    var answered = checks.filter(function (s) { return s.id in progress.results; });
    var right = answered.filter(function (s) { return progress.results[s.id]; }).length;
    meterText.textContent = seen + " of " + flat.length + " read" + (checks.length ? " · " + right + " of " + checks.length + " right" : "");
    Object.keys(tocLinks).forEach(function (id) { tocLinks[id].classList.toggle("is-seen", !!progress.seen[id]); });
  }

  function buildStudy(root) {
    var presentBtn = h("button", { class: "bk-btn bk-btn-ink", type: "button", html: ICON.play + "Present", onclick: function () { openPresent(0); } });
    meterFill = h("div", { class: "bk-meter-fill" });
    meterText = h("span");
    var bar = h("header", { class: "bk-bar" }, h("div", { class: "bk-bar-inner" },
      h("div", { class: "bk-bar-title" }, h("b", { text: D.chapter.number + ". " + (L.title || D.chapter.title) }), h("span", { text: D.book.title + (D.book.author ? " · " + D.book.author : "") })),
      h("div", { class: "bk-meter" }, h("div", { class: "bk-meter-track" }, meterFill), meterText),
      presentBtn));

    var toc = h("nav", { class: "bk-toc", "aria-label": "Lesson contents" });
    var main = h("main", { class: "bk-main" });

    var pages = D.chapter.pages ? "pp. " + D.chapter.pages : null;
    var hero = h("section", { class: "bk-hero", "aria-label": "Lesson overview" },
      h("div", { class: "bk-hero-book", text: D.book.title + (D.book.author ? " · " + D.book.author : "") }),
      h("div", { class: "bk-hero-chapter", text: "Chapter " + D.chapter.number }),
      h("h1", { html: md(L.title || D.chapter.title) }),
      L.subtitle ? mdEl("p", L.subtitle, "bk-hero-sub") : null,
      h("div", { class: "bk-hero-meta" },
        pages ? h("span", { text: pages }) : null,
        h("span", { text: "about " + L.minutes + " minutes" }), h("span", { text: flat.length + " slides" })),
      h("div", { class: "bk-hero-actions" },
        h("button", { class: "bk-btn bk-btn-turn", type: "button", html: ICON.play + "Present from the start", onclick: function () { openPresent(0); } }),
        firstUnseen() > 0 ? h("button", { class: "bk-btn", type: "button", text: "Continue where you left off", onclick: function () { jump(flat[firstUnseen()].id); } }) : null),
      h("div", { class: "bk-hero-block" },
        h("div", { class: "bk-label", text: "By the end you can" }),
        h("ol", { class: "bk-objectives" }, L.objectives.map(function (o) { return mdEl("li", o); }))),
      D.sections.length ? h("div", { class: "bk-hero-block" },
        h("div", { class: "bk-label", text: "What this lesson does with the chapter" }),
        h("div", { class: "bk-map" }, D.sections.map(mapRow))) : null);
    append(main, hero);

    L.sections.forEach(function (sec, si) {
      var list = h("ol");
      append(toc, [h("h4", { text: sec.title }), list]);
      var head = h("div", { class: "bk-section-head", id: "sec-" + (si + 1) }, h("span", { class: "bk-label", text: "Part " + (si + 1) + " of " + L.sections.length }), mdEl("h2", sec.title));
      append(main, head);
      sec.slides.forEach(function (s) {
        var layout = layoutOf(s);
        if (layout === "title") return;
        if (layout === "section") {
          if (s.text) append(head, mdEl("p", s.text, "bk-text"));
          return;
        }
        var card = renderSlide(s, "study");
        card.classList.add("bk-card");
        if (isTurn(s)) card.classList.add("is-turn");
        card.id = s.id;
        append(main, card);
        var a = h("a", { href: "#" + s.id, class: isTurn(s) ? "is-turn" : null }, h("span", { class: "bk-dot" }), h("span", { html: md(s.title) }));
        a.addEventListener("click", function (e) { e.preventDefault(); jump(s.id); });
        tocLinks[s.id] = a;
        list.appendChild(h("li", null, a));
      });
    });

    append(main, buildRecap());
    root.replaceChildren(bar, h("div", { class: "bk-study" }, h("div", { class: "bk-study-grid" }, toc, main)));
    redrawCharts(main);
    var lastW = innerWidth, pending = null;
    window.addEventListener("resize", function () {
      if (innerWidth === lastW) return;
      lastW = innerWidth; clearTimeout(pending);
      pending = setTimeout(function () { redrawCharts(main); }, 150);
    });
    watchSeen();
    updateMeter();
  }

  function buildRecap() {
    var box = h("section", { class: "bk-recap", "aria-label": "Recap" }, h("div", { class: "bk-section-head" }, h("span", { class: "bk-label", text: "Recap" }), h("h2", { text: "What to keep" })));
    if (L.takeaways && L.takeaways.length) append(box, h("ul", { class: "bk-points" }, L.takeaways.map(function (t) { return mdEl("li", t); })));
    if (L.glossary && L.glossary.length) {
      append(box, h("div", { class: "bk-label", text: "Glossary" }));
      append(box, h("dl", { class: "bk-glossary" }, L.glossary.map(function (g) {
        return h("div", null, mdEl("dt", g.term), mdEl("dd", g.def), g.example ? mdEl("dd", g.example, "bk-glossary-ex") : null);
      })));
    }
    var skipped = D.sections.filter(function (s) { return s.status === "skipped"; });
    if (skipped.length) {
      append(box, h("div", { class: "bk-label", text: "Left out on purpose" }));
      append(box, h("div", { class: "bk-map" }, skipped.map(mapRow)));
    }
    append(box, h("p", { class: "bk-colophon",
      text: "Built by bookery from " + D.book.title + ". Every number and quotation on these slides was checked against the chapter's text; anything marked “Beyond the book” is not from it." }));
    return box;
  }

  // A line of the chapter's contents and what the lesson did with it.
  function mapRow(s) {
    var skipped = s.status === "skipped";
    return h("div", { class: "bk-map-row" },
      h("span", { class: "bk-where", text: s.label }),
      h("span", { class: "bk-map-title" }, s.title, skipped && s.reason ? h("span", { class: "bk-map-reason", text: s.reason }) : null),
      h("span", { class: "bk-status" + (skipped ? "" : " is-taught"), text: skipped ? "left out" : "taught" }));
  }

  function firstUnseen() {
    for (var i = 0; i < flat.length; i++) if (!progress.seen[flat[i].id]) return i;
    return 0;
  }

  function jump(id, instant) {
    var el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: instant ? "auto" : "smooth", block: "start" });
  }

  function watchSeen() {
    if (!("IntersectionObserver" in window)) return;
    var current = null;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        var id = e.target.id;
        markSeen(id);
        if (current && tocLinks[current]) tocLinks[current].classList.remove("is-here");
        current = id;
        if (tocLinks[id]) tocLinks[id].classList.add("is-here");
      });
    }, { rootMargin: "-30% 0px -55% 0px" });
    document.querySelectorAll(".bk-card").forEach(function (c) { io.observe(c); });
  }

  // ---------------------------------------------------------------------
  // Present mode
  // ---------------------------------------------------------------------

  var P = null;

  function deck() {
    var out = [];
    if (!flat.length || layoutOf(flat[0]) !== "title") {
      out.push({ id: "__title", layout: "title", title: L.title || D.chapter.title, text: L.subtitle || "" });
    }
    return out.concat(flat);
  }

  function deckIndex(id) {
    var slides = deck();
    for (var i = 0; i < slides.length; i++) if (slides[i].id === id) return i;
    return -1;
  }

  function openPresent(index) {
    if (P) return;
    var slides = deck();
    var canvas = h("div", { class: "bk-canvas" });
    var viewport = h("div", { class: "bk-viewport" }, canvas);
    var counter = h("span");
    var trackFill = h("div");
    var notes = h("div", { class: "bk-pnotes", hidden: true });
    var notesBtn = h("button", { class: "bk-btn", type: "button", text: "Notes", title: "Speaker notes (N)" });
    var overlay = h("div", { class: "bk-present", role: "dialog", "aria-label": "Presentation", tabindex: "-1" },
      viewport,
      h("div", { class: "bk-pbar" },
        h("button", { class: "bk-btn", type: "button", html: ICON.prev, "aria-label": "Previous slide", onclick: function () { go(P.i - 1); } }),
        h("button", { class: "bk-btn", type: "button", html: ICON.next, "aria-label": "Next slide", onclick: next }),
        counter, h("div", { class: "bk-ptrack" }, trackFill),
        h("span", { class: "bk-hint", text: "← → to move · N notes · F fullscreen · Esc exit" }),
        notesBtn,
        h("button", { class: "bk-btn", type: "button", html: ICON.full, "aria-label": "Fullscreen", onclick: fullscreen }),
        h("button", { class: "bk-btn", type: "button", html: ICON.close, "aria-label": "Exit presentation", onclick: closePresent })));
    viewport.appendChild(notes);
    notesBtn.addEventListener("click", toggleNotes);
    P = { slides: slides, i: 0, cache: {}, canvas: canvas, viewport: viewport, overlay: overlay, notes: notes, notesBtn: notesBtn, counter: counter, trackFill: trackFill };
    document.body.appendChild(overlay);
    document.documentElement.style.overflow = "hidden";
    window.addEventListener("resize", fit);
    document.addEventListener("keydown", onKey);
    var sx = null;
    viewport.addEventListener("touchstart", function (e) { sx = e.touches[0].clientX; }, { passive: true });
    viewport.addEventListener("touchend", function (e) {
      if (sx === null) return;
      var dx = e.changedTouches[0].clientX - sx; sx = null;
      if (Math.abs(dx) > 50) { if (dx < 0) next(); else go(P.i - 1); }
    });
    fit();
    go(index || 0);
    overlay.focus();
    // Slides fitted before the webfonts arrived were measured in the
    // fallback face; refit them once the real (taller) one is in.
    if (document.fonts && document.fonts.status !== "loaded") {
      document.fonts.ready.then(function () {
        if (!P) return;
        Object.keys(P.cache).forEach(function (id) { P.cache[id]._fitted = false; });
        var el = slideEl(P.i);
        fitSlide(el);
        el._fitted = true;
      });
    }
  }

  function closePresent() {
    if (!P) return;
    var id = P.slides[P.i] && P.slides[P.i].id;
    P.overlay.remove();
    document.documentElement.style.overflow = "";
    window.removeEventListener("resize", fit);
    document.removeEventListener("keydown", onKey);
    try { if (document.fullscreenElement) document.exitFullscreen(); } catch (e) { /* not in fullscreen */ }
    P = null;
    if (id && id !== "__title") jump(id);
  }

  function fit() {
    if (!P) return;
    var r = P.viewport.getBoundingClientRect();
    var s = Math.min(r.width / 1600, r.height / 900) * 0.96;
    P.canvas.style.transform = "translate(-50%, -50%) scale(" + s + ")";
  }

  // The stage is a fixed 1600x900. Once a slide is on it, charts are redrawn
  // at their real width; if the slide still runs past the stage, charts give
  // up height first (their type stays the same size), then the tallest
  // figure narrows (keeping its aspect, so its pins stay put), down to floors.
  function fitSlide(el) {
    redrawCharts(el);
    var charts = [].slice.call(el.querySelectorAll(".bk-chart")).map(function (w) { return w.parentNode; });
    var frames = [].slice.call(el.querySelectorAll(".bk-figure-frame"));
    for (var pass = 0; pass < 8; pass++) {
      var over = el.scrollHeight - el.clientHeight;
      if (over <= 4) return;
      var c = charts.filter(function (x) { return x._units && x._units.ht > 210; })[0];
      if (c) {
        c._fitHt = Math.max(210, Math.floor(c._units.ht - over / c._units.scale - 4));
        c._redraw();
        continue;
      }
      var img = frames.map(function (f) { return f.querySelector("img"); })
        .reduce(function (a, m) { return !a || m.offsetHeight > a.offsetHeight ? m : a; }, null);
      if (!img) return;
      var hgt = img.offsetHeight, wd = img.offsetWidth;
      var target = Math.max(hgt - over - 6, hgt * 0.45, 200);
      if (target >= hgt - 1) return;
      img.parentNode.style.maxWidth = Math.floor((wd * target) / hgt + 22) + "px";
    }
  }

  function redrawCharts(root) {
    root.querySelectorAll(".bk-chart").forEach(function (w) { if (w.parentNode._redraw) w.parentNode._redraw(); });
  }

  function slideEl(i) {
    var s = P.slides[i];
    if (!P.cache[s.id]) P.cache[s.id] = renderSlide(s, "present");
    return P.cache[s.id];
  }

  function go(i) {
    if (!P) return;
    i = Math.max(0, Math.min(P.slides.length - 1, i));
    P.i = i;
    var el = slideEl(i);
    P.canvas.replaceChildren(el);
    if (!el._fitted) { fitSlide(el); el._fitted = true; }
    el.scrollTop = 0;
    var s = P.slides[i];
    if (s.id !== "__title") markSeen(s.id);
    P.counter.textContent = i + 1 + " / " + P.slides.length;
    P.trackFill.style.width = ((i + 1) / P.slides.length) * 100 + "%";
    renderNotes();
  }

  function next() {
    if (!P) return;
    var el = slideEl(P.i);
    var builds = el._ctx ? el._ctx.builds : [];
    for (var k = 0; k < builds.length; k++) { if (builds[k]()) return; }
    go(P.i + 1);
  }

  function renderNotes() {
    var s = P.slides[P.i];
    var parts = [];
    if (s.notes) parts.push(h("div", null, h("div", { class: "bk-label", text: "Say" }), mdEl("p", s.notes)));
    if (s.deeper) parts.push(h("div", null, h("div", { class: "bk-label", text: "Go deeper" }), mdEl("p", s.deeper)));
    if (s.beyond) parts.push(h("div", null, h("div", { class: "bk-label", text: "Beyond the book" }), mdEl("p", s.beyond)));
    var passages = renderPassages(s);
    if (passages) parts.push(h("div", null, h("div", { class: "bk-label", text: "From the book · " + pagesOf(s) }), passages));
    if (!parts.length) parts.push(h("p", { class: "bk-hint", text: "No notes for this slide." }));
    P.notes.replaceChildren.apply(P.notes, parts);
  }

  function toggleNotes() {
    P.notes.hidden = !P.notes.hidden;
    P.notesBtn.classList.toggle("is-on", !P.notes.hidden);
  }

  function fullscreen() {
    try {
      if (document.fullscreenElement) document.exitFullscreen();
      else if (P.overlay.requestFullscreen) P.overlay.requestFullscreen().catch(function () { /* not allowed here */ });
    } catch (e) { /* fullscreen unavailable */ }
  }

  function onKey(e) {
    if (!P) return;
    var t = e.target;
    var typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT");
    if (typing && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
    if (e.key === "ArrowRight" || e.key === "PageDown" || (e.key === " " && !(t && t.tagName === "BUTTON"))) { e.preventDefault(); next(); }
    else if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); go(P.i - 1); }
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(P.slides.length - 1);
    else if (e.key === "n" || e.key === "N") toggleNotes();
    else if (e.key === "f" || e.key === "F") fullscreen();
    else if (e.key === "Escape" && !document.fullscreenElement) closePresent();
  }

  // ---------------------------------------------------------------------
  // Self-test: run by `bookery learn verify` in headless Chrome. Mounts
  // every slide in both modes and records anything that went wrong.
  // ---------------------------------------------------------------------

  function selftest() {
    var result = { ok: false, slides: flat.length, errors: errors, overflow: [], images: { total: 0, broken: [] }, math: null, hscroll: false };
    function done() {
      var imgs = document.images;
      result.images.total = imgs.length;
      for (var i = 0; i < imgs.length; i++) if (!imgs[i].complete || !imgs[i].naturalWidth) result.images.broken.push(imgs[i].alt || imgs[i].src.slice(0, 40));
      result.hscroll = document.documentElement.scrollWidth > window.innerWidth + 1;
      result.shown = customText();
      openPresent(0);
      for (var k = 0; k < P.slides.length; k++) {
        go(k);
        var el = P.canvas.firstChild;
        var over = el.scrollHeight - el.clientHeight;
        var wide = el.scrollWidth - el.clientWidth;
        if (over > 8 || wide > 8) result.overflow.push({ slide: P.slides[k].id, px: Math.max(over, wide) });
      }
      closePresent();
      result.dead = answerAll();
      result.math = sawMath ? (window.katex ? "rendered" : "katex unavailable") : "none";
      result.errors = errors;
      result.ok = !errors.length && !result.overflow.length && !result.images.broken.length && !result.hscroll && !result.dead.length;
      document.documentElement.setAttribute("data-selftest", JSON.stringify(result));
      // A runner showing the page in an iframe (to get a phone-width viewport) reads it from here.
      if (window.parent !== window) window.parent.postMessage({ bookerySelftest: result }, "*");
    }
    var fonts = document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve();
    Promise.race([fonts, new Promise(function (r) { setTimeout(r, 3000); })]).then(function () { setTimeout(done, 300); });
  }

  // What each custom widget shows a study-mode reader before they touch it,
  // for the cold-read transcript. Its props alone can mislead: a number may
  // be an index, and a label may be computed from several props. A widget
  // that steps on "next" (api.onNext) is stepped through, and each step adds
  // the lines it newly put on the page, after a "[next]" line. A form field
  // shows its value, not its text, so it reads as "[value]", or "[ ]" empty.
  // innerText runs a drawing's labels together ("write" + "and" on the next
  // line reads "writeand"), so each drawing stands in as its lines of text.
  function customText() {
    var out = {};
    function shown(el) { return getComputedStyle(el).visibility === "visible"; }
    function svgLines(svg) {
      var lines = [];
      svg.querySelectorAll("text").forEach(function (t) {
        if (!t.getClientRects().length || !shown(t)) return;
        var line = "";
        t.childNodes.forEach(function (n) {
          if (n.nodeType === 3) { line += n.textContent; return; }
          if (n.nodeName !== "tspan" || !shown(n)) return;
          var moves = n.hasAttribute("x") || n.hasAttribute("y") || parseFloat(n.getAttribute("dy") || 0);
          if (moves && line.trim()) { lines.push(line); line = ""; }
          line += n.textContent;
        });
        if (line.trim()) lines.push(line);
      });
      return lines;
    }
    function text(host) {
      var marks = [], drawings = [];
      host.querySelectorAll("input[type=text], input[type=number], input:not([type]), textarea, select").forEach(function (f) {
        if (!f.getClientRects().length) return;   // hidden: not on the page
        var m = h("span", { text: " [" + (f.value || " ") + "] " });
        f.after(m);
        marks.push(m);
      });
      host.querySelectorAll("svg").forEach(function (svg) {
        if (svg.parentElement.closest("svg") || !svg.getClientRects().length) return;
        var m = h("div", { text: svgLines(svg).join("\n") });
        m.style.whiteSpace = "pre-line";
        svg.after(m);
        marks.push(m);
        drawings.push([svg, svg.style.display]);
        svg.style.display = "none";
      });
      var t = host.innerText.replace(/[ \t]+/g, " ").replace(/\s*\n\s*/g, "\n").trim();
      marks.forEach(function (m) { m.remove(); });
      drawings.forEach(function (d) { d[0].style.display = d[1]; });
      return t;
    }
    flat.forEach(function (s) {
      if (!s.widget || s.widget.type !== "custom") return;
      var slide = document.getElementById(s.id), host = slide && slide.querySelector(".bk-custom");
      if (!host) return;
      var now = text(host), parts = [now];
      var art = host.closest(".bk-slide"), builds = art && art._ctx ? art._ctx.builds : [];
      builds.forEach(function (b) {
        for (var n = 0; n < 30 && b(); n++) {
          var before = now.split("\n");
          now = text(host);
          var fresh = now.split("\n").filter(function (line) { return before.indexOf(line) < 0; });
          if (fresh.length) parts.push("[next]\n" + fresh.join("\n"));
        }
      });
      out[s.id] = parts.join("\n");
    });
    return out;
  }

  // Answer every recall check in study mode, the way a reader would, and
  // expect feedback. A check that renders but can't be completed (a lost
  // bucket, a reveal that never shows) passes every other test.
  function answerAll() {
    var dead = [];
    flat.forEach(function (s) {
      var type = s.widget && s.widget.type;
      if (!RECALL[type]) return;
      var slide = document.getElementById(s.id), w = slide && slide.querySelector(".bk-widget");
      if (!w) { dead.push({ slide: s.id, type: type, reason: "not rendered" }); return; }
      var turn = w.querySelector(".bk-btn-turn");
      if (type === "quiz") {
        // Pick exactly the right answers: the reader who does must be told so.
        var opts = w.querySelectorAll(".bk-option");
        s.widget.options.forEach(function (o, i) { if (o.correct && opts[i]) opts[i].click(); });
        if (s.widget.multi && turn) turn.click();
        if (!w.querySelector(".bk-feedback")) dead.push({ slide: s.id, type: type, reason: "no feedback after answering" });
        else if (!w.querySelector(".bk-feedback.is-good")) dead.push({ slide: s.id, type: type, reason: "calls the right answer wrong" });
        return;
      }
      if (type === "predict" && !turn) {
        var opt = w.querySelector(".bk-option"); if (opt) opt.click();
      } else if (type === "predict") {
        turn.click();
      } else if (type === "sort") {
        var bucket = w.querySelector(".bk-bucket");
        w.querySelectorAll(".bk-sort-pool .bk-pill").forEach(function (p) { p.click(); if (bucket) bucket.click(); });
        if (turn) turn.click();
      } else if (type === "match") {
        var cols = w.querySelectorAll(".bk-match-col");
        if (cols.length === 2) cols[0].querySelectorAll(".bk-tile").forEach(function (l) {
          cols[1].querySelectorAll(".bk-tile").forEach(function (r) { if (!l.disabled && !r.disabled) { l.click(); r.click(); } });
        });
      }
      if (!w.querySelector(".bk-feedback")) dead.push({ slide: s.id, type: type, reason: "no feedback after answering" });
    });
    return dead;
  }

  // ---------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------

  Bookery.boot = function () {
    window.addEventListener("error", function (e) { errors.push("error: " + (e.message || e.type)); });
    window.addEventListener("unhandledrejection", function (e) { errors.push("rejection: " + (e.reason && e.reason.message ? e.reason.message : e.reason)); });
    var root = document.getElementById("bk-app");
    try {
      D = JSON.parse(document.getElementById("lesson-data").textContent);
      L = D.lesson;
      document.documentElement.lang = L.language || "en";
      (L.glossary || []).forEach(function (g) {
        glossary[g.term.toLowerCase()] = g.def.replace(/[*`]/g, "");
        terms[g.term.toLowerCase()] = g;
      });
      L.sections.forEach(function (sec) { sec.slides.forEach(function (s) { flat.push(s); bySlide[s.id] = s; }); });
      storeKey = "bookery:" + D.book.title + ":ch" + D.chapter.number;
      loadProgress();
      buildStudy(root);
    } catch (e) {
      errors.push("boot: " + (e && e.message ? e.message : e));
      root.replaceChildren(h("div", { class: "bk-error", text: "This lesson failed to load: " + (e && e.message ? e.message : e) }));
    }
    if (window.BOOKERY_SELFTEST) { selftest(); return; }
    // #present opens the deck, #present-<slide> opens it at that slide and
    // #<slide> scrolls study mode to it -- shareable links into a lesson.
    // The preview's "~built" suffix (for `bookery learn shot`) also plays
    // every build of the opening slide, so its figure notes show.
    var hash = (location.hash || "").slice(1).replace(/~built$/, "");
    if (hash === "present") openPresent(0);
    else if (hash.indexOf("present-") === 0) openPresent(Math.max(0, deckIndex(hash.slice(8))));
    else if (hash && bySlide[hash]) setTimeout(function () { jump(hash, true); }, 50);
    if (P && window.BOOKERY_BUILT) {
      var builds = slideEl(P.i)._ctx ? slideEl(P.i)._ctx.builds : [];
      builds.forEach(function (b) { for (var n = 0; n < 100 && b(); n++) { /* next build */ } });
    }
  };
})();
