"""The format ladder's fallback cannot download something BIGGER than the rung above it.

`download.VIDEO_FORMAT` is a yt-dlp format selector: a slash-separated ladder,
tried left to right, that decides how large a download is allowed to get. Its
first two rungs are height-bounded, and its tail was not:

    bv*[height<=720]+ba/b[height<=720]/bv+ba/b
                                       ^^^^^^^  best video, no bound

`bv*` and `b` select the BEST rendition yt-dlp can find. So a video with no
<=720p rendition at all — a 4K-only upload, and they are ordinary now — fell
through both bounded rungs and downloaded at 4K, on the branch whose entire
purpose is staying small. The two bounds above it read as a policy the tail
then discards.

**This pins a weaker property than the finding asked for, and the difference is
load-bearing.** The finding framed it as "every selector in the chain carries a
height bound". That is not the fix and cannot be: a bounded tail such as
`bv*[height<=1080]+ba/b[height<=1080]` matches NOTHING on a ladder whose
smallest rendition is 4K, and a yt-dlp selector that matches nothing is a hard
failure — it turns a working (if oversized) download into no download at all.
`wv*+ba/w` is total: `wv*`/`w` match everything the old tail matched, and pick
the smallest instead of the largest. So what is pinned here is:

    no rung of the ladder can select a LARGER rendition than the rung above it

concretely, that no unbounded *best*-video selector remains anywhere in it.

**A review found that property necessary but not sufficient, and the ladder
grew a middle pair as a result.** `[height<=720]` DROPS a format whose height
is unknown rather than keeping it, so a source whose formats carry no height —
an HLS manifest with no RESOLUTION attribute, the generic extractor — skipped
both bounded rungs and reached the tail EVERY time, not just when it was too
big. With no height there is also no floor, so the tail took the smallest
rendition on offer: 6000 kbps down to 150 in the corpus below, which is not a
usable visual input for a tool whose output is frames an agent reads. The same
gap downgraded a video-less source from 256 kbps audio to 64, and there the
audio IS the transcript. `[height<=?720]` is yt-dlp's unknown-tolerant form and
keeps those formats, so the second property pinned here is:

    the ladder must never select something SMALLER than the old one did on a
    ladder it had no business shrinking in the first place

Both regressions were invisible to the first version of this file, and for two
separate reasons that are themselves now pinned — see `_incomplete_formats`
and `_rank`.

NON-GOALS, so a green run is not read as more than it is:

  * **It does not cap the download at 720p, and cannot.** On a ladder whose
    smallest rendition is 4K the download is still 4K, because there is nothing
    else to fetch. The claim is monotonic, not absolute: the fallback now takes
    the smallest available rather than the largest.

  * **`wv*`/`w` mean "worst by yt-dlp's default sort", not "fewest pixels" and
    not "fewest bytes".** Measured against yt-dlp 2026.06.09, that sort is
    `(hidden, aud_or_vid, hasvid, ie_pref, lang, quality, res, ...)` — `res` is
    seventh, and both `ie_pref` and `quality` outrank it, while `size` and `br`
    are twelfth and thirteenth. So on an extractor that assigns a per-rendition
    `quality`, "worst" is that extractor's preference order and the tail can
    pick something LARGER than the old selector did. `test_no_format_sort_is_
    passed` stops moviola adding a `-S` of its own; nothing here can stop an
    extractor-side preference, and that is where this actually bites.

  * **The synthetic ladders assume yt-dlp's worst-first ordering convention.**
    `build_format_selector` does no sorting of its own — `bv*` takes the LAST
    matching format and `wv*` the first — so a list written best-first inverts
    every answer here. Real extractors emit worst-first, and nothing in this
    network-free suite drives a real extractor, so an extractor that ever
    emitted a differently-ordered list would be invisible to these tests.

  * **The muxed fallback takes the audio down with the video.** `/w` selects a
    whole file, so its audio is whatever that file carries, and the transcript
    is made from that audio. The split-stream rungs keep `ba` (best audio) and
    `test_best_audio_survives_the_shrink` pins it; the muxed case is a trade
    made knowingly, on the grounds that a muxed-only ladder offers no way to
    keep the good audio and drop the big video.

  * **That trade also fires on a ladder that DOES offer separate audio, if the
    only video formats are muxed — and there it is not forced.** `wv*` matches
    a muxed format, and yt-dlp's default `--no-audio-multistreams` then drops
    the `+ba`, so a ladder of [a64, a256, m1080/96k, m2160/192k] yields m1080's
    96 kbps rather than a256. The old tail's `bv` was video-ONLY and could not
    reach that case. It remains a net byte saving and is not fixed here: the
    only lever is `--audio-multistreams`, which re-adds a whole audio stream
    and an ffmpeg merge pass to save a transcript-quality delta. Recorded in
    TODOS.md rather than left implied, and `test_best_audio_survives_the_
    shrink` runs only the split-stream ladder, so this file does not see it.

  * **A heightless ladder is rescued to its BEST, not bounded.** The tolerant
    rungs keep formats whose height is unknown, and `bv*` then takes the
    largest of them — because unknown is not the same as small, and the
    alternative is the floorless tail. So on such a source the download is
    exactly what it was before this file existed: no worse, and no better.
    Nothing here can bound a rendition whose size the manifest never states.

  * **The legitimate configuration this must not fire on is a ladder that HAS
    a <=720p rendition.** Those must select exactly what they always selected —
    `test_a_ladder_with_720_is_untouched` and the muxed equivalent run the old
    and the new selector side by side and require the same pick. `--audio-only`
    must likewise be untouched: `AUDIO_FORMAT` asks for best audio, because
    that is the flag's entire point. It is now `ba` rather than `ba/bestaudio`
    — the same selector twice, one rung of which could never fire — and the
    test asserts the property rather than the literal, so the next legitimate
    rewording does not read as a policy change.

  * **Format-selector syntax belongs to yt-dlp, and no version is pinned.** The
    behavioural class runs only where `yt_dlp` happens to be importable (it is
    a dev-only import — moviola shells out to the binary) and skips silently
    everywhere else. The structural class is a string check and would pass
    against a future yt-dlp that renamed these atoms.

Every value written below is inert filler. Nothing here reads a real credential.
"""
from __future__ import annotations

