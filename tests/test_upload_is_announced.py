"""No audio leaves this machine without stderr having said so first.

`_announce_upload` was already unit-tested — five tests in test_whisper_api.py
call it directly and check its arithmetic. All five pass with BOTH of its call
sites in `transcribe_video` deleted, which is exactly the mutation that survived
the review: the notice was proven correct and never proven to be printed.

These tests drive the real `transcribe_video` and snapshot stderr AT THE MOMENT
the first upload is attempted, so the assertion is ordering as well as presence.
A notice printed after the request has already gone out is not consent, it is a
receipt.

NON-GOALS, so a green run is not read as more than it is:

  * This proves the sentence is WRITTEN to stderr before the request. It cannot
    prove anyone reads it — an agent harness may swallow stderr entirely, and
    nothing here can see that.
  * It does not re-check the numbers in the notice. test_whisper_api owns the
    size/minutes/request-count arithmetic; this file owns whether the call
    happens at all on the path a user actually takes.
  * It covers the audio path only. yt-dlp's own network traffic during download,
    and the frame path's spend notices, are elsewhere and are not pinned here.
  * It says nothing about the local backend beyond "it does not announce an
    upload" — that backend makes no request, so there is nothing to announce.

Every value written below is inert filler. Nothing here reads a real credential.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

import whisper

FILLER = "placeholder-value-not-a-credential"


def _audio_file(tmp_path: Path, size: int) -> Path:
    """A sparse file of exactly `size` bytes — only its st_size is ever read."""
    path = tmp_path / "audio.mp3"
    with path.open("wb") as handle:
        handle.truncate(size)
    return path


class _Recorder:
    """Stands in for the network call and snapshots stderr as it is entered."""

    def __init__(self, buffer: io.StringIO) -> None:
        self.buffer = buffer
        self.stderr_at_each_call: list[str] = []

    def __call__(self, *args, **kwargs) -> list[dict]:
        self.stderr_at_each_call.append(self.buffer.getvalue())
        return [{"start": 0.0, "end": 1.0, "text": "filler"}]


def _capture_stderr(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Redirect sys.stderr into a buffer we can read mid-run.

    Deliberately a plain helper called from inside each test body, NOT a
    fixture. pytest re-assigns `sys.stderr` when it resumes global capture at
    the setup→call transition, so the same monkeypatch applied in a fixture is
    silently clobbered before the test runs and every buffer reads empty.
    capsys is not a substitute here: `readouterr()` empties what it returns, and
    these tests need to read stderr partway through without disturbing it.
    """
    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buffer)
    return buffer


class TestTheUploadIsAnnouncedBeforeItHappens:
    def test_a_single_request_upload_says_so_before_the_request(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        stderr_buffer = _capture_stderr(monkeypatch)
        audio = _audio_file(tmp_path, 2 * 1024 * 1024)
        recorder = _Recorder(stderr_buffer)
        monkeypatch.setattr(whisper, "extract_audio", lambda *a, **k: audio)
        monkeypatch.setattr(whisper, "_transcribe_file", recorder)

        whisper.transcribe_video(
            "video.mp4", tmp_path / "out.mp3", backend="groq", api_key=FILLER
        )

        assert len(recorder.stderr_at_each_call) == 1
        assert "uploading to api.groq.com in 1 request" in recorder.stderr_at_each_call[0]

    def test_a_chunked_upload_announces_every_request_before_the_first_one(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        stderr_buffer = _capture_stderr(monkeypatch)
        # Three chunks' worth. The point of announcing up front is that this is
        # three billed requests, not one, and the user finds that out before any
        # of them rather than after all three.
        audio = _audio_file(tmp_path, whisper.MAX_UPLOAD_BYTES * 2 + 1)
        chunks = [(tmp_path / f"chunk{i}.mp3", float(i * 60)) for i in range(3)]
        recorder = _Recorder(stderr_buffer)
        monkeypatch.setattr(whisper, "extract_audio", lambda *a, **k: audio)
        monkeypatch.setattr(whisper, "audio_duration", lambda *a, **k: 180.0)
        monkeypatch.setattr(whisper, "split_audio", lambda *a, **k: chunks)
        monkeypatch.setattr(whisper, "_transcribe_file", recorder)

        whisper.transcribe_video(
            "video.mp4", tmp_path / "out.mp3", backend="openai", api_key=FILLER
        )

        assert len(recorder.stderr_at_each_call) == 3
        assert "uploading to api.openai.com in 3 requests" in recorder.stderr_at_each_call[0]

    def test_an_hours_long_upload_carries_the_cost_warning_before_the_request(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        stderr_buffer = _capture_stderr(monkeypatch)
        minutes = whisper.COST_WARN_MINUTES + 1
        audio = _audio_file(tmp_path, int(whisper.AUDIO_BYTES_PER_MINUTE * minutes))
        chunks = [(tmp_path / "chunk0.mp3", 0.0)]
        recorder = _Recorder(stderr_buffer)
        monkeypatch.setattr(whisper, "extract_audio", lambda *a, **k: audio)
        monkeypatch.setattr(whisper, "audio_duration", lambda *a, **k: minutes * 60.0)
        monkeypatch.setattr(whisper, "split_audio", lambda *a, **k: chunks)
        monkeypatch.setattr(whisper, "_transcribe_file", recorder)

        whisper.transcribe_video(
            "video.mp4", tmp_path / "out.mp3", backend="groq", api_key=FILLER
        )

        assert "bills per minute of it" in recorder.stderr_at_each_call[0]

    def test_the_local_backend_announces_no_upload(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        stderr_buffer = _capture_stderr(monkeypatch)
        audio = _audio_file(tmp_path, 2 * 1024 * 1024)
        monkeypatch.setattr(whisper, "extract_audio", lambda *a, **k: audio)
        monkeypatch.setattr(
            whisper,
            "_transcribe_local",
            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "filler"}],
        )

        whisper.transcribe_video("video.mp4", tmp_path / "out.mp3", backend="local")

        assert "uploading to" not in stderr_buffer.getvalue()
        assert "transcribing on-device" in stderr_buffer.getvalue()
