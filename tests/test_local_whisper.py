"""Local (faster-whisper) backend: selection, runtime resolution, degradation.

Deliberately no test that actually transcribes: that would need a multi-hundred-MB
model download, so CI would either skip it or take minutes. What is covered is the
part that breaks silently — which backend gets chosen, and what happens on a
machine where faster-whisper or CUDA is absent.
"""
from __future__ import annotations

import gc
import subprocess
import weakref
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
        monkeypatch.delenv("MOVIOLA_WHISPER_DEVICE", raising=False)
        monkeypatch.delenv("MOVIOLA_WHISPER_COMPUTE", raising=False)
        device, compute = local_whisper.resolve_runtime(device="cpu")
        assert device == "cpu"
        assert compute == local_whisper.CPU_COMPUTE

    def test_explicit_compute_type_is_honoured(self, monkeypatch):
        monkeypatch.delenv("MOVIOLA_WHISPER_DEVICE", raising=False)
        _, compute = local_whisper.resolve_runtime(device="cpu", compute_type="float32")
        assert compute == "float32"

    def test_env_vars_are_read(self, monkeypatch):
        monkeypatch.setenv("MOVIOLA_WHISPER_DEVICE", "cpu")
        monkeypatch.setenv("MOVIOLA_WHISPER_COMPUTE", "int8")
        assert local_whisper.resolve_runtime() == ("cpu", "int8")

    @pytest.mark.parametrize("value", ["cuda:0", "gpu", "mps", "CUDA:1", "metal"])
    def test_unknown_devices_are_rejected_not_silently_demoted(self, value, monkeypatch):
        # Each of these used to fall through every branch and land on "cpu" with
        # nothing printed, so asking for a GPU got a CPU transcode several times
        # slower and the user had no way to tell.
        monkeypatch.setenv("MOVIOLA_WHISPER_DEVICE", value)
        with pytest.raises(SystemExit, match="Unknown Whisper device"):
            local_whisper.resolve_runtime()

    def test_a_blank_device_still_means_auto(self, monkeypatch):
        # Blank-but-present is what a secret sync or a half-filled .env produces;
        # it must read as "unset", not as an unknown device.
        monkeypatch.setenv("MOVIOLA_WHISPER_DEVICE", "   ")
        monkeypatch.delenv("MOVIOLA_WHISPER_COMPUTE", raising=False)
        device, _ = local_whisper.resolve_runtime()
        assert device in ("cpu", "cuda")

    def test_case_and_padding_are_tolerated(self, monkeypatch):
        monkeypatch.setenv("MOVIOLA_WHISPER_DEVICE", "  CPU  ")
        monkeypatch.delenv("MOVIOLA_WHISPER_COMPUTE", raising=False)
        assert local_whisper.resolve_runtime() == ("cpu", local_whisper.CPU_COMPUTE)

    def test_falls_back_to_cpu_when_ctranslate2_missing(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "ctranslate2":
                raise ImportError("no ctranslate2")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        monkeypatch.delenv("MOVIOLA_WHISPER_DEVICE", raising=False)
        monkeypatch.delenv("MOVIOLA_WHISPER_COMPUTE", raising=False)
        device, compute = local_whisper.resolve_runtime()
        assert device == "cpu"
        assert compute == local_whisper.CPU_COMPUTE


class TestPreloadCudaLibs:
    def test_never_raises_and_returns_a_count(self):
        # Best-effort by contract: a machine with no nvidia wheels must get 0,
        # not an exception, because the CPU path depends on this not blowing up.
        assert local_whisper._preload_cuda_libs() >= 0

    def test_survives_a_site_module_without_getsitepackages(self, monkeypatch):
        # Some virtualenv-provided `site` modules have no getsitepackages(), and
        # the AttributeError is not a SystemExit — it escaped transcribe_local()
        # and crashed the run instead of falling back to CPU.
        def boom():
            raise AttributeError("module 'site' has no attribute 'getsitepackages'")

        monkeypatch.setattr(local_whisper.site, "getsitepackages", boom)
        assert local_whisper._preload_cuda_libs() >= 0

    def test_survives_both_probes_failing(self, monkeypatch):
        def boom():
            raise AttributeError("nope")

        monkeypatch.setattr(local_whisper.site, "getsitepackages", boom)
        monkeypatch.setattr(local_whisper.site, "getusersitepackages", boom)
        assert local_whisper._preload_cuda_libs() == 0

    def test_alt_builds_are_skipped(self, tmp_path, monkeypatch):
        """libnvrtc.alt.so.12 is a second build of libnvrtc.so.12.

        Loading both RTLD_GLOBAL puts two definitions of the same symbols into
        one namespace and lets glob order pick the winner. The .alt build is
        also 113 MB, so skipping it is free in both directions.
        """
        lib = tmp_path / "nvidia" / "cuda_nvrtc" / "lib"
        lib.mkdir(parents=True)
        for name in ("libnvrtc.so.12", "libnvrtc.alt.so.12",
                     "libnvrtc-builtins.alt.so.12.9"):
            (lib / name).write_bytes(b"")

        seen = []
        monkeypatch.setattr(local_whisper.site, "getsitepackages", lambda: [str(tmp_path)])
        monkeypatch.setattr(local_whisper.site, "getusersitepackages", lambda: [])

        def fake_cdll(path, mode=0):
            seen.append(Path(path).name)
            return object()

        monkeypatch.setattr(local_whisper.ctypes, "CDLL", fake_cdll)
        local_whisper._preload_cuda_libs()
        assert seen == ["libnvrtc.so.12"]


class TestCpuThreads:
    def test_a_pin_wins_over_everything(self, monkeypatch):
        monkeypatch.setenv("MOVIOLA_WHISPER_CPU_THREADS", "3")
        monkeypatch.setenv("OMP_NUM_THREADS", "9")
        assert local_whisper.cpu_threads() == 3

    def test_a_non_numeric_pin_is_an_error_not_a_silent_default(self, monkeypatch):
        # Falling back to the default here would be the same class of bug as the
        # device string that used to demote to CPU without saying so.
        monkeypatch.setenv("MOVIOLA_WHISPER_CPU_THREADS", "lots")
        with pytest.raises(SystemExit) as exc:
            local_whisper.cpu_threads()
        assert "MOVIOLA_WHISPER_CPU_THREADS" in str(exc.value)

    def test_a_preset_omp_num_threads_is_left_alone(self, monkeypatch):
        # CTranslate2 honours OMP_NUM_THREADS only while cpu_threads is 0, so
        # returning a number here would silently override a deliberate setting.
        monkeypatch.delenv("MOVIOLA_WHISPER_CPU_THREADS", raising=False)
        monkeypatch.setenv("OMP_NUM_THREADS", "2")
        assert local_whisper.cpu_threads() == 0

    def test_falls_back_to_the_library_default_when_cores_are_unreadable(self, monkeypatch):
        monkeypatch.delenv("MOVIOLA_WHISPER_CPU_THREADS", raising=False)
        monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
        monkeypatch.setattr(local_whisper, "_physical_cores", lambda: 0)
        assert local_whisper.cpu_threads() == 0

    def test_physical_cores_never_exceed_the_affinity_mask(self, monkeypatch):
        # A taskset-pinned process reads every core in /proc/cpuinfo and may use
        # two of them. Reporting the file's answer would oversubscribe by 6x.
        monkeypatch.setattr(local_whisper.os, "sched_getaffinity", lambda pid: {0, 1})
        assert local_whisper._physical_cores() <= 2

    def test_physical_cores_are_not_logical_cores(self):
        # The measured point of the whole exercise: on an SMT machine the right
        # answer is below os.cpu_count(), and on a non-SMT one it equals it.
        cores = local_whisper._physical_cores()
        assert 0 < cores <= (local_whisper.os.cpu_count() or 1)


class TestOfflineMode:
    @pytest.mark.parametrize("name", ["MOVIOLA_WHISPER_OFFLINE", "HF_HUB_OFFLINE"])
    def test_either_switch_turns_it_on(self, monkeypatch, name):
        monkeypatch.delenv("MOVIOLA_WHISPER_OFFLINE", raising=False)
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
        monkeypatch.setenv(name, "1")
        assert local_whisper.offline() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "  ", ""])
    def test_off_values_and_blanks_stay_off(self, monkeypatch, value):
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
        monkeypatch.setenv("MOVIOLA_WHISPER_OFFLINE", value)
        assert local_whisper.offline() is False


