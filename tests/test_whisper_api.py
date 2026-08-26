"""The paid API path: request assembly, retries, response parsing, cost notice.

Everything in this file was uncovered. `tests/test_whisper.py` tests the pure
maths around the API call — chunk planning, timestamp shifting, range guards —
and stops at the edge of the network, so `_build_multipart`, `_post_whisper`,
`_retry_after` and `_segments_from_response` had no tests at all. That gap is
what let `_segments_from_response` trust the response shape: it parsed
`data.get("segments")` and `seg.get("text")` on whatever a 200 body decoded to,
and a JSON array or a bare string reached `.get()` on the wrong type. Nothing
caught the resulting AttributeError, so a malformed payload cost the whole
report, frames included.

No socket is opened here: `urlopen` is replaced and `time.sleep` is neutered so
the retry ladder runs at full speed. What this file therefore CANNOT see is
whether the real endpoints accept the multipart body it builds — that needs a
live key and a paid request, and neither belongs in a test suite.
"""
from __future__ import annotations

import email.message
import io
import json
import urllib.error
from pathlib import Path

import pytest

import whisper
from transcribe import format_transcript


def _http_error(code: int, headers: dict[str, str] | None = None, body: bytes = b"") -> urllib.error.HTTPError:
    hdrs = email.message.Message()
    for k, v in (headers or {}).items():
        hdrs[k] = v
    return urllib.error.HTTPError(
        "https://api.groq.com/x", code, "boom", hdrs, io.BytesIO(body)
    )


