"""Stage 4 - emit the Docusaurus site.

Chapter bodies are written as `.md`, not `.mdx`. With `markdown.format:
'detect'` those parse as CommonMark, which matters because this book's prose
contains bare `<`, `{` and `}` constantly -- under MDX each one is a build
error. Remark plugins still run, so math continues to work.

Cross-references are only turned into links when their target actually exists
in the emitted set. A reference to a chapter that has not been processed yet
is left as plain text and logged, because `onBrokenLinks: 'throw'` would
otherwise make incremental single-chapter builds impossible.

Run:  python -m pipeline.stage4_emit --chapter 2
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz

from . import config, furniture, pdfutil, textnorm

RE_XREF = re.compile(
    r"\b(Chapter|Section|Figure|Table|Exercise)\s+(\d+(?:\.\d+)?)\b"
    r"|\bEquation\s+\(?(\d+\.\d+)\)?"
)
RE_CITATION = re.compile(r"\[\s*\d{1,3}(?:\s*,\s*\d{1,3})*\s*\]")
RE_BIB_ENTRY = re.compile(r"^\[(\d{1,3})\]\s*(.*)$")

# Host names that the original sets in small caps; they extract lowercased and
# are presented in small caps rather than rewritten.
SMALL_CAPS_NODES = {
    "arpanet", "mit", "bbn", "rand", "ucla", "ucsb", "sri", "sdc", "utah",
    "stanford", "harvard", "lincoln", "case", "carnegie",
}


def slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9\s-]", "", text.lower())
    return "-".join(text.split())


def display_title(meta: dict) -> str:
    """Heading/nav text for a chapter entry.

    The Preface is modelled as chapter 0 so it gets the same pipeline as
    every numbered chapter (see pipeline/stage0_acquire.py), but it is not
    itself numbered in the book -- there is no "Chapter 0" heading to match,
    only "Preface". Every other entry keeps its "N. Title" form.
    """
    return meta["title"] if meta["number"] == 0 else f"{meta['number']}. {meta['title']}"


@dataclass
class Registry:
    """Every link target the site can currently resolve."""

    chapters: dict[int, str] = field(default_factory=dict)
    sections: dict[str, tuple[str, str]] = field(default_factory=dict)
    figures: dict[str, tuple[str, str]] = field(default_factory=dict)
    tables: dict[str, tuple[str, str]] = field(default_factory=dict)
    equations: dict[str, tuple[str, str]] = field(default_factory=dict)
    citations: set[int] = field(default_factory=set)
    has_bibliography: bool = False

    def anchors(self) -> set[str]:
        found = {f"chapter-{n}" for n in self.chapters}
        found |= {anchor for _, anchor in self.sections.values()}
        found |= {anchor for _, anchor in self.figures.values()}
        found |= {anchor for _, anchor in self.tables.values()}
        found |= {anchor for _, anchor in self.equations.values()}
        if self.has_bibliography:
            found |= {f"ref-{n}" for n in self.citations}
        return found


def doc_dir_for(part: dict | None) -> str:
    return part["slug"] if part else ""


def doc_path_for(triage: dict, chapter: dict) -> str:
    """Route path of a chapter document, without extension."""
    parts = {p["number"]: p for p in triage["boundary_map"]["parts"]}
    part = parts.get(chapter.get("part")) if chapter.get("part") else None
    filename = f"{chapter['number']:02d}-{slugify(chapter['title'])}"
    return f"{doc_dir_for(part)}/{filename}" if part else filename


def build_registry(triage: dict, emitted: list[int], assets: dict) -> Registry:
    registry = Registry()
    by_number = {c["number"]: c for c in triage["boundary_map"]["chapters"]}

    for number in emitted:
        chapter = by_number[number]
        path = doc_path_for(triage, chapter)
        registry.chapters[number] = path
        for section in chapter["sections"]:
            anchor = "sec-" + section["number"].replace(".", "-")
            registry.sections[section["number"]] = (path, anchor)
        for asset in assets.get(config.chapter_id(number), []):
            label = asset.get("label")
            if label and not asset.get("issue"):
                registry.figures[label] = (path, "fig-" + label.replace(".", "-"))

        # Payoff matrices are figures to the reader and to the prose, but they
        # have no asset, so the asset list alone does not know they exist.
        # Without this, "as shown in Figure 6.1" resolves to nothing for the 25
        # matrices in chapter 6 and every one of those references is dropped.
        model_path = config.RECONCILE_DIR / f"{config.chapter_id(number)}.json"
        if model_path.exists():
            model = json.loads(model_path.read_text())
            for block in model.get("blocks", []):
                label = block.get("label")
                if block.get("type") == "matrix" and label:
                    registry.figures[label] = (path, "fig-" + label.replace(".", "-"))
                elif block.get("type") == "equation" and label:
                    registry.equations[label] = (path, "eq-" + label.replace(".", "-"))

    return registry


# --------------------------------------------------------------------------
# Cross-reference rewriting
# --------------------------------------------------------------------------


def link_references(text: str, registry: Registry, unresolved: list[dict], page: int) -> str:
    """Rewrite prose references into links where the target exists."""

    def replace_xref(match: re.Match[str]) -> str:
        whole = match.group(0)
        if match.group(3) is not None:
            kind, number = "Equation", match.group(3)
        else:
            kind, number = match.group(1), match.group(2)
        lookup = {
            "Chapter": lambda: (
                (registry.chapters[int(number)], f"chapter-{number}")
                if number.isdigit() and int(number) in registry.chapters
                else None
            ),
            "Section": lambda: registry.sections.get(number),
            "Figure": lambda: registry.figures.get(number),
            "Table": lambda: registry.tables.get(number),
            "Equation": lambda: registry.equations.get(number),
        }.get(kind, lambda: None)

        target = lookup()
        if not target:
            unresolved.append({"label": whole, "page": page, "reason": "target_not_emitted"})
            return whole
        path, anchor = target
        if kind == "Chapter":
            return f"[{whole}](/{path})"
        return f"[{whole}](/{path}#{anchor})"

    text = RE_XREF.sub(replace_xref, text)

    def replace_citation(match: re.Match[str]) -> str:
        numbers = [int(n) for n in re.findall(r"\d+", match.group(0))]
        if not registry.has_bibliography:
            unresolved.append(
                {"label": match.group(0), "page": page, "reason": "no_bibliography"}
            )
            return match.group(0)
        # Bracketed number lists also occur as interval notation in prose,
        # e.g. "the interval [0, 1]". A real citation only ever names
        # bibliography entries, so a bracket whose numbers are not all valid
        # entries -- 0 is never one, since entries start at 1 -- is math
        # notation, not a citation, and is left exactly as printed.
        if not all(n in registry.citations for n in numbers):
            return match.group(0)
        links = ", ".join(f"[{n}](/bibliography#ref-{n})" for n in numbers)
        return f"[{links}]"

    return RE_CITATION.sub(replace_citation, text)


def apply_small_caps(text: str, words: list[str]) -> str:
    """Re-present small-caps words without altering their characters.

    The PDF's text layer stores "mit" for a name the page shows as MIT in small
    caps. Uppercasing it would put characters in the output that the source
    does not contain and would break the coverage comparison, so the casing is
    restored with styling instead.
    """
    for word in sorted(set(words), key=len, reverse=True):
        bare = word.strip(".,;:()[]")
        if len(bare) < 2:
            continue
        text = re.sub(
            rf"(?<![\w>]){re.escape(bare)}(?![\w<])",
            f'<span class="node">{bare}</span>',
            text,
        )
    return text


def escape_commonmark(text: str) -> str:
    """Escape the few characters CommonMark would misread as markup.

    Deliberately minimal: `<`, `{` and `}` are safe in CommonMark, so the
    aggressive escaping MDX would need is not applied here.
    """
    return re.sub(r"(?<!\\)([*_`])", r"\\\1", text)


# --------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------


def markdown_table(cells: list[list[str]]) -> list[str]:
    """Render a grid as a GFM table, with the axes emphasised.

    A payoff matrix reads as a header row of one player's strategies and a
    header column of the other's, so both are set in bold. Cell contents are
    escaped: a pipe would end the cell early, and a lone backslash -- which the
    corner cell uses to separate the two player names -- would escape whatever
    followed it.
    """
    if not cells:
        return []

    def cell(text: str, emphasise: bool) -> str:
        text = text.replace("\\", "\\\\").replace("|", "\\|").strip()
        return f"**{text}**" if emphasise and text else text

    width = max(len(row) for row in cells)
    rows = [list(row) + [""] * (width - len(row)) for row in cells]

    out = ["| " + " | ".join(cell(c, True) for c in rows[0]) + " |"]
    out.append("| " + " | ".join("---" for _ in range(width)) + " |")
    for row in rows[1:]:
        out.append(
            "| " + " | ".join(cell(c, i == 0) for i, c in enumerate(row)) + " |"
        )
    out.append("")
    return out


def emit_chapter(
    chapter: int, triage: dict, assets: dict, registry: Registry
) -> tuple[Path, list[dict]]:
    cid = config.chapter_id(chapter)
    model = json.loads((config.RECONCILE_DIR / f"{cid}.json").read_text())
    meta = next(c for c in triage["boundary_map"]["chapters"] if c["number"] == chapter)

    # Keyed by block id, not label: an uncaptioned figure has no label at
    # all, and looking it up by label would collide every uncaptioned
    # figure on the dict's single `None` key, silently dropping every one
    # of them but the last from the emitted page.
    asset_by_block = {
        a["block"]: a for a in assets.get(cid, []) if a.get("block") and not a.get("issue")
    }

    unresolved: list[dict] = []
    lines: list[str] = []

    # Sub-captions ("(a) A graph on 4 nodes.") sit under their panel, above the
    # figure's own caption. Ordered by position alone they land after it,
    # reading as an orphaned line, so each is emitted with its figure.
    sub_captions: dict[str, list[dict]] = {}
    consumed: set[str] = set()
    for figure in (b for b in model["blocks"] if b.get("type") == "figure" and b.get("bbox")):
        fx0, fy0, fx1, fy1 = figure["bbox"]
        for block in model["blocks"]:
            if block.get("type") != "caption" or block.get("label") or not block.get("bbox"):
                continue
            if block["page"] != figure["page"] or block["id"] in consumed:
                continue
            bx0, by0, bx1, by1 = block["bbox"]
            within_columns = bx0 < fx1 and bx1 > fx0
            below_or_inside = fy0 <= by0 <= fy1 + 12
            if within_columns and below_or_inside:
                sub_captions.setdefault(figure["id"], []).append(block)
                consumed.add(block["id"])

    sidebar_position = chapter
    lines.append("---")
    # Docusaurus treats a leading "NN-" in a filename as an ordering prefix and
    # strips it from the document id. Setting the id explicitly keeps the
    # chapter number in the route, which the cross-reference links rely on.
    title = display_title(meta)
    description = (
        "Preface to Networks, Crowds, and Markets."
        if meta["number"] == 0
        else f"Chapter {meta['number']} of Networks, Crowds, and Markets."
    )
    lines.append(f"id: {chapter:02d}-{slugify(meta['title'])}")
    lines.append(f'title: "{title}"')
    lines.append(f'sidebar_label: "{title}"')
    lines.append(f"sidebar_position: {sidebar_position}")
    lines.append(f'description: "{description}"')
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")

    printed_first = meta["printed_page"]
    offset = triage["boundary_map"]["printed_to_pdf_offset"]

    for block in model["blocks"]:
        kind = block.get("type")
        text = block.get("text", "")
        page = block.get("page", 0)

        if kind == "heading":
            level = block.get("level", 2)
            label = block.get("label")
            if level == 1:
                # The chapter opener heading is already emitted as the title.
                continue
            anchor = block.get("anchor")
            body = link_references(text, registry, unresolved, page)
            suffix = f" {{#{anchor}}}" if anchor else ""
            lines.append(f"## {body}{suffix}")
            lines.append("")
            continue

        if kind == "figure":
            label = block.get("label")
            asset = asset_by_block.get(block["id"])
            anchor = "fig-" + label.replace(".", "-") if label else None
            if anchor:
                lines.append(f'<a id="{anchor}"></a>')
                lines.append("")
            if asset:
                # Brackets inside alt text terminate the image syntax early and
                # the image then renders as literal prose, which no gate that
                # only checks for the file on disk would notice.
                alt = re.sub(r"[\[\]]", "", asset["alt"]).replace('"', "'")
                lines.append(f"![{alt}](/{asset['file']})")
                lines.append("")
            for sub in sorted(
                sub_captions.get(block["id"], []), key=lambda b: (b["bbox"][1], b["bbox"][0])
            ):
                body = link_references(sub.get("text", ""), registry, unresolved, page)
                lines.append(f'<span class="figure-caption">{body}</span>')
                lines.append("")
            caption = block.get("caption")
            if label:
                # A caption is only ever partly optional: the book always
                # prints "Figure N.N:", but a handful of exercise figures
                # (14.15, 14.18, 14.22) print nothing after it. The label
                # line still belongs in the output either way.
                rendered = (
                    link_references(caption, registry, unresolved, page)
                    if caption
                    else ""
                )
                rendered = apply_small_caps(rendered, block.get("small_caps") or [])
                suffix = f" {rendered}" if rendered.strip() else ""
                lines.append(
                    f'<span class="figure-caption">**Figure {label}:**{suffix}</span>'
                )
                lines.append("")
            continue

        if kind == "caption":
            # Sub-captions such as "(a) A graph on 4 nodes." carry content and
            # are emitted; the main figure caption is emitted with its figure.
            if block.get("label") or block["id"] in consumed:
                continue
            body = link_references(text, registry, unresolved, page)
            body = apply_small_caps(body, block.get("small_caps") or [])
            lines.append(f'<span class="figure-caption">{body}</span>')
            lines.append("")
            continue

        if kind == "matrix":
            # A payoff matrix is emitted as a real table rather than a bitmap,
            # so its payoffs stay selectable, searchable and readable aloud.
            # The book captions these "Figure N.N" and cross-references them as
            # figures, so the anchor is a figure anchor: rewriting them as
            # tables here would break every "see Figure 6.1" in the prose.
            label = block.get("label")
            if label:
                lines.append(f'<a id="fig-{label.replace(".", "-")}"></a>')
                lines.append("")
            lines.extend(markdown_table(block.get("cells") or []))
            caption = block.get("caption")
            if label:
                rendered = (
                    link_references(caption, registry, unresolved, page)
                    if caption
                    else ""
                )
                rendered = apply_small_caps(rendered, block.get("small_caps") or [])
                suffix = f" {rendered}" if rendered.strip() else ""
                lines.append(
                    f'<span class="figure-caption">**Figure {label}:**{suffix}</span>'
                )
                lines.append("")
            continue

        if kind == "table":
            cells = block.get("cells") or []
            label = block.get("label")
            if label:
                lines.append(f'<a id="tbl-{label.replace(".", "-")}"></a>')
                lines.append("")
            lines.extend(markdown_table(cells))
            if block.get("caption") and label:
                rendered = link_references(block["caption"], registry, unresolved, page)
                lines.append(
                    f'<span class="figure-caption">**Table {label}:** {rendered}</span>'
                )
                lines.append("")
            continue

        if kind == "equation":
            label = block.get("label")
            if label:
                lines.append(f'<a id="eq-{label.replace(".", "-")}"></a>')
                lines.append("")
            latex = block.get("latex") or text
            lines.append("$$")
            lines.append(latex)
            lines.append("$$")
            lines.append("")
            continue

        if kind == "exercise_part":
            # Indented via CSS, not whitespace: four leading spaces would make
            # CommonMark render the sub-part as an indented code block, since
            # intervening paragraphs break it away from its list item.
            body = link_references(text, registry, unresolved, page)
            body = apply_small_caps(body, block.get("small_caps") or [])
            lines.append(f'<span class="exercise-part">{body}</span>')
            lines.append("")
            continue

        if kind in ("paragraph", "exercise", "footnote"):
            body = link_references(text, registry, unresolved, page)
            body = apply_small_caps(body, block.get("small_caps") or [])
            lines.append(body)
            lines.append("")
            continue

    path = config.DOCS / (doc_path_for(triage, meta) + ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n")
    return path, unresolved


def emit_bibliography(triage: dict, pdf: Path) -> tuple[Path, set[int]]:
    """Emit the bibliography, one anchor per numbered entry."""
    bm = triage["boundary_map"]
    first = bm["bibliography_pdf_page"]
    last = bm["bibliography_last_pdf_page"]
    if not first:
        raise SystemExit("no bibliography in this PDF")

    doc = fitz.open(pdf)
    page_furniture = furniture.load_or_detect(doc, triage)
    body_size = furniture.body_size_for(doc, first, last)

    entries: dict[int, list[str]] = {}
    current: int | None = None

    for page_no in range(first, last + 1):
        height = doc[page_no - 1].rect.height
        for row in pdfutil.page_lines(doc, page_no):
            if page_furniture.reason_for(
                row.text, row.y0, row.y1, row.size, body_size, height
            ):
                continue
            if row.text.strip() == "Bibliography":
                continue
            match = RE_BIB_ENTRY.match(row.text)
            if match:
                current = int(match.group(1))
                entries[current] = [match.group(2).strip()]
            elif current is not None:
                entries[current].append(row.text.strip())

    lines = [
        "---",
        'title: "Bibliography"',
        "sidebar_label: Bibliography",
        "sidebar_position: 900",
        "---",
        "",
        "# Bibliography",
        "",
    ]
    for number in sorted(entries):
        body = " ".join(entries[number])
        body = re.sub(r"\s+", " ", textnorm.for_output(body)).strip()
        lines.append(f'<a id="ref-{number}"></a>')
        lines.append("")
        lines.append(f"**[{number}]** {body}")
        lines.append("")

    path = config.DOCS / "bibliography.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n")
    return path, set(entries)


def emit_home(triage: dict, emitted: list[int]) -> Path:
    """Landing page at the site root, listing what has been processed.

    With `routeBasePath: '/'` the docs plugin owns the root route, so a
    document must claim `slug: /` or every navbar link to `/` is broken.
    """
    bm = triage["boundary_map"]
    by_number = {c["number"]: c for c in bm["chapters"]}
    lines = [
        "---",
        "id: index",
        'title: "Networks, Crowds, and Markets"',
        "sidebar_label: Overview",
        "sidebar_position: 0",
        "slug: /",
        "---",
        "",
        "# Networks, Crowds, and Markets",
        "",
        "Reasoning about a Highly Connected World, by David Easley and Jon",
        "Kleinberg. This site is a local, automatically generated transcription",
        "used to validate an extraction pipeline.",
        "",
        "> **Licensing.** The source PDF is published freely by its authors, but",
        "> the book is copyright Cambridge University Press. This build is a local",
        "> validation artifact and is not for publication or redeployment.",
        "",
        f"Processed so far: {len(emitted)} of {len(bm['chapters'])} chapters.",
        "",
    ]

    # Chapters that precede Part I are listed first, as they are read.
    top_level = sorted(n for n in emitted if not by_number[n].get("part"))
    if top_level:
        lines.append("## Front matter")
        lines.append("")
        for number in top_level:
            chapter = by_number[number]
            lines.append(
                f"- [{display_title(chapter)}](/{doc_path_for(triage, chapter)})"
            )
        lines.append("")

    for part in bm["parts"]:
        members = [n for n in part["chapters"] if n in emitted]
        if not members:
            continue
        lines.append(f"## {part['roman']}. {part['title']}")
        lines.append("")
        for number in members:
            chapter = by_number[number]
            path = doc_path_for(triage, chapter)
            lines.append(f"- [{number}. {chapter['title']}](/{path})")
        lines.append("")

    path = config.DOCS / "index.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n")
    return path


def emit_sidebars(triage: dict, emitted: list[int], has_bibliography: bool) -> Path:
    """Generate sidebars.ts from the boundary map. Never hand-written."""
    bm = triage["boundary_map"]
    by_number = {c["number"]: c for c in bm["chapters"]}
    items: list[str] = ["    'index',"]

    # Chapters that precede Part I sit at the top level.
    for number in sorted(n for n in emitted if not by_number[n].get("part")):
        items.append(f"    '{doc_path_for(triage, by_number[number])}',")

    for part in bm["parts"]:
        members = [n for n in part["chapters"] if n in emitted]
        if not members:
            continue
        items.append("    {")
        items.append("      type: 'category',")
        items.append(f"      label: '{part['roman']}. {part['title']}',")
        items.append("      collapsed: false,")
        items.append("      items: [")
        for number in members:
            items.append(f"        '{doc_path_for(triage, by_number[number])}',")
        items.append("      ],")
        items.append("    },")

    if has_bibliography:
        items.append("    'bibliography',")

    body = "\n".join(items)
    content = f"""// Generated by pipeline/stage4_emit.py from work/triage.json.
