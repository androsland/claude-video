#!/usr/bin/env python3
"""Transcribe a video with Whisper — locally, or via the Groq / OpenAI API.

Strategy: extract audio (mono 16kHz mp3, tiny payload), then hand it to a
backend. Returns segments in the same shape as transcribe.parse_vtt so the rest
of the pipeline (filter_range, format_transcript) doesn't care where the
transcript came from.

The API path is pure stdlib — no `pip install groq` or `pip install openai`
needed. The "local" backend lives in local_whisper.py and is the only one with
an optional third-party dependency (faster-whisper); it is imported lazily so
this module keeps working when that package is absent.
"""
from __future__ import annotations

import io
import json
import math
import mimetypes
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


GROQ_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"

OPENAI_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_MODEL = "whisper-1"

LOCAL_BACKEND = "local"

# Both Groq's free tier and OpenAI whisper-1 cap uploads at 25 MB. We target a
# margin under that so multipart framing overhead never pushes a chunk over.
MAX_UPLOAD_BYTES = 24 * 1024 * 1024

# Floor for "this file actually contains audio". mp3 at 64 kbps mono is ~8 kB per
# second; a header-only file ffmpeg writes for an out-of-range seek measured 333
# bytes. See extract_audio().
MIN_AUDIO_BYTES = 2048


