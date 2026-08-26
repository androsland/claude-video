"""Cross-file invariants — things no single module can hold on its own.

These are the checks that a shared constant would normally cover. It cannot
here: the install command is printed from six runtime messages, but the same
version pin also lives in README.md, SKILL.md, CHANGELOG.md and the `.env`
template that setup.py scaffolds into the user's home directory — prose that
no Python constant can reach. Extracting a constant would fix six of thirteen
sites while adding a module-level import of `local_whisper` to `setup.py` and
`whisper.py`, where that import is deliberately lazy and wrapped in
`except Exception: return False` so a broken install degrades to "not
available" instead of crashing the preflight. So the invariant is enforced by
reading the files instead of by refactoring toward the constant.

Deliberately NOT covered: whether the pin is the RIGHT version. This checks
that thirteen copies agree, not that they agree on something correct.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# The trailing exclusions matter: these strings live inside Python source, so a
# match runs into the backslash of an escaped quote (\\"faster-whisper>=1.0\\")
# unless the class stops there too.
PIN = re.compile(r"faster-whisper\s*([<>=!~]=?[^\"'`\\\s,)]*)")
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".sh", ".txt", ".yml", ".yaml"}


def _tracked_text_files() -> list[Path]:
    out = []
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if SKIP_DIRS & set(path.relative_to(REPO).parts):
            continue
        out.append(path)
    return out


def test_the_faster_whisper_pin_is_identical_everywhere():
    found: dict[str, list[str]] = {}
    for path in _tracked_text_files():
        for pin in PIN.findall(path.read_text(encoding="utf-8", errors="replace")):
            found.setdefault(pin, []).append(str(path.relative_to(REPO)))
    assert found, "no faster-whisper pin found at all — did the spelling change?"
    assert len(found) == 1, (
        "the faster-whisper version pin has drifted:\n"
        + "\n".join(f"  {pin}: {sorted(set(files))}" for pin, files in sorted(found.items()))
    )


def test_the_pin_is_quoted_wherever_it_is_a_shell_command():
    """`pip install faster-whisper>=1.0` unquoted makes the shell truncate the
    file `faster-whisper` and install the unpinned package. Every site that
    prints an install command has to carry the quotes."""
    unquoted = []
    for path in _tracked_text_files():
        if path.name == Path(__file__).name:
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for match in re.finditer(r"pip3?\s+install\s+(\S+)", line):
                target = match.group(1)
                # A bare, PINNED spec is the hazard. Anything already wrapped —
                # "faster-whisper>=1.0", or \"faster-whisper>=1.0\" inside Python
                # source — does not start with the package name and is skipped.
                # Prose that says `pip install faster-whisper` with no version is
                # a legitimate sentence, not a shell hazard, and must not trip
                # this either.
                if target.startswith("faster-whisper") and PIN.search(target):
                    unquoted.append(f"{path.relative_to(REPO)}:{line_no}: {line.strip()[:100]}")
    assert not unquoted, "unquoted pip install of a pinned spec:\n" + "\n".join(unquoted)