class TestCpuComputeLadder:
    def test_int8_is_used_when_supported(self, monkeypatch):
        monkeypatch.setattr(local_whisper, "_best_cpu_compute", lambda: "int8")
        assert local_whisper.resolve_runtime("cpu") == ("cpu", "int8")

    def test_falls_through_to_a_supported_type(self, monkeypatch):
        # The CPU branch used to return int8 unconditionally while the CUDA
        # branch probed — and nothing retries after a CPU load fails, so an
        # int8-less CPU had no second chance.
        import sys as _sys
        import types

        fake = types.ModuleType("ctranslate2")
        fake.get_supported_compute_types = lambda device: {"float32"}
        monkeypatch.setitem(_sys.modules, "ctranslate2", fake)
        assert local_whisper._best_cpu_compute() == "float32"

    def test_keeps_int8_when_the_probe_cannot_run(self, monkeypatch):
        import sys as _sys

        monkeypatch.setitem(_sys.modules, "ctranslate2", None)
        assert local_whisper._best_cpu_compute() == "int8"


class TestImportErrorReporting:
    def test_the_real_import_failure_reaches_the_user(self, monkeypatch, tmp_path):
        """"Not installed" is only one reason is_available() says no.

        A broken CTranslate2 or a numpy ABI mismatch lands here too, and telling
        that user to pip install faster-whisper sends them to reinstall a package
        that is already there.
        """
        monkeypatch.setattr(local_whisper, "is_available", lambda: False)
        monkeypatch.setattr(
            local_whisper, "import_error",
            lambda: "ImportError: numpy.core.multiarray failed to import",
        )
        with pytest.raises(SystemExit) as exc:
            local_whisper.transcribe_local(tmp_path / "a.mp3")
        assert "numpy.core.multiarray failed to import" in str(exc.value)

    def test_a_real_import_failure_is_captured_verbatim(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def boom(name, *args, **kwargs):
            if name == "faster_whisper":
                raise OSError("libstdc++.so.6: version GLIBCXX_3.4.32 not found")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", boom)
        assert local_whisper.is_available() is False
        assert "GLIBCXX_3.4.32" in local_whisper.import_error()

    def test_a_successful_import_clears_the_last_error(self, monkeypatch):
        # Otherwise a stale message from an earlier probe would be reported
        # alongside a backend that is working fine.
        local_whisper._IMPORT_ERROR = "ImportError: stale"
        if local_whisper.is_available():
            assert local_whisper.import_error() == ""


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
        assert "pip install \"faster-whisper>=1.0\"" in str(exc.value)


class TestBackendResolution:
    def test_explicit_local_requires_the_package(self, monkeypatch):
        monkeypatch.setattr(whisper, "local_available", lambda: True)
        assert whisper.resolve_backend("local") == ("local", None)

        monkeypatch.setattr(whisper, "local_available", lambda: False)
        # (None, None) rather than ("local", None): the caller can then print one
        # actionable hint instead of failing part-way through a transcode.
        assert whisper.resolve_backend("local") == (None, None)

    def test_local_wins_over_an_api_key_when_unpinned(self, monkeypatch):
        monkeypatch.setattr(whisper, "load_api_key", lambda pref=None: ("groq", "k"))
        monkeypatch.setattr(whisper, "local_available", lambda: True)
        # A key present in the environment for some unrelated tool must not
        # cause an upload. Local-first is the reason this fork exists.
        assert whisper.resolve_backend() == ("local", None)

    def test_an_api_key_is_the_fallback_when_local_is_absent(self, monkeypatch):
        monkeypatch.setattr(whisper, "load_api_key", lambda pref=None: ("groq", "k"))
        monkeypatch.setattr(whisper, "local_available", lambda: False)
        assert whisper.resolve_backend() == ("groq", "k")

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

        def fake_transcribe(path, model=None, device=None, compute_type=None,
                            language=None, offline_mode=None):
            captured.update(model=model, device=device, compute_type=compute_type,
                            language=language, offline_mode=offline_mode)
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]

        monkeypatch.setattr(local_whisper, "transcribe_local", fake_transcribe)
        whisper.transcribe_video(
            "v.mp4", tmp_path / "a.mp3", backend="local",
            options={"model": "small", "device": "cpu", "compute": "int8",
                     "language": "de", "offline": True},
        )
        assert captured == {
            "model": "small", "device": "cpu", "compute_type": "int8",
            "language": "de", "offline_mode": True,
        }

    def test_an_explicit_offline_false_is_not_collapsed_to_none(self, monkeypatch, tmp_path):
        """`or None` here would turn "the user said no" into "ask the environment".

        With HF_HUB_OFFLINE=1 set for some other tool, that difference is the
        difference between honouring MOVIOLA_WHISPER_OFFLINE=0 and ignoring it.
        """
        captured = {}

        monkeypatch.setattr(
            whisper, "extract_audio",
            lambda v, out, start=None, end=None: (out.write_bytes(b"x"), out)[1],
        )
        monkeypatch.setattr(local_whisper, "is_available", lambda: True)

        def fake_transcribe(path, offline_mode=None, **kw):
            captured["offline_mode"] = offline_mode
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]

        monkeypatch.setattr(local_whisper, "transcribe_local", fake_transcribe)
        whisper.transcribe_video(
            "v.mp4", tmp_path / "a.mp3", backend="local", options={"offline": False},
        )
        assert captured["offline_mode"] is False

    def test_blank_options_become_none(self, monkeypatch, tmp_path):
        """Unset config values are "" — they must not reach faster-whisper as ""."""
        captured = {}

        monkeypatch.setattr(
            whisper, "extract_audio",
            lambda v, out, start=None, end=None: (out.write_bytes(b"x"), out)[1],
        )
        monkeypatch.setattr(local_whisper, "is_available", lambda: True)

        def fake_transcribe(path, model=None, device=None, compute_type=None,
                            language=None, offline_mode=None):
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

    def test_a_range_past_the_end_of_the_video_is_an_error_not_silence(self, tmp_path: Path):
        """ffmpeg exits 0 and writes a valid, header-only mp3 for an out-of-range
        seek — measured at 333 bytes — so a `st_size == 0` check passes it through
        and Whisper transcribes silence into an empty transcript with no
        explanation. The message has to name the range, since that is the input
        the user got wrong."""
        source = tmp_path / "tone.mp3"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
             "-ar", "16000", "-ac", "1", str(source)],
            check=True,
        )
        with pytest.raises(SystemExit, match="no audio for the requested range"):
            whisper.extract_audio(str(source), tmp_path / "clip.mp3", 999.0, 1009.0)

    def test_the_out_of_range_message_names_the_range(self, tmp_path: Path):
        source = tmp_path / "tone.mp3"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
             "-ar", "16000", "-ac", "1", str(source)],
            check=True,
        )
        with pytest.raises(SystemExit, match=r"999\.0s.*1009\.0s"):
            whisper.extract_audio(str(source), tmp_path / "clip.mp3", 999.0, 1009.0)

    def test_an_in_range_clip_is_not_caught_by_the_floor(self, tmp_path: Path):
        # The guard must not fire on a legitimately short clip. One second at
        # 64 kbps mono is ~8 kB, four times the floor.
        source = tmp_path / "tone.mp3"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
             "-ar", "16000", "-ac", "1", str(source)],
            check=True,
        )
        clipped = whisper.extract_audio(str(source), tmp_path / "clip.mp3", 1.0, 2.0)
        assert clipped.stat().st_size > whisper.MIN_AUDIO_BYTES

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


