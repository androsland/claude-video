"""`balance_bidi` must not be the denial of service it exists to prevent.

`untrusted.balance_bidi` closes the bidi scopes an untrusted value leaves open,
and every value it sees is remote by definition — an API error body, a hub
exception, a video title. Its first implementation matched a closer by scanning
the whole open-scope stack from the top:

    for i in range(len(stack) - 1, -1, -1):
        if stack[i] == ch:
            del stack[i]
            break

A closer that matches nothing scans the entire stack and deletes nothing, so a
value of N openers of one kind followed by N closers of the OTHER kind costs
N * N. Measured against that implementation on the machine this was written on:
4,000 characters took 0.20s, 8,000 took 0.74s, 16,000 took 2.7s, 32,000 took
11.2s. That is clean quadratic growth, and 32,000 characters is nothing for an
HTTP response body — a few kilobytes of attacker bandwidth buys minutes of a
synchronous process, and the growth does not stop there.

`local_whisper.py` is where it was reachable with no bound at all: a cache miss
goes through huggingface_hub, `HfHubHTTPError` embeds the hub's entire response
body in its message, and the fence ran on `str(exc)` whole. `whisper.py`'s
sites were never reachable this way, which is why they are not covered here —
`str(HTTPError)` carries the status-line reason phrase, `http.client` decodes
that latin-1 and caps the line at 65536 bytes, and not one bidi control is
representable below U+0100.

Two things are pinned: the rewrite returns byte-identical output to the scan it
replaces, and the scan is gone.

NON-GOALS, so a green run is not read as more than it is:

  * **The timing assertion is wall-clock, and wall-clock is not a proof.** It
    gives the linear implementation roughly two orders of magnitude of headroom
    — enough that a loaded CI runner does not fail it, and enough that the
    quadratic scan cannot pass it. It does not measure complexity, and it cannot
    see a regression that is merely slow.

  * **It pins THAT scan, not every way this could be slowed down.** A change
    making `balance_bidi` quadratic in some other dimension, or moving the cost
    into a caller, is invisible here.

  * **A bounded call is still an unbounded read.** Truncating before the fence
    caps what `balance_bidi` sees; it does nothing about a caller reading a huge
    body into memory first. `exc.read()` in `whisper.py` is filed in TODOS.md
    for exactly that, and nothing here covers it.

  * **The oracle is the OLD implementation, not UAX#9.** What is asserted is
    equivalence to a deliberate approximation. Both are wrong about the real
    algorithm in the same direction and on purpose — `balance_bidi`'s own
    docstring says so — and this file would not notice if they were.

  * **The legitimate configuration this must not fire on is ordinary text.** A
    balanced value, a value with no controls at all, and a right-to-left title
    that needs no override must all come back unchanged; the equivalence corpus
    includes them, so a "fix" that started appending terminators defensively
    would fail here rather than pass.

  * **One line of the implementation is deliberately unpinned.** `balance_bidi`
    drops dead slots off the end of its stack, which keeps a balanced value's
    stack as short as its nesting is deep instead of as long as its opener
    count. Deleting that loop was run as a mutation and all 617 tests still
    passed, because it changes memory and changes no answer. Nothing here can
    see it, and a memory assertion precise enough to catch it would be flakier
    than the bug. Stated so a green run is not read as "every line is covered".
"""
from __future__ import annotations

import itertools
import random
import time

import pytest

import untrusted


# The seven openers and two closers `untrusted` knows about, plus one ordinary
# character so the corpus contains text and not only controls. Written as
# escapes for the same reason `untrusted.py` is: four literal overrides on one
# line reorder how that line displays, in a file about stopping exactly that.
LRE, RLE, LRO, RLO = "\u202a", "\u202b", "\u202d", "\u202e"
LRI, RLI, FSI = "\u2066", "\u2067", "\u2068"
PDF, PDI = "\u202c", "\u2069"
ALPHABET = (LRE, RLE, LRO, RLO, LRI, RLI, FSI, PDF, PDI, "x")


