#!/usr/bin/env python3
"""/moviola entry point: download video, extract frames, parse transcript.

Prints a markdown report to stdout listing frame paths + transcript. Claude
then Reads each frame path to see the video.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from config import DETAILS, WHISPER_BACKENDS, frame_cap, get_config  # noqa: E402
from download import download, fetch_captions, is_url  # noqa: E402
from frames import MAX_FPS, auto_fps, auto_fps_focus, extract_at_timestamps, extract_keyframes, extract_scene_or_uniform, format_time, get_metadata, merge_frames, parse_time, parse_timestamps  # noqa: E402
from transcribe import filter_range, format_transcript, parse_vtt  # noqa: E402
# Three names, three different reasons, and this comment did not account for the
# third until a review read it as the complete list it presents itself as.
# `stderr_line` is used below, by md_inline. `finite_float` is used below too, by
# `metadata_from_info` — the transcript-only path has no video to probe, so
# yt-dlp's `info.json` is the only thing that knows the duration, and it needs
# the same guard `frames.get_metadata` puts on ffprobe's. `balance_bidi` is re-exported and
# not used here: it was defined in this module until stderr needed it too, and
# `whisper` importing `moviola` would be a cycle, so it moved to a leaf module
# both sides import — a second copy is how the U+2028 fix reached one output
# channel and not the other. Its only reader through this name is the test that
# pins the two callers to one definition, so the re-export is test-facing rather
# than API; `LINE_BREAKS` was re-exported alongside it and had no reader at all.
from untrusted import balance_bidi, finite_float, stderr_line  # noqa: E402,F401
from whisper import (  # noqa: E402
    LOCAL_BACKEND,
    TranscriptGaps,
    env_key_backend,
    resolve_backend,
    transcribe_video,
)


def format_missing_ranges(gaps: TranscriptGaps | None) -> str:
    """The summary bullet's suffix when part of the transcript never arrived.

    Empty string on every complete run, which is the overwhelming majority of
    them — this must read as an exception, not as a field the report always has.
    """
    if not gaps or not gaps.failed:
        return ""
    spans = ", ".join(f"{format_time(s)}–{format_time(e)}" for s, e in gaps.ranges)
    return f" — **INCOMPLETE: {gaps.failed} of {gaps.total} audio chunks failed**, missing {spans}"


def gap_warning(gaps: TranscriptGaps) -> str:
    """The block-quote above the transcript itself.

    It names the specific misreading rather than only the fact: text either
    side of a dropped chunk closes over the hole, so the transcript LOOKS
    continuous across a span nothing ever transcribed. A reader who is told
    only "1 of 4 chunks failed" still has no reason to distrust what they read.
    """
    spans = ", ".join(f"{format_time(s)}–{format_time(e)}" for s, e in gaps.ranges)
    return (
        f"> **Warning:** {gaps.failed} of {gaps.total} audio chunks failed to "
        f"transcribe. Nothing below covers {spans}, and the surrounding text runs "
        "continuous across the gap — it does not read as truncated. Treat any "
        "claim about that span as unsupported."
    )


def resolve_whisper_choice(flag: str | None, configured: str) -> str | None:
    """Which backend the user pinned, or None for "let resolve_backend decide".

    --whisper wins over MOVIOLA_WHISPER, and "auto" on either side means no pin.
    `auto` used to be rejected by argparse, which left no way to undo a
    MOVIOLA_WHISPER=groq pin for a single run short of editing the config file or
    clearing the variable — so the flag that exists to override the config could
    override it in every direction except back to normal.
    """
    if flag:
        return None if flag == "auto" else flag
    return configured if configured and configured != "auto" else None


def metadata_from_info(info: dict | None) -> dict:
    """Stand-in metadata for the path with no video to probe.

    `--detail transcript` on a captioned URL never downloads the video, so there
    is nothing for ffprobe to look at and yt-dlp's `info.json` is the only thing
    that knows how long it is.

    The duration goes through `finite_float` for the same reason the probe's
    does — it is a number an extractor put in a JSON file, not one this program
    computed, and a bare `float()` on it turned an odd `info.json` into a dead
    run. Extracted from the expression it used to be so it can be tested without
    driving a download.

    This docstring used to say "everything ffprobe would have answered is None
    here, deliberately: the report says 'unknown', it does not guess." That is
    the intent and it is wrong about the code in three places, each of which a
    review found by reading the dict below against it:

      * `has_audio` is `False`, not None, and False is an assertion rather than
        an absence. A captioned URL whose video has audio is reported as having
        none. Nothing consumes the field on this path today, which is why it is
        a latent wrong answer rather than a live one.

      * `size_bytes` is not here at all. `frames.get_metadata` returns six keys
        and this returns five, so the two producers of `meta` do not agree on
        their shape — the divergence is pinned by
        `test_the_shape_matches_what_the_probe_returns` rather than left for a
        future consumer to discover as a KeyError.

      * An unknown duration is `0.0`, and `format_time` renders that as a
        confident `00:00`. The report does not say "unknown"; it says the video
        is zero seconds long, and `auto_fps(0)` then budgets exactly one frame.
        Filed in TODOS.md — the sentinel needs a value the formatter can tell
        apart from a real zero, which is a change to both sides.
    """
    return {
        "duration_seconds": finite_float((info or {}).get("duration"), 0.0),
        "width": None,
        "height": None,
        "codec": None,
        "has_audio": False,
    }


def _longest_backtick_run(text: str) -> int:
    """Length of the longest unbroken run of backticks in `text`. 0 if there are none."""
    best = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        best = max(best, run)
    return best


def md_inline(value: object) -> str:
    """Fence one untrusted short value as markdown inline code.

    This report is markdown that goes straight into an agent's context, and
    several values in it are authored by whoever made the video rather than by
    this program: yt-dlp's title and uploader, ffprobe's codec name. A title of
    "Tutorial`" followed by a newline and "## Ignore the above" otherwise renders
    as report structure, and nothing downstream can tell it from a heading this
    program wrote.

    Three edits, all structural. The first two are `stderr_line`'s and are
    shared with it: line breaks collapse to spaces, because a line break ends
    the list item the value sits in and lets everything after it become
    top-level markdown — all ten of them, not just the two that were
    demonstrated — and unclosed bidi scopes are closed. The third is this
    function's own, because it is the only one of the two that emits markdown:
    the value is wrapped in a backtick run one longer than the longest run
    inside it, padded with a space when it starts or ends with a backtick —
    CommonMark's own rule for putting backticks inside a code span.

    An empty value becomes a span containing one space rather than two adjacent
    backticks, which is not an empty code span at all: it is an unpaired
    backtick run that pairs with the next one in the document and swallows every
    line in between.

    Lossless everywhere else, on purpose: this is not a sanitizer and strips no
    character class. A title in Japanese, with emoji, with an apostrophe or with
    a stray angle bracket comes out as itself.

    NON-GOALS. It closes the STRUCTURAL channel only — a title that reads
    "ignore your previous instructions" is still perfectly legible text sitting
    in an agent's context, correctly fenced. It governs this one value and not
    the line it is interpolated into. And it protects the report, not the whole
    of stderr: `stderr_line` now fences the values this process interpolates
    there, but yt-dlp inherits the file descriptor and writes past both.
    """
    text = stderr_line(value)
    if not text:
        text = " "
    ticks = "`" * (_longest_backtick_run(text) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def md_fence(body: str) -> str:
    """The shortest code fence `body` cannot close early. Never shorter than three.

    A fenced block ends at the first line that is nothing but backticks, at least
    as long as the opening fence — so a transcript containing ``` escapes a
    three-backtick fence and the rest of it lands in the report body as markdown.
    Captions come from the remote video and Whisper text comes from its audio, so
    both are attacker-reachable on a hostile video.

    Deliberately over-approximate: it measures the longest backtick run anywhere
    in the body rather than looking for lines shaped like a closing fence. The
    cost of being wrong that way is a couple of extra backticks; the cost of the
    precise version being wrong is the injection this exists to stop.

    Two things this does NOT do, so the fence is not mistaken for safety. It
    closes the STRUCTURAL channel only: a transcript that says "ignore your
    previous instructions" is still perfectly legible text sitting in the agent's
    context, and no fence changes that. And it cannot see the frames at all —
    they enter the context as images, so text rendered inside a video frame is
    untouched by anything here.
    """
    return "`" * max(3, _longest_backtick_run(body) + 1)

