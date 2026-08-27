#!/usr/bin/env python3
"""Probe video metadata and extract frames at an auto-scaled fps.

Auto-fps targets a frame budget, not a fixed rate. Token cost scales with frame
count, so budget-by-duration keeps short videos dense and long videos capped.
When a user-specified range is passed, focused-mode budgets denser (they are
zooming in for detail).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# This module has a __main__ block, so it has to find its siblings when run
# directly as well as when moviola.py imports it. Same guarded insert whisper.py
# and setup.py use.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from untrusted import (  # noqa: E402
    finite_float,
    json_object,
    stderr_block,
    stderr_line,
)


MAX_FPS = 2.0
SCENE_THRESHOLD = 0.20
# Keep scene-detection results once we have at least this many distinct shots.
# Below this the video is effectively static (screen recording, talking head),
# so we fall back to uniform sampling. Matching the reference fork's behaviour,
# this is a low floor — NOT the frame budget — so normal videos with cuts use
# the (single-pass) scene engine instead of paying for a wasted second decode.
SCENE_MIN_FRAMES = 8
# Below this many decoded keyframes a clip is too sparse for keyframe coverage
# (very short or oddly encoded), so the cheap tier falls back to uniform.
KEYFRAME_MIN = 4
MAX_READ_DIMENSION = 1998
# Frame-delta dedup: downscale each frame to a DEDUP_THUMB x DEDUP_THUMB
# grayscale thumbnail and treat two frames as near-identical when their mean
# per-pixel difference (0-255) is at or below DEDUP_THRESHOLD. Conservative on
# purpose: only collapses frames that are visually the same shot, so a code diff
# / scrolling terminal / slide-gaining-a-bullet survives. Unlike a within-frame
# perceptual hash, this distinguishes flat frames (solid slides, fades) by luma.
DEDUP_THUMB = 16
DEDUP_THRESHOLD = 2.0
SHOWINFO_TS_RE = re.compile(r"pts_time:([0-9.]+)")

class FrameScheme:
    """The one place a frame filename's shape is written down.

    The writer and the sorter have to agree on this or a positional join
    misaligns, and until this existed the agreement was three copies of the
    literal `frame_%04d.jpg` and a regex that looked for digits anywhere. That
    held only because every caller empties the directory of its own output
    first, so there was exactly one scheme present — a property nothing
    enforced and nothing would have noticed losing.

    `%04d` sets a MINIMUM width, not a maximum: past frame 9999 ffmpeg writes
    five digits, so the pattern has to accept any width while still requiring
    that the digits are the WHOLE name after the prefix. `frame_a_0001.jpg`
    matching is the failure this exists to stop.
    """

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.glob = f"{prefix}*.jpg"
        self.template = f"{prefix}%04d.jpg"
        self._name_re = re.compile(rf"{re.escape(prefix)}(\d+)")

    def number(self, path: Path) -> int | None:
        """The frame number this scheme wrote into `path`, or None if it did not."""
        match = self._name_re.fullmatch(path.stem)
        return int(match.group(1)) if match else None


# The detail engines (uniform, scene, keyframe) all write one scheme, and the
# transcript-cue extractor writes another so the two can share a directory
# without either clobbering the other. They are separate constants rather than
# one parameterised call precisely so that the non-overlap is a fact about two
# named things instead of an argument passed correctly at four call sites.
DETAIL_FRAMES = FrameScheme("frame_")
CUE_FRAMES = FrameScheme("cue_")


# How many foreign names the disclosure spells out before it starts counting.
# The line exists to send someone to a specific file, and ten names does that;
# a thousand does not, and the caller does not control how many files somebody
# else put in the directory.
_MAX_LISTED = 10


def frames_in_order(out_dir: Path, scheme: FrameScheme | None = None) -> list[Path]:
    """The frames `scheme` wrote into `out_dir`, in the order ffmpeg wrote them.

    Lexicographic order is wrong here: `frame_10000.jpg` sorts between
    `frame_1000.jpg` and `frame_1001.jpg` (`.` is 0x2E, `0` is 0x30), and since
    every caller pairs frames with timestamps BY POSITION, from that point on
    each image carries somebody else's timestamp — silently, in a report that
    looks exactly as correct as any other. Uncapped scene detection on a long
    video reaches four figures easily.

    A name that matches the glob but not the scheme is EXCLUDED and named on
    stderr rather than sorted into a plausible slot. There is no information
    anywhere that says where `frame_a_0001.jpg` belongs in a sequence of
    `frame_%04d.jpg`, and a positional join given one more file than there are
    timestamps does not fail — it shifts, and then reports a misalignment
    warning about ffmpeg for a file ffmpeg never wrote.

    The `scheme` argument has no production caller today: every reader wants
    the detail frames, and the cue frames are write-only — `cue_*.jpg` is
    extracted for the report and never re-read in order. It is here because the
    NUMBER of schemes is what makes the prefix check meaningful: a function that
    hard-coded `DETAIL_FRAMES` would silently be a function about one scheme
    again, and the second scheme is the reason `frame_a_0001.jpg` has to be
    excluded rather than guessed at.

    NON-GOALS. This fixes the ORDER; it says nothing about whether ffmpeg's own
    showinfo timestamps are right, which is ffmpeg's business. It cannot see a
    collision INSIDE one scheme — `frame_1.jpg` and `frame_0001.jpg` both read
    as frame 1, and the filename breaks the tie so the order stays stable rather
    than reporting them. It never raises: a foreign file must not take down a
    run whose frames are all fine. And a frame this returns may still have NO
    timestamp reported for it — that is `pair_with_timestamps`' problem, and it
    drops such frames rather than inventing a time for them. It is also not
    the first thing to look at a foreign file: every caller sweeps the glob and
    unlinks what it matches before running ffmpeg, so the exclusion path here
    fires only for a name written BETWEEN that sweep and this read. The
    disclosure is therefore rare by construction and says nothing about what
    the sweep already deleted — which is a gap, and it is filed as one.
    """
    # Resolved here rather than as a default argument, which would bind the
    # object at DEFINITION time — the writer would read the name and the sorter
    # a snapshot of it, which is the disagreement this whole change exists to
    # remove, reintroduced by a Python default.
    scheme = scheme or DETAIL_FRAMES
    numbered: list[tuple[int, str, Path]] = []
    foreign: list[str] = []
    for path in out_dir.glob(scheme.glob):
        number = scheme.number(path)
        if number is None:
            foreign.append(path.name)
        else:
            numbered.append((number, path.name, path))

    if foreign:
        # Named, not counted: the point of the line is to send someone to the
        # specific file. The names come off a directory this program did not
        # necessarily fill, so they are fenced like any other untrusted value —
        # and capped, because the COUNT is theirs to choose too. `stderr_line`
        # bounds each name's shape and nothing bounds their number, so a
        # directory holding ten thousand matching names turned one warning into
        # ten thousand names of somebody else's text.
        shown = sorted(foreign)[:_MAX_LISTED]
        listed = ", ".join(stderr_line(name) for name in shown)
        if len(foreign) > _MAX_LISTED:
            listed += f", and {len(foreign) - _MAX_LISTED} more"
        print(
            f"[moviola] {len(foreign)} file(s) in {out_dir} match {scheme.glob} but not "
            f"the {scheme.prefix}NNNN.jpg scheme this run writes — excluded from the frame "
            f"order, because nothing says where they belong in it: {listed}",
            file=sys.stderr,
        )

    return [path for _number, _name, path in sorted(numbered, key=lambda item: item[:2])]



def pair_with_timestamps(
    files: list[Path],
    timestamps: list[float],
    reason: str,
    label: str,
) -> tuple[list[dict], int]:
    """Pair each frame with its own showinfo timestamp, dropping any it lacks.

    Both extraction engines carried the identical line
    `ts = timestamps[i] if i < len(timestamps) else offset`, so once showinfo's
    output ran short every remaining image was labelled with the START of the
    requested range. A plausible number in the right units is the worst kind of
    wrong answer: a report saying "at 0:00" for a frame from minute nine reads
    as ordinary output, and nothing downstream can tell it from one.

    There is no honest timestamp to substitute, so a frame without one is
    dropped and its file removed. The count comes back for the caller to
    disclose; leaving the file behind would be worse than useless, because
    `frames_in_order` globs the directory and the next thing to look would
    re-pair it by position.

    A SURPLUS of timestamps is not a shortfall and does not warn: passing
    `-frames:v` caps the files written while showinfo keeps reporting, and that
    is an ordinary capped run.
    """
    paired: list[dict] = []
    for i, path in enumerate(files):
        if i >= len(timestamps):
            try:
                path.unlink()
            except OSError:
                pass
            continue
        paired.append({
            "index": i,
            "timestamp_seconds": timestamps[i],
            "path": str(path),
            "reason": "first-frame" if (reason == "scene-change" and i == 0) else reason,
        })

    untimed = len(files) - len(paired)
    if untimed:
        print(
            f"[moviola] {label}: ffmpeg reported {len(timestamps)} timestamps for "
            f"{len(files)} frames — dropped {untimed} that could not be placed on "
            "the timeline. Frames that remain may also be misaligned if the "
            "missing reports were not the last ones.",
            file=sys.stderr,
        )
    return paired, untimed


def _scale_filter(resolution: int) -> str:
    return (
        f"scale=w='min({resolution},iw)':h='min({MAX_READ_DIMENSION},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )


def _clamp_fps(fps: float, duration_seconds: float, max_frames: int) -> tuple[float, int]:
    fps = min(fps, MAX_FPS)
    target = min(max_frames, max(1, int(round(fps * duration_seconds))))
    return fps, target


def parse_time(value: str | float | int | None) -> float | None:
    """Parse SS, MM:SS, or HH:MM:SS (with optional .ms) into seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    raise SystemExit(f"Cannot parse time value: {value!r} (expected SS, MM:SS, or HH:MM:SS)")


