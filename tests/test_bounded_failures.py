"""Every failure mode is bounded, and every test in the suite is honest.

Four findings, two of each kind.

Unbounded failure modes:

  * `_retry_after` returned whatever number the server put in `Retry-After` and
    that value went straight to `time.sleep`. A provider having a bad day, a
    misconfigured proxy, or anyone able to answer the request can park moviola
    for as long as they like — `Retry-After: 86400` is a day — with nothing on
    stderr after the one retry notice. A negative value is worse than long: it
    reaches `time.sleep` and raises ValueError from inside the error handler.
  * Chunk files are written to `<work>/chunks/` and never removed. Chunking only
    happens on audio over the 24 MB upload cap, so the leak is proportional to
    the longest videos, and with `--out-dir` — which SKILL.md tells the agent to
    reuse — it accumulates across runs rather than dying with a temp directory.

Tests that were not testing what they appeared to:

  * `moviola.py`'s argparse `choices` for `--detail` and `--whisper` were string
    literals duplicating `config.DETAILS` and `config.WHISPER_BACKENDS`. Two
    lists of the same set, in two files, with nothing comparing them: adding a
    backend to config would leave the flag rejecting it, and the failure would
    read as "that backend does not exist".
  * `test_check_setup_hook._run` popped four variables out of `os.environ` with
    no monkeypatch and no restore. It is dead code — `subprocess.run(env=env)`
    hands the child a closed dict, so the child never saw the parent's
    environment either way — and its only effect is on the pytest process
    itself, where it silently deletes those variables for every test that runs
    afterwards.

NON-GOALS, so a green run is not read as more than it is:

  * `_truthy`'s tri-state is checked here at the config layer, where it is
    produced. That the local backend then honours None differently from False is
    local_whisper's own test's business.
  * Bounding the delay does not make the request succeed. A rate-limited run
    still gives up at MAX_429_RETRIES; it just gives up promptly.
  * Cleaning the chunk files says nothing about the extracted audio or the work
    directory itself, which still outlive the run and are recorded in TODOS.md.
  * Comparing the parser's choices to config's sets proves the two agree. It
    does not prove either is the RIGHT set — a value wrong in both places is
    invisible from here.

Every value written below is inert filler. Nothing here reads a real credential.
"""
from __future__ import annotations

import email.message
import io
import os
import urllib.error
from pathlib import Path

import pytest

import config
import moviola
import test_check_setup_hook
import whisper

FILLER = "placeholder-value-not-a-credential"


def _http_error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    hdrs = email.message.Message()
    for key, value in (headers or {}).items():
        hdrs[key] = value
    return urllib.error.HTTPError("https://example.invalid/x", code, "boom", hdrs, io.BytesIO(b""))


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"filler audio bytes")
    return path