class _FakeSegment:
    """Shaped like faster-whisper's Segment: .start / .end / .text attributes."""

    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


class _FakeInfo:
    def __init__(self, duration=0.0, language=None):
        self.duration = duration
        self.language = language


class _FakeModel:
    """Stands in for a loaded WhisperModel. transcribe() returns (generator, info).

    Two failure modes, and the difference is the whole point: `raises` fires at
    the transcribe() call, while `raises_mid_drain` fires after some segments
    have already been yielded. CTranslate2 resolves its CUDA libraries lazily,
    so a broken GPU install produces the second shape, not the first — a test
    that only simulates the first would pass against a retry that wrapped the
    load alone.
    """

    def __init__(self, segments, info=None, raises=None, raises_mid_drain=None):
        self._segments = segments
        self._info = info or _FakeInfo()
        self._raises = raises
        self._raises_mid_drain = raises_mid_drain
        self.calls = []

    def transcribe(self, path, language=None, vad_filter=False):
        self.calls.append({"path": path, "language": language, "vad_filter": vad_filter})
        if self._raises is not None:
            raise self._raises

        def gen():
            yield from self._segments
            if self._raises_mid_drain is not None:
                raise self._raises_mid_drain

        return gen(), self._info


def _progress_lines(capsys) -> list[str]:
    """Only the percentage lines — not the detected-language line, which shares
    the prefix up to the word 'whisper'."""
    return [ln for ln in capsys.readouterr().err.splitlines() if "local whisper:" in ln]


