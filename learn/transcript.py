"""The lesson as a reader meets it, in plain text: the cold-read script.

A lesson replaces the book, so the test that matters is whether someone who
never opened the book can follow it. The gates can check that a declared
term is taught before it is used; they can't notice a term nobody declared,
a symbol nobody explained, or a question with nothing to reason from. A
reader can. `transcript.md` is everything a study-mode reader sees, in
order -- titles, text, definition cards, figure captions and pinned notes,
chart axes and values, the text each custom widget draws on the page (read
off it by `verify`), every question and what it reveals -- and nothing
they don't (speaker notes, the book's passages). The skill hands it to a
fresh reader who has only this file and asks where they got lost.
"""

from __future__ import annotations

import re

from . import schema

KEYS = "ABCDEFGH"


def render(data: dict, shown: dict | None = None) -> str:
    """`shown` maps a slide id to the text its custom widget drew on the
    page (from the runtime self-test); without it a custom widget is
    described from its props."""
    lesson = data["lesson"]
    gloss = {g["term"].lower(): g for g in lesson.get("glossary") or []}
    out = [f"# {lesson.get('title') or data['chapter']['title']}"]
    book = data.get("book") or {}
    if book.get("title"):
        out.append(f"(A lesson on chapter {data['chapter'].get('number')} of {book['title']}" + (f", by {book['author']})" if book.get("author") else ")"))
    if lesson.get("subtitle"):
        out.append(lesson["subtitle"])
    out += ["", "After this lesson you can:"]
    out += [f"- {o}" for o in lesson.get("objectives") or []]
    n = 0
    for section in lesson.get("sections") or []:
        out += ["", f"## {section.get('title', '')}", ""]
        for slide in section.get("slides") or []:
            n += 1
            out += _slide(n, slide, gloss, data.get("figures") or {}, (shown or {}).get(slide.get("id")))
    if lesson.get("takeaways"):
        out += ["## Recap", ""] + [f"- {t}" for t in lesson["takeaways"]]
    if gloss:
        out += ["", "## Glossary", ""] + [f"- **{g['term']}**: {g['def']}" for g in gloss.values()]
    # A term link reads as its words on the page ("[[Term|words]]").
    text = RE_LINK.sub(lambda m: m.group(2) or m.group(1), "\n".join(out))
    return text.rstrip() + "\n"


RE_LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def _slide(n: int, s: dict, gloss: dict, figures: dict, shown: str | None = None) -> list[str]:
    if schema.is_divider(s):
        head = f"### {n}. [{'opening' if s.get('layout') == 'title' else 'section'} slide] {s.get('title', '')}"
        return [head] + ([s["text"]] if s.get("text") else []) + [""]
    lines = [f"### {n}. {s.get('title', '')}" + (f"   ({s['kicker']})" if s.get("kicker") else "")]
    if s.get("text"):
        lines.append(s["text"])
    for t in s.get("teaches") or []:
        g = gloss.get(t.lower())
        if g:
            lines.append(f"> NEW TERM **{g['term']}**: {g['def']}")
            if g.get("example"):
                lines.append(f"> Example: {g['example']}")
    lines += [f"- {p}" for p in s.get("points") or []]
    lines += _main(s, figures, shown)
    if s.get("deeper"):
        lines.append(f"[Go deeper] {s['deeper']}")
    if s.get("beyond"):
        lines.append(f"[Beyond the book] {s['beyond']}")
    return lines + [""]


def _main(s: dict, figures: dict, shown: str | None = None) -> list[str]:
    if s.get("figure"):
        return _figure(s["figure"], figures)
    if s.get("stats"):
        return ["STATS: " + " | ".join(
            f"{x['value']} {x['label']}" + (f" ({x['context']})" if x.get("context") else "") for x in s["stats"])]
    if s.get("quote"):
        q = s["quote"]
        return [f"QUOTE: “{q['text']}”" + (f" ({q['cite']})" if q.get("cite") else "")]
    if s.get("columns"):
        out = ["COLUMNS:"]
        for c in s["columns"]:
            body = c.get("text") or "; ".join(c.get("points") or [])
            out.append(f"  [{c['title']}] {body}")
        return out
    if s.get("widget"):
        return _widget(s["widget"], figures, shown)
    return []


def _figure(spec: dict, figures: dict) -> list[str]:
    f = figures.get(spec.get("key")) or {}
    # An unnumbered figure shows no label on the page, so none here either.
    label = f" ({f['label']})" if f.get("label") else ""
    out = [f"FIGURE from the book{label}: {spec.get('caption') or f.get('caption') or ''}".rstrip()]
    if spec.get("alt"):
        # What the picture itself shows, for a reader who can't see it.
        out.append(f"  the image shows: {spec['alt']}")
    out += [f"  note {i + 1}: {n['text']}" for i, n in enumerate(spec.get("notes") or [])]
    return out


