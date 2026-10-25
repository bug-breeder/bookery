"""Build one lesson into a single-file page for a Claude Artifact.

Everything the page needs ships inside it: the lesson spec, the book's
figures (re-encoded as WebP data URIs), the passages each slide cites, and
the shared runtime. The only network loads are Google Fonts and, when a
lesson has math, KaTeX from cdnjs -- the two hosts the Artifact CSP allows.

Outputs, in work/learn/chNN/:
  index.html    the page to publish (no <html>/<head>: the Artifact
                skeleton wraps it)
  preview.html  the same page with a document skeleton, for a local browser
                and for the runtime gate (`#selftest`)
  publish.json  what to pass the Artifact tool
  transcript.md the lesson as plain text, exactly what a reader sees, for
                the cold read (see learn/transcript.py)

Run:  python -m learn.build --chapter 2   (or: bookery learn build --chapter 2)
"""

from __future__ import annotations

import argparse
import base64
import copy
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from pipeline import config, textnorm

from . import gates, schema, transcript
from .source import ChapterSource, Exhibit, build_dir, lesson_dir

RUNTIME = Path(__file__).resolve().parent / "runtime"

# Inter sets everything, text and apparatus alike, the way HeroUI does.
FONTS = (
    "https://fonts.googleapis.com/css2?"
    "family=Inter:ital,opsz,wght@0,14..32,400..800;1,14..32,400..800&display=swap"
)
KATEX = "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.js"

# Figures are shown at most ~1100 CSS px wide in present mode; 1400 keeps
# them sharp on a 2x screen at study width without bloating the page.
MAX_IMAGE_WIDTH = 1400
WEBP_QUALITY = 82
# The artifact gallery card's subtitle holds at most this many characters.
DESCRIPTION_MAX = 1000

# The same delimiters the runtime's md() renders as math.
RE_MATH = re.compile(
    r"\\\[|\$\$|\\\(|\$(?![\s\d])(?:\\\$|[^$\n])+?(?<!\s)\$(?!\d)"
)
RE_CAPTION_LABEL = re.compile(r"^\s*(?:figure|fig\.|table|tbl\.?)\s*[0-9]+(?:\.[0-9]+)*[.:]?\s*", re.I)


@dataclass
class Built:
    index: Path
    preview: Path
    publish: Path
    bytes: int
    figures: int
    transcript: Path
    data: dict


def load_lesson(number: int, root: Path | None = None) -> tuple[dict, Path]:
    path = lesson_dir(number, root) / "lesson.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist -- write the lesson first "
            "(see .claude/skills/learn/SKILL.md)."
        )
    return json.loads(path.read_text()), path


def figure_key(ex: Exhibit) -> str:
    return re.sub(r"[^a-z0-9]+", "-", ex.ref.lower()).strip("-")