def plan_chunks(
    total_seconds: float,
    total_bytes: int,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> list[tuple[float, float]]:
    """Split a duration into contiguous (offset, duration) chunks under max_bytes.

    Size scales linearly with duration (constant-bitrate mono mp3), so an even
    time split yields evenly-sized chunks. Returns a single full-length chunk
    when the audio already fits.
    """
    if total_bytes <= max_bytes or total_seconds <= 0:
        return [(0.0, total_seconds)]

    n = math.ceil(total_bytes / max_bytes)
    chunk = total_seconds / n
    plan: list[tuple[float, float]] = []
    for i in range(n):
        offset = i * chunk
        # The last chunk absorbs any rounding remainder so durations sum exactly.
        duration = (total_seconds - offset) if i == n - 1 else chunk
        plan.append((round(offset, 3), round(duration, 3)))
    return plan


API_CANDIDATES = (("GROQ_API_KEY", "groq"), ("OPENAI_API_KEY", "openai"))

# Any bit granting group or other access. moviola's one answer to "can somebody
# else on this machine reach my key file" — every surface that asks calls
# warn_if_key_file_is_exposed rather than re-deriving this, because the copies
# had already drifted: setup.py tested `mode & 0o044` (READ only) and stayed
# silent on a group-writable file, which is the worse case of the two — another
# user replaces the key and the audio is uploaded to, and billed to, their
# account. test_key_file_permissions pins all three surfaces to one table.
KEY_FILE_EXPOSED_BITS = 0o077

_PERM_WARNED: set[str] = set()


def warn_if_key_file_is_exposed(path: Path) -> None:
    """Warn to stderr, once per path per process, if others can reach `path`.

    It warns and returns; it never refuses to read the file. The key is already
    on disk either way, and stranding a run over a condition `chmod` fixes in
    one command trades a real failure for a hypothetical one.

    NON-GOALS: this reads the file's MODE BITS. It cannot see the directory's
    mode, a POSIX ACL, or a filesystem that does not implement modes at all (a
    Windows drive under WSL, FAT/exFAT, some network mounts) — on any of those
    an exposed key reports clean and `chmod` is a no-op. It also says nothing
    about a key that was already leaked by other means.
    """
    key = str(path)
    if key in _PERM_WARNED:
        return
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return
    if not mode & KEY_FILE_EXPOSED_BITS:
        return
    _PERM_WARNED.add(key)
    sys.stderr.write(
        f"[moviola] WARNING: {path} has permissions {mode:03o} — other users on "
        f"this machine can reach your API key. Fix: chmod 600 {path}\n"
    )
    sys.stderr.flush()



def _env_key(name: str) -> str | None:
    """An API key read from the process environment, or None if unset or blank."""
    value = os.environ.get(name)
    return value.strip() if value else None


def env_key_backend() -> str | None:
    """The backend whose key sits in the process environment, if any.

    Exists ONLY to explain why an unpinned run declined to upload. Never call it
    to choose a backend: ignoring the environment is the whole point.
    """
    for name, backend in API_CANDIDATES:
        if _env_key(name):
            return backend
    return None


def load_api_key(
    preferred: str | None = None, *, allow_env: bool = True
) -> tuple[str, str] | tuple[None, None]:
    """Return (backend, api_key). Prefers Groq, falls back to OpenAI.

    If `preferred` is "groq" or "openai", only that backend's key is considered.

    Only `~/.config/moviola/.env` is searched on disk, whatever `allow_env` says.
    That file is the one place a key means moviola may use it, because setup.py
    asked before writing it.

    `allow_env=False` additionally ignores the process environment.
    resolve_backend passes it when nothing is pinned: a key exported into a shell
    was put there for whatever the user was running at the time, and reading it
    as standing permission to upload their audio mistakes an accident for
    consent.
    """
    def _from_dotenv(path: Path, name: str) -> str | None:
        if not path.exists():
            return None
        # The runtime is the surface that actually reads the key, and until the
        # consent audit it was the only one of the three that never checked the
        # file's mode. That was a hole rather than a division of labour.
        warn_if_key_file_is_exposed(path)
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() != name:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                return value or None
        except OSError:
            return None
        return None

    # moviola's own config file, and nothing else. A key in `$PWD/.env` belongs
    # to whatever project that directory happens to be — for a Claude Code
    # plugin, cwd is the user's checkout by construction, and that file may have
    # been committed by someone they have never met. Reading it is the same
    # mistake as reading an ambient environment variable: it takes a
    # credential's presence for its owner's permission, and it silently decides
    # whose account the audio is billed and disclosed to.
    #
    # It was invisible to both preflights, which read this path alone, so a key
    # found here uploaded audio underneath a "no backend configured" notice.
    # test_consent_oracles pins all three surfaces to a single answer.
    dotenv_paths = [Path.home() / ".config" / "moviola" / ".env"]

    candidates = API_CANDIDATES
    if preferred is not None:
        candidates = tuple(c for c in candidates if c[1] == preferred)

    for key_name, backend in candidates:
        value = _env_key(key_name) if allow_env else None
        if not value:
            for candidate in dotenv_paths:
                value = _from_dotenv(candidate, key_name)
                if value:
                    break
        if value:
            return backend, value

    return None, None


def local_available() -> bool:
    """True if the on-device backend can run (faster-whisper importable)."""
    try:
        import local_whisper
    except Exception:
        return False
    return local_whisper.is_available()


def resolve_backend(preferred: str | None = None) -> tuple[str | None, str | None]:
    """Return (backend, api_key). api_key is None for "local", which needs none.

    Precedence when nothing is pinned is deliberately local-first: if
    faster-whisper is importable, the audio does not leave the machine. The cost
    is real and is the reason the ordering is stated everywhere it is
    observable: a CPU transcode can take minutes where an API call takes
    seconds. Pin MOVIOLA_WHISPER=groq/openai (or pass --whisper) to trade the
    other way.

    Local-first alone was not enough, and the gap is the reason for allow_env.
    On a machine WITHOUT faster-whisper — the state every machine starts in —
    an unpinned run used to fall through to whatever GROQ_API_KEY or
    OPENAI_API_KEY it could see, an ambient one exported for a different tool
    included, and upload the audio. So an unpinned lookup consults only
    moviola's own config file. A pin is consent and restores the environment as
    a key source.

    `$PWD/.env` is not a key source at all, pinned or not. Upstream reads it and
    this fork deliberately does not: for a Claude Code plugin the working
    directory is the user's checkout, so a `.env` committed to a repo they
    cloned would pick the provider account their audio is billed and disclosed
    to. It was also unreadable by both preflights, which made every such upload
    an unannounced one.

    NON-GOALS, because an unstated limit reads as a claim of coverage:
      - It cannot tell a key exported FOR moviola from one exported for another
        tool; both are just os.environ. Someone who deliberately exports one now
        has to pin MOVIOLA_WHISPER, and the no-backend hint tells them so.
      - Dropping `$PWD/.env` removes a real workflow — a per-project key — and
        offers nothing in its place beyond moving the key to the config file or
        pinning. That is the intended trade, not an oversight.
      - It does not stop a pinned upload, and is not meant to. Pinning is the
        consent, and MOVIOLA_WHISPER is itself readable from the environment so
        CI can still pin without a config file.
      - Consent is judged from where the key SITS, which cannot distinguish a
        config file the user wrote from one an installer wrote for them.

    Returns (None, None) when no backend is usable — `preferred` names an API
    backend whose key is missing, "local" without faster-whisper, or nothing is
    pinned and the only key found is an ambient environment one — so the caller
    reports one hint rather than failing mid-transcode.
    """
    if preferred == LOCAL_BACKEND:
        return (LOCAL_BACKEND, None) if local_available() else (None, None)
    if preferred:
        return load_api_key(preferred)

    if local_available():
        return LOCAL_BACKEND, None
    return load_api_key(allow_env=False)

def extract_audio(
    video_path: str,
    out_path: Path,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> Path:
    """Extract mono 16kHz 64kbps mp3 — ~480 kB/min, fits any Whisper limit.

    `start_seconds`/`end_seconds` clip the extraction, so a focused request only
    ever transcribes the range it asked about. -ss/-to go before -i (input
    seeking), which lets ffmpeg skip straight to the keyframe rather than
    decoding everything up to it.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    # The range is checked HERE rather than only in the CLI because this is where
    # the numbers become an ffmpeg command line. moviola.py validates its own
    # flags, but transcribe_video() takes start/end from any caller, and an
    # inverted range reaches ffmpeg as "-to value smaller than -ss" — a message
    # naming flags the caller never wrote. This is a shape check, not a bounds
    # check: it cannot see the video's duration, so a range past the end of the
    # file is still ffmpeg's to report.
    if start_seconds is not None and start_seconds < 0:
        raise SystemExit(f"audio range start must be non-negative, got {start_seconds:.3f}s")
    if end_seconds is not None and end_seconds <= (start_seconds or 0.0):
        raise SystemExit(
            f"audio range end ({end_seconds:.3f}s) must be greater than its start "
            f"({start_seconds or 0.0:.3f}s)"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    seek: list[str] = []
    if start_seconds is not None and start_seconds > 0:
        seek += ["-ss", f"{start_seconds:.3f}"]
    if end_seconds is not None:
        seek += ["-to", f"{end_seconds:.3f}"]
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        *seek,
        "-i", str(Path(video_path).resolve()),
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "64k",
        str(out_path.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg audio extraction failed: {result.stderr.strip()}")
    # Not `== 0`: an -ss past the end of the video exits 0 and writes a valid but
    # empty mp3 — measured at 333 bytes for a header-only file — which sails past
    # a zero-byte check and reaches Whisper as silence. At 64 kbps mono one second
    # is ~8 kB, so anything under 2 kB carries no audio worth transcribing.
    size = out_path.stat().st_size if out_path.exists() else 0
    if size < MIN_AUDIO_BYTES:
        if seek:
            raise SystemExit(
                "ffmpeg produced no audio for the requested range "
                f"({_range_text(start_seconds, end_seconds)}) — the range is "
                "probably past the end of the video, or the video has no audio track"
            )
        raise SystemExit("ffmpeg produced no audio — video may have no audio track")
    return out_path


def _range_text(start_seconds: float | None, end_seconds: float | None) -> str:
    start = "start" if not start_seconds else f"{start_seconds:.1f}s"
    end = "end" if end_seconds is None else f"{end_seconds:.1f}s"
    return f"{start}–{end}"


def audio_duration(audio_path: Path) -> float:
    """Return the duration of an audio file in seconds via ffprobe."""
    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is not installed. Install with: brew install ffmpeg")

    result = subprocess.run(
        [
            "ffprobe",
            # -v error, not -v quiet: quiet silences ffprobe's stderr, so the
            # `result.stderr` reported below on a non-zero exit was always empty
            # and the failure message said nothing at all.
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            str(audio_path.resolve()),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed: {result.stderr.strip()}")
    fmt = json.loads(result.stdout or "{}").get("format", {})
    return float(fmt.get("duration") or 0.0)


def split_audio(
    full_audio: Path,
    work_dir: Path,
    plan: list[tuple[float, float]],
) -> list[tuple[Path, float]]:
    """Slice full_audio into per-plan chunk files, returning (path, offset) pairs.

    Uses stream copy (`-c copy`) so there is no re-encode and no quality loss;
    mp3 frame boundaries are close enough for transcription's purposes.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    work_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[tuple[Path, float]] = []
    for index, (offset, duration) in enumerate(plan):
        out_path = work_dir / f"chunk_{index:03d}.mp3"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-ss", f"{offset:.3f}",
            "-i", str(full_audio.resolve()),
            "-t", f"{duration:.3f}",
            "-c", "copy",
            str(out_path.resolve()),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            raise SystemExit(
                f"ffmpeg failed to split audio chunk {index + 1}: {result.stderr.strip()}"
            )
        chunks.append((out_path, offset))
    return chunks


def _build_multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    """Assemble a multipart/form-data body the Whisper APIs accept.

    Whisper's multipart upload is small and predictable — doing it by hand
    keeps us on pure stdlib instead of pulling requests/groq/openai SDKs.
    """
    boundary = f"----MoviolaBoundary{uuid.uuid4().hex}"
    eol = b"\r\n"
    buf = io.BytesIO()

    for name, value in fields.items():
        buf.write(f"--{boundary}".encode()); buf.write(eol)
        buf.write(f'Content-Disposition: form-data; name="{name}"'.encode()); buf.write(eol)
        buf.write(eol)
        buf.write(str(value).encode()); buf.write(eol)

    mimetype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    buf.write(f"--{boundary}".encode()); buf.write(eol)
    buf.write(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode()
    )
    buf.write(eol)
    buf.write(f"Content-Type: {mimetype}".encode()); buf.write(eol)
    buf.write(eol)
    buf.write(file_path.read_bytes())
    buf.write(eol)
    buf.write(f"--{boundary}--".encode()); buf.write(eol)

    return buf.getvalue(), boundary


# extract_audio pins 64 kbps mono, so bytes convert to minutes without an
# ffprobe spawn. An estimate on purpose — see _announce_upload.
AUDIO_BYTES_PER_MINUTE = 64_000 / 8 * 60
COST_WARN_MINUTES = 60

API_HOSTS = {"groq": "api.groq.com", "openai": "api.openai.com"}


def _announce_upload(backend: str, audio_bytes: int) -> None:
    """Say what is about to be sent, and where, before the first paid request.

    The frame path already warns before it spends: moviola.py prints a
    token-burner notice above 250 frames and a coverage notice on videos over
    ten minutes. The audio path had no equivalent. plan_chunks splits at the
    24 MB upload cap with no ceiling on the number of chunks that produces, and
    each chunk is one billed request, so a long video became an unannounced
    sequence of them.

    This deliberately does NOT cap anything. A cap would break the long-video
    case it exists to protect, and picking a ceiling for someone else's budget
    is not this script's call. What was missing is that the spend was invisible
    until after it happened.

    Minutes are ESTIMATED from the byte count at extract_audio's fixed bitrate
    rather than probed: this runs before every upload and a subprocess for one
    stderr line is not worth it. The number is an order of magnitude, not a
    billing statement, and it says nothing about what either provider charges.
    """
    minutes = audio_bytes / AUDIO_BYTES_PER_MINUTE
    requests = max(1, math.ceil(audio_bytes / MAX_UPLOAD_BYTES))
    host = API_HOSTS.get(backend, backend)
    print(
        f"[moviola] audio: {audio_bytes / (1024 * 1024):.1f} MB (~{minutes:.0f} min) — "
        f"uploading to {host} in {requests} request{'' if requests == 1 else 's'}…",
        file=sys.stderr,
    )
    if minutes >= COST_WARN_MINUTES:
        print(
            f"[moviola] Warning: that is roughly {minutes / 60:.1f} hours of audio and "
            f"the {backend} API bills per minute of it. Narrow the job with "
            "`--start HH:MM:SS --end HH:MM:SS`, skip transcription with "
            "`--no-whisper`, or `pip install \"faster-whisper>=1.0\"` to run it on "
            "this machine for nothing.",
            file=sys.stderr,
        )


MAX_ATTEMPTS = 4       # initial + 3 retries
MAX_429_RETRIES = 2
RETRY_BASE_DELAY = 2.0


def _post_whisper(endpoint: str, api_key: str, model: str, audio_path: Path) -> dict:
    fields = {
        "model": model,
        "response_format": "verbose_json",
        "temperature": "0",
    }
    body, boundary = _build_multipart(fields, audio_path)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        # Groq sits behind Cloudflare — the default `Python-urllib/3.x` UA
        # trips WAF rule 1010 (403) before auth even runs. Any non-default
        # UA clears it; we identify honestly.
        "User-Agent": "moviola-skill/1.0 (+claude-code; python-urllib)",
    }

    context = ssl.create_default_context()
    rate_limit_hits = 0
    last_exc: Exception | None = None
    last_detail = ""

    for attempt in range(MAX_ATTEMPTS):
        request = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=300, context=context) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = _read_error_body(exc)
            last_exc, last_detail = exc, detail

            # 4xx other than 429 are client errors — no retry will fix them.
            if 400 <= exc.code < 500 and exc.code != 429:
                raise SystemExit(f"Whisper request failed: {exc}{detail}")

            if exc.code == 429:
                rate_limit_hits += 1
                if rate_limit_hits >= MAX_429_RETRIES:
                    raise SystemExit(f"Whisper request failed: {exc}{detail}")
                delay = _retry_after(exc) or RETRY_BASE_DELAY * (2 ** attempt) + 1
            else:
                delay = RETRY_BASE_DELAY * (2 ** attempt)

            if attempt < MAX_ATTEMPTS - 1:
                print(
                    f"[moviola] whisper HTTP {exc.code} — retrying in {delay:.1f}s "
                    f"(attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as exc:
            last_exc, last_detail = exc, ""
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (attempt + 1)
                print(
                    f"[moviola] whisper network error ({type(exc).__name__}: {exc}) — "
                    f"retrying in {delay:.1f}s (attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Whisper returned non-JSON response: {exc}: {payload[:200]}")

    raise SystemExit(
        f"Whisper request failed after {MAX_ATTEMPTS} attempts: {last_exc}{last_detail}"
    )


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read()
    except Exception:
        return ""
    if not body:
        return ""
    try:
        return f" — {body.decode('utf-8', errors='replace')[:400]}"
    except Exception:
        return ""


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    header = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def shift_segments(segments: list[dict], offset_seconds: float) -> list[dict]:
    """Return a copy of segments with start/end shifted by offset_seconds.

    Each chunk is transcribed in isolation, so Whisper returns 0-based timestamps
    per chunk; shifting by the chunk's offset stitches them into source time.

    A copy even at offset 0, where it used to hand back the caller's own list.
    Docstrings that promise a copy and return an alias on one branch are how a
    caller ends up mutating a list it was told it owned, and the branch that does
    it is the one nobody tests.
    """
    if offset_seconds == 0:
        return [dict(seg) for seg in segments]
    return [
        {
            "start": round(seg["start"] + offset_seconds, 2),
            "end": round(seg["end"] + offset_seconds, 2),
            "text": seg["text"],
        }
        for seg in segments
    ]


def _as_seconds(value: object) -> float:
    """Coerce a response timestamp to a rounded float; 0.0 when it is unusable.

    A segment whose timestamp is garbled is still worth its text. Losing the
    transcript over one bad float would be the wrong trade, and float(None)
    raising TypeError inside a list comprehension is how that used to happen.

    NaN and the infinities are the cases that do not announce themselves:
    json.loads admits the non-standard `NaN`/`Infinity`/`-Infinity` tokens by
    default, and float() and round() both accept them without complaint. They
    survive this function only to blow up later at int(seg["start"]) in
    format_transcript, which runs over the WHOLE concatenated transcript — so
    one bad timestamp from one chunk would discard every segment, which is the
    exact trade this is here to avoid. isfinite is the check that sees them.
    """
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(seconds, 2) if math.isfinite(seconds) else 0.0


def _segments_from_response(data: object) -> list[dict]:
    """Convert Whisper verbose_json into our {start, end, text} segment format.

    Deliberately defensive about shape, because `data` is whatever a 200
    response body happened to parse to and nothing between here and the provider
    guarantees the documented object: a proxy or gateway can answer 200 with a
    JSON array or a bare string, and a schema change can put non-dicts inside
    "segments". Each of those reached .get() or .strip() on the wrong type and
    raised AttributeError or TypeError — neither of which any caller caught, so
    a malformed payload took down the whole report, frames included, when the
    frames were already extracted and cost nothing to keep.

    It is NOT a schema validator and does not try to be: a well-formed object
    carrying nonsense text passes straight through, which is correct — judging
    transcript content is not this function's job.
    """
    if not isinstance(data, dict):
        raise SystemExit(
            f"Whisper returned a JSON {type(data).__name__}, expected an object"
        )

    out: list[dict] = []
    raw = data.get("segments")
    for seg in raw if isinstance(raw, list) else []:
        if not isinstance(seg, dict):
            continue
        text = seg.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        out.append({
            "start": _as_seconds(seg.get("start")),
            "end": _as_seconds(seg.get("end")),
            "text": text.strip(),
        })

    if not out:
        full = data.get("text")
        if isinstance(full, str) and full.strip():
            out.append({"start": 0.0, "end": 0.0, "text": full.strip()})

    return out


def transcribe_chunks(
    chunks: list[tuple[Path, float]],
    transcribe_one,
) -> list[dict]:
    """Transcribe each chunk, shift its segments by the chunk offset, concatenate.

    A chunk that fails after its own retries is logged and skipped so one bad
    slice doesn't discard the whole transcript. Raises only if every chunk fails.
    """
    segments: list[dict] = []
    failures = 0
    for index, (path, offset) in enumerate(chunks):
        try:
            chunk_segments = transcribe_one(path)
        except SystemExit as exc:
            failures += 1
            print(
                f"[moviola] chunk {index + 1}/{len(chunks)} failed — skipping ({exc})",
                file=sys.stderr,
            )
            continue
        segments.extend(shift_segments(chunk_segments, offset))
        print(
            f"[moviola] chunk {index + 1}/{len(chunks)} → {len(chunk_segments)} segments",
            file=sys.stderr,
        )

    if failures == len(chunks):
        raise SystemExit("Whisper failed on every audio chunk")
    return segments


def _transcribe_file(backend: str, api_key: str, audio_path: Path) -> list[dict]:
    """Upload one audio file and return its 0-based segments."""
    if backend == "groq":
        response = _post_whisper(GROQ_ENDPOINT, api_key, GROQ_MODEL, audio_path)
    elif backend == "openai":
        response = _post_whisper(OPENAI_ENDPOINT, api_key, OPENAI_MODEL, audio_path)
    else:
        raise SystemExit(f"Unknown whisper backend: {backend}")
    return _segments_from_response(response)


def _transcribe_local(audio_path: Path, options: dict | None = None) -> list[dict]:
    """Run the on-device backend. Imported here so faster-whisper stays optional."""
    try:
        import local_whisper
    except Exception as exc:  # pragma: no cover - import machinery failure
        raise SystemExit(f"Local whisper backend unavailable: {exc}") from exc

    opts = options or {}
    return local_whisper.transcribe_local(
        audio_path,
        model=opts.get("model") or None,
        device=opts.get("device") or None,
        compute_type=opts.get("compute") or None,
        language=opts.get("language") or None,
        # `or None` would collapse an explicit False into None, and None here
        # means "ask the environment" — the opposite of what the user configured.
        offline_mode=opts.get("offline"),
    )


def transcribe_video(
    video_path: str,
    audio_out: Path,
    backend: str | None = None,
    api_key: str | None = None,
    options: dict | None = None,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> tuple[list[dict], str]:
    """Run the full flow: extract audio → transcribe → parse segments.

    `options` carries local-backend settings (model/device/compute/language) and
    is ignored by the API backends. `start_seconds`/`end_seconds` restrict the
    work to one range; returned segment times are shifted back onto the original
    video's timeline, so callers see absolute timestamps either way.

    Returns (segments, backend_used). Raises SystemExit on any failure.
    """
    pinned = backend is not None
    if backend is None:
        backend, api_key = resolve_backend()
    elif backend != LOCAL_BACKEND and api_key is None:
        _, api_key = load_api_key(backend)

    if not backend:
        setup_py = Path(__file__).resolve().parent / "setup.py"
        ambient = None if pinned else env_key_backend()
        if ambient:
            raise SystemExit(
                f"No Whisper backend available. {ambient.upper()}_API_KEY is set in this "
                "environment, but an unpinned run does not upload audio on the strength "
                "of an environment variable alone — it may have been exported for "
                f"something else entirely. Set MOVIOLA_WHISPER={ambient} in "
                f"~/.config/moviola/.env (or pass --whisper {ambient}) to opt in, or "
                "`pip install \"faster-whisper>=1.0\"` to transcribe on this machine."
            )
        raise SystemExit(
            "No Whisper backend available. Either install the local backend "
            "(`pip install \"faster-whisper>=1.0\"`) for on-device transcription, or set "
            "GROQ_API_KEY / OPENAI_API_KEY in ~/.config/moviola/.env. Run "
            f"`python3 {setup_py}` to configure."
        )

    if backend != LOCAL_BACKEND and not api_key:
        raise SystemExit(
            f"No API key for the {backend} Whisper backend. Set "
            f"{backend.upper()}_API_KEY, or use --whisper local for on-device "
            "transcription."
        )

    offset = start_seconds if start_seconds and start_seconds > 0 else 0.0
    span = ""
    if start_seconds is not None or end_seconds is not None:
        span = f" [{offset:.0f}s–{end_seconds:.0f}s]" if end_seconds else f" [from {offset:.0f}s]"
    print(f"[moviola] extracting audio for Whisper ({backend}){span}…", file=sys.stderr)
    audio_path = extract_audio(video_path, audio_out, start_seconds, end_seconds)
    audio_bytes = audio_path.stat().st_size

    def transcribe_one(path: Path) -> list[dict]:
        return _transcribe_file(backend, api_key, path)

    if backend == LOCAL_BACKEND:
        # No chunking: the 24 MB split exists solely to stay under the APIs'
        # upload cap, which does not apply on-device. faster-whisper streams
        # long audio in 30-second windows itself, so a 3-hour file is fine.
        print(
            f"[moviola] audio: {audio_bytes / 1024:.0f} kB — transcribing on-device…",
            file=sys.stderr,
        )
        segments = _transcribe_local(audio_path, options)
    elif audio_bytes <= MAX_UPLOAD_BYTES:
        _announce_upload(backend, audio_bytes)
        segments = transcribe_one(audio_path)
    else:
        _announce_upload(backend, audio_bytes)
        duration = audio_duration(audio_path)
        plan = plan_chunks(duration, audio_bytes, MAX_UPLOAD_BYTES)
        print(
            f"[moviola] splitting into {len(plan)} chunks to stay under the "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload cap…",
            file=sys.stderr,
        )
        chunks = split_audio(audio_path, audio_out.parent / "chunks", plan)
        segments = transcribe_chunks(chunks, transcribe_one)

    if not segments:
        raise SystemExit("Whisper returned no transcript segments")

    # The clip starts at 0 as far as Whisper is concerned; put it back on the
    # video's timeline so timestamps line up with the extracted frames.
    if offset:
        segments = shift_segments(segments, offset)

    print(f"[moviola] transcribed {len(segments)} segments via {backend}", file=sys.stderr)
    return segments, backend


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: whisper.py <video-path> [<audio-out.mp3>] [--backend local|groq|openai]", file=sys.stderr)
        raise SystemExit(2)

    video = sys.argv[1]
    audio_out = Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else Path("audio.mp3")
    backend_override = None
    if "--backend" in sys.argv:
        backend_override = sys.argv[sys.argv.index("--backend") + 1]

    segments, backend = transcribe_video(video, audio_out, backend=backend_override)
    print(json.dumps({"backend": backend, "segments": segments}, indent=2))
