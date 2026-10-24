"""The lesson spec: what Claude writes, and its structural rules.

A lesson is data, not a page. Claude writes `lessons/chNN/lesson.json`; the
shared runtime (`learn/runtime/`) renders it. Keeping authoring to a closed
vocabulary of slide parts and widget types is what makes a lesson checkable
at all: every string a reader sees sits in a known field, so the gates can
find each number, quote and citation without parsing free-form HTML.

The authoring reference for this vocabulary (with examples) lives in
`.claude/skills/learn/authoring.md`. Keep the two in sync.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1

RE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")

# Slide layouts the runtime knows. Most slides omit `layout` and get one
# inferred from their parts (widget > figure > stats > quote > columns >
# points > statement); these are for the slides that have no parts.
LAYOUTS = {"title", "section", "statement", "auto"}

WIDGET_TYPES = {
    "quiz",        # multiple choice with per-option feedback
    "predict",     # commit to a guess, then see the book's answer
    "chart",       # bar / line / scatter of data from the book
    "steps",       # click through a sequence (a game's moves, a proof)
    "flashcards",  # flip cards for recall
    "match",       # pair terms with their meaning
    "sort",        # classify items into buckets
    "explore",     # sliders driving formulas from the chapter
    "table",       # a small data table from the book
    "custom",      # a chapter-specific module in lessons/chNN/widgets/
}

# Widgets that count as the reader *doing* something (marked "Your turn"
# on the page). A chart or table is visual but not interactive in this
# sense, nor is a custom widget declared `"static": true` (a drawn figure).
ACTIVE_WIDGETS = {"quiz", "predict", "steps", "flashcards", "match", "sort", "explore", "custom"}
RECALL_WIDGETS = {"quiz", "predict", "match", "sort"}
# Widgets whose numbers state the book's data (and may be `transcribed`).
DATA_WIDGETS = {"chart", "table", "explore", "custom"}

CHART_KINDS = {"bar", "line", "scatter"}

# A slide's main part; at most one per slide.
MAIN_PARTS = ("widget", "figure", "stats", "quote", "columns")

# Keys whose numeric values configure a control rather than state a fact:
# a slider's range is not a claim about the book.
CONTROL_KEYS = {
    "min", "max", "step", "value", "seed", "rounds", "width", "height",
    "delay", "speed", "default", "decimals", "tolerance", "columns_span",
}

# Fields whose text is deliberately outside the book and labelled so in the
# page. They are exempt from the numbers/quotes gates -- the runtime renders
# them under a visible "Beyond the book" marker, never as the book's claim.
BEYOND_KEYS = {"beyond"}


@dataclass
class Problem:
    where: str
    message: str

    def as_dict(self) -> dict:
        return {"where": self.where, "message": self.message}


def iter_slides(lesson: dict):
    """Yield (section, slide) in reading order."""
    for section in lesson.get("sections") or []:
        for slide in section.get("slides") or []:
            yield section, slide


def slide_widget_type(slide: dict) -> str | None:
    w = slide.get("widget")
    return w.get("type") if isinstance(w, dict) else None


def is_active(slide: dict) -> bool:
    w = slide.get("widget")
    if not isinstance(w, dict) or w.get("type") not in ACTIVE_WIDGETS:
        return False
    return not (w["type"] == "custom" and w.get("static") is True)


def is_divider(slide: dict) -> bool:
    return slide.get("layout") in ("title", "section")


def _str(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _list_of_str(v) -> bool:
    return isinstance(v, list) and all(_str(x) for x in v)


def validate(lesson: dict, widgets_dir: Path | None = None) -> list[Problem]:
    """Structural problems only. Grounding, numbers, quotes, coverage and
    density are separate gates, so a report says which kind of wrong it is."""
    p: list[Problem] = []

    def bad(where: str, msg: str) -> None:
        p.append(Problem(where, msg))

    if lesson.get("schema") != SCHEMA_VERSION:
        bad("schema", f"expected \"schema\": {SCHEMA_VERSION}")
    for key in ("title", "subtitle"):
        if key in lesson and not _str(lesson[key]):
            bad(key, "must be a non-empty string")
    if not _list_of_str(lesson.get("objectives")) or not 2 <= len(lesson["objectives"]) <= 5:
        bad("objectives", "2-5 learning objectives, each a string")
    if not isinstance(lesson.get("minutes"), (int, float)) or not 5 <= lesson["minutes"] <= 90:
        bad("minutes", "estimated study time in minutes, 5-90")
    sections = lesson.get("sections")
    if not isinstance(sections, list) or not sections:
        bad("sections", "at least one section with slides")
        return p

    seen_ids: set[str] = set()
    for si, section in enumerate(sections):
        where = f"sections[{si}]"
        if not _str(section.get("title")):
            bad(where, "section needs a title")
        if not isinstance(section.get("slides"), list) or not section["slides"]:
            bad(where, "section needs at least one slide")
            continue
        for slide in section["slides"]:
            sid = slide.get("id")
            w = f"slide {sid!r}"
            if not isinstance(sid, str) or not RE_ID.match(sid):
                bad(w, "id must be lowercase kebab-case, at most 48 chars")
            elif sid in seen_ids:
                bad(w, "duplicate slide id")
            seen_ids.add(sid if isinstance(sid, str) else "")
            _validate_slide(slide, w, bad, widgets_dir)

    for i, item in enumerate(lesson.get("skipped") or []):
        if not _str(item.get("section")) or not _str(item.get("reason")):
            bad(f"skipped[{i}]", "needs \"section\" (its label) and a \"reason\"")
    for i, item in enumerate(lesson.get("glossary") or []):
        if not _str(item.get("term")) or not _str(item.get("def")):
            bad(f"glossary[{i}]", "needs \"term\" and \"def\"")
        # The slide that teaches a term shows its definition with this
        # example; a definition alone is how a reader nods and still can't
        # use the word.
        if not _str(item.get("example")):
            bad(f"glossary[{i}]", "needs an \"example\": the term at work in one concrete case")
        if "aliases" in item and not _list_of_str(item["aliases"]):
            bad(f"glossary[{i}]", "aliases must be a list of strings (other ways a slide names the term)")
    return p


def _validate_slide(slide: dict, w: str, bad, widgets_dir: Path | None) -> None:
    layout = slide.get("layout", "auto")
    if layout not in LAYOUTS:
        bad(w, f"unknown layout {layout!r}; one of {sorted(LAYOUTS)}")
    if not _str(slide.get("title")):
        bad(w, "every slide needs a title")
    if "points" in slide and not _list_of_str(slide["points"]):
        bad(w, "points must be a list of strings")
    if "sources" in slide and not _list_of_str(slide["sources"]):
        bad(w, "sources must be a list of block ids")
    if "teaches" in slide and not _list_of_str(slide["teaches"]):
        bad(w, "teaches must be a list of glossary terms")

    fig = slide.get("figure")
    if fig is not None:
        if not isinstance(fig, dict) or not _str(fig.get("ref")):
            bad(w, "figure needs a \"ref\" (e.g. \"fig 2.4\", \"tbl 2.2\" or a block id)")
        else:
            for i, note in enumerate(fig.get("notes") or []):
                ok = isinstance(note, dict) and _str(note.get("text"))
                ok = ok and all(
                    isinstance(note.get(k), (int, float)) and 0 <= note[k] <= 1 for k in ("x", "y")
                )
                if not ok:
                    bad(w, f"figure.notes[{i}] needs text and x/y as fractions 0-1 of the image")

    stats = slide.get("stats")
    if stats is not None:
        if not isinstance(stats, list) or not 1 <= len(stats) <= 3:
            bad(w, "stats is a list of 1-3 {value, label}")
        else:
            for i, s in enumerate(stats):
                if not isinstance(s, dict) or not _str(s.get("value")) or not _str(s.get("label")):
                    bad(w, f"stats[{i}] needs value and label strings")

    quote = slide.get("quote")
    if quote is not None and (not isinstance(quote, dict) or not _str(quote.get("text"))):
        bad(w, "quote needs text (verbatim from the book) and ideally cite")

    cols = slide.get("columns")
    if cols is not None:
        if not isinstance(cols, list) or not 2 <= len(cols) <= 3:
            bad(w, "columns is a list of 2-3 {title, text|points}")
        else:
            for i, c in enumerate(cols):
                if not isinstance(c, dict) or not _str(c.get("title")):
                    bad(w, f"columns[{i}] needs a title")

    widget = slide.get("widget")
    if widget is not None:
        _validate_widget(widget, w, bad, widgets_dir)

    # One idea per slide: a slide shows one main thing (plus a line of text
    # or a few points). Two would compete for the same screen in present mode.
    parts = [k for k in MAIN_PARTS if slide.get(k) is not None]
    if len(parts) > 1:
        bad(w, f"one main part per slide, found {parts}; split it into two slides")


def _validate_widget(widget: dict, w: str, bad, widgets_dir: Path | None) -> None:
    t = widget.get("type") if isinstance(widget, dict) else None
    if t not in WIDGET_TYPES:
        bad(w, f"unknown widget type {t!r}; one of {sorted(WIDGET_TYPES)}")
        return
    w = f"{w} widget {t}"

    # Values read off a book image (a screenshot of a spreadsheet, a chart
    # with no numbers in the text) can't be checked against the chapter's
    # text; the widget names the images so a reviewer -- and the reader --
    # can check them against the source.
    tr = widget.get("transcribed")
    if tr is not None and (not _list_of_str(tr) or not tr):
        bad(w, "transcribed: a list of the exhibit refs the numbers were read from")
    elif tr is not None and t not in DATA_WIDGETS:
        bad(w, f"transcribed only applies to widgets that carry data ({', '.join(sorted(DATA_WIDGETS))})")

    if t == "quiz":
        opts = widget.get("options")
        if not _str(widget.get("question")):
            bad(w, "needs a question")
        if not isinstance(opts, list) or not 2 <= len(opts) <= 6:
            bad(w, "2-6 options")
            return
        texts = [o.get("text") for o in opts if isinstance(o, dict)]
        if len(texts) != len(opts) or not all(_str(x) for x in texts):
            bad(w, "every option needs text")
        if len(set(texts)) != len(texts):
            bad(w, "options must be distinct")
        correct = sum(1 for o in opts if isinstance(o, dict) and o.get("correct") is True)
        if widget.get("multi"):
            if correct < 1:
                bad(w, "a multi-answer quiz needs at least one correct option")
        elif correct != 1:
            bad(w, f"exactly one option must have \"correct\": true (found {correct})")
        if not all(_str(o.get("why")) for o in opts if isinstance(o, dict)):
            bad(w, "every option needs a \"why\" -- feedback is the point of a quiz")

    elif t == "predict":
        if not _str(widget.get("question")):
            bad(w, "needs a question")
        inp = widget.get("input") or {}
        if inp.get("kind") == "slider":
            for k in ("min", "max"):
                if not isinstance(inp.get(k), (int, float)):
                    bad(w, f"slider input needs numeric {k}")
            if not isinstance(widget.get("answer"), (int, float)):
                bad(w, "a slider prediction needs a numeric answer")
        elif inp.get("kind") == "choice":
            if not _list_of_str(inp.get("options")) or len(inp["options"]) < 2:
                bad(w, "choice input needs 2+ options")
            elif widget.get("answer") not in inp["options"]:
                bad(w, "answer must be one of the choice options")
        else:
            bad(w, "input.kind must be \"slider\" or \"choice\"")
        if not _str(widget.get("reveal")):
            bad(w, "needs a reveal: what the book found, shown after the guess")

    elif t == "chart":
        kind = widget.get("chart")
        if kind not in CHART_KINDS:
            bad(w, f"chart must be one of {sorted(CHART_KINDS)}")
        series = widget.get("series")
        if not isinstance(series, list) or not 1 <= len(series) <= 4:
            bad(w, "1-4 series")
            return
        if kind in ("bar", "line"):
            cats = widget.get("x", {}).get("values")
            if not isinstance(cats, list) or not cats:
                bad(w, "x.values: the categories (bar) or x positions (line)")
                return
            for i, s in enumerate(series):
                vals = s.get("values")
                if not _str(s.get("name")) or not isinstance(vals, list) or len(vals) != len(cats):
                    bad(w, f"series[{i}] needs a name and one value per x value")
                elif not all(v is None or isinstance(v, (int, float)) for v in vals):
                    bad(w, f"series[{i}] values must be numbers (or null for a gap)")
        else:
            for i, s in enumerate(series):
                pts = s.get("points")
                if not _str(s.get("name")) or not isinstance(pts, list) or not pts:
                    bad(w, f"series[{i}] needs a name and points [[x, y], ...]")
                elif not all(
                    isinstance(pt, list) and len(pt) >= 2
                    and all(isinstance(v, (int, float)) for v in pt[:2])
                    for pt in pts
                ):
                    bad(w, f"series[{i}] points must be [x, y] or [x, y, \"label\"]")

    elif t == "steps":
        steps = widget.get("steps")
        if not isinstance(steps, list) or not 2 <= len(steps) <= 8:
            bad(w, "2-8 steps")
        elif not all(isinstance(s, dict) and _str(s.get("title")) for s in steps):
            bad(w, "every step needs a title")
        else:
            for i, s in enumerate(steps):
                fig = s.get("figure")
                if fig is not None and (not isinstance(fig, dict) or not _str(fig.get("ref"))):
                    bad(w, f"steps[{i}].figure needs a \"ref\"")

    elif t == "flashcards":
        cards = widget.get("cards")
        if not isinstance(cards, list) or not 2 <= len(cards) <= 12:
            bad(w, "2-12 cards")
        elif not all(isinstance(c, dict) and _str(c.get("front")) and _str(c.get("back")) for c in cards):
            bad(w, "every card needs front and back")

    elif t == "match":
        pairs = widget.get("pairs")
        if not isinstance(pairs, list) or not 3 <= len(pairs) <= 6:
            bad(w, "3-6 pairs")
        elif not all(isinstance(x, dict) and _str(x.get("left")) and _str(x.get("right")) for x in pairs):
            bad(w, "every pair needs left and right")

    elif t == "sort":
        buckets = widget.get("buckets")
        items = widget.get("items")
        if not _list_of_str(buckets) or not 2 <= len(buckets) <= 4:
            bad(w, "2-4 buckets")
        elif not isinstance(items, list) or not 3 <= len(items) <= 10:
            bad(w, "3-10 items")
        else:
            for i, it in enumerate(items):
                if not isinstance(it, dict) or not _str(it.get("text")) or it.get("bucket") not in buckets:
                    bad(w, f"items[{i}] needs text and a bucket that is one of {buckets}")

    elif t == "explore":
        params = widget.get("params")
        if not isinstance(params, list) or not 1 <= len(params) <= 5:
            bad(w, "1-5 params")
            return
        ids = []
        for i, prm in enumerate(params):
            pid = prm.get("id") if isinstance(prm, dict) else None
            if not isinstance(pid, str) or not re.match(r"^[a-zA-Z_]\w*$", pid):
                bad(w, f"params[{i}] needs an identifier id")
            ids.append(pid)
            if not all(isinstance(prm.get(k), (int, float)) for k in ("min", "max", "value")):
                bad(w, f"params[{i}] needs numeric min, max, value")
        outs = widget.get("outputs")
        if not isinstance(outs, list) or not outs:
            bad(w, "at least one output {label, expr}")
        else:
            for i, o in enumerate(outs):
                if not isinstance(o, dict) or not _str(o.get("label")) or not _str(o.get("expr")):
                    bad(w, f"outputs[{i}] needs label and expr")
        for i, v in enumerate(widget.get("verdicts") or []):
            if not isinstance(v, dict) or not _str(v.get("when")) or not _str(v.get("text")):
                bad(w, f"verdicts[{i}] needs when (an expression) and text")
        plot = widget.get("plot")
        if plot is not None:
            if plot.get("over") not in ids:
                bad(w, "plot.over must name one of the params")
            ys = plot.get("y")
            if not isinstance(ys, list) or not 1 <= len(ys) <= 4 or not all(
                isinstance(y, dict) and _str(y.get("name")) and _str(y.get("expr")) for y in ys
            ):
                bad(w, "plot.y is a list of 1-4 {name, expr} curves")

    elif t == "table":
        cols = widget.get("columns")
        rows = widget.get("rows")
        if not _list_of_str(cols):
            bad(w, "columns: header strings")
        elif not isinstance(rows, list) or not rows or not 1 <= len(rows) <= 14:
            bad(w, "1-14 rows")
        elif not all(isinstance(r, list) and len(r) == len(cols) for r in rows):
            bad(w, "every row needs one cell per column")

    elif t == "custom":
        mod = widget.get("module")
        if not isinstance(mod, str) or not RE_ID.match(mod):
            bad(w, "module: kebab-case name of lessons/chNN/widgets/<module>.js")
        elif widgets_dir is not None and not (widgets_dir / f"{mod}.js").exists():
            bad(w, f"widgets/{mod}.js does not exist")
        if not _str(widget.get("caption")):
            bad(w, "a custom widget needs a caption telling the reader what to do")
        if "static" in widget and not isinstance(widget["static"], bool):
            bad(w, "static is true (a drawn figure, nothing to do) or false")
        if "wide" in widget and not isinstance(widget["wide"], bool):
            bad(w, "wide is true (keep the stage's full width in present mode) or false")
