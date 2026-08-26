#!/usr/bin/env python3
"""Transcribe audio on-device with faster-whisper — no API, no key, no upload.

Strategy: reuse the same mono 16 kHz mp3 that the API path extracts, and run it
through faster-whisper (CTranslate2). Returns segments in the same
``{start, end, text}`` shape as :func:`whisper._segments_from_response` and
:func:`transcribe.parse_vtt`, so nothing downstream cares where the transcript
came from.

Why faster-whisper rather than openai-whisper: it is a CTranslate2 reimplementation,
so it runs several times faster at the same accuracy, has no torch dependency
(~2.5 GB saved), and quantizes to int8 so ``large-v3`` fits on a 4 GB GPU or a
laptop CPU. Model weights download on first use to the Hugging Face cache; later loads
still make a revision check against huggingface.co unless MOVIOLA_WHISPER_OFFLINE=1
(or, as a real environment variable, HF_HUB_OFFLINE=1).

Your audio never leaves the machine: no key, no upload, and nothing about the
content of the video is ever sent anywhere. The one thing that does go out is
the model fetch described above — weights in, never audio out.
"""
from __future__ import annotations

import ctypes
import glob
import os
import site
import sys
from pathlib import Path


# Default model. Overridable via MOVIOLA_WHISPER_MODEL — accepts a faster-whisper
# size alias ("tiny", "base", "small", "medium", "large-v3", "distil-large-v3"),
# a Hugging Face repo id, or a path to a local CTranslate2 model directory.
# large-v3 is a 2.9 GB one-time download and matches the API backends' quality;
# "small" (464 MB) is the usual choice when disk or CPU time is tight.
DEFAULT_MODEL = "large-v3"

# GPU compute type. int8_float16 rather than float16 on purpose: it halves VRAM
# (~1.5 GB vs ~3.1 GB for large-v3) with no accuracy loss worth measuring, which
# is the difference between fitting and OOM-ing on a 4 GB card.
GPU_COMPUTE = "int8_float16"
GPU_COMPUTE_FALLBACK = "float16"
CPU_COMPUTE = "int8"

# The devices CTranslate2 actually has a backend for. Anything else is rejected
# rather than quietly demoted — see resolve_runtime().
DEVICES = frozenset({"auto", "cpu", "cuda"})


# Why the failed import is kept: "faster-whisper is not installed" is only one of
# the reasons this returns False. A half-installed CTranslate2, an ABI mismatch
# against numpy, or an OSError from a missing libstdc++ all land here too, and
# telling that user to `pip install faster-whisper` sends them to reinstall a
# package that is already there. The message is reported verbatim, never parsed.
_IMPORT_ERROR = ""


def import_error() -> str:
    """Why the last is_available() said no, as "TypeName: message". "" if it said yes."""
    return _IMPORT_ERROR


def is_available() -> bool:
    """True if faster-whisper is importable (i.e. the user opted into local)."""
    global _IMPORT_ERROR
    try:
        import faster_whisper  # noqa: F401
    except Exception as exc:
        _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
        return False
    _IMPORT_ERROR = ""
    return True


def _first_set(*candidates: str | None) -> str:
    """First candidate that is non-empty once stripped, lowercased. "" if none."""
    for value in candidates:
        if value and value.strip():
            return value.strip().lower()
    return ""


def _physical_cores() -> int:
    """Physical (not logical) cores usable by this process. 0 when unknown.

    0 is a real answer, not a failure code: it is what :func:`_load_model` passes
    to leave CTranslate2 on its own default, so a platform this cannot read stays
    exactly as it was rather than getting a guess.

    Measured on a 6-core/12-thread Ryzen 4800H, `small`/int8/CPU over 120 s of
    audio, three rounds: 6 threads 9.5-9.7 s, 4 threads (CTranslate2's default)
    11.2-12.4 s, 8 threads 11.6-12.1 s, 12 threads 11.8-12.0 s. Physical cores
    win and oversubscribing past them is worse than under-using them, which is
    why this counts cores rather than reaching for os.cpu_count().

    Deliberately NOT handled, because nothing here can see them: a cgroup CPU
    quota (a container limited to 1 CPU on a 64-core host still reads 64 here),
    and any platform without /proc or sysctl — Windows returns 0 and keeps the
    library default.
    """
    # Affinity first where it exists: it is the one limit that is visible, and a
    # taskset-pinned process must not be told it has the whole machine.
    try:
        allowed = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        allowed = os.cpu_count() or 0

    physical = 0
    try:
        if sys.platform == "darwin":
            import subprocess

            out = subprocess.run(
                ["sysctl", "-n", "hw.physicalcpu"],
                capture_output=True, text=True, timeout=5,
            )
            physical = int(out.stdout.strip() or 0)
        else:
            pairs = set()
            physical_id = core_id = None
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                key, _, value = line.partition(":")
                key = key.strip()
                if key == "physical id":
                    physical_id = value.strip()
                elif key == "core id":
                    core_id = value.strip()
                elif not key:
                    if core_id is not None:
                        pairs.add((physical_id, core_id))
                    physical_id = core_id = None
            if core_id is not None:
                pairs.add((physical_id, core_id))
            physical = len(pairs)
    except Exception:
        return 0

    if physical <= 0:
        return 0
    return min(physical, allowed) if allowed else physical


