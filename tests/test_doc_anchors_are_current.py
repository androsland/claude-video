"""The `file.py:line` anchors this repo's prose cites must still point at the code.

Two documents carry line anchors into `skills/moviola/scripts/`: the stderr-fencing
entry in `TODOS.md`, and the module docstring of `tests/test_stderr_is_untrusted.py`.
Both name the seven sites that put a captured subprocess stderr into a `SystemExit`,
and both name the two that run ffmpeg at `-loglevel info`. Those numbers have now
drifted twice on one branch, in both directions and for the same reason each time: an
edit ABOVE the cited sites shifted them, and the anchor arithmetic was done against
the file as it stood before that edit rather than after it. The second drift was
introduced by the very commit that fixed the first, and it was +1 rather than +6, so
it survived a reviewer who confirmed the corrected numbers by quoting them back.

This file re-derives both sets from the code and compares. A human correcting an
anchor by hand is doing arithmetic; this does the same arithmetic every run.

WHAT IT PINS

  * The `frames.py` and `whisper.py` line numbers cited together on one line of
    either document are exactly the lines where a `.stderr` attribute is read inside
    a `raise`, as found by walking the AST of every module in the scripts directory.

  * The `frames.py` line numbers cited on a line that also contains `-loglevel info`
    are exactly the lines where that argument appears in `frames.py`.

NON-GOALS, stated because an unstated limit reads as a claim of coverage:

  * **It checks the SET, never the meaning.** If two raise sites swapped places in
    the file, both sets would still match and this file would stay green. It knows
    that the cited numbers are the raise-site numbers; it has no idea whether the
    sentence around them describes what is actually there. The three `TODOS.md`
    anchors named below are exactly that failure: each pointed at a real line of
    `frames.py` saying something entirely unrelated, and no set comparison can
    see it.

  * **It covers two documents and two classes of anchor. Nothing else.** Every other
    `X.py:NNN` in this repository is uncovered — `TODOS.md` alone carries dozens
    pointing at `download.py`, `moviola.py`, `transcribe.py` and `local_whisper.py`,
    and this file will never look at one of them. The commit that introduced this
    file also fixed four such anchors BY HAND, none of which any signature here
    matches: a stale `untrusted.py:350` in `tests/test_stderr_blocks_are_fenced.py`,
    and three in `TODOS.md` that had drifted by roughly +148 without anyone
    noticing — `frames.py:146` for a `json.loads` that is at 294, `:156` for a
    nested `finite_float` default at 305, and `:367` for the `-frames:v` in
    `extract_scene_candidates` at 465. Four hand-fixes against two covered
    classes is the ratio; it is not a coverage claim, and it is the argument for
    citing a symbol rather than a line where prose can afford to.

  * **It goes quiet rather than failing when it cannot find the sentence.** Both
    signatures are structural — "a line citing both files" and "a line citing
    `-loglevel info`" — chosen so the test keys on content rather than on wording,
    and a reword of either sentence keeps it working. But a REWRAP does not: move
    the `whisper.py` anchors onto their own line and the first signature matches
    nothing, and this file reports success having checked nothing at all. The
    `test_the_signatures_still_match_something` guards below exist for exactly that,
    and they are the only reason a silent pass is distinguishable from a real one.

  * **It must not fire on a document that cites no line numbers.** Prose naming the
    seven sites without anchoring them is a legitimate, deliberate choice — it is
    what `CHANGELOG.md` does, which is why `CHANGELOG.md` is not read here. Absence
    of anchors is never a failure; only a WRONG anchor is.

  * **It cannot see an anchor that is correct today and stale tomorrow** any more
    than a reviewer can. It shortens the window to one test run; it does not close
    it. Nothing here runs on a document nobody edited.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from repo_files import REPO

SCRIPTS = REPO / "skills" / "moviola" / "scripts"

# Documents that carry anchors into the scripts directory. CHANGELOG.md is
# deliberately absent: it describes the same seven sites and cites not one line
# number, which is a legitimate way to write it and not this file's business.
ANCHORED_DOCS = (
    REPO / "TODOS.md",
    REPO / "tests" / "test_stderr_is_untrusted.py",
)

# Anchors are written two ways in these documents: in full (`frames.py:291`) and
# elided after the first (`/:402`, ` and :474`). Both spellings have to be read, and
# an elided one inherits the file named most recently to its left on the same line.
ANCHOR = re.compile(r"`?(?:([a-z_]+\.py))?:(\d+)`?")


def cited_anchors(line: str) -> dict[str, set[int]]:
    """Every `file.py:line` on one line of prose, elisions attributed leftward."""
    found: dict[str, set[int]] = {}
    current: str | None = None
    for match in ANCHOR.finditer(line):
        name, number = match.group(1), int(match.group(2))
        if name is not None:
            current = name
        if current is None:
            continue
        found.setdefault(current, set()).add(number)
    return found


def raise_sites() -> dict[str, set[int]]:
    """Every line reading a `.stderr` attribute inside a `raise`, by module."""
    sites: dict[str, set[int]] = {}
    for path in sorted(SCRIPTS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Attribute) and inner.attr == "stderr":
                    sites.setdefault(path.name, set()).add(inner.lineno)
    return sites


def loglevel_info_lines() -> set[int]:
    """Every line of frames.py carrying the `-loglevel info` argument pair."""
    text = (SCRIPTS / "frames.py").read_text(encoding="utf-8").split("\n")
    return {
        number
        for number, line in enumerate(text, start=1)
        if '"-loglevel", "info"' in line
    }


def lines_citing_both(doc: Path) -> list[tuple[int, dict[str, set[int]]]]:
    """Lines naming both frames.py and whisper.py anchors — the raise-site sentence.

    Structural rather than textual: any sentence that anchors both files at once is
    about the seven sites, because they are the only thing this repository cites
    that way. A reword keeps working; a rewrap does not, which the NON-GOALS say.
    """
    out = []
    for number, line in enumerate(doc.read_text(encoding="utf-8").split("\n"), 1):
        found = cited_anchors(line)
        if "frames.py" in found and "whisper.py" in found:
            out.append((number, found))
    return out


def lines_citing_loglevel(doc: Path) -> list[tuple[int, set[int]]]:
    """Lines carrying `-loglevel info` alongside frames.py anchors."""
    out = []
    for number, line in enumerate(doc.read_text(encoding="utf-8").split("\n"), 1):
        if "-loglevel" not in line or "info" not in line:
            continue
        found = cited_anchors(line)
        if "frames.py" in found:
            out.append((number, found["frames.py"]))
    return out


class TestTheRaiseSiteAnchors:
    def test_every_cited_pair_matches_the_ast(self) -> None:
        sites = raise_sites()
        assert sites, "the AST sweep found no fenced raise sites at all"
        for doc in ANCHORED_DOCS:
            for number, found in lines_citing_both(doc):
                for name, cited in found.items():
                    actual = sites.get(name, set())
                    assert cited == actual, (
                        f"{doc.name}:{number} cites {name} at "
                        f"{sorted(cited)}; the raise sites are {sorted(actual)}"
                    )

    def test_the_signature_still_matches_something(self) -> None:
        # Without this the class above passes vacuously the moment somebody
        # rewraps either sentence, and a green run would mean "checked nothing".
        for doc in ANCHORED_DOCS:
            assert lines_citing_both(doc), (
                f"{doc.name} no longer cites frames.py and whisper.py anchors on "
                "one line — the raise-site check is now silently inspecting "
                "nothing. Rewrap it back, or retire this test deliberately."
            )


class TestTheLoglevelAnchors:
    def test_every_cited_line_carries_the_argument(self) -> None:
        actual = loglevel_info_lines()
        assert actual, "frames.py no longer runs anything at -loglevel info"
        for doc in ANCHORED_DOCS:
            for number, cited in lines_citing_loglevel(doc):
                assert cited == actual, (
                    f"{doc.name}:{number} cites frames.py at {sorted(cited)} for "
                    f"-loglevel info; the argument is at {sorted(actual)}"
                )

    def test_the_signature_still_matches_something(self) -> None:
        matched = [d for d in ANCHORED_DOCS if lines_citing_loglevel(d)]
        assert matched, (
            "no document cites -loglevel info with a frames.py anchor any more — "
            "the check is inspecting nothing."
        )


class TestTheReaderItself:
    """The elision reader is the only non-obvious machinery here, so it is pinned."""

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("`frames.py:291`/`:402`", {"frames.py": {291, 402}}),
            (
                "`frames.py:291` and `whisper.py:376`/`:422`",
                {"frames.py": {291}, "whisper.py": {376, 422}},
            ),
            ("no anchors here at all", {}),
            (":404 with no file named first", {}),
        ],
    )
    def test_elisions_attribute_leftward(
        self, line: str, expected: dict[str, set[int]]
    ) -> None:
        assert cited_anchors(line) == expected

    def test_a_bare_number_before_any_filename_is_dropped(self) -> None:
        # An elided anchor inherits the file to its LEFT. With nothing to its
        # left it belongs to no file, and guessing would invent a citation the
        # document never made.
        assert cited_anchors(":99 then `frames.py:291`") == {"frames.py": {291}}
