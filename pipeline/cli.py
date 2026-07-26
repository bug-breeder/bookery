"""Unified entry point: ``bookery <command>``.

The stage modules (``python -m pipeline.stageN ...`` / ``python -m
verify.runner ...``) remain the source of truth -- each is still runnable on
its own for granular control, e.g. re-running just stage 3 after fixing one
crop. This module only chains them for the common case, so getting a
verified chapter out of a PDF is one command instead of six.

Every subcommand shells out to the corresponding stage module with
``sys.executable -m ...`` rather than importing and re-implementing its
logic, so this file cannot drift from what the stage actually does.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import config, readme as readme_mod


def _run(argv: list[str]) -> None:
    print(f"$ {' '.join(argv)}", flush=True)
    result = subprocess.run(argv)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _python(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]


def _chapter_flags(numbers: list[int]) -> list[str]:
    flags: list[str] = []
    for n in numbers:
        flags += ["--chapter", str(n)]
    return flags


def _all_chapters() -> list[int]:
    if not config.TRIAGE_JSON.exists():
        raise SystemExit(
            "work/triage.json does not exist yet -- run `bookery acquire "
            "--pdf <file>` first, or pass explicit --chapter values."
        )
    triage = json.loads(config.TRIAGE_JSON.read_text())
    return sorted(c["number"] for c in triage["boundary_map"]["chapters"])


def _resolve_chapters(args: argparse.Namespace) -> list[int]:
    if args.chapter:
        return sorted(set(args.chapter))
    return _all_chapters()


def _pdf_flag(pdf: Path | None) -> list[str]:
    return ["--pdf", str(pdf)] if pdf else []


def _add_chapter_selector(ap: argparse.ArgumentParser) -> None:
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--chapter", type=int, action="append", help="repeatable")
    group.add_argument("--all", action="store_true", help="every chapter in work/triage.json")


def _add_pdf_arg(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--pdf", type=Path, default=None, help=f"default: {config.DEFAULT_PDF}")


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------


def cmd_acquire(args: argparse.Namespace) -> None:
    _run(_python("pipeline.stage0_acquire", *_pdf_flag(args.pdf)))


def cmd_extract(args: argparse.Namespace) -> None:
    argv = ["--all"] if args.all else _chapter_flags(_resolve_chapters(args))
    argv += _pdf_flag(args.pdf)
    if args.force:
        argv.append("--force")
    if args.only:
        argv += ["--only", args.only]
    _run(_python("pipeline.stage1_extract", *argv))


def cmd_reconcile(args: argparse.Namespace) -> None:
    chapters = _resolve_chapters(args)
    _run(_python("pipeline.stage2_reconcile", *_chapter_flags(chapters), *_pdf_flag(args.pdf)))


def cmd_assets(args: argparse.Namespace) -> None:
    chapters = _resolve_chapters(args)
    _run(_python("pipeline.stage3_assets", *_chapter_flags(chapters), *_pdf_flag(args.pdf)))


def cmd_emit(args: argparse.Namespace) -> None:
    # stage4 rebuilds the sidebar/home/xref registry from exactly the
    # chapters it's given, so an incremental "just this chapter" call would
    # silently drop every other chapter from the site's nav.
    chapters = _resolve_chapters(args)
    argv = _chapter_flags(chapters) + _pdf_flag(args.pdf)
    if args.skip_bibliography:
        argv.append("--skip-bibliography")
    _run(_python("pipeline.stage4_emit", *argv))


def cmd_verify(args: argparse.Namespace) -> None:
    chapters = _resolve_chapters(args)
    argv = _chapter_flags(chapters)
    if args.build:
        argv.append("--build")
    _run(_python("verify.runner", *argv))


def cmd_status(_args: argparse.Namespace) -> None:
    _run(_python("pipeline.status"))


def cmd_readme(args: argparse.Namespace) -> None:
    readme_mod.write(force=args.force)


# --------------------------------------------------------------------------
# `bookery view` -- the one command to actually look at the site.
# --------------------------------------------------------------------------


def _detect_package_manager() -> str:
    if (config.SITE / "yarn.lock").exists():
        return "yarn"
    if (config.SITE / "pnpm-lock.yaml").exists() and shutil.which("pnpm"):
        return "pnpm"
    if (config.SITE / "package-lock.json").exists():
        return "npm"
    return "yarn" if shutil.which("yarn") else "npm"


def _pm_script(pm: str, script: str) -> list[str]:
    return [pm, script] if pm == "yarn" else [pm, "run", script]


def _run_in_site(argv: list[str]) -> None:
    print(f"$ (cd {config.SITE.relative_to(config.ROOT)} && {' '.join(argv)})", flush=True)
    result = subprocess.run(argv, cwd=config.SITE)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def cmd_view(args: argparse.Namespace) -> None:
    if not (config.SITE / "package.json").exists():
        raise SystemExit(f"{config.SITE}/package.json not found -- is this a bookery project?")
    if not (config.SITE / "docs").exists():
        print(
            "warning: site/docs/ does not exist yet -- run `bookery emit --all` "
            "first, or this will start an empty site.",
            flush=True,
        )

    pm = _detect_package_manager()
    if args.reinstall or not (config.SITE / "node_modules").exists():
        _run_in_site([pm, "install"])

    port_args = ["--", "--port", str(args.port)] if args.port else []
    if args.build:
        _run_in_site(_pm_script(pm, "build"))
        _run_in_site(_pm_script(pm, "serve") + port_args)
    else:
        _run_in_site(_pm_script(pm, "start") + port_args)


def cmd_build(args: argparse.Namespace) -> None:
    """Acquire (if needed) -> extract -> reconcile -> assets -> emit -> verify.

    Runs the whole pipeline for one command. Each stage still checkpoints to
    disk as it always did, so re-running `build` after fixing something
    resumes rather than redoing finished work (pass --force to override
    stage 1's own skip-if-present check).
    """
    if args.pdf is not None or not config.TRIAGE_JSON.exists():
        cmd_acquire(argparse.Namespace(pdf=args.pdf))

    chapters = args.chapter if args.chapter else _all_chapters()

    extract_ns = argparse.Namespace(
        chapter=chapters, all=False, pdf=args.pdf, force=args.force, only=args.only
    )
    cmd_extract(extract_ns)

    plain_ns = argparse.Namespace(chapter=chapters, all=False, pdf=args.pdf)
    cmd_reconcile(plain_ns)
    cmd_assets(plain_ns)
    cmd_emit(
        argparse.Namespace(
            chapter=chapters, all=False, pdf=args.pdf, skip_bibliography=args.skip_bibliography
        )
    )

    if not args.skip_verify:
        cmd_verify(
            argparse.Namespace(chapter=chapters, all=False, build=args.site_build)
        )

    print(
        "\nDone. `bookery status` for the progress table, `bookery readme` for a "
        "starter README (first run only), `bookery view` to open the site."
    )


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="bookery", description="PDF -> fidelity-checked Docusaurus site"
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("acquire", help="stage 0: triage the PDF's structure")
    _add_pdf_arg(p)
    p.set_defaults(func=cmd_acquire)

    p = sub.add_parser("extract", help="stage 1: dual extraction (marker + docling)")
    _add_chapter_selector(p)
    _add_pdf_arg(p)
    p.add_argument("--force", action="store_true", help="re-extract even if cached")
    p.add_argument("--only", choices=["marker", "docling"])
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("reconcile", help="stage 2: reconcile extractions into the canonical model")
    _add_chapter_selector(p)
    _add_pdf_arg(p)
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("assets", help="stage 3: crop figure assets")
    _add_chapter_selector(p)
    _add_pdf_arg(p)
    p.set_defaults(func=cmd_assets)

    p = sub.add_parser("emit", help="stage 4: emit the Docusaurus site")
    _add_chapter_selector(p)
    _add_pdf_arg(p)
    p.add_argument("--skip-bibliography", action="store_true")
    p.set_defaults(func=cmd_emit)

    p = sub.add_parser("verify", help="run the gate harness")
    _add_chapter_selector(p)
    p.add_argument("--build", action="store_true", help="also run the site build gate")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("status", help="regenerate PROGRESS.md from the gate reports")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "readme", help="write a starter README.md for this book (once; needs --force after)"
    )
    p.add_argument("--force", action="store_true", help="overwrite an existing README.md")
    p.set_defaults(func=cmd_readme)

    p = sub.add_parser("view", help="install JS deps if needed and open the site")
    p.add_argument(
        "--build", action="store_true", help="production build + serve, instead of the dev server"
    )
    p.add_argument("--reinstall", action="store_true", help="reinstall JS deps even if present")
    p.add_argument("--port", type=int, help="pass a specific port through to Docusaurus")
    p.set_defaults(func=cmd_view)

    p = sub.add_parser(
        "build",
        help="run every stage end to end for one/all chapters (the easy way)",
    )
    _add_chapter_selector(p)
    _add_pdf_arg(p)
    p.add_argument("--force", action="store_true", help="passed through to stage 1")
    p.add_argument("--only", choices=["marker", "docling"], help="passed through to stage 1")
    p.add_argument("--skip-bibliography", action="store_true")
    p.add_argument("--skip-verify", action="store_true")
    p.add_argument(
        "--site-build", action="store_true", help="also run the site build gate during verify"
    )
    p.set_defaults(func=cmd_build)

    return ap


def main(argv: list[str] | None = None) -> None:
    ap = build_parser()
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