def cpu_threads() -> int:
    """Threads for CPU inference. 0 means "leave CTranslate2's default alone".

    MOVIOLA_WHISPER_CPU_THREADS overrides everything. A pre-set OMP_NUM_THREADS
    is left alone too: CTranslate2 honours it only when cpu_threads is 0, so
    passing a number here would silently override a setting the user made on
    purpose (or that a job scheduler made on their behalf).
    """
    pinned = _first_set(os.environ.get("MOVIOLA_WHISPER_CPU_THREADS"))
    if pinned:
        try:
            return max(0, int(pinned))
        except ValueError:
            raise SystemExit(
                f"MOVIOLA_WHISPER_CPU_THREADS must be an integer, got {pinned!r}."
            )
    if _first_set(os.environ.get("OMP_NUM_THREADS")):
        return 0
    return _physical_cores()


def offline() -> bool:
    """True if the model must load from cache without contacting huggingface.co.

    MOVIOLA_WHISPER_OFFLINE exists alongside huggingface_hub's own HF_HUB_OFFLINE
    because the plugin's config file is not the process environment: config.py
    reads ~/.config/moviola/.env for MOVIOLA_* keys only and exports nothing, so
    HF_HUB_OFFLINE written there would be read by no one. Either switch works;
    this is the one that works from the file the setup flow actually scaffolds.
    """
    for name in ("MOVIOLA_WHISPER_OFFLINE", "HF_HUB_OFFLINE"):
        value = _first_set(os.environ.get(name))
        if value and value not in ("0", "false", "no", "off"):
            return True
    return False


def resolve_runtime(
    device: str | None = None,
    compute_type: str | None = None,
) -> tuple[str, str]:
    """Pick (device, compute_type), honouring explicit overrides.

    ``device`` of None/"auto" probes for a usable CUDA device via CTranslate2 and
    falls back to CPU. Detection is best-effort only — a machine can report a CUDA
    device and still fail at load time on missing cuDNN (common under WSL), which
    is why :func:`transcribe_local` also retries on CPU at runtime.
    """
    # Strip before the `or` chain, not after: a blank-but-present env var — what a
    # half-filled .env or a secret sync produces — is truthy, so `x or default`
    # would select the blank string and only the strip afterwards would reveal it,
    # as an empty device name that matches nothing.
    want_device = _first_set(device, os.environ.get("MOVIOLA_WHISPER_DEVICE"), "auto")
    want_compute = _first_set(compute_type, os.environ.get("MOVIOLA_WHISPER_COMPUTE"), "auto")

    # Anything unrecognised used to fall through every branch below and land on
    # "cpu", so `MOVIOLA_WHISPER_DEVICE=cuda:0` — or `gpu`, or `mps` — asked for a
    # GPU and silently got a CPU transcode several times slower, with nothing
    # printed. CTranslate2 has no MPS backend and takes the device index
    # separately, so none of those are typos we can honour; say so instead.
    if want_device not in DEVICES:
        raise SystemExit(
            f"Unknown Whisper device {want_device!r}. "
            f"Use one of: {', '.join(sorted(DEVICES))}. "
            "(CTranslate2 has no Apple-Metal backend, and a device index like "
            "'cuda:0' is not accepted here — set CUDA_VISIBLE_DEVICES to pick a card.)"
        )

    resolved_device = "cpu"
    supported: set[str] = set()
    if want_device in ("auto", "cuda"):
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                resolved_device = "cuda"
                supported = set(ctranslate2.get_supported_compute_types("cuda"))
        except Exception:
            resolved_device = "cpu"
    if want_device == "cpu":
        resolved_device = "cpu"
    elif want_device == "cuda":
        # Explicit request: honour it even if probing failed, and let the load
        # error (with its CPU retry) be the thing that reports the real problem.
        resolved_device = "cuda"

    if want_compute != "auto":
        return resolved_device, want_compute

    if resolved_device == "cuda":
        if not supported or GPU_COMPUTE in supported:
            return resolved_device, GPU_COMPUTE
        if GPU_COMPUTE_FALLBACK in supported:
            return resolved_device, GPU_COMPUTE_FALLBACK
        return resolved_device, "float32"

    # The CPU side used to hand back "int8" unconditionally while the CUDA side
    # above probed. That asymmetry has no fallback behind it: the retry list in
    # transcribe_local only appends a CPU attempt when the *first* attempt was
    # CUDA, so a CPU that cannot do int8 fails the load and stops. Probe here
    # too, and keep int8 as the answer whenever the probe cannot run — that is
    # the previous behaviour, so a missing ctranslate2 changes nothing.
    return resolved_device, _best_cpu_compute()