def format_time(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def get_metadata(video_path: str) -> dict:
    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is not installed. Install with: brew install ffmpeg")

    result = subprocess.run(
        [
            "ffprobe",
            # -v error, not -v quiet, for the same reason whisper.py:408 says
            # so: quiet silences ffprobe's stderr along with its info, so the
            # `result.stderr` fenced below on a non-zero exit was always empty
            # and this site rendered "(ffprobe wrote nothing to stderr)" no
            # matter what actually went wrong. stdout stays JSON either way —
            # measured byte-identical under both flags, on a successful run
            # and a failing one.
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(Path(video_path).resolve()),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            "ffprobe failed:\n"
            + stderr_block(result.stderr, source="ffprobe")
        )

    # ffprobe's stdout is a value this program did not write, and until this
    # guard it was read as though it were: `json.loads(...)` straight into
    # `.get()`. `json_object` answers None for all three ways that goes wrong —
    # text that is not JSON, valid JSON that is not an object, and a document
    # nested past the recursion limit — and this site treats None as fatal.
    #
    # Fatal rather than empty, because carrying on means a report stating a
    # duration of zero as a fact about a video nothing successfully probed. It
    # is the same call the returncode guard four lines above makes, for the same
    # reason: a probe answering in a format that is not its own is evidence
    # about what is on PATH, not evidence about the video.
    #
    # `or "{}"` predates this and survives it — a probe that exits 0 and writes
    # NOTHING is a different claim from one that writes something else, and only
    # the second is what changed here. `.strip()` puts whitespace-only output on
    # the "nothing" side of that line, where it belongs and where it also keeps
    # `stderr_block`'s empty-capture branch (which names stderr, not stdout)
    # unreachable from this call.
    data = json_object(result.stdout.strip() or "{}")
    if data is None:
        raise SystemExit(
            "ffprobe exited 0 but did not write a JSON object:\n"
            + stderr_block(result.stdout, source="ffprobe")
        )
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    # Nested rather than chained with `or`: an unparseable value is truthy, so
    # `fmt["duration"] or video_stream["duration"]` took "N/A" and never asked
    # the stream that knew. Falling through on "could not parse" is what the
    # fallback was for.
    duration = finite_float(
        fmt.get("duration"), finite_float(video_stream.get("duration"), 0.0)
    )
    return {
        "duration_seconds": duration,
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "codec": video_stream.get("codec_name"),
        # Same guard, same reason. A file size is never large enough for the
        # float round-trip to lose a byte: the first integer a float cannot
        # represent exactly is 9 petabytes.
        "size_bytes": int(finite_float(fmt.get("size"), 0.0)),
        "has_audio": audio_stream is not None,
    }


def auto_fps(duration_seconds: float, max_frames: int = 100) -> tuple[float, int]:
    """Pick fps that targets a sensible frame budget for full-video scans."""
    if duration_seconds <= 0:
        return 1.0, 1

    if duration_seconds <= 30:
        target = min(max_frames, max(12, int(round(duration_seconds))))
    elif duration_seconds <= 60:
        target = min(max_frames, 40)
    elif duration_seconds <= 180:  # 3 min
        target = min(max_frames, 60)
    elif duration_seconds <= 600:  # 10 min
        target = min(max_frames, 80)
    else:
        target = max_frames

    return _clamp_fps(target / duration_seconds, duration_seconds, max_frames)


def auto_fps_focus(duration_seconds: float, max_frames: int = 100) -> tuple[float, int]:
    """Denser budget for user-specified ranges — they are zooming in for detail."""
    if duration_seconds <= 0:
        return min(MAX_FPS, 2.0), 2

    if duration_seconds <= 5:
        target = min(max_frames, max(10, int(round(duration_seconds * 6))))
    elif duration_seconds <= 15:
        target = min(max_frames, max(30, int(round(duration_seconds * 4))))
    elif duration_seconds <= 30:
        target = min(max_frames, 60)
    elif duration_seconds <= 60:
        target = min(max_frames, 80)
    elif duration_seconds <= 180:
        target = max_frames
    else:
        target = max_frames

    return _clamp_fps(target / duration_seconds, duration_seconds, max_frames)


def extract(
    video_path: str,
    out_dir: Path,
    fps: float,
    resolution: int = 512,
    max_frames: int = 100,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> list[dict]:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    out_dir.mkdir(parents=True, exist_ok=True)
    for existing in out_dir.glob(DETAIL_FRAMES.glob):
        existing.unlink()

    output_pattern = str(out_dir / DETAIL_FRAMES.template)
    cmd: list[str] = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
    ]

    # -ss before -i = fast seek (keyframe-snap, good enough for preview frames).
    if start_seconds is not None:
        cmd += ["-ss", f"{start_seconds:.3f}"]
    if end_seconds is not None:
        cmd += ["-to", f"{end_seconds:.3f}"]

    cmd += [
        "-i", str(Path(video_path).resolve()),
        "-vf", f"fps={fps},{_scale_filter(resolution)}",
        "-frames:v", str(max_frames),
        "-q:v", "4",
        output_pattern,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            "ffmpeg frame extraction failed:\n"
            + stderr_block(result.stderr, source="ffmpeg")
        )

    offset = start_seconds or 0.0
    frames = frames_in_order(out_dir)
    return [
        {
            "index": i,
            "timestamp_seconds": round(offset + (i / fps if fps > 0 else 0.0), 2),
            "path": str(p),
            "reason": "uniform",
        }
        for i, p in enumerate(frames)
    ]


def extract_scene_candidates(
    video_path: str,
    out_dir: Path,
    resolution: int = 512,
    max_frames: int | None = 100,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    threshold: float = SCENE_THRESHOLD,
) -> tuple[list[dict], int]:
    """Extract first frame plus ffmpeg scene-change frames.

    When ``max_frames`` is set, ``-frames:v`` lets ffmpeg stop decoding once it
    has emitted that many frames (early exit) and avoids writing extras that we
    would only delete afterwards. ``None`` (uncapped "complete" detail) keeps
    every detected shot, as the user explicitly opted in.

    Returns (frames, untimed_dropped). The second value counts frames ffmpeg
    wrote but never reported a timestamp for; :func:`pair_with_timestamps`
    deletes those rather than inventing a time, and the count exists so the
    caller can say a shortfall happened.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    out_dir.mkdir(parents=True, exist_ok=True)
    for existing in out_dir.glob(DETAIL_FRAMES.glob):
        existing.unlink()

    output_pattern = str(out_dir / DETAIL_FRAMES.template)
    cmd: list[str] = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "info",
        "-y",
    ]
    if start_seconds is not None:
        cmd += ["-ss", f"{start_seconds:.3f}"]
    if end_seconds is not None:
        cmd += ["-to", f"{end_seconds:.3f}"]

    vf = f"select='eq(n\\,0)+gt(scene\\,{threshold})',{_scale_filter(resolution)},showinfo"
    cmd += [
        "-i", str(Path(video_path).resolve()),
        "-vf", vf,
        "-vsync", "vfr",
    ]
    if max_frames is not None:
        cmd += ["-frames:v", str(max_frames)]
    cmd += [
        "-q:v", "4",
        output_pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            "ffmpeg scene extraction failed:\n"
            + stderr_block(result.stderr, source="ffmpeg")
        )

    offset = start_seconds or 0.0
    timestamps = [round(offset + float(match.group(1)), 2) for match in SHOWINFO_TS_RE.finditer(result.stderr)]
    return pair_with_timestamps(
        frames_in_order(out_dir), timestamps, "scene-change", "scene detection"
    )


def _even_indices(count: int, n: int) -> list[int]:
    """Indices of ``n`` evenly-spaced items out of ``count`` (first + last kept).

    ``n >= count`` returns every index; ``n == 1`` returns just the first.
    """
    if n >= count:
        return list(range(count))
    if n <= 1:
        return [0]
    return [round(i * (count - 1) / (n - 1)) for i in range(n)]


def parse_timestamps(value: str | None) -> list[float]:
    """Parse a comma-separated list of times (SS, MM:SS, HH:MM:SS) into a
    sorted, de-duplicated list of seconds. Empty/blank tokens are skipped;
    an unparseable token raises (via :func:`parse_time`)."""
    if not value:
        return []
    out: list[float] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        seconds = parse_time(token)
        if seconds is not None:
            out.append(float(seconds))
    return sorted(set(out))


def merge_frames(primary: list[dict], pinned: list[dict]) -> list[dict]:
    """Combine two frame lists into one chronological list and reindex 0..n-1.

    ``pinned`` frames (transcript cues) are never dropped — this is a plain
    union, so the cap is enforced upstream by reserving budget for the cues.
    """
    merged = sorted([*primary, *pinned], key=lambda f: f["timestamp_seconds"])
    for i, frame in enumerate(merged):
        frame["index"] = i
    return merged


def extract_at_timestamps(
    video_path: str,
    out_dir: Path,
    timestamps: list[float],
    resolution: int = 512,
    max_frames: int | None = None,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> tuple[list[dict], dict]:
    """Grab exactly one frame at each requested timestamp (transcript cues).

    Timestamps are absolute source seconds. Any falling outside an active
    ``[start, end]`` focus window are dropped. Files use a ``cue_*.jpg`` prefix
    so they sit alongside detail-engine ``frame_*.jpg`` output without either
    clobbering the other. When more cues than ``max_frames`` survive, they are
    even-sampled (first + last kept) before extraction.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    out_dir.mkdir(parents=True, exist_ok=True)
    for existing in out_dir.glob(CUE_FRAMES.glob):
        existing.unlink()

    lo = start_seconds or 0.0
    hi = end_seconds if end_seconds is not None else float("inf")
    requested = sorted(set(round(float(t), 2) for t in timestamps))
    in_window = [t for t in requested if lo <= t <= hi]
    dropped = len(requested) - len(in_window)

    if max_frames is not None and len(in_window) > max_frames:
        points = [in_window[i] for i in _even_indices(len(in_window), max_frames)]
    else:
        points = in_window

    out: list[dict] = []
    for t in points:
        path = out_dir / (CUE_FRAMES.template % len(out))
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-ss", f"{t:.3f}",
            "-i", str(Path(video_path).resolve()),
            "-frames:v", "1",
            "-vf", _scale_filter(resolution),
            "-q:v", "4",
            str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and path.exists():
            out.append({
                "index": len(out),
                "timestamp_seconds": t,
                "path": str(path),
                "reason": "transcript-cue",
            })

    meta = {
        "engine": "timestamps",
        "candidate_count": len(requested),
        "selected_count": len(out),
        "dropped_out_of_window": dropped,
        "fallback": False,
    }
    return out, meta


def _even_sample(candidates: list[dict], n: int) -> list[dict]:
    """Pick ``n`` evenly-spaced candidates (always including first and last),
    delete the JPEGs we drop, and reindex the survivors 0..len-1.

    Shared by every capped engine so all detail modes sample the same way:
    detect all candidates across the full range, then thin down to the cap.
    ``n >= len(candidates)`` keeps everything (the uncapped / under-cap case).
    """
    selected = [candidates[i] for i in _even_indices(len(candidates), n)]

    keep_paths = {sel["path"] for sel in selected}
    for cand in candidates:
        if cand["path"] not in keep_paths:
            try:
                Path(cand["path"]).unlink()
            except OSError:
                pass
    for i, frame in enumerate(selected):
        frame["index"] = i
    return selected


def _frame_delta(a: bytes, b: bytes) -> float:
    """Mean absolute per-pixel difference (0-255) between two grayscale
    thumbnails. Mismatched lengths are treated as maximally different so a
    decode hiccup never collapses distinct frames."""
    if not a or len(a) != len(b):
        return float("inf")
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def _thumb_frames(paths: list[Path]) -> list[bytes]:
    """Decode every frame in ``paths`` to a small grayscale thumbnail via one
    ffmpeg pass over the JPEG sequence.

    ffmpeg does the pixel decode (keeps us pure-stdlib); we slice the raw
    grayscale stream into one ``DEDUP_THUMB``-square thumbnail per frame.
    Fail-open: any ffmpeg error, an unrecognized name, or a byte-count mismatch
    returns ``[]`` so the caller skips dedup rather than breaking extraction.
    """
    if not paths:
        return []
    paths = [Path(p) for p in paths]
    m = re.match(r"(.*?)(\d+)(\.[A-Za-z0-9]+)$", paths[0].name)
    if m is None:
        return []
    prefix, digits, ext = m.group(1), m.group(2), m.group(3)
    pattern = str(paths[0].parent / f"{prefix}%0{len(digits)}d{ext}")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-start_number", str(int(digits)),
        "-i", pattern,
        "-vf", f"scale={DEDUP_THUMB}:{DEDUP_THUMB},format=gray",
        "-f", "rawvideo",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        return []

    chunk = DEDUP_THUMB * DEDUP_THUMB
    data = result.stdout
    if len(data) != chunk * len(paths):
        return []
    return [data[i * chunk:(i + 1) * chunk] for i in range(len(paths))]


def dedupe_perceptual(
    candidates: list[dict], threshold: float = DEDUP_THRESHOLD
) -> tuple[list[dict], int]:
    """Drop near-identical frames from a chronological candidate list.

    Thumbnails the extracted JPEGs and greedily removes frames whose mean
    per-pixel difference from the last kept one is within ``threshold``. Returns
    ``(survivors, dropped_count)``; a no-op (unchanged list) when thumbnails are
    unavailable or there are fewer than two candidates.
    """
    if len(candidates) <= 1:
        return candidates, 0
    thumbs = _thumb_frames([Path(c["path"]) for c in candidates])
    return _dedupe_by_deltas(candidates, thumbs, threshold)


def _dedupe_by_deltas(
    candidates: list[dict], thumbs: list[bytes], threshold: float = DEDUP_THRESHOLD
) -> tuple[list[dict], int]:
    """Greedily drop frames within ``threshold`` mean per-pixel difference of the
    last *kept* frame. Deletes dropped JPEGs and reindexes survivors 0..n-1 (same
    cleanup contract as :func:`_even_sample`). Fail-open: if ``thumbs`` does not
    line up 1:1 with ``candidates``, return them unchanged.
    """
    if len(thumbs) != len(candidates) or len(candidates) <= 1:
        return candidates, 0

    kept = [candidates[0]]
    last = thumbs[0]
    dropped: list[dict] = []
    for cand, thumb in zip(candidates[1:], thumbs[1:]):
        if _frame_delta(thumb, last) <= threshold:
            dropped.append(cand)
        else:
            kept.append(cand)
            last = thumb

    for cand in dropped:
        try:
            Path(cand["path"]).unlink()
        except OSError:
            pass
    for i, frame in enumerate(kept):
        frame["index"] = i
    return kept, len(dropped)


def extract_scene_or_uniform(
    video_path: str,
    out_dir: Path,
    fps: float,
    target_frames: int,
    resolution: int = 512,
    max_frames: int | None = 100,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    dedup: bool = True,
) -> tuple[list[dict], dict]:
    """Prefer scene selection, falling back to uniform only when the video is
    effectively static (fewer than ``SCENE_MIN_FRAMES`` detected shots).

    Scene cuts are detected across the *whole* range (uncapped), near-identical
    frames are dropped (:func:`dedupe_perceptual`, unless ``dedup`` is False),
    and the survivors are even-sampled down to ``max_frames`` via
    :func:`_even_sample`, exactly like the keyframe engine. This costs a full
    decode, but it guarantees coverage spans the entire clip — capping detection
    with ``-frames:v`` instead would keep only the first ``max_frames`` cuts and
    drop the tail of long videos (and could even fall below ``SCENE_MIN_FRAMES``
    and misfire the uniform fallback on a cut-heavy clip).
    """
    scene_frames, untimed = extract_scene_candidates(
        video_path,
        out_dir,
        resolution=resolution,
        max_frames=None,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
    )
    scene_count = len(scene_frames)
    if scene_count >= SCENE_MIN_FRAMES:
        deduped, n_dropped = dedupe_perceptual(scene_frames) if dedup else (scene_frames, 0)
        cap = len(deduped) if max_frames is None else max_frames
        selected = _even_sample(deduped, cap)
        return selected, {
            "engine": "scene",
            "candidate_count": scene_count,
            "deduped_count": n_dropped,
            "selected_count": len(selected),
            "fallback": False,
            "untimed_dropped": untimed,
        }

    fallback_cap = target_frames if max_frames is None else min(max_frames, target_frames)
    frames = extract(
        video_path,
        out_dir,
        fps=fps,
        resolution=resolution,
        max_frames=fallback_cap,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
    )
    n_dropped = 0
    if dedup:
        frames, n_dropped = dedupe_perceptual(frames)
    return frames, {
        "engine": "uniform",
        "candidate_count": scene_count,
        "deduped_count": n_dropped,
        "selected_count": len(frames),
        "fallback": True,
        "fallback_from": "scene",
        # Deliberately NOT `untimed_dropped`. These frames came from `extract()`,
        # which re-extracts the range and times what it writes as
        # `offset + i / fps` — it never reads showinfo. So nothing the scene pass
        # failed to place survives into this output, and the sentence the other
        # key renders is false of every frame in it.
        #
        # The count still explains something, which is why it is carried rather
        # than dropped: the floor below is compared against the count AFTER
        # untimed frames are removed, so a pass that detected plenty of shots and
        # could not time some of them lands here — and "uniform fallback"
        # otherwise reads as "this video is static".
        "untimed_before_fallback": untimed,
        "untimed_caused_fallback": bool(untimed) and scene_count + untimed >= SCENE_MIN_FRAMES,
    }


def extract_keyframes(
    video_path: str,
    out_dir: Path,
    resolution: int = 512,
    max_frames: int | None = 50,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    dedup: bool = True,
) -> tuple[list[dict], dict]:
    """Decode only keyframes (I-frames) — the cheap, near-instant tier.

    ``-skip_frame nokey`` makes ffmpeg reconstruct only keyframes, skipping all
    P/B frames. Encoders emit keyframes at scene cuts, so these already
    approximate "distinct moments". Near-identical frames are dropped
    (:func:`dedupe_perceptual`, unless ``dedup`` is False); over-cap →
    even-sample first→last; too few keyframes → uniform fallback.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    out_dir.mkdir(parents=True, exist_ok=True)
    for existing in out_dir.glob(DETAIL_FRAMES.glob):
        existing.unlink()

    output_pattern = str(out_dir / DETAIL_FRAMES.template)
    cmd: list[str] = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "info",
        "-y",
    ]
    if start_seconds is not None:
        cmd += ["-ss", f"{start_seconds:.3f}"]
    if end_seconds is not None:
        cmd += ["-to", f"{end_seconds:.3f}"]
    cmd += [
        "-skip_frame", "nokey",
        "-i", str(Path(video_path).resolve()),
        "-vf", f"{_scale_filter(resolution)},showinfo",
        "-vsync", "vfr",
        "-q:v", "4",
        output_pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            "ffmpeg keyframe extraction failed:\n"
            + stderr_block(result.stderr, source="ffmpeg")
        )

    offset = start_seconds or 0.0
    timestamps = [round(offset + float(m.group(1)), 2) for m in SHOWINFO_TS_RE.finditer(result.stderr)]
    candidates, untimed = pair_with_timestamps(
        frames_in_order(out_dir), timestamps, "keyframe", "keyframe extraction"
    )

    # Too few keyframes → uniform fallback over the same range.
    if len(candidates) < KEYFRAME_MIN:
        for cand in candidates:
            try:
                Path(cand["path"]).unlink()
            except OSError:
                pass
        meta = get_metadata(video_path)
        full_duration = meta["duration_seconds"]
        eff_start = start_seconds or 0.0
        eff_end = end_seconds if end_seconds is not None else full_duration
        eff_duration = max(0.0, eff_end - eff_start)
        budget = max_frames if max_frames is not None else 100
        fps, _ = auto_fps(eff_duration, max_frames=budget)
        frames_out = extract(
            video_path,
            out_dir,
            fps=fps,
            resolution=resolution,
            max_frames=budget,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )
        n_dropped = 0
        if dedup:
            frames_out, n_dropped = dedupe_perceptual(frames_out)
        return frames_out, {
            "engine": "uniform",
            "candidate_count": len(candidates),
            "deduped_count": n_dropped,
            "selected_count": len(frames_out),
            "fallback": True,
            "fallback_from": "keyframe",
            # Same reasoning as the scene engine's uniform fallback above: the
            # re-extract wiped the untimed frames along with everything else, so
            # the count describes the pass that was discarded and not the output
            # being reported. It is kept because it can explain the fallback.
            "untimed_before_fallback": untimed,
            "untimed_caused_fallback": (
                bool(untimed) and len(candidates) + untimed >= KEYFRAME_MIN
            ),
        }

    # Detect-all, drop near-duplicates, then even-sample down to the cap (first +
    # last always kept). ``max_frames is None`` (uncapped) keeps every keyframe.
    candidate_count = len(candidates)
    deduped, n_dropped = dedupe_perceptual(candidates) if dedup else (candidates, 0)
    cap = len(deduped) if max_frames is None else max_frames
    selected = _even_sample(deduped, cap)
    return selected, {
        "engine": "keyframe",
        "candidate_count": candidate_count,
        "deduped_count": n_dropped,
        "selected_count": len(selected),
        "fallback": False,
        "untimed_dropped": untimed,
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "usage: frames.py <video-path> <out-dir> [--fps F] [--resolution W] "
            "[--max-frames N] [--start T] [--end T] [--no-dedup]",
            file=sys.stderr,
        )
        raise SystemExit(2)

    video = sys.argv[1]
    out = Path(sys.argv[2])
    args = sys.argv[3:]

    fps_override = None
    resolution = 512
    max_frames = 100
    start_arg = None
    end_arg = None
    dedup = True
    i = 0
    while i < len(args):
        if args[i] == "--fps":
            fps_override = float(args[i + 1]); i += 2
        elif args[i] == "--resolution":
            resolution = int(args[i + 1]); i += 2
        elif args[i] == "--max-frames":
            max_frames = int(args[i + 1]); i += 2
        elif args[i] == "--start":
            start_arg = args[i + 1]; i += 2
        elif args[i] == "--end":
            end_arg = args[i + 1]; i += 2
        elif args[i] == "--no-dedup":
            dedup = False; i += 1
        else:
            i += 1

    meta = get_metadata(video)
    start_sec = parse_time(start_arg)
    end_sec = parse_time(end_arg)
    full_duration = meta["duration_seconds"]

    effective_start = start_sec if start_sec is not None else 0.0
    effective_end = end_sec if end_sec is not None else full_duration
    effective_duration = max(0.0, effective_end - effective_start)

    focused = start_sec is not None or end_sec is not None
    if focused:
        fps, target = auto_fps_focus(effective_duration, max_frames=max_frames)
    else:
        fps, target = auto_fps(effective_duration, max_frames=max_frames)
    if fps_override is not None:
        fps = fps_override
        target = max(1, int(round(fps * effective_duration)))

    frames = extract(
        video, out,
        fps=fps,
        resolution=resolution,
        max_frames=max_frames,
        start_seconds=start_sec,
        end_seconds=end_sec,
    )
    deduped_count = 0
    if dedup:
        frames, deduped_count = dedupe_perceptual(frames)
    print(json.dumps(
        {
            "meta": meta, "fps": fps, "target": target, "focused": focused,
            "deduped_count": deduped_count, "frames": frames,
        },
        indent=2,
    ))
