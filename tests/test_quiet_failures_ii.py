"""Round two of "a wrong answer is never a quiet answer".

`test_quiet_failures.py` closed four paths where moviola produced a confident,
well-formed result that was not true. The review that followed found more, and
this file pins them. Same standard as round one: the failure may still happen —
several of these are failures moviola is deliberately built to survive — but it
may not arrive silently, and it may not arrive wearing the shape of a success.

  * A partial transcript reached the report with nothing marking it partial.
    `transcribe_chunks` counted failures and used the count for exactly one
    thing: raising when it equalled the number of chunks. Nine chunks of ten
    succeeding returned the concatenation as an ordinary list of segments, and
    the report was then indistinguishable from a complete one. The only trace
    was a line on stderr — a channel a reader may not have and a summariser will
    not weigh. The gap is worst where it matters most: a dropped chunk is a HOLE
    in the middle of the timeline, so the transcript reads as CONTINUOUS across
    a span it never covered.

NON-GOALS, so a green run is not read as more than it is:

  * These pin what the second review found. They are not a survey, and nothing
    here proves there is no third round. Round one's file says the same thing
    and was right to.
  * **Skipping a failed chunk rather than failing the run is correct and is not
    changed here.** One bad slice discarding a whole transcript is the trade
    this was built to avoid. The fix is disclosure, not strictness — so a test
    that demanded a raise on any failure would be pinning the wrong behaviour.
  * Nothing here touches the single-file transcription path, which has no chunks
    and either succeeds or raises. It reports one chunk, zero failed, and that
    is not a disclosure — it is the absence of one.
  * The gap ranges are the ranges moviola ASKED ffmpeg for. If a chunk file's
    real audio does not span what the plan said, that discrepancy is invisible
    from here.
  * This says nothing about whether the surviving segments are themselves
    correct. A chunk that succeeds and returns nonsense is a different problem
    with no signal to fire on.
  * One link of the chain is pinned NEXT DOOR, not here: that `split_audio`
    carries each chunk's duration at all is asserted in
    `test_whisper.py::TestSplitAudio::test_returns_plan_offsets`, where
    `split_audio`'s contract lives. Reverting it survives this file and dies
    there. Said out loud because a KILL run scoped to this module alone reports
    5 of 6, and the missing one is a scoping decision rather than a hole.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import moviola
import whisper


# --------------------------------------------------------------------------
# A partial transcript is marked partial.
# --------------------------------------------------------------------------


def _chunks(*specs: tuple[str, float, float]) -> list:
    """Build the chunk list `transcribe_chunks` consumes, by name/offset/duration."""
    return [whisper.AudioChunk(Path(name), offset, duration)
            for name, offset, duration in specs]


class TestAPartialTranscriptSaysSo:
    """A dropped chunk is a hole in the middle, not a short ending.

    The danger is not that text is missing — it is that the text either side of
    the hole closes over it seamlessly, so the report reads as a continuous
    account of a span it never covered.
    """

    def test_a_failed_chunk_is_reported_as_a_missing_range(self) -> None:
        chunks = _chunks(("a.mp3", 0.0, 100.0), ("b.mp3", 100.0, 100.0))

        def flaky(path: Path) -> list[dict]:
            if path.stem == "b":
                raise SystemExit("chunk b failed")
            return [{"start": 1.0, "end": 2.0, "text": "a"}]

        result = whisper.transcribe_chunks(chunks, flaky)

        assert result.segments == [{"start": 1.0, "end": 2.0, "text": "a"}]
        assert result.gaps.ranges == [(100.0, 200.0)]
        assert result.gaps.failed == 1
        assert result.gaps.total == 2

    def test_the_range_comes_from_the_chunk_that_failed_not_the_run(self) -> None:
        """A gap labelled with the START of the range is the finding, not the fix.

        Substituting a plausible number in the right units is exactly how the
        original defect read as ordinary output. The gap must name the span the
        failed chunk covered.
        """
        chunks = _chunks(
            ("a.mp3", 0.0, 60.0), ("b.mp3", 60.0, 60.0), ("c.mp3", 120.0, 45.5)
        )

        def only_c_fails(path: Path) -> list[dict]:
            if path.stem == "c":
                raise SystemExit("boom")
            return [{"start": 0.0, "end": 1.0, "text": path.stem}]

        result = whisper.transcribe_chunks(chunks, only_c_fails)

        assert result.gaps.ranges == [(120.0, 165.5)]

    def test_several_failures_are_all_named(self) -> None:
        chunks = _chunks(
            ("a.mp3", 0.0, 10.0), ("b.mp3", 10.0, 10.0), ("c.mp3", 20.0, 10.0)
        )

        def only_b_survives(path: Path) -> list[dict]:
            if path.stem == "b":
                return [{"start": 0.0, "end": 1.0, "text": "b"}]
            raise SystemExit("boom")

        result = whisper.transcribe_chunks(chunks, only_b_survives)

        assert result.gaps.ranges == [(0.0, 10.0), (20.0, 30.0)]
        assert result.gaps.failed == 2

    def test_a_complete_transcript_reports_no_gaps(self) -> None:
        """The must-NOT-fire half. Every legitimate run goes through here."""
        chunks = _chunks(("a.mp3", 0.0, 100.0), ("b.mp3", 100.0, 100.0))

        def fine(path: Path) -> list[dict]:
            return [{"start": 0.0, "end": 2.0, "text": path.stem}]

        result = whisper.transcribe_chunks(chunks, fine)

        assert result.gaps.ranges == []
        assert result.gaps.failed == 0
        assert result.gaps.total == 2

    def test_every_chunk_failing_still_raises(self) -> None:
        """Unchanged behaviour, pinned so the disclosure work does not soften it."""
        chunks = _chunks(("a.mp3", 0.0, 10.0), ("b.mp3", 10.0, 10.0))

        with pytest.raises(SystemExit):
            whisper.transcribe_chunks(chunks, _boom)


def _boom(path: Path) -> list[dict]:
    raise SystemExit("boom")


class TestTheGapsReachTheReport:
    """Threading them out of `transcribe_chunks` is only half the fix.

    The finding was that stdout could not be told apart from a complete run.
    A gap the report never prints closes nothing.
    """

    def _report(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        clip: Path,
        gaps: object,
    ) -> str:
        segments = [{"start": 0.0, "end": 1.0, "text": "hello"}]

        def fake_transcribe(*a: object, **k: object) -> tuple:
            return segments, "groq", gaps

        monkeypatch.setattr(moviola, "resolve_backend", lambda pref=None: ("groq", "k"))
        monkeypatch.setattr(moviola, "transcribe_video", fake_transcribe)
        monkeypatch.setattr(sys, "argv", ["moviola.py", str(clip), "--detail", "efficient"])
        assert moviola.main() == 0
        return capsys.readouterr().out

    def test_the_report_names_the_missing_span(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        audio_clip: Path,
    ) -> None:
        gaps = whisper.TranscriptGaps(ranges=[(200.0, 300.0)], failed=1, total=4)
        out = self._report(monkeypatch, capsys, audio_clip, gaps)

        assert "INCOMPLETE" in out
        assert "1 of 4" in out
        # Rendered as a timestamp, not raw seconds — every other time in the
        # report is, and a lone float reads as a segment index.
        assert "3:20" in out and "5:00" in out

    def test_the_report_says_the_text_closes_over_the_hole(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        audio_clip: Path,
    ) -> None:
        """The specific misreading this exists to prevent, stated in the report."""
        gaps = whisper.TranscriptGaps(ranges=[(10.0, 20.0)], failed=1, total=3)
        out = self._report(monkeypatch, capsys, audio_clip, gaps)

        assert "continuous" in out.lower()

    def test_a_complete_run_says_nothing_about_gaps(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        audio_clip: Path,
    ) -> None:
        """The must-NOT-fire half: no warning on the path that always runs."""
        gaps = whisper.TranscriptGaps(ranges=[], failed=0, total=1)
        out = self._report(monkeypatch, capsys, audio_clip, gaps)

        assert "INCOMPLETE" not in out
        assert "missing" not in out.lower()


class TestTheGapsMoveWithTheTimeline:
    """A `--start` run transcribes a clip that begins at zero, then shifts.

    The segments are already put back on the video's timeline. A gap left on
    the clip's timeline would name a span that exists in the report and is
    fully transcribed — a confident wrong answer, which is worse than the
    silence this whole file exists to end.
    """

    def _run(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, start: float):
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x" * (whisper.MAX_UPLOAD_BYTES * 2 + 1))
        chunks = [
            whisper.AudioChunk(tmp_path / "c0.mp3", 0.0, 60.0),
            whisper.AudioChunk(tmp_path / "c1.mp3", 60.0, 60.0),
        ]

        def one_fails(_backend, _key, path: Path) -> list[dict]:
            if path.name == "c1.mp3":
                raise SystemExit("boom")
            return [{"start": 0.0, "end": 1.0, "text": "a"}]

        monkeypatch.setattr(whisper, "extract_audio", lambda *a, **k: audio)
        monkeypatch.setattr(whisper, "audio_duration", lambda *a, **k: 120.0)
        monkeypatch.setattr(whisper, "split_audio", lambda *a, **k: chunks)
        monkeypatch.setattr(whisper, "cleanup_chunks", lambda *a, **k: None)
        monkeypatch.setattr(whisper, "_transcribe_file", one_fails)
        _segments, _backend, gaps = whisper.transcribe_video(
            "v.mp4",
            tmp_path / "out.mp3",
            backend="groq",
            api_key="placeholder-value-not-a-credential",
            start_seconds=start,
        )
        return gaps

    def test_a_start_offset_moves_the_gap_too(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        gaps = self._run(monkeypatch, tmp_path, start=300.0)
        assert gaps.ranges == [(360.0, 420.0)]

    def test_without_a_start_the_gap_is_untouched(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The must-NOT-fire half: no shift where there is no offset."""
        gaps = self._run(monkeypatch, tmp_path, start=0.0)
        assert gaps.ranges == [(60.0, 120.0)]
