"""Structural edits and guarded parses for values this program did not write.

Both of moviola's output channels are documents an agent reads. stdout is the
markdown report; stderr is the running commentary next to it. Neither carries a
marker separating what this program wrote from what a stranger's video title,
or a stranger's server, said back — so a remote value that ends its own line
becomes a line the reader has no way to attribute.

This module holds the edits that answer that, and nothing else. Both output
paths share these definitions rather than each keeping a copy: the copies drift,
and the drift is silent. `md_inline` gained U+2028 handling once because a title
carrying it reached the report as two lines; a second implementation on the
stderr side would have kept the bug.

There are two shapes, because there are two kinds of foreign value. A short one
— an API error body, a filename, a reason phrase — belongs INSIDE a line this
program wrote, so `stderr_line` makes it incapable of ending that line. A
captured subprocess stderr is a document of its own: it arrives as forty-odd
lines whose structure is the entire reason anyone prints it, so `stderr_block`
keeps the structure and attributes every line of it instead. Feeding the second
kind to the first was the tempting mistake — it type-checks, it is one call,
and it silently turns a diagnostic into one unreadable run-on.

The same "did not write it" test is why `finite_float` lives here rather than
beside either of its callers. ffprobe's JSON and yt-dlp's `info.json` are both
somebody else's idea of a number arriving as a string, and both are read by
modules that cannot import each other — `frames.py` probes the video, `moviola.py`
covers the transcript-only path where there is no video to probe. A leaf module
is the only place one definition can serve both.

`json_object` is the same test one level up. `finite_float` asks whether a VALUE
inside a document is a number; `json_object` asks whether there is a document at
all — a question `frames.py` answered by assumption until a stdout that was not
JSON turned a subprocess problem into a `JSONDecodeError` naming a column of a
string nobody had seen. Neither of them decides what a failure MEANS: one
returns the caller's default and the other returns `None`, and the policy that
reads it stays at the call site where the consequences are known.

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

  * **It has no opinion about MARKDOWN.** `stderr_line` and `stderr_block` are
    the plain forms; `md_inline` in `moviola.py` adds the markdown fence on top
    of the first. The report's own multi-line shape, `md_fence`, is still not
    here and is the notable gap: it closes the backtick-run escape and
    correctly preserves line breaks, but it applies no bidi balancing at all, so
    an override opened inside a hostile transcript keeps reordering display past
    the closing fence. Filed in TODOS.md, not fixed here. `stderr_block` does
    balance per line, so the two multi-line shapes now differ in exactly that
    respect — which is the argument for `md_fence` eventually calling in here,
    and not an argument that it already does.

  * **`stderr_block`'s attribution is structural IN THE STRING; its notices are
    not structural at all.** No foreign line can produce a line without the
    prefix, and inside the string an agent ingests that is the whole guarantee:
    unprefixed means moviola wrote it. It does not survive a TERMINAL — the
    families two bullets above repaint at physical column zero, and a run of C0
    backspace overstrikes the prefix with no escape sequence at all, so a human
    reading this directly gets a weaker promise than an agent does. See
    `stderr_block`'s own NON-GOALS for that half. The header and the
    "not shown" line are ordinary text a hostile capture can imitate INSIDE a
    prefixed line, and so is the per-line width marker. They exist so a bound
    is never silent, not so a reader can trust their arithmetic.
"""
from __future__ import annotations

import json
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

# The constants a fenced block is built from. Public because the tests assert
# against them rather than against literals — a bound nobody can name is a
# bound nobody can check, and `MAX_BLOCK_WIDTH` was exactly that until the
# review that added `test_one_line_of_a_bounded_block_is_still_bounded`'s
# allowance: it had been pinned by `< 1000`, five times its own value.
#
# The prefix is the only structural part. It goes on every line of the capture,
# so a reader's rule is "unprefixed means moviola wrote it", and no foreign line
# can satisfy that rule no matter what it contains -- in the STRING. On a
# terminal it can, and the module NON-GOALS above say how.
BLOCK_PREFIX = "| "

# `| ` is ON + WS — it holds no STRONG character, and UAX#9 P2/P3 take a line's
# base direction from its first strong one. A capture line opening in Hebrew or
# Arabic therefore resolves the whole rendered line to RTL, N1/N2 carry the
# leading neutrals along with it, and `| ` is painted at the visual RIGHT edge
# with the author's text at visual column zero. `balance_bidi` is powerless
# there: nothing was ever opened, so there is nothing to close. One LRM after
# the prefix fixes the line's base direction to LTR before any foreign
# character is read. It is not an opener or a closer, so it composes with
# `balance_bidi` untouched, and it is invisible.
DIR_ANCHOR = "\u200e"

