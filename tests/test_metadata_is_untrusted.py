"""Numbers that arrive from a subprocess are parsed as strings a stranger wrote.

`get_metadata` builds moviola's whole idea of the video out of ffprobe's JSON,
and two of its fields were handed to a bare `float()` / `int()`:

    duration = float(fmt.get("duration") or video_stream.get("duration") or 0)
    ...
    "size_bytes": int(fmt.get("size") or 0),

`moviola.py` has the same shape on the other producer — the transcript-only path
has no video to probe, so it takes `duration` straight out of yt-dlp's
`info.json`. A value that is present but not a number raises `ValueError` out of
the probe, which is neither caught nor mentioned anywhere, and takes down a run
that could have carried on with "duration unknown" and the frames it can still
extract.

`untrusted.finite_float` is the guard: anything that is not a finite number
becomes the caller's default. Non-finite is included on purpose and is not
pedantry — `float()` accepts `"nan"` and `"inf"` and returns them happily, and
the crash then lands two functions away in `_clamp_fps` as
`int(round(nan))`, a `ValueError: cannot convert float NaN to integer` naming a
frame-budget helper the user never heard of rather than the metadata that was
bad. An INFINITE duration reaches it as nan rather than inf, and this file said
otherwise until a review ran it: `auto_fps(inf)` and `auto_fps_focus(inf)` both
raise that same ValueError, because an infinite duration makes `fps` 0.0 and
`0.0 * inf` is nan. `int(round(inf))` genuinely is an OverflowError, but the
place that expression occurs is `main()`'s `fps_override` branch, where a finite
fps multiplies an infinite duration directly.

Non-numeric, non-finite and OVERSIZED are three separate rejections and the
third arrived last. A Python int has no maximum, `float()` raises
`OverflowError` — not `ValueError` — on one too large for a double, and
`json.loads` produces exactly that shape from a bare JSON integer literal. So
the guard raised the very exception it exists to prevent, on the one producer
whose input is real JSON rather than a string: `_read_info` at
`download.py:200`. `TestTheGuardItself::test_an_oversized_int_becomes_the_default`
is the pin, and it asserts the premise (`float(10**400)` is an `OverflowError`)
alongside the fix so a future Python that changed that would fail loudly here.

**The premise of the original finding was wrong, and correcting it is half of
what this file pins.** TODOS.md recorded this as "a container that reports `N/A`
takes down the whole run", and `N/A` is real — but it is a property of
ffprobe's DEFAULT writer, not of the JSON writer moviola actually asks for.
`test_the_na_string_is_the_default_writers` runs one real ffprobe over one real
file and shows both halves: `duration=N/A` in the default writer's output, and
the key simply absent from `-print_format json`. An absent key was already
handled by the `or 0` chain. So through moviola's own command line the
`ValueError` is NOT reachable today, and this file's guard is defence in depth
rather than a fix for a live crash.

It is still worth having. moviola pins no ffprobe version and no yt-dlp version,
`-show_optional_fields always` (ffmpeg >= 5.1) is a documented way to put the
`N/A` string INTO the JSON, and the yt-dlp half has no writer guarantee of any
kind behind it. The parse is one flag away from being reachable, and the guard
costs four lines.

NON-GOALS, so a green run is not read as more than it is:

  * **This does not claim the crash was reachable.** See above. If
    `test_the_na_string_is_the_default_writers` ever fails on the JSON half,
    that is the news: the guard has stopped being defensive and become
    load-bearing, and TODOS.md should be corrected back.

  * **It pins two producers, and there are only two.** ffprobe via
    `frames.get_metadata`, and yt-dlp via `moviola.metadata_from_info`. A third
    place that ever parses a number out of somebody else's output is not covered
    here and would need its own call to the guard — nothing enforces that rule,
    which is the same caller-side limitation TODOS.md already records for
    `stderr_line`.

  * **"Unknown duration" is 0.0, and the report states it as a fact.**
    `moviola.py` prints `- **Duration:** 00:00 (0.0s)` for a duration it does
    not know, and `auto_fps(0)` budgets exactly one frame. Both predate this
    change — an ABSENT duration key has always produced them — so nothing here
    makes them worse and nothing here fixes them. Filed in TODOS.md.

  * **The legitimate configuration this must not fire on is ordinary metadata.**
    A real ffprobe probe of a real clip must come back with its true duration,
    width, height, codec and size; `test_a_real_probe_is_untouched` runs one so
    that a guard which started defaulting everything to zero would fail here
    rather than pass quietly.

  * **`finite_float` is a coercion, not a validator.** It cannot tell a wrong
    duration from a right one. ffprobe reporting `3.0` for a thirty-second video
    is invisible here and to everything downstream of here.

Every value written below is inert filler. Nothing here reads a real credential.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import types
from pathlib import Path

import pytest

import frames
import moviola
import untrusted


FILLER = "placeholder-value-not-a-credential"


def _stub_ffprobe(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    """Make `frames.get_metadata` see exactly `payload` as ffprobe's JSON."""
    monkeypatch.setattr(frames.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        frames.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout=json.dumps(payload), stderr=""
        ),
    )


