"""Which directory the entry point trusts when it is invoked through a symlink.

`moviola.py` computed `SCRIPT_DIR` as `Path(__file__).parent.resolve()` while every
other script in the package computed it as `Path(__file__).resolve().parent`. The two
spellings are identical until `__file__` is a symlink, and then they disagree about
which directory the program is in:

    .parent.resolve()   -> the directory holding the SYMLINK
    .resolve().parent   -> the directory holding the real script

All six sites then insert `SCRIPT_DIR` at the head of `sys.path`, which is what turns
a cosmetic divergence into a behavioural one. `moviola.py` is only the one that inserts
UNGUARDED — the five peers wrap it in `if str(SCRIPT_DIR) not in sys.path`. **That guard
does not save a regressed peer**, and this was reproduced rather than reasoned: with the
wrong spelling `SCRIPT_DIR` is the SYMLINK's directory, which by construction is not yet
on `sys.path`, so the guard passes and inserts the wrong directory at position 0 exactly
as the unguarded form does. Two consequences, both reproduced before this file was
written:

  * **A module beside the symlink shadows the package's own.** CPython already sets
    `sys.path[0]` to the REAL script's directory — it resolves the symlink itself —
    so the sibling imports on lines 18-40 never actually broke, and `--help` through
    a symlink exited 0 the whole time. What the insert did was prepend a SECOND
    directory ahead of the correct one. A `config.py` dropped next to the symlink
    therefore wins over the real `config.py`, and the same holds for `download`,
    `frames`, `local_whisper`, `setup`, `transcribe`, `untrusted`, `whisper` and
    `workdir` — `local_whisper` is imported by bare name from `whisper.py` and
    `setup.py` rather than here, and `sys.path` is process-global. That is
    arbitrary code execution against anyone who installs the skill the ordinary way
    — a symlink from a directory on PATH into the skill folder.

  * **`SCRIPT_DIR / "setup.py"` points at nothing.** `moviola.py:547` and `:693`
    build the installer path from `SCRIPT_DIR`, so through a symlink they addressed
    a `setup.py` beside the symlink, which does not exist.

The fix is the one-line spelling change. It does NOT make the insert redundant, and
the insert is deliberately kept: under `-P` or `PYTHONSAFEPATH=1` (3.11+) CPython
computes no script directory at all, and with the insert removed
`python3.13 -P moviola.py --help` dies with `ModuleNotFoundError: No module named
'config'` while plain invocation stays green — measured both ways on 3.13.13. What the
fix changes is that for the invocations where CPython DOES set `sys.path[0]`, the
insert is now a duplicate of the correct directory instead of a second, wrong one
ahead of it.

NON-GOALS, so a green run here is not read as more than it is:

  * The behavioural class drives `moviola.py` and nothing else — a limit of reach,
    NOT a statement that the other five are harmless. All six mutate `sys.path`;
    four of the five peers (`frames`, `local_whisper`, `setup`, `whisper`) carry
    `if __name__ == "__main__"` and are directly invocable; and a peer regressed to
    the wrong spelling was reproduced shadowing a decoy `config.py` through a
    symlink DESPITE its guard. A regression in a peer is therefore the same
    arbitrary code execution, and the structural class below is the only thing that
    would catch it — as a change of FORM. Nothing here drives the resulting
    BEHAVIOUR for any file but `moviola.py`.

  * The structural class is a textual scan of `SCRIPT_DIR = ` assignments. It cannot
    see a module that finds its own directory some other way — `os.path.dirname`, a
    package-relative import, an environment variable — and such a module is invisible
    to it by construction, not by oversight.

  * The legitimate configurations it must NOT fire on are the two neighbouring
    expressions that are correct and deliberately different: `setup.py`'s
    `installer = Path(__file__).resolve()`, which resolves the FILE and not its
    directory, and `whisper.py`'s
    `setup_py = Path(__file__).resolve().parent / "setup.py"`, which is the good
    form already. Scoping to the `SCRIPT_DIR` name is what keeps both out of scope.

  * Neither class pins WHICH files define `SCRIPT_DIR`. Deleting a script, or adding
    one that never defines it, is invisible here. The scan asserts it found a
    non-empty set and that `moviola.py` is in it, which catches a glob that matches
    nothing; it does not freeze the roster.

  * It says nothing about a symlink whose TARGET is itself a symlink, or about a
    relative symlink resolved against a different working directory. `Path.resolve()`
    handles both, and nothing here proves it.

  * A platform that cannot create symlinks skips the behavioural class rather than
    passing it. A skip is visible in `pytest -rs`; a silent pass would not be.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "moviola" / "scripts"
ENTRY_POINT = SCRIPTS / "moviola.py"

# A module that announces itself and refuses to be a working `config`. Importing it
# is the whole signal: if this string reaches the output, the directory holding the
# symlink was searched before the directory holding the real script.
DECOY_SENTINEL = "MOVIOLA_DECOY_CONFIG_WAS_IMPORTED"
DECOY_CONFIG = f'raise SystemExit("{DECOY_SENTINEL}")\n'

# The form every site must use. Written as the exact source text rather than parsed,
# because the defect this pins WAS a source-text difference between two spellings
# that mean the same thing to a reader skimming for `Path`, `__file__`, `resolve`
# and `parent` — all four of which appear in both.
GOOD_FORM = "Path(__file__).resolve().parent"
SCRIPT_DIR_ASSIGNMENT = re.compile(r"^SCRIPT_DIR\s*=\s*(.+?)\s*$", re.MULTILINE)


def _clean_env() -> dict[str, str]:
    """The ambient environment minus anything that would itself alter `sys.path`.

    `PYTHONPATH` is the one that matters: a value inherited from whoever launched
    pytest would put a third directory into the search order and make the result
    depend on the developer's shell rather than on the code under test.
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def _run_through(link: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(link), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        env=_clean_env(),
    )


