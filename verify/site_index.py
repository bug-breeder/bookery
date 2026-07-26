"""Inventory of routes and anchors that actually exist in the emitted site.

Gate 6 resolves cross-references against this rather than against the
pipeline's own intentions: the question is whether a link in the emitted
Markdown lands on something a reader can reach.

Docusaurus performs the same check at build time, but doing it here gives
page-level attribution and works without a full build.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

RE_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
RE_FM_FIELD = re.compile(r"^(id|slug):\s*(.+?)\s*$", re.MULTILINE)
RE_HEADING_ANCHOR = re.compile(r"^\s{0,3}#{1,6}\s+.*?\{#([A-Za-z0-9_.:-]+)\}\s*$", re.MULTILINE)
RE_HTML_ANCHOR = re.compile(r"""<a\s+id=["']([^"']+)["']""")
# Internal links only: site-absolute, so "/img/..." style assets are excluded
# by requiring the target not to look like a file.
RE_LINK = re.compile(r"\]\((/[^)\s]*)\)")


@dataclass
class SiteIndex:
    anchors: dict[str, set[str]] = field(default_factory=dict)

    @property
    def routes(self) -> set[str]:
        return set(self.anchors)

    def resolve(self, route: str, anchor: str | None) -> bool:
        route = route.rstrip("/") or "/"
        if route not in self.anchors:
            return False
        return anchor is None or anchor in self.anchors[route]


def _route_for(path: Path, docs_dir: Path, front: dict[str, str]) -> str:
    if "slug" in front and front["slug"].startswith("/"):
        return front["slug"].rstrip("/") or "/"
    relative = path.relative_to(docs_dir)
    stem = front.get("id") or re.sub(r"^\d+-", "", relative.stem)
    parent = str(relative.parent)
    route = stem if parent in (".", "") else f"{parent}/{stem}"
    return "/" + route.strip("/")


def _front_matter(text: str) -> dict[str, str]:
    match = RE_FRONTMATTER.search(text)
    if not match:
        return {}
    return {k: v.strip().strip("\"'") for k, v in RE_FM_FIELD.findall(match.group(1))}


def build(docs_dir: Path) -> SiteIndex:
    index = SiteIndex()
    if not docs_dir.exists():
        return index
    for path in sorted(docs_dir.rglob("*.md")):
        text = path.read_text()
        front = _front_matter(text)
        route = _route_for(path, docs_dir, front)
        anchors = set(RE_HEADING_ANCHOR.findall(text)) | set(RE_HTML_ANCHOR.findall(text))
        index.anchors.setdefault(route, set()).update(anchors)
    return index


def links_in(markdown: str) -> list[tuple[str, str | None]]:
    """Every site-internal link target in a document, as (route, anchor)."""
    found: list[tuple[str, str | None]] = []
    for target in RE_LINK.findall(markdown):
        if "." in target.rsplit("/", 1)[-1]:
            # An asset path such as /img/ch02/fig-2-1.png, not a page.
            continue
        route, _, anchor = target.partition("#")
        found.append(((route.rstrip("/") or "/"), anchor or None))
    return found
