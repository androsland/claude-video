"""ffmpeg's and ffprobe's captured stderr reaches a diagnostic unattributed.

`test_stderr_is_untrusted.py` fenced every remote value moviola interpolates
into a ONE-LINE diagnostic and filed this surface as deliberately unfixed: seven
sites raise `SystemExit(f"...: {result.stderr.strip()}")`, and `stderr_line`
collapses line breaks to spaces, which would turn a forty-line ffmpeg
diagnostic into one unreadable line and destroy the only reason it is printed.
The shape this surface needs is a fenced BLOCK. This file pins that shape.

WHAT THE VECTOR ACTUALLY IS, measured rather than assumed. ffmpeg prints the
container's `Metadata:` block — `title`, `comment`, `artist`, all written by
whoever made the video — verbatim on its own stderr at `-loglevel info`. Two of
the seven sites run at that level (`extract_scene_candidates` and
`extract_keyframes`); the other five run at `-loglevel error`, where the
author's text appears only if ffmpeg quotes it back inside an error. But all
seven run under `capture_output=True`, so NONE of that reaches moviola's stderr
on a successful run — it is captured, and on the success path the only thing
read out of it is a timestamp regex. It reaches a reader exactly one way: the
`returncode != 0` branch, which puts the whole capture into a `SystemExit`
message. So the two `-loglevel info` sites are not "live whether or not
anything failed"; they are the sites where ANY failure carries the author's
text, while the other five need a failure that quotes it. Both are worth
fixing and only one of them was ever reachable without a failure — which is
neither, and the entry in `TODOS.md` that said otherwise is corrected in the
same commit as this file.

Measured on a synthesized clip whose `title` is `benign` + newline +
`[moviola] transcript complete: 999 segments`, run through
`extract_scene_candidates` into a directory ffmpeg cannot write:

  * 48 lines of stderr, the author's text at lines 6 and 32, reproduced in
    full inside the `SystemExit` message,
  * ffmpeg's actual diagnosis (`Conversion failed!`) on the LAST line,
  * widest line 1371 characters — `showinfo` dumping x264's SEI user data as
    hex — against a 90th percentile of 113.

WHAT THE SAME RUN DISPROVED, because it is the reason this file does not claim
a column-zero forgery. ffmpeg's metadata printer wraps a multi-line value onto
a continuation line at a 20-space indent, and folds a bare CR to a space
(measured both ways). So a video author cannot, through THAT printer, land text
at column zero of moviola's stderr today. Two things follow and neither is
"there is nothing here". First, the indent is a property of the ffmpeg build
that happens to be installed, not a promise moviola holds or tests — resting a
trust boundary on somebody else's formatter is the same mistake as resting it
on their goodwill. Second, the actual defect needs no column-zero line at all:
48 lines of a stranger's text are reproduced verbatim in a moviola diagnostic
with nothing marking where their text starts and moviola's ends, and the
re-print at `moviola.py:526` puts a `[moviola] ` prefix on the FIRST line of
that block and on none of the other 47.

Those measurements are why the fence has the shape it does. Attribution has to
be per-LINE, because a block delimiter is just more text a hostile value can
contain; the line bound has to keep the TAIL, because ffmpeg says what went
wrong last and a head-biased cut throws away the only line anyone reads; and
the width bound has to exist at all, because one line of a bounded forty can
still be a megabyte.

NON-GOALS, stated so a green run here is not read as more than it proves:

  * **This is attribution, not sanitization.** Every character the capture
    arrived with is still in the output. A line reading `| Conversion failed!`
    that ffmpeg never wrote is still a lie the reader can be told; what the
    reader can no longer be told is that moviola said it. The same limit
    `untrusted.py`'s own NON-GOALS carry applies here unchanged, including the
    three families — ANSI CSI, OSC, and the implicit marks U+200E/U+200F/U+061C
    — that repaint or reorder a terminal without opening any scope to close.

  * **The notices are informational, and only the prefix is structural.** A
    foreign line cannot produce a line without the prefix, which is what makes
    "unprefixed means moviola wrote it" hold. It cannot produce a *believable*
    truncation notice either, but nothing here proves that — a hostile capture
    can certainly contain the text `(8 earlier line(s) not shown)` inside a
    prefixed line, and the per-line width marker sits inside foreign territory
    by construction. Neither is load-bearing; both are there to stop a bound
    from being silent.

  * **It says nothing about yt-dlp.** `download.py` hands yt-dlp
    `stdout=sys.stderr, stderr=sys.stderr`, so those bytes never pass through
    this process and no interpolation fence can reach one of them. That is
    still the largest volume of remote text on moviola's stderr and it is still
    untouched.

  * **The source sweep below reads the AST, and only inside `raise`.** That
    scope is deliberate rather than lazy: `extract_scene_candidates` and
    `extract_keyframes` both read `result.stderr` on the SUCCESS path, where a
    regex pulls `showinfo`'s timestamps out of it, and fencing that would
    silently drop scene candidates. So the rule is "a capture that becomes a
    raised message must be fenced", not "a capture must be fenced". What it
    cannot see: a capture bound to a plain local first (`err = result.stderr`
    above the raise leaves no `.stderr` attribute inside the raise at all), one
    formatted in a helper the raise merely calls, and one that reaches the
    reader through `print(..., file=sys.stderr)` rather than a raise. In the
    other direction it is indiscriminate — it keys on the ATTRIBUTE NAME
    `stderr`, so a `raise` that happened to mention `sys.stderr` would be
    reported as an offender; no `raise` in this repository does that today.
    The print shape, however, is NOT hypothetical: `moviola.py:526` is exactly
    it — `print(f"[moviola] whisper fallback failed: {exc}", file=sys.stderr)`
    on a caught `SystemExit`, and `exc` there is routinely a fenced block. It
    is safe for a reason the sweep gets no credit for: attribution is applied
    where the block is CONSTRUCTED, so every foreign line still carries its
    prefix by the time this re-print sees it. A site that formatted a raw
    `result.stderr` into a print would be the same defect in a shape nothing
    here can see. So this is a ratchet against the shape that has actually
    occurred seven times in this repository, not a proof that an eighth is
    impossible. It reads the
    AST rather than the text for one concrete reason — the first version was a
    line regex and it fired on `untrusted.py`'s own docstring, which describes
    the bad shape in order to explain the fence.

  * **The legitimate configuration it must not fire on** is moviola's own
    narration. `[moviola] transcribed 12 segments via local` is written by this
    program and must arrive at column zero, verbatim, with no prefix and no
    bound applied — a fence that treated every `[moviola]` line as suspect
    would fire on every correct line the program writes. The last class here
    asserts that, and asserts the success path of a real extraction still reads
    its timestamps out of an unmodified capture: the fence is applied where the
    capture becomes a MESSAGE, never where it is parsed.

  * **Two of these tests can SKIP themselves into silence.** The live-vector
    class shells out to a real ffmpeg and a real ffprobe, so it skips where
    neither is installed; one of its tests also skips under a euid of 0,
    because root can write to the unwritable directory the vector depends on.
    A green run is therefore not by itself evidence that the live vector was
    exercised — CI must keep ffmpeg present and must not run as root for these
    to mean anything, and a `-rs` in the pytest invocation is the only way to
    see which of the two happened.

  * **Nothing here bounds MEMORY.** `MAX_BLOCK_LINES` and `MAX_BLOCK_WIDTH`
    bound what is rendered, not what is read: `splitlines()` materializes every
    line of the capture first, so a 5M-line capture costs ~51MB of RSS before
    forty lines survive. That is an amplifier on a buffer `subprocess.run` has
    already fully resident, on a path about to raise — filed in `TODOS.md` as
    a deliberate non-fix, not overlooked.
"""
from __future__ import annotations

