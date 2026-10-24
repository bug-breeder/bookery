"""Lesson gates: what a condensed lesson is allowed to claim, and how.

The site gates prove nothing was lost. A lesson loses most of the chapter on
purpose, so these gates check the opposite promises:

1. `schema`     the spec is well-formed and every reference resolves
2. `grounding`  every content slide cites the blocks it condenses
3. `numbers`    every number a reader sees exists in the chapter
4. `quotes`     every quotation is verbatim from the chapter
5. `coverage`   every section is either taught or skipped with a reason
6. `density`    no wall of text: word budgets per slide, enough of the
                book's figures, and at least one check of what was taught
7. `teaching`   the lesson replaces the book, so no slide uses a term
                before the slide that teaches it, and every term is taught

The runner adds `runtime` (the built page boots in a real browser with no
errors) and `size` (the page fits an Artifact).

Like the site gates, each is a check that can fail: a lesson that invents a
statistic fails `numbers` no matter how plausible the statistic is.
"""

from __future__ import annotations

import re
from pathlib import Path

from pipeline import textnorm
from verify.gates import BLOCKED, FAIL, PASS, Gate

from . import schema
from .source import ChapterSource

# ---------------------------------------------------------------------------
# Word budgets (the density gate). Present mode puts one slide on a screen at
# a time, so these are what keeps a slide readable from the back of a room.
# ---------------------------------------------------------------------------

LIMITS = {
    "title": 12,
    "kicker": 6,
    "text": 50,
    "point": 18,
    "points": 5,
    "column": 30,
    "quote": 45,
    "stat_label": 14,
    "visible": 90,
    "define": 40,
    "example": 30,
    "deeper": 160,
    "notes": 200,
    "beyond": 80,
    "question": 35,
    "option": 20,
    "why": 40,
}
# A lesson the reader studies instead of the book teaches every idea it
# uses, so it runs longer than a deck that leans on a speaker.
MIN_SLIDES, MAX_SLIDES = 8, 70
# Only a floor on checks, not a quota: a quota fills a lesson with
# questions nobody needed to answer. Whether each interaction earns its
# place is the author's call (see the skill's authoring guide).
MIN_RECALL = 1                 # quizzes/predictions/matches/sorts per lesson
MIN_VISUAL_SHARE = 0.4         # share of content slides with a figure, chart, stat...
MAX_TEXT_STREAK = 2            # consecutive slides that are only words
MIN_EXHIBITS = 4               # book figures/tables used, capped at what exists

_RE_MARKUP = re.compile(r"\$\$?[^$]+\$\$?|\*\*|[*`_]|\[\[|\]\]")
_RE_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
_RE_ELLIPSIS = re.compile(r"\s*(?:\.\s?\.\s?\.|…)\s*")
_RE_LATEX = re.compile(r"\$\$?([^$]+)\$\$?")

# String fields that are identifiers or code, never reader-facing prose.
IDENT_KEYS = {
    "id", "sources", "ref", "module", "layout", "type", "chart", "expr", "block",
    "kind", "format", "color", "when", "over", "accent", "teaches", "aliases",
}
SKIP_KEYS = IDENT_KEYS | schema.BEYOND_KEYS


def words(text: str) -> int:
    return len(_RE_MARKUP.sub(" ", text or "").split())


def _numbers(text: str) -> list[str]:
    # Math is checked like prose: "$\alpha = 0.5$" asserts 0.5. Thousands
    # separators are typography, so "1,000" and "1000" are the same claim.
    text = _RE_THOUSANDS.sub("", text)
    return textnorm.numbers(text)


