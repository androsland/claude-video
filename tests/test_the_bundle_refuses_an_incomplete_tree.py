"""`build-skill.sh`'s refusals, driven by running the real script.

`build-skill.sh` builds the claude.ai upload bundle with `git archive HEAD:skills/moviola`
— it reads the COMMIT, never the working tree. Three guards exist because of that gap,
and none of them had a test. `test_the_release_is_reproducible.py` said in its NON-GOALS
that "`build-skill.sh` has its own coverage"; it did not. The only thing exercising this
script was `TestThePublishedBundleShipsWhatGitattributesClaims`, which re-implements the
`git archive` call to inspect the file list and never runs the script at all. That
sentence has been narrowed to what is true.

The guard the review named is the untracked one, and it is the subtle one. `git diff` and
`git diff --cached` both report nothing for a file git has never seen, so a new runtime
module that was written but not added produced a bundle missing it — under a success
message giving a file count and a size, which is exactly the shape of output a person
reads as confirmation.

Every case below runs the real `build-skill.sh` against a synthetic repository built for
that case, so what is pinned is the script's behaviour rather than a restatement of its
source.

NON-GOALS, so a green run here is not read as more than it is:

  * **It says nothing about THIS repository's tree.** The script is copied into a
    throwaway repo and run there. A green run here means the guards work, not that
    `dist/moviola.skill` built from this checkout would be complete.

  * **The 200-file cap is not driven.** Reaching it needs 201 committed files per
    invocation, and the check is a single integer comparison whose failure mode is
    arithmetic rather than logic. It is left to inspection deliberately, and the
    consequence is that a regression there — an inverted comparison, a wrong bound —
    would not be caught here.

  * It checks the script's exit status and its message, never that the bundle it
    produces installs anywhere or that its contents are runnable. A `.skill` file
    holding the right file names and the wrong bytes is green here.

  * It cannot see a guard that is missing entirely. Each test names a condition the
    script already refuses; nothing enumerates the conditions it *should* refuse, so a
    fourth hazard nobody has thought of is invisible by construction.

  * The legitimate configurations it must NOT fire on are pinned as their own passing
    cases rather than left implicit: an untracked file OUTSIDE `skills/moviola`, and an
    untracked file inside it that `.gitignore` already excludes. Both are ordinary
    working states — scratch notes at the repo root, a local `__pycache__` — and a guard
    that refused either would make the script unusable in a real checkout.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO / "skills" / "moviola" / "scripts" / "build-skill.sh"

# Identity for the throwaway repo's single commit. Passed with -c rather than written
# into a config file so nothing here depends on, or alters, the developer's own git
# identity.
GIT_IDENTITY = (
    "-c", "user.email=tests@example.invalid",
    "-c", "user.name=moviola tests",
)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *GIT_IDENTITY, "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )


@pytest.fixture
def bundle_repo(tmp_path: Path) -> Path:
    """A committed repository laid out the way `build-skill.sh` expects to find one.

    The script resolves its own repo root as `dirname $0`/../../.., so the copy has to
    sit at `skills/moviola/scripts/build-skill.sh` for the layout to be the real one.
    """
    if shutil.which("git") is None or shutil.which("unzip") is None:
        pytest.skip("build-skill.sh needs both git and unzip on PATH")

    scripts = tmp_path / "skills" / "moviola" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(BUILD_SCRIPT, scripts / "build-skill.sh")
    (scripts / "moviola.py").write_text("# runtime stub\n", encoding="utf-8")
    (tmp_path / "skills" / "moviola" / "SKILL.md").write_text(
        "---\nname: moviola\n---\n", encoding="utf-8"
    )
    # Something tracked OUTSIDE the subtree that ships, so the dirty-tree cases can
    # dirty a file the untracked guard is deliberately not scoped to.
    (tmp_path / "README.md").write_text("# throwaway\n", encoding="utf-8")

    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


def _build(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the copied script exactly as a developer would, from an unrelated cwd."""
    return subprocess.run(
        ["bash", str(root / "skills" / "moviola" / "scripts" / "build-skill.sh")],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(root.parent),
    )


