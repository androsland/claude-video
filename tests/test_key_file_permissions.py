"""One answer to "can anyone else on this machine read my API key?".

Three surfaces ask that question about `~/.config/moviola/.env`, and until this
file they answered it with three different predicates:

  * `setup.py::_check_file_permissions` tested `mode & 0o044` — group- and
    other-READ. A group-writable or world-writable key file passed silently,
    even though "someone else can replace your key and bill their uploads to
    you" is the worse outcome of the two.
  * `hooks/scripts/check-setup.sh` tested `perms != "600" && perms != "400"` —
    a string comparison against two literals, so it warned about `700`, where
    no other user has any access at all.
  * `whisper.py` — the one that actually reads the key at runtime — did not
    ask at all. Since the audit that dropped `$PWD/.env`, it reads the same
    file `setup.py` does, so its silence was a real hole rather than a
    division of labour.

This is the same shape as test_consent_oracles: several surfaces answering one
question, drifting because each re-derived the rule instead of sharing it. The
rule is `mode & 0o077` — ANY bit granting group or other access — and the table
below is the single place it is written down.

NON-GOALS, stated so a green run is not read as more than it is:

  * It checks MODE BITS, not reachability. A file at 0600 inside a
    world-readable directory is still findable; a checkout on a filesystem with
    no POSIX permissions (a Windows drive under WSL, FAT/exFAT, some network
    mounts) reports whatever mode the driver invents, and `chmod` there is a
    no-op; POSIX ACLs can grant access the mode never mentions. None of that is
    visible here, and this warning will never fire on any of it.
  * It says nothing about a key that was ALREADY exposed. A key committed to a
    repo, pasted into a shell history, or synced to a backup is compromised at
    any mode.
  * It does not cover `$PWD/.env`. moviola stopped reading that file entirely,
    so its permissions are not moviola's business.
  * Every surface WARNS; none refuses. Refusing to read an exposed file would
    strand a run over a condition the user can fix in one command, and the key
    is already on disk either way.

Every value written below is inert filler. Nothing here reads a real credential.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "moviola" / "scripts"
HOOK = ROOT / "hooks" / "scripts" / "check-setup.sh"

FILLER = "placeholder-value-not-a-credential"

# (id, mode, must_warn) — must_warn is exactly `bool(mode & 0o077)`, spelled out
# per row so a change to the rule has to change this table and be seen.
#
# The two rows that carry the argument: `owner-all` is a legitimate mode no
# surface may warn about (the bash hook did), and `group-writable` /
# `other-writable` are exposure no surface may stay silent about (setup.py did).
MODES = [
    ("owner-read-write", 0o600, False),
    ("owner-read-only", 0o400, False),
    ("owner-all", 0o700, False),
    ("group-readable", 0o640, True),
    ("other-readable", 0o604, True),
    ("group-writable", 0o620, True),
    ("other-writable", 0o602, True),
    ("other-executable", 0o601, True),
    ("world-open", 0o666, True),
]
IDS = [case[0] for case in MODES]


def _home_with_config(tmp_path: Path, mode: int) -> Path:
    """A fake $HOME holding a config file at exactly `mode`."""
    home = tmp_path / "home"
    config_dir = home / ".config" / "moviola"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = config_dir / ".env"
    config.write_text(f"GROQ_API_KEY={FILLER}\n", encoding="utf-8")
    config.chmod(mode)
    return home


def _clean_env(home: Path, **extra: str) -> dict:
    """A subprocess environment with no ambient key and no inherited config."""
    return {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        **extra,
    }


def _warnings(text: str, home: Path) -> int:
    """How many times the output told the user to fix the config file's mode.

    Matched on the remedy rather than the prose, so a reworded warning still
    counts and only a MISSING or SPURIOUS warning fails a test.
    """
    return text.count(f"chmod 600 {home}/.config/moviola/.env")


@pytest.fixture(autouse=True)
def _requires_posix_modes(tmp_path: Path) -> None:
    """Skip where chmod does not stick — the mode table is meaningless there."""
    probe = tmp_path / "probe"
    probe.write_text("x", encoding="utf-8")
    probe.chmod(0o604)
    if probe.stat().st_mode & 0o777 != 0o604:
        pytest.skip("filesystem does not honour POSIX modes")


@pytest.mark.parametrize("name,mode,must_warn", MODES, ids=IDS)
class TestEverySurfaceUsesTheSamePredicate:
    def test_the_runtime_warns_exactly_when_the_file_is_exposed(
        self, tmp_path: Path, name: str, mode: int, must_warn: bool
    ) -> None:
        home = _home_with_config(tmp_path, mode)
        proc = subprocess.run(
            [sys.executable, "-c", "import whisper; whisper.load_api_key(None, allow_env=False)"],
            env=_clean_env(home, PYTHONPATH=str(SCRIPTS)),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert (_warnings(proc.stderr, home) > 0) is must_warn, proc.stderr

    def test_the_agents_preflight_warns_exactly_when_the_file_is_exposed(
        self, tmp_path: Path, name: str, mode: int, must_warn: bool
    ) -> None:
        home = _home_with_config(tmp_path, mode)
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "setup.py"), "--json"],
            env=_clean_env(home),
            capture_output=True,
            text=True,
        )
        # Exactly once, not merely at least once. setup.py now reaches the key
        # through whisper.load_api_key, so two independently-warning copies of
        # this check would print the same line twice for one file.
        assert _warnings(proc.stderr, home) == (1 if must_warn else 0), proc.stderr

    def test_the_session_hook_warns_exactly_when_the_file_is_exposed(
        self, tmp_path: Path, name: str, mode: int, must_warn: bool
    ) -> None:
        home = _home_with_config(tmp_path, mode)
        proc = subprocess.run(
            ["bash", str(HOOK)],
            env=_clean_env(home),
            capture_output=True,
            text=True,
        )
        combined = proc.stdout + proc.stderr
        assert (_warnings(combined, home) > 0) is must_warn, combined
