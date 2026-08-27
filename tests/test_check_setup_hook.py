"""The SessionStart hook's one-line status.

It is shell, it duplicates whisper.resolve_backend()'s precedence in bash, and
until now nothing tested it — which is how it came to announce an API backend to
a user who had pinned `local`. Every dependency it probes is stubbed onto PATH:
`command -v` and the python3 spawn are the only things it looks at, so a fake
python3 that exits 0 or 1 controls has_local_whisper exactly, without depending
on whether the developer's machine happens to have faster-whisper installed.
"""
from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

import config

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "scripts" / "check-setup.sh"

# Inert filler, spelled the way test_consent_oracles.py spells it. Deliberately
# not shaped like a provider key, so neither a secret scanner nor a human
# skimming the diff has to stop and check.
FILLER = "placeholder-value-not-a-credential"


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
    # No environment scrubbing happens here, deliberately. `env=env` below hands
    # the child a closed dict, so an ambient key in the developer's shell cannot
    # reach the hook in the first place. This used to pop four names out of
    # os.environ with no restore, which did nothing for the child and quietly
    # deleted them for every test that ran after it in the same process.
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


class TestAmbientEnvironmentKeyIsNotConsent:
    """Unpinned, the hook must not call an ambient key "ready".

    whisper.resolve_backend passes allow_env=False when nothing is pinned, so a
    key that lives only in the process environment selects no backend. This hook
    duplicates that precedence in bash, and a hook that says "ready — via the
    groq API" while a real run declines to upload is the same class of lie the
    pin bug above was.
    """

    AMBIENT = {"GROQ_API_KEY": "not-a-real-key"}

    def test_unpinned_does_not_announce_an_ambient_key_as_ready(self, tmp_path):
        out = _run(tmp_path, local_whisper=False, extra_env=self.AMBIENT)
        assert "groq API" not in out.stdout

    def test_unpinned_explains_the_refusal_and_names_the_pin(self, tmp_path):
        out = _run(tmp_path, local_whisper=False, extra_env=self.AMBIENT)
        assert "MOVIOLA_WHISPER=groq" in out.stdout
        assert "not-a-real-key" not in out.stdout

    def test_a_pin_makes_the_same_ambient_key_usable(self, tmp_path):
        out = _run(
            tmp_path,
            env_body="MOVIOLA_WHISPER=groq\n",
            local_whisper=False,
            extra_env=self.AMBIENT,
        )
        assert "groq API" in out.stdout

    def test_local_still_wins_over_an_ambient_key(self, tmp_path):
        out = _run(tmp_path, local_whisper=True, extra_env=self.AMBIENT)
        assert "on this machine" in out.stdout