import re

import pytest

import download


FILLER = "placeholder-value-not-a-credential"

# What the tail looked like before this file existed. History, not
# configuration: it is here so the before/after contrast is executed rather
# than asserted in prose. `test_the_baseline_is_actually_different` is the
# guard that stops every comparison below going quietly tautological if the
# two ever converge again.
PREVIOUS_VIDEO_FORMAT = "bv*[height<=720]+ba/b[height<=720]/bv+ba/b"

try:  # pragma: no cover - environment-dependent
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None

needs_yt_dlp = pytest.mark.skipif(
    yt_dlp is None,
    reason="yt_dlp is a dev-only import; moviola shells out to the yt-dlp binary",
)


# --------------------------------------------------------------------------
# Reading a selector string
# --------------------------------------------------------------------------

_FILTER = re.compile(r"\[[^\]]*\]")

_LONG_FORMS = {
    "bestvideo": ("video", "best"),
    "worstvideo": ("video", "worst"),
    "bestaudio": ("audio", "best"),
    "worstaudio": ("audio", "worst"),
    "best": ("video", "best"),
    "worst": ("video", "worst"),
}


def _rungs(selector: str) -> list[str]:
    """The ladder, top rung first. yt-dlp tries these left to right."""
    return selector.split("/")


def _atoms(rung: str) -> list[str]:
    """One rung's parts. `bv*+ba` is two atoms merged into one download."""
    return rung.split("+")


def _classify(atom: str) -> tuple[str, str]:
    """`(kind, extreme)` for one atom — kind in video/audio, extreme in best/worst.

    Raises rather than guessing. An atom this cannot read is a selector the
    rules below would silently skip, which is the shape of a test that passes
    because it looked at nothing.
    """
    bare = _FILTER.sub("", atom)
    if bare in _LONG_FORMS:
        return _LONG_FORMS[bare]
    if bare[:1] in ("b", "w"):
        extreme = "best" if bare[0] == "b" else "worst"
        rest = bare[1:]
        if rest in ("", "v", "v*"):
            # A bare `b`/`w` is a muxed file, which carries video.
            return "video", extreme
        if rest in ("a", "a*"):
            return "audio", extreme
    raise AssertionError(f"unrecognised yt-dlp selector atom: {atom!r}")


def _video_atoms(selector: str) -> list[str]:
    return [
        atom
        for rung in _rungs(selector)
        for atom in _atoms(rung)
        if _classify(atom)[0] == "video"
    ]


