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

import contextlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import download
import frames
import untrusted
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


@contextlib.contextmanager
def _holding(body: str, work: Path) -> Iterator[subprocess.Popen]:
    """A child process that takes the lock on `work`, says so, and then blocks.

    Both call sites need the same six things — a child on this repo's
    `scripts/`, a line on stdout once the lock is actually held, a bounded
    wait, stderr captured so a child that dies says why instead of surfacing
    as a mystery timeout, a kill on the way out, and a `with` on the Popen so
    the pipes close even when the assertion in the middle raises. Only `body`
    differs, and it is the interesting line: HOW the lock is taken.

    The child sleeps 30s, not 60. Nothing waits for it — every caller kills it
    in a `finally` — so the number is only the ceiling on a hung run, and the
    smaller one keeps a wedged suite bounded.
    """
    with subprocess.Popen(
        [
            sys.executable, "-c",
            "import sys, time; sys.path.insert(0, %r)\n"
            "import workdir\n"
            "from pathlib import Path\n"
            "work = Path(%r)\n"
            "%s" % (str(SCRIPTS), str(work), body),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            assert child.stdout is not None
            assert child.stdout.readline().strip() == "held", (
                "the child never reported holding the lock"
            )
            yield child
        finally:
            child.kill()
            child.wait(timeout=10)


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
        # Excluded is only half of it: the exclusion has to be DISCLOSED, and
        # it has to name the file that was dropped rather than the ones that
        # were kept. A line listing all four names sends the reader to three
        # files that are fine.
        err = capsys.readouterr().err
        assert "frame_a_0001.jpg" in err
        assert "frame_0002.jpg" not in err

    def test_a_foreign_name_cannot_forge_a_line_or_reorder_the_rest(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # The names in that disclosure come off a directory this program did
        # not fill, and they go into the stream moviola narrates itself in. A
        # filename is a place someone can put a newline: `frame_x\nfake.jpg`
        # ends moviola's line and starts one of its own at column zero, which
        # is `stderr_line`'s entire reason for existing. Strip the call and the
        # whole suite still passed, so this is the test that holds it there.
        self._lay_out(tmp_path, [1])
        sneaky = "frame_x\nfake.jpg"
        bidi = "frame_y\u202e0001.jpg"
        (tmp_path / sneaky).write_bytes(b"jpeg")
        (tmp_path / bidi).write_bytes(b"jpeg")

        frames.frames_in_order(tmp_path)

        err = capsys.readouterr().err
        assert err.strip().count("\n") == 0, "a filename opened a second line"
        assert sneaky not in err, "the raw newline reached stderr"
        assert untrusted.stderr_line(sneaky) in err
        # `balance_bidi` APPENDS terminators rather than stripping openers, so
        # the raw name is legitimately still a substring. What must hold is
        # that the override does not outlive the value and reorder the report
        # printed after it.
        assert err.count("\u202e") == err.count("\u202c")

    def test_a_flood_of_foreign_names_does_not_become_the_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # `stderr_line` bounds the SHAPE of each name; nothing bounded how many
        # of them there are, and the count is chosen by whoever filled the
        # directory rather than by this program. Fifty foreign files turned one
        # warning into fifty names of somebody else's text, in the same stream
        # the report is read from — so the line names ten and counts the rest.
        for i in range(50):
            (tmp_path / f"frame_x{i:03d}.jpg").write_bytes(b"jpeg")

        assert frames.frames_in_order(tmp_path) == []

        err = capsys.readouterr().err
        assert "50 file(s)" in err, "the total must survive the cap"
        assert "and 40 more" in err, "the cap must say what it withheld"
        assert err.count("frame_x") == 10

    def test_two_widths_of_one_number_keep_a_stable_order(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # The collision this module's docstring says it cannot RESOLVE. It can
        # still be required not to thrash: `frame_1.jpg` and `frame_0001.jpg`
        # both read as frame 1, and the sort falls back to the filename so the
        # order is at least the same on every run. Sorting on the number alone
        # leaves the pair in directory order, which is arbitrary and differs
        # between filesystems — and it stays SILENT, because both names are
        # this scheme's own and a disclosure here would be a false alarm.
        for name in ("frame_1.jpg", "frame_0001.jpg", "frame_0002.jpg"):
            (tmp_path / name).write_bytes(b"jpeg")

        names = [p.name for p in frames.frames_in_order(tmp_path)]

        assert names == ["frame_0001.jpg", "frame_1.jpg", "frame_0002.jpg"]
        assert capsys.readouterr().err == ""

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
        body = (
            "with workdir.exclusive(work):\n"
            "    print('held', flush=True); time.sleep(30)"
        )
        with _holding(body, tmp_path):
            with pytest.raises(SystemExit):
                with workdir.exclusive(tmp_path):
                    pass

        with workdir.exclusive(tmp_path):
            pass

    def test_the_lock_file_is_not_mistaken_for_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # It lives in a directory two other modules glob. A lock that showed up
        # as a frame, or as the downloaded video, would trade one wrong report
        # for another.
        with workdir.exclusive(tmp_path):
            assert list(tmp_path.iterdir()) != []
            assert frames.frames_in_order(tmp_path) == []
            assert download._pick_video(tmp_path, {}) is None
            assert download._pick_subtitle(tmp_path, {}) is None

            # Not being COUNTED is only half of it. `frames_in_order` names
            # what it excludes, and `.moviola.lock` is a file moviola itself
            # writes into the directory — a disclosure about it would be this
            # module warning the user about this module, on every single run.
            # It stays quiet because the lock does not match `frame_*.jpg`,
            # and that is the property being pinned rather than assumed.
            assert capsys.readouterr().err == ""

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
        body = (
            "workdir.hold(work)\n"
            "print('held', flush=True); time.sleep(30)"
        )
        with _holding(body, tmp_path):
            with pytest.raises(SystemExit):
                with workdir.exclusive(tmp_path):
                    pass

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


class TestWhatHappensWithNoKernelLockAvailable:
    """The platform fallback: `fcntl` is POSIX, and moviola is not POSIX-only.

    `exclusive` opens with a branch for `fcntl is None` that warns and yields.
    Nothing exercised it, so the branch was free to become anything — a
    `SystemExit` refusing to run on Windows at all, or a silent `yield` — and
    the suite would have stayed green either way. Which one it is matters:
    refusing turns a missing kernel feature into a broken install, and staying
    silent turns it into the mixed report this whole module exists to prevent,
    with nothing anywhere saying the guard was off.

    NON-GOALS:

      * This does not test moviola ON Windows. It simulates the absence of
        `fcntl` and pins the resulting behaviour, which is a different and much
        smaller claim; nothing here has run on a non-POSIX host.
      * It does not make the unguarded case safe. Two runs sharing an
        `--out-dir` without a lock still overwrite each other — the warning is
        the entire remedy, which is why its WORDING is asserted rather than its
        presence.
    """

    def test_without_fcntl_the_run_continues_and_says_the_guard_is_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setattr(workdir, "fcntl", None)

        with workdir.exclusive(tmp_path):
            pass

        err = capsys.readouterr().err
        # The warning has to name the CONSEQUENCE and the LEVER. "no fcntl on
        # this platform" alone is a fact about the interpreter that tells the
        # reader nothing about their video.
        assert "NOT" in err, "the warning does not say the directory is unlocked"
        assert "--out-dir" in err, "the warning does not name the lever"
        assert not (tmp_path / workdir.LOCK_NAME).exists(), (
            "a lock file nothing locks is worse than none — a later run on a "
            "host that DOES have fcntl would find it and describe its holder"
        )

    def test_without_fcntl_a_second_run_is_not_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        # The must-not-fire for the fallback. Refusing here would mean moviola
        # simply does not run twice on a host without `fcntl`, including in two
        # different directories, and the branch would be a platform ban wearing
        # a warning's clothes.
        monkeypatch.setattr(workdir, "fcntl", None)

        with workdir.exclusive(tmp_path):
            with workdir.exclusive(tmp_path):
                pass

        assert capsys.readouterr().err.count("[moviola]") == 2


class TestTheLockFileIsNotSomebodyElsesFile:
    """The lock is taken on a file moviola made, in a directory it does not own.

    Everything in `TestTheWorkDirectoryIsHeldExclusively` assumes
    `.moviola.lock` is either absent or a regular file this program wrote. That
    assumption is the same one the rest of this file exists to dismantle: the
    working directory is a user-chosen `--out-dir` the skill tells the agent to
    reuse, so its contents are not this run's to trust. These pin what happens
    when the entry at that path is something else.

    NON-GOALS, so a green run is not read as more than it is:

      * These are about the FINAL component only. A symlinked `--out-dir` is a
        legitimate, ordinary configuration — a `~/videos` that points into
        another volume — and is pinned below as a must-not-fire.
      * They do not make the directory safe to share with a hostile process.
        A process that can write into `--out-dir` can still delete this run's
        frames, plant a `video.mp4`, or fill the disk; the lock is not an
        access control and never was.
      * They say nothing about the parent directory being swapped underneath
        the run. Guarding that needs `dir_fd`-relative opens throughout, which
        is a different design, and nothing here detects it.
      * A write that fails for an ordinary reason — ENOSPC, EIO — still leaves
        a traceback rather than a refusal. The refusals here are for a lock
        path that is the WRONG KIND OF THING, which is the case somebody else
        creates; a full disk is the user's own machine telling them so.
    """

    def test_a_planted_symlink_is_refused_rather_than_followed(
        self, tmp_path: Path
    ) -> None:
        # `os.open(path, O_RDWR | O_CREAT)` follows a symlink at the final
        # component, and the next thing this module does is ftruncate the fd to
        # zero and write the lock record into it. So a planted `.moviola.lock`
        # symlink does not redirect the lock — it DESTROYS whatever the link
        # points at, before the run has downloaded anything. Measured against
        # the pre-fix code: a victim file came back holding
        # `{"pid": ..., "started": ...}`, and the `unlink()` on the way out
        # removed the symlink, so nothing was left to say what had happened.
        victim = tmp_path / "victim.txt"
        victim.write_text("IMPORTANT USER DATA\n")
        lock = tmp_path / workdir.LOCK_NAME
        lock.symlink_to(victim)

        with pytest.raises(SystemExit) as exc:
            with workdir.exclusive(tmp_path):
                pass

        assert victim.read_text() == "IMPORTANT USER DATA\n"
        # The link itself survives too. Removing it would destroy the only
        # evidence that someone put it there.
        assert lock.is_symlink()
        assert str(lock) in str(exc.value)
        assert "Remove it, or pass a different --out-dir" in str(exc.value)

    @pytest.mark.parametrize(
        "plant",
        [lambda path: path.mkdir(), lambda path: os.mkfifo(path)],
        ids=["directory", "fifo"],
    )
    def test_a_lock_path_that_is_not_a_regular_file_is_refused(
        self, tmp_path: Path, plant
    ) -> None:
        # Both of these reach real code. A directory raises IsADirectoryError
        # out of `os.open`; a FIFO opens fine under O_RDWR and then raises
        # EINVAL out of `os.ftruncate`, several lines past the only `except`
        # in the function. Either way an unhandled traceback escapes `main()`,
        # naming ftruncate rather than the planted file — so a directory a
        # stranger can write to becomes a denial of service on every run
        # pointed at it, described in terms of the wrong thing.
        lock = tmp_path / workdir.LOCK_NAME
        plant(lock)

        with pytest.raises(SystemExit) as exc:
            with workdir.exclusive(tmp_path):
                pass

        assert str(lock) in str(exc.value)
        assert "Remove it, or pass a different --out-dir" in str(exc.value)

    def test_a_symlinked_out_dir_is_still_an_ordinary_directory(
        self, tmp_path: Path
    ) -> None:
        # The must-not-fire. `O_NOFOLLOW` applies to the final component, and
        # the final component here is `.moviola.lock`, not the directory. A
        # `--out-dir` that is itself a symlink — the ordinary shape of a
        # `~/videos` pointing at another volume — must lock exactly as before.
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)

        with workdir.exclusive(link):
            assert (real / workdir.LOCK_NAME).exists()
        assert not (real / workdir.LOCK_NAME).exists()

    def test_the_lock_is_held_on_the_file_the_path_still_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The window is between `os.open` and the `flock` two lines later. A
        # holder leaving in exactly that window unlinks the entry this run just
        # opened; the flock then succeeds on an inode with no name, and the
        # NEXT arrival opens the path, creates a fresh inode, locks that, and
        # runs concurrently. Two runs, two inodes, both convinced they hold the
        # directory — which is the mixed report this module exists to prevent,
        # reached through the module itself.
        #
        # Reproduced by hand before the fix: fd A on the unlinked inode and fd
        # B on a fresh one both took LOCK_EX | LOCK_NB successfully. Simulated
        # here rather than raced, because a real race is not a test.
        fired: list[int] = []
        real_open = os.open

        def racing_open(path, flags, mode=0o777):
            fd = real_open(path, flags, mode)
            if str(path).endswith(workdir.LOCK_NAME) and not fired:
                fired.append(1)
                # The holder exits here, between our open and our flock.
                os.unlink(path)
            return fd

        monkeypatch.setattr(os, "open", racing_open)

        with workdir.exclusive(tmp_path):
            assert fired, "the simulated race never fired"
            # The lock is only worth anything if the file the next arrival
            # opens is the one this run holds.
            assert (tmp_path / workdir.LOCK_NAME).exists()
            with pytest.raises(SystemExit):
                with workdir.exclusive(tmp_path):
                    pass

    def test_a_lock_record_that_is_gone_still_describes_something(
        self, tmp_path: Path
    ) -> None:
        # `_describe_holder` runs only after a flock has FAILED, so the file
        # existed a moment ago — but the holder's release path unlinks it, and
        # that can land between the two. `read_text` on a missing file raises
        # FileNotFoundError, which is an OSError and not the ValueError the
        # guard catches, so the refusal turned into a traceback.
        described = workdir._describe_holder(tmp_path / workdir.LOCK_NAME)

        assert "another moviola run" in described
        assert "\n" not in described

    def test_an_enormous_lock_record_is_not_read_whole(self, tmp_path: Path) -> None:
        # The record is a file this run does not own, read with no size bound.
        # Leading whitespace is legal JSON, so 70KB of it followed by a valid
        # record parses fine today — which is the demonstration that the size
        # is set by whoever wrote the file rather than by this program.
        lock = tmp_path / workdir.LOCK_NAME
        lock.write_bytes(b" " * (70 * 1024) + json.dumps({"pid": 4321}).encode())

        described = workdir._describe_holder(lock)

        assert "pid 4321" not in described
        assert "another moviola run" in described
        # The MESSAGE, not just the absence of the pid. A truncated read leaves
        # invalid JSON behind, so "could not be read" also hides the pid — and
        # tells the user their lock file is corrupt when it is merely enormous.
        # Asserting the wording is what separates this defence from the one
        # below; without it either could be deleted and this test stays green.
        assert "implausibly large" in described

    def test_the_record_is_never_asked_for_in_one_unbounded_gulp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The size check runs AFTER the read, which makes it no protection on
        # its own: a lock path pointing at /dev/zero has no size to check and
        # the read simply never returns. The bound has to be on the REQUEST,
        # and the only way to see a bound on a request is to watch the request.
        requested: list[int] = []
        watching: list[int] = []
        real_read = os.read

        def spy_read(fd: int, count: int) -> bytes:
            if watching:
                requested.append(count)
            return real_read(fd, count)

        monkeypatch.setattr(os, "read", spy_read)
        lock = tmp_path / workdir.LOCK_NAME
        lock.write_bytes(b'{"pid": 11}')

        watching.append(1)
        try:
            described = workdir._describe_holder(lock)
        finally:
            watching.clear()

        assert "pid 11" in described, "the ordinary path stopped working"
        assert requested, "the record no longer goes through os.read"
        assert max(requested) <= workdir._MAX_RECORD + 1

    def test_an_enormous_started_field_does_not_become_the_message(
        self, tmp_path: Path
    ) -> None:
        # `stderr_line` neutralizes line breaks and balances bidi marks; it
        # does not shorten anything, and `untrusted`'s own NON-GOALS say
        # bounding the input is the caller's job — `whisper._read_error_body`
        # slices 400 characters before calling in, and that is the pattern.
        # Without a slice here, a valid record carrying a 10KB `started`
        # becomes one 10KB stderr line in the agent's context window.
        #
        # Deliberately UNDER the record cap the test above pins. A field big
        # enough to blow that cap is caught one layer earlier and proves
        # nothing about this slice; the interesting size is the one that
        # arrives as an entirely well-formed record.
        lock = tmp_path / workdir.LOCK_NAME
        lock.write_text(json.dumps({"pid": 4321, "started": "z" * 10_000}))

        described = workdir._describe_holder(lock)

        assert "pid 4321" in described
        assert len(described) < 1000


    def test_the_file_is_removed_before_the_lock_is_released(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The order is deliberate and a comment is all that says so, which means
        # a tidy-up that swapped two adjacent lines would read as harmless. It
        # is not free to swap: releasing first hands the lock to an arrival that
        # already has this inode open, and the unlink then deletes the file that
        # arrival is holding — so the run after THAT one creates a fresh inode
        # and both are live at once. `_acquire`'s inode re-check is what makes
        # that recoverable; this ordering is what makes it rare.
        order: list[str] = []
        real_unlink = Path.unlink
        real_flock = workdir.fcntl.flock

        # The spy goes on `Path.unlink`, not on `os.unlink`. Python 3.10's
        # pathlib routes through a `_accessor` that captured `os.unlink` at
        # import time, so patching the `os` attribute intercepts nothing there
        # and the test passes vacuously with an empty `order`. Patching the
        # method actually called works on every version in the support range.
        def spy_unlink(self, *args, **kwargs):
            if self.name == workdir.LOCK_NAME:
                order.append("unlink")
            return real_unlink(self, *args, **kwargs)

        def spy_flock(fd, operation):
            if operation == workdir.fcntl.LOCK_UN:
                order.append("unlock")
            return real_flock(fd, operation)

        monkeypatch.setattr(Path, "unlink", spy_unlink)
        monkeypatch.setattr(workdir.fcntl, "flock", spy_flock)

        with workdir.exclusive(tmp_path):
            pass

        assert order == ["unlink", "unlock"]

    def test_a_lock_file_replaced_during_the_run_is_not_removed(
        self, tmp_path: Path
    ) -> None:
        # The exit path unlinks by PATH, and by then the path need not name the
        # file this run locked. If something replaced it mid-run, that entry is
        # somebody else's — quite possibly a third run that already holds a lock
        # on it — and removing it takes their lock out from under them, leaving
        # them holding an inode no new arrival will ever open. This run's own
        # lock still dies correctly: the fd closes either way, which is the
        # kernel-level release the whole module rests on.
        lock = tmp_path / workdir.LOCK_NAME
        with workdir.exclusive(tmp_path):
            assert lock.exists()
            lock.unlink()
            lock.write_text("a different run's lock file")

        assert lock.exists(), "this run removed a file that was not its own"
        assert lock.read_text() == "a different run's lock file"