class _CountingBody:
    """A response body that serves what it is asked for and counts it.

    A real socket-backed body answers `read()` with no argument by accumulating
    everything the far end sends into one bytes object. This one cannot: an
    endless stream would hang the suite, so the unbounded call is answered with
    `_UNBOUNDED` bytes — large enough that reading it whole is unmistakable in
    `served`, small enough that the test costs nothing. That substitution is the
    only artificial thing here, and it is the reason `served` rather than a
    MemoryError is what gets asserted on.
    """

    _UNBOUNDED = 4 * 1024 * 1024

    def __init__(self, unit: bytes = b"x") -> None:
        self.unit = unit
        self.served = 0
        self.sizes: list[int | None] = []

    def read(self, size: int | None = None) -> bytes:
        self.sizes.append(size)
        want = self._UNBOUNDED if size is None or size < 0 else size
        out = (self.unit * (want // len(self.unit) + 1))[:want]
        self.served += len(out)
        return out

    def close(self) -> None:  # `addbase` closes the fp it was handed
        pass


def _http_error_with_body(body: _CountingBody) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid/x", 500, "boom", email.message.Message(), body
    )


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record what the retry ladder ASKS for instead of waiting for it."""
    recorded: list[float] = []

    def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(whisper.time, "sleep", fake_sleep)
    return recorded


class TestNoServerCanParkTheRun:
    def _throttle(self, monkeypatch: pytest.MonkeyPatch, header: str) -> None:
        def throttled(*args: object, **kwargs: object) -> None:
            raise _http_error(429, {"Retry-After": header})

        monkeypatch.setattr(whisper, "urlopen", throttled)

    def test_a_days_long_retry_after_is_capped(
        self, monkeypatch: pytest.MonkeyPatch, audio: Path, sleeps: list[float]
    ) -> None:
        # 86400 is not hypothetical: it is what a provider returns when it has
        # decided you are done for the day. Honouring it verbatim means a run
        # that never returns and never says anything more.
        self._throttle(monkeypatch, "86400")
        with pytest.raises(SystemExit):
            whisper._post_whisper("https://x", FILLER, "m", audio)
        assert sleeps
        assert max(sleeps) <= whisper.MAX_RETRY_DELAY

    def test_a_negative_retry_after_never_reaches_sleep(
        self, monkeypatch: pytest.MonkeyPatch, audio: Path, sleeps: list[float]
    ) -> None:
        # time.sleep raises ValueError on a negative argument, and it would do so
        # from inside the handler for the error being retried — turning a rate
        # limit into a traceback about sleeping.
        self._throttle(monkeypatch, "-5")
        with pytest.raises(SystemExit):
            whisper._post_whisper("https://x", FILLER, "m", audio)
        assert all(s >= 0 for s in sleeps)

    def test_a_nonsense_retry_after_falls_back_to_the_ladder(
        self, monkeypatch: pytest.MonkeyPatch, audio: Path, sleeps: list[float]
    ) -> None:
        self._throttle(monkeypatch, "whenever you like")
        with pytest.raises(SystemExit):
            whisper._post_whisper("https://x", FILLER, "m", audio)
        assert all(0 <= s <= whisper.MAX_RETRY_DELAY for s in sleeps)

    def test_a_reasonable_retry_after_is_still_honoured_verbatim(self) -> None:
        # The cap must not become a rewrite: a server asking for 2.5 seconds
        # knows something this program does not.
        assert whisper._retry_after(_http_error(429, {"Retry-After": "2.5"})) == 2.5

    def test_the_cap_is_returned_rather_than_the_headers_number(self) -> None:
        assert whisper._retry_after(_http_error(429, {"Retry-After": "999999"})) == (
            whisper.MAX_RETRY_DELAY
        )

    def test_every_delay_the_ladder_itself_produces_is_bounded(
        self, monkeypatch: pytest.MonkeyPatch, audio: Path, sleeps: list[float]
    ) -> None:
        # The 5xx path computes its own backoff and never consults the server.
        # It is inside the cap today; this is what notices if the ladder grows.
        def broken(*args: object, **kwargs: object) -> None:
            raise _http_error(503)

        monkeypatch.setattr(whisper, "urlopen", broken)
        with pytest.raises(SystemExit):
            whisper._post_whisper("https://x", FILLER, "m", audio)
        assert sleeps
        assert all(0 <= s <= whisper.MAX_RETRY_DELAY for s in sleeps)


class TestChunkFilesDoNotOutliveTheTranscript:
    def _chunked_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail: bool = False
    ) -> Path:
        """Drive transcribe_video down the chunked branch; return the chunks dir."""
        work = tmp_path / "work"
        work.mkdir()
        audio_out = work / "audio.mp3"
        chunk_dir = work / "chunks"

        oversized = work / "big.mp3"
        with oversized.open("wb") as handle:
            handle.truncate(whisper.MAX_UPLOAD_BYTES * 2 + 1)

        def fake_split(full_audio: Path, out_dir: Path, plan: list) -> list:
            out_dir.mkdir(parents=True, exist_ok=True)
            made = []
            for index, (offset, duration) in enumerate(plan):
                path = out_dir / f"chunk_{index:03d}.mp3"
                path.write_bytes(b"filler")
                made.append(whisper.AudioChunk(path, offset, duration))
            return made

        def transcribed(path: Path) -> list[dict]:
            if fail:
                raise SystemExit("no")
            return [{"start": 0.0, "end": 1.0, "text": "filler"}]

        monkeypatch.setattr(whisper, "extract_audio", lambda *a, **k: oversized)
        monkeypatch.setattr(whisper, "audio_duration", lambda *a, **k: 900.0)
        monkeypatch.setattr(whisper, "split_audio", fake_split)
        monkeypatch.setattr(whisper, "_transcribe_file", lambda *a, **k: transcribed(a[-1]))

        try:
            whisper.transcribe_video(
                "video.mp4", audio_out, backend="groq", api_key=FILLER
            )
        except SystemExit:
            if not fail:
                raise
        return chunk_dir

    def test_a_successful_run_leaves_no_chunks_behind(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        chunk_dir = self._chunked_run(monkeypatch, tmp_path)
        assert list(chunk_dir.glob("chunk_*.mp3")) == []

    def test_a_failed_run_leaves_no_chunks_behind_either(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The failing path is the one that leaks in practice: it is reached when
        # the audio is longest, which is exactly when the chunks are largest.
        chunk_dir = self._chunked_run(monkeypatch, tmp_path, fail=True)
        assert list(chunk_dir.glob("chunk_*.mp3")) == []

    def test_split_audio_clears_a_previous_runs_chunks(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A run that produces fewer chunks than the last one used to leave the
        # tail of the old set sitting next to the new one, indistinguishable by
        # name from a chunk of this audio.
        work = tmp_path / "chunks"
        work.mkdir()
        for stale in range(5):
            (work / f"chunk_{stale:03d}.mp3").write_bytes(b"yesterday")
        source = tmp_path / "audio.mp3"
        source.write_bytes(b"filler")

        class _Ok:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> _Ok:
            Path(cmd[-1]).write_bytes(b"today")
            return _Ok()

        monkeypatch.setattr(whisper.shutil, "which", lambda _n: "/usr/bin/ffmpeg")
        monkeypatch.setattr(whisper.subprocess, "run", fake_run)
        whisper.split_audio(source, work, [(0.0, 10.0), (10.0, 10.0)])

        assert sorted(p.name for p in work.glob("chunk_*.mp3")) == [
            "chunk_000.mp3",
            "chunk_001.mp3",
        ]


class TestTheFlagsAcceptExactlyWhatTheConfigAccepts:
    def test_the_detail_flag_offers_the_configured_set(self) -> None:
        parser = moviola.build_parser()
        choices = _choices(parser, "--detail")
        assert set(choices) == set(config.DETAILS)

    def test_the_whisper_flag_offers_the_configured_set(self) -> None:
        parser = moviola.build_parser()
        choices = _choices(parser, "--whisper")
        assert set(choices) == set(config.WHISPER_BACKENDS)

    def test_the_detail_flag_lists_them_cheapest_first(self) -> None:
        # --help is where a user decides what to pass, and the order is the cost
        # progression. A set would render it alphabetically and lose that.
        assert _choices(moviola.build_parser(), "--detail") == list(config.DETAILS)

    def test_an_unknown_detail_is_still_rejected(self) -> None:
        with pytest.raises(SystemExit):
            moviola.build_parser().parse_args(["src", "--detail", "cinematic"])


def _choices(parser: object, flag: str) -> list[str]:
    for action in parser._actions:  # type: ignore[attr-defined]
        if flag in action.option_strings:
            return list(action.choices)
    raise AssertionError(f"{flag} is not a flag on this parser")


class TestTheConfigsThirdStateSurvives:
    """`whisper_offline` is the only tri-state setting, and it is load-bearing."""

    def test_unset_is_none_and_not_false(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # None means "no opinion, let HF_HUB_OFFLINE answer". False means "this
        # user said no". local_whisper.transcribe_local branches on exactly that
        # difference, so collapsing them silently overrides the environment.
        monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "absent.env")
        monkeypatch.delenv("MOVIOLA_WHISPER_OFFLINE", raising=False)
        assert config.get_config()["whisper_offline"] is None

    @pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", "Off"])
    def test_the_documented_falsey_words_are_false(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
    ) -> None:
        monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "absent.env")
        monkeypatch.setenv("MOVIOLA_WHISPER_OFFLINE", value)
        assert config.get_config()["whisper_offline"] is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "anything"])
    def test_everything_else_is_true(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
    ) -> None:
        monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "absent.env")
        monkeypatch.setenv("MOVIOLA_WHISPER_OFFLINE", value)
        assert config.get_config()["whisper_offline"] is True

    def test_whitespace_only_is_unset_rather_than_true(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # `_setting` strips, so a scaffolded-and-never-filled value must land in
        # the unset state rather than reading as the string " ".
        monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "absent.env")
        monkeypatch.setenv("MOVIOLA_WHISPER_OFFLINE", "   ")
        assert config.get_config()["whisper_offline"] is None


class TestTheHookTestsDoNotDamageTheProcessTheyRunIn:
    def test_running_the_hook_helper_leaves_the_environment_alone(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The helper popped GROQ_API_KEY and three others out of os.environ with
        # no restore. Every test that ran afterwards in the same process saw an
        # environment the developer did not have — which is how a suite starts
        # passing for reasons nobody chose.
        monkeypatch.setenv("GROQ_API_KEY", FILLER)
        monkeypatch.setenv("MOVIOLA_WHISPER", "local")
        test_check_setup_hook._run(tmp_path)
        assert os.environ.get("GROQ_API_KEY") == FILLER
        assert os.environ.get("MOVIOLA_WHISPER") == "local"

    def test_the_child_never_sees_the_parents_environment_anyway(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # This is why the pops were dead code as well as harmful: `env=env`
        # hands the child a closed dict, so an ambient key in the developer's
        # shell cannot reach the hook. That isolation is the thing worth pinning.
        monkeypatch.setenv("GROQ_API_KEY", FILLER)
        result = test_check_setup_hook._run(tmp_path, binaries=True, local_whisper=False)
        # The hook has a distinct sentence for "a key is set in this
        # environment". Seeing it here would mean the parent's variable reached
        # the child, and every hook test would be reading the developer's shell.
        assert "An API key is set in this environment" not in result.stdout


class TestAnErrorBodyIsBoundedBeforeItIsRead:
    """The read is bounded, not only the report.

    `_read_error_body` sliced `[:400]` off the DECODED body, which bounds what
    gets printed and says nothing about what gets allocated. `exc.read()` with
    no argument pulls the whole response into memory first — a server answering
    a 500 with a gigabyte-long body gets the whole gigabyte in before Python
    takes 400 characters off the front. It is on an error path, so a healthy run
    never touches it, and the failure is a MemoryError raised inside the handler
    for the error being reported rather than anything silent. That is still a
    failure mode a stranger chooses.

    NON-GOALS:

      * This is a resource bound and not a forgery fence. `stderr_line` closed
        that channel and nothing here reopens it — a body is fenced whether it
        arrived truncated or whole, and the fence still runs after the slice so
        it can close a bidi scope the slice orphaned.
      * `read(n)` is a request, not a guarantee. A stream is free to answer with
        fewer bytes, and nothing here re-reads to fill the quota, because the
        report wants a prefix rather than a complete body.
      * Bounding the read says nothing about the rest of the response. Headers
        are read by urllib before this function is reached and are not bounded
        here; a hostile header set is a separate finding with a separate owner.
      * The 4 MB an unbounded read serves in these tests is a stand-in chosen to
        terminate. It is not a claim about how large a real hostile body would
        be — the real number is whatever the far end feels like sending, which
        is exactly the problem.
    """

    def test_the_read_asks_for_a_bound(self) -> None:
        body = _CountingBody()
        whisper._read_error_body(_http_error_with_body(body))
        assert body.sizes, "the body was never read at all"
        # `None` or a negative is the unbounded call. Anything else is a number
        # this program chose, which is the whole property.
        assert all(
            size is not None and size > 0 for size in body.sizes
        ), f"an unbounded read reached the stream: {body.sizes}"

    def test_a_gigantic_body_does_not_all_reach_memory(self) -> None:
        body = _CountingBody()
        whisper._read_error_body(_http_error_with_body(body))
        assert body.served <= whisper.MAX_ERROR_BODY_BYTES, (
            f"{body.served} bytes were pulled in for a 400-character report"
        )

    def test_the_bound_is_wide_enough_for_the_whole_report(self) -> None:
        # The report is 400 CHARACTERS and the bound is in BYTES, so the bound
        # has to clear 400 times UTF-8's widest encoding or the fix would have
        # quietly shortened every multi-byte error message. This is the
        # must-not-fire half: a bound of 400 passes the two tests above and
        # fails this one.
        assert whisper.MAX_ERROR_BODY_BYTES >= 400 * 4

    def test_four_hundred_wide_characters_survive_intact(self) -> None:
        # Four-byte characters, which is the worst case the arithmetic above is
        # sized for. All 400 must come back — the bound must not be visible in
        # the output for any body a human would actually want to read.
        body = _CountingBody(unit="☃🚀".encode())
        out = whisper._read_error_body(_http_error_with_body(body))
        assert out.count("🚀") + out.count("☃") == 400

    def test_a_sequence_cut_by_the_bound_cannot_reach_the_report(self) -> None:
        # `read(n)` can stop mid-character, and `errors="replace"` turns that
        # tail into U+FFFD. It is harmless only because the bound clears 400
        # characters with room to spare, so the damaged tail is always past the
        # slice. Pinned rather than assumed: a bound tightened to near 400 bytes
        # would put the replacement character inside the report.
        body = _CountingBody(unit="🚀".encode())
        out = whisper._read_error_body(_http_error_with_body(body))
        assert "�" not in out

    def test_an_empty_body_is_still_reported_as_nothing(self) -> None:
        # The bound is the only change. A server that says nothing still gets
        # an empty string rather than a dangling em-dash on the diagnostic.
        empty = _CountingBody()
        empty._UNBOUNDED = 0

        class _Empty(_CountingBody):
            def read(self, size: int | None = None) -> bytes:
                self.sizes.append(size)
                return b""

        assert whisper._read_error_body(_http_error_with_body(_Empty())) == ""

    def test_a_stream_that_raises_is_still_survivable(self) -> None:
        # The bound sits inside the same try. A body that raises on read gets
        # an empty report, not an exception thrown from inside the handler for
        # the exception being reported.
        class _Angry(_CountingBody):
            def read(self, size: int | None = None) -> bytes:
                raise OSError("connection reset")

        assert whisper._read_error_body(_http_error_with_body(_Angry())) == ""