def _num_str(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def _source_numbers(text: str) -> set[str]:
    """Numbers in the source, plus their percent<->proportion twins.

    The book writes "0.45" in a table and "45 percent" in prose for the same
    fact; a lesson may use either form without inventing anything.
    """
    out: set[str] = set()
    for n in _numbers(text):
        out.add(n)
        try:
            v = float(n)
        except ValueError:
            continue
        out.add(_num_str(v * 100))
        out.add(_num_str(v / 100))
    return out


def _literal(v) -> str:
    return _num_str(float(v))


# ---------------------------------------------------------------------------
# Walking a slide's reader-facing content
# ---------------------------------------------------------------------------


def iter_strings(node, path: str = ""):
    """(path, text) for every reader-facing string under a slide or widget."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            if k in SKIP_KEYS:
                continue
            yield from iter_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_strings(v, f"{path}[{i}]")


def iter_data_numbers(slide: dict):
    """(path, value) for numeric literals that state a fact.

    Only data fields count: chart values, table cells, a prediction's answer,
    and a custom widget's props. Figure-note coordinates, slider ranges and
    the like configure the page and say nothing about the book.
    """
    w = slide.get("widget") or {}
    t = w.get("type")
    if t == "chart":
        for i, v in enumerate((w.get("x") or {}).get("values") or []):
            if isinstance(v, (int, float)):
                yield f"widget.x.values[{i}]", v
        for si, s in enumerate(w.get("series") or []):
            for i, v in enumerate(s.get("values") or []):
                if isinstance(v, (int, float)):
                    yield f"widget.series[{si}].values[{i}]", v
            for i, pt in enumerate(s.get("points") or []):
                for j, v in enumerate(pt[:2] if isinstance(pt, list) else []):
                    if isinstance(v, (int, float)):
                        yield f"widget.series[{si}].points[{i}][{j}]", v
    elif t == "table":
        for ri, row in enumerate(w.get("rows") or []):
            for ci, v in enumerate(row if isinstance(row, list) else []):
                if isinstance(v, (int, float)):
                    yield f"widget.rows[{ri}][{ci}]", v
    elif t == "predict" and isinstance(w.get("answer"), (int, float)):
        yield "widget.answer", w["answer"]
    elif t == "custom":
        yield from _walk_props(w.get("props"), "widget.props")


def _walk_props(node, path: str):
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            if k not in schema.CONTROL_KEYS:
                yield from _walk_props(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_props(v, f"{path}[{i}]")


def claims_view(slide: dict) -> dict:
    """The slide minus its distractors.

    A wrong quiz option ("about 10 percent") is not a claim about the book;
    it is there to be wrong. Its `why` is a claim, and stays.
    """
    w = slide.get("widget")
    if not isinstance(w, dict) or w.get("type") not in ("quiz", "predict"):
        return slide
    w = dict(w)
    if w["type"] == "quiz":
        w["options"] = [
            o if o.get("correct") else {k: v for k, v in o.items() if k != "text"}
            for o in w.get("options") or []
            if isinstance(o, dict)
        ]
    elif isinstance(w.get("input"), dict):
        inp = dict(w["input"])
        inp["options"] = [o for o in inp.get("options") or [] if o == w.get("answer")]
        w["input"] = inp
    return {**slide, "widget": w}


def figure_refs(slide: dict) -> list[str]:
    """Every book image a slide shows: its figure, any figure in its steps,
    and the images a widget's numbers were transcribed from (shown on
    request beside the widget)."""
    refs = [(slide.get("figure") or {}).get("ref")]
    w = slide.get("widget") or {}
    if w.get("type") == "steps":
        refs += [(st.get("figure") or {}).get("ref") for st in w.get("steps") or [] if isinstance(st, dict)]
    if isinstance(w.get("transcribed"), list):
        refs += w["transcribed"]
    return [r for r in refs if isinstance(r, str) and r.strip()]


def _resolved_sources(slide: dict, src: ChapterSource) -> list[str]:
    ids = [src.resolve_id(s) for s in slide.get("sources") or []]
    for ref in figure_refs(slide):
        ex = src.resolve_exhibit(ref)
        if ex:
            ids.append(ex.block_id)
    quote = slide.get("quote") or {}
    if quote.get("block"):
        ids.append(src.resolve_id(quote["block"]))
    return [i for i in ids if i]


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def gate_schema(lesson: dict, src: ChapterSource, widgets_dir: Path) -> Gate:
    problems = schema.validate(lesson, widgets_dir)
    for _section, slide in schema.iter_slides(lesson):
        for ref in figure_refs(slide):
            ex = src.resolve_exhibit(ref)
            if ex is None:
                problems.append(schema.Problem(f"slide {slide.get('id')!r}", f"figure ref {ref!r} matches no exhibit"))
            elif ex.file is None:
                problems.append(schema.Problem(f"slide {slide.get('id')!r}", f"{ref!r} has no image on disk (a recovered matrix?); use a table widget instead"))
    detail = {"problems": [p.as_dict() for p in problems[:80]], "count": len(problems)}
    return Gate("schema", PASS if not problems else FAIL, detail)


def gate_grounding(lesson: dict, src: ChapterSource) -> Gate:
    uncited, unresolved = [], []
    content = 0
    for _section, slide in schema.iter_slides(lesson):
        for s in slide.get("sources") or []:
            if src.resolve_id(s) is None:
                unresolved.append({"slide": slide.get("id"), "source": s})
        if schema.is_divider(slide):
            continue
        content += 1
        if not slide.get("sources"):
            uncited.append(slide.get("id"))
    for i, g in enumerate(lesson.get("glossary") or []):
        for s in g.get("sources") or []:
            if src.resolve_id(s) is None:
                unresolved.append({"glossary": g.get("term"), "source": s})
    detail = {
        "content_slides": content,
        "uncited_slides": uncited,
        "unresolved_sources": unresolved[:60],
    }
    ok = not uncited and not unresolved
    return Gate("grounding", PASS if ok else FAIL, detail)


def gate_numbers(lesson: dict, src: ChapterSource) -> Gate:
    chapter_numbers = _source_numbers(src.full_text)
    block_numbers: dict[str, set[str]] = {}

    def cited_numbers(ids: list[str]) -> set[str]:
        out: set[str] = set()
        for bid in ids:
            if bid not in block_numbers:
                block_numbers[bid] = _source_numbers(src.block_text(src.by_id[bid]))
            out |= block_numbers[bid]
        return out

    invented, uncited, derived, transcribed = [], [], [], []
    checked = 0

    def check(where: str, slide_id: str | None, value: str, cited: set[str] | None) -> None:
        nonlocal checked
        checked += 1
        if value not in chapter_numbers:
            invented.append({"slide": slide_id, "field": where, "value": value})
        elif cited is not None and value not in cited:
            uncited.append({"slide": slide_id, "field": where, "value": value})

    for _section, slide in schema.iter_slides(lesson):
        sid = slide.get("id")
        cited = cited_numbers(_resolved_sources(slide, src))
        widget = slide.get("widget") or {}
        is_derived = bool(widget.get("derived"))
        read_from = widget.get("transcribed") if isinstance(widget.get("transcribed"), list) else None
        for path, text in iter_strings(claims_view(slide)):
            for n in _numbers(text):
                check(path, sid, n, cited)
        read_values: set[str] = set()
        for path, v in iter_data_numbers(slide):
            value = _literal(v)
            if read_from:
                # Read off the book's images: the text can't confirm them
                # (a "4" in a score table matches any 4 in the chapter), so
                # they go to a reviewer with the images they came from.
                read_values.add(value)
                checked += 1
                continue
            if is_derived and value not in chapter_numbers:
                derived.append({"slide": sid, "field": path, "value": value, "how": widget["derived"]})
                continue
            check(path, sid, value, cited)
        if read_values:
            transcribed.append({
                "slide": sid,
                "from": read_from,
                "values": sorted(read_values, key=float),
            })

    for key in ("title", "subtitle", "objectives", "glossary", "takeaways"):
        for path, text in iter_strings(lesson.get(key), key):
            for n in _numbers(text):
                check(path, None, n, None)

    detail = {
        "numbers_checked": checked,
        "invented": invented[:60],
        "invented_count": len(invented),
        # Present in the chapter, but not in the blocks this slide cites.
        # Not a failure -- the number is real -- but the citation is loose.
        "cited_elsewhere": uncited[:40],
        # Values a widget declares as computed from the book (with how).
        # Not checkable against the text; listed for human review.
        "derived_for_review": derived[:40],
        # Values a widget declares as read off book images (`transcribed`).
        # Check them against the images; the page shows them beside the widget.
        "transcribed_for_review": transcribed[:40],
    }
    return Gate("numbers", PASS if not invented else FAIL, detail)


def _quote_segments(text: str) -> list[str]:
    text = text.strip().strip("\"“”‘’'")
    return [s for s in (textnorm.normalize(x) for x in _RE_ELLIPSIS.split(text)) if s]


def gate_quotes(lesson: dict, src: ChapterSource) -> Gate:
    failures, loose = [], []
    total = 0
    norm = src.normalized_blocks
    whole = "\n".join(norm.values())
    for _section, slide in schema.iter_slides(lesson):
        quote = slide.get("quote")
        if not quote:
            continue
        total += 1
        ids = _resolved_sources(slide, src)
        pool = " ".join(norm[i] for i in ids)
        segments = _quote_segments(quote.get("text", ""))
        if all(seg in pool for seg in segments):
            continue
        missing = [seg for seg in segments if seg not in whole]
        if missing:
            failures.append({"slide": slide.get("id"), "not_in_chapter": missing[:3]})
        else:
            loose.append({"slide": slide.get("id"), "note": "verbatim, but not in the cited blocks"})
    detail = {"quotes": total, "failures": failures, "cited_elsewhere": loose}
    return Gate("quotes", PASS if not failures else FAIL, detail)


def gate_coverage(lesson: dict, src: ChapterSource) -> Gate:
    if not src.sections:
        return Gate("coverage", PASS, {"note": "chapter has no sections; nothing to cover", "sections": 0})
    touched: dict[str, list[str]] = {}
    exhibits_used: set[str] = set()
    exhibit_blocks = {ex.block_id: ex.ref for ex in src.exhibits}
    for _section, slide in schema.iter_slides(lesson):
        for bid in _resolved_sources(slide, src):
            label = src.section_of.get(bid)
            if label:
                touched.setdefault(label, []).append(slide.get("id"))
            if bid in exhibit_blocks:
                exhibits_used.add(exhibit_blocks[bid])
    skipped = {}
    for item in lesson.get("skipped") or []:
        key = str(item.get("section", "")).strip()
        skipped[key] = item.get("reason", "")
        skipped[key.lower()] = item.get("reason", "")

    rows, missing = [], []
    for s in src.sections:
        reason = skipped.get(s.label) or skipped.get(s.title.lower())
        if s.label in touched:
            rows.append({"section": s.label, "title": s.title, "status": "taught", "slides": sorted(set(touched[s.label]))})
        elif reason:
            rows.append({"section": s.label, "title": s.title, "status": "skipped", "reason": reason})
        else:
            rows.append({"section": s.label, "title": s.title, "status": "MISSING", "words": s.words})
            missing.append(s.label)
    detail = {
        "sections": rows,
        "missing": missing,
        "exhibits_used": sorted(exhibits_used),
        "exhibits_available": len(src.exhibits),
    }
    return Gate("coverage", PASS if not missing else FAIL, detail)


def _visible_words(slide: dict) -> int:
    n = words(slide.get("title", "")) + words(slide.get("kicker", "")) + words(slide.get("text", ""))
    n += sum(words(p) for p in slide.get("points") or [])
    for s in slide.get("stats") or []:
        n += words(s.get("value", "")) + words(s.get("label", "")) + words(s.get("context", ""))
    n += words((slide.get("quote") or {}).get("text", ""))
    for c in slide.get("columns") or []:
        n += words(c.get("title", "")) + words(c.get("text", ""))
        n += sum(words(p) for p in c.get("points") or [])
    return n


def _is_visual(slide: dict) -> bool:
    t = schema.slide_widget_type(slide)
    return bool(
        slide.get("figure") or slide.get("stats")
        or t in ("chart", "table", "explore", "custom", "steps", "predict")
    )


def _is_text_only(slide: dict) -> bool:
    return not (
        slide.get("figure") or slide.get("stats") or slide.get("quote")
        or slide.get("columns") or slide.get("widget")
    ) and not schema.is_divider(slide)


def gate_density(lesson: dict, src: ChapterSource) -> Gate:
    over: list[dict] = []

    def limit(slide_id, field: str, n: int, key: str) -> None:
        if n > LIMITS[key]:
            over.append({"slide": slide_id, "field": field, "words": n, "limit": LIMITS[key]})

    slides = [s for _sec, s in schema.iter_slides(lesson)]
    content = [s for s in slides if not schema.is_divider(s)]
    for s in slides:
        sid = s.get("id")
        limit(sid, "title", words(s.get("title", "")), "title")
        limit(sid, "kicker", words(s.get("kicker", "")), "kicker")
        limit(sid, "text", words(s.get("text", "")), "text")
        pts = s.get("points") or []
        if len(pts) > LIMITS["points"]:
            over.append({"slide": sid, "field": "points", "items": len(pts), "limit": LIMITS["points"]})
        for i, p in enumerate(pts):
            limit(sid, f"points[{i}]", words(p), "point")
        for i, c in enumerate(s.get("columns") or []):
            n = words(c.get("text", "")) + sum(words(p) for p in c.get("points") or [])
            limit(sid, f"columns[{i}]", n, "column")
        limit(sid, "quote", words((s.get("quote") or {}).get("text", "")), "quote")
        for i, st in enumerate(s.get("stats") or []):
            limit(sid, f"stats[{i}].label", words(st.get("label", "")), "stat_label")
        limit(sid, "visible text", _visible_words(s), "visible")
        limit(sid, "deeper", words(s.get("deeper", "")), "deeper")
        limit(sid, "notes", words(s.get("notes", "")), "notes")
        limit(sid, "beyond", words(s.get("beyond", "")), "beyond")
        w = s.get("widget") or {}
        if w.get("type") in ("quiz", "predict"):
            limit(sid, "widget.question", words(w.get("question", "")), "question")
        for i, o in enumerate(w.get("options") or [] if w.get("type") == "quiz" else []):
            limit(sid, f"widget.options[{i}].text", words(o.get("text", "")), "option")
            limit(sid, f"widget.options[{i}].why", words(o.get("why", "")), "why")

    for g in lesson.get("glossary") or []:
        where = f"glossary {g.get('term')!r}"
        limit(None, f"{where}.def", words(g.get("def", "")), "define")
        limit(None, f"{where}.example", words(g.get("example", "")), "example")

    types = [schema.slide_widget_type(s) for s in content]
    active = sum(1 for s in content if schema.is_active(s))
    recall = sum(1 for t in types if t in schema.RECALL_WIDGETS)
    visual = sum(1 for s in content if _is_visual(s))

    streak, worst, worst_at = 0, 0, None
    for s in slides:
        streak = streak + 1 if _is_text_only(s) else 0
        if streak > worst:
            worst, worst_at = streak, s.get("id")

    shown = {
        ex.ref
        for s in slides
        for ex in (src.resolve_exhibit(r) for r in figure_refs(s))
        if ex
    }
    exhibit_blocks = {ex.block_id: ex.ref for ex in src.exhibits}
    data_refs = {
        exhibit_blocks[bid]
        for s in slides
        if schema.slide_widget_type(s) in ("chart", "table", "explore", "custom")
        for bid in _resolved_sources(s, src)
        if bid in exhibit_blocks
    }
    exhibits_used = shown | data_refs
    usable = sum(1 for ex in src.exhibits if ex.file or ex.data)
    need_exhibits = min(MIN_EXHIBITS, usable)

    lesson_problems = []
    if not MIN_SLIDES <= len(slides) <= MAX_SLIDES:
        lesson_problems.append(f"{len(slides)} slides; keep a lesson to {MIN_SLIDES}-{MAX_SLIDES}")
    if recall < MIN_RECALL:
        lesson_problems.append(
            f"{recall} recall checks (quiz/predict/match/sort); need at least {MIN_RECALL} "
            "that makes the reader use what the lesson taught"
        )
    if content and visual / len(content) < MIN_VISUAL_SHARE:
        lesson_problems.append(
            f"{visual}/{len(content)} slides are visual; need {MIN_VISUAL_SHARE:.0%} "
            "(figure, chart, stats, table, explorable, steps, prediction)"
        )
    if worst > MAX_TEXT_STREAK:
        lesson_problems.append(
            f"{worst} text-only slides in a row (ending at {worst_at!r}); max {MAX_TEXT_STREAK}"
        )
    if len(exhibits_used) < need_exhibits:
        lesson_problems.append(
            f"{len(exhibits_used)} of the book's exhibits used; need {need_exhibits} "
            "(a figure on a slide, or a chart/table citing its block)"
        )

    detail = {
        "slides": len(slides),
        "content_slides": len(content),
        "interactive": active,
        "recall_checks": recall,
        "visual_share": round(visual / len(content), 3) if content else 0,
        "longest_text_streak": worst,
        "exhibits_used": sorted(exhibits_used),
        "over_budget": over[:60],
        "lesson_problems": lesson_problems,
    }
    ok = not over and not lesson_problems
    return Gate("density", PASS if ok else FAIL, detail)


# ---------------------------------------------------------------------------
# Teaching order. The reader never opens the book, so the first time they
# meet a term must be the slide that teaches it (definition and example on
# screen), not a question that assumes it.
# ---------------------------------------------------------------------------

_RE_LINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_FOLD = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2011": "-", "\u2019": "'"})


def _fold(text: str) -> str:
    return " ".join((text or "").translate(_FOLD).lower().split())


def term_pattern(entry: dict) -> re.Pattern:
    """Matches a glossary term, its aliases, and their plurals, as words."""
    forms = [entry["term"], *(entry.get("aliases") or [])]
    alts = sorted({re.escape(_fold(f)) for f in forms if _fold(f)}, key=len, reverse=True)
    return re.compile(r"(?<![\w-])(?:" + "|".join(alts) + r")(?:e?s)?(?!\w)")


def _reader_strings(node, path: str = ""):
    """Every string a reader can see, including deeper, notes and beyond."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            if k not in IDENT_KEYS:
                yield from _reader_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _reader_strings(v, f"{path}[{i}]")