class TestACleanTreeBuilds:
    """The positive control. Without it every refusal below could pass on a broken script.

    NON-GOALS: it proves the script reaches its success path and writes a file with the
    committed runtime in it. It does not validate the bundle beyond the presence of two
    names.
    """

    def test_a_committed_tree_produces_a_bundle(self, bundle_repo: Path) -> None:
        result = _build(bundle_repo)

        assert result.returncode == 0, (
            f"a clean committed tree did not build.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        out = bundle_repo / "dist" / "moviola.skill"
        assert out.is_file(), f"no bundle at {out}; stdout:\n{result.stdout}"
        assert "built dist/moviola.skill" in result.stdout

        listing = subprocess.run(
            ["unzip", "-l", str(out)], capture_output=True, text=True, timeout=60
        ).stdout
        assert "moviola/SKILL.md" in listing, listing
        assert "moviola/scripts/moviola.py" in listing, listing


class TestTheBundleRefusesATreeItWouldMisrepresent:
    """Each guard, driven by the condition it exists to refuse.

    NON-GOALS: these assert the refusal and its message, not the exit code's value
    beyond being non-zero — the script uses 1 throughout and nothing depends on which
    non-zero it is.
    """

    def test_an_unstaged_change_is_refused(self, bundle_repo: Path) -> None:
        """`git archive` reads HEAD, so an uncommitted edit would silently not ship."""
        (bundle_repo / "README.md").write_text("# edited\n", encoding="utf-8")

        result = _build(bundle_repo)

        assert result.returncode != 0, result.stdout
        assert "working tree is dirty" in result.stderr, result.stderr

    def test_a_staged_but_uncommitted_change_is_refused(self, bundle_repo: Path) -> None:
        """Staging is not committing, and the archive reads the commit."""
        (bundle_repo / "README.md").write_text("# staged\n", encoding="utf-8")
        _git(bundle_repo, "add", "README.md")

        result = _build(bundle_repo)

        assert result.returncode != 0, result.stdout
        assert "working tree is dirty" in result.stderr, result.stderr

    def test_an_untracked_runtime_module_is_refused_and_named(
        self, bundle_repo: Path
    ) -> None:
        """The guard the review named: invisible to both `git diff` invocations above."""
        new_module = bundle_repo / "skills" / "moviola" / "scripts" / "newthing.py"
        new_module.write_text("# never added\n", encoding="utf-8")

        result = _build(bundle_repo)

        assert result.returncode != 0, (
            "an untracked module under skills/moviola built a bundle that could not "
            f"contain it.\nstdout:\n{result.stdout}"
        )
        assert "untracked files under skills/moviola" in result.stderr, result.stderr
        assert "skills/moviola/scripts/newthing.py" in result.stderr, (
            "the refusal did not name the offending file, so a developer is told "
            f"something is wrong but not what.\n{result.stderr}"
        )

    def test_an_untracked_skill_md_is_refused(self, bundle_repo: Path) -> None:
        """The same guard, on the file whose absence would be least visible."""
        (bundle_repo / "skills" / "moviola" / "reference.md").write_text(
            "# not added\n", encoding="utf-8"
        )

        result = _build(bundle_repo)

        assert result.returncode != 0, result.stdout
        assert "skills/moviola/reference.md" in result.stderr, result.stderr

    def test_a_second_skill_md_is_refused(self, bundle_repo: Path) -> None:
        """claude.ai resolves one SKILL.md per bundle; two is ambiguous, not additive.

        Committed rather than left untracked, so this drives the SKILL.md count check
        specifically and not the untracked guard two tests up.
        """
        nested = bundle_repo / "skills" / "moviola" / "reference"
        nested.mkdir()
        (nested / "SKILL.md").write_text("---\nname: other\n---\n", encoding="utf-8")
        _git(bundle_repo, "add", "-A")
        _git(bundle_repo, "commit", "-qm", "a second SKILL.md")

        result = _build(bundle_repo)

        assert result.returncode != 0, (
            "a bundle with two SKILL.md files built successfully.\n"
            f"stdout:\n{result.stdout}"
        )
        assert "expected exactly one SKILL.md" in result.stderr, result.stderr


class TestTheGuardIsScopedToWhatShips:
    """The two passing cases, so the guard is pinned as scoped rather than as absent.

    A test suite that only drives refusals cannot tell a correctly-scoped guard from one
    that refuses everything, and `UNTRACKED=$(git ls-files --others --exclude-standard
    -- skills/moviola)` carries both a path scope and an exclude flag that a widening
    edit would quietly drop.

    NON-GOALS: it pins the scope at two points, not the whole boundary. A path that is
    neither the repo root nor inside `skills/moviola` — a sibling top-level directory —
    is not driven here.
    """

    def test_an_untracked_file_outside_the_subtree_still_builds(
        self, bundle_repo: Path
    ) -> None:
        """Scratch notes at the repo root are an ordinary state, not a broken bundle."""
        (bundle_repo / "NOTES.md").write_text("# scratch\n", encoding="utf-8")

        result = _build(bundle_repo)

        assert result.returncode == 0, (
            "an untracked file OUTSIDE skills/moviola blocked the build. It cannot "
            "affect a bundle archived from skills/moviola, so refusing it makes the "
            f"script unusable in a working checkout.\nstderr:\n{result.stderr}"
        )

    def test_an_ignored_file_inside_the_subtree_still_builds(
        self, bundle_repo: Path
    ) -> None:
        """`--exclude-standard` is what keeps __pycache__ from blocking every build."""
        (bundle_repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        _git(bundle_repo, "add", ".gitignore")
        _git(bundle_repo, "commit", "-qm", "ignore caches")
        cache = bundle_repo / "skills" / "moviola" / "scripts" / "__pycache__"
        cache.mkdir()
        (cache / "moviola.cpython-310.pyc").write_bytes(b"\x00")

        result = _build(bundle_repo)

        assert result.returncode == 0, (
            "a gitignored file inside skills/moviola blocked the build. Dropping "
            "--exclude-standard would make every build after the first one fail on "
            f"the bytecode cache the first one created.\nstderr:\n{result.stderr}"
        )