class _Response:
    """The slice of urlopen's return value _post_whisper actually touches."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(whisper.time, "sleep", lambda _s: None)


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 64)
    return path


class TestSegmentsFromResponseIsShapeSafe:
    """A 200 body is not a promise about its own shape."""

    def test_the_documented_shape_parses(self) -> None:
        out = whisper._segments_from_response(
            {"segments": [{"start": 1.234, "end": 2.5, "text": "  hello  "}]}
        )
        assert out == [{"start": 1.23, "end": 2.5, "text": "hello"}]

    @pytest.mark.parametrize("body", [[], ["a"], "a string", 7, None, True])
    def test_a_non_object_body_is_a_named_error_not_a_traceback(self, body: object) -> None:
        # This is the crash. Before the guard, every one of these reached
        # .get() on the wrong type and raised AttributeError, which no caller
        # caught. SystemExit is what moviola.py's fallback block already
        # handles, so the report degrades to frames-only instead of dying.
        with pytest.raises(SystemExit) as exc:
            whisper._segments_from_response(body)
        assert "expected an object" in str(exc.value)

    @pytest.mark.parametrize("segments", ["not a list", 3, {"a": 1}])
    def test_a_non_list_segments_field_falls_through_to_the_full_text(
        self, segments: object
    ) -> None:
        out = whisper._segments_from_response({"segments": segments, "text": "fallback"})
        assert out == [{"start": 0.0, "end": 0.0, "text": "fallback"}]

    def test_non_dict_entries_inside_segments_are_skipped_not_fatal(self) -> None:
        out = whisper._segments_from_response(
            {"segments": ["junk", None, 5, {"start": 0, "end": 1, "text": "kept"}]}
        )
        assert out == [{"start": 0.0, "end": 1.0, "text": "kept"}]

    @pytest.mark.parametrize("text", [None, 5, [], {}, "", "   "])
    def test_a_segment_without_usable_text_is_skipped(self, text: object) -> None:
        assert whisper._segments_from_response({"segments": [{"text": text}]}) == []

    @pytest.mark.parametrize(
        "stamp", [None, "abc", [], {}, float("nan"), float("inf"), float("-inf")]
    )
    def test_a_garbled_timestamp_costs_the_timestamp_not_the_text(
        self, stamp: object
    ) -> None:
        out = whisper._segments_from_response(
            {"segments": [{"start": stamp, "end": stamp, "text": "kept"}]}
        )
        # Assert the VALUE, not just that a segment survived. An earlier version
        # of this test checked only `len(out) == 1 and text == "kept"`, which
        # passed for NaN — the one input where the guarantee did not hold, since
        # float() and round() both accept it. A test named for a guarantee it
        # never checks is worse than no test.
        assert out == [{"start": 0.0, "end": 0.0, "text": "kept"}]

    @pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
    def test_the_non_standard_json_float_tokens_cannot_reach_the_report(
        self, token: str
    ) -> None:
        # json.loads admits these by default, so they are not hypothetical: a
        # gateway that emits them produces a segment that survives parsing and
        # then raises ValueError/OverflowError at int(seg["start"]) inside
        # format_transcript — which runs over every chunk at once, so one bad
        # timestamp would cost the entire transcript.
        body = json.loads('{"segments":[{"start":%s,"end":%s,"text":"kept"}]}' % (token, token))
        out = whisper._segments_from_response(body)
        assert out == [{"start": 0.0, "end": 0.0, "text": "kept"}]
        format_transcript(out)  # would raise before the isfinite guard

    def test_a_numeric_string_timestamp_is_accepted(self) -> None:
        out = whisper._segments_from_response(
            {"segments": [{"start": "1.5", "end": "2", "text": "x"}]}
        )
        assert out[0] == {"start": 1.5, "end": 2.0, "text": "x"}

    def test_an_empty_object_yields_nothing_rather_than_raising(self) -> None:
        assert whisper._segments_from_response({}) == []

    def test_a_non_string_top_level_text_does_not_reach_strip(self) -> None:
        assert whisper._segments_from_response({"segments": [], "text": 42}) == []


class TestPostWhisperRetries:
    def test_a_json_object_body_is_returned_verbatim(
        self, monkeypatch: pytest.MonkeyPatch, audio: Path, no_sleep: None
    ) -> None:
        monkeypatch.setattr(whisper, "urlopen", lambda *a, **k: _Response(b'{"text":"hi"}'))
        assert whisper._post_whisper("https://x", "k", "m", audio) == {"text": "hi"}

    def test_a_non_json_200_is_a_named_error(
        self, monkeypatch: pytest.MonkeyPatch, audio: Path, no_sleep: None
    ) -> None:
        # An HTML error page served with a 200 is the classic captive-portal or
        # misrouted-proxy answer.
        monkeypatch.setattr(whisper, "urlopen", lambda *a, **k: _Response(b"<html>nope"))
        with pytest.raises(SystemExit) as exc:
            whisper._post_whisper("https://x", "k", "m", audio)
        assert "non-JSON response" in str(exc.value)

    def test_a_400_does_not_retry(
        self, monkeypatch: pytest.MonkeyPatch, audio: Path, no_sleep: None
    ) -> None:
        calls = []

        def fail(*a: object, **k: object) -> None:
            calls.append(1)
            raise _http_error(400, body=b"bad model")

        monkeypatch.setattr(whisper, "urlopen", fail)
        with pytest.raises(SystemExit) as exc:
            whisper._post_whisper("https://x", "k", "m", audio)
        # Retrying a client error just bills the user again for the same answer.
        assert len(calls) == 1
        assert "bad model" in str(exc.value)

    def test_a_429_retries_and_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, audio: Path, no_sleep: None
    ) -> None:
        state = {"n": 0}

        def flaky(*a: object, **k: object) -> _Response:
            state["n"] += 1
            if state["n"] == 1:
                raise _http_error(429, {"Retry-After": "0"})
            return _Response(b'{"text":"ok"}')

        monkeypatch.setattr(whisper, "urlopen", flaky)
        assert whisper._post_whisper("https://x", "k", "m", audio) == {"text": "ok"}
        assert state["n"] == 2

    def test_repeated_429s_give_up_at_the_dedicated_ceiling(
        self, monkeypatch: pytest.MonkeyPatch, audio: Path, no_sleep: None
    ) -> None:
        calls = []

        def throttled(*a: object, **k: object) -> None:
            calls.append(1)
            raise _http_error(429, {"Retry-After": "0"})

        monkeypatch.setattr(whisper, "urlopen", throttled)
        with pytest.raises(SystemExit):
            whisper._post_whisper("https://x", "k", "m", audio)
        # MAX_429_RETRIES, not MAX_ATTEMPTS: a rate limit gets a shorter leash
        # than a transient 5xx because every attempt is another billed request.
        assert len(calls) == whisper.MAX_429_RETRIES

    def test_a_500_is_retried_up_to_the_attempt_ceiling(
        self, monkeypatch: pytest.MonkeyPatch, audio: Path, no_sleep: None
    ) -> None:
        calls = []

        def down(*a: object, **k: object) -> None:
            calls.append(1)
            raise _http_error(503)

        monkeypatch.setattr(whisper, "urlopen", down)
        with pytest.raises(SystemExit) as exc:
            whisper._post_whisper("https://x", "k", "m", audio)
        assert len(calls) == whisper.MAX_ATTEMPTS
        assert "after" in str(exc.value)

    def test_a_network_error_is_retried_then_reported(
        self, monkeypatch: pytest.MonkeyPatch, audio: Path, no_sleep: None
    ) -> None:
        calls = []

        def broken(*a: object, **k: object) -> None:
            calls.append(1)
            raise urllib.error.URLError("no route to host")

        monkeypatch.setattr(whisper, "urlopen", broken)
        with pytest.raises(SystemExit):
            whisper._post_whisper("https://x", "k", "m", audio)
        assert len(calls) == whisper.MAX_ATTEMPTS

    def test_the_api_key_never_reaches_the_error_message(
        self, monkeypatch: pytest.MonkeyPatch, audio: Path, no_sleep: None
    ) -> None:
        secret = "not-a-real-key-abcdef"

        def refuse(*a: object, **k: object) -> None:
            raise _http_error(400)

        monkeypatch.setattr(whisper, "urlopen", refuse)
        with pytest.raises(SystemExit) as exc:
            whisper._post_whisper("https://x", secret, "m", audio)
        assert secret not in str(exc.value)


class TestRetryAfter:
    def test_a_numeric_header_is_honoured(self) -> None:
        assert whisper._retry_after(_http_error(429, {"Retry-After": "2.5"})) == 2.5

    def test_a_missing_header_yields_none(self) -> None:
        assert whisper._retry_after(_http_error(429)) is None

    def test_an_http_date_header_yields_none_rather_than_raising(self) -> None:
        # RFC 7231 allows a date here. Falling back to the exponential ladder is
        # the honest answer; crashing on a spec-legal header is not.
        stamp = "Wed, 21 Oct 2015 07:28:00 GMT"
        assert whisper._retry_after(_http_error(429, {"Retry-After": stamp})) is None


class TestBuildMultipart:
    def test_the_returned_boundary_delimits_the_returned_body(self, audio: Path) -> None:
        body, boundary = whisper._build_multipart({"model": "whisper-1"}, audio)
        assert body.count(f"--{boundary}".encode()) == 3  # field, file, terminator
        assert body.endswith(f"--{boundary}--\r\n".encode())

    def test_the_audio_bytes_are_present_unmodified(self, audio: Path) -> None:
        body, _ = whisper._build_multipart({}, audio)
        assert audio.read_bytes() in body

    def test_each_field_becomes_its_own_part(self, audio: Path) -> None:
        body, _ = whisper._build_multipart({"model": "m", "response_format": "j"}, audio)
        assert b'name="model"' in body and b'name="response_format"' in body
        assert b'name="file"; filename="audio.mp3"' in body

    def test_every_boundary_is_unique_across_calls(self, audio: Path) -> None:
        # A fixed boundary that happened to occur inside the audio would corrupt
        # the request in a way that only shows up on some files.
        assert whisper._build_multipart({}, audio)[1] != whisper._build_multipart({}, audio)[1]


class TestUploadIsAnnouncedBeforeItIsBilled:
    """The frame path warns before it spends; this path did not."""

    def test_a_short_clip_reports_size_destination_and_request_count(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        whisper._announce_upload("groq", 2 * 1024 * 1024)
        err = capsys.readouterr().err
        assert "api.groq.com" in err
        assert "in 1 request…" in err

    def test_the_request_count_tracks_the_upload_cap(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        whisper._announce_upload("openai", whisper.MAX_UPLOAD_BYTES * 3 + 1)
        assert "in 4 requests…" in capsys.readouterr().err

    def test_a_long_job_is_warned_about_before_the_first_request(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        big = int(whisper.AUDIO_BYTES_PER_MINUTE * (whisper.COST_WARN_MINUTES + 1))
        whisper._announce_upload("groq", big)
        err = capsys.readouterr().err
        assert "Warning" in err
        assert "--no-whisper" in err and "faster-whisper" in err

    def test_a_job_under_the_threshold_is_not_warned_about(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        small = int(whisper.AUDIO_BYTES_PER_MINUTE * (whisper.COST_WARN_MINUTES - 1))
        whisper._announce_upload("groq", small)
        assert "Warning" not in capsys.readouterr().err

    def test_it_does_not_cap_anything(self, capsys: pytest.CaptureFixture) -> None:
        # A stated NON-GOAL, pinned so it cannot drift into a hard limit: this
        # makes spend visible, it does not refuse the job. Choosing a ceiling
        # for someone else's budget is not this script's call.
        whisper._announce_upload("groq", int(whisper.AUDIO_BYTES_PER_MINUTE * 600))
        assert "Warning" in capsys.readouterr().err  # warned, and nothing raised
