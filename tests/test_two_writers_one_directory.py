"""What happens when the work directory holds files this run did not write.

Both cases here are the same mistake wearing different clothes: a directory is
treated as though this run is the only thing writing into it, and when that turns
out to be false the result is not a crash but a *plausible report about the wrong
files*. Every caller empties `out_dir` of its own outputs before extracting, so
in practice the assumption holds — which is exactly why nothing enforces it and
nothing notices when it stops holding.

  * `frames_in_order` sorted on the LAST run of digits anywhere in the name, so
    two naming schemes in one directory silently interleave. `frame_a_0001.jpg`
    and `frame_0001.jpg` both parse to 1; the tiebreak is the filename, which is
    the lexicographic bug again in a smaller room. Since every caller pairs
    frames with timestamps BY POSITION, one foreign name shifts every frame
    after it onto somebody else's timestamp.

NON-GOALS, so a green run is not read as more than it is:

  * These pin the two entries the quiet-failures review left open in TODOS.md.
    They are not a survey of shared-directory hazards, and the codebase has
    more directories than this one.
  * The scheme fix places frames written by ONE scheme. It does not merge two
    schemes into a single correct order — there is no information anywhere that
    says how `frame_0001.jpg` and `frame_a_0001.jpg` interleave in time, and
    inventing one is the failure this branch exists to stop. Foreign names are
    excluded and named, not ordered.
  * It cannot see a collision INSIDE one scheme: `frame_1.jpg` and
    `frame_0001.jpg` both read as frame 1, and the sort falls back to the
    filename for a stable order rather than reporting them. Nothing writes both,
    and a run whose extractor wrote both has a worse problem than the sort.
  * The legitimate configuration this must NOT fire on is the ordinary one: a
    directory holding only `frame_%04d.jpg` files, at any digit width, including
    the five-digit names past 9999 that `test_quiet_failures.py` pins. A
    disclosure on a normal run would train people to ignore the line.
  * `cue_*.jpg` living beside `frame_*.jpg` is a legitimate two-scheme
    directory and must stay silent: the two globs do not overlap, and that is
    the property being relied on rather than assumed.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

import frames

FRAMES_PY = (
    Path(__file__).resolve().parent.parent
    / "skills" / "moviola" / "scripts" / "frames.py"
)
# Any string literal that spells a frame filename shape: a prefix followed by a
# printf width, an f-string width, or a glob star, ending in .jpg.
#
# The f-string arm is not hypothetical padding. `f"cue_{len(out):04d}.jpg"` is
# what the cue writer literally said before this branch, and the first version of
# this pattern covered only `%0Nd` and `*` — so a mutation restoring that exact
# line SURVIVED the whole suite. An invariant that cannot see the shape it was
# written to outlaw is decoration.
FILENAME_LITERAL = re.compile(
    r"""f?["'][a-z_]+(?:%0\d+d|\*|\{[^"']*:0\d+d\})\.jpg["']"""
)


class TestTheFrameSchemeIsOneConstant:
    """The writer and the sorter must agree, and today only a comment says so."""

    def _lay_out(self, out_dir: Path, numbers: list[int]) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        for n in numbers:
            (out_dir / f"frame_{n:04d}.jpg").write_bytes(b"jpeg")

    def test_a_foreign_name_is_not_placed_in_the_order(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # The whole bug in one directory. `frame_a_0001.jpg` matches the glob and
        # its last digit run is 1, so it lands between frame 1 and frame 2 — and
        # since pairing is positional, frame 2 onwards then wears the timestamp
        # of the frame before it.
        self._lay_out(tmp_path, [1, 2, 3])
        (tmp_path / "frame_a_0001.jpg").write_bytes(b"jpeg")

        names = [p.name for p in frames.frames_in_order(tmp_path)]

        assert names == ["frame_0001.jpg", "frame_0002.jpg", "frame_0003.jpg"]

    def test_the_foreign_name_is_named_on_stderr(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # Excluding it silently would be the same class of failure one level up:
        # the count of frames would just be smaller than the directory, with
        # nothing saying which file went or why.
        self._lay_out(tmp_path, [1, 2])
        (tmp_path / "frame_a_0001.jpg").write_bytes(b"jpeg")

        frames.frames_in_order(tmp_path)

        err = capsys.readouterr().err
        # The filename, not a bare count: the point of the line is to send
        # someone to the specific file, and "1 file excluded" does not.
        assert "frame_a_0001.jpg" in err
        assert "frame_" in err and "scheme" in err.lower()

    def test_a_name_without_a_number_is_excluded_rather_than_sorted_last(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # It used to sort last and stay in the list, which handed
        # `pair_with_timestamps` one more file than there were timestamps — so a
        # stray file produced the "frames that remain may be misaligned" warning
        # about frames that were all fine.
        self._lay_out(tmp_path, [1, 2])
        (tmp_path / "frame_partial.jpg").write_bytes(b"jpeg")

        names = [p.name for p in frames.frames_in_order(tmp_path)]

        assert names == ["frame_0001.jpg", "frame_0002.jpg"]
        assert "frame_partial.jpg" in capsys.readouterr().err

    def test_an_ordinary_directory_says_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        self._lay_out(tmp_path, [1, 2, 3])
        assert len(frames.frames_in_order(tmp_path)) == 3
        assert capsys.readouterr().err == ""

    def test_five_digit_names_are_still_the_scheme(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # `%04d` is a MINIMUM width. Past 9999 ffmpeg writes five digits, and
        # those names are as legitimate as any other — a scheme check that
        # rejected them would break the very run test_quiet_failures.py pins.
        self._lay_out(tmp_path, [9999, 10000, 10001])
        names = [p.name for p in frames.frames_in_order(tmp_path)]
        assert names == ["frame_9999.jpg", "frame_10000.jpg", "frame_10001.jpg"]
        assert capsys.readouterr().err == ""

    def test_cue_frames_beside_detail_frames_stay_silent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # Two schemes in one directory is the SUPPORTED case, and it is supported
        # because the globs do not overlap. Pinned rather than assumed.
        self._lay_out(tmp_path, [1, 2])
        (tmp_path / "cue_0000.jpg").write_bytes(b"jpeg")

        detail = [p.name for p in frames.frames_in_order(tmp_path)]
        cues = [p.name for p in frames.frames_in_order(tmp_path, frames.CUE_FRAMES)]

        assert detail == ["frame_0001.jpg", "frame_0002.jpg"]
        assert cues == ["cue_0000.jpg"]
        assert capsys.readouterr().err == ""

    def test_the_writer_reads_the_same_constant_as_the_sorter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The constant is worth nothing if a call site keeps its own literal, so
        # this changes the scheme and checks ffmpeg is told about it. A writer
        # holding `frame_%04d.jpg` inline fails here and nowhere else.
        monkeypatch.setattr(frames, "DETAIL_FRAMES", frames.FrameScheme("shot_"))
        out_dir = tmp_path / "frames"
        out_dir.mkdir()
        (out_dir / "shot_0009.jpg").write_bytes(b"stale")
        seen: dict[str, list[str]] = {}

        def fake_run(cmd, *args, **kwargs):
            seen["cmd"] = list(cmd)
            for n in (1, 2):
                (out_dir / f"shot_{n:04d}.jpg").write_bytes(b"jpeg")
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = ""
            result.stderr = "".join(
                f"[Parsed_showinfo_1 @ 0x0] n:{i} pts_time:{float(i)} \n"
                for i in range(2)
            )
            return result

        monkeypatch.setattr(frames.subprocess, "run", fake_run)
        got, untimed = frames.extract_scene_candidates("video.mp4", out_dir, resolution=512)

        assert str(out_dir / "shot_%04d.jpg") in seen["cmd"]
        assert [Path(f["path"]).name for f in got] == ["shot_0001.jpg", "shot_0002.jpg"]
        # `untimed`, NOT `not (out_dir / "shot_0009.jpg").exists()`. That was the
        # obvious assertion and it is vacuous: `pair_with_timestamps` deletes any
        # frame it cannot time, so the stale file is gone whether the sweep found
        # it or not, and a sweep mutated back to its own `frame_*.jpg` literal
        # passed it. What actually changes is the COUNT — the stale frame reaches
        # the pairing step, gets dropped, and the run reports "ffmpeg reported 2
        # timestamps for 3 frames" about a file ffmpeg never wrote. Measured: 1
        # with the literal, 0 with the shared constant.
        assert untimed == 0

    def test_a_foreign_name_does_not_shift_the_engine_output(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # End to end, because the helper being right is worth nothing if the
        # shift reappears at the call site. ffmpeg reports three timestamps and
        # writes three frames; a fourth file from somewhere else must not make
        # frame 2 answer to frame 1's time.
        out_dir = tmp_path / "frames"

        def fake_run(cmd, *args, **kwargs):
            out_dir.mkdir(parents=True, exist_ok=True)
            for n in (1, 2, 3):
                (out_dir / f"frame_{n:04d}.jpg").write_bytes(b"jpeg")
            (out_dir / "frame_a_0001.jpg").write_bytes(b"foreign")
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = ""
            result.stderr = "".join(
                f"[Parsed_showinfo_1 @ 0x0] n:{i} pts_time:{float(n)} \n"
                for i, n in enumerate((10, 20, 30))
            )
            return result

        monkeypatch.setattr(frames.subprocess, "run", fake_run)
        got, untimed = frames.extract_scene_candidates("video.mp4", out_dir, resolution=512)

        assert [(Path(f["path"]).name, f["timestamp_seconds"]) for f in got] == [
            ("frame_0001.jpg", 10.0),
            ("frame_0002.jpg", 20.0),
            ("frame_0003.jpg", 30.0),
        ]
        # And the foreign file must not be counted as a frame that lost its
        # timestamp — that warning is about ffmpeg, and this is not ffmpeg.
        assert untimed == 0

    def test_no_call_site_spells_a_frame_filename_itself(self) -> None:
        # The behavioural tests above reach three of the four writers; nothing
        # here drives `extract_cue_frames` end to end, because doing so means
        # synthesizing a clip for a path whose only change is which constant it
        # reads. This covers all four at once, and it is the invariant the
        # deferral actually asked for — ONE owner for the scheme, not four
        # copies that happen to agree today.
        #
        # NON-GOAL: this reads source text. It covers the two shapes a writer
        # would plausibly reach for — a printf template and an f-string width —
        # but not one assembled by concatenation (`prefix + "%04d.jpg"`), and it
        # says nothing about whether the scheme is used correctly at a site that
        # does read the constant.
        found = FILENAME_LITERAL.findall(FRAMES_PY.read_text())

        # Zero, not one: FrameScheme builds both shapes from its `prefix`
        # argument (`f"{prefix}*.jpg"`), so even the owner never spells a
        # complete frame filename. Any match is a call site that went its own way.
        assert found == [], f"frame filename literals outside FrameScheme: {found}"
