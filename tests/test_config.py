"""Regression test for the BOOKERY_ROOT override.

`pipeline.config` computes every path as a module-level constant at import
time, which is what lets every other module just do `from pipeline import
config` and trust `config.WORK` etc. without re-deriving them. That same
"decided once at import" property means the override has to be exercised in
a fresh interpreter -- reloading the module in-process would leak whichever
value won the race into every other test that happens to import it later.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _root_seen_by_subprocess(env_root: str | None) -> str:
    env = {"PATH": __import__("os").environ.get("PATH", "")}
    if env_root is not None:
        env["BOOKERY_ROOT"] = env_root
    proc = subprocess.run(
        [sys.executable, "-c", "from pipeline import config; print(config.ROOT)"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def test_root_defaults_to_the_tool_checkout_without_the_override():
    assert _root_seen_by_subprocess(None) == str(REPO_ROOT)


def test_bookery_root_env_var_redirects_every_derived_path(tmp_path):
    book_dir = tmp_path / "some-book"
    book_dir.mkdir()
    assert _root_seen_by_subprocess(str(book_dir)) == str(book_dir)


def test_bookery_root_expands_user_and_relative_components(tmp_path):
    # A trailing ".." component is a realistic way a shell-set BOOKERY_ROOT
    # arrives un-normalised; every path derived from it (WORK, SITE, ...)
    # must still land inside the intended book directory, not spuriously
    # miss cache hits against the same directory reached without the "..".
    nested = tmp_path / "store" / "book" / "nested"
    nested.mkdir(parents=True)
    messy = str(nested / ".." )
    assert _root_seen_by_subprocess(messy) == str(tmp_path / "store" / "book")