def _best_cpu_compute() -> str:
    """First of int8 / int8_float32 / float32 this CPU supports. int8 if unknown."""
    try:
        import ctranslate2

        supported = set(ctranslate2.get_supported_compute_types("cpu"))
    except Exception:
        return CPU_COMPUTE
    for candidate in (CPU_COMPUTE, "int8_float32", "float32"):
        if candidate in supported:
            return candidate
    return CPU_COMPUTE


def _preload_cuda_libs() -> int:
    """Make pip-installed CUDA libraries loadable by CTranslate2. Returns count loaded.

    `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` drops its .so files under
    site-packages/nvidia/*/lib, which is not on the dynamic loader's search path.
    CTranslate2 then reports "Library libcublas.so.12 is not found" on a machine
    where the library is demonstrably installed — the single most common reason
    GPU transcription silently falls back to CPU. Loading them RTLD_GLOBAL here
    puts them in the process's global symbol table before the model is built.

    Discovery is by glob, never by hardcoded soname, so this keeps working across
    CUDA major versions. It is best-effort: a system-wide CUDA install needs none
    of it, and every failure is ignored in favour of the CPU retry.

    Deliberately NOT narrowed to an allowlist of libraries CTranslate2 "obviously"
    needs. The tempting cut is nvrtc — 227 MB of JIT compiler for an engine that
    ships precompiled kernels — but cuDNN's runtime-compiled engine path loads
    nvrtc itself, so dropping it trades 227 MB for a GPU that silently demotes to
    CPU on some models. Breadth costs 0.125 s and ~295 MB of demand-paged RSS
    here (17 libraries, 2.23 GB on disk); that is the price of not guessing.

    NOT supported on Windows, and not silently: the pip wheels put DLLs under
    nvidia/*/bin rather than */lib, and Windows resolves them through
    os.add_dll_directory() rather than RTLD_GLOBAL, so the POSIX mechanism below
    is not portable by adding a pattern. It finds nothing there and the CPU path
    is used — correct, just slower. See TODOS.md.
    """
    bases: list[str] = []
    # Both are guarded, not just the second one: getsitepackages() is absent from
    # some virtualenv-provided `site` modules, and an AttributeError raised here
    # is not a SystemExit, so it would escape transcribe_local() entirely and
    # crash the run instead of falling back to CPU.
    for probe in (site.getsitepackages, site.getusersitepackages):
        try:
            found = probe()
        except Exception:
            continue
        bases.extend([found] if isinstance(found, str) else found)

    libs = []
    for base in bases:
        libs.extend(glob.glob(os.path.join(base, "nvidia", "*", "lib", "*.so*")))
    # The ".alt" builds are a second copy of a library the glob already found
    # (libnvrtc.alt.so.12 beside libnvrtc.so.12 — 113 MB of the total). Loading
    # both RTLD_GLOBAL puts two definitions of the same symbols in one namespace
    # and glob order picks the winner, which is a coin flip nobody chose.
    libs = [lib for lib in libs if ".alt." not in os.path.basename(lib)]
    if not libs:
        return 0

    # Several passes: these libraries depend on each other and glob order is
    # arbitrary, so one that fails on pass 1 often loads once its dependency has.
    loaded: set[str] = set()
    for _ in range(3):
        progressed = False
        for lib in libs:
            if lib in loaded:
                continue
            try:
                ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                continue
            loaded.add(lib)
            progressed = True
        if not progressed:
            break
    return len(loaded)


