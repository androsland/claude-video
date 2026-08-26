"""Structural edits and guarded parses for values this program did not write.

Both of moviola's output channels are documents an agent reads. stdout is the
markdown report; stderr is the running commentary next to it. Neither carries a
marker separating what this program wrote from what a stranger's video title,
or a stranger's server, said back — so a remote value that ends its own line
becomes a line the reader has no way to attribute.

This module holds the two edits that answer that, and nothing else. Both output
paths share these definitions rather than each keeping a copy: the copies drift,
and the drift is silent. `md_inline` gained U+2028 handling once because a title
carrying it reached the report as two lines; a second implementation on the
stderr side would have kept the bug.

The same "did not write it" test is why `finite_float` lives here rather than
beside either of its callers. ffprobe's JSON and yt-dlp's `info.json` are both
somebody else's idea of a number arriving as a string, and both are read by
modules that cannot import each other — `frames.py` probes the video, `moviola.py`
covers the transcript-only path where there is no video to probe. A leaf module
is the only place one definition can serve both.

NON-GOALS, stated here because an unstated limit reads as a claim of coverage:

  * **Not a sanitizer.** Nothing is stripped, shortened, or escaped. Every
    character a value arrived with is still in the result — backticks, ANSI
    escapes, the `[moviola] ` prefix itself. The edits are structural: they stop
    a value ending a line, and they close any bidi SCOPE it left open. A value
    that is one plausible line of English lying about what happened passes
    through untouched, deliberately.

  * **"Closes a bidi scope" is narrower than "stops reordering", and the gap is
    real.** Three families reorder or repaint display and are untouched here,
    which matters because SKILL.md documents running these scripts directly and
    `whisper.py` has a `__main__`, so a human at a terminal is a reachable
    reader. (1) ANSI CSI sequences — `ESC[A`, `ESC[F`, `ESC[2K`, `ESC[2J` — move
    the cursor and erase, so a remote value can repaint lines already written.
    (2) OSC sequences: OSC 8 retargets a hyperlink, OSC 52 writes the viewer's
    clipboard. (3) The implicit directional marks U+200E, U+200F and U+061C
    reorder the neutral run that follows them and need no terminator at all, so
    `balance_bidi` is structurally blind to them — there is no open scope to
    close. Closing these means escaping, which is the sanitizer this is not.

  * **It only reaches values this process interpolates.** A subprocess that
    inherits a file descriptor writes past this module entirely —
    `download.py` hands yt-dlp `stdout=sys.stderr, stderr=sys.stderr`, and not
    one of those bytes can be edited here.

  * **It has no opinion about output format.** `stderr_line` is the plain form;
    `md_inline` in `moviola.py` adds the markdown fence on top of it. Anything
    needing a multi-line block — a captured ffmpeg banner, or the transcript
    body `md_fence` writes — needs a different shape and does not belong here
    yet. `md_fence` is the notable one: it closes the backtick-run escape and
    correctly preserves line breaks, but it applies no bidi balancing at all, so
    an override opened inside a hostile transcript keeps reordering display past
    the closing fence. Filed in TODOS.md, not fixed here.
"""
from __future__ import annotations

import math

# Every character Python's own splitlines() treats as a terminator. A value
# carrying any one of them ends the line it was interpolated into, and
# everything after it reads as text this program wrote. Two of these were
# discovered the hard way: U+2028 and U+2029 are line breaks to almost
# everything downstream and were not to the first version of this table.
LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
_TO_SPACES = {ord(ch): " " for ch in LINE_BREAKS}

# Bidi scopes, and the character that closes each. Two independent stacks: PDF
# closes an embedding or override, PDI closes an isolate.
_BIDI_OPENERS = {
    "\u202a": "\u202c",  # LRE
    "\u202b": "\u202c",  # RLE
    "\u202d": "\u202c",  # LRO
    "\u202e": "\u202c",  # RLO
    "\u2066": "\u2069",  # LRI
    "\u2067": "\u2069",  # RLI
    "\u2068": "\u2069",  # FSI
}
_BIDI_CLOSERS = ("\u202c", "\u2069")