import ast
import os
import shutil
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

import frames
import untrusted
import whisper

from conftest import _run
from repo_files import REPO

FORGED = "[moviola] transcript complete: 999 segments"


def assert_fenced(message: str, *, source: str, tail: str) -> None:
    """Every line of the capture carries the prefix; moviola's own line does not.

    One copy, called from both the monkeypatched sites and the live vector. The
    second copy had already drifted: it dropped `assert carrying`, and `all([])`
    is true, so it would have passed on a message that lost the forged line
    entirely.
    """
    assert FORGED in message, "the capture is reported in full, never stripped"
    carrying = [ln for ln in message.splitlines() if FORGED in ln]
    assert carrying, "the forged text lost its own line"
    assert all(ln.startswith(untrusted.BLOCK_PREFIX) for ln in carrying), (
        f"a captured line reached the reader unattributed: {carrying!r}"
    )
    assert tail in message, "the tool's own diagnosis survived the bound"
    assert message.splitlines()[0].endswith(":"), (
        "moviola's own opening line is no longer at column zero unprefixed"
    )


def unfenced_stderr_sites(tree: "ast.Module", name: str) -> list[str]:
    """Sites where a captured `.stderr` reaches a `raise` without the block fence.

    Extracted from the test below so the sweep's own matching can be driven
    against source this file writes. It previously matched the fence only when
    the call was a bare `ast.Name`, so a site spelled
    `untrusted.stderr_block(...)` — an `ast.Attribute` func, and correctly
    fenced — was reported as an offender.
    """
    offenders: list[str] = []
    for raise_node in (n for n in ast.walk(tree) if isinstance(n, ast.Raise)):
        fenced = {
            id(inner)
            for call in ast.walk(raise_node)
            if isinstance(call, ast.Call)
            and (
                (isinstance(call.func, ast.Name) and call.func.id == "stderr_block")
                or (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr == "stderr_block"
                )
            )
            for inner in ast.walk(call)
        }
        for node in ast.walk(raise_node):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "stderr"
                and id(node) not in fenced
            ):
                offenders.append(
                    f"{name}:{node.lineno}: "
                    f"{ast.unparse(node)} reaches a raise unfenced"
                )
    return offenders