def _is_height_bounded(atom: str) -> bool:
    # The optional `?` is yt-dlp's unknown-tolerant form: `[height<=?720]`
    # keeps a format whose height field is ABSENT, where `[height<=720]` drops
    # it. Both are bounds. A regex that could not read the `?` form would
    # report the tolerant rungs as unbounded best-selectors and fail the rule
    # below on a ladder they exist to rescue.
    return bool(re.search(r"\[height\s*<=?\s*\??\s*\d+\]", atom))


# --------------------------------------------------------------------------
# Synthetic format ladders
# --------------------------------------------------------------------------
#
# ORDER MATTERS AND IS NOT COSMETIC. `build_format_selector` sorts nothing:
# `bv*` takes the last matching entry and `wv*` the first, so these lists are
# written worst-first, which is the order yt-dlp's own extractors produce.
# Writing one best-first inverts every expectation in this file.


def _video(fid: str, height: int) -> dict:
    return {
        "format_id": fid, "ext": "mp4", "protocol": "https", "url": FILLER,
        "vcodec": "avc1.640028", "acodec": "none",
        "height": height, "width": height * 16 // 9, "tbr": height * 2,
    }


def _audio(fid: str, tbr: int) -> dict:
    return {
        "format_id": fid, "ext": "m4a", "protocol": "https", "url": FILLER,
        "vcodec": "none", "acodec": "mp4a.40.2", "tbr": tbr, "abr": tbr,
    }


def _muxed(fid: str, height: int) -> dict:
    return {
        "format_id": fid, "ext": "mp4", "protocol": "https", "url": FILLER,
        "vcodec": "avc1.640028", "acodec": "mp4a.40.2",
        "height": height, "width": height * 16 // 9, "tbr": height * 2,
    }


# The shape the corpus could not represent until a review pointed at it: a
# format carrying NO height. HLS manifests with no RESOLUTION attribute, the
# generic extractor, and a good many embed/CDN extractors all produce these.
# `[height<=720]` DROPS such a format rather than keeping it, so a ladder made
# only of these skips every strictly-bounded rung and lands on the tail every
# single time — which is why the tolerant `[height<=?720]` rungs exist.


def _video_nh(fid: str, tbr: int) -> dict:
    fmt = _video(fid, 720)
    fmt.update({"format_id": fid, "tbr": tbr})
    del fmt["height"], fmt["width"]
    return fmt


def _muxed_nh(fid: str, tbr: int) -> dict:
    fmt = _muxed(fid, 720)
    fmt.update({"format_id": fid, "tbr": tbr})
    del fmt["height"], fmt["width"]
    return fmt


LADDERS: dict[str, list[dict]] = {
    # Split video+audio, the ordinary YouTube shape.
    "has-720": [_audio("a256", 256), _video("v720", 720), _video("v1080", 1080), _video("v2160", 2160)],
    "4k-and-1080-only": [_audio("a256", 256), _video("v1080", 1080), _video("v2160", 2160)],
    "4k-only": [_audio("a256", 256), _video("v2160", 2160)],
    "1080-only-single": [_audio("a256", 256), _video("v1080", 1080)],
    # Muxed-only, the shape the second and fourth rungs exist for.
    "muxed-720-present": [_muxed("m720", 720), _muxed("m1080", 1080), _muxed("m2160", 2160)],
    "muxed-1080-only": [_muxed("m1080", 1080), _muxed("m2160", 2160)],
    # No video at all. Two audio renditions, because the question here is not
    # "does it raise" but WHICH audio it settles on — the transcript is made
    # from whatever this picks.
    "audio-only": [_audio("a64", 64), _audio("a256", 256)],
    # Heightless. Every strictly-bounded rung skips these entirely.
    "no-height-split": [
        _audio("a256", 256), _video_nh("vlo", 150), _video_nh("vmid", 1200),
        _video_nh("vhi", 6000),
    ],
    "no-height-muxed": [_muxed_nh("mlo", 150), _muxed_nh("mhi", 6000)],
    # Mixed: one heightless rendition beside two that are bounded and too big.
    "mixed-heights": [
        _audio("a256", 256), _video_nh("vunk", 900), _video("v1080", 1080),
        _video("v2160", 2160),
    ],
    # Two audio renditions, so shrinking the video can be caught shrinking the
    # audio with it.
    "4k-1080-two-audios": [
        _audio("a64", 64), _audio("a256", 256), _video("v1080", 1080), _video("v2160", 2160),
    ],
}

