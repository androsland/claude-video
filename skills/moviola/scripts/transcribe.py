#!/usr/bin/env python3
"""Parse a WebVTT subtitle file into a clean, timestamped transcript.

YouTube auto-subs emit rolling-duplicate cues (each line appears 2-3 times as it
scrolls). We dedupe consecutive identical cues and merge their time ranges.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


# WebVTT's hours component is OPTIONAL, and when present it is "one or more"
# digits, not two: `01:30.000` and `100:00:00.000` are both spec-legal. The old
# pattern demanded exactly two, so a file of either shape parsed to zero
# segments — and zero segments is what "this video has no captions" looks like
# at every call site, so moviola uploaded the audio and paid for a transcript
# that was already on disk. `_CUE_TS` is written once and used for both ends.
_CUE_TS = r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})"
TS_RE = re.compile(rf"{_CUE_TS}\s+-->\s+{_CUE_TS}")
TAG_RE = re.compile(r"<[^>]+>")


def _to_seconds(h: str | None, m: str, s: str, ms: str) -> float:
    """Seconds from a cue timestamp. `h` is None when the file omits hours."""
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(path: str) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    segments: list[dict] = []
    i = 0
    while i < len(lines):
        match = TS_RE.match(lines[i])
        if not match:
            i += 1
            continue

        start = _to_seconds(*match.groups()[:4])
        end = _to_seconds(*match.groups()[4:])
        i += 1

        cue_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            cleaned = TAG_RE.sub("", lines[i]).strip()
            if cleaned:
                cue_lines.append(cleaned)
            i += 1

        cue_text = " ".join(cue_lines).strip()
        if cue_text:
            segments.append({"start": round(start, 2), "end": round(end, 2), "text": cue_text})
        i += 1

    if not segments:
        # A subtitle file that yields nothing is indistinguishable from no
        # subtitle file at all by the time this returns, and the two lead to
        # very different bills. Say it here, where the filename is still known.
        print(
            f"[moviola] no usable cues in {Path(path).name} — treating this video "
            "as having no captions",
            file=sys.stderr,
        )

    return _dedupe(segments)


def _dedupe(segments: list[dict]) -> list[dict]:
    """Collapse rolling duplicates common in YouTube auto-subs."""
    out: list[dict] = []
    for seg in segments:
        if out and seg["text"] == out[-1]["text"]:
            out[-1]["end"] = seg["end"]
            continue
        if out and seg["text"].startswith(out[-1]["text"] + " "):
            out[-1]["text"] = seg["text"]
            out[-1]["end"] = seg["end"]
            continue
        out.append(seg)
    return out


def filter_range(
    segments: list[dict],
    start_seconds: float | None,
    end_seconds: float | None,
) -> list[dict]:
    """Return segments whose time range overlaps [start, end]."""
    if start_seconds is None and end_seconds is None:
        return segments
    lo = start_seconds if start_seconds is not None else float("-inf")
    hi = end_seconds if end_seconds is not None else float("inf")
    return [seg for seg in segments if seg["end"] >= lo and seg["start"] <= hi]


def format_transcript(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        start = int(seg["start"])
        stamp = f"[{start // 60:02d}:{start % 60:02d}]"
        lines.append(f"{stamp} {seg['text']}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: transcribe.py <vtt-path>", file=sys.stderr)
        raise SystemExit(2)
    print(format_transcript(parse_vtt(sys.argv[1])))