class TestAnUnrecognisedPinIsNotAnUnusableBackend:
    """A string that is not a backend name must not be described as one.

    The hook printed one message for two different inputs. `MOVIOLA_WHISPER=groq`
    with no key is a real backend that cannot run here, and "is pinned but that
    backend is not usable here — install it, or set the matching API key" is
    exactly right for it. `MOVIOLA_WHISPER=mlx` is not a backend at all:
    `get_config` drops it and resolves as if nothing were pinned, there is
    nothing to install, and there is no matching key to set. The hook said the
    same sentence to both, and it was the only thing either user was ever told,
    because `get_config` discarded the value in silence.

    The case arm is cross-pinned to `config.WHISPER_BACKENDS` below rather than
    re-listed here. The hook already spells `local`, `groq` and `openai` in bash
    and nothing compared that spelling to the Python tuple; a name added to the
    tuple and not to the hook now fails here instead of resolving as unpinned on
    the one surface a human actually reads.

    NON-GOALS, so a green run is not read as more than it is:

      * **It pins the MESSAGE, not the resolution.** An unrecognised pin
        resolved as unpinned before this change and still does — which is
        `get_config`'s behaviour and correct. Only the description changed.
      * **It says nothing about the Python side of the same finding.**
        `get_config` reporting what it discarded, and `moviola.py` printing it,
        are pinned in `test_an_unrecognised_setting_is_reported.py`. The two
        surfaces are fixed together and tested apart, which is what would catch
        one of them regressing alone.
      * **The legitimate configurations it must not fire on** are every name in
        `WHISPER_BACKENDS`, including `auto` and including a differently-cased
        spelling of a real one. Asserted below rather than left implicit.
      * It cannot see a pin that is a real backend name the hook resolves
        wrongly for some other reason; `TestPinIsHonoured` above owns that.
    """

    NOT_A_BACKEND = "not a backend name"
    UNUSABLE = "is pinned but that backend is not usable"

    def test_an_unrecognised_pin_says_it_is_not_a_backend_name(self, tmp_path):
        out = _run(tmp_path, env_body="MOVIOLA_WHISPER=mlx\n", local_whisper=False)
        assert self.NOT_A_BACKEND in out.stdout, (
            f"a typo'd backend name was described as an unusable backend, or not "
            f"at all.\nstdout:\n{out.stdout}"
        )
        assert self.UNUSABLE not in out.stdout, (
            "the message for a real-but-unusable backend was used for a string "
            f"that is not a backend.\nstdout:\n{out.stdout}"
        )

    def test_it_lists_what_the_recognised_values_are(self, tmp_path):
        out = _run(tmp_path, env_body="MOVIOLA_WHISPER=mlx\n", local_whisper=False)
        for name in config.WHISPER_BACKENDS:
            assert name in out.stdout, (
                f"the notice does not name {name}, so it tells the user their "
                f"value is wrong without telling them what is right.\n{out.stdout}"
            )

    def test_the_notice_appears_even_when_a_backend_resolves(self, tmp_path):
        """The setting is ignored whatever else happens, so it is still news."""
        out = _run(tmp_path, env_body="MOVIOLA_WHISPER=mlx\n", local_whisper=True)
        assert self.NOT_A_BACKEND in out.stdout
        assert "on this machine" in out.stdout, (
            "the status line was lost; the notice is an addition to it, not a "
            f"replacement for it.\nstdout:\n{out.stdout}"
        )

    def test_the_notice_survives_a_completed_setup(self, tmp_path):
        """`SETUP_COMPLETE=true` exits before the status line — and must not
        take a broken setting down with it.

        The permissions warning above already prints ahead of that exit, so
        "warnings precede the silence" is the file's own existing shape rather
        than a new rule. A SessionStart hook is the only surface that tells a
        user about their config file without them running anything.
        """
        out = _run(
            tmp_path,
            env_body="SETUP_COMPLETE=true\nMOVIOLA_WHISPER=mlx\n",
            local_whisper=False,
        )
        assert self.NOT_A_BACKEND in out.stdout, (
            f"a fully-configured install was told nothing about a setting that "
            f"is being ignored.\nstdout:\n{out.stdout}"
        )
        assert out.returncode == 0

    @pytest.mark.parametrize("backend", config.WHISPER_BACKENDS)
    def test_every_recognised_backend_is_not_called_a_typo(self, backend, tmp_path):
        """Cross-pins the hook's bash `case` against the Python tuple."""
        out = _run(
            tmp_path, env_body=f"MOVIOLA_WHISPER={backend}\n", local_whisper=True
        )
        assert self.NOT_A_BACKEND not in out.stdout, (
            f"{backend} is in config.WHISPER_BACKENDS and the hook does not "
            f"recognise it, so it resolves as unpinned on the surface the user "
            f"reads.\nstdout:\n{out.stdout}"
        )

    def test_a_differently_cased_pin_is_honoured_not_ignored(self, tmp_path):
        """`get_config` lowercases the pin; the hook read it case-sensitively.

        With `MOVIOLA_WHISPER=LOCAL` and a key in the config file, the hook fell
        through to the unpinned arm and announced the API backend — the exact
        lie `TestPinIsHonoured` exists to prevent, reached by a different route.
        """
        out = _run(
            tmp_path,
            env_body=f"MOVIOLA_WHISPER=LOCAL\nGROQ_API_KEY={FILLER}\n",
            local_whisper=False,
        )
        assert self.NOT_A_BACKEND not in out.stdout, (
            f"a real backend name in a different case was called a typo.\n{out.stdout}"
        )
        assert "groq API" not in out.stdout, (
            "the hook announced the API backend to a user who pinned local, "
            f"because it compared the pin case-sensitively.\nstdout:\n{out.stdout}"
        )
        assert self.UNUSABLE in out.stdout, (
            f"the pin was honoured but its unusability was not reported.\n{out.stdout}"
        )