class TestTheBlockFenceExists:
    """`untrusted.stderr_block` is the shape this surface needs.

    `stderr_line` is the wrong tool and its docstring says so: it exists to make
    a value ONE line, by collapsing every break to a space. Applied to a
    forty-line capture it produces one line of forty joined fragments, which is
    unreadable and discards the structure a person debugging a failed ffmpeg run
    is reading it for.
    """

    def test_the_module_exposes_a_block_fence(self) -> None:
        assert hasattr(untrusted, "stderr_block"), (
            "the block-shaped fence belongs in untrusted.py, the leaf module — "
            "not in frames.py or whisper.py, which import it"
        )

    def test_every_foreign_line_carries_the_prefix(self) -> None:
        capture = "Input #0, mov\n  Metadata:\n    title : benign\n" + FORGED
        block = untrusted.stderr_block(capture, source="ffmpeg")
        lines = block.splitlines()
        foreign = [ln for ln in lines if ln.startswith(untrusted.BLOCK_PREFIX)]
        assert len(foreign) == 4
        # Filtering ON the prefix and then asserting the prefix is vacuous. The
        # property is about the lines that DON'T carry it: the only unprefixed
        # lines may be the ones moviola wrote itself.
        unprefixed = [ln for ln in lines if not ln.startswith(untrusted.BLOCK_PREFIX)]
        assert unprefixed == [lines[0]], (
            f"a line that is not the header escaped the prefix: {unprefixed!r}"
        )

    def test_the_forged_line_cannot_reach_column_zero(self) -> None:
        block = untrusted.stderr_block("ffmpeg died\n" + FORGED, source="ffmpeg")
        assert FORGED in block, "the value is reported in full, never stripped"
        for line in block.splitlines():
            assert not line.startswith("[moviola]"), (
                f"a captured line reached column zero looking like moviola's "
                f"own narration: {line!r}"
            )

    def test_exotic_terminators_are_line_breaks_too(self) -> None:
        # splitlines() treats all of these as terminators, so a capture using
        # one of them ends a line just as \n does. Whatever splits the block
        # has to split on the same set, or the piece after the break inherits
        # no prefix and arrives at column zero.
        for terminator in untrusted.LINE_BREAKS:
            block = untrusted.stderr_block(f"first{terminator}{FORGED}", source="ffmpeg")
            for line in block.splitlines():
                assert not line.startswith("[moviola]"), (
                    f"U+{ord(terminator):04X} carried a line past the fence"
                )

    def test_a_bidi_scope_does_not_leak_into_the_next_line(self) -> None:
        # An override opened on one line reorders the display of everything
        # after it — including the prefix on the line below, which is the mark
        # the reader is relying on. Balancing has to happen per line.
        block = untrusted.stderr_block(f"‮opened here\nsecond line\n{FORGED}", source="ffmpeg")
        for line in block.splitlines():
            assert untrusted.balance_bidi(line) == line, (
                f"line left a bidi scope open past its own end: {line!r}"
            )

    def test_the_line_count_is_bounded_and_the_bound_is_announced(self) -> None:
        block = untrusted.stderr_block("\n".join(f"line {i}" for i in range(500)), source="ffmpeg")
        foreign = [ln for ln in block.splitlines() if ln.startswith(untrusted.BLOCK_PREFIX)]
        assert len(foreign) <= untrusted.MAX_BLOCK_LINES
        assert "not shown" in block, "a silent cut reads as a complete capture"

    def test_the_bound_keeps_the_tail_because_ffmpeg_diagnoses_last(self) -> None:
        # Measured: `Conversion failed!` is the LAST line of a real failure and
        # the metadata block is near the front. A head-biased bound keeps the
        # part the video author wrote and drops the part ffmpeg wrote.
        block = untrusted.stderr_block(
            "\n".join(f"line {i}" for i in range(500)) + "\nConversion failed!",
            source="ffmpeg",
        )
        assert "Conversion failed!" in block
        assert "line 499" in block
        assert "line 0\n" not in block

    def test_one_line_of_a_bounded_block_is_still_bounded(self) -> None:
        block = untrusted.stderr_block("x" * 100_000, source="ffmpeg")
        widest = max(len(ln) for ln in block.splitlines())
        # Against the CONSTANT, not a literal. `< 1000` passed with 780
        # characters of slack and stayed green if MAX_BLOCK_WIDTH quadrupled,
        # which is precisely the "bound nobody can check" untrusted.py:99 warns
        # about. The rendered allowance is the content slice, the prefix and the
        # direction anchor, the width marker, and at most one terminator per
        # opened scope (a 200-char slice can open at most 200).
        allowance = (
            untrusted.MAX_BLOCK_WIDTH * 2 + len(untrusted.BLOCK_PREFIX) + 32
        )
        assert widest <= allowance, f"{widest} exceeds {allowance}"
        assert widest > untrusted.MAX_BLOCK_WIDTH, (
            "the bound is not being exercised at all — this test would pass on "
            "a block that dropped the line entirely"
        )

    def test_the_source_label_cannot_reach_column_zero(self) -> None:
        # `source` is interpolated into the header and into the empty-capture
        # sentence, and both land at column zero. Every OTHER value in this
        # function is prefixed. The docstring called it "the one trusted
        # argument", which is a convention no code enforced: an eighth site
        # passing `cmd[0]` or a filename would forge a moviola line, and the
        # AST sweep keys on the attribute name `stderr` so it would not see it.
        # The property is `stderr_line`'s, so state it as `stderr_line`'s: the
        # label may still be READ inside moviola's own line — nothing here is a
        # sanitizer — it just may not END that line and start one of its own.
        forged = "ffmpeg\n" + FORGED
        for capture in ("boring", ""):
            benign = untrusted.stderr_block(capture, source="ffmpeg")
            hostile = untrusted.stderr_block(capture, source=forged)
            own = [
                ln
                for ln in hostile.splitlines()
                if not ln.startswith(untrusted.BLOCK_PREFIX)
            ]
            assert len(own) == len(
                [
                    ln
                    for ln in benign.splitlines()
                    if not ln.startswith(untrusted.BLOCK_PREFIX)
                ]
            ), f"the source label bought itself an extra line at column zero: {own!r}"
            assert not any(ln.startswith(FORGED) for ln in own), (
                f"the source label reached column zero: {own!r}"
            )

    def test_the_prefix_anchors_the_line_direction(self) -> None:
        # `| ` is ON + WS: it holds no strong LTR character. UAX#9 P2/P3 take a
        # line's base direction from its FIRST strong character, so a capture
        # line opening in Hebrew or Arabic resolves the whole line to RTL and
        # N1/N2 carry the leading neutrals with it — the prefix renders at the
        # visual RIGHT edge and the author's text occupies visual column zero.
        # balance_bidi cannot help: no scope was ever opened. So the prefix has
        # to carry a strong LTR character of its own.
        block = untrusted.stderr_block("\u05d0\u05d1\u05d2 shalom", source="ffmpeg")
        line = [ln for ln in block.splitlines() if "shalom" in ln][0]
        strong = [
            ch for ch in line if unicodedata.bidirectional(ch) in ("L", "R", "AL")
        ]
        assert strong and strong[0] == "\u200e", (
            "the first strong character of a rendered line is the capture's, so "
            f"the line resolves RTL and the prefix moves: {strong[:3]!r}"
        )

    def test_the_header_counts_the_lines_that_actually_follow(self) -> None:
        # On a truncating capture the header said "500 line(s) ... follow" above
        # 40 lines. The notice below it kept the bound from being silent, but
        # the header is one of the two lines this module holds out as moviola's
        # own trustworthy text, and its arithmetic was simply wrong.
        block = untrusted.stderr_block(
            "\n".join(f"line {i}" for i in range(500)), source="ffmpeg"
        )
        lines = block.splitlines()
        following = [ln for ln in lines if ln.startswith(untrusted.BLOCK_PREFIX)]
        assert str(len(following)) in lines[0], (
            f"the header does not name the {len(following)} lines that follow: "
            f"{lines[0]!r}"
        )

    @pytest.mark.parametrize("count", [39, 40, 41])
    def test_the_line_bound_is_exact_at_its_boundary(self, count: int) -> None:
        assert untrusted.MAX_BLOCK_LINES == 40, "this test names the boundary"
        block = untrusted.stderr_block(
            "\n".join(f"line {i}" for i in range(count)), source="ffmpeg"
        )
        kept = [ln for ln in block.splitlines() if ln.startswith(untrusted.BLOCK_PREFIX)]
        assert len(kept) == min(count, untrusted.MAX_BLOCK_LINES)
        assert ("not shown" in block) is (count > untrusted.MAX_BLOCK_LINES)
        assert f"line {count - 1}" in block, "the tail is what the bound keeps"

    @pytest.mark.parametrize("width", [199, 200, 201])
    def test_the_width_bound_is_exact_at_its_boundary(self, width: int) -> None:
        assert untrusted.MAX_BLOCK_WIDTH == 200, "this test names the boundary"
        block = untrusted.stderr_block("x" * width, source="ffmpeg")
        assert ("char(s))" in block) is (width > untrusted.MAX_BLOCK_WIDTH)

    def test_the_bounds_are_honoured_when_the_caller_supplies_them(self) -> None:
        # `max_lines` and `max_width` are public keyword parameters that no
        # caller and no test ever supplied, so the truncation arithmetic under a
        # non-default bound shipped unexercised.
        block = untrusted.stderr_block(
            "\n".join("y" * 20 for _ in range(10)),
            source="ffmpeg",
            max_lines=3,
            max_width=5,
        )
        kept = [ln for ln in block.splitlines() if ln.startswith(untrusted.BLOCK_PREFIX)]
        assert len(kept) == 3
        assert "(7 earlier line(s) not shown)" in block
        assert "+15 char(s)" in block

    def test_a_truncated_line_still_closes_the_scope_it_opened(self) -> None:
        # The slice-then-balance order at untrusted.py:350 is called
        # load-bearing, and it was asserted only on the branch that never
        # slices: the bidi test fed short lines, which take the `else` arm.
        block = untrusted.stderr_block(
            "\u202e" + "z" * 400, source="ffmpeg", max_width=50
        )
        line = [ln for ln in block.splitlines() if "z" in ln][0]
        assert line.count("\u202e") == line.count("\u202c"), (
            f"an override survived the slice unclosed: {line!r}"
        )

    def test_an_indented_first_line_keeps_its_indentation(self) -> None:
        # All seven sites passed `result.stderr.strip()`, which trims the
        # leading whitespace of the FIRST line only — so an ffmpeg banner
        # opening with an indented line rendered de-indented while lines 2..N
        # kept theirs. Trimming foreign text is a decision the leaf owns.
        block = untrusted.stderr_block("    indented\n    also", source="ffmpeg")
        kept = [ln for ln in block.splitlines() if ln.startswith(untrusted.BLOCK_PREFIX)]
        assert [ln[len(untrusted.BLOCK_PREFIX):].lstrip("\u200e") for ln in kept] == [
            "    indented",
            "    also",
        ]

    def test_an_empty_capture_says_so_rather_than_nothing(self) -> None:
        block = untrusted.stderr_block("   \n  \n", source="ffmpeg")
        assert block.strip(), "an empty capture must not render as an empty message"
        assert not any(
            ln.startswith(untrusted.BLOCK_PREFIX) for ln in block.splitlines()
        ), "there is no foreign line to attribute"