// Do not edit by hand: regenerate with `python -m pipeline.stage4_emit`.
import type {{SidebarsConfig}} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {{
  book: [
{body}
  ],
}};

export default sidebars;
"""
    path = config.SITE / "sidebars.ts"
    path.write_text(content)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 4: emit the Docusaurus site")
    ap.add_argument("--chapter", type=int, action="append", required=True)
    ap.add_argument("--pdf", type=Path, default=config.DEFAULT_PDF)
    ap.add_argument("--skip-bibliography", action="store_true")
    args = ap.parse_args()

    triage = json.loads(config.TRIAGE_JSON.read_text())
    assets = json.loads(config.ASSETS_JSON.read_text()) if config.ASSETS_JSON.exists() else {}

    citations: set[int] = set()
    if not args.skip_bibliography:
        bib_path, citations = emit_bibliography(triage, args.pdf)
        print(f"bibliography: {len(citations)} entries -> {bib_path.name}")

    registry = build_registry(triage, args.chapter, assets)
    registry.citations = citations
    registry.has_bibliography = bool(citations)

    for number in args.chapter:
        path, unresolved = emit_chapter(number, triage, assets, registry)
        by_reason: dict[str, int] = {}
        for item in unresolved:
            by_reason[item["reason"]] = by_reason.get(item["reason"], 0) + 1
        print(
            f"ch{number:02d}: -> {path.relative_to(config.SITE)} "
            f"({len(unresolved)} references left as plain text: {by_reason})"
        )

        # Record what could not be linked so the harness can attribute it.
        report_path = config.WORK / f"xrefs-{config.chapter_id(number)}.json"
        report_path.write_text(
            json.dumps(
                {"anchors": sorted(registry.anchors()), "unresolved": unresolved},
                indent=1,
            )
        )

    home = emit_home(triage, args.chapter)
    print(f"home: {home.relative_to(config.SITE)}")

    sidebar = emit_sidebars(triage, args.chapter, bool(citations))
    print(f"sidebar: {sidebar.relative_to(config.SITE)}")


if __name__ == "__main__":
    main()