def gate_teaching(lesson: dict) -> Gate:
    entries = [g for g in lesson.get("glossary") or [] if isinstance(g, dict) and isinstance(g.get("term"), str)]
    by_key = {_fold(g["term"]): g for g in entries}
    patterns = {k: term_pattern(g) for k, g in by_key.items()}
    taught_at: dict[str, str] = {}
    early, unknown, twice, links = [], [], [], []

    for section in lesson.get("sections") or []:
        for i, slide in enumerate(section.get("slides") or []):
            sid = slide.get("id")
            teaches = [_fold(t) for t in slide.get("teaches") or [] if isinstance(t, str)]
            for t in teaches:
                if t not in by_key:
                    unknown.append({"slide": sid, "term": t})
                elif t in taught_at:
                    twice.append({"slide": sid, "term": by_key[t]["term"], "first": taught_at[t]})
            texts = [] if slide.get("layout") == "title" else list(_reader_strings(slide))
            if i == 0 and texts:
                texts.append(("section title", section.get("title") or ""))
            # The definition card shows on this slide, so its words must be
            # taught already too (a trust game's "trustee", say).
            for t in teaches:
                if t in by_key:
                    texts += [("definition", by_key[t].get("def", "")), ("example", by_key[t].get("example", ""))]
            flagged: set[str] = set()
            for path, text in texts:
                for m in _RE_LINK.finditer(text):
                    if _fold(m.group(1)) not in by_key:
                        links.append({"slide": sid, "term": m.group(1)})
                folded = _fold(text)
                for k, pat in patterns.items():
                    if k in taught_at or k in teaches or k in flagged:
                        continue
                    m = pat.search(folded)
                    if m:
                        flagged.add(k)
                        lo = max(0, m.start() - 30)
                        early.append({"slide": sid, "term": by_key[k]["term"], "field": path,
                                      "context": folded[lo:m.end() + 30]})
            for t in teaches:
                if t in by_key:
                    taught_at.setdefault(t, sid)

    for e in early:
        e["taught_on"] = taught_at.get(_fold(e["term"]))
    untaught = [g["term"] for k, g in by_key.items() if k not in taught_at]
    detail = {
        "terms": len(by_key),
        "taught": {by_key[k]["term"]: sid for k, sid in taught_at.items()},
        "used_before_taught": early[:60],
        "never_taught": untaught,
        "taught_twice": twice,
        "unknown_terms": unknown,
        "unknown_links": links[:40],
    }
    ok = not (early or untaught or twice or unknown or links)
    return Gate("teaching", PASS if ok else FAIL, detail)


def gate_size(index_html: Path, limit_bytes: int = 15_000_000) -> Gate:
    if not index_html.exists():
        return Gate("size", BLOCKED, {"reason": "page not built"})
    n = index_html.stat().st_size
    return Gate("size", PASS if n <= limit_bytes else FAIL, {"bytes": n, "limit": limit_bytes})


def static_gates(lesson: dict, src: ChapterSource, widgets_dir: Path) -> list[Gate]:
    first = gate_schema(lesson, src, widgets_dir)
    if not first.ok and any("expected \"schema\"" in p["message"] or p["where"] == "sections" for p in first.detail["problems"]):
        # Nothing downstream can walk a lesson without sections.
        return [first]
    return [
        first,
        gate_grounding(lesson, src),
        gate_numbers(lesson, src),
        gate_quotes(lesson, src),
        gate_coverage(lesson, src),
        gate_density(lesson, src),
        gate_teaching(lesson),
    ]
