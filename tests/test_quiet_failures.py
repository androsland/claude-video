"""A wrong answer is never a quiet answer.

Every case here is a path where moviola produced a confident, well-formed
result that was not true, and said nothing. That is worse than a crash: a
traceback stops the user, and a plausible report does not — it goes into an
agent's context and gets acted on.

The four:

  * `download_url` treated "a file matching video* exists in the output
    directory" as proof the download worked, and never looked at yt-dlp's exit
    code. With `--out-dir` pointing at a reused directory, a run whose download
    failed outright picked up the PREVIOUS run's video and reported on it —
    right filename, wrong film, no error anywhere.
  * yt-dlp exiting non-zero while still producing a video is a real and expected
    case (a subtitle variant 429s). It was handled by ignoring the exit code
    entirely, so a partial failure and a clean run were indistinguishable.
  * `TS_RE` required exactly two-digit hours, and WebVTT's hours component is
    OPTIONAL and may be longer than two digits. A spec-legal `MM:SS.mmm` file
    parsed to zero segments, and zero segments is indistinguishable from "this
    video has no captions" — so moviola escalated to a paid API upload while a
    perfectly good transcript sat on disk.
  * Frames were paired with timestamps by position after a LEXICOGRAPHIC sort of
    `frame_%04d.jpg`. Past 9999 frames ffmpeg stops padding, `frame_10000.jpg`
    sorts between `frame_1000.jpg` and `frame_1001.jpg`, and from there every
    image carries somebody else's timestamp. Uncapped scene detection on a long
    video reaches that count.

NON-GOALS, so a green run is not read as more than it is:

  * These pin the four paths the review found. They are not a survey: nothing
    here proves there is no fifth quiet failure, and the shape is common enough
    in this codebase that assuming otherwise would be wrong.
  * Making a failure loud is not making it succeed. A stale-directory run now
    stops with a named reason; it does not recover the download.
  * The frame test proves the ORDER is numeric. It does not prove ffmpeg's
    showinfo timestamps are themselves correct, which is ffmpeg's business.
  * Nothing here touches the case where showinfo reports FEWER timestamps than
    there are frames. That is fixed and pinned in `test_quiet_failures_ii.py`,
    not here — a frame without a timestamp is now dropped rather than labelled
    with the range start.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import download
import frames
import transcribe

VIDEO_BYTES = b"not really an mp4, and nothing here decodes it"


class _FakeResult:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def _fake_ytdlp(
    monkeypatch: pytest.MonkeyPatch, out_dir: Path, returncode: int, writes: bool
) -> None:
    """Stand in for yt-dlp: optionally write a video, return `returncode`."""

    def fake_run(cmd, *args, **kwargs):
        if writes:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "video.mp4").write_bytes(VIDEO_BYTES)
        return _FakeResult(returncode)

    monkeypatch.setattr(download.subprocess, "run", fake_run)


class TestAFailedDownloadCannotBorrowAnEarlierRunsVideo:
    """`--out-dir` is documented and reused, so the directory is not empty."""

    def test_a_stale_video_is_not_mistaken_for_this_runs_download(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out_dir = tmp_path / "download"
        out_dir.mkdir()
        (out_dir / "video.mp4").write_bytes(b"yesterday's completely different film")
        _fake_ytdlp(monkeypatch, out_dir, returncode=1, writes=False)

        with pytest.raises(SystemExit) as exc:
            download.download_url("https://example.invalid/v", out_dir)

        # The message has to name the exit code, or the user is left guessing
        # which of the two failures they hit. Pinned as "(exit 1)", not as a bare
        # "1": the message interpolates `out_dir`, and pytest builds tmp_path
        # under a session counter (`/tmp/pytest-of-<user>/pytest-<N>/`), so a
        # lone digit is satisfied by the PATH on any run where N carries a 1 —
        # including runs where the code stopped naming the exit code at all.
        assert "(exit 1)" in str(exc.value)

    def test_a_stale_video_is_not_mistaken_for_a_successful_download_either(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The exit code alone is not the guard: yt-dlp can exit 0 having
        # downloaded nothing (every requested format filtered out, say), and the
        # stale file is just as wrong then.
        out_dir = tmp_path / "download"
        out_dir.mkdir()
        (out_dir / "video.mp4").write_bytes(b"yesterday's completely different film")
        _fake_ytdlp(monkeypatch, out_dir, returncode=0, writes=False)

        with pytest.raises(SystemExit):
            download.download_url("https://example.invalid/v", out_dir)

    def test_a_video_this_run_produced_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out_dir = tmp_path / "download"
        _fake_ytdlp(monkeypatch, out_dir, returncode=0, writes=True)

        result = download.download_url("https://example.invalid/v", out_dir)

        assert Path(result["video_path"]).read_bytes() == VIDEO_BYTES
        assert result["downloaded"] is True

    def test_a_partial_failure_still_succeeds_but_says_so(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # This is the case the original code was written for and must keep
        # working: a subtitle variant 429s, yt-dlp exits non-zero, the video is
        # fine. Proceeding is right. Proceeding SILENTLY is not — the transcript
        # in the report may be missing and nothing else would ever mention it.
        out_dir = tmp_path / "download"
        _fake_ytdlp(monkeypatch, out_dir, returncode=1, writes=True)

        result = download.download_url("https://example.invalid/v", out_dir)

        assert Path(result["video_path"]).read_bytes() == VIDEO_BYTES
        # "exited 1", not a bare "1" — same reason as the SystemExit above,
        # pre-emptively: nothing else in this line carries a digit today, but
        # the assertion should pin the code to the word it qualifies rather
        # than to whatever else the line grows.
        assert "exited 1" in capsys.readouterr().err


class TestEveryTimestampWebVTTAllowsIsParsed:
    """The hours component is optional and may exceed two digits."""

    def _write(self, tmp_path: Path, body: str) -> str:
        path = tmp_path / "subs.vtt"
        path.write_text("WEBVTT\n\n" + body, encoding="utf-8")
        return str(path)

    def test_the_shape_that_was_already_covered_still_works(self, tmp_path: Path) -> None:
        segments = transcribe.parse_vtt(
            self._write(tmp_path, "00:00:01.000 --> 00:00:03.000\nhello there\n")
        )
        assert [s["text"] for s in segments] == ["hello there"]

    def test_minutes_and_seconds_without_an_hours_field(self, tmp_path: Path) -> None:
        # Legal WebVTT, emitted by plenty of tools for anything under an hour.
        # It parsed to [] — and [] is what "this video has no captions" looks
        # like, so moviola uploaded the audio and paid for a transcript it
        # already had.
        segments = transcribe.parse_vtt(
            self._write(tmp_path, "00:01.000 --> 00:03.500\nhello there\n")
        )
        assert [s["text"] for s in segments] == ["hello there"]
        assert segments[0]["start"] == 1.0
        assert segments[0]["end"] == 3.5

    def test_hours_longer_than_two_digits(self, tmp_path: Path) -> None:
        segments = transcribe.parse_vtt(
            self._write(tmp_path, "100:00:01.000 --> 100:00:03.000\nlong stream\n")
        )
        assert [s["text"] for s in segments] == ["long stream"]
        assert segments[0]["start"] == 360001.0

    def test_a_comma_decimal_separator_still_works(self, tmp_path: Path) -> None:
        segments = transcribe.parse_vtt(
            self._write(tmp_path, "00:01,000 --> 00:03,500\nsrt-style\n")
        )
        assert [s["text"] for s in segments] == ["srt-style"]

    def test_cue_settings_after_the_timestamp_are_ignored(self, tmp_path: Path) -> None:
        segments = transcribe.parse_vtt(
            self._write(tmp_path, "00:01.000 --> 00:03.000 align:start position:0%\nhi\n")
        )
        assert [s["text"] for s in segments] == ["hi"]

    def test_a_subtitle_file_with_no_usable_cues_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # Zero segments is indistinguishable from "no captions exist" at every
        # call site, and the consequence of guessing wrong is a paid upload. If
        # a file was handed over and nothing came out of it, that is a fact the
        # user needs before the bill.
        segments = transcribe.parse_vtt(
            self._write(tmp_path, "this file is not a cue list at all\n")
        )
        assert segments == []
        assert "subs.vtt" in capsys.readouterr().err

    def test_an_ordinary_parse_says_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        transcribe.parse_vtt(self._write(tmp_path, "00:01.000 --> 00:03.000\nhi\n"))
        assert capsys.readouterr().err == ""


class TestFramesArePairedWithTheirOwnTimestamps:
    """ffmpeg stops zero-padding past the width `%04d` asks for."""

    def _lay_out(self, out_dir: Path, count: int, first: int = 1) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        for n in range(first, first + count):
            (out_dir / f"frame_{n:04d}.jpg").write_bytes(b"jpeg")

    def test_four_digit_names_are_in_numeric_order(self, tmp_path: Path) -> None:
        self._lay_out(tmp_path, 12)
        names = [p.name for p in frames.frames_in_order(tmp_path)]
        assert names[:3] == ["frame_0001.jpg", "frame_0002.jpg", "frame_0003.jpg"]
        assert names[-1] == "frame_0012.jpg"

    def test_five_digit_names_sort_after_four_digit_ones(self, tmp_path: Path) -> None:
        # The whole bug in one assertion: lexicographically frame_10000.jpg
        # lands between frame_1000.jpg and frame_1001.jpg, so from frame 10000
        # onwards every image carries somebody else's timestamp.
        self._lay_out(tmp_path, 3, first=1000)
        self._lay_out(tmp_path, 3, first=10000)
        names = [p.name for p in frames.frames_in_order(tmp_path)]
        assert names == [
            "frame_1000.jpg", "frame_1001.jpg", "frame_1002.jpg",
            "frame_10000.jpg", "frame_10001.jpg", "frame_10002.jpg",
        ]

    def test_the_scene_engine_pairs_the_ten_thousandth_frame_correctly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # End-to-end through the extractor, because the helper being right is
        # worth nothing if a call site keeps its own sorted(glob(...)).
        out_dir = tmp_path / "frames"
        numbers = [999, 1000, 9999, 10000, 10001]

        def fake_run(cmd, *args, **kwargs):
            out_dir.mkdir(parents=True, exist_ok=True)
            for n in numbers:
                (out_dir / f"frame_{n:04d}.jpg").write_bytes(b"jpeg")
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = ""
            result.stderr = "".join(
                f"[Parsed_showinfo_1 @ 0x0] n:{i} pts_time:{float(n)} \n"
                for i, n in enumerate(numbers)
            )
            return result

        monkeypatch.setattr(frames.subprocess, "run", fake_run)
        got, _untimed = frames.extract_scene_candidates("video.mp4", out_dir, resolution=512)

        assert [(Path(f["path"]).name, f["timestamp_seconds"]) for f in got] == [
            ("frame_0999.jpg", 999.0),
            ("frame_1000.jpg", 1000.0),
            ("frame_9999.jpg", 9999.0),
            ("frame_10000.jpg", 10000.0),
            ("frame_10001.jpg", 10001.0),
        ]

    def test_a_name_without_a_number_does_not_crash_the_sort(self, tmp_path: Path) -> None:
        # Nothing writes these, but the helper is a shared entry point and a
        # crash here would take down a run whose frames were all fine.
        #
        # This used to assert the stray was KEPT and sorted last, which was the
        # wrong contract: keeping it handed `pair_with_timestamps` one more file
        # than there were timestamps, so a file nobody here wrote produced a
        # "frames that remain may be misaligned" warning about ffmpeg. It is now
        # excluded and named on stderr — pinned in full in
        # `test_two_writers_one_directory.py`. The no-crash property, which is
        # what this test was for, is unchanged.
        self._lay_out(tmp_path, 2)
        (tmp_path / "frame_partial.jpg").write_bytes(b"jpeg")
        names = [p.name for p in frames.frames_in_order(tmp_path)]
        assert names == ["frame_0001.jpg", "frame_0002.jpg"]