class TestNoCapturedStderrIsInterpolatedRaw:
    """A source-level ratchet: nothing stops an eighth site being written.

    Seven raise sites had this shape and every one of them was added by
    someone reading the six above it. A test that pins the seven by name goes
    green the moment an eighth is added; this one reads the shipped source and
    fires on the shape.
    """

    def test_no_raise_site_interpolates_result_stderr_unfenced(self) -> None:
        scripts = REPO / "skills" / "moviola" / "scripts"
        offenders: list[str] = []
        for path in sorted(scripts.glob("*.py")):
            offenders += unfenced_stderr_sites(
                ast.parse(path.read_text(), filename=str(path)), path.name
            )
        assert offenders == [], (
            "a captured stderr is interpolated into a message without the block "
            "fence:\n  " + "\n  ".join(offenders)
        )


class TestTheSweepItself:
    """The ratchet is source that judges source; a false positive disables it.

    Both halves matter. A sweep that misses the bad shape stops being a ratchet;
    one that fires on a good shape gets deleted by the next person it blocks.
    """

    def test_a_qualified_call_counts_as_fenced(self) -> None:
        # `untrusted.stderr_block(...)` is an ast.Attribute func. The sweep
        # matched only ast.Name, so this correctly-fenced site was reported as
        # an offender — a legitimate configuration the ratchet fired on.
        src = (
            "def f(result):\n"
            "    raise SystemExit('x:' + untrusted.stderr_block("
            "result.stderr, source='ffmpeg'))\n"
        )
        assert unfenced_stderr_sites(ast.parse(src), "q.py") == []

    def test_a_bare_call_still_counts_as_fenced(self) -> None:
        src = (
            "def f(result):\n"
            "    raise SystemExit('x:' + stderr_block("
            "result.stderr, source='ffmpeg'))\n"
        )
        assert unfenced_stderr_sites(ast.parse(src), "b.py") == []

    def test_the_bad_shape_is_still_caught(self) -> None:
        src = "def f(result):\n    raise SystemExit(f'x: {result.stderr}')\n"
        assert len(unfenced_stderr_sites(ast.parse(src), "bad.py")) == 1