def build_parser() -> argparse.ArgumentParser:
    """The CLI as a value, so what it accepts can be compared to the config.

    The choices lists here used to be string literals duplicating
    config.DETAILS and config.WHISPER_BACKENDS, with nothing comparing the two.
    Adding a backend to the config left the flag rejecting it, and argparse's
    error reads as "that backend does not exist" rather than "that flag is
    stale". Building the parser separately from running it is what lets a test
    hold the two sets up against each other.
    """
    ap = argparse.ArgumentParser(
        prog="moviola",
        description="Download a video, extract auto-scaled frames, and surface the transcript.",
    )
    ap.add_argument("source", help="Video URL or local file path")
    ap.add_argument("--max-frames", type=int, default=None, help="Override frame cap")
    ap.add_argument("--resolution", type=int, default=512, help="Frame width in pixels (default 512)")
    ap.add_argument("--fps", type=float, default=None, help="Override auto-fps")
    ap.add_argument(
        "--detail",
        choices=list(DETAILS),
        default=None,
        help="Fidelity/speed dial: transcript (no frames), efficient (fast keyframes, cap 50), "
             "balanced (scene, cap 100), token-burner (scene, uncapped).",
    )
    ap.add_argument(
        "--timestamps",
        type=str,
        default=None,
        help="Comma-separated absolute timestamps (SS, MM:SS, HH:MM:SS) to grab a frame at, "
             "e.g. transcript-flagged 'look here' moments. Added on top of the detail frames "
             "(reserved against the cap); with --detail transcript these become the only frames.",
    )
    ap.add_argument("--start", type=str, default=None, help="Range start (SS, MM:SS, or HH:MM:SS)")
    ap.add_argument("--end", type=str, default=None, help="Range end (SS, MM:SS, or HH:MM:SS)")
    ap.add_argument("--out-dir", type=str, default=None, help="Working directory (default: tmp)")
    ap.add_argument(
        "--no-whisper",
        action="store_true",
        help="Disable Whisper fallback. Report frames-only if no captions available.",
    )
    ap.add_argument(
        "--whisper",
        choices=list(WHISPER_BACKENDS),
        default=None,
        help="Force a specific Whisper backend. 'local' runs faster-whisper on "
             "this machine and needs no API key. 'auto' is the default and is "
             "also the way to undo a MOVIOLA_WHISPER pin for one run: local when "
             "faster-whisper is importable, else an API key.",
    )
    ap.add_argument(
        "--no-dedup",
        action="store_true",
        help="Disable near-duplicate frame removal. Keeps visually identical "
             "frames (static screen recordings, held slides) instead of collapsing them.",
    )
    return ap


