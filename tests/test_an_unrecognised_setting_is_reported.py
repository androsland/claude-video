"""A setting moviola discards must say so instead of vanishing.

`get_config()` validates two settings against a closed tuple and replaces
anything else with the default:

    MOVIOLA_WHISPER  not in WHISPER_BACKENDS -> "auto"
    MOVIOLA_DETAIL   not in DETAILS          -> "balanced"

Both replacements were silent, and silence is what made them a defect rather
than a design. A user who writes `MOVIOLA_WHISPER=mlx` has stated an intention;
the program's answer was to resolve as if they had stated nothing, and to say
nothing about the difference. `## Quiet failures` in TODOS.md is the section
this belongs to.

The SessionStart hook then described the same input a THIRD way. It read the
pin case-sensitively while `get_config` lowercases it first, so `mlx` and
`LOCAL` both fell through the `case` to the unpinned arm — and for anything that
was not the literal string `auto` it printed "is pinned but that backend is not
usable here", which describes a real backend that cannot run rather than a
string that is not a backend name. It offered a remedy to match: install it, or
set the matching API key. There is nothing to install for `mlx`.

The two are not the same fix and this file pins both halves:

  * `get_config()` now RETURNS what it discarded — `rejected`, a tuple of
    facts, not a message — and `moviola.py` prints one stderr line per entry.
    Keeping the formatting out of `config.py` is what keeps it a leaf module
    with no I/O, which is why the raw value and the live `allowed` tuple travel
    together instead of a caller re-deriving either.
  * `check-setup.sh` lowercases the pin the way `config.py` does, and
    distinguishes "not a backend name" from "a backend that is not usable
    here". That half lives in `test_check_setup_hook.py`, beside the rest of
    the hook's coverage, and it cross-pins the hook's four-name `case` against
    `config.WHISPER_BACKENDS` so the two lists cannot drift apart in silence.

The value reaches stderr through `stderr_line`, not an f-string. It is not a
value this program wrote — `MOVIOLA_WHISPER` is whatever the user put in their
environment or their config file — so it gets the same fence every other
foreign value on stderr gets.

NON-GOALS, so a green run is not read as more than it is:

  * **It does not change what the fallback IS.** `auto` and `balanced` are
    still what an unrecognised value resolves to, and that is asserted below
    rather than left implicit — announcing the fallback and changing it are
    different pieces of work, and only the first is here.

  * **`MOVIOLA_DETAIL` is compared case-sensitively while `MOVIOLA_WHISPER` is
    lowercased first, and this file pins that asymmetry as it stands.** So
    `MOVIOLA_DETAIL=Balanced` is reported as unrecognised. That is today's
    behaviour and it is now said out loud instead of swallowed, which is the
    whole change; making the two agree would silently start accepting a value
    that is rejected today, which is a behaviour change and belongs in its own
    commit.

  * **It says nothing about `--whisper` and `--detail`.** argparse validates
    both against the same tuples with `choices` and exits non-zero on anything
    else, so the flag path was never silent and is untouched.

  * **The other settings `get_config` returns are deliberately unvalidated**,
    and their absence from `rejected` is correct rather than an oversight.
    `whisper_model`, `whisper_device`, `whisper_compute` and
    `whisper_language` are passed through verbatim because validating them
    would mean hardcoding a model list that goes stale — `config.py` says so at
    the site. Nothing here should ever start reporting them.

  * **It drives one caller.** `moviola.py` prints the lines; `setup.py` also
    calls `get_config()` and prints nothing new. That is a limit of reach, not
    a claim that the preflight should stay quiet.

  * **Structure, not meaning** — the same limit `stderr_line` documents. It
    checks that a value carrying a line break cannot become two lines of
    moviola's own output. A setting whose value reads like an instruction is
    still legible text in an agent's context, correctly fenced.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import config

REPO = Path(__file__).resolve().parent.parent
ENTRY_POINT = REPO / "skills" / "moviola" / "scripts" / "moviola.py"

# A source that cannot exist. The settings are read at the top of `main()` and
# the run dies at source resolution, so the stderr under test is produced
# without a network, ffmpeg, or a fixture clip.
MISSING_SOURCE = "/nonexistent/moviola-test-source.mp4"


def _cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str) -> dict:
    """`get_config()` with a guaranteed-absent config file and a known env."""
    for name in ("MOVIOLA_WHISPER", "MOVIOLA_DETAIL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "absent.env")
    return config.get_config()


def _rejected(cfg: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in cfg["rejected"]}


class TestGetConfigReportsWhatItDiscarded:
    """The discarded value survives as data, so a caller can say what happened.

    NON-GOALS: it inspects the returned facts, not any message. Nothing here
    proves a caller prints them — `TestMoviolaSaysItOnStderr` below owns that,
    for one caller.
    """

    def test_an_unrecognised_whisper_backend_is_reported(self, monkeypatch, tmp_path):
        cfg = _cfg(monkeypatch, tmp_path, MOVIOLA_WHISPER="mlx")

        assert cfg["whisper"] == "auto", (
            "the fallback itself changed; this file announces it, it does not "
            "move it"
        )
        entry = _rejected(cfg).get("MOVIOLA_WHISPER")
        assert entry is not None, (
            f"an unrecognised backend name was discarded silently: {cfg['rejected']!r}"
        )
        assert entry["value"] == "mlx", "the reported value is not what the user wrote"
        assert entry["fallback"] == "auto"
        assert tuple(entry["allowed"]) == config.WHISPER_BACKENDS, (
            "the reported set is a second copy rather than the live tuple, which "
            "is the drift this exists to end"
        )

    def test_the_value_is_reported_as_the_user_wrote_it(self, monkeypatch, tmp_path):
        """Not the lowercased copy validation used.

        The user has to find this string in their own config file, and
        `MOVIOLA_WHISPER=MLX` is not there under any other spelling.
        """
        cfg = _cfg(monkeypatch, tmp_path, MOVIOLA_WHISPER="MLX")
        assert _rejected(cfg)["MOVIOLA_WHISPER"]["value"] == "MLX"

    def test_an_unrecognised_detail_is_reported(self, monkeypatch, tmp_path):
        cfg = _cfg(monkeypatch, tmp_path, MOVIOLA_DETAIL="bogus")

        assert cfg["detail"] == "balanced"
        entry = _rejected(cfg).get("MOVIOLA_DETAIL")
        assert entry is not None, (
            f"an unrecognised detail level was discarded silently: {cfg['rejected']!r}"
        )
        assert entry["value"] == "bogus"
        assert entry["fallback"] == "balanced"
        assert tuple(entry["allowed"]) == config.DETAILS

    def test_both_are_reported_together(self, monkeypatch, tmp_path):
        cfg = _cfg(monkeypatch, tmp_path, MOVIOLA_WHISPER="mlx", MOVIOLA_DETAIL="bogus")
        assert set(_rejected(cfg)) == {"MOVIOLA_WHISPER", "MOVIOLA_DETAIL"}


class TestTheLegitimateConfigurationsItMustNotFireOn:
    """Everything a working install does must report nothing at all.

    NON-GOALS: these are the four shapes a correct setting arrives in, not an
    exhaustive enumeration of them.
    """

    def test_nothing_set_reports_nothing(self, monkeypatch, tmp_path):
        assert _cfg(monkeypatch, tmp_path)["rejected"] == ()

    @pytest.mark.parametrize("backend", config.WHISPER_BACKENDS)
    def test_every_recognised_backend_reports_nothing(
        self, backend, monkeypatch, tmp_path
    ):
        cfg = _cfg(monkeypatch, tmp_path, MOVIOLA_WHISPER=backend)
        assert cfg["rejected"] == (), f"{backend} is in WHISPER_BACKENDS and was rejected"
        assert cfg["whisper"] == backend

    @pytest.mark.parametrize("detail", config.DETAILS)
    def test_every_recognised_detail_reports_nothing(self, detail, monkeypatch, tmp_path):
        cfg = _cfg(monkeypatch, tmp_path, MOVIOLA_DETAIL=detail)
        assert cfg["rejected"] == ()
        assert cfg["detail"] == detail

    def test_a_differently_cased_backend_is_accepted_not_reported(
        self, monkeypatch, tmp_path
    ):
        """`get_config` lowercases the pin, so `LOCAL` is a real pin."""
        cfg = _cfg(monkeypatch, tmp_path, MOVIOLA_WHISPER="LOCAL")
        assert cfg["whisper"] == "local"
        assert cfg["rejected"] == ()

    def test_a_blank_value_falls_through_and_reports_nothing(
        self, monkeypatch, tmp_path
    ):
        """A scaffolded-but-empty key is not a value the user chose.

        `_setting` treats blank as unset on purpose, so it reaches the default
        and there is nothing to complain about.
        """
        cfg = _cfg(monkeypatch, tmp_path, MOVIOLA_WHISPER="", MOVIOLA_DETAIL="")
        assert cfg["rejected"] == ()
        assert cfg["whisper"] == "auto"
        assert cfg["detail"] == "balanced"


class TestMoviolaSaysItOnStderr:
    """The one caller that turns the facts into a line a human reads.

    NON-GOALS: it asserts on the shape of the line, not on its prose. It also
    drives `moviola.py` only — `setup.py` reads the same config and is not
    covered here.
    """

    def _run(self, tmp_path: Path, **env: str) -> subprocess.CompletedProcess:
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "TMPDIR": str(tmp_path),
        }
        environment.update(env)
        return subprocess.run(
            [sys.executable, str(ENTRY_POINT), MISSING_SOURCE],
            capture_output=True,
            text=True,
            timeout=120,
            env=environment,
        )

    def test_an_unrecognised_backend_is_named_on_stderr(self, tmp_path):
        result = self._run(tmp_path, MOVIOLA_WHISPER="mlx")

        assert "MOVIOLA_WHISPER" in result.stderr, (
            f"nothing said the setting was discarded.\nstderr:\n{result.stderr}"
        )
        assert "mlx" in result.stderr, "the discarded value was not shown back"
        for name in config.WHISPER_BACKENDS:
            assert name in result.stderr, (
                f"the recognised set is incomplete on stderr: {name} is missing.\n"
                f"{result.stderr}"
            )

    def test_an_unrecognised_detail_is_named_on_stderr(self, tmp_path):
        result = self._run(tmp_path, MOVIOLA_DETAIL="bogus")
        assert "MOVIOLA_DETAIL" in result.stderr
        assert "bogus" in result.stderr

    def test_a_recognised_setting_says_nothing(self, tmp_path):
        """The control. A working install must not gain a new stderr line."""
        result = self._run(tmp_path, MOVIOLA_WHISPER="auto", MOVIOLA_DETAIL="balanced")
        assert "MOVIOLA_WHISPER" not in result.stderr, (
            f"a valid pin produced a complaint.\nstderr:\n{result.stderr}"
        )
        assert "MOVIOLA_DETAIL" not in result.stderr

    def test_the_value_cannot_end_the_line_it_sits_in(self, tmp_path):
        """It is a foreign value on stderr, so it gets `stderr_line`'s fence.

        Without it, a setting containing a line break puts whatever follows at
        column zero of moviola's own diagnostic stream, indistinguishable from
        a line moviola wrote.
        """
        result = self._run(tmp_path, MOVIOLA_WHISPER="mlx\n[moviola] all clear")

        assert "\n[moviola] all clear" not in result.stderr, (
            "the setting's line break survived into stderr, so a value the user "
            f"controls forged a moviola line.\nstderr:\n{result.stderr}"
        )
        assert "all clear" in result.stderr, (
            "the value was dropped rather than fenced, so the user is not shown "
            f"what they actually wrote.\nstderr:\n{result.stderr}"
        )