# Both bounds are measured rather than picked. A real ffmpeg failure at
# `-loglevel info` — a 1s synthesized clip into an unwritable directory — came
# back as 48 lines, 90th-percentile width 113, widest line 1371 (that one is
# `showinfo` dumping x264's SEI user data as hex). 40 lines and 200 columns keep
# an ordinary diagnostic essentially whole while bounding a hostile one, and the
# widest real line is the one worth cutting.
MAX_BLOCK_LINES = 40
MAX_BLOCK_WIDTH = 200


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
    `_clamp_fps` as `int(round(nan))` — a ValueError, and a crash naming a
    frame-budget helper rather than the metadata that was wrong. An infinite
    duration reaches it as nan rather than inf, because it makes `fps` 0.0 and
    `0.0 * inf` is nan; `auto_fps(inf)` and `auto_fps_focus(inf)` were both run
    to confirm that, and both raise ValueError. (The bare expression
    `int(round(inf))` IS an OverflowError, and this docstring claimed as much
    of `_clamp_fps` until a review executed it. The place that shape really
    occurs is `main()`'s `fps_override` branch, where a finite fps times an
    infinite duration gives `int(round(inf))` directly.)

    `OverflowError` is caught beside TypeError and ValueError because a Python
    int has no maximum and `float()` raises it — not ValueError — on one too
    large for a double. `json.loads` produces exactly that from a bare JSON
    integer literal, so a yt-dlp `info.json` carrying a 400-digit `duration`
    escaped the guard entirely until a review caught it.

    NON-GOALS, so the name is not read as a stronger promise than it keeps:

      * This is a coercion, not a validation. It cannot tell a wrong number
        from a right one — ffprobe answering `3.0` for a thirty-second video
        passes straight through, here and everywhere downstream.

      * `default` is REFUSED, not coerced, and the asymmetry is the point.
        `value` came from a stranger, so a bad one is ordinary and becomes the
        default; `default` is a literal a moviola author typed, so a non-finite
        one is this program's bug and raises ValueError. It is checked on entry
        rather than at the point of return, because a lazy check fires only
        when `value` happens to be unparseable too — the caller's bug would
        then ship green and surface later, far from the line that caused it.
        Every call site passes 0.0 today, so nothing in the tree can trigger
        it; the name is the promise a future caller reads, and this is what
        enforces it.

      * The refusal is finiteness alone. A negative default and a very large
        one both pass — sign and magnitude are separate findings with separate
        owners, and widening the guard to catch them here would be a behaviour
        change wearing a fix's clothes.

      * Finiteness is not magnitude, and the consequence is a dead run rather
        than a silly one. There is no ceiling here and nothing downstream
        imposes one: `auto_fps` answers a huge duration with a tiny fps, and
        past 1,000,000 seconds Python reprs that fps in scientific notation,
        which ffmpeg rejects outright as a video rate. Measured and filed in
        TODOS.md; the fix is at the format site, not here.

      * Sign is not checked either. A negative duration passes, and
        `format_time(-1.0)` renders it as `-1:59:59`.
    """
    try:
        default_is_finite = math.isfinite(default)
    except (TypeError, OverflowError):
        # TypeError for a `default` that is not a number at all; OverflowError
        # for a Python int too large for a double, which is the same
        # not-representable-as-a-finite-float condition arriving by a different
        # exception. Both answer False rather than escaping as themselves — a
        # caller debugging this should read one message naming `default`, not a
        # traceback naming the math module.
        default_is_finite = False
    if not default_is_finite:
        raise ValueError(
            f"finite_float() was given a non-finite default: {default!r}. "
            "`value` is coerced because a stranger wrote it; `default` is this "
            "program's own literal, so a non-finite one is a bug here and is "
            "refused rather than repaired."
        )

    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def json_object(text: object) -> dict | None:
    """A JSON object parsed out of somebody else's output, or None for anything else.

    `json.loads` is the wrong call to make directly on a captured stdout,
    because it fails in two shapes and only one of them looks like a failure.
    It RAISES `ValueError` when the text is not JSON at all — a shim on PATH, a
    wrapper that prints a warning first, a proxy answering with an HTML error
    page. And it SUCCEEDS, handing back a list or a number or `None`, when the
    text is valid JSON that is not an object: `[]`, `3`, `"text"`, `null` and
    `true` all parse cleanly. The second is the dangerous one precisely because
    the parse worked — the failure lands at the caller's first `.get()` as an
    `AttributeError` naming a dict method, a frame away from the subprocess that
    produced the document.

    A third belongs to the parser rather than to the input: a document nested
    past the interpreter's recursion limit raises `RecursionError`, which is
    neither a `ValueError` nor caught by anybody expecting one, and about a
    thousand opening brackets reach it. Catching it is narrow enough to be safe
    here — there is no `object_hook` and no `parse_float`, so the only recursion
    this can swallow is the JSON scanner's own.

    All three answer `None`, and `None` is a question rather than a decision. A
    caller that can carry on without the document reads it as "nothing to read";
    a caller that cannot reads it as evidence about what is on PATH. Choosing
    between those is policy, and it belongs at the call site that knows the
    consequences — this function decides only whether the bytes are the shape
    the caller was promised.

    NON-GOALS:

      * **Shape, not schema, and certainly not truth.** `{}` and a document
        missing every expected key are the same answer here, and so is one
        describing a different video entirely. What the fields mean is
        `finite_float`'s problem and the caller's.

      * **Not a size or a memory bound.** A hundred-megabyte object parses and
        is returned whole. Whatever produced `text` already bounded it or did
        not — `subprocess.run` has buffered the entire capture before this is
        called — and that bound is not this function's to give.

      * **A `dict` is the only container it recognises.** A document whose top
        level is legitimately an array is refused, which is correct for every
        caller there is today and would be wrong for one that wanted a list.
        That caller wants its own function, not a widened return type here.
    """
    try:
        document = json.loads(text)  # type: ignore[arg-type]
    except (TypeError, ValueError, RecursionError):
        return None
    return document if isinstance(document, dict) else None


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


def stderr_block(
    value: object,
    *,
    source: str,
    max_lines: int = MAX_BLOCK_LINES,
    max_width: int = MAX_BLOCK_WIDTH,
) -> str:
    """Render a captured subprocess stderr as an attributed, bounded block.

    Seven sites in this program USED to raise
    `SystemExit(f"...: {result.stderr}")` after an ffmpeg or ffprobe run
    returned non-zero — the shape the AST sweep in
    `tests/test_stderr_blocks_are_fenced.py` now refuses an eighth of. Every
    one of those captures is a document somebody else wrote. At `-loglevel info` it opens
    with the container's `Metadata:` block — `title`, `comment`, `artist`,
    chosen by whoever made the video — and it lands in the agent's context
    beside the report, with nothing marking where their text ends and this
    program's resumes. `moviola.py`'s re-print of a caught `SystemExit` makes
    that concrete: it puts a `[moviola] ` prefix on the first line of the block
    and on none of the other forty-seven.

    `stderr_line` is the wrong instrument here and the reason is in its own
    docstring: it makes a value ONE line by collapsing every break to a space.
    Applied to a forty-line diagnostic that produces one line of forty joined
    fragments and throws away the structure the diagnostic was printed for.

    So: `source` names the writer, every line of the capture is prefixed with
    `BLOCK_PREFIX`, and the result is bounded in both dimensions, because the
    writer chose both. `source` used to be documented as "the one trusted
    argument" and trusted on that word alone; it is the only value here that
    reaches column zero, so it now goes through `stderr_line` like any other.

    The line bound keeps the TAIL. That is the load-bearing half of it: ffmpeg
    prints what went wrong last (`Conversion failed!` was the final line of
    every failure measured) and puts the container's metadata near the front, so
    a head-biased cut would keep the part a stranger wrote and discard the only
    line a person debugging the run is looking for.

    Ordering inside a line is slice-then-balance, the same order
    `_read_error_body` uses: the slice can cut a bidi scope in half, so
    balancing has to see what actually survives. Balancing is per LINE rather
    than per block, because an override opened on line three would otherwise
    reorder the display of line four — including the prefix that line four's
    attribution rests on.

    NON-GOALS, so the prefix is not read as a stronger promise than it keeps:

      * **Attribution, not sanitization.** Nothing is stripped or escaped.
        `| Conversion failed!` on a line ffmpeg never wrote is still a lie the
        reader can be told; what they can no longer be told is that moviola
        said it. Every family `balance_bidi` and this module are already blind
        to — ANSI CSI, OSC 8/52, the implicit marks U+200E/U+200F/U+061C —
        passes through a prefixed line untouched.

      * **Only the prefix is structural, and only in the STRING.** The header
        and the "not shown" line sit at column zero because they are this
        program's, and a foreign line structurally cannot reach column zero of
        the string an agent ingests. The capture can, however, contain text
        that reads exactly like either of them, and the width marker sits
        inside foreign territory by construction. Treat all three as notices,
        never as arithmetic to trust.

      * **On a TERMINAL, column zero is not structural at all.** The ANSI
        family disclosed above includes column-addressing sequences — CHA
        (`ESC[G`), CNL (`ESC[E`), and the two-character 7-bit NEL (`ESC E`,
        which `splitlines` correctly does not treat as a break) — that
        repaint foreign text at physical column zero with no `| ` in front of
        it. Nor is an ESC required to do it: a run of C0 backspace (`0x08`)
        walks a naive terminal's cursor back across `BLOCK_PREFIX` and
        `DIR_ANCHOR` and overstrikes them with whatever prints next — the
        same outcome by an older and simpler mechanism. `DIR_ANCHOR` closes
        the bidi half of this; nothing here closes either control-character
        half, and the agent reading the string is the reader this module is
        written for.

      * **`max_width` bounds the content, not the rendered line.** The prefix,
        any terminators `balance_bidi` appends, and the width marker are added
        afterwards. The result is still bounded — a 200-character slice can
        open at most 200 scopes — just not bounded AT `max_width`.

      * **The bound is on what is RENDERED, not on what is read.**
        `splitlines()` materializes every line of the capture before
        `max_lines` is applied, so peak memory is O(lines in the capture) rather
        than O(`max_lines`) — measured at +51MB RSS for a 10MB, 5M-line capture.
        It is an amplifier and not a new unbounded read: `subprocess.run` has
        already buffered the whole capture before this function is called, and
        this is a failure path about to raise. A single enormous LINE costs
        nothing extra, because `len(line)` is O(1) and the slice happens before
        `balance_bidi` sees it.

      * **It cannot see a capture that never reaches it.** A subprocess given
        an inherited descriptor writes past this module entirely, which is
        exactly what `download.py` does with yt-dlp, and that remains the
        largest volume of remote text on this program's stderr.
    """
    text = str(value)
    # `source` is the only value in this function that reaches column zero, so
    # it is the only one that could forge a line moviola never wrote. The
    # docstring called it "a caller-supplied literal — the one trusted
    # argument", which is a convention that nothing enforced: an eighth site
    # passing `cmd[0]`, a tool path or a filename would land foreign text at
    # column zero, and the AST sweep keys on the attribute name `stderr` so it
    # would not see it. One call closes it for every caller there will ever be.
    source = stderr_line(source)
    if not text.strip():
        # An empty capture rendered as an empty block reads as "no diagnostic
        # exists", when what happened is that the tool ran and said nothing —
        # a different and more interesting fact.
        #
        # It does NOT say "exited non-zero", though it did until the gate that
        # caught it. This function is handed a capture and never a returncode,
        # so that was a fact it had no access to, and one of its own callers
        # contradicts the guess: `whisper.split_audio` holds the only compound
        # guard of the seven — `returncode != 0 or not out_path.exists() or
        # st_size == 0` — so a run that exits 0 and leaves no chunk behind
        # lands here and was reported to the reader as a non-zero exit.
        # `extract_audio` documents that exact case two functions above it: an
        # -ss past the end of the media exits 0 and writes a valid but empty
        # mp3, measured at 333 bytes. Report what the capture shows; the exit
        # status belongs to the caller, which is the only one holding it.
        return f"({source} wrote nothing to stderr)"

    # Trimmed here rather than at each call site. All seven passed
    # `result.stderr.strip()`, which also eats the FIRST line's indentation — so
    # an ffmpeg banner opening with an indented line rendered de-indented while
    # every line below it kept its own. What wants trimming is the blank margin,
    # never the foreign text's own shape, and that is a decision this module
    # owns rather than one seven callers each get right separately.
    lines = text.strip("\r\n").splitlines()
    dropped = max(0, len(lines) - max_lines)
    kept = len(lines) - dropped
    # Both numbers, because the header used to name only the capture size while
    # `dropped` lines were silently absent below it: a 500-line capture was
    # announced as "500 line(s) ... follow" above forty. A hostile capture can
    # imitate this line, which is why the NON-GOALS call it a notice — but that
    # is a reason not to TRUST it, never a licence for moviola's own arithmetic
    # to be wrong.
    out = [
        f"-- {kept} of {len(lines)} line(s) of {source} output follow; moviola "
        f"wrote none of them, and marks each with {BLOCK_PREFIX!r} --"
    ]
    if dropped:
        out.append(f"({dropped} earlier line(s) not shown)")
    for line in lines[dropped:]:
        if len(line) > max_width:
            body = balance_bidi(line[:max_width]) + f" ...(+{len(line) - max_width} char(s))"
        else:
            body = balance_bidi(line)
        out.append(BLOCK_PREFIX + DIR_ANCHOR + body)
    return "\n".join(out)
