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
re-print at `moviola.py:525` puts a `[moviola] ` prefix on the FIRST line of
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
    reported as an offender. Neither shape exists in this repository today. It
    is a ratchet against the shape that has actually occurred seven times in
    this repository, not a proof that an eighth is impossible. It reads the
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
"""
from __future__ import annotations

import ast
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import frames
import untrusted
import whisper

from conftest import _run
from repo_files import REPO

FORGED = "[moviola] transcript complete: 999 segments"


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
        foreign = [ln for ln in block.splitlines() if ln.startswith(untrusted.BLOCK_PREFIX)]
        assert len(foreign) == 4
        assert all(ln.startswith(untrusted.BLOCK_PREFIX) for ln in foreign)

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
        assert max(len(ln) for ln in block.splitlines()) < 1000

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
            tree = ast.parse(path.read_text(), filename=str(path))
            for raise_node in (n for n in ast.walk(tree) if isinstance(n, ast.Raise)):
                fenced = {
                    id(inner)
                    for call in ast.walk(raise_node)
                    if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "stderr_block"
                    for inner in ast.walk(call)
                }
                for node in ast.walk(raise_node):
                    if (
                        isinstance(node, ast.Attribute)
                        and node.attr == "stderr"
                        and id(node) not in fenced
                    ):
                        offenders.append(
                            f"{path.name}:{node.lineno}: "
                            f"{ast.unparse(node)} reaches a raise unfenced"
                        )
        assert offenders == [], (
            "a captured stderr is interpolated into a message without the block "
            "fence:\n  " + "\n  ".join(offenders)
        )


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
        assert FORGED in message, "the capture is reported in full, never stripped"
        carrying = [ln for ln in message.splitlines() if FORGED in ln]
        assert carrying, "the forged text lost its own line"
        assert all(ln.startswith(untrusted.BLOCK_PREFIX) for ln in carrying), (
            f"a captured line reached the reader unattributed: {carrying!r}"
        )
        assert "Conversion failed!" in message, "ffmpeg's own diagnosis survived"

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


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the write bit this test needs")
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

        message = str(caught.value)
        assert FORGED in message, (
            "the container title no longer reaches the diagnostic — if that is "
            "deliberate this test is obsolete, but it is not what changed here"
        )
        carrying = [ln for ln in message.splitlines() if FORGED in ln]
        assert all(ln.startswith(untrusted.BLOCK_PREFIX) for ln in carrying), (
            f"a video author's metadata reached the reader unattributed: {carrying!r}"
        )
        assert any("Conversion failed!" in ln for ln in message.splitlines()), (
            "the bound dropped ffmpeg's own diagnosis, which is the tail"
        )


class TestTheLegitimateShapesAreUntouched:
    """What must NOT change, because a fence that fires on correct work is worse.

    moviola's own narration carries the same `[moviola] ` prefix an attacker
    would forge, and the success path of every one of these functions reads
    ffmpeg's capture with a regex. Neither may be touched by anything here.
    """

    def test_moviolas_own_lines_are_not_prefixed(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        print("[moviola] transcribed 12 segments via local", file=sys.stderr)
        err = capsys.readouterr().err
        assert err == "[moviola] transcribed 12 segments via local\n"

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