def finite_float(value: object, default: float = 0.0) -> float:
    """`value` as a finite float, or `default` if it is not one.

    Numbers that arrive from a subprocess arrive as strings a stranger's file
    caused to be written, and a bare `float()` on one of them turns bad metadata
    into a dead run. ffprobe reports what it can determine and nothing more, so
    a container with no timing information — a raw elementary stream, an
    interrupted capture — leaves the field absent or unparseable; yt-dlp's
    `duration` comes out of an extractor and carries no writer guarantee at all.

    Non-finite is rejected along with non-numeric, and that is the part worth
    stating: `float()` accepts `"nan"` and `"inf"` and hands them on happily.
    They then survive every comparison in `auto_fps` and raise inside
    `_clamp_fps`, where `int(round(nan))` is a ValueError and `int(round(inf))`
    an OverflowError — a crash naming a frame-budget helper rather than the
    metadata that was wrong.

    NON-GOAL: this is a coercion, not a validation. It cannot tell a wrong
    number from a right one — ffprobe answering `3.0` for a thirty-second video
    passes straight through, here and everywhere downstream.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def balance_bidi(text: str) -> str:
    """Close, inside `text`, every bidi scope `text` opens and never closes.

    An unterminated override does not end where the value ends — it keeps
    reordering the display of everything after it, including the headings this
    program wrote and the reader is entitled to trust. Fencing the value as code
    does not help: the controls are still in the character stream.

    Terminators are APPENDED, never stripped, so the value keeps every character
    it arrived with. A legitimate right-to-left title is unaffected; ordinary
    RTL text needs no override, and one that is already balanced gets nothing
    added.

    Cost is linear in the length of `text`, and that is load-bearing rather
    than incidental. Every value reaching this function is remote, so anything
    superlinear here is an amplifier: the first version of this loop matched a
    closer by scanning the open-scope stack from the top, which meant a closer
    matching NOTHING walked the whole stack and deleted nothing. N openers of
    one kind followed by N closers of the other cost N², and 32,000 characters
    — nothing for an HTTP response body — took eleven seconds. The two
    per-kind index stacks below are what replaced that scan; they hold the
    slots still open for each closer, so the nearest match is a pop.

    NON-GOALS: this is an approximation of UAX#9, not an implementation of it.
    It matches a closer to the nearest open scope of the same kind, where the
    real algorithm resolves matching within an isolating run sequence — so on
    pathological interleavings it can append a terminator that was not needed.
    That direction is harmless. And confining the reordering says nothing about
    the value misrepresenting ITSELF: a filename that displays reversed still
    displays reversed inside its own span.

    NON-GOAL: linear is not bounded. This walks whatever it is handed, so a
    caller passing a value it never truncated still pays for every character of
    it — and still holds all of them in memory. Bounding the INPUT is each
    caller's job; `_read_error_body` slices 400 characters before calling in,
    and that is the pattern.
    """
    # `stack` holds one slot per scope opened, in the order they opened, and a
    # slot is set to None when its scope closes rather than removed — removing
    # it would shift every index recorded below. `open_slots` maps each closer
    # to the slots still open for it, so matching a closer to the nearest open
    # scope of that kind is a pop instead of a search.
    stack: list[str | None] = []
    open_slots: dict[str, list[int]] = {closer: [] for closer in _BIDI_CLOSERS}
    for ch in text:
        closer = _BIDI_OPENERS.get(ch)
        if closer is not None:
            open_slots[closer].append(len(stack))
            stack.append(closer)
            continue
        matching = open_slots.get(ch)
        # None when `ch` is not a closer at all, empty when it is one with
        # nothing open — a closer that matches nothing is left alone, exactly
        # as it was before.
        if matching:
            stack[matching.pop()] = None
            # Dead slots at the end are unreachable from `open_slots`, so
            # dropping them keeps a balanced value's stack as short as its
            # nesting is deep. Each slot is popped at most once across the
            # whole call, so this stays linear overall.
            while stack and stack[-1] is None:
                stack.pop()
    return text + "".join(slot for slot in reversed(stack) if slot is not None)


def stderr_line(value: object) -> str:
    """Render one untrusted value so it cannot become a line of its own.

    stderr is where this program narrates itself, one `[moviola] ` line at a
    time, and it lands in the same context as the report. A value interpolated
    into one of those lines that carries a line break ends it, and whatever
    follows arrives at column zero looking exactly like the next thing moviola
    said. An API error body is the live instance: `_read_error_body` takes 400
    bytes of whatever a server chose to send and puts them in a `SystemExit`
    message, so a body reading `quota exceeded` + newline + `[moviola] transcript
    complete` forges a progress line for the price of a 400 response.

    Line breaks become spaces and unclosed bidi scopes are closed — the same two
    edits `md_inline` makes, which is why it calls this. What it does NOT do is
    wrap the result in backticks: stderr is not markdown, and a fence there
    would be noise around every diagnostic in the program.

    NON-GOAL: this makes the value one line; it does not make it true. Nothing
    here stops a remote value from being a convincing sentence, and nothing
    should — the value is reported in full because a caller debugging a failed
    request needs to read what the server actually said.
    """
    return balance_bidi(str(value).translate(_TO_SPACES))