def reference_balance_bidi(text: str) -> str:
    """The implementation this replaced, kept as the oracle.

    Deliberately a verbatim copy rather than an import: the point of the
    comparison is that two independently written algorithms agree, and an
    oracle that called the code under test would agree with anything.
    """
    stack: list[str] = []
    for ch in text:
        closer = untrusted._BIDI_OPENERS.get(ch)
        if closer is not None:
            stack.append(closer)
        elif ch in untrusted._BIDI_CLOSERS:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] == ch:
                    del stack[i]
                    break
    return text + "".join(reversed(stack))


class TestTheRewriteChangedNoAnswer:
    """Byte-identical output to the scan, across every shape worth checking."""

    @pytest.mark.parametrize("length", [0, 1, 2, 3, 4])
    def test_every_string_up_to_four_characters_agrees(self, length):
        # 11,111 strings over the full alphabet. Short enough to enumerate
        # exhaustively, long enough to contain every nesting and interleaving
        # of two closer kinds that the matching rule can distinguish.
        for combo in itertools.product(ALPHABET, repeat=length):
            text = "".join(combo)
            assert untrusted.balance_bidi(text) == reference_balance_bidi(text), (
                f"diverged on {text!r}"
            )

    def test_longer_random_strings_agree(self):
        # Exhaustive stops at four; a mismatched closer only starts deleting the
        # WRONG entry once the stack is deep, so the long random corpus is what
        # covers a stack with several live scopes of both kinds at once.
        rng = random.Random(20260826)
        for _ in range(2000):
            length = rng.randrange(0, 60)
            text = "".join(rng.choice(ALPHABET) for _ in range(length))
            assert untrusted.balance_bidi(text) == reference_balance_bidi(text), (
                f"diverged on {text!r}"
            )

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "an ordinary title",
            "אבג",  # Hebrew: right-to-left, and needs no override
            LRE + "balanced" + PDF,
            LRI + "isolated" + PDI,
            PDF,  # a closer with nothing open
            PDI + PDF,
            LRE + LRI + "nested" + PDI + PDF,
        ],
    )
    def test_the_ordinary_cases_are_returned_unchanged_by_both(self, text):
        # The legitimate configuration. Every one of these is already balanced
        # or has nothing to balance, so both implementations must return the
        # input untouched — an implementation that appended defensively would
        # be caught here rather than by the equivalence corpus alone.
        assert untrusted.balance_bidi(text) == text
        assert reference_balance_bidi(text) == text


class TestTheClosingScanIsGone:
    """The adversarial shape must not cost more than the value is long."""

    # An opener of one kind followed by a closer of the OTHER kind: the closer
    # matches nothing, so the scan walks the whole stack and deletes nothing.
    @pytest.mark.parametrize(
        "opener,closer",
        [(LRE, PDI), (LRI, PDF), (RLO, PDI), (FSI, PDF)],
        ids=["embedding-then-pdi", "isolate-then-pdf", "override-then-pdi", "fsi-then-pdf"],
    )
    def test_sixteen_thousand_unmatched_scopes_finish_promptly(self, opener, closer):
        # 32,000 characters. Against the scan this took 11.2s; against a linear
        # implementation it is single-digit milliseconds. The budget sits two
        # orders of magnitude above the latter so a loaded runner cannot fail
        # it, and far below the former so the scan cannot pass it.
        hostile = opener * 16_000 + closer * 16_000

        start = time.perf_counter()
        result = untrusted.balance_bidi(hostile)
        elapsed = time.perf_counter() - start

        assert elapsed < 2.0, (
            f"balance_bidi took {elapsed:.2f}s on {len(hostile)} characters — "
            "the quadratic closer scan is back"
        )
        # And it still did the job: 16,000 scopes were opened and none closed,
        # so 16,000 terminators are appended.
        assert result == hostile + untrusted._BIDI_OPENERS[opener] * 16_000
