"""The set of files the repository SHIPS, for every test that audits repo-wide claims.

One definition, two callers. It lived twice — byte-identical copies in
`test_consistency.py` and `test_the_docs_are_checked.py` — and when the working-tree
walk was replaced with `git ls-files`, only one copy moved. Half the audits kept
reading local scratch while the commit message said the problem was fixed. That is
the failure mode a duplicated helper has: a fix that silently reaches one caller and
a claim that covers both.

NON-GOALS, so a green run over this set is not read as more than it is:

  * It does not decide WHAT to look for. Every caller brings its own pattern; this
    answers only "which files are the repository's own".
  * It cannot see a file that is about to be added. A real defect written into an
    untracked file is invisible here BY DESIGN — an untracked file makes no claim
    on anyone but its author, and that cuts both ways.
  * Under a sparse checkout it audits a subset and says so nowhere. `git ls-files`
    reports the full index; the `is_file()` filter then drops whatever the cone did
    not materialise, so the sweeps go green over the files that happen to be on
    disk. Nothing here detects that.
  * When git cannot answer it does not guess — see `tracked_text_files`.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".sh", ".txt", ".yml", ".yaml"}


def git_listed_paths() -> list[str] | None:
    """Every path in git's index, or None when git cannot answer.

    None is a real answer and callers must handle it: a machine with no git at all,
    or a checkout git refuses to read — `safe.directory` exits 128 when the tree is
    owned by another UID, which is the ordinary case for `/mnt/c` under WSL and for
    a container mounting the repo.

    The failure is reported on stderr rather than swallowed. `capture_output=True`
    takes git's own diagnostic with it, and a silent downgrade to a different file
    set is exactly the kind of drift the tests that call this exist to catch.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "-z"],
            capture_output=True,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = (getattr(exc, "stderr", b"") or b"").decode("utf-8", "replace").strip()
        print(
            f"[tests] git could not list {REPO}: {exc}{' — ' + detail if detail else ''}",
            file=sys.stderr,
        )
        return None
    # `-z` suppresses core.quotePath, so raw filesystem bytes arrive here. os.fsdecode
    # round-trips them; a plain .decode("utf-8") raises UnicodeDecodeError on the first
    # latin-1 filename — a ValueError, which no `except OSError` catches, so one badly
    # named tracked file would take the whole module down at collection.
    return [os.fsdecode(entry) for entry in completed.stdout.split(b"\0") if entry]


def tracked_text_files() -> list[Path]:
    """The repo's own text files, as git sees them.

    Asks git rather than walking the working tree, because the name is the whole
    point: every caller audits these files as CLAIMS THE REPOSITORY MAKES, and an
    untracked file makes no claim to anyone but the person who wrote it. The walk
    scanned local scratch — `SESSION.md` from the write-session plugin, `LOOP.md`,
    an editor backup, a downloaded sample — and reported findings against files
    that ship with nobody. It failed exactly that way here: a merge-commit line in
    `SESSION.md` quoting a branch name of the form `<owner>/chore/<slug>` parsed as
    a reference to a repository called `chore`, and the self-reference audit went
    red in a working tree while a clean checkout of the same commit stayed green.

    Note the shape of that false positive, because tracking does not remove it: the
    audit reads `<owner>/<word>` as a repo slug, and a branch name written in prose
    has the same shape. A tracked file quoting one — a CHANGELOG line, a runbook —
    would still trip it. Consulting git fixes the file SET, not the pattern.

    **Skips rather than falling back to the walk.** The walk was tried as a fallback
    and is worse than no answer: with git shimmed to exit 128, or removed from PATH,
    or `.git` deleted, the suite goes RED — 1 failed, 17 passed — naming
    `SESSION.md`, a file git was never asked about. A suite that refuses to run is
    worse than one that scans too much only if the over-scan is quiet, and this one
    is not. So when the file set is unknowable the audits say so and skip, which is
    visible in pytest's output and honest about what was checked.
    """
    listed = git_listed_paths()
    if listed is None:
        pytest.skip(
            "git cannot list this checkout, so which files the repository ships is "
            "unknown here. Walking the working tree instead was tried and rejected: "
            "it reads local scratch as repo content and takes the repo-wide audits "
            "RED against a file that is not in the repository."
        )

    files = []
    for rel in listed:
        path = REPO / rel
        if path.suffix not in TEXT_SUFFIXES or not path.is_file():
            continue
        if SKIP_DIRS & set(Path(rel).parts):
            continue
        files.append(path)

    # Without this, an empty listing is a green run over nothing. git answers with
    # zero paths whenever REPO resolves inside a different repository that has no
    # commits — and all three repo-wide audits then assert `[] == []` and pass.
    assert files, (
        f"git listed no text files under {REPO}. Every repo-wide audit would pass "
        "over an empty set, which is a green run that checked nothing."
    )
    return files