class TestCollectSegmentShape:
    """_collect() converts faster-whisper's objects into the dict shape the rest
    of the pipeline consumes. Nothing else in the suite touches this attribute
    contract, so a renamed field upstream would otherwise pass CI silently."""

    def test_produces_start_end_text_dicts(self, tmp_path):
        model = _FakeModel([_FakeSegment(0.0, 1.5, "hello"), _FakeSegment(1.5, 3.0, "world")])
        out = local_whisper._collect(model, tmp_path / "a.mp3", None, vad=True)
        assert out == [
            {"start": 0.0, "end": 1.5, "text": "hello"},
            {"start": 1.5, "end": 3.0, "text": "world"},
        ]

    def test_rounds_to_two_decimals(self, tmp_path):
        model = _FakeModel([_FakeSegment(0.123456, 1.987654, "x")])
        out = local_whisper._collect(model, tmp_path / "a.mp3", None, vad=True)
        assert out == [{"start": 0.12, "end": 1.99, "text": "x"}]

    def test_strips_and_drops_empty_text(self, tmp_path):
        model = _FakeModel([
            _FakeSegment(0.0, 1.0, "  padded  "),
            _FakeSegment(1.0, 2.0, "   "),
            _FakeSegment(2.0, 3.0, ""),
            _FakeSegment(3.0, 4.0, None),
        ])
        out = local_whisper._collect(model, tmp_path / "a.mp3", None, vad=True)
        assert out == [{"start": 0.0, "end": 1.0, "text": "padded"}]

    def test_tolerates_none_offsets(self, tmp_path):
        model = _FakeModel([_FakeSegment(None, None, "t")])
        out = local_whisper._collect(model, tmp_path / "a.mp3", None, vad=True)
        assert out == [{"start": 0.0, "end": 0.0, "text": "t"}]

    def test_passes_language_and_vad_through(self, tmp_path):
        model = _FakeModel([_FakeSegment(0.0, 1.0, "t")])
        local_whisper._collect(model, tmp_path / "a.mp3", "el", vad=False)
        assert model.calls == [{"path": str(tmp_path / "a.mp3"), "language": "el", "vad_filter": False}]

    def test_reports_progress_for_a_long_clip(self, tmp_path, capsys):
        segments = [_FakeSegment(float(i * 60), float((i + 1) * 60), f"s{i}") for i in range(3)]
        model = _FakeModel(segments, info=_FakeInfo(duration=180.0))
        local_whisper._collect(model, tmp_path / "a.mp3", None, vad=True)
        assert _progress_lines(capsys) == [
            "[moviola] local whisper: 33% (60s/180s)",
            "[moviola] local whisper: 66% (120s/180s)",
            "[moviola] local whisper: 100% (180s/180s)",
        ]

    def test_progress_marks_advance_past_a_long_segment(self, tmp_path, capsys):
        """One segment spanning five minutes prints once, not once per minute,
        and the next mark lands past its end so the segment right after it stays
        quiet. Without the catch-up loop the mark would still be at 120s and
        that following segment would print a second time."""
        model = _FakeModel(
            [
                _FakeSegment(0.0, 300.0, "one long stretch"),
                _FakeSegment(300.0, 320.0, "just after"),
                _FakeSegment(320.0, 400.0, "past the next mark"),
            ],
            info=_FakeInfo(duration=600.0),
        )
        local_whisper._collect(model, tmp_path / "a.mp3", None, vad=True)
        assert _progress_lines(capsys) == [
            "[moviola] local whisper: 50% (300s/600s)",
            "[moviola] local whisper: 66% (400s/600s)",
        ]

    def test_announces_the_detected_language_when_none_was_pinned(self, tmp_path, capsys):
        model = _FakeModel([_FakeSegment(0.0, 1.0, "t")], info=_FakeInfo(language="el"))
        local_whisper._collect(model, tmp_path / "a.mp3", None, vad=True)
        assert "detected language: el" in capsys.readouterr().err

    def test_stays_quiet_about_language_when_one_was_pinned(self, tmp_path, capsys):
        model = _FakeModel([_FakeSegment(0.0, 1.0, "t")], info=_FakeInfo(language="el"))
        local_whisper._collect(model, tmp_path / "a.mp3", "el", vad=True)
        assert "detected language" not in capsys.readouterr().err