BOUNDED_LADDERS = ("has-720", "muxed-720-present")


def _incomplete_formats(formats: list[dict]) -> bool:
    """What YoutubeDL computes for this ladder — not a constant.

    This flag is load-bearing and was hardcoded `False` here until a review
    caught it. yt-dlp derives it from the formats themselves, and when it is
    True a bare `b`/`w` stops meaning "best/worst muxed file" and falls back to
    the best/worst *incomplete* stream. On an audio-only ladder that is the
    difference between `w` selecting nothing and `w` selecting the worst audio
    — so a harness that pins it False pins the opposite of the shipped command
    line, and hides the one regression this ladder exists to catch.
    """
    return all(f.get("vcodec") == "none" for f in formats) or all(
        f.get("acodec") == "none" for f in formats
    )


def _select(selector: str, formats: list[dict]) -> tuple[str, ...]:
    """The format ids `selector` picks out of `formats`, or `()` for no match."""
    ydl = yt_dlp.YoutubeDL({"quiet": True, "simulate": True})
    ctx = {
        "formats": list(formats),
        "has_merged_format": False,
        "incomplete_formats": _incomplete_formats(formats),
    }
    picks = list(ydl.build_format_selector(selector)(ctx))
    if not picks:
        return ()
    chosen = picks[0]
    parts = chosen.get("requested_formats") or [chosen]
    return tuple(part["format_id"] for part in parts)


def _rank(ids: tuple[str, ...], formats: list[dict]) -> int:
    """A size proxy for one pick, in units the LADDER chooses.

    Height when every video format in the ladder carries one, bitrate when none
    of them do. Picking the unit per ladder rather than per pick is what makes
    two picks from the same ladder comparable; heights and bitrates are
    different units and are never mixed into one comparison.

    The predecessor of this function read heights only and returned 0 when it
    found none, so on a heightless ladder the monotonicity assertion evaluated
    `0 <= 0` and passed while the selection fell from 6000 kbps to 150. That is
    the failure mode this shape exists to remove: a proxy that silently becomes
    a constant is an assertion that silently stops asserting.
    """
    by_id = {f["format_id"]: f for f in formats}
    video = [f for f in formats if f.get("vcodec") != "none"]
    key = "height" if (video and all(f.get("height") for f in video)) else "tbr"
    picked = [by_id[i] for i in ids if i in by_id]
    scope = [f for f in picked if f.get("vcodec") != "none"] or picked
    return max((f.get(key) or f.get("abr") or 0) for f in scope) if scope else 0


class TestTheLadderShape:
    """Structural. Runs everywhere, including where yt_dlp is absent."""

    def test_the_parser_understands_every_atom(self):
        # Vacuity guard. Every rule below iterates atoms, so a parser that
        # quietly recognised none of them would make all of them pass.
        atoms = [a for rung in _rungs(download.VIDEO_FORMAT) for a in _atoms(rung)]
        assert len(atoms) >= 4
        for atom in atoms:
            kind, extreme = _classify(atom)
            assert kind in ("video", "audio")
            assert extreme in ("best", "worst")
        assert _video_atoms(download.VIDEO_FORMAT), "no video atom found — parser is blind"

    def test_no_rung_selects_the_best_video_without_a_bound(self):
        # The finding itself. Every video atom must be held down one of two
        # ways: a height filter, or asking for the worst rather than the best.
        for atom in _video_atoms(download.VIDEO_FORMAT):
            bounded = _is_height_bounded(atom)
            worst = _classify(atom)[1] == "worst"
            assert bounded or worst, (
                f"{atom!r} in {download.VIDEO_FORMAT!r} selects the best video with no "
                "height bound — a 4K-only upload downloads at 4K"
            )

    def test_the_leading_rungs_are_still_height_bounded(self):
        # The fallback getting safer must not come at the cost of the primary
        # intent: the first thing tried is still a <=720p rendition.
        for rung in _rungs(download.VIDEO_FORMAT)[:2]:
            assert "[height<=720]" in rung, f"leading rung {rung!r} lost its bound"

    def test_the_last_rung_matches_anything(self):
        # Why the rejected alternative was rejected. A filtered last rung can
        # match nothing, and a yt-dlp selector matching nothing is a hard
        # failure, not a large download.
        last = _rungs(download.VIDEO_FORMAT)[-1]
        assert "[" not in last, (
            f"last rung {last!r} carries a filter; a ladder it cannot match "
            "fails the download outright instead of falling back"
        )

    def test_every_split_rung_takes_the_best_audio(self):
        for rung in _rungs(download.VIDEO_FORMAT):
            for atom in _atoms(rung):
                kind, extreme = _classify(atom)
                if kind == "audio":
                    assert extreme == "best", (
                        f"{atom!r} shrinks the audio; the transcript is made from it"
                    )

    def test_the_audio_only_selector_asks_for_best_audio(self):
        # The legitimate configuration. `--audio-only` is already small; there
        # is nothing to cap, and best audio is the whole point of the flag.
        #
        # This pinned the literal `"ba/bestaudio"` until a review observed that
        # `bestaudio` is merely the long form of `ba`, so the second rung could
        # never fire on a ladder where the first did not. Pinning the string
        # made an unreachable rung look load-bearing. What matters is the
        # property, so that is what is asserted: every rung asks for audio, and
        # every rung asks for the best of it.
        rungs = _rungs(download.AUDIO_FORMAT)
        assert rungs, "the audio selector must not be empty"
        for rung in rungs:
            assert _classify(rung) == ("audio", "best")
        assert not _is_height_bounded(download.AUDIO_FORMAT)

    def test_the_baseline_is_actually_different(self):
        # Vacuity guard for the behavioural class: every before/after
        # comparison there is trivially satisfied if these two strings agree.
        assert download.VIDEO_FORMAT != PREVIOUS_VIDEO_FORMAT