def _widget(w: dict, figures: dict, shown: str | None = None) -> list[str]:
    t = w.get("type")
    out = [f"(instruction: {w['caption']})"] if w.get("caption") and t != "custom" else []
    if t == "quiz":
        out.append(f"QUIZ{' (pick all that apply)' if w.get('multi') else ''}: {w['question']}")
        out += [f"  {KEYS[i]}. {o['text']}" for i, o in enumerate(w["options"])]
        out.append("  After answering, the reader sees:")
        out += [f"    {KEYS[i]} is {'right' if o.get('correct') else 'wrong'}: {o.get('why', '')}" for i, o in enumerate(w["options"])]
        if w.get("explain"):
            out.append(f"    {w['explain']}")
    elif t == "predict":
        inp = w.get("input") or {}
        out.append(f"PREDICT (the reader commits to a guess first): {w['question']}")
        if inp.get("kind") == "slider":
            out.append(f"  slider {inp.get('label') or ''} from {inp.get('min')} to {inp.get('max')}{inp.get('unit') or ''}")
        else:
            out += [f"  {KEYS[i]}. {o}" for i, o in enumerate(inp.get("options") or [])]
        out.append(f"  After the guess: the answer is {w.get('answer')}{(inp.get('unit') or '') if inp.get('kind') == 'slider' else ''}. {w.get('reveal', '')}")
    elif t == "chart":
        x, y = w.get("x") or {}, w.get("y") or {}
        out.append(f"CHART ({w.get('chart')}): x = {x.get('label', '')}; y = {y.get('label', '')}{' (' + y['unit'] + ')' if y.get('unit') else ''}")
        for s in w.get("series") or []:
            if s.get("values") is not None:
                pairs = ", ".join(f"{c}: {v}" for c, v in zip(x.get("values") or [], s["values"]))
            else:
                pairs = ", ".join(f"({p[0]}, {p[1]}" + (f", {p[2]}" if len(p) > 2 else "") + ")" for p in s.get("points") or [])
            out.append(f"  {s.get('name')}: {pairs}")
        if w.get("note"):
            out.append(f"  {w['note']}")
    elif t == "table":
        cols = w.get("columns") or []
        out.append("TABLE:")
        out.append("  | " + " | ".join(str(c) for c in cols) + " |")
        out += ["  | " + " | ".join(str(c) for c in row) + " |" for row in w.get("rows") or []]
        if w.get("note"):
            out.append(f"  {w['note']}")
    elif t == "steps":
        out.append("STEPS (one at a time):")
        for i, st in enumerate(w.get("steps") or []):
            fig = st.get("figure")
            ff = (figures.get(fig.get("key")) or {}) if fig else {}
            extra = f" [figure{' ' + ff['label'] if ff.get('label') else ''}: {fig.get('caption') or ff.get('caption', '')}]" if fig else ""
            out.append(f"  {i + 1}. {st['title']}" + (f": {st['text']}" if st.get("text") else "") + extra)
    elif t == "flashcards":
        out.append("FLASHCARDS:")
        out += [f"  {c['front']} -> {c['back']}" for c in w.get("cards") or []]
    elif t == "match":
        out.append(f"MATCH: {w.get('prompt', '')}")
        out += [f"  {p['left']} <-> {p['right']}" for p in w.get("pairs") or []]
    elif t == "sort":
        out.append(f"SORT into {' / '.join(w.get('buckets') or [])}: {w.get('prompt', '')}")
        out += [f"  {it['text']} -> {it['bucket']}" + (f" ({it['why']})" if it.get("why") else "") for it in w.get("items") or []]
    elif t == "explore":
        # The page shows labels and live numbers, never the formulas or the
        # verdict conditions, so neither does the transcript.
        params = w.get("params") or []
        start = {p["id"]: p.get("value", p.get("min")) for p in params if "id" in p}
        out.append("EXPLORE (sliders the reader moves):")
        out += [f"  slider {p.get('label')}: {p.get('min')} to {p.get('max')}{p.get('unit') or ''}, starts at {p.get('value', p.get('min'))}" for p in params]
        for o in w.get("outputs") or []:
            v = _evaluate(o.get("expr", ""), start)
            out.append(f"  shows {o['label']}" + (f" (at the start: {_num(v, o.get('decimals'))})" if v is not None else ""))
        verdicts = w.get("verdicts") or []
        if verdicts:
            out.append("  one message at a time, depending on the sliders:")
            out += [f"    - {v['text']}" for v in verdicts]
        plot = w.get("plot")
        if plot:
            names = ", ".join(y.get("name", "") for y in plot.get("y") or [])
            out.append(f"  a plot of {names} against {plot.get('xLabel') or plot.get('over')}")
        if w.get("note"):
            out.append(f"  {w['note']}")
    elif t == "custom":
        kind = "FIGURE drawn for the lesson" if w.get("static") else "INTERACTIVE TOOL"
        out.append(f"{kind}: {w.get('caption', '')}")
        words = list(dict.fromkeys(_strings(w.get("props"))))
        if shown is not None:
            out.append("  it shows:")
            for line in shown.splitlines():
                if line.strip() == "[next]":
                    # The runtime stepped the widget forward (see customText).
                    out.append("  after the reader steps forward, it adds:")
                elif line.strip():
                    out.append(f"    {line}")
            # What an interactive widget holds back until the reader acts: a
            # record with a new field keeps its first field, so the reader can
            # tell which item ("give: 4") the new field belongs to, and drops
            # the rest of what is already on the page (settings like "max: 5").
            later = []
            for x in words:
                if _seen(x, shown) or re.fullmatch(r"[\w]+(?:-[\w]+)+", x):
                    continue
                if _record(x):
                    first, *rest = x.split("; ")
                    x = "; ".join([first] + [p for p in rest if not _seen(p, shown)])
                later.append(x)
            if later and not w.get("static"):
                out.append("  after the reader acts, it can also show (from the widget's data): " + " | ".join(later[:80]))
        elif words:
            out.append("  drawn from: " + " | ".join(words[:80]))
    return out