def encode_image(path: Path) -> tuple[str, int, int]:
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "P"):
        # Book figures sit on white paper; flatten transparency onto white
        # so the dark theme's mat doesn't show through.
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    if img.width > MAX_IMAGE_WIDTH:
        h = round(img.height * MAX_IMAGE_WIDTH / img.width)
        img = img.resize((MAX_IMAGE_WIDTH, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=WEBP_QUALITY, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode(), img.width, img.height


def _figure_specs(slide: dict):
    """Every figure spec dict on a slide (its own, and its steps')."""
    if isinstance(slide.get("figure"), dict):
        yield slide["figure"]
    w = slide.get("widget") or {}
    if w.get("type") == "steps":
        for st in w.get("steps") or []:
            if isinstance(st, dict) and isinstance(st.get("figure"), dict):
                yield st["figure"]


def _passage(src: ChapterSource, bid: str) -> dict:
    b = src.by_id[bid]
    if b["type"] in ("figure", "matrix", "table"):
        text = b.get("caption") or ""
    elif b["type"] == "equation":
        text = b.get("latex") or b.get("text") or ""
    else:
        text = b.get("text") or ""
    label = src.section_of.get(bid)
    section = next((f"{s.label} {s.title}" if s.label[0].isdigit() else s.title
                    for s in src.sections if s.label == label), None)
    return {
        "page": src.printed_page(b),
        "text": textnorm.for_output(text).strip(),
        "kind": b["type"],
        "section": section,
    }


def payload(lesson: dict, src: ChapterSource) -> tuple[dict, int]:
    lesson = copy.deepcopy(lesson)
    figures: dict[str, dict] = {}
    sources: dict[str, dict] = {}

    def embed(slide: dict, ref: str) -> str:
        ex = src.resolve_exhibit(ref)
        if ex is None or ex.file is None:
            raise ValueError(f"slide {slide.get('id')!r}: figure {ref!r} has no image (run verify)")
        key = figure_key(ex)
        if key not in figures:
            data_uri, w, h = encode_image(ex.file)
            word = "Table" if ex.kind == "table" else "Figure"
            figures[key] = {
                "src": data_uri,
                "w": w,
                "h": h,
                "label": f"{word} {ex.label}" if ex.label else None,
                "caption": RE_CAPTION_LABEL.sub("", textnorm.for_output(ex.caption)).strip(),
                "page": ex.printed_page,
            }
        return key

    for _section, slide in schema.iter_slides(lesson):
        for spec in _figure_specs(slide):
            spec["key"] = embed(slide, spec["ref"])
        widget = slide.get("widget") or {}
        if isinstance(widget.get("transcribed"), list):
            # The page shows these images beside the widget; refs become keys.
            widget["transcribed"] = [embed(slide, ref) for ref in widget["transcribed"]]
        # Cited blocks travel with the page so "From the book" can show them.
        ids = gates._resolved_sources(slide, src)
        slide["sources"] = list(dict.fromkeys(ids))
        quote = slide.get("quote") or {}
        if quote.get("block"):
            quote["block"] = src.resolve_id(quote["block"]) or quote["block"]
        for bid in slide["sources"]:
            sources.setdefault(bid, _passage(src, bid))

    coverage = gates.gate_coverage(lesson, src).detail.get("sections", [])
    meta = src.meta
    pages = None
    if meta.get("printed_page") is not None and meta.get("pdf_page_end") is not None:
        pages = config.printed_page_range(meta)
    data = {
        "book": {"title": src.book["title"], "author": src.book["author"]},
        "chapter": {"number": src.number, "title": src.title, "pages": pages},
        "lesson": lesson,
        "figures": figures,
        "sources": sources,
        "sections": [
            {"label": r["section"], "title": r["title"],
             "status": "taught" if r["status"] == "taught" else "skipped",
             "reason": r.get("reason")}
            for r in coverage
        ],
    }
    return data, len(figures)


def has_math(lesson: dict) -> bool:
    return any(RE_MATH.search(text) for _p, text in gates.iter_strings(lesson))


def _script_json(data: dict) -> str:
    # Inside <script>, only "</" can end the element early.
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _custom_widgets(lesson: dict, widgets_dir: Path) -> str:
    used = sorted({
        (s.get("widget") or {}).get("module")
        for _sec, s in schema.iter_slides(lesson)
        if schema.slide_widget_type(s) == "custom"
    } - {None})
    parts = []
    for name in used:
        code = (widgets_dir / f"{name}.js").read_text()
        parts.append(f"/* widget: {name} */\n(function () {{\n{code}\n}})();")
    return "\n".join(parts)


def render_page(data: dict, lesson: dict, widgets_dir: Path) -> str:
    title = lesson.get("title") or data["chapter"]["title"]
    css = (RUNTIME / "app.css").read_text()
    js = (RUNTIME / "app.js").read_text()
    widgets = _custom_widgets(lesson, widgets_dir)
    head = [
        # The Artifact skeleton supplies these two; a downloaded copy has only the page.
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
        f"<title>{_esc(title)}</title>",
        '<meta name="claude:viewport-fit-sides" content="cover">',
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        f'<link rel="stylesheet" href="{FONTS}">',
        f"<style>\n{css}\n</style>",
    ]
    body = [
        '<div id="bk-app"></div>',
        f'<script type="application/json" id="lesson-data">{_script_json(data)}</script>',
    ]
    if has_math(lesson):
        body.append(f'<script src="{KATEX}"></script>')
    body.append(f"<script>\n{js}\n</script>")
    if widgets:
        body.append(f"<script>\n{widgets}\n</script>")
    body.append("<script>Bookery.boot();</script>")
    return "\n".join(head + body) + "\n"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# The Artifact skeleton, approximated for local viewing and the runtime gate.
PREVIEW_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>
:root { color-scheme: light; padding-top: env(safe-area-inset-top); padding-bottom: env(safe-area-inset-bottom); }
body { margin: 0; font: 14px system-ui, sans-serif; }
img { max-width: 100%; }
[hidden] { display: none !important; }
</style>
<script>if (location.hash === "#selftest") window.BOOKERY_SELFTEST = 1;
if (/~built$/.test(location.hash)) window.BOOKERY_BUILT = 1;</script>
</head>
<body>
"""


def build(number: int, root: Path | None = None, write_transcript: bool = True) -> Built:
    src = ChapterSource(number, root)
    lesson, lesson_path = load_lesson(number, root)
    widgets_dir = lesson_path.parent / "widgets"
    data, n_figures = payload(lesson, src)
    page = render_page(data, lesson, widgets_dir)

    out = build_dir(number, root)
    out.mkdir(parents=True, exist_ok=True)
    index = out / "index.html"
    index.write_text(page)
    preview = out / "preview.html"
    preview.write_text(PREVIEW_HEAD + page + "</body>\n</html>\n")
    script = out / "transcript.md"
    if write_transcript:
        script.write_text(transcript.render(data))

    title = lesson.get("title") or src.title
    about = _plain(lesson.get("subtitle") or src.title).rstrip(".")
    # Trim the lesson's own line at a word if it must, never the book's
    # title: a card cut off at "...chapter 7 of Obviously Awesome:" reads broken.
    tail = f" (an interactive lesson on chapter {number} of {src.book['title']})."
    room = DESCRIPTION_MAX - len(tail)
    if len(about) > room:
        about = about[:room - 1].rsplit(" ", 1)[0] + "…"
    publish = out / "publish.json"
    publish.write_text(json.dumps({
        "file_path": str(index),
        "title": title,
        "description": about + tail,
        "icon": "book",
        "bytes": index.stat().st_size,
    }, indent=2) + "\n")
    return Built(index, preview, publish, index.stat().st_size, n_figures, script, data)


def _plain(s: str) -> str:
    return re.sub(r"[*`]|\[\[|\]\]|\|[^\]]*\]\]", "", s)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chapter", type=int, action="append", required=True, help="repeatable")
    args = ap.parse_args()
    for n in args.chapter:
        b = build(n)
        print(f"{b.index}  ({b.bytes / 1e6:.2f} MB, {b.figures} figures)\n{b.preview}")


if __name__ == "__main__":
    main()
