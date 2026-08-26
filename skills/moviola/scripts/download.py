#!/usr/bin/env python3
"""Download a video via yt-dlp, or resolve a local file path.

Also fetches subtitles (manual first, then auto-generated) in VTT format so
transcribe.py can parse them without needing Whisper.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}

# The two yt-dlp format selectors, named because they are a policy rather than
# an implementation detail: a slash-separated fallback ladder, tried left to
# right, that decides how big a download is allowed to get.
#
# The tail used to be `bv*+ba/b` — BEST video, no bound — so a 4K-only upload
# fell through both bounded rungs and downloaded at 4K, on the branch whose
# whole purpose is staying small. `wv*`/`w` ask for the worst rendition
# instead, which is the smallest the ladder offers by yt-dlp's default sort.
#
# They carry no height bound of their own, and deliberately so: a bounded tail
# matches NOTHING on a ladder whose smallest rendition is above the bound, and
# a yt-dlp selector that matches nothing fails the download outright rather
# than falling back. `wv*`/`w` match everything the old tail matched.
#
# The middle pair exists because `[height<=720]` DROPS a format whose height is
# unknown rather than keeping it. HLS manifests with no RESOLUTION attribute
# and the generic extractor produce exactly those, so without a tolerant rung
# such a source skipped both bounds and hit the tail EVERY time — and with no
# height to bound it, the tail has no floor: it took the smallest rendition on
# offer, which is a downgrade rather than a saving. `[height<=?720]` is the
# unknown-tolerant form and keeps those formats, so the ladder falls back to
# them at their best before it ever reaches the worst-selectors.
#
# That tolerant `b[height<=?720]` rung is also what handles a source with no
# video at all: audio formats have no height, so it keeps them, and yt-dlp's
# incomplete-formats fallback then resolves it to the BEST audio. Without it a
# bare `w` matched instead and took the worst — and on that path the audio IS
# the transcript, so it is not a size trade, just a worse transcript.
#
# Audio stays `ba` wherever it is selected separately: the transcript is made
# from it, and it is not the expensive half.
VIDEO_FORMAT = (
    "bv*[height<=720]+ba/b[height<=720]"
    "/bv*[height<=?720]+ba/b[height<=?720]"
    "/wv*+ba/w"
)
# `ba`, not `ba/bestaudio`: `bestaudio` is the long form of the same selector,
# so the second rung could never fire on a ladder where the first did not.
AUDIO_FORMAT = "ba"


def is_url(source: str) -> bool:
    if source.startswith("-"):
        return False
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def resolve_local(path: str) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise SystemExit(f"File not found: {p}")
    if p.suffix.lower() not in VIDEO_EXTS:
        print(
            f"[moviola] warning: {p.suffix} is not a known video extension, proceeding anyway",
            file=sys.stderr,
        )
    return {
        "video_path": str(p),
        "subtitle_path": None,
        "info": {"title": p.name, "url": str(p)},
        "downloaded": False,
    }


def snapshot_dir(out_dir: Path) -> dict[str, tuple[int, int]]:
    """What `out_dir` holds right now, so a later pick can tell new from stale.

    `--out-dir` is a documented flag and the skill tells the agent to reuse the
    directory, so "a file named video.* is in there" has never meant "this run
    downloaded it". A run whose download failed outright picked up the PREVIOUS
    run's video and reported on it: right filename, wrong film, no error
    anywhere. Recording (mtime, size) per name before yt-dlp starts is what
    makes the difference visible.

    NON-GOALS. It cannot tell a re-download of the SAME video from a stale copy
    of it, and does not need to — either way the file answers the URL that was
    asked for. It compares mtime and size, so a byte-identical rewrite at an
    identical mtime reads as stale. And it says nothing about a file another
    process writes into the directory while yt-dlp is running, which is the
    separate problem of two runs sharing one work directory.
    """
    snapshot: dict[str, tuple[int, int]] = {}
    try:
        entries = list(out_dir.iterdir())
    except OSError:
        return snapshot
    for entry in entries:
        try:
            stat = entry.stat()
        except OSError:
            continue
        snapshot[entry.name] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _is_from_this_run(path: Path, before: dict[str, tuple[int, int]]) -> bool:
    """True if `path` did not exist before this run, or changed during it."""
    prior = before.get(path.name)
    if prior is None:
        return True
    try:
        stat = path.stat()
    except OSError:
        return False
    return (stat.st_mtime_ns, stat.st_size) != prior


def _pick_subtitle(out_dir: Path, before: dict[str, tuple[int, int]]) -> Path | None:
    candidates = [c for c in sorted(out_dir.glob("video*.vtt"))
                  if _is_from_this_run(c, before)]
    if not candidates:
        return None
    preferred = [
        c for c in candidates
        if any(marker in c.name for marker in (".en.", ".en-US.", ".en-GB.", ".en-orig."))
    ]
    return preferred[0] if preferred else candidates[0]


def _pick_video(out_dir: Path, before: dict[str, tuple[int, int]]) -> Path | None:
    for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".opus"):
        for candidate in sorted(out_dir.glob(f"video*{ext}")):
            if _is_from_this_run(candidate, before):
                return candidate
    for candidate in sorted(out_dir.glob("video.*")):
        if candidate.suffix.lower() in VIDEO_EXTS and _is_from_this_run(candidate, before):
            return candidate
    return None


def fetch_captions(url: str, out_dir: Path) -> dict:
    """Fetch metadata and best available VTT captions without downloading video."""
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed. Install with: brew install yt-dlp")

    out_dir.mkdir(parents=True, exist_ok=True)
    before = snapshot_dir(out_dir)
    output_template = str(out_dir / "video.%(ext)s")
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "en.*",
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-playlist",
        "--ignore-errors",
        "-o", output_template,
        "--",
        url,
    ]
    result = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    subtitle = _pick_subtitle(out_dir, before)
    info = _read_info(out_dir / "video.info.json", url, before)
    if result.returncode != 0 and subtitle is None:
        # Having no captions is an ordinary outcome, not an error. Having none
        # AFTER a non-zero exit is a different thing, and the two used to be
        # reported identically — as silence — so a rate-limited caption fetch
        # looked exactly like a video that has none, and the caller went on to
        # pay for a transcript.
        print(
            f"[moviola] yt-dlp exited {result.returncode} and produced no captions "
            "— treating this video as having none",
            file=sys.stderr,
        )
    return {
        "video_path": None,
        "subtitle_path": str(subtitle) if subtitle else None,
        "info": info or {"url": url},
        "downloaded": False,
    }


def _read_info(info_path: Path, url: str, before: dict[str, tuple[int, int]]) -> dict:
    """Read this run's info.json. A leftover one describes a different video."""
    info: dict = {}
    if info_path.exists() and _is_from_this_run(info_path, before):
        try:
            raw = json.loads(info_path.read_text(encoding="utf-8"))
            info = {
                "title": raw.get("title"),
                "uploader": raw.get("uploader") or raw.get("channel"),
                "duration": raw.get("duration"),
                "url": raw.get("webpage_url") or url,
            }
        except Exception as exc:
            print(f"[moviola] info.json parse failed: {exc}", file=sys.stderr)
            info = {"url": url}
    return info


