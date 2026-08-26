"""The three surfaces that answer "will this upload my audio?" must agree.

moviola answers that question in three places, in two languages:

  * ``whisper.resolve_backend()``    — the runtime. This one actually uploads.
  * ``setup.py --json``              — the preflight the agent parses.
  * ``hooks/scripts/check-setup.sh`` — the SessionStart line the human reads.

Only the first has any effect. The other two exist so the user knows what the
first is about to do, which makes a disagreement between them worse than a plain
bug: the run gets *consented to* on the strength of an answer that came from a
different oracle than the one doing the uploading.

They diverged. An unpinned run resolved a key out of ``$PWD/.env`` and uploaded,
while both preflights read only ``~/.config/moviola/.env`` and reported that
nothing was configured — so a cloned repo carrying a ``.env`` sent the user's
audio to a stranger's provider account underneath a "no backend configured"
notice.

Reproducing that needed all three surfaces in one test, which is what this file
is. Each case drives every oracle through its own front door — two subprocesses
and a bash script, no monkeypatching of the thing under test — and asserts they
land on the same backend. A fix applied to one surface and not the others fails
here instead of shipping.

NON-GOALS, so a pass is not read as more than it is. This file pins the
*key-source precedence* only: it holds local-whisper unavailable throughout,
because that is the state every machine starts in and the only one where a key
is consulted at all — the local-first ordering is pinned in test_backend_consent
and test_check_setup_hook instead. It compares the three surfaces to each other
and to a table, so a change that moves all three the same wrong way passes here.
And it says nothing about what happens *after* a backend resolves; whether the
upload is then announced is test_whisper_api's job.

Every key below is inert filler. Nothing here reads a real credential.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "moviola" / "scripts"
HOOK = REPO / "hooks" / "scripts" / "check-setup.sh"

# Inert filler. Deliberately not shaped like a provider key so that neither a
# secret scanner nor a human skimming the diff has to stop and check.
FILLER = "placeholder-value-not-a-credential"


# --------------------------------------------------------------------------
# Environment construction
# --------------------------------------------------------------------------

def _blocked_faster_whisper(tmp_path: Path) -> Path:
    """A directory that makes ``import faster_whisper`` fail.

    The test host may genuinely have faster-whisper installed — this repo's own
    does — and every case below is about the state where it is absent. Shadowing
    it on ``PYTHONPATH`` puts a Python subprocess into that state without
    uninstalling anything, and without the test's answer depending on which
    machine it runs on.
    """
    d = tmp_path / "nolocal"
    d.mkdir(exist_ok=True)
    (d / "faster_whisper.py").write_text(
        "raise ImportError('blocked by test_consent_oracles')\n", encoding="utf-8"
    )
    return d


def _home_with(tmp_path: Path, settings: str) -> Path:
    """A fake HOME whose moviola config file holds ``settings`` (or no file)."""
    home = tmp_path / "home"
    (home / ".config" / "moviola").mkdir(parents=True, exist_ok=True)
    f = home / ".config" / "moviola" / ".env"
    if settings:
        f.write_text(settings, encoding="utf-8")
        f.chmod(0o600)
    elif f.exists():
        f.unlink()
    return home


def _cwd_with(tmp_path: Path, settings: str) -> Path:
    """A working directory carrying ``settings`` — the cloned-repo case."""
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    f = work / ".env"
    if settings:
        f.write_text(settings, encoding="utf-8")
    elif f.exists():
        f.unlink()
    return work


def _stub(path: Path, exit_code: int = 0) -> None:
    path.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _py_env(home: Path, blocked: Path, ambient: dict, pin: str) -> dict:
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": f"{blocked}{os.pathsep}{SCRIPTS}",
        "PYTHONDONTWRITEBYTECODE": "1",
        **ambient,
    }
    if pin != "auto":
        env["MOVIOLA_WHISPER"] = pin
    return env


# --------------------------------------------------------------------------
# The three oracles
# --------------------------------------------------------------------------

def _ask_runtime(home: Path, work: Path, ambient: dict, pin: str, blocked: Path) -> str | None:
    """What ``whisper.resolve_backend()`` would actually do."""
    code = (
        "import json, sys, whisper;"
        "b, _k = whisper.resolve_backend(%r);"
        "json.dump({'backend': b}, sys.stdout)"
    ) % (None if pin == "auto" else pin)
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(work), capture_output=True, text=True,
        # The runtime reads its pin through config.py, which consults the
        # process environment, so the pin travels the same way here as it does
        # for the other two surfaces rather than through the call argument.
        env=_py_env(home, blocked, ambient, pin),
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["backend"]


def _ask_setup_json(home: Path, work: Path, ambient: dict, pin: str, blocked: Path) -> str | None:
    """What the preflight the agent parses reports."""
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "setup.py"), "--json"],
        cwd=str(work), capture_output=True, text=True,
        env=_py_env(home, blocked, ambient, pin),
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["whisper_backend"]


def _ask_hook(tmp_path: Path, home: Path, work: Path, ambient: dict, pin: str) -> str | None:
    """What the SessionStart line tells the human.

    The hook has no machine-readable output by design — it is one sentence for a
    person — so its answer is read back out of that sentence.
    """
    binpath = tmp_path / "bin"
    binpath.mkdir(exist_ok=True)
    _stub(binpath / "ffmpeg")
    _stub(binpath / "yt-dlp")
    _stub(binpath / "python3", 1)   # find_spec fails -> local unavailable
    _stub(binpath / "stat")         # keep the permission warning out of stdout
    env = {"PATH": f"{binpath}:/usr/bin:/bin", "HOME": str(home), **ambient}
    if pin != "auto":
        env["MOVIOLA_WHISPER"] = pin
    out = subprocess.run(
        ["bash", str(HOOK)], cwd=str(work),
        capture_output=True, text=True, env=env,
    )
    assert out.returncode == 0, out.stderr
    text = out.stdout
    if "on this machine via faster-whisper" in text:
        return "local"
    for name in ("groq", "openai"):
        if f"transcription via the {name} API" in text:
            return name
    return None


# --------------------------------------------------------------------------
# The matrix
# --------------------------------------------------------------------------

CONFIG_GROQ = f"GROQ_API_KEY={FILLER}-config\n"
CONFIG_OPENAI = f"OPENAI_API_KEY={FILLER}-config\n"
CWD_GROQ = f"GROQ_API_KEY={FILLER}-from-the-working-directory\n"
AMBIENT_GROQ = {"GROQ_API_KEY": f"{FILLER}-ambient"}

# (id, pin, config file body, $PWD/.env body, process env, backend that must run)
CASES = [
    ("nothing-anywhere",                "auto",   "",            "",       {},           None),
    ("config-key-is-consent",           "auto",   CONFIG_GROQ,   "",       {},           "groq"),
    ("config-key-openai",               "auto",   CONFIG_OPENAI, "",       {},           "openai"),
    ("ambient-env-is-not-consent",      "auto",   "",            "",       AMBIENT_GROQ, None),
    ("cwd-dotenv-is-not-consent",       "auto",   "",            CWD_GROQ, {},           None),
    ("cwd-dotenv-and-ambient-together", "auto",   "",            CWD_GROQ, AMBIENT_GROQ, None),
    ("config-wins-over-a-cwd-dotenv",   "auto",   CONFIG_OPENAI, CWD_GROQ, {},           "openai"),
    ("a-pin-restores-the-environment",  "groq",   "",            "",       AMBIENT_GROQ, "groq"),
    ("a-pin-does-not-restore-cwd",      "groq",   "",            CWD_GROQ, {},           None),
    ("a-pin-reads-the-config-file",     "groq",   CONFIG_GROQ,   "",       {},           "groq"),
    ("a-pin-elsewhere-borrows-nothing", "openai", "",            "",       AMBIENT_GROQ, None),
    ("a-local-pin-without-local",       "local",  CONFIG_GROQ,   "",       {},           None),
]


@pytest.mark.parametrize(
    "pin,config_body,cwd_body,ambient,expected",
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
class TestEveryOracleAgrees:
    """Same question, three implementations, one answer.

    Split into three tests rather than one combined assertion so a failure names
    WHICH surface drifted. "They disagree" leaves you to work out whether the
    runtime moved or a preflight did, and that is the whole diagnosis.
    """

    def test_the_runtime_resolves_what_the_matrix_says(
        self, tmp_path, pin, config_body, cwd_body, ambient, expected
    ):
        blocked = _blocked_faster_whisper(tmp_path)
        home = _home_with(tmp_path, config_body)
        work = _cwd_with(tmp_path, cwd_body)
        assert _ask_runtime(home, work, ambient, pin, blocked) == expected

    def test_the_agents_preflight_agrees_with_the_runtime(
        self, tmp_path, pin, config_body, cwd_body, ambient, expected
    ):
        blocked = _blocked_faster_whisper(tmp_path)
        home = _home_with(tmp_path, config_body)
        work = _cwd_with(tmp_path, cwd_body)
        assert _ask_setup_json(home, work, ambient, pin, blocked) == expected

    def test_the_humans_session_line_agrees_with_the_runtime(
        self, tmp_path, pin, config_body, cwd_body, ambient, expected
    ):
        home = _home_with(tmp_path, config_body)
        work = _cwd_with(tmp_path, cwd_body)
        assert _ask_hook(tmp_path, home, work, ambient, pin) == expected


class TestThePreflightIsInternallyConsistent:
    """One document must not contradict itself.

    The reproduction that started this file produced, in a single ``--json``
    payload: ``whisper_backend: "groq"`` beside ``has_api_key: false`` and
    ``has_transcription: false``, with ``status: "ready"``. Whichever field the
    agent happened to read decided whether the user got warned.
    """

    def _payload(self, tmp_path, config_body, cwd_body, ambient) -> dict:
        blocked = _blocked_faster_whisper(tmp_path)
        home = _home_with(tmp_path, config_body)
        work = _cwd_with(tmp_path, cwd_body)
        out = subprocess.run(
            [sys.executable, str(SCRIPTS / "setup.py"), "--json"],
            cwd=str(work), capture_output=True, text=True,
            env=_py_env(home, blocked, ambient, "auto"),
        )
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    def test_a_named_backend_implies_transcription_is_available(self, tmp_path):
        payload = self._payload(tmp_path, CONFIG_GROQ, "", {})
        assert payload["whisper_backend"] == "groq"
        assert payload["has_api_key"] is True
        assert payload["has_transcription"] is True

    @pytest.mark.parametrize(
        "config_body,cwd_body,ambient",
        [
            ("", CWD_GROQ, {}),
            ("", "", AMBIENT_GROQ),
            ("", CWD_GROQ, AMBIENT_GROQ),
        ],
        ids=["cwd-dotenv", "ambient-env", "both"],
    )
    def test_a_key_the_runtime_would_refuse_is_not_reported_as_available(
        self, tmp_path, config_body, cwd_body, ambient
    ):
        """``has_api_key`` must mean "usable", not "a string exists somewhere".

        It was computed by re-deriving the lookup instead of asking the runtime,
        so an ambient key made it ``true`` while an unpinned run refused that
        very key — the preflight announced a backend and the run did frames
        only. ``status`` and ``can_proceed`` are both downstream of it.
        """
        payload = self._payload(tmp_path, config_body, cwd_body, ambient)
        assert payload["whisper_backend"] is None
        assert payload["has_api_key"] is False
        assert payload["has_transcription"] is False
        assert payload["status"] != "ready"