def _probe(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", *args, "-show_format", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


class TestTheGuardItself:
    """`finite_float` answers the default for anything that is not a number."""

    @pytest.mark.parametrize(
        "value",
        ["N/A", "", "  ", "unknown", FILLER, None, [], {}, object()],
        ids=["na", "empty", "blank", "word", "filler", "none", "list", "dict", "object"],
    )
    def test_non_numbers_become_the_default(self, value):
        assert untrusted.finite_float(value, 7.5) == 7.5

    @pytest.mark.parametrize(
        "value", ["nan", "inf", "-inf", "Infinity", float("nan"), float("inf")],
        ids=["nan", "inf", "neg-inf", "Infinity", "nan-float", "inf-float"],
    )
    def test_non_finite_numbers_become_the_default(self, value):
        # `float()` accepts every one of these — that is the point, and it is
        # why `math.isfinite` has to run afterwards rather than the try/except
        # carrying the whole guard.
        #
        # This asserted `float(value) is not None`, which is true of everything
        # `float()` returns and could only ever fail by raising. It pinned
        # "float() does not reject them" while reading as though it pinned
        # something about the result.
        assert not math.isfinite(float(value))
        assert untrusted.finite_float(value, 0.0) == 0.0

    @pytest.mark.parametrize(
        "value",
        [10**400, -(10**400), int("9" * 400)],
        ids=["huge-int", "huge-negative-int", "long-digit-int"],
    )
    def test_an_oversized_int_becomes_the_default(self, value):
        # A Python int has no maximum, and `float()` raises OverflowError —
        # NOT ValueError — when asked to convert one that will not fit a
        # double. `except (TypeError, ValueError)` therefore let it straight
        # through, and the exception this module exists to prevent escaped the
        # guard written to prevent it.
        #
        # Reachable, and only on the yt-dlp half: `_read_info` is
        # `json.loads` over `video.info.json` (download.py:200), and a bare
        # JSON integer literal becomes an unbounded Python int. ffprobe's JSON
        # writer emits `duration` and `size` as strings, and a long digit
        # STRING is safe by a different route — `float()` returns `inf` and
        # `math.isfinite` rejects it — so this is the yt-dlp path alone.
        with pytest.raises(OverflowError):
            float(value)  # the premise: it is not a ValueError
        assert untrusted.finite_float(value, 0.0) == 0.0
        assert moviola.metadata_from_info({"duration": value})["duration_seconds"] == 0.0

    @pytest.mark.parametrize(
        "value,expected",
        [("1.5", 1.5), ("0", 0.0), ("0.000000", 0.0), (2, 2.0), (3.25, 3.25), ("-4", -4.0)],
    )
    def test_real_numbers_are_returned(self, value, expected):
        assert untrusted.finite_float(value, 99.0) == expected

    def test_the_default_defaults_to_zero(self):
        assert untrusted.finite_float("N/A") == 0.0


class TestTheGuardCoversItsOwnDefault:
    """A function named for finiteness must not hand back a non-finite number.

    `default` was returned unexamined from both exit paths, so
    `finite_float(x, float("inf"))` answered inf out of the guard that exists to
    reject inf. Latent rather than live — every call site in the tree passes
    `0.0`, and the one that passes a computed value nests a `finite_float` call
    the same guard already vetted — but the name is the promise the next caller
    reads, and nothing enforced it.
    """

    @pytest.mark.parametrize(
        "bad",
        [float("inf"), float("-inf"), float("nan")],
        ids=["inf", "neg-inf", "nan"],
    )
    def test_a_non_finite_default_is_refused(self, bad):
        # ValueError, not a coerced 0.0. `value` is a stranger's string and gets
        # coerced; `default` is a literal a moviola author typed, so a bad one
        # is this program's bug and silently repairing it hides the bug at the
        # only moment anyone could see it.
        with pytest.raises(ValueError, match="default"):
            untrusted.finite_float("N/A", bad)

    def test_the_refusal_does_not_wait_for_bad_data(self):
        # Checked on entry, not at the point of return. A lazy check only fires
        # when `value` also happens to be unparseable, so a caller with an
        # infinite default would ship green and fail the first time a stranger
        # sent something odd — the defect surfacing far from the line that
        # caused it, which is exactly the shape this module exists to stop.
        with pytest.raises(ValueError, match="default"):
            untrusted.finite_float("1.5", float("inf"))

    @pytest.mark.parametrize(
        "good", [0.0, 7.5, -4.0, 0, 10**300], ids=["zero", "float", "negative", "int", "large"]
    )
    def test_every_finite_default_still_passes(self, good):
        # The refusal is finiteness and nothing else. Negative and very large
        # defaults are legitimate and must not be caught by it: magnitude and
        # sign are separate findings with separate owners, and a guard that
        # quietly took them too would be a behaviour change wearing a fix's
        # clothes.
        assert untrusted.finite_float("N/A", good) == good

    def test_a_default_that_is_not_a_number_at_all_is_refused(self):
        # `math.isfinite(None)` raises TypeError, which would escape as a
        # different exception naming the math module. The guard answers for its
        # own parameter rather than letting stdlib do it in a worse voice.
        with pytest.raises(ValueError, match="default"):
            untrusted.finite_float("N/A", "0.0")


class TestFfprobeMetadata:
    """A non-numeric field degrades to unknown instead of killing the run."""

    def test_a_non_numeric_duration_does_not_raise(self, tmp_path):
        meta = None
        with pytest.MonkeyPatch.context() as mp:
            _stub_ffprobe(mp, {"format": {"duration": "N/A"}, "streams": []})
            meta = frames.get_metadata(str(tmp_path / "video.mp4"))
        assert meta["duration_seconds"] == 0.0

    def test_a_non_finite_duration_does_not_reach_the_frame_budget(self, tmp_path):
        with pytest.MonkeyPatch.context() as mp:
            _stub_ffprobe(mp, {"format": {"duration": "nan"}, "streams": []})
            meta = frames.get_metadata(str(tmp_path / "video.mp4"))
        assert meta["duration_seconds"] == 0.0
        # The reason non-finite is in the guard at all: this call is what used
        # to raise, one function past the bad value and naming neither it nor
        # ffprobe.
        assert math.isfinite(meta["duration_seconds"])
        frames.auto_fps(meta["duration_seconds"])

    def test_an_unparseable_format_duration_falls_through_to_the_stream(self, tmp_path):
        # Strictly better than the `or` chain it replaces: `"N/A"` is truthy, so
        # the old code took it and never consulted the stream that knew.
        with pytest.MonkeyPatch.context() as mp:
            _stub_ffprobe(
                mp,
                {
                    "format": {"duration": "N/A"},
                    "streams": [{"codec_type": "video", "duration": "12.5"}],
                },
            )
            meta = frames.get_metadata(str(tmp_path / "video.mp4"))
        assert meta["duration_seconds"] == 12.5

    def test_a_non_numeric_size_does_not_raise(self, tmp_path):
        with pytest.MonkeyPatch.context() as mp:
            _stub_ffprobe(mp, {"format": {"duration": "3.0", "size": "N/A"}, "streams": []})
            meta = frames.get_metadata(str(tmp_path / "video.mp4"))
        assert meta["size_bytes"] == 0
        assert meta["duration_seconds"] == 3.0

    def test_a_real_probe_is_untouched(self, static_clip):
        # The legitimate configuration. A guard that defaulted everything to
        # zero would pass every test above and fail this one.
        meta = frames.get_metadata(str(static_clip))
        assert meta["duration_seconds"] == pytest.approx(3.0, abs=0.5)
        assert meta["width"] == 320
        assert meta["height"] == 240
        assert meta["codec"] == "h264"
        assert meta["size_bytes"] > 0


class TestYtDlpMetadata:
    """The transcript-only path has no video to probe and reads yt-dlp instead."""

    def test_a_non_numeric_duration_does_not_raise(self):
        meta = moviola.metadata_from_info({"duration": "N/A", "title": FILLER})
        assert meta["duration_seconds"] == 0.0

    def test_a_missing_info_block_is_zero(self):
        assert moviola.metadata_from_info(None)["duration_seconds"] == 0.0
        assert moviola.metadata_from_info({})["duration_seconds"] == 0.0

    def test_a_real_duration_survives(self):
        assert moviola.metadata_from_info({"duration": 91.4})["duration_seconds"] == 91.4
        assert moviola.metadata_from_info({"duration": "91.4"})["duration_seconds"] == 91.4

    def test_the_shape_matches_what_the_probe_returns(self, static_clip):
        # Both branches feed the same `meta` variable, so a key present in one
        # and absent from the other is a KeyError waiting for a code path.
        #
        # This asserted `set(stand_in) <= {six names}` and could not see the one
        # divergence that exists. Subset is satisfied by a stand-in that DROPS a
        # key, which is the direction the KeyError comes from, and the six names
        # were a literal restating the probe rather than a reading of it — so
        # both halves of the comparison were written by hand and neither was
        # measured. Both sides are read from their producers now, and the
        # difference is asserted exactly rather than bounded.
        stand_in = moviola.metadata_from_info({"duration": 1.0})
        probed = frames.get_metadata(str(static_clip))

        assert set(stand_in) == {
            "duration_seconds", "width", "height", "codec", "has_audio",
        }
        # The divergence, stated as the fact it is: the probe answers with
        # `size_bytes` and the stand-in has no key for it. Nothing reads it off
        # `meta` outside `frames.py` today, so this is latent rather than live —
        # and this line is what a future consumer trips over instead of the
        # KeyError. If the stand-in ever gains the key, delete this assertion;
        # do not widen it.
        assert set(probed) - set(stand_in) == {"size_bytes"}
        assert set(stand_in) - set(probed) == set()


@pytest.fixture(scope="module")
def unknown_duration_clip(tmp_path_factory):
    """A raw H.264 elementary stream: no container, so no timing to report."""
    path = tmp_path_factory.mktemp("nodur") / "raw.h264"
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-t", "1", "-i", "color=c=blue:s=160x120:r=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "h264", str(path),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return path


class TestTheReachabilityCorrection:
    """One real ffprobe, one real file, both writers — the NON-GOAL's evidence."""

    def test_the_na_string_is_the_default_writers(self, unknown_duration_clip):
        # If this half ever fails, ffprobe stopped saying N/A at all and the
        # correction in TODOS.md needs re-deriving from scratch.
        assert "duration=N/A" in _probe(unknown_duration_clip)

    def test_the_json_writer_omits_the_key_instead(self, unknown_duration_clip):
        # And if THIS half ever fails, the guard has stopped being defensive:
        # the ValueError became reachable through moviola's own command line,
        # and TODOS.md should be corrected back.
        payload = json.loads(_probe(unknown_duration_clip, "-print_format", "json"))
        assert "duration" not in payload["format"]
        assert "N/A" not in json.dumps(payload)

    def test_moviola_asks_for_the_json_writer(self, tmp_path):
        # The correction only holds while the probe keeps asking for JSON.
        seen: list[list[str]] = []

        def record(cmd, *a, **k):
            seen.append(list(cmd))
            return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(frames.shutil, "which", lambda name: "/usr/bin/" + name)
            mp.setattr(frames.subprocess, "run", record)
            frames.get_metadata(str(tmp_path / "video.mp4"))

        assert seen and seen[0][0] == "ffprobe"
        assert "-print_format" in seen[0]
        assert seen[0][seen[0].index("-print_format") + 1] == "json"
        assert "-show_optional_fields" not in seen[0]


def _stub_stdout(monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    """Like `_stub_ffprobe`, but the stdout is a RAW string rather than a payload.

    `_stub_ffprobe` takes a dict and serialises it, so it can only ever produce
    valid JSON objects — which is precisely the assumption under test here.
    """
    monkeypatch.setattr(frames.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        frames.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout=stdout, stderr=""
        ),
    )


class TestTheDocumentIsUntrustedAndNotOnlyItsFields:
    """One trust boundary up from everything above.

    `finite_float` asks whether a VALUE inside ffprobe's document is a number.
    Nothing asked whether there was a document. `get_metadata` read
    `json.loads(result.stdout or "{}")` and called `.get()` on the result, and
    two shapes went through:

      * `json.loads` RAISES `ValueError` when stdout is not JSON at all — a
        shim, a wrapper printing a warning, a busybox applet, anything on PATH
        answering to the name. Nothing caught it, so the run died of a
        `JSONDecodeError` naming a column of a string the user never saw.
      * `json.loads` SUCCEEDS on `[]`, `3`, `"text"`, `null` and `true`. All
        are valid JSON and none is an object. This is the shape that reads as
        safe — the parse worked — and the failure lands one line later as
        `AttributeError: 'list' object has no attribute 'get'`, naming a dict
        method rather than the subprocess that wrote the document.

    A third arrived with the guard rather than before it: `json.loads` raises
    `RecursionError`, not `ValueError`, on a document nested past the
    interpreter's limit, which is roughly a thousand deep and costs a
    two-kilobyte string to reach.

    All three now answer the same thing — this is not the document ffprobe
    promised — and the run stops rather than carrying on, with what was
    actually written fenced and attributed. Stopping is the same call the
    returncode guard four lines above already makes: a probe that answers with
    something other than its own format is not evidence about the video, it is
    evidence about what is on PATH, and a report built from `{}` would state a
    duration of zero as a fact.

    NON-GOALS, so a green run is not read as more than it is:

      * **Empty stdout is unchanged and still means an empty document.**
        `or "{}"` predates this and survives it: a probe that exits 0 and says
        nothing yields metadata with everything defaulted, exactly as before.
        That is a different claim from "said something that is not JSON", and
        conflating them would have changed a behaviour this finding is not
        about. The zero-duration report it produces is filed separately.

      * **Shape, not contents.** A document that IS an object passes here
        whatever is inside it. `{"streams": "not a list"}` is somebody else's
        problem — `next((s for s in ...))` over a string yields characters —
        and this test says nothing about it.

      * **It cannot see a probe that lies consistently.** A shim emitting a
        well-formed ffprobe document describing a different video passes every
        assertion below. The guard is about whether the bytes are the shape
        that was promised, never about whether they are true.

      * **The reachability story is unchanged and is still low.** A real
        ffprobe under `-v error -print_format json` either writes JSON or exits
        non-zero, and the returncode guard already owns the second half. This
        is defence in depth on the same footing as the `finite_float` guards,
        and TODOS.md records it that way.

    Every value written below is inert filler. Nothing here reads a real
    credential.
    """

    NOT_JSON = "ffprobe: unrecognized option '-show_streams'"

    def test_stdout_that_is_not_json_is_refused_rather_than_raised_through(
        self, tmp_path: Path
    ) -> None:
        with pytest.MonkeyPatch.context() as mp:
            _stub_stdout(mp, self.NOT_JSON)
            with pytest.raises(SystemExit) as caught:
                frames.get_metadata(str(tmp_path / "video.mp4"))
        message = str(caught.value)
        assert "ffprobe" in message, message
        # The point of the guard is that the diagnostic names the subprocess,
        # not the json module. A traceback naming `json/decoder.py` is what
        # this replaces.
        assert "JSONDecodeError" not in message, message

    def test_the_refusal_shows_what_was_actually_written(
        self, tmp_path: Path
    ) -> None:
        with pytest.MonkeyPatch.context() as mp:
            _stub_stdout(mp, self.NOT_JSON)
            with pytest.raises(SystemExit) as caught:
                frames.get_metadata(str(tmp_path / "video.mp4"))
        assert self.NOT_JSON in str(caught.value)

    def test_the_capture_is_fenced_like_every_other_foreign_block(
        self, tmp_path: Path
    ) -> None:
        # Same instrument the seven stderr sites use, on the one stdout that
        # ever reaches a diagnostic. A foreign line must not reach column zero.
        with pytest.MonkeyPatch.context() as mp:
            _stub_stdout(mp, self.NOT_JSON)
            with pytest.raises(SystemExit) as caught:
                frames.get_metadata(str(tmp_path / "video.mp4"))
        lines = str(caught.value).split("\n")
        assert not lines[0].startswith(untrusted.BLOCK_PREFIX), lines[0]
        assert "ffprobe" in lines[1], lines[1]
        assert any(
            line.startswith(untrusted.BLOCK_PREFIX) and self.NOT_JSON in line
            for line in lines
        ), lines

    def test_a_forged_line_cannot_reach_column_zero(self, tmp_path: Path) -> None:
        # The reason the fence is here and not a plain f-string: the capture is
        # multi-line and a stranger chose every line of it.
        forged = "not json\nffprobe failed:\n{}"
        with pytest.MonkeyPatch.context() as mp:
            _stub_stdout(mp, forged)
            with pytest.raises(SystemExit) as caught:
                frames.get_metadata(str(tmp_path / "video.mp4"))
        body = str(caught.value).split("\n")[2:]
        assert body, str(caught.value)
        for line in body:
            assert line.startswith(untrusted.BLOCK_PREFIX) or line.startswith("--"), (
                f"a foreign line reached column zero: {line!r}"
            )

    @pytest.mark.parametrize(
        "stdout",
        ["[]", '["a", "b"]', "3", "3.5", '"a string"', "null", "true"],
        ids=["array", "array-of-strings", "int", "float", "string", "null", "bool"],
    )
    def test_valid_json_that_is_not_an_object_is_refused(
        self, stdout: str, tmp_path: Path
    ) -> None:
        # The half that reads as safe. The parse SUCCEEDS; `.get()` is what
        # fails, one line later, naming a dict method.
        with pytest.MonkeyPatch.context() as mp:
            _stub_stdout(mp, stdout)
            with pytest.raises(SystemExit) as caught:
                frames.get_metadata(str(tmp_path / "video.mp4"))
        assert "ffprobe" in str(caught.value)

    def test_the_failure_is_not_an_attribute_error(self, tmp_path: Path) -> None:
        # Pinned by name, because `AttributeError` is what a reader of the
        # original code would have to guess at from a traceback.
        with pytest.MonkeyPatch.context() as mp:
            _stub_stdout(mp, "[]")
            try:
                frames.get_metadata(str(tmp_path / "video.mp4"))
            except SystemExit:
                pass
            except AttributeError as exc:  # pragma: no cover - the defect
                pytest.fail(f"the list reached `.get()`: {exc}")

    def test_a_document_nested_past_the_interpreters_limit_is_refused(
        self, tmp_path: Path
    ) -> None:
        # `json.loads` raises RecursionError here, not ValueError, so a guard
        # catching only the latter would let this through unchanged. Two
        # kilobytes of brackets is enough — the limit is ~1000 and the parse
        # bails in well under a millisecond.
        depth = sys.getrecursionlimit() * 2
        bomb = "[" * depth + "]" * depth
        with pytest.MonkeyPatch.context() as mp:
            _stub_stdout(mp, bomb)
            with pytest.raises(SystemExit) as caught:
                frames.get_metadata(str(tmp_path / "video.mp4"))
        assert "ffprobe" in str(caught.value)

    def test_an_empty_stdout_still_means_an_empty_document(
        self, tmp_path: Path
    ) -> None:
        # The must-not-fire half, and the behaviour this change deliberately
        # leaves alone. `or "{}"` is older than the guard and outlives it.
        with pytest.MonkeyPatch.context() as mp:
            _stub_stdout(mp, "")
            meta = frames.get_metadata(str(tmp_path / "video.mp4"))
        assert meta["duration_seconds"] == 0.0
        assert meta["width"] is None
        assert meta["has_audio"] is False

    def test_whitespace_only_stdout_counts_as_nothing_written(
        self, tmp_path: Path
    ) -> None:
        # The seam between "wrote nothing" and "wrote something else". A newline
        # is not a claim about the video, so it belongs on the empty side — and
        # it has to be put there explicitly, because `"\n" or "{}"` is `"\n"`.
        # It is also what keeps `stderr_block`'s empty-capture branch out of
        # reach from this call site: that branch says "wrote nothing to stderr",
        # which is the wrong stream for the one stdout that reaches a
        # diagnostic here.
        for blank in ("\n", "   ", "\r\n\t "):
            with pytest.MonkeyPatch.context() as mp:
                _stub_stdout(mp, blank)
                meta = frames.get_metadata(str(tmp_path / "video.mp4"))
            assert meta["duration_seconds"] == 0.0, blank
            assert meta["width"] is None, blank

    def test_an_object_is_read_exactly_as_before(self, tmp_path: Path) -> None:
        # The other must-not-fire half. A guard that started refusing real
        # documents would fail here rather than pass quietly.
        with pytest.MonkeyPatch.context() as mp:
            _stub_ffprobe(
                mp,
                {
                    "format": {"duration": "12.5", "size": "4096"},
                    "streams": [
                        {
                            "codec_type": "video",
                            "codec_name": "h264",
                            "width": 640,
                            "height": 480,
                        },
                        {"codec_type": "audio", "codec_name": "aac"},
                    ],
                },
            )
            meta = frames.get_metadata(str(tmp_path / "video.mp4"))
        assert meta == {
            "duration_seconds": 12.5,
            "width": 640,
            "height": 480,
            "codec": "h264",
            "size_bytes": 4096,
            "has_audio": True,
        }


class TestTheGuardIsALeafAndDecidesNothing:
    """`untrusted.json_object` answers the shape question and no other.

    It lives beside `finite_float` for the reason AGENTS.md gives: a guarded
    parse of somebody else's output belongs in the leaf module, not in the
    caller that happens to need it first. What it deliberately does NOT do is
    choose what a failure means — it answers `None`, and `get_metadata` decides
    that `None` is fatal. A caller that wanted to carry on with no metadata
    would read the same `None` and do the opposite, which is why the policy is
    not in here.

    NON-GOALS:

      * **It is not a schema check.** `{}` and a document with every key
        missing are the same answer. What is INSIDE an object is
        `finite_float`'s problem and the caller's.
      * **It is not a size bound.** A hundred-megabyte JSON object parses and
        is returned. The bound that matters for this program is the one on
        `subprocess.run`'s capture, which is upstream of here and is not this
        function's to give.
    """

    @pytest.mark.parametrize(
        "text",
        ["{}", '{"a": 1}', '{"nested": {"deep": [1, 2, 3]}}'],
        ids=["empty", "flat", "nested"],
    )
    def test_an_object_comes_back_as_a_dict(self, text: str) -> None:
        assert untrusted.json_object(text) == json.loads(text)

    @pytest.mark.parametrize(
        "text",
        ["", "   ", "not json", "{", '{"a": }', "[]", "3", '"s"', "null", "true"],
        ids=[
            "empty", "blank", "prose", "truncated", "malformed",
            "array", "number", "string", "null", "bool",
        ],
    )
    def test_anything_else_is_none(self, text: str) -> None:
        assert untrusted.json_object(text) is None

    def test_a_value_that_is_not_text_at_all_is_none(self) -> None:
        # `json.loads(None)` raises TypeError rather than ValueError, and a
        # caller reading an absent attribute is how it arrives.
        assert untrusted.json_object(None) is None
        assert untrusted.json_object(object()) is None

    def test_a_nesting_bomb_is_none_rather_than_a_recursion_error(self) -> None:
        depth = sys.getrecursionlimit() * 2
        assert untrusted.json_object("[" * depth + "]" * depth) is None

    def test_the_bytes_form_is_accepted(self) -> None:
        # `json.loads` takes bytes, and a caller reading a pipe rather than a
        # `text=True` capture would hand it bytes. Answering None for a valid
        # object would be a false alarm, not a refusal.
        assert untrusted.json_object(b'{"a": 1}') == {"a": 1}
