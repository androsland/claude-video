"""An ambient API key is not consent to upload.

`resolve_backend`'s local-first ordering only protects a machine that HAS
faster-whisper. On the state every machine starts in — no local backend — an
unpinned run used to fall through to whatever GROQ_API_KEY or OPENAI_API_KEY it
could see, an ambient one exported for some entirely different tool included,
and upload the audio.

These tests exercise the REAL `load_api_key` against a real HOME and working
directory. That is the point of the file: the stubbed selection tests in
test_local_whisper.py cannot see where a key came from, which is exactly how
this gap survived them.

Every value below is inert filler. Nothing here reads a real credential.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import whisper

CONFIG_BASENAME = ".env"
FAKE_CONFIGURED = "not-a-real-key-configured"
FAKE_AMBIENT = "not-a-real-key-ambient"


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point HOME and cwd at tmp_path and clear every real key from the env."""
    (tmp_path / "home").mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    for name, _ in whisper.API_CANDIDATES:
        monkeypatch.delenv(name, raising=False)


def _configure(tmp_path: Path, name: str, value: str) -> None:
    """Write one setting into the fake ~/.config/moviola config file."""
    cfg = tmp_path / "home" / ".config" / "moviola"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / CONFIG_BASENAME).write_text("%s=%s\n" % (name, value), encoding="utf-8")


class TestUnpinnedIgnoresTheProcessEnvironment:
    def test_declines_a_key_that_exists_only_in_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("GROQ_API_KEY", FAKE_AMBIENT)
        monkeypatch.setattr(whisper, "local_available", lambda: False)
        assert whisper.resolve_backend() == (None, None)

    def test_uses_a_key_from_moviolas_own_config_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _isolate(monkeypatch, tmp_path)
        _configure(tmp_path, "GROQ_API_KEY", FAKE_CONFIGURED)
        monkeypatch.setattr(whisper, "local_available", lambda: False)
        # setup.py asked before writing that file. That is the consent, and it
        # is the difference this whole change turns on.
        assert whisper.resolve_backend() == ("groq", FAKE_CONFIGURED)

    def test_uses_a_key_from_the_working_directorys_dotenv(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _isolate(monkeypatch, tmp_path)
        (tmp_path / CONFIG_BASENAME).write_text(
            "OPENAI_API_KEY=%s\n" % FAKE_CONFIGURED, encoding="utf-8"
        )
        monkeypatch.setattr(whisper, "local_available", lambda: False)
        # A stated NON-GOAL, pinned by a test so it cannot drift silently: the
        # project .env is treated as deliberate, the same as upstream treats it,
        # even though it may belong to another tool entirely. Narrowing that is
        # a separate decision from this one.
        assert whisper.resolve_backend() == ("openai", FAKE_CONFIGURED)

    def test_local_still_wins_over_a_configured_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _isolate(monkeypatch, tmp_path)
        _configure(tmp_path, "GROQ_API_KEY", FAKE_CONFIGURED)
        monkeypatch.setattr(whisper, "local_available", lambda: True)
        assert whisper.resolve_backend() == ("local", None)


class TestAPinIsConsent:
    def test_a_pin_restores_the_environment_as_a_key_source(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("GROQ_API_KEY", FAKE_AMBIENT)
        monkeypatch.setattr(whisper, "local_available", lambda: False)
        # MOVIOLA_WHISPER is itself readable from the environment, so CI keeps a
        # way to opt in without writing a config file.
        assert whisper.resolve_backend("groq") == ("groq", FAKE_AMBIENT)

    def test_a_pin_to_the_other_provider_does_not_borrow_the_ambient_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("GROQ_API_KEY", FAKE_AMBIENT)
        monkeypatch.setattr(whisper, "local_available", lambda: False)
        assert whisper.resolve_backend("openai") == (None, None)


class TestTheRefusalExplainsItself:
    """A silent refusal would just look like moviola is broken."""

    def test_env_key_backend_reports_the_ambient_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _isolate(monkeypatch, tmp_path)
        assert whisper.env_key_backend() is None
        monkeypatch.setenv("OPENAI_API_KEY", FAKE_AMBIENT)
        assert whisper.env_key_backend() == "openai"

    def test_a_blank_environment_key_is_not_a_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _isolate(monkeypatch, tmp_path)
        # A secret sync that exports an empty value is common enough that this
        # repo has a rule about it. Blank reads as unset here too, so nobody is
        # told to pin a key they do not actually have.
        monkeypatch.setenv("GROQ_API_KEY", "   ")
        assert whisper.env_key_backend() is None

    def test_the_hint_names_the_pin_that_would_opt_in(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("GROQ_API_KEY", FAKE_AMBIENT)
        monkeypatch.setattr(whisper, "local_available", lambda: False)
        with pytest.raises(SystemExit) as exc:
            whisper.transcribe_video("v.mp4", tmp_path / "a.mp3")
        message = str(exc.value)
        assert "MOVIOLA_WHISPER=groq" in message
        assert "GROQ_API_KEY is set in this environment" in message
        # ...and never the key itself.
        assert FAKE_AMBIENT not in message

    def test_a_pinned_backend_with_no_key_keeps_its_own_message(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setattr(whisper, "local_available", lambda: False)
        with pytest.raises(SystemExit) as exc:
            whisper.transcribe_video("v.mp4", tmp_path / "a.mp3", backend="groq")
        # The ambient-key explanation must not leak onto a path where the user
        # already asked for this backend explicitly.
        assert "--whisper local" in str(exc.value)