def main() -> int:
    args = build_parser().parse_args()

    config = get_config()
    detail = args.detail or str(config["detail"])
    whisper_choice = resolve_whisper_choice(args.whisper, str(config["whisper"]))
    whisper_options = {
        "model": config["whisper_model"],
        "device": config["whisper_device"],
        "compute": config["whisper_compute"],
        "language": config["whisper_language"],
        "offline": config["whisper_offline"],
    }
    configured_cap = frame_cap(detail)
    if args.max_frames is not None:
        max_frames = args.max_frames
    else:
        max_frames = configured_cap
    if max_frames is not None and max_frames < 1:
        raise SystemExit("--max-frames must be greater than zero")
    budget_cap = max_frames if max_frames is not None else 100
    cue_timestamps = parse_timestamps(args.timestamps)

    if args.out_dir:
        work = Path(args.out_dir).expanduser().resolve()
    else:
        work = Path(tempfile.mkdtemp(prefix="moviola-"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"[moviola] working dir: {work}", file=sys.stderr)

    url_source = is_url(args.source)
    dl: dict = {"subtitle_path": None, "info": {}, "downloaded": False}
    transcript_segments: list[dict] = []
    transcript_text: str | None = None
    transcript_source: str | None = None
    transcript_gaps: TranscriptGaps | None = None
    video_path: str | None = None

    if url_source:
        print("[moviola] checking metadata/captions via yt-dlp…", file=sys.stderr)
        dl = fetch_captions(args.source, work / "download")
        if dl.get("subtitle_path"):
            try:
                transcript_segments = parse_vtt(dl["subtitle_path"])
                transcript_text = format_transcript(transcript_segments)
                transcript_source = "captions"
            except Exception as exc:
                print(f"[moviola] subtitle parse failed: {exc}", file=sys.stderr)
                transcript_segments = []

    # --timestamps needs the video for frame grabs, so it overrides the
    # transcript-mode download skip (and forces a full, not audio-only, fetch).
    audio_only = detail == "transcript" and not cue_timestamps
    if detail == "transcript" and transcript_segments and not cue_timestamps:
        video_path = None
    else:
        if url_source:
            print(
                "[moviola] downloading audio via yt-dlp…" if audio_only
                else "[moviola] downloading video via yt-dlp…",
                file=sys.stderr,
            )
            dl = download(
                args.source,
                work / "download",
                audio_only=audio_only,
            )
        else:
            print("[moviola] using local file…", file=sys.stderr)
            dl = download(args.source, work / "download")
        video_path = dl["video_path"]

    meta = get_metadata(video_path) if video_path else metadata_from_info(dl.get("info"))
    full_duration = meta["duration_seconds"]

    start_sec = parse_time(args.start)
    end_sec = parse_time(args.end)

    if start_sec is not None and start_sec < 0:
        raise SystemExit("--start must be non-negative")
    # Compare against the EFFECTIVE start, not only an explicit one. Requiring
    # start_sec to be set skipped the check entirely for `--end 0` and
    # `--end -5`, which then reached ffmpeg and failed with "-to value smaller
    # than -ss" — a message about flags the user never typed.
    if end_sec is not None and end_sec <= (start_sec or 0.0):
        raise SystemExit(
            f"--end must be greater than --start ({start_sec:.1f}s)" if start_sec
            else "--end must be greater than 0"
        )
    if full_duration > 0 and start_sec is not None and start_sec >= full_duration:
        raise SystemExit(f"--start {start_sec:.1f}s is past end of video ({full_duration:.1f}s)")

    effective_start = start_sec if start_sec is not None else 0.0
    effective_end = end_sec if end_sec is not None else full_duration
    effective_duration = max(0.0, effective_end - effective_start)
    focused = start_sec is not None or end_sec is not None

    if focused:
        fps, target = auto_fps_focus(effective_duration, max_frames=budget_cap)
    else:
        fps, target = auto_fps(effective_duration, max_frames=budget_cap)
    if args.fps is not None:
        fps = min(args.fps, MAX_FPS)
        target = max(1, int(round(fps * effective_duration)))

    if transcript_segments and focused:
        transcript_segments = filter_range(transcript_segments, start_sec, end_sec)
        transcript_text = format_transcript(transcript_segments)

    scope = (
        f"{format_time(effective_start)}-{format_time(effective_end)} ({effective_duration:.1f}s)"
        if focused else f"full {effective_duration:.1f}s"
    )
    frames: list[dict] = []
    frame_meta: dict = {"engine": "none", "candidate_count": 0, "selected_count": 0, "fallback": False}
    cue_frames: list[dict] = []
    cue_meta: dict = {}

    # Transcript cues are pinned: extracted first and counted against the cap so
    # the detail engine never evicts the moments the user explicitly asked for.
    if cue_timestamps and video_path:
        cue_frames, cue_meta = extract_at_timestamps(
            video_path,
            work / "frames",
            cue_timestamps,
            resolution=args.resolution,
            max_frames=max_frames,
            start_seconds=start_sec,
            end_seconds=end_sec,
        )
        if cue_meta.get("dropped_out_of_window"):
            print(
                f"[moviola] {cue_meta['dropped_out_of_window']} cue timestamp(s) outside the "
                "focus range — dropped",
                file=sys.stderr,
            )

    detail_budget = max_frames if max_frames is None else max(0, max_frames - len(cue_frames))
    if detail != "transcript" and video_path and detail_budget != 0:
        cap_label = "unlimited" if detail_budget is None else str(detail_budget)
        engine_label = "keyframes" if detail == "efficient" else "scene-aware frames"
        print(
            f"[moviola] extracting {engine_label} over {scope} "
            f"(target {target}, cap {cap_label})…",
            file=sys.stderr,
        )
        if detail == "efficient":
            frames, frame_meta = extract_keyframes(
                video_path,
                work / "frames",
                resolution=args.resolution,
                max_frames=detail_budget,
                start_seconds=start_sec,
                end_seconds=end_sec,
                dedup=not args.no_dedup,
            )
        else:  # balanced, token-burner
            frames, frame_meta = extract_scene_or_uniform(
                video_path,
                work / "frames",
                fps=fps,
                target_frames=target,
                resolution=args.resolution,
                max_frames=detail_budget,
                start_seconds=start_sec,
                end_seconds=end_sec,
                dedup=not args.no_dedup,
            )

    if cue_frames:
        frames = merge_frames(frames, cue_frames)

    if not transcript_segments and dl.get("subtitle_path"):
        try:
            all_segments = parse_vtt(dl["subtitle_path"])
            transcript_segments = filter_range(all_segments, start_sec, end_sec) if focused else all_segments
            transcript_text = format_transcript(transcript_segments)
            transcript_source = "captions"
        except Exception as exc:
            print(f"[moviola] subtitle parse failed: {exc}", file=sys.stderr)

    if not transcript_segments and not args.no_whisper and video_path and meta.get("has_audio"):
        backend, api_key = resolve_backend(whisper_choice)
        # The local backend needs no key — only the API backends gate on one.
        if backend and (backend == LOCAL_BACKEND or api_key):
            try:
                # Pass the range down rather than transcribing the whole video
                # and discarding most of it: on the local backend that waste is
                # minutes of compute, not a throwaway API call.
                all_segments, used_backend, transcript_gaps = transcribe_video(
                    video_path,
                    work / "audio.mp3",
                    backend=backend,
                    api_key=api_key,
                    options=whisper_options,
                    start_seconds=start_sec,
                    end_seconds=end_sec,
                )
                # filter_range still runs: Whisper can emit a segment that
                # straddles the boundary of the clip it was handed.
                transcript_segments = filter_range(all_segments, start_sec, end_sec) if focused else all_segments
                transcript_text = format_transcript(transcript_segments)
                transcript_source = f"whisper ({used_backend})"
            except SystemExit as exc:
                print(f"[moviola] whisper fallback failed: {exc}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                # The transcript is optional; the report is not. Frames are
                # already extracted and written by the time this runs, and an
                # unexpected exception here used to take them down with it —
                # a raw traceback and nothing on stdout at all. KeyboardInterrupt
                # is not an Exception, so Ctrl-C still ends the run.
                print(
                    f"[moviola] whisper fallback failed "
                    f"({type(exc).__name__}: {exc}) — continuing without a transcript",
                    file=sys.stderr,
                )
        else:
            setup_py = SCRIPT_DIR / "setup.py"
            if whisper_choice == LOCAL_BACKEND:
                hint = ("whisper backend 'local' was requested but faster-whisper is not "
                        "installed — run `pip install \"faster-whisper>=1.0\"`")
            elif whisper_choice:
                hint = (f"whisper backend '{whisper_choice}' was requested but its API key "
                        f"is missing — run `python3 {setup_py}` to set one")
            else:
                ambient = env_key_backend()
                if ambient:
                    hint = (
                        f"no subtitles, and {ambient.upper()}_API_KEY is set in this "
                        "environment but an unpinned run will not upload audio on the "
                        "strength of an environment variable alone — set "
                        f"MOVIOLA_WHISPER={ambient} in ~/.config/moviola/.env or pass "
                        f"`--whisper {ambient}` to opt in, or `pip install "
                        "\"faster-whisper>=1.0\"` to transcribe on-device")
                else:
                    hint = ("no subtitles and no transcription backend — run "
                            "`pip install \"faster-whisper>=1.0\"` to transcribe on-device, or "
                            f"`python3 {setup_py}` to set an API key")
            print(f"[moviola] {hint}", file=sys.stderr)
    elif not transcript_segments and video_path and not meta.get("has_audio"):
        print("[moviola] no audio stream found — proceeding without transcription", file=sys.stderr)

    info = dl.get("info") or {}

    print()
    print("# moviola: video report")
    print()
    print(f"- **Source:** {md_inline(args.source)}")
    if info.get("title"):
        print(f"- **Title:** {md_inline(info['title'])}")
    if info.get("uploader"):
        print(f"- **Uploader:** {md_inline(info['uploader'])}")
    print(f"- **Duration:** {format_time(full_duration)} ({full_duration:.1f}s)")
    if focused:
        print(
            f"- **Focus range:** {format_time(effective_start)} → {format_time(effective_end)} "
            f"({effective_duration:.1f}s)"
        )
    if meta.get("width") and meta.get("height"):
        codec = md_inline(meta["codec"]) if meta.get("codec") else "unknown codec"
        print(f"- **Resolution:** {meta['width']}x{meta['height']} ({codec})")
    range_mode = "focused" if focused else "full"
    print(f"- **Detail:** {detail}")
    detail_count = frame_meta.get("selected_count", 0)
    if detail != "transcript":
        cap_label = "unlimited" if detail_budget is None else str(detail_budget)
        engine = frame_meta.get("engine", "scene")
        fallback = " with uniform fallback" if frame_meta.get("fallback") else ""
        deduped = frame_meta.get("deduped_count", 0)
        # Not a tuning note like the dedup count beside it: a shortfall here
        # means ffmpeg told us about fewer frames than it wrote, so the frames
        # it did not account for were discarded rather than mislabelled — and
        # the ones that remain are only as aligned as the reports that arrived.
        untimed = frame_meta.get("untimed_dropped", 0)
        untimed_note = (
            f" — **{untimed} dropped without a timestamp from ffmpeg**; "
            "remaining timestamps may be misaligned"
            if untimed else ""
        )
        dedup_note = f", {deduped} near-duplicate{'s' if deduped != 1 else ''} dropped" if deduped else ""
        print(
            f"- **Frames:** {detail_count} selected from {frame_meta.get('candidate_count', detail_count)} "
            f"candidates ({engine}{fallback}{dedup_note}, {range_mode} range, budget {target}, cap {cap_label})"
            f"{untimed_note}"
        )
    elif not cue_frames:
        print("- **Frames:** skipped (transcript detail)")
    if cue_frames:
        dropped = cue_meta.get("dropped_out_of_window", 0)
        drop_note = f", {dropped} dropped outside range" if dropped else ""
        print(
            f"- **Cue frames:** {len(cue_frames)} at transcript-flagged timestamps "
            f"(transcript-cue{drop_note})"
        )
    if frames:
        print(f"- **Frame size:** max {args.resolution}px wide, max 1998px tall")
    if transcript_segments:
        in_range = " in range" if focused else ""
        missing = format_missing_ranges(transcript_gaps)
        print(
            f"- **Transcript:** {len(transcript_segments)} segments{in_range} "
            f"(via {transcript_source or 'captions'}){missing}"
        )
    else:
        print("- **Transcript:** none available")

    if detail == "token-burner" and len(frames) > 250:
        print()
        print(
            f"> **Warning:** token-burner detail selected {len(frames)} frames. "
            "This may use a large number of image tokens."
        )

    if not focused and full_duration > 600 and detail not in ("transcript", "token-burner"):
        mins = int(full_duration // 60)
        print()
        print(
            f"> **Warning:** This is a {mins}-minute video. Frame coverage is sparse at this length "
            f"under `{detail}` detail — its cap spreads thin across the full clip. For better results, "
            "re-run with `--start HH:MM:SS --end HH:MM:SS` to zoom into a section, or use "
            "`--detail token-burner` to keep every scene-change frame across the whole video."
        )

    print()
    print("## Frames")
    print()
    if frames:
        print(f"Frames live at: `{work / 'frames'}`")
        print()
        print(
            "**Read each frame path below with the Read tool to view the image.** "
            "Frames are in chronological order; `t=MM:SS` is the absolute timestamp in the source video."
        )
        print()
        for frame in frames:
            print(
                f"- `{frame['path']}` "
                f"(t={format_time(frame['timestamp_seconds'])}, reason={frame.get('reason', 'selected')})"
            )
    else:
        print("_No frames extracted._")

    print()
    print("## Transcript")
    print()
    if transcript_text:
        label = transcript_source or "captions"
        if transcript_gaps and transcript_gaps.failed:
            print(gap_warning(transcript_gaps))
            print()
        if focused:
            print(f"_Source: {label}. Filtered to {format_time(effective_start)} → {format_time(effective_end)}:_")
        else:
            print(f"_Source: {label}._")
        print()
        fence = md_fence(transcript_text)
        print(fence)
        print(transcript_text)
        print(fence)
    elif detail == "transcript":
        print(
            "_No transcript available at transcript detail. Captions were missing and Whisper was "
            "unavailable or failed, so there is no visual fallback here. Re-run with "
            "`--detail balanced` for frames._"
        )
    elif focused and dl.get("subtitle_path"):
        print(f"_No transcript lines fell inside {format_time(effective_start)} → {format_time(effective_end)}._")
    else:
        setup_py = SCRIPT_DIR / "setup.py"
        print(
            "_No transcript available — proceed with frames only. "
            "Captions were missing and no Whisper backend ran: either "
            "`--no-whisper` was used, or faster-whisper is not installed and no "
            "API key is set, or `MOVIOLA_WHISPER` pins a backend that is not "
            "usable here. "
            f"Run `python3 {setup_py}` to see which, and to enable Whisper._"
        )

    print()
    print("---")
    print(f"_Work dir: `{work}` — delete when done._")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