class TestTheFenceReachesEverySite:
    """Seven sites, driven through the real functions with a canned failure.

    The sweep above reads text; this reads behaviour. A fence that is spelled
    at the raise site but never reached — a guard that returns first, a helper
    that reformats — passes the sweep and fails here.
    """

    # The forged line sits at column zero here, unlike the indented shape the
    # installed ffmpeg happens to produce. That is deliberate: the fence is what
    # is under test, not ffmpeg's formatter, and a capture that only ever
    # arrives pre-indented would let a fence that does nothing pass.
    CAPTURE = (
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mp4':\n"
        "  Metadata:\n"
        "    title           : benign\n"
        f"{FORGED}\n"
        "Conversion failed!"
    )

    @pytest.fixture()
    def failing_run(self, monkeypatch: pytest.MonkeyPatch):
        def fake_run(cmd, *args, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=self.CAPTURE)

        monkeypatch.setattr(frames.subprocess, "run", fake_run)
        monkeypatch.setattr(whisper.subprocess, "run", fake_run)
        monkeypatch.setattr(frames.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
        return fake_run

    def _assert_fenced(self, message: str) -> None:
        assert_fenced(message, source="ffprobe/ffmpeg", tail="Conversion failed!")

    def test_get_metadata_asks_ffprobe_to_speak(self, tmp_path: Path) -> None:
        # A real ffprobe, no monkeypatch. `-v quiet` silences ffprobe's stderr
        # as well as its info, so this site's capture was ALWAYS empty and the
        # block it now renders would permanently read "(ffprobe exited non-zero
        # and wrote nothing to stderr)". The fence was applied to a site that
        # had nothing to fence. whisper.py:408 already carries the fix and the
        # comment explaining it; this is its sibling.
        if shutil.which("ffprobe") is None:
            pytest.skip("ffprobe is not installed")
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"not really a video")
        with pytest.raises(SystemExit) as caught:
            frames.get_metadata(str(clip))
        message = str(caught.value)
        assert "wrote nothing to stderr" not in message, (
            "ffprobe's verbosity is suppressing the diagnostic this site fences"
        )
        assert any(
            ln.startswith(untrusted.BLOCK_PREFIX) for ln in message.splitlines()
        ), f"no attributed capture line reached the reader: {message!r}"

    def test_an_indented_capture_line_keeps_its_indentation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The call sites passed `result.stderr.strip()`, which trims the leading
        # whitespace of the FIRST line only — so an ffmpeg banner opening with
        # an indented line rendered de-indented while lines 2..N kept theirs.
        # Trimming somebody else's text is a decision the leaf module owns.
        def fake_run(cmd, *args, **kwargs):
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="    Metadata:\n    title : x\n"
            )

        monkeypatch.setattr(frames.subprocess, "run", fake_run)
        monkeypatch.setattr(frames.shutil, "which", lambda _name: "/usr/bin/ffprobe")
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"nope")
        with pytest.raises(SystemExit) as caught:
            frames.get_metadata(str(clip))
        bodies = [
            ln[len(untrusted.BLOCK_PREFIX):].lstrip("\u200e")
            for ln in str(caught.value).splitlines()
            if ln.startswith(untrusted.BLOCK_PREFIX)
        ]
        assert bodies == ["    Metadata:", "    title : x"]

    def test_get_metadata(self, failing_run, tmp_path: Path) -> None:
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"not really a video")
        with pytest.raises(SystemExit) as caught:
            frames.get_metadata(str(clip))
        self._assert_fenced(str(caught.value))

    def test_extract(self, failing_run, tmp_path: Path) -> None:
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"not really a video")
        with pytest.raises(SystemExit) as caught:
            frames.extract(str(clip), tmp_path / "out", fps=1.0)
        self._assert_fenced(str(caught.value))

    def test_extract_scene_candidates(self, failing_run, tmp_path: Path) -> None:
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"not really a video")
        with pytest.raises(SystemExit) as caught:
            frames.extract_scene_candidates(str(clip), tmp_path / "out")
        self._assert_fenced(str(caught.value))

    def test_extract_keyframes(self, failing_run, tmp_path: Path) -> None:
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"not really a video")
        with pytest.raises(SystemExit) as caught:
            frames.extract_keyframes(str(clip), tmp_path / "out")
        self._assert_fenced(str(caught.value))

    def test_extract_audio(self, failing_run, tmp_path: Path) -> None:
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"not really a video")
        with pytest.raises(SystemExit) as caught:
            whisper.extract_audio(str(clip), tmp_path / "a.mp3")
        self._assert_fenced(str(caught.value))

    def test_audio_duration(self, failing_run, tmp_path: Path) -> None:
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"not really audio")
        with pytest.raises(SystemExit) as caught:
            whisper.audio_duration(audio)
        self._assert_fenced(str(caught.value))

    def test_split_audio(self, failing_run, tmp_path: Path) -> None:
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x" * 4096)
        with pytest.raises(SystemExit) as caught:
            whisper.split_audio(audio, tmp_path, [(0.0, 30.0)])
        self._assert_fenced(str(caught.value))


