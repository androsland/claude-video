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

  * `snapshot_dir` records (mtime, size) per name before yt-dlp starts and asks
    "is this file new or changed since then". That is the right question for a
    REUSED directory and the wrong one for a SHARED one: a file a second moviola
    process writes during the window is new-since-the-snapshot and reads as ours.
    The two runs also overwrite each other's `video.*` and `frame_*.jpg`
    outright, so the report is assembled from a mix of two films with nothing
    anywhere saying so.

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
  * The lock is advisory and POSIX-only. It cannot see a run on a host without
    `fcntl`, a filesystem where `flock` is a no-op (many NFS mounts), or a
    non-moviola process writing into the directory. It is a guard against the
    accident these tests describe — the same user starting a second run — not a
    concurrency primitive, and nothing here tests it under real contention.
  * The legitimate configuration the lock must NOT fire on is two runs in two
    DIFFERENT directories, which is the ordinary case: the default work dir is a
    fresh `mkdtemp`, so only an explicit shared `--out-dir` can collide at all.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import download
import frames
import workdir

FRAMES_PY = (
    Path(__file__).resolve().parent.parent
    / "skills" / "moviola" / "scripts" / "frames.py"
)
SCRIPTS = FRAMES_PY.parent
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


class TestTheWorkDirectoryIsHeldExclusively:
    """One run owns the work directory for its whole span, or it does not start."""

    def test_a_second_run_is_refused_while_the_first_holds_it(
        self, tmp_path: Path
    ) -> None:
        # The failure this prevents is not a crash. Two runs sharing one
        # `--out-dir` overwrite each other's `video.*` and `frame_*.jpg`, and
        # `snapshot_dir` reads the other run's files as this run's because they
        # are new since the snapshot — so the report is a mix of two films and
        # says nothing about it.
        with workdir.exclusive(tmp_path):
            with pytest.raises(SystemExit):
                with workdir.exclusive(tmp_path):
                    pass

    def test_the_refusal_names_the_holder(self, tmp_path: Path) -> None:
        # "Directory is locked" sends nobody anywhere. The pid is what lets
        # someone decide between waiting and picking another directory.
        with workdir.exclusive(tmp_path):
            with pytest.raises(SystemExit) as exc:
                with workdir.exclusive(tmp_path):
                    pass

        message = str(exc.value)
        assert str(os.getpid()) in message
        # The REMEDY clause, not a bare `"--out-dir" in message`. That was the
        # obvious assertion and it is vacuous: the refusal names the flag twice,
        # once to explain the collision and once to say what to do about it, so
        # a mutation deleting the actionable half left the substring in place
        # and the test green. Measured: the flag appears 2x in the message.
        assert "Pass a different --out-dir" in message

    def test_the_lock_is_released_when_the_run_finishes(self, tmp_path: Path) -> None:
        with workdir.exclusive(tmp_path):
            pass
        # No SystemExit, and nothing left behind in a directory the user is told
        # to look at afterwards.
        with workdir.exclusive(tmp_path):
            pass
        assert list(tmp_path.iterdir()) == []

    def test_the_lock_is_released_when_the_run_raises(self, tmp_path: Path) -> None:
        # moviola raises SystemExit on most failure paths. A lock that survived
        # one would make the NEXT run refuse for a reason that no longer exists.
        with pytest.raises(SystemExit):
            with workdir.exclusive(tmp_path):
                raise SystemExit("ffmpeg is not installed")
        # The FILE, not just the lock. Re-acquiring proves nothing on its own:
        # closing the fd in the outer `finally` drops the kernel lock whether or
        # not the inner cleanup ran, so a mutation that skips cleanup on the
        # raise path still let the next `exclusive()` through. What it leaves
        # behind is the record file, in a directory the refusal tells the user
        # to go and look at.
        assert not (tmp_path / workdir.LOCK_NAME).exists()
        with workdir.exclusive(tmp_path):
            pass

    def test_two_different_directories_do_not_block_each_other(
        self, tmp_path: Path
    ) -> None:
        # The must-not-fire case, and it is the ordinary one: without an
        # explicit --out-dir every run gets its own mkdtemp.
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        with workdir.exclusive(a), workdir.exclusive(b):
            pass

    def test_a_killed_run_does_not_leave_the_directory_locked(
        self, tmp_path: Path
    ) -> None:
        # This is the whole reason it is `flock` and not a pid file. The kernel
        # drops the lock when the fd closes, including on SIGKILL, so there is
        # no stale-lock state and nothing has to decide whether a recorded pid
        # is still alive — a decision that is wrong the moment the pid is reused.
        holder = subprocess.Popen(
            [
                sys.executable, "-c",
                "import sys, time; sys.path.insert(0, %r); import workdir\n"
                "with workdir.exclusive(__import__('pathlib').Path(%r)):\n"
                "    print('held', flush=True); time.sleep(60)"
                % (str(SCRIPTS), str(tmp_path)),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "held"
            with pytest.raises(SystemExit):
                with workdir.exclusive(tmp_path):
                    pass
        finally:
            holder.kill()
            holder.wait(timeout=10)

        with workdir.exclusive(tmp_path):
            pass

    def test_the_lock_file_is_not_mistaken_for_output(self, tmp_path: Path) -> None:
        # It lives in a directory two other modules glob. A lock that showed up
        # as a frame, or as the downloaded video, would trade one wrong report
        # for another.
        with workdir.exclusive(tmp_path):
            assert list(tmp_path.iterdir()) != []
            assert frames.frames_in_order(tmp_path) == []
            assert download._pick_video(tmp_path, {}) is None
            assert download._pick_subtitle(tmp_path, {}) is None

    def test_the_entry_point_takes_the_lock(self, tmp_path: Path) -> None:
        # The module being right is worth nothing if main() never calls it. The
        # lock is held HERE, deterministically, so there is no race in the test
        # — the subprocess must refuse on arrival.
        work = tmp_path / "work"
        work.mkdir()
        with workdir.exclusive(work):
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / "moviola.py"),
                    str(tmp_path / "absent.mp4"), "--out-dir", str(work),
                ],
                capture_output=True, text=True, timeout=120,
            )

        assert result.returncode != 0, result.stdout
        combined = result.stdout + result.stderr
        assert str(os.getpid()) in combined
        # The remedy clause, for the reason spelled out in
        # `test_the_refusal_names_the_holder` — the bare flag appears twice.
        assert "Pass a different --out-dir" in combined

    def test_a_finished_run_leaves_no_lock_behind(self, tmp_path: Path) -> None:
        # `hold` releases through atexit rather than a `with`, so the release
        # path is the one that has no syntax holding it in place — it is worth a
        # test of its own. This run dies on a missing input, which is a
        # `SystemExit` from deep inside main(): the ordinary failure shape.
        work = tmp_path / "work"
        work.mkdir()
        result = subprocess.run(
            [
                sys.executable, str(SCRIPTS / "moviola.py"),
                str(tmp_path / "absent.mp4"), "--out-dir", str(work),
            ],
            capture_output=True, text=True, timeout=120,
        )

        assert result.returncode != 0
        assert not (work / workdir.LOCK_NAME).exists()
        # And the directory is usable again immediately, which is the point.
        with workdir.exclusive(work):
            pass

    def test_hold_keeps_holding_after_it_returns(self, tmp_path: Path) -> None:
        # `hold` is a bare call in the middle of `main()`, not a `with` block, so
        # nothing in the SYNTAX keeps the lock alive for the rest of the run —
        # only the `atexit` registration does, by being the last reference to the
        # ExitStack. Drop that line and the stack is collected the instant `hold`
        # returns, the generator finalizes, and the lock is released before the
        # run has downloaded anything. Measured directly: with the registration
        # removed, a second `exclusive()` on the same directory succeeds
        # immediately. `test_a_finished_run_leaves_no_lock_behind` cannot see
        # this — releasing too EARLY also leaves no lock behind.
        holder = subprocess.Popen(
            [
                sys.executable, "-c",
                "import sys, time; sys.path.insert(0, %r); import workdir\n"
                "workdir.hold(__import__('pathlib').Path(%r))\n"
                "print('held', flush=True); time.sleep(60)"
                % (str(SCRIPTS), str(tmp_path)),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "held"
            with pytest.raises(SystemExit):
                with workdir.exclusive(tmp_path):
                    pass
        finally:
            holder.kill()
            holder.wait(timeout=10)

    @pytest.mark.parametrize(
        "content",
        [b"", b"not json at all", b"[1, 2, 3]", b'{"pid": "nope"}', b"\xff\xfe"],
        ids=["empty", "garbage", "wrong-shape", "wrong-type", "not-utf8"],
    )
    def test_an_unreadable_lock_record_still_describes_something(
        self, tmp_path: Path, content: bytes
    ) -> None:
        # The record is read back off a directory this run does not own, so it
        # is somebody else's output even though moviola wrote the last one.
        # `empty` is not a hypothetical: the holder writes the record just AFTER
        # taking the lock, so a run arriving inside that window finds exactly
        # this and must still say something a person can act on.
        lock = tmp_path / workdir.LOCK_NAME
        lock.write_bytes(content)

        described = workdir._describe_holder(lock)

        assert "another moviola run" in described
        assert "\n" not in described

    def test_a_lock_record_cannot_forge_a_stderr_line(self, tmp_path: Path) -> None:
        # The refusal is interpolated into a `[moviola] ` line. A record whose
        # `started` carries a newline would end that line and put whatever
        # follows at column zero, looking exactly like the next thing moviola
        # said — the same forgery `stderr_line` exists to stop everywhere else.
        lock = tmp_path / workdir.LOCK_NAME
        lock.write_text(json.dumps({
            "pid": 4321,
            "started": "now\n[moviola] transcript complete",
        }))

        described = workdir._describe_holder(lock)

        assert "\n" not in described
        assert "transcript complete" in described  # reported, not stripped

        # The pid is NOT fenced, and does not need to be — but only because it
        # is used solely when it is an `int`, and an int has no newline to carry.
        # That type check is the fence here, so it is pinned as one.
        lock.write_text(json.dumps({"pid": "9\n[moviola] transcript complete"}))

        assert "transcript complete" not in workdir._describe_holder(lock)