@pytest.fixture
def symlinked_entry_point(tmp_path: Path) -> Path:
    """`moviola` in an otherwise empty directory, pointing at the real script."""
    link = tmp_path / "moviola"
    try:
        link.symlink_to(ENTRY_POINT)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this platform cannot create a symlink here: {exc}")
    return link


class TestASymlinkedEntryPointDoesNotTrustItsOwnDirectory:
    """Running through a symlink must search the real script's directory, not the link's.

    NON-GOALS: it drives `--help`, which is the cheapest path that still performs
    every sibling import on line 17. It does not exercise a download, a frame
    extraction or a transcript, and a directory-resolution bug that only bites
    further into a run would not appear here.
    """

    def test_a_symlinked_entry_point_runs(
        self, symlinked_entry_point: Path
    ) -> None:
        """The positive control, so the shadowing case cannot pass by being broken.

        Without this, a `moviola.py` that failed to start for an unrelated reason
        would satisfy the assertion below — the sentinel is absent from the output
        of a program that never got as far as importing anything.
        """
        result = _run_through(symlinked_entry_point)

        assert result.returncode == 0, (
            "running the entry point through a symlink failed before the shadowing "
            f"case could be tested.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "usage:" in result.stdout

    def test_a_module_beside_the_symlink_does_not_shadow_the_package(
        self, symlinked_entry_point: Path
    ) -> None:
        """A `config.py` next to the symlink must lose to the package's own."""
        decoy = symlinked_entry_point.parent / "config.py"
        decoy.write_text(DECOY_CONFIG, encoding="utf-8")

        result = _run_through(symlinked_entry_point)
        combined = result.stdout + result.stderr

        assert DECOY_SENTINEL not in combined, (
            "a config.py placed beside the symlink was imported instead of the "
            "package's own. The entry point prepended the symlink's directory to "
            "sys.path ahead of the real script's directory, so anything dropped "
            f"next to the link shadows the package.\n{combined}"
        )
        assert result.returncode == 0, (
            "the entry point did not survive an unrelated file sitting beside the "
            f"symlink.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


class TestEverySiteComputesItsDirectoryTheSameWay:
    """All six `SCRIPT_DIR` assignments must use the symlink-correct spelling.

    This is the half that covers the sites the behavioural class cannot reach. The
    original finding named three peers; the tree holds five, and a fix scoped to the
    three named would have left two unguarded.

    NON-GOALS: form, not behaviour — it proves the spelling is uniform, never that
    any particular consequence of getting it wrong is absent. And it reads source
    text, so a site assembled at runtime is outside it.
    """

    def test_every_script_dir_resolves_the_file_before_taking_the_parent(self) -> None:
        assignments = {}
        for script in sorted(SCRIPTS.glob("*.py")):
            for match in SCRIPT_DIR_ASSIGNMENT.finditer(
                script.read_text(encoding="utf-8")
            ):
                assignments.setdefault(script.name, []).append(match.group(1))

        assert assignments, (
            f"no SCRIPT_DIR assignment was found under {SCRIPTS}. Every assertion "
            "below would pass over an empty set."
        )
        assert "moviola.py" in assignments, (
            "moviola.py defines no SCRIPT_DIR, so the site this test exists for is "
            "not being checked."
        )

        wrong = {
            name: exprs
            for name, exprs in assignments.items()
            if any(expr != GOOD_FORM for expr in exprs)
        }
        assert not wrong, (
            f"these SCRIPT_DIR assignments do not use `{GOOD_FORM}`: {wrong}. "
            "`Path(__file__).parent.resolve()` resolves the directory holding the "
            "symlink; `Path(__file__).resolve().parent` resolves the file first and "
            "then takes the directory holding the real script. They differ only "
            "when the script is reached through a symlink, which is how the skill "
            "is ordinarily installed."
        )
