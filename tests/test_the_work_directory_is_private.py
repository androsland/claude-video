"""Whether the two ways of choosing a work directory agree about who may read it.

moviola picks its working directory one of two ways, and they disagreed:

    no --out-dir : tempfile.mkdtemp(prefix="moviola-")   -> 0700, always
    --out-dir X  : Path(X).mkdir(parents=True, ...)      -> 0777 & ~umask, so 0755

That directory holds the downloaded video, every extracted frame and the transcript
for the whole run. On the default path nobody else on the machine can look at any of
it; on `--out-dir` everybody could, and nothing said so. The flag reads like a choice
of LOCATION, not a choice of audience.

`test_key_file_permissions.py` states the complementary non-goal in its own docstring
— "a file at 0600 inside a world-readable directory is still findable" — which is the
hazard this file closes for the one directory moviola creates itself.

The fix is `mode=0o700` on the `mkdir`, which makes the explicit path match the default
one. It deliberately does NOT chmod a directory that already exists: `exist_ok=True`
ignores `mode` for an existing directory, and a user who points `--out-dir` at a
directory they already made has expressed a preference moviola should not overrule.

NON-GOALS, so a green run here is not read as more than it is:

  * **Only the LEAF directory is protected.** `parents=True` creates intermediate
    directories with the default mode, not with `mode` — measured, not assumed — so
    `--out-dir a/b/c` leaves `a` and `b` at 0755 and only `c` at 0700. Traversal into
    `c` is still refused, which is what protects the contents, but the intermediate
    names are visible. Nothing here fires on that.

  * **It says nothing about the mode of the FILES inside.** Frames, `video.*` and the
    transcript are written with the ambient umask. Inside a 0700 directory that is
    unreachable by anyone else; inside a pre-existing directory the user chose, it is
    the user's exposure and moviola does not change it. Filed in TODOS.md rather than
    fixed here.

  * **Mode bits, not reachability** — the same limit `test_key_file_permissions.py`
    states. POSIX ACLs, a filesystem with no permission model (a Windows drive under
    WSL, FAT, some network mounts), or a parent directory that is itself group-writable
    can all grant access the mode never mentions. The fixture skips when the filesystem
    does not honour a mode at all, which is visible under `pytest -rs`; it cannot
    detect the subtler cases.

  * **It says nothing about a directory that was already exposed.** Re-using a 0755
    `--out-dir` from a previous run keeps 0755, by design, and that is asserted below
    as a passing case rather than left implicit.

  * The subprocess is run under an explicit `umask 022`. A developer whose shell sets
    a restrictive umask would otherwise get 0700 from the unfixed code and a green run
    that proved nothing — the defect is invisible at umask 077.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ENTRY_POINT = REPO / "skills" / "moviola" / "scripts" / "moviola.py"

# A source that cannot exist. The work directory is created before the source is
# resolved, so the run reaches the code under test and then stops immediately — no
# network, no ffmpeg, no fixture clip.
MISSING_SOURCE = "/nonexistent/moviola-test-source.mp4"

WORKING_DIR_LINE = re.compile(r"^\[moviola\] working dir: (.+)$", re.MULTILINE)


def _mode(path: Path) -> str:
    return oct(stat.S_IMODE(path.stat().st_mode))


@pytest.fixture
def honours_modes(tmp_path: Path) -> None:
    """Skip where the filesystem invents permissions instead of storing them."""
    probe = tmp_path / ".mode-probe"
    probe.mkdir(mode=0o700)
    if stat.S_IMODE(probe.stat().st_mode) != 0o700:
        pytest.skip(
            f"this filesystem does not honour directory modes "
            f"({_mode(probe)} for a 0o700 mkdir), so nothing here can be checked"
        )
    probe.rmdir()


def _run(args: list[str], cwd: Path, tmpdir: Path) -> subprocess.CompletedProcess[str]:
    """Run moviola under a known-permissive umask, in an isolated TMPDIR.

    `umask 022` is set inside the shell rather than with `os.umask` in the parent,
    because the parent's umask is process-global and a test suite may be running
    other tests in the same process at the same time.
    """
    command = " ".join(
        ["umask", "022", "&&", "exec", shlex.quote(sys.executable),
         shlex.quote(str(ENTRY_POINT)), *(shlex.quote(a) for a in args)]
    )
    env = dict(os.environ)
    env["TMPDIR"] = str(tmpdir)
    return subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(cwd),
        env=env,
    )


def _reported_work_dir(result: subprocess.CompletedProcess[str]) -> Path:
    match = WORKING_DIR_LINE.search(result.stderr)
    assert match, (
        "moviola did not report a working directory, so the run never reached the "
        f"code under test.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return Path(match.group(1))


class TestBothWaysOfChoosingAWorkDirectoryAgree:
    """`--out-dir` must be as private as the temporary directory it replaces.

    NON-GOALS: it compares the two creation paths at one point — the mode of the
    directory moviola itself creates. It does not compare anything else about them.
    """

    def test_the_default_temporary_directory_is_private(
        self, tmp_path: Path, honours_modes: None
    ) -> None:
        """The reference the other path has to match, asserted rather than assumed."""
        result = _run([MISSING_SOURCE], cwd=tmp_path, tmpdir=tmp_path)
        work = _reported_work_dir(result)

        try:
            assert _mode(work) == "0o700", (
                f"tempfile.mkdtemp produced {_mode(work)}, so the reference this "
                "file compares --out-dir against is not what it claims."
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_an_out_dir_moviola_creates_is_private(
        self, tmp_path: Path, honours_modes: None
    ) -> None:
        """The defect: an explicit --out-dir was world-readable."""
        work = tmp_path / "work"

        result = _run([MISSING_SOURCE, "--out-dir", str(work)], cwd=tmp_path,
                      tmpdir=tmp_path)

        assert work.is_dir(), (
            f"--out-dir did not create the directory.\nstderr:\n{result.stderr}"
        )
        assert _mode(work) == "0o700", (
            f"--out-dir created {work} as {_mode(work)}. It holds the downloaded "
            "video, every frame and the transcript, and the default path "
            "(tempfile.mkdtemp) creates the same content at 0o700 — so passing the "
            "flag silently widened the audience for the whole run."
        )

    def test_an_existing_out_dir_keeps_the_mode_its_owner_chose(
        self, tmp_path: Path, honours_modes: None
    ) -> None:
        """The legitimate configuration this must NOT fire on.

        A directory the user made themselves carries a decision moviola has no
        standing to reverse, and `exist_ok=True` ignores `mode` for an existing
        directory — so this is a property of the fix, asserted, not a coincidence.
        """
        work = tmp_path / "shared"
        work.mkdir(mode=0o755)

        self_check = _mode(work)
        assert self_check == "0o755", f"the fixture could not create 0o755: {self_check}"

        _run([MISSING_SOURCE, "--out-dir", str(work)], cwd=tmp_path, tmpdir=tmp_path)

        assert _mode(work) == "0o755", (
            f"moviola changed an existing directory from 0o755 to {_mode(work)}. "
            "Tightening a directory the user already created is a decision that "
            "belongs to them; the fix applies a mode at creation only."
        )
