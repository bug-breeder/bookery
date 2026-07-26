"""Regenerates PROGRESS.md from the pipeline's own artifacts.

Progress is derived, never asserted. Every value here is read back from
triage.json, the stage outputs on disk, and the gate reports, so a chapter
cannot be recorded as done while its gates disagree. Hand-maintained status
files drift the moment someone forgets to update them; this one cannot.

Intent lives in PLAN.md and is written by hand. State lives here and is
generated. The two must not be mixed.

Run:  python -m pipeline.status
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import config

# Phase definitions are intent, not state: which chapter is used to prove which
# capability, and in what order. The *state* of each phase is computed from the
# gate reports below.
PHASES: list[dict] = [
    {
        "number": 0,
        "name": "Foundations",
        "goal": "Environment, scaffold, page/chapter triage, 8-gate harness",
        "chapters": [],
        "done_when": "harness",
    },
    {
        "number": 1,
        "name": "Prose pilot",
        "goal": "One ordinary chapter green on all eight gates",
        "chapters": [2],
    },
    {
        "number": 2,
        "name": "Tables",
        "goal": "Payoff matrices emitted as Markdown tables, every matrix verified",
        "chapters": [6],
    },
    {
        "number": 3,
        "name": "Mathematics",
        "goal": "Spectral chapter with every equation KaTeX-clean under strict mode",
        "chapters": [14],
    },
    {
        "number": 4,
        "name": "Front matter",
        "goal": "Overview chapter and the Preface",
        "chapters": [1, 0],
    },
    {
        "number": 5,
        "name": "Bulk run",
        "goal": "The remaining chapters, batched by part",
        "chapters": "rest",
    },
    {
        "number": 6,
        "name": "Ship",
        "goal": "Search, MANIFEST.json, README, accessibility and site polish",
        "chapters": [],
        "done_when": "manual",
    },
]

GATE_NAMES = (
    "text_coverage",
    "numeric_fidelity",
    "structural_counts",
    "assets",
    "math",
    "xrefs",
    "build",
    "visual",
)

PASSING = {"PASS", "REVIEWED"}


@dataclass
class ChapterState:
    number: int
    title: str
    printed: str
    extracted: bool
    reconciled: bool
    has_assets: bool
    emitted: bool
    gates_passed: int
    gates_total: int
    status: str
    pages: int

    @property
    def is_green(self) -> bool:
        return self.status == "green"


def _chapter_state(chapter: dict, assets: dict) -> ChapterState:
    number = chapter["number"]
    cid = config.chapter_id(number)

    marker = (config.EXTRACT_DIR / "marker" / f"{cid}.json").exists()
    docling = (config.EXTRACT_DIR / "docling" / f"{cid}.json").exists()
    reconciled = (config.RECONCILE_DIR / f"{cid}.json").exists()
    # Presence of the key means stage3 ran for this chapter, even if it found
    # zero figures (the Preface). An empty list is a real, checked result,
    # not the same thing as the stage never having run.
    has_assets = cid in assets
    emitted = bool(sorted(config.DOCS.rglob(f"{number:02d}-*.md"))) if config.DOCS.exists() else False

    report_path = config.REPORTS / f"{cid}.json"
    passed = 0
    status = "not started"
    if report_path.exists():
        report = json.loads(report_path.read_text())
        gates = report.get("gates", {})
        passed = sum(1 for name in GATE_NAMES if gates.get(name, {}).get("status") in PASSING)
        if passed == len(GATE_NAMES):
            status = "green"
        elif any(g.get("status") == "FAIL" for g in gates.values()):
            status = "failing"
        else:
            status = "in progress"
    elif reconciled or marker:
        status = "in progress"

    return ChapterState(
        number=number,
        title=chapter["title"],
        printed=config.printed_page_range(chapter),
        extracted=marker and docling,
        reconciled=reconciled,
        has_assets=has_assets,
        emitted=emitted,
        gates_passed=passed,
        gates_total=len(GATE_NAMES),
        status=status,
        pages=chapter["pdf_page_end"] - chapter["pdf_page"] + 1,
    )


def _phase_state(phase: dict, states: dict[int, ChapterState]) -> str:
    if phase.get("done_when") == "harness":
        return "complete" if config.TRIAGE_JSON.exists() else "not started"
    if phase.get("done_when") == "manual":
        return "not started"

    chapters = phase["chapters"]
    if chapters == "rest":
        claimed = {n for p in PHASES if isinstance(p["chapters"], list) for n in p["chapters"]}
        chapters = [n for n in states if n not in claimed]

    if not chapters:
        return "not started"
    green = [n for n in chapters if states[n].is_green]
    started = [n for n in chapters if states[n].status != "not started"]

    if len(green) == len(chapters):
        return "complete"
    if started:
        return f"in progress ({len(green)}/{len(chapters)} green)"
    return "not started"


def build() -> str:
    triage = json.loads(config.TRIAGE_JSON.read_text())
    bm = triage["boundary_map"]
    assets = json.loads(config.ASSETS_JSON.read_text()) if config.ASSETS_JSON.exists() else {}

    states = {c["number"]: _chapter_state(c, assets) for c in bm["chapters"]}
    green = [s for s in states.values() if s.is_green]
    total_pages = sum(s.pages for s in states.values())
    green_pages = sum(s.pages for s in green)
    preface_state = states.get(0)
    preface_status = preface_state.status if preface_state else "not started"

    bibliography = 0
    bib_path = config.DOCS / "bibliography.md"
    if bib_path.exists():
        import re

        bibliography = len(re.findall(r"^<a id=\"ref-\d+\">", bib_path.read_text(), re.M))

    rows: list[str] = [
        "# Progress",
        "",
        "Generated by `python -m pipeline.status`. Do not edit by hand: every",
        "value is read back from the pipeline's artifacts and gate reports, so a",
        "chapter cannot be recorded as done while its gates disagree. The plan and",
        "the conventions a new session needs are in PLAN.md.",
        "",
        f"- Chapters green: **{len(green)} of {len(states)}**",
        f"- Chapter pages green: **{green_pages} of {total_pages}** "
        f"({green_pages / total_pages:.1%})",
        f"- Bibliography entries: {bibliography}",
        f"- Preface: {preface_status}",
        "",
        "A chapter is *green* only when all eight gates pass. `visual` reports",
        "REVIEWED once every flagged page has an adjudication verdict on record.",
        "",
        "## Phases",
        "",
        "| Phase | Name | Goal | Chapters | State |",
        "| --- | --- | --- | --- | --- |",
    ]

    for phase in PHASES:
        chapters = phase["chapters"]
        if chapters == "rest":
            claimed = {n for p in PHASES if isinstance(p["chapters"], list) for n in p["chapters"]}
            chapters = [n for n in states if n not in claimed]
        listed = ", ".join(str(n) for n in chapters) if chapters else "-"
        state = _phase_state(phase, states)
        rows.append(
            f"| {phase['number']} | {phase['name']} | {phase['goal']} | {listed} | {state} |"
        )

    rows += [
        "",
        "## Chapters",
        "",
        "Stage columns are `x` when that stage's artifact exists on disk.",
        "",
        "| Ch | Title | Printed pp. | Extract | Reconcile | Assets | Emit | Gates | Status |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for number in sorted(states):
        s = states[number]
        mark = lambda flag: "x" if flag else " "  # noqa: E731
        gates = f"{s.gates_passed}/{s.gates_total}" if s.gates_passed else "-"
        rows.append(
            f"| {s.number} | {s.title} | {s.printed} | {mark(s.extracted)} | "
            f"{mark(s.reconciled)} | {mark(s.has_assets)} | {mark(s.emitted)} | "
            f"{gates} | {s.status} |"
        )

    outstanding: list[str] = []
    for path in sorted(config.REPORTS.glob("ch*.json")):
        report = json.loads(path.read_text())
        for item in report.get("review_queue") or []:
            outstanding.append(
                f"- Chapter {report['chapter']}: {item.get('kind')} "
                f"{item.get('gate') or item.get('page') or ''}".rstrip()
            )

    rows += ["", "## Outstanding review items", ""]
    rows += outstanding or ["None. Every processed chapter is fully adjudicated."]

    rows += [
        "",
        "## Known limitations",
        "",
        "- Marker runs in fast mode with no LLM pass; equation-heavy chapters are",
        "  the weakest case and are attacked in phase 3 before the bulk run.",
        "- Docling's layout model is pinned to CPU because its float64 tensors are",
        "  unsupported on MPS.",
        "- The scanned-PDF path, where model text would become primary instead of",
        "  the text layer, is not exercised by this fixture.",
        "- An indented list distinguished only by layout, not by a recurring marker",
        "  pattern (the Preface's weekly schedule), is reconciled as one flowing",
        "  paragraph rather than separate list lines. No content is lost -- every",
        "  gate is token- and number-exact -- but it reads as a wall of text where",
        "  the source prints one line per week.",
    ]
    return "\n".join(rows) + "\n"


def write() -> None:
    path = config.ROOT / "PROGRESS.md"
    path.write_text(build())
    print(f"wrote {path.name}")


if __name__ == "__main__":
    write()