def download_url(
    url: str,
    out_dir: Path,
    audio_only: bool = False,
) -> dict:
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed. Install with: brew install yt-dlp")

    out_dir.mkdir(parents=True, exist_ok=True)
    before = snapshot_dir(out_dir)
    output_template = str(out_dir / "video.%(ext)s")

    fmt = AUDIO_FORMAT if audio_only else VIDEO_FORMAT
    cmd = [
        "yt-dlp",
        "-N", "8",
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "en.*",
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-playlist",
        "--ignore-errors",
        "-o", output_template,
        "--",
        url,
    ]

    # yt-dlp may exit non-zero if a subtitle variant fails (e.g. 429) even when
    # the video itself downloaded fine, so a non-zero exit is not by itself a
    # failure. What it cannot mean is "use whatever file happens to be lying
    # here": the test is a video file THIS RUN produced, which is what `before`
    # is for.
    result = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    video = _pick_video(out_dir, before)
    if video is None:
        raise SystemExit(
            f"yt-dlp did not produce a video file in {out_dir} (exit {result.returncode})"
        )

    if result.returncode != 0:
        # Succeeding quietly after a partial failure is how a report ends up
        # missing its transcript with nothing anywhere saying why.
        print(
            f"[moviola] yt-dlp exited {result.returncode} but produced a video "
            "— continuing; captions or metadata may be missing",
            file=sys.stderr,
        )

    subtitle = _pick_subtitle(out_dir, before)
    info = _read_info(out_dir / "video.info.json", url, before)

    return {
        "video_path": str(video),
        "subtitle_path": str(subtitle) if subtitle else None,
        "info": info or {"url": url},
        "downloaded": True,
    }


def download(
    source: str,
    out_dir: Path,
    audio_only: bool = False,
) -> dict:
    if is_url(source):
        return download_url(source, out_dir, audio_only=audio_only)
    return resolve_local(source)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: download.py <url-or-path> <out-dir>", file=sys.stderr)
        raise SystemExit(2)
    result = download(sys.argv[1], Path(sys.argv[2]))
    print(json.dumps(result, indent=2))