@needs_yt_dlp
class TestWhatItSelects:
    """Behavioural. Drives yt-dlp's own selector over synthetic ladders."""

    @pytest.mark.parametrize("name", list(LADDERS))
    def test_the_fallback_never_grows(self, name):
        formats = LADDERS[name]
        before = _select(PREVIOUS_VIDEO_FORMAT, formats)
        after = _select(download.VIDEO_FORMAT, formats)
        assert _rank(after, formats) <= _rank(before, formats), (
            f"on ladder {name!r} the new selector picks {after} "
            f"(rank {_rank(after, formats)}), bigger than the old {before} "
            f"(rank {_rank(before, formats)})"
        )

    @pytest.mark.parametrize(
        "name", ["no-height-split", "no-height-muxed", "mixed-heights"]
    )
    def test_the_size_proxy_is_not_a_constant_on_these_ladders(self, name):
        # Vacuity guard for the assertion above, and the reason `_rank`
        # replaced a height-only helper. That predecessor returned 0 for every
        # pick on a heightless ladder, so `never_grows` evaluated `0 <= 0` and
        # passed while the selection fell from 6000 kbps to 150. A proxy that
        # collapses to a constant is an assertion that has stopped asserting.
        formats = LADDERS[name]
        assert _rank(_select(PREVIOUS_VIDEO_FORMAT, formats), formats) > 0
        assert _rank(_select(download.VIDEO_FORMAT, formats), formats) > 0

    @pytest.mark.parametrize("name", BOUNDED_LADDERS)
    def test_a_ladder_with_720_is_untouched(self, name):
        # The legitimate configuration, executed. A change that started
        # shrinking everything would pass every test above and fail here.
        formats = LADDERS[name]
        assert _select(download.VIDEO_FORMAT, formats) == _select(
            PREVIOUS_VIDEO_FORMAT, formats
        )
        assert _rank(_select(download.VIDEO_FORMAT, formats), formats) == 720

    def test_a_4k_and_1080_ladder_falls_back_to_1080(self):
        # The finding's own case, split-stream form.
        formats = LADDERS["4k-and-1080-only"]
        assert _select(PREVIOUS_VIDEO_FORMAT, formats) == ("v2160", "a256")
        assert _select(download.VIDEO_FORMAT, formats) == ("v1080", "a256")

    def test_a_muxed_4k_and_1080_ladder_falls_back_to_1080(self):
        # And the muxed form, which takes the fourth rung rather than the third.
        formats = LADDERS["muxed-1080-only"]
        assert _select(PREVIOUS_VIDEO_FORMAT, formats) == ("m2160",)
        assert _select(download.VIDEO_FORMAT, formats) == ("m1080",)

    def test_a_4k_only_ladder_still_downloads(self):
        # The reason the tail is unfiltered rather than bounded at 1080. There
        # is one rendition; it is 4K; the alternative to a large download here
        # is no download.
        formats = LADDERS["4k-only"]
        assert _select(download.VIDEO_FORMAT, formats) == ("v2160", "a256")

    def test_best_audio_survives_the_shrink(self):
        # Shrinking the video must not quietly shrink the transcript's source.
        formats = LADDERS["4k-1080-two-audios"]
        picked = _select(download.VIDEO_FORMAT, formats)
        assert "a256" in picked and "a64" not in picked

    def test_an_audio_only_ladder_keeps_the_best_audio(self):
        # This asserted `== ()` until a review caught that the harness was
        # forcing `incomplete_formats=False`. With the flag computed the way
        # yt-dlp computes it, the tail's bare `w` DOES match here — it falls
        # back to the worst incomplete stream — so the shipped ladder quietly
        # downgraded 256k to 64k on any audio-only source. That audio is the
        # transcript's only input, and `_pick_video` accepts `.m4a`/`.mp3`/
        # `.opus`, so the run continues on it without a word.
        #
        # `--audio-only` was never the affected path; it uses AUDIO_FORMAT.
        # This is a plain `/moviola <podcast-url>` with no flags at all.
        formats = LADDERS["audio-only"]
        assert _select(PREVIOUS_VIDEO_FORMAT, formats) == ("a256",)
        assert _select(download.VIDEO_FORMAT, formats) == ("a256",)

    @pytest.mark.parametrize(
        "name,expected", [("no-height-split", "vhi"), ("no-height-muxed", "mhi")]
    )
    def test_a_heightless_ladder_is_not_downgraded(self, name, expected):
        # `[height<=720]` DROPS a format whose height is unknown, so a ladder
        # of these skips both strictly-bounded rungs. Without the tolerant
        # `[height<=?720]` pair it lands on the tail every time and takes the
        # smallest rendition on offer — 150 kbps here, which is not a usable
        # visual input for a tool whose whole output is frames an agent reads.
        # There is no 720p bound protecting these: with heights unknown the
        # tail has no floor at all.
        formats = LADDERS[name]
        assert expected in _select(download.VIDEO_FORMAT, formats)
        assert _select(download.VIDEO_FORMAT, formats) == _select(
            PREVIOUS_VIDEO_FORMAT, formats
        )

    def test_a_heightless_rendition_is_preferred_over_a_bounded_oversized_one(self):
        # The mixed case. `vunk` might be anything, but the two it is up
        # against are known to be over the bound, so it is the only candidate
        # that could be under it. Shrinks relative to the old selector's 4K.
        formats = LADDERS["mixed-heights"]
        assert _select(PREVIOUS_VIDEO_FORMAT, formats) == ("v2160", "a256")
        assert _select(download.VIDEO_FORMAT, formats) == ("vunk", "a256")


