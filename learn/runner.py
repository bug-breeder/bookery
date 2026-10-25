"""Verify a lesson: static gates, build, then the page in a real browser.

    python -m learn.runner verify --chapter 2 [--no-browser]
    python -m learn.runner shot   --chapter 2 [--slide ID] [--present] [--dark] [--phone]
    python -m learn.runner record --chapter 2 --url https://claude.ai/...

`verify` writes reports/learn-chNN.json and exits non-zero unless every gate
passes. The runtime gate loads preview.html#selftest in headless Chrome,
which mounts every slide in study and present mode, answers every recall
check, and reports script errors, slides that overflow the 1600x900 stage,
broken images, checks that give no feedback, and horizontal scroll at
desktop and phone widths. Without a browser the gate is
BLOCKED, not PASS -- the same rule as the site gates.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pipeline import config
from verify.gates import BLOCKED, FAIL, PASS, Gate

from . import build as build_mod
from . import gates, transcript
from .source import ChapterSource, build_dir, lesson_dir

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
]

VIEWPORTS = {"desktop": (1280, 900), "phone": (400, 860)}
RE_SELFTEST = re.compile(r'data-selftest="([^"]*)"')
# Headless Chrome won't open a window narrower than this (on macOS at
# least), so narrower viewports load the page in an iframe of that width.
MIN_WINDOW = 500


def find_chrome() -> str | None:
    env = os.environ.get("BOOKERY_CHROME")
    if env:
        return env
    for c in CHROME_CANDIDATES:
        path = c if os.path.isabs(c) else shutil.which(c)
        if path and os.path.exists(path):
            return path
    return None


def _chrome(chrome: str, url: str, size: tuple[int, int], extra: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory(prefix="bookery-chrome-") as profile:
        argv = [
            chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--no-first-run", "--no-default-browser-check", "--mute-audio",
            f"--user-data-dir={profile}", f"--window-size={size[0]},{size[1]}",
            *extra, url,
        ]
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _framed(preview: Path, anchor: str, size: tuple[int, int], frame: bool = False) -> tuple[str, tuple[int, int]]:
    """The URL and window size that show `preview` at `size`. A viewport
    narrower than Chrome's minimum window is an iframe in a wrapper page,
    which relays the self-test result the page posts to its parent. So is
    one that must be scrolled (`frame`): headless Chrome screenshots a
    scrolled top-level page as blank, but a scrolled iframe paints."""
    if size[0] >= MIN_WINDOW and not frame:
        return preview.as_uri() + anchor, size
    wrapper = preview.with_name(f"frame-{size[0]}.html")
    wrapper.write_text(
        '<!doctype html><meta charset="utf-8"><body style="margin:0;background:#808080">'
        f'<iframe src="{preview.name}{anchor}" width="{size[0]}" height="{size[1]}" '
        'style="border:0;display:block"></iframe><script>'
        'addEventListener("message",function(e){if(e.data&&e.data.bookerySelftest)'
        'document.documentElement.setAttribute("data-selftest",JSON.stringify(e.data.bookerySelftest))})'
        "</script>",
        encoding="utf-8",
    )
    return wrapper.as_uri(), (max(MIN_WINDOW, size[0]), size[1])


def selftest(chrome: str, preview: Path, size: tuple[int, int]) -> dict:
    url, window = _framed(preview, "#selftest", size)
    proc = _chrome(chrome, url, window, ["--virtual-time-budget=20000", "--dump-dom"])
    m = RE_SELFTEST.search(proc.stdout or "")
    if not m:
        return {"ok": False, "errors": ["selftest never reported (page failed to boot?)"],
                "stderr": (proc.stderr or "")[-800:]}
    return json.loads(html.unescape(m.group(1)))


def gate_runtime(preview: Path, no_browser: bool = False) -> Gate:
    if no_browser:
        return Gate("runtime", BLOCKED, {"reason": "--no-browser"})
    chrome = find_chrome()
    if chrome is None:
        return Gate("runtime", BLOCKED, {"reason": "no Chrome/Chromium found (set BOOKERY_CHROME)"})
    runs = {}
    for name, size in VIEWPORTS.items():
        try:
            runs[name] = selftest(chrome, preview, size)
        except subprocess.TimeoutExpired:
            runs[name] = {"ok": False, "errors": ["browser timed out"]}
    desktop = runs["desktop"]
    detail = {
        "slides": desktop.get("slides"),
        "errors": sorted({e for r in runs.values() for e in r.get("errors") or []})[:40],
        "present_overflow": [dict(o, viewport=k) for k, r in runs.items() for o in r.get("overflow") or []],
        "broken_images": desktop.get("images", {}).get("broken") or [],
        "dead_widgets": desktop.get("dead") or [],
        "images": desktop.get("images", {}).get("total"),
        "math": desktop.get("math"),
        "hscroll": {k: r.get("hscroll") for k, r in runs.items()},
        "shown": desktop.get("shown") or {},
    }
    if any(r.get("stderr") for r in runs.values()):
        detail["stderr"] = next(r["stderr"] for r in runs.values() if r.get("stderr"))
    ok = all(r.get("ok") for r in runs.values())
    return Gate("runtime", PASS if ok else FAIL, detail)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _summary(g: Gate) -> str:
    d = g.detail
    if g.status == BLOCKED:
        return d.get("reason", "")
    if g.name == "schema":
        return f"{d['count']} problems" + (f": {d['problems'][0]['where']}: {d['problems'][0]['message']}" if d["problems"] else "")
    if g.name == "grounding":
        bad = len(d["uncited_slides"]) + len(d["unresolved_sources"])
        return f"{d['content_slides']} content slides" + (f", {bad} uncited/unresolved" if bad else ", all cited")
    if g.name == "numbers":
        s = f"{d['numbers_checked']} numbers, {d['invented_count']} not in the chapter"
        if d["invented"]:
            x = d["invented"][0]
            s += f" (e.g. {x['value']} in {x['slide']}.{x['field']})"
        review = len(d.get("derived_for_review") or []) + len(d.get("transcribed_for_review") or [])
        if review:
            s += f"; {review} widget(s) to review (derived or transcribed, see report)"
        return s
    if g.name == "quotes":
        return f"{d['quotes']} quotes, {len(d['failures'])} not verbatim"
    if g.name == "coverage":
        if "note" in d:
            return d["note"]
        taught = sum(1 for r in d["sections"] if r["status"] == "taught")
        skipped = sum(1 for r in d["sections"] if r["status"] == "skipped")
        s = f"{taught} taught, {skipped} skipped, {len(d['missing'])} missing"
        return s + (f" ({', '.join(d['missing'])})" if d["missing"] else "")
    if g.name == "density":
        s = (f"{d['slides']} slides, {d['interactive']} interactive, {d['recall_checks']} recall, "
             f"{d['visual_share']:.0%} visual, {len(d['exhibits_used'])} exhibits")
        problems = len(d["over_budget"]) + len(d["lesson_problems"])
        return s + (f"; {problems} problems" if problems else "")
    if g.name == "runtime":
        s = f"{d.get('slides')} slides, {d.get('images')} images, math {d.get('math')}"
        issues = (len(d["errors"]) + len(d["present_overflow"]) + len(d["broken_images"]) + len(d.get("dead_widgets", []))
                  + sum(1 for v in d["hscroll"].values() if v))
        return s + (f"; {issues} issues" if issues else "")
    if g.name == "size":
        return f"{d['bytes'] / 1e6:.2f} MB of {d['limit'] / 1e6:.0f} MB"
    if g.name == "teaching":
        s = f"{len(d['taught'])} of {d['terms']} terms taught"
        early = len(d["used_before_taught"])
        return s + (f", {early} used before they are taught" if early else ", none used early")
    return ""


def _problems(g: Gate) -> list[str]:
    """The lines worth printing under a failing gate."""
    d, out = g.detail, []
    if g.name == "schema":
        out = [f"{p['where']}: {p['message']}" for p in d["problems"]]
    elif g.name == "grounding":
        out = [f"slide {s!r} cites no blocks" for s in d["uncited_slides"]]
        out += [f"{x.get('slide') or x.get('glossary')!r}: unknown source {x['source']!r}" for x in d["unresolved_sources"]]
    elif g.name == "numbers":
        out = [f"{x['slide']}.{x['field']}: {x['value']} is not in the chapter" for x in d["invented"]]
    elif g.name == "quotes":
        out = [f"{x['slide']}: not verbatim: {x['not_in_chapter'][0][:80]!r}" for x in d["failures"]]
    elif g.name == "coverage":
        out = [f"section {s} is neither taught nor in `skipped`" for s in d.get("missing", [])]
    elif g.name == "density":
        out = list(d["lesson_problems"])
        out += [f"{x['slide']}.{x['field']}: {x.get('words', x.get('items'))} > {x['limit']}" for x in d["over_budget"]]
    elif g.name == "teaching":
        for x in d["used_before_taught"]:
            when = f"slide {x['taught_on']!r} teaches it" if x["taught_on"] else "no slide teaches it"
            out.append(f"{x['slide']}.{x['field']}: uses {x['term']!r} before it is taught ({when}): ...{x['context']}...")
        out += [f"glossary term {t!r} is never taught (no slide lists it in `teaches`)" for t in d["never_taught"]]
        out += [f"{x['slide']}: teaches {x['term']!r} again (first taught on {x['first']!r})" for x in d["taught_twice"]]
        out += [f"{x['slide']}: teaches {x['term']!r}, which is not in the glossary" for x in d["unknown_terms"]]
        out += [f"{x['slide']}: [[{x['term']}]] is not in the glossary" for x in d["unknown_links"]]
    elif g.name == "runtime":
        out = list(d["errors"])
        out += [f"slide {x['slide']!r} overflows the stage by {x['px']}px ({x.get('viewport', 'desktop')})" for x in d["present_overflow"]]
        out += [f"broken image: {x}" for x in d["broken_images"]]
        out += [f"slide {x['slide']!r}: {x['type']} check {x['reason']}" for x in d.get("dead_widgets", [])]
        out += [f"horizontal scroll at {k} width" for k, v in d["hscroll"].items() if v]
    return out


def verify(number: int, no_browser: bool = False) -> bool:
    src = ChapterSource(number)
    lesson, lesson_path = build_mod.load_lesson(number)
    widgets_dir = lesson_path.parent / "widgets"
    results = gates.static_gates(lesson, src, widgets_dir)

    schema_ok = results[0].ok
    built = None
    if schema_ok:
        built = build_mod.build(number)
        runtime = gate_runtime(built.preview, no_browser)
        # What the custom widgets drew, read off the page, replaces the
        # transcript's guess from their props.
        shown = runtime.detail.pop("shown", None)
        if shown:
            built.transcript.write_text(transcript.render(built.data, shown))
        results.append(runtime)
        results.append(gates.gate_size(built.index))
    else:
        results.append(Gate("runtime", BLOCKED, {"reason": "schema failed; page not built"}))
        results.append(Gate("size", BLOCKED, {"reason": "schema failed; page not built"}))

    ok = all(g.ok for g in results)
    report = {
        "chapter": number,
        "lesson": str(lesson_path),
        "status": PASS if ok else (FAIL if any(g.status == FAIL for g in results) else BLOCKED),
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "gates": {g.name: g.as_dict() for g in results},
    }
    if built:
        report["build"] = {"index": str(built.index), "preview": str(built.preview), "transcript": str(built.transcript),
                           "publish": str(built.publish), "bytes": built.bytes, "figures": built.figures}
    config.REPORTS.mkdir(parents=True, exist_ok=True)
    out = config.REPORTS / f"learn-{config.chapter_id(number)}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    print(f"{config.chapter_id(number)} lesson  {report['status']}")
    for g in results:
        print(f"  {g.name:<10} {g.status:<8} {_summary(g)}")
        if not g.ok and g.status != BLOCKED:
            for line in _problems(g)[:25]:
                print(f"      - {line}")
    print(f"report: {out}")
    if built:
        print(f"page:   {built.index}\npreview: {built.preview}\ntranscript: {built.transcript}")
    return ok


# ---------------------------------------------------------------------------
# Screenshots, for looking at the page without opening a browser
# ---------------------------------------------------------------------------


def shot(number: int, slide: str | None, present: bool, dark: bool, phone: bool, out: Path | None) -> Path:
    chrome = find_chrome()
    if chrome is None:
        raise SystemExit("no Chrome/Chromium found (set BOOKERY_CHROME)")
    # Always rebuild: a preview left over from before the last edit to
    # lesson.json or a widget would show the reader a page that no longer exists.
    preview = build_mod.build(number).preview
    if present:
        # One slide is shot after all its builds, so its figure notes show.
        anchor = f"#present-{slide}~built" if slide else "#present"
        size = (1600, 900) if not phone else (860, 400)
    else:
        anchor = f"#{slide}" if slide else ""
        size = VIEWPORTS["phone"] if phone else (1280, 900)
        size = (size[0], 2400)
    name = "-".join(x for x in ["shot", "present" if present else "study", slide, "dark" if dark else "", "phone" if phone else ""] if x)
    out = out or build_dir(number) / f"{name}.png"
    extra = [f"--screenshot={out}", "--virtual-time-budget=8000"]
    # Pin the theme both ways: left alone, headless Chrome follows the OS's.
    extra.append("--force-dark-mode" if dark else "--blink-settings=preferredColorScheme=1")
    url, window = _framed(preview, anchor, size, frame=bool(slide) and not present)
    _chrome(chrome, url, window, extra)
    if window != size and out.exists():
        from PIL import Image

        with Image.open(out) as im:
            im.crop((0, 0, size[0], size[1])).save(out)
    print(out)
    return out


def record(number: int, url: str) -> None:
    manifest_path = config.ROOT / "lessons" / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    publish = build_dir(number) / "publish.json"
    info = json.loads(publish.read_text()) if publish.exists() else {}
    lesson, _ = build_mod.load_lesson(number)
    manifest[config.chapter_id(number)] = {
        "url": url,
        "title": info.get("title") or lesson.get("title"),
        "bytes": info.get("bytes"),
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(dict(sorted(manifest.items())), indent=2) + "\n")
    print(f"{config.chapter_id(number)} -> {url}  ({manifest_path})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("verify")
    p.add_argument("--chapter", type=int, action="append", required=True)
    p.add_argument("--no-browser", action="store_true")
    p = sub.add_parser("shot")
    p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--slide")
    p.add_argument("--present", action="store_true")
    p.add_argument("--dark", action="store_true")
    p.add_argument("--phone", action="store_true")
    p.add_argument("--out", type=Path)
    p = sub.add_parser("record")
    p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--url", required=True)
    args = ap.parse_args()

    if args.cmd == "verify":
        ok = all([verify(n, args.no_browser) for n in args.chapter])
        sys.exit(0 if ok else 1)
    elif args.cmd == "shot":
        shot(args.chapter, args.slide, args.present, args.dark, args.phone, args.out)
    elif args.cmd == "record":
        record(args.chapter, args.url)


if __name__ == "__main__":
    main()
