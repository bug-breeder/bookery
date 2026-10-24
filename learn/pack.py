"""Write the source pack Claude reads before authoring a lesson.

The pack is the chapter's canonical model flattened into one Markdown file
where every block carries its id and printed page. That id is what a lesson
cites, and what every lesson gate resolves back to the model -- so the pack
deliberately shows ids rather than hiding them behind prose.

Run:  python -m learn.pack --chapter 2   (or: bookery learn pack --chapter 2)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from pipeline import config, textnorm

from .source import ChapterSource, build_dir


def _loc(src: ChapterSource, block: dict) -> str:
    page = src.printed_page(block)
    short = src.short_id(block["id"])
    return f"{short} p{page}" if page is not None else short


def render(src: ChapterSource) -> str:
    meta = src.meta
    lines: list[str] = []
    pages = ""
    if meta.get("printed_page") is not None and meta.get("pdf_page_end") is not None:
        pages = f" · printed pp. {config.printed_page_range(meta)}"
    lines += [
        f"# Source pack: Chapter {src.number} — {src.title}",
        "",
        f"Book: {src.book['title']}" + (f" · {src.book['author']}" if src.book["author"] else "")
        + f"{pages} · {src.words:,} words · {len(src.exhibits)} exhibits",
    ]
    if src.model.get("degraded"):
        lines.append(
            f"Degraded extraction: {(src.model.get('degraded_reason') or '').rstrip('.')}. "
            "Equations and table "
            "data may be garbled; never quote garbled text."
        )
    lines += [
        "",
        "Cite blocks by the id in brackets (e.g. `b0034`). Exhibits are referenced as "
        "`fig N.N` / `tbl N.N`, or by block id when unlabelled.",
        "",
        "## Sections (cover each in a slide, or list it under `skipped` with a reason)",
        "",
    ]
    for s in src.sections:
        page = f" p{s.printed_page}" if s.printed_page is not None else ""
        lines.append(
            f"- **{s.label}** {s.title} — starts {src.short_id(s.block_id)}{page}, "
            f"{len(s.block_ids)} blocks, {s.words:,} words"
        )
    subs = [x for x in meta.get("sections", []) if x.get("number", "").count(".") > 1]
    if subs:
        lines += ["", "Subsections (from the table of contents; no need to cover each):", ""]
        for x in subs:
            lines.append(f"  - {x['number']} {x['title']} (p{x.get('printed_page')})")
    if not src.sections:
        lines.append("- (this chapter has no numbered sections)")

    lines += ["", "## Exhibits", ""]
    if src.exhibits:
        lines.append("| ref | kind | block | page | size | image | caption |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for ex in src.exhibits:
            size = f"{ex.width}x{ex.height}" if ex.width else "-"
            image = str(ex.file) if ex.file else "(no image)"
            caption = ex.caption.replace("|", "/")[:160]
            lines.append(
                f"| {ex.ref} | {ex.kind} | {src.short_id(ex.block_id)} | "
                f"{ex.printed_page or '-'} | {size} | {image} | {caption} |"
            )
    else:
        lines.append("(none)")

    lines += ["", "## Text", ""]
    exhibit_by_block = {ex.block_id: ex for ex in src.exhibits}
    exhibit_labels = {ex.label for ex in src.exhibits if ex.label}
    for b in src.blocks:
        t = b["type"]
        text = textnorm.for_output(b.get("text") or "").strip()
        loc = _loc(src, b)
        if t == "heading":
            depth = "###" if (b.get("level") or 1) >= 2 else "##"
            lines += ["", f"{depth} {text}  [{loc}]", ""]
        elif t in ("figure", "matrix", "table"):
            ex = exhibit_by_block.get(b["id"])
            ref = ex.ref if ex else b["id"]
            cap = textnorm.for_output(b.get("caption") or "").strip()
            lines.append(f"[{loc}] ▣ EXHIBIT {ref}: {cap}")
            data = ex.data if ex else ""
            if data:
                lines.append(f"    data: {textnorm.for_output(data)}")
        elif t == "caption":
            # The labelled caption is already shown on its exhibit line.
            if b.get("label") in exhibit_labels:
                continue
            lines.append(f"[{loc} caption] {text}")
        elif t == "equation":
            lines.append(f"[{loc} eq] ${b.get('latex') or text}$")
        elif t == "footnote":
            lines.append(f"[{loc} fn] {text}")
        elif text:
            lines.append(f"[{loc}] {text}")
    return "\n".join(lines) + "\n"


def write(number: int) -> Path:
    src = ChapterSource(number)
    out = build_dir(number) / "pack.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = render(src)
    out.write_text(text)
    figures = sum(1 for ex in src.exhibits if ex.file)
    print(
        f"{out}  ({len(text):,} chars ≈ {len(text) // 4:,} tokens; {src.words:,} words, "
        f"{len(src.sections)} sections, {len(src.exhibits)} exhibits, {figures} with images)"
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chapter", type=int, action="append", required=True, help="repeatable")
    args = ap.parse_args()
    for n in args.chapter:
        write(n)


if __name__ == "__main__":
    main()