_MATH = {"min": min, "max": max, "abs": abs, "pow": pow, "round": round,
         "sqrt": lambda x: x ** 0.5, "clamp": lambda x, lo, hi: max(lo, min(hi, x))}


def _evaluate(expr: str, values: dict):
    """An output's value at the sliders' starting positions, or None."""
    if not re.fullmatch(r"[\w\s.+\-*/(),]*", expr):
        return None
    try:
        return float(eval(expr, {"__builtins__": {}}, {**_MATH, **values}))  # noqa: S307 -- arithmetic on our own lesson data
    except Exception:  # noqa: BLE001
        return None


def _num(v: float, decimals) -> str:
    return f"{v:.{decimals if decimals is not None else 2}f}"


def _seen(s: str, shown: str) -> bool:
    """Whether a prop's text is on the page: for "key: 3" its value, and for
    a record ("give: 4; rejected: 37") every field of it."""
    if _record(s):
        return all(_seen(part, shown) for part in s.split("; "))
    m = re.fullmatch(r"\w+: ([\d., -]+)", s)
    if m:
        return all(re.search(rf"(?<![\d.]){re.escape(v.strip())}(?![\d])", shown) for v in m.group(1).split(","))
    m = re.fullmatch(r"\w+: (.+)", s)
    return re.sub(r"[*_`]", "", m.group(1) if m else s) in shown


def _record(s: str) -> bool:
    return "; " in s and all(re.fullmatch(r"\w+: .+", part) for part in s.split("; "))


def _scalar(v) -> bool:
    return isinstance(v, (str, int, float)) and not isinstance(v, bool)


def _strings(node, key: str = ""):
    """What a custom figure is drawn from: its text, and its numbers with the
    prop name that says what they are ("rejected: 44")."""
    if isinstance(node, str):
        if node.strip():
            yield node.strip()
    elif isinstance(node, bool) or node is None:
        return
    elif isinstance(node, (int, float)):
        yield f"{key}: {node}" if key else str(node)
    elif isinstance(node, dict):
        # A small record with a number in it reads as one ("give: 4;
        # rejected: 37"): split up, its numbers lose what they belong to.
        fields = {k: v for k, v in node.items() if v is not None and v != ""}
        if len(fields) > 1 and all(_scalar(v) for v in fields.values()) and any(not isinstance(v, str) for v in fields.values()):
            yield "; ".join(f"{k}: {v.strip() if isinstance(v, str) else v}" for k, v in fields.items())
            return
        for k, v in node.items():
            # An id or key names a part for the widget's code; the reader never sees it.
            if k in ("id", "key") and isinstance(v, str):
                continue
            yield from _strings(v, k)
    elif isinstance(node, list):
        if node and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in node):
            yield f"{key}: " + ", ".join(str(v) for v in node) if key else ", ".join(str(v) for v in node)
        else:
            for v in node:
                yield from _strings(v, key)
