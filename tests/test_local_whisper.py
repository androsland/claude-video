"""Local (faster-whisper) backend: selection, runtime resolution, degradation.

Deliberately no test that actually transcribes: that would need a multi-hundred-MB
model download, so CI would either skip it or take minutes. What is covered is the
part that breaks silently — which backend gets chosen, and what happens on a
machine where faster-whisper or CUDA is absent.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import local_whisper
import whisper


class TestIsAvailable:
    def test_returns_a_bool(self):
        assert isinstance(local_whisper.is_available(), bool)

    def test_false_when_import_fails(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "faster_whisper":
                raise ImportError("no faster_whisper")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert local_whisper.is_available() is False


class TestResolveRuntime:
    def test_explicit_cpu_never_picks_cuda(self, monkeypatch):
        monkeypatch.delenv("WATCH_WHISPER_DEVICE", raising=False)
        monkeypatch.delenv("WATCH_WHISPER_COMPUTE", raising=False)
        device, compute = local_whisper.resolve_runtime(device="cpu")
        assert device == "cpu"
        assert compute == local_whisper.CPU_COMPUTE

    def test_explicit_compute_type_is_honoured(self, monkeypatch):
        monkeypatch.delenv("WATCH_WHISPER_DEVICE", raising=False)
        _, compute = local_whisper.resolve_runtime(device="cpu", compute_type="float32")
        assert compute == "float32"

    def test_env_vars_are_read(self, monkeypatch):
        monkeypatch.setenv("WATCH_WHISPER_DEVICE", "cpu")
        monkeypatch.setenv("WATCH_WHISPER_COMPUTE", "int8")
        assert local_whisper.resolve_runtime() == ("cpu", "int8")

    def test_falls_back_to_cpu_when_ctranslate2_missing(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "ctranslate2":
                raise ImportError("no ctranslate2")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        monkeypatch.delenv("WATCH_WHISPER_DEVICE", raising=False)
        monkeypatch.delenv("WATCH_WHISPER_COMPUTE", raising=False)
        device, compute = local_whisper.resolve_runtime()
        assert device == "cpu"
        assert compute == local_whisper.CPU_COMPUTE


class TestPreloadCudaLibs:
    def test_never_raises_and_returns_a_count(self):
        # Best-effort by contract: a machine with no nvidia wheels must get 0,
        # not an exception, because the CPU path depends on this not blowing up.
        assert local_whisper._preload_cuda_libs() >= 0


class TestVadProblemDetection:
    @pytest.mark.parametrize("message", [
        "onnxruntime is not installed",
        "Could not load the Silero VAD model",
        "vad_filter requires onnxruntime",
    ])
    def test_recognises_vad_failures(self, message):
        assert local_whisper._looks_like_vad_problem(RuntimeError(message)) is True

    @pytest.mark.parametrize("message", [
        "Library libcublas.so.12 is not found or cannot be loaded",
        "CUDA failed with error out of memory",
        "unsupported compute type",
    ])
    def test_does_not_swallow_real_failures(self, message):
        # Misclassifying these as VAD problems would retry without VAD, fail
        # identically, and report the wrong cause to the user.
        assert local_whisper._looks_like_vad_problem(RuntimeError(message)) is False


class TestMissingDependency:
    def test_transcribe_local_raises_systemexit_with_install_hint(self, monkeypatch, tmp_path):
        monkeypatch.setattr(local_whisper, "is_available", lambda: False)
        with pytest.raises(SystemExit) as exc:
            local_whisper.transcribe_local(tmp_path / "nope.mp3")
        assert "pip install faster-whisper" in str(exc.value)


class TestBackendResolution:
    def test_explicit_local_requires_the_package(self, monkeypatch):
        monkeypatch.setattr(whisper, "local_available", lambda: True)
        assert whisper.resolve_backend("local") == ("local", None)

        monkeypatch.setattr(whisper, "local_available", lambda: False)
        # (None, None) rather than ("local", None): the caller can then print one
        # actionable hint instead of failing part-way through a transcode.
        assert whisper.resolve_backend("local") == (None, None)

    def test_api_key_wins_over_local_when_unpinned(self, monkeypatch):
        monkeypatch.setattr(whisper, "load_api_key", lambda pref=None: ("groq", "k"))
        monkeypatch.setattr(whisper, "local_available", lambda: True)
        # Adding a local backend must not change what an existing key-holder gets.
        assert whisper.resolve_backend() == ("groq", "k")

    def test_local_is_the_fallback_when_no_key(self, monkeypatch):
        monkeypatch.setattr(whisper, "load_api_key", lambda pref=None: (None, None))
        monkeypatch.setattr(whisper, "local_available", lambda: True)
        assert whisper.resolve_backend() == ("local", None)

    def test_nothing_available_returns_none(self, monkeypatch):
        monkeypatch.setattr(whisper, "load_api_key", lambda pref=None: (None, None))
        monkeypatch.setattr(whisper, "local_available", lambda: False)
        assert whisper.resolve_backend() == (None, None)

    def test_explicit_api_backend_without_key_returns_none(self, monkeypatch):
        monkeypatch.setattr(whisper, "load_api_key", lambda pref=None: (None, None))
        monkeypatch.setattr(whisper, "local_available", lambda: True)
        # Asking for groq and silently getting local would be a surprise.
        assert whisper.resolve_backend("groq") == (None, None)


class TestLocalDispatch:
    def test_transcribe_video_skips_chunking_for_local(self, monkeypatch, tmp_path):
        """The 24 MB split exists for the upload cap; on-device there is no cap."""
        audio = tmp_path / "audio.mp3"

        def fake_extract(video_path, out_path, start=None, end=None):
            out_path.write_bytes(b"x" * (whisper.MAX_UPLOAD_BYTES + 1))
            return out_path

        monkeypatch.setattr(whisper, "extract_audio", fake_extract)
        monkeypatch.setattr(whisper, "split_audio", _must_not_be_called)
        monkeypatch.setattr(
            whisper, "_transcribe_local",
            lambda path, options: [{"start": 0.0, "end": 1.0, "text": "hi"}],
        )
        segments, backend = whisper.transcribe_video("v.mp4", audio, backend="local")
        assert backend == "local"
        assert segments == [{"start": 0.0, "end": 1.0, "text": "hi"}]

    def test_local_options_are_forwarded(self, monkeypatch, tmp_path):
        captured = {}

        monkeypatch.setattr(
            whisper, "extract_audio",
            lambda v, out, start=None, end=None: (out.write_bytes(b"x"), out)[1],
        )
        monkeypatch.setattr(local_whisper, "is_available", lambda: True)

        def fake_transcribe(path, model=None, device=None, compute_type=None, language=None):
            captured.update(model=model, device=device, compute_type=compute_type, language=language)
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]

        monkeypatch.setattr(local_whisper, "transcribe_local", fake_transcribe)
        whisper.transcribe_video(
            "v.mp4", tmp_path / "a.mp3", backend="local",
            options={"model": "small", "device": "cpu", "compute": "int8", "language": "de"},
        )
        assert captured == {
            "model": "small", "device": "cpu", "compute_type": "int8", "language": "de",
        }

    def test_blank_options_become_none(self, monkeypatch, tmp_path):
        """Unset config values are "" — they must not reach faster-whisper as ""."""
        captured = {}

        monkeypatch.setattr(
            whisper, "extract_audio",
            lambda v, out, start=None, end=None: (out.write_bytes(b"x"), out)[1],
        )
        monkeypatch.setattr(local_whisper, "is_available", lambda: True)

        def fake_transcribe(path, model=None, device=None, compute_type=None, language=None):
            captured.update(model=model, device=device, language=language)
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]

        monkeypatch.setattr(local_whisper, "transcribe_local", fake_transcribe)
        whisper.transcribe_video(
            "v.mp4", tmp_path / "a.mp3", backend="local",
            options={"model": "", "device": "", "compute": "", "language": ""},
        )
        assert captured == {"model": None, "device": None, "language": None}

    def test_local_needs_no_api_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr(whisper, "load_api_key", _must_not_be_called)
        monkeypatch.setattr(
            whisper, "extract_audio",
            lambda v, out, start=None, end=None: (out.write_bytes(b"x"), out)[1],
        )
        monkeypatch.setattr(
            whisper, "_transcribe_local",
            lambda path, options: [{"start": 0.0, "end": 1.0, "text": "hi"}],
        )
        _, backend = whisper.transcribe_video("v.mp4", tmp_path / "a.mp3", backend="local")
        assert backend == "local"

    def test_api_backend_without_key_still_errors(self, monkeypatch, tmp_path):
        monkeypatch.setattr(whisper, "load_api_key", lambda pref=None: (None, None))
        with pytest.raises(SystemExit) as exc:
            whisper.transcribe_video("v.mp4", tmp_path / "a.mp3", backend="groq")
        assert "--whisper local" in str(exc.value)


class TestFocusedExtraction:
    """--start/--end must clip the audio, not transcribe everything and discard."""

    def test_extract_audio_clips_to_range(self, tmp_path: Path):
        source = tmp_path / "tone.mp3"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
             "-ar", "16000", "-ac", "1", str(source)],
            check=True,
        )
        clipped = whisper.extract_audio(str(source), tmp_path / "clip.mp3", 5.0, 10.0)
        assert 4.0 < whisper.audio_duration(clipped) < 6.5

    def test_segments_are_shifted_back_onto_the_video_timeline(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            whisper, "extract_audio",
            lambda v, out, start=None, end=None: (out.write_bytes(b"x"), out)[1],
        )
        # Whisper sees the clip as starting at 0; the caller must see absolute time.
        monkeypatch.setattr(
            whisper, "_transcribe_local",
            lambda path, options: [{"start": 0.0, "end": 2.0, "text": "hi"}],
        )
        segments, _ = whisper.transcribe_video(
            "v.mp4", tmp_path / "a.mp3", backend="local", start_seconds=60.0, end_seconds=70.0,
        )
        assert segments == [{"start": 60.0, "end": 62.0, "text": "hi"}]

    def test_no_shift_without_a_start(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            whisper, "extract_audio",
            lambda v, out, start=None, end=None: (out.write_bytes(b"x"), out)[1],
        )
        monkeypatch.setattr(
            whisper, "_transcribe_local",
            lambda path, options: [{"start": 0.0, "end": 2.0, "text": "hi"}],
        )
        segments, _ = whisper.transcribe_video(
            "v.mp4", tmp_path / "a.mp3", backend="local", end_seconds=10.0,
        )
        assert segments == [{"start": 0.0, "end": 2.0, "text": "hi"}]


def _must_not_be_called(*args, **kwargs):
    raise AssertionError("should not be called")