def _load_model(model: str, device: str, compute_type: str, threads: int, local_only: bool):
    """Build the WhisperModel. Two arguments here that faster-whisper defaults badly.

    cpu_threads: its default of 0 means "let CTranslate2 decide", and CTranslate2
    decides 4 regardless of the machine — measurably the wrong number on anything
    with more than four cores (see :func:`_physical_cores`). Passing 0 through is
    still the fallback when the core count cannot be read.

    local_files_only: its default of False means every load calls
    snapshot_download(), which contacts huggingface.co for a revision check even
    when the weights have been cached for months. That is the only network access
    this backend makes, and a fork whose premise is "the audio never leaves the
    machine" should let a user close it — see :func:`offline`.
    """
    from faster_whisper import WhisperModel

    return WhisperModel(
        model,
        device=device,
        compute_type=compute_type,
        cpu_threads=threads,
        local_files_only=local_only,
    )


def _looks_like_vad_problem(exc: Exception) -> bool:
    """True if `exc` reads like a missing VAD dependency rather than a real failure.

    Matching on message text is crude, but the alternative — treating *any*
    exception from a VAD-enabled transcribe as a VAD problem — misreports GPU
    and OOM failures as "VAD unavailable" and sends the user down the wrong path.
    """
    blob = f"{type(exc).__name__}: {exc}".lower()
    return any(token in blob for token in ("onnx", "vad", "silero"))


def _collect(loaded, audio_path: Path, language: str | None, vad: bool) -> list[dict]:
    """Transcribe and fully drain the segment generator.

    Draining matters: faster-whisper's transcribe() returns lazily, so the actual
    compute — and therefore any GPU failure — happens in this loop, not at the
    call above it. Anything that consumes segments outside a try/except would let
    those errors escape the fallback that is supposed to catch them.
    """
    segments, info = loaded.transcribe(str(audio_path), language=language, vad_filter=vad)

    out: list[dict] = []
    total = getattr(info, "duration", 0.0) or 0.0
    next_mark = 60.0
    for seg in segments:
        text = (seg.text or "").strip()
        if text:
            out.append({
                "start": round(float(seg.start or 0.0), 2),
                "end": round(float(seg.end or 0.0), 2),
                "text": text,
            })
        # Report progress so a long clip doesn't look like a hang.
        if total and seg.end and seg.end >= next_mark:
            pct = min(100, int(100 * seg.end / total))
            print(f"[moviola] local whisper: {pct}% ({seg.end:.0f}s/{total:.0f}s)", file=sys.stderr)
            while next_mark <= seg.end:
                next_mark += 60.0

    detected = getattr(info, "language", None)
    if detected and language is None:
        print(f"[moviola] local whisper detected language: {detected}", file=sys.stderr)
    return out


def _run(loaded, audio_path: Path, language: str | None) -> list[dict]:
    """Transcribe, dropping the VAD filter if (and only if) VAD is what broke.

    vad_filter needs onnxruntime and the bundled Silero weights; when either is
    missing faster-whisper raises. VAD only trims silence, so losing it costs
    speed, never correctness. Every other failure propagates so the caller's
    device fallback can see it.
    """
    try:
        return _collect(loaded, audio_path, language, vad=True)
    except Exception as exc:
        if not _looks_like_vad_problem(exc):
            raise
        print(
            f"[moviola] VAD filter unavailable ({type(exc).__name__}) — "
            "transcribing without it",
            file=sys.stderr,
        )
        return _collect(loaded, audio_path, language, vad=False)