@pytest.mark.skipif(
    getattr(os, "geteuid", lambda: -1)() == 0,
    reason="root ignores the write bit this test needs",
)
class TestTheLiveVectorEndToEnd:
    """No monkeypatch: a real clip, a real ffmpeg, a real failure.

    Everything above pins the fence against a capture this file wrote. This one
    proves the capture is real — that a title in an mp4 container reaches a
    moviola diagnostic at all, which is the claim the whole surface rests on.

    It asserts attribution, not column position. The installed ffmpeg indents a
    wrapped metadata value, so asserting "no author text at column zero" would
    pass on unfenced code and pin nothing; what it would really be testing is
    ffmpeg's formatter. Every line of the capture carrying the prefix is the
    property moviola actually owns.
    """

    @pytest.fixture()
    def forging_clip(self, tmp_path: Path) -> Path:
        clip = tmp_path / "forged.mp4"
        _run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-t", "1", "-i", "testsrc=size=64x64:rate=5",
            "-metadata", f"title=benign\n{FORGED}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
        ])
        return clip

    def test_a_container_title_reaches_the_diagnostic_attributed(
        self, forging_clip: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "out"
        out.mkdir()
        os.chmod(out, stat.S_IRUSR | stat.S_IXUSR)
        if os.access(out, os.W_OK):
            pytest.skip("this filesystem does not enforce the write bit")
        try:
            with pytest.raises(SystemExit) as caught:
                frames.extract_scene_candidates(str(forging_clip), out)
        finally:
            os.chmod(out, stat.S_IRWXU)

        assert_fenced(
            str(caught.value), source="ffmpeg", tail="Conversion failed!"
        )


class TestTheLegitimateShapesAreUntouched:
    """What must NOT change, because a fence that fires on correct work is worse.

    moviola's own narration carries the same `[moviola] ` prefix an attacker
    would forge, and the success path of every one of these functions reads
    ffmpeg's capture with a regex. Neither may be touched by anything here.
    """

    def test_moviolas_own_lines_are_not_prefixed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # This test used to `print(...)` and assert the printed literal came
        # back. That exercised `print` and capsys and no moviola code at all: it
        # passed unchanged with `stderr_block`, `BLOCK_PREFIX` and all seven
        # fenced sites deleted, and it would also have passed if the fence
        # wrongly prefixed every line moviola writes. The must-not-fire-on
        # configuration is the one thing this class exists to hold, so it has to
        # be asserted against a real message from a real fenced site.
        narration = "[moviola] transcribed 12 segments via local"

        def fake_run(cmd, *args, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=narration)

        monkeypatch.setattr(frames.subprocess, "run", fake_run)
        monkeypatch.setattr(frames.shutil, "which", lambda _name: "/usr/bin/ffprobe")
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"nope")
        with pytest.raises(SystemExit) as caught:
            frames.get_metadata(str(clip))
        lines = str(caught.value).splitlines()

        assert lines[0] == "ffprobe failed:", (
            "moviola's own opening line was rewritten by the fence"
        )
        assert not lines[0].startswith(untrusted.BLOCK_PREFIX)
        # ...and the narration-shaped line that arrived from OUTSIDE is
        # prefixed, which is the whole distinction: the fence keys on where the
        # text came from, never on what it looks like.
        assert any(
            ln.startswith(untrusted.BLOCK_PREFIX) and narration in ln for ln in lines
        ), f"a capture imitating moviola reached the reader unattributed: {lines!r}"

    def test_the_success_path_still_reads_timestamps_from_the_capture(
        self, cut_clip: Path, tmp_path: Path
    ) -> None:
        # The fence goes where a capture becomes a MESSAGE. `showinfo`'s
        # timestamps are parsed out of the same string on the success path, and
        # a fence applied there — prefixing every line, bounding the count —
        # would silently drop scene candidates.
        out, _dropped = frames.extract_scene_candidates(str(cut_clip), tmp_path / "f")
        assert len(out) > 1
        assert all(fr["timestamp_seconds"] >= 0 for fr in out)

    def test_a_short_single_line_capture_is_not_bounded_away(self) -> None:
        block = untrusted.stderr_block("No such file or directory", source="ffprobe")
        assert "No such file or directory" in block
        assert "not shown" not in block