class TestTheArgvUsesIt:
    """The constants are what actually reach yt-dlp — not decoration."""

    def _argv(self, monkeypatch, tmp_path, **kwargs) -> list[str]:
        seen: list[list[str]] = []

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr(download.shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            download.subprocess, "run", lambda cmd, *a, **k: (seen.append(list(cmd)), _Result())[1]
        )
        # No file appears, so this raises after the argv is already built —
        # which is all that is being inspected.
        with pytest.raises(SystemExit):
            download.download_url("https://example.invalid/v", tmp_path / "dl", **kwargs)
        assert seen, "yt-dlp was never invoked"
        return seen[0]

    def test_the_video_format_reaches_yt_dlp(self, monkeypatch, tmp_path):
        argv = self._argv(monkeypatch, tmp_path)
        assert argv[argv.index("-f") + 1] == download.VIDEO_FORMAT

    def test_audio_only_reaches_yt_dlp(self, monkeypatch, tmp_path):
        argv = self._argv(monkeypatch, tmp_path, audio_only=True)
        assert argv[argv.index("-f") + 1] == download.AUDIO_FORMAT

    def test_no_format_sort_is_passed(self, monkeypatch, tmp_path):
        # `wv*`/`w` mean "worst by the active sort order". moviola passes no
        # sort, so that order is yt-dlp's default, which leads on resolution.
        # A `--format-sort` here would redefine "worst" and this file's
        # premise with it.
        argv = self._argv(monkeypatch, tmp_path)
        assert "--format-sort" not in argv
        assert "-S" not in argv