def transcribe_local(
    audio_path: Path,
    model: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
    language: str | None = None,
    offline_mode: bool | None = None,
) -> list[dict]:
    """Transcribe `audio_path` on-device and return {start, end, text} segments.

    `language` of None lets Whisper auto-detect. `offline_mode` of None reads
    :func:`offline`, so the environment answers when the caller has no opinion.
    Raises SystemExit on failure so the caller's existing `except SystemExit`
    fallback path keeps working.
    """
    if not is_available():
        raise SystemExit(
            "faster-whisper could not be imported "
            f"({import_error() or 'no error reported'}). Install the local backend with:\n"
            "  pip install \"faster-whisper>=1.0\"\n"
            "…or set GROQ_API_KEY / OPENAI_API_KEY to use an API backend instead.\n"
            "If it is already installed, the message above is the real failure — a "
            "broken CTranslate2 or a numpy ABI mismatch lands here too, and "
            "reinstalling faster-whisper will not fix either."
        )

    model_name = model or os.environ.get("MOVIOLA_WHISPER_MODEL") or DEFAULT_MODEL
    resolved_device, resolved_compute = resolve_runtime(device, compute_type)
    local_only = offline() if offline_mode is None else bool(offline_mode)
    if local_only:
        print(
            "[moviola] offline: loading the model from cache only, no revision "
            "check against huggingface.co",
            file=sys.stderr,
        )

    # A visible CUDA device is not a working one. CTranslate2 resolves cuBLAS and
    # cuDNN lazily, so a machine can enumerate a GPU, construct the model without
    # complaint, and only fail on the first matrix multiply. _preload_cuda_libs()
    # removes the most common cause; OOM, driver mismatches and unsupported compute
    # types remain, and only show up mid-transcode. Deliberately no library-presence
    # pre-flight beyond that preload: hardcoding
    # sonames (libcublas.so.12, libcudnn_ops.so.9) would silently demote a working
    # GPU as soon as CTranslate2 moves to the next CUDA major. Retrying the whole
    # load-and-transcode on CPU is version-proof and costs one wasted model load.
    attempts = [(resolved_device, resolved_compute)]
    if resolved_device == "cuda":
        attempts.append(("cpu", CPU_COMPUTE))

    last_error = ""
    for index, (attempt_device, attempt_compute) in enumerate(attempts):
        loaded = None
        try:
            # Inside the try, not before it: _preload_cuda_libs() walks
            # site-packages and calls into the dynamic loader, and anything it
            # raises has to be caught by the same retry that catches a failed
            # load — otherwise it escapes as a non-SystemExit and skips the CPU
            # fallback that exists precisely for a broken CUDA install.
            if attempt_device == "cuda":
                # The count was computed and discarded before. It is the fastest
                # way to tell "no pip CUDA wheels here, the system install is
                # doing the work" from "the wheels are here and still did not
                # help", which are the two shapes behind a surprise CPU fallback.
                count = _preload_cuda_libs()
                print(
                    f"[moviola] preloaded {count} pip CUDA librar"
                    f"{'y' if count == 1 else 'ies'}"
                    + ("" if count else " (none found — relying on a system CUDA install)"),
                    file=sys.stderr,
                )
            print(
                f"[moviola] loading local whisper model '{model_name}' "
                f"({attempt_device}/{attempt_compute})…",
                file=sys.stderr,
            )
            loaded = _load_model(
                model_name,
                attempt_device,
                attempt_compute,
                cpu_threads() if attempt_device == "cpu" else 0,
                local_only,
            )
            return _run(loaded, audio_path, language)
        except Exception as exc:
            # Keep the message, not the exception object. Binding `exc` to a name
            # that outlives this block keeps its traceback alive, the traceback
            # holds this frame, and this frame holds `loaded` — so the failed CUDA
            # model would stay resident in VRAM while the CPU attempt loads a
            # second copy of the same weights. That doubles peak memory in exactly
            # the OOM case the retry exists to survive.
            last_error = f"{type(exc).__name__}: {exc}"
            if index + 1 < len(attempts):
                print(
                    f"[moviola] {attempt_device} backend failed "
                    f"({last_error}) — falling back to CPU",
                    file=sys.stderr,
                )
        finally:
            # Same reason, for the non-exception path out of the loop and for the
            # window between the failure and the next attempt's allocation.
            loaded = None

    raise SystemExit(f"Local whisper failed ('{model_name}'): {last_error}")


if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        print("usage: local_whisper.py <audio-path> [--model M] [--device cpu|cuda]", file=sys.stderr)
        raise SystemExit(2)

    argv = sys.argv[1:]
    audio = Path(argv[0])
    model_arg = argv[argv.index("--model") + 1] if "--model" in argv else None
    device_arg = argv[argv.index("--device") + 1] if "--device" in argv else None

    result = transcribe_local(audio, model=model_arg, device=device_arg)
    print(json.dumps({"backend": "local", "segments": result}, indent=2))
