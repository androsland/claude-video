"""The SessionStart hook's one-line status.

It is shell, it duplicates whisper.resolve_backend()'s precedence in bash, and
until now nothing tested it — which is how it came to announce an API backend to
a user who had pinned `local`. Every dependency it probes is stubbed onto PATH:
`command -v` and the python3 spawn are the only things it looks at, so a fake
python3 that exits 0 or 1 controls has_local_whisper exactly, without depending
on whether the developer's machine happens to have faster-whisper installed.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "scripts" / "check-setup.sh"


def _stub(path: Path, exit_code: int = 0) -> None:
    path.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run(
    tmp_path: Path,
    *,
    env_body: str = "",
    binaries: bool = True,
    local_whisper: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    if env_body:
        cfg = home / ".config" / "moviola"
        cfg.mkdir(parents=True, exist_ok=True)
        f = cfg / ".env"
        f.write_text(env_body, encoding="utf-8")
        f.chmod(0o600)

    binpath = tmp_path / "bin"
    binpath.mkdir(exist_ok=True)
    if binaries:
        _stub(binpath / "ffmpeg")
        _stub(binpath / "yt-dlp")
    # find_spec succeeds iff this fake python3 exits 0.
    _stub(binpath / "python3", 0 if local_whisper else 1)
    _stub(binpath / "stat")

    env = {
        "PATH": f"{binpath}:/usr/bin:/bin",
        "HOME": str(home),
    }
    for leaked in ("GROQ_API_KEY", "OPENAI_API_KEY", "SETUP_COMPLETE", "MOVIOLA_WHISPER"):
        os.environ.pop(leaked, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(HOOK)], capture_output=True, text=True, env=env,
    )


class TestPinIsHonoured:
    def test_a_local_pin_is_not_overridden_by_a_present_key(self, tmp_path):
        # The bug: the old chain checked keys first and never read the pin, so a
        # user who deliberately chose on-device was told the API backend ran.
        out = _run(
            tmp_path,
            env_body="GROQ_API_KEY=sk-test\nMOVIOLA_WHISPER=local\n",
            local_whisper=True,
        )
        assert "on this machine" in out.stdout
        assert "groq API" not in out.stdout

    def test_a_groq_pin_is_not_overridden_by_local_being_installed(self, tmp_path):
        out = _run(
            tmp_path,
            env_body="GROQ_API_KEY=sk-test\nMOVIOLA_WHISPER=groq\n",
            local_whisper=True,
        )
        assert "groq API" in out.stdout

    @pytest.mark.parametrize(
        "body",
        ["MOVIOLA_WHISPER=groq\n", "MOVIOLA_WHISPER=openai\n", "MOVIOLA_WHISPER=local\n"],
    )
    def test_an_unusable_pin_says_so_instead_of_claiming_ready(self, body, tmp_path):
        out = _run(tmp_path, env_body=body, local_whisper=False)
        assert "is pinned but that backend is not usable" in out.stdout
        assert "ready —" not in out.stdout


class TestUnpinnedPrecedence:
    def test_local_wins_when_both_are_available(self, tmp_path):
        out = _run(tmp_path, env_body="GROQ_API_KEY=sk-test\n", local_whisper=True)
        assert "on this machine" in out.stdout

    def test_the_api_backend_is_named_when_local_is_absent(self, tmp_path):
        out = _run(tmp_path, env_body="OPENAI_API_KEY=sk-test\n", local_whisper=False)
        assert "openai API" in out.stdout

    def test_neither_available_falls_back_to_the_captions_hint(self, tmp_path):
        out = _run(tmp_path, local_whisper=False)
        assert "ready for videos with native captions" in out.stdout


class TestKeyParsing:
    def test_an_indented_key_is_found(self, tmp_path):
        # awk matched $1 untrimmed while read_env_file() strips the line first,
        # so this key was honoured by every Python caller and invisible here.
        out = _run(tmp_path, env_body="   GROQ_API_KEY=sk-test\n", local_whisper=False)
        assert "groq API" in out.stdout

    def test_a_commented_key_is_not_found(self, tmp_path):
        out = _run(tmp_path, env_body="# GROQ_API_KEY=sk-test\n", local_whisper=False)
        assert "ready for videos with native captions" in out.stdout

    def test_a_blank_value_is_not_a_key(self, tmp_path):
        out = _run(tmp_path, env_body="GROQ_API_KEY=\n", local_whisper=False)
        assert "ready for videos with native captions" in out.stdout

    def test_a_quoted_value_is_unwrapped(self, tmp_path):
        out = _run(tmp_path, env_body='GROQ_API_KEY="sk-test"\n', local_whisper=False)
        assert "groq API" in out.stdout

    def test_no_key_value_reaches_stdout(self, tmp_path):
        # The hook answers "is one configured" and must never echo the secret.
        out = _run(tmp_path, env_body="GROQ_API_KEY=sk-do-not-print\n", local_whisper=False)
        assert "sk-do-not-print" not in out.stdout
        assert "sk-do-not-print" not in out.stderr


class TestSilenceAndBinaries:
    def test_a_completed_setup_with_binaries_is_silent(self, tmp_path):
        out = _run(
            tmp_path,
            env_body="SETUP_COMPLETE=true\nGROQ_API_KEY=sk-test\n",
            local_whisper=False,
        )
        assert out.stdout == ""
        assert out.returncode == 0

    def test_missing_binaries_win_over_everything_else(self, tmp_path):
        out = _run(
            tmp_path,
            env_body="GROQ_API_KEY=sk-test\n",
            binaries=False,
            local_whisper=True,
        )
        assert "needs ffmpeg + yt-dlp" in out.stdout

    def test_it_never_exits_non_zero(self, tmp_path):
        # It is a SessionStart hook: a non-zero exit is noise in every session.
        for body in ("", "MOVIOLA_WHISPER=groq\n", "SETUP_COMPLETE=true\n"):
            assert _run(tmp_path, env_body=body).returncode == 0