class TestRunVadFallback:
    def test_retries_without_vad_when_vad_is_the_problem(self, tmp_path, monkeypatch):
        calls = []

        def fake_collect(loaded, audio_path, language, vad):
            calls.append(vad)
            if vad:
                raise RuntimeError("onnxruntime is not installed")
            return [{"start": 0.0, "end": 1.0, "text": "ok"}]

        monkeypatch.setattr(local_whisper, "_collect", fake_collect)
        out = local_whisper._run(object(), tmp_path / "a.mp3", None)
        assert calls == [True, False]
        assert out == [{"start": 0.0, "end": 1.0, "text": "ok"}]

    def test_device_failure_is_not_swallowed_as_a_vad_problem(self, tmp_path, monkeypatch):
        def fake_collect(loaded, audio_path, language, vad):
            raise RuntimeError("Library libcublas.so.12 is not found")

        monkeypatch.setattr(local_whisper, "_collect", fake_collect)
        with pytest.raises(RuntimeError, match="libcublas"):
            local_whisper._run(object(), tmp_path / "a.mp3", None)


class TestLoadModelArguments:
    """What actually reaches WhisperModel. Both defaults here were wrong.

    cpu_threads defaulted to 0, which CTranslate2 reads as "decide for me" and
    then decides 4 whatever the machine has. local_files_only defaulted to False,
    so every load called snapshot_download() and contacted huggingface.co for a
    revision check on weights cached months ago.
    """

    def _capture(self, monkeypatch, tmp_path, **env):
        seen = {}

        class _Fake:
            def transcribe(self, path, language=None, vad_filter=True):
                return iter([_FakeSegment(0.0, 1.0, "hi")]), _FakeInfo(1.0, "en")

        def fake_load(model, device, compute_type, threads, local_only):
            seen.update(model=model, device=device, compute_type=compute_type,
                        threads=threads, local_only=local_only)
            return _Fake()

        for key in ("MOVIOLA_WHISPER_OFFLINE", "HF_HUB_OFFLINE",
                    "MOVIOLA_WHISPER_CPU_THREADS", "OMP_NUM_THREADS"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(local_whisper, "is_available", lambda: True)
        monkeypatch.setattr(local_whisper, "resolve_runtime", lambda d, c: ("cpu", "int8"))
        monkeypatch.setattr(local_whisper, "_load_model", fake_load)
        return seen

    def test_cpu_threads_reach_the_model(self, monkeypatch, tmp_path):
        seen = self._capture(monkeypatch, tmp_path, MOVIOLA_WHISPER_CPU_THREADS="7")
        local_whisper.transcribe_local(tmp_path / "a.mp3")
        assert seen["threads"] == 7

    def test_a_gpu_attempt_leaves_the_thread_count_alone(self, monkeypatch, tmp_path):
        # cpu_threads is a CPU-inference setting; passing it on a CUDA load would
        # be meaningless at best and is not what was measured.
        seen = self._capture(monkeypatch, tmp_path, MOVIOLA_WHISPER_CPU_THREADS="7")
        monkeypatch.setattr(local_whisper, "resolve_runtime",
                            lambda d, c: ("cuda", "int8_float16"))
        monkeypatch.setattr(local_whisper, "_preload_cuda_libs", lambda: 0)
        local_whisper.transcribe_local(tmp_path / "a.mp3")
        assert seen["device"] == "cuda"
        assert seen["threads"] == 0

    def test_offline_defaults_to_off(self, monkeypatch, tmp_path):
        seen = self._capture(monkeypatch, tmp_path)
        local_whisper.transcribe_local(tmp_path / "a.mp3")
        assert seen["local_only"] is False

    def test_the_env_switch_turns_offline_on(self, monkeypatch, tmp_path):
        seen = self._capture(monkeypatch, tmp_path, MOVIOLA_WHISPER_OFFLINE="1")
        local_whisper.transcribe_local(tmp_path / "a.mp3")
        assert seen["local_only"] is True

    def test_an_explicit_argument_beats_the_environment(self, monkeypatch, tmp_path):
        # config.py resolves ~/.config/moviola/.env, which is not the process
        # environment — the argument is how a file setting gets a say at all.
        seen = self._capture(monkeypatch, tmp_path, MOVIOLA_WHISPER_OFFLINE="1")
        local_whisper.transcribe_local(tmp_path / "a.mp3", offline_mode=False)
        assert seen["local_only"] is False

    def test_offline_is_announced_on_stderr(self, monkeypatch, tmp_path, capsys):
        # A cache-only load fails differently from a networked one; the user has
        # to be able to tell which mode produced the failure.
        self._capture(monkeypatch, tmp_path, MOVIOLA_WHISPER_OFFLINE="1")
        local_whisper.transcribe_local(tmp_path / "a.mp3")
        assert "offline" in capsys.readouterr().err


class TestDeviceFallbackLoop:
    """transcribe_local()'s cuda->cpu retry. The failure it exists for happens
    while the generator drains, not at load, so both shapes are exercised."""

    def _pin_cuda(self, monkeypatch):
        monkeypatch.setattr(local_whisper, "is_available", lambda: True)
        monkeypatch.setattr(local_whisper, "resolve_runtime", lambda d, c: ("cuda", "int8_float16"))
        monkeypatch.setattr(local_whisper, "_preload_cuda_libs", lambda: 0)

    def test_falls_back_to_cpu_when_the_gpu_fails_at_load(self, tmp_path, monkeypatch, capsys):
        self._pin_cuda(monkeypatch)
        seen = []

        def fake_load(model, device, compute_type, threads=0, local_only=False):
            seen.append((device, compute_type))
            if device == "cuda":
                raise RuntimeError("CUDA failed with error out of memory")
            return _FakeModel([_FakeSegment(0.0, 1.0, "cpu result")])

        monkeypatch.setattr(local_whisper, "_load_model", fake_load)
        out = local_whisper.transcribe_local(tmp_path / "a.mp3")
        assert seen == [("cuda", "int8_float16"), ("cpu", local_whisper.CPU_COMPUTE)]
        assert out == [{"start": 0.0, "end": 1.0, "text": "cpu result"}]
        assert "falling back to CPU" in capsys.readouterr().err

    def test_falls_back_when_the_gpu_fails_mid_transcode(self, tmp_path, monkeypatch):
        """The real cuBLAS failure surfaces while the generator drains, after
        segments have already been yielded — the model loads clean because
        CTranslate2 resolves CUDA lazily and only dies at the first GEMM. So the
        GPU model here yields one segment and then raises, and the assertion is
        that the partial GPU output is discarded rather than merged with the
        CPU retry's."""
        self._pin_cuda(monkeypatch)

        def fake_load(model, device, compute_type, threads=0, local_only=False):
            if device == "cuda":
                return _FakeModel(
                    [_FakeSegment(0.0, 1.0, "partial gpu output")],
                    raises_mid_drain=RuntimeError("Library libcublas.so.12 is not found"),
                )
            return _FakeModel([_FakeSegment(0.0, 1.0, "cpu result")])

        monkeypatch.setattr(local_whisper, "_load_model", fake_load)
        out = local_whisper.transcribe_local(tmp_path / "a.mp3")
        assert out == [{"start": 0.0, "end": 1.0, "text": "cpu result"}]

    def test_no_cpu_retry_when_cpu_was_the_first_choice(self, tmp_path, monkeypatch):
        monkeypatch.setattr(local_whisper, "is_available", lambda: True)
        monkeypatch.setattr(local_whisper, "resolve_runtime", lambda d, c: ("cpu", "int8"))
        seen = []

        def fake_load(model, device, compute_type, threads=0, local_only=False):
            seen.append(device)
            raise RuntimeError("nope")

        monkeypatch.setattr(local_whisper, "_load_model", fake_load)
        with pytest.raises(SystemExit, match="Local whisper failed"):
            local_whisper.transcribe_local(tmp_path / "a.mp3")
        assert seen == ["cpu"]

    def test_both_attempts_failing_raises_systemexit_naming_the_last_error(self, tmp_path, monkeypatch):
        self._pin_cuda(monkeypatch)

        def fake_load(model, device, compute_type, threads=0, local_only=False):
            raise RuntimeError(f"{device} is broken")

        monkeypatch.setattr(local_whisper, "_load_model", fake_load)
        with pytest.raises(SystemExit, match="cpu is broken"):
            local_whisper.transcribe_local(tmp_path / "a.mp3")

    def test_a_failing_cuda_preload_still_falls_back_to_cpu(self, tmp_path, monkeypatch, capsys):
        """_preload_cuda_libs() ran outside the retry's try, so anything it raised
        escaped transcribe_local() as a non-SystemExit — past the caller's
        `except SystemExit` — instead of being the trigger for the CPU retry."""
        monkeypatch.setattr(local_whisper, "is_available", lambda: True)
        monkeypatch.setattr(local_whisper, "resolve_runtime", lambda d, c: ("cuda", "int8_float16"))

        def boom() -> int:
            raise AttributeError("module 'site' has no attribute 'getsitepackages'")

        monkeypatch.setattr(local_whisper, "_preload_cuda_libs", boom)
        seen = []

        def fake_load(model, device, compute_type, threads=0, local_only=False):
            seen.append(device)
            return _FakeModel([_FakeSegment(0.0, 1.0, "cpu result")])

        monkeypatch.setattr(local_whisper, "_load_model", fake_load)
        out = local_whisper.transcribe_local(tmp_path / "a.mp3")
        # The cuda attempt never reached _load_model — the preload killed it.
        assert seen == ["cpu"]
        assert out == [{"start": 0.0, "end": 1.0, "text": "cpu result"}]
        assert "falling back to CPU" in capsys.readouterr().err

    def test_the_failed_gpu_model_is_released_before_the_cpu_one_loads(self, tmp_path, monkeypatch):
        """Peak memory, not tidiness. Binding the exception to a name that
        outlives the except block keeps its traceback, the traceback keeps this
        frame, and the frame keeps `loaded` — so the dead CUDA model stayed
        resident while the CPU retry allocated a second copy of the same weights.
        That doubles peak usage in exactly the OOM case the retry exists for."""
        self._pin_cuda(monkeypatch)
        gpu_ref: list = []

        def fake_load(model, device, compute_type, threads=0, local_only=False):
            if device == "cuda":
                doomed = _FakeModel(
                    [_FakeSegment(0.0, 1.0, "partial")],
                    raises_mid_drain=RuntimeError("CUDA failed with error out of memory"),
                )
                gpu_ref.append(weakref.ref(doomed))
                return doomed
            # By the time the CPU model is being built, nothing may still be
            # holding the GPU one.
            gc.collect()
            assert gpu_ref[0]() is None, "the failed CUDA model was still resident"
            return _FakeModel([_FakeSegment(0.0, 1.0, "cpu result")])

        monkeypatch.setattr(local_whisper, "_load_model", fake_load)
        assert local_whisper.transcribe_local(tmp_path / "a.mp3") == [
            {"start": 0.0, "end": 1.0, "text": "cpu result"}
        ]
