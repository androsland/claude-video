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

  * **The 200-file cap is not driven — but the gap is narrower than "untested".**
    Reaching it needs 201 committed files per invocation, so nothing here exercises
    the refusal. Measured 2026-08-27, not assumed: INVERTING the comparison
    (`-gt 200` to `-lt 200`) fails 4 of the 8 tests below, the positive control among
    them, because every successful build runs that line against a 5-entry archive.
    What escapes is a LOOSENED bound — `-gt 20000` passes all 8 — and that is the
    single regression this file cannot see. An earlier draft of this NON-GOAL named
    "an inverted comparison" as uncaught, which was false and would have sent someone
    to write a test that already exists in effect.

  * It checks the script's exit status and its message, never that the bundle it
    produces installs anywhere or that its contents are runnable. A `.skill` file
    holding the right file names and the wrong bytes is green here.

  * **The developer's global git config is neutralized, not accommodated.** A green run
    here says nothing about whether the script behaves on a machine whose
    `core.excludesFile`, `core.attributesFile` or `core.hooksPath` is set — it says the
    script behaves with none of them. That is the right trade for a guard test (the
    alternative is a suite whose answer depends on whose laptop it runs on), but it means
    a user-visible misbehaviour caused by their own git config is invisible here.

  * **Neutralized is a claim, so it is driven and not asserted in a comment.**
    `TestTheSandboxNeutralizesTheDevelopersGitConfig` writes a config that WOULD disarm
    two of the guards and checks they still fire. It covers three lookup locations
    (`GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM`, `$XDG_CONFIG_HOME/git/`, and
    `$HOME/.config/git/` when XDG is unset), which is not the same as covering every
    location git has: `$GIT_DIR/info/{exclude,attributes}` inside the repo under test,
    `core.hooksPath`, and anything reached through PATH rather than through config are
    all outside it. Those three are the ones that were reproduced defeating the guards,
    not a proof that the list is closed.

  * **The git floor is a skip, and the skip can be missed.** `GIT_CONFIG_GLOBAL` and
    `GIT_CONFIG_SYSTEM` are honoured from 2.32; below that git ignores them silently and
    every case here would quietly run against the real configuration. `bundle_repo`
    skips under 2.32, but only when the version string PARSES — an exotic build whose
    version cannot be read is allowed through rather than skipped, on the reasoning that
    an unreadable version is far likelier to be a modern build than an ancient one. The
    real backstop is the sandbox class itself: on a git that ignored these variables its
    three cases would fail loudly rather than pass.

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

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO / "skills" / "moviola" / "scripts" / "build-skill.sh"

# `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` are honoured from git 2.32. An older git
# does not reject them, it IGNORES them — so the sandbox below would silently stop
# isolating and every case here would run against the developer's real configuration.
GIT_CONFIG_ENV_SINCE = (2, 32)

# The developer's own git configuration must not reach any git this file runs, because
# `build-skill.sh` asks git questions whose ANSWERS a global setting can change. Two
# layers, and they do different jobs.
#
# GIT_SANDBOX removes what must not be inherited. `--exclude-standard` (build-skill.sh:22)
# consults `core.excludesFile`, so a contributor whose global ignore file happens to match
# a fixture filename gets an untracked guard that never fires and two RED tests that say
# nothing about this repository. That is not hypothetical: `core.excludesFile` is set on
# the machine this was written on, and the suite passed only because `~/.gitignore` did not
# happen to name `newthing.py`. Reproduced 2026-08-27 by pointing a global excludes file at
# the fixture filenames — 2 failed, 6 passed — and closed by the two variables below.
# The same lever also covers `core.attributesFile` (which `git archive` at build-skill.sh:31
# consults for `export-ignore`), `core.hooksPath` (a global pre-commit hook that rejects the
# fixture's commit), and `commit.gpgsign` (no key exists for the identity below, so every
# case ERRORS at setup with `exit status 128`).
#
# Emptying the two CONFIG files is not by itself enough, and that gap shipped once. Those
# two variables suppress an EXPLICIT `core.excludesFile` / `core.attributesFile`, and git
# then falls back to `$XDG_CONFIG_HOME/git/{ignore,attributes}` — or, when XDG_CONFIG_HOME
# is unset, to `$HOME/.config/git/{ignore,attributes}` — neither of which those two
# variables gate. Reproduced 2026-08-27 both ways: with a hostile ignore file at either
# location the untracked guard returned nothing while `core.excludesFile` correctly read
# as unset, and with a hostile attributes file `git archive` dropped the runtime and the
# script still reported success. Pointing XDG_CONFIG_HOME at `os.devnull` closes both
# paths at once — `/dev/null/git/ignore` cannot be opened, and SETTING the variable is
# also what stops git consulting `$HOME/.config`. A directory was considered and rejected:
# an empty directory can be written into later, a non-directory cannot.
#
# It has to be the ENVIRONMENT and not more `-c` flags: `_git()` below is only used for
# init/add/commit, while the two commands that actually consult these settings are run by
# build-skill.sh as its own bare `git` invocations. A `-c` flag on `_git()` cannot reach
# them; an inherited environment variable can. Requires git 2.32+.
#
# GIT_IDENTITY then supplies what an empty config lacks — a committer. `commit.gpgsign` is
# kept here too, redundantly with the sandbox, because it is the one setting whose absence
# turns a green run into an error rather than a wrong answer.
#
# NOT closed: a `.gitconfig` inside the repository being archived, and anything that changes
# git's behaviour through PATH rather than through config.
GIT_SANDBOX = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "XDG_CONFIG_HOME": os.devnull,
}
GIT_IDENTITY = (
    "-c", "user.email=tests@example.invalid",
    "-c", "user.name=moviola tests",
    "-c", "commit.gpgsign=false",
)


def _git_version() -> tuple[int, ...] | None:
    """The installed git's version as a tuple, or None if it could not be read."""
    try:
        out = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, timeout=30
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    parts = out.split()
    if len(parts) < 3:
        return None
    numbers = []
    for chunk in parts[2].split("."):
        if not chunk.isdigit():
            break
        numbers.append(int(chunk))
    return tuple(numbers) or None


def _write_git_config_dir(
    parent: Path, *, ignore: str = "", attributes: str = ""
) -> None:
    """A `git/` config directory of the shape git looks for under XDG or `$HOME/.config`.

    It holds exactly what would sabotage the guards below, so a test that finds the
    guards still firing has proved the sandbox reached this location.
    """
    git_dir = parent / "git"
    git_dir.mkdir(parents=True)
    if ignore:
        (git_dir / "ignore").write_text(ignore, encoding="utf-8")
    if attributes:
        (git_dir / "attributes").write_text(attributes, encoding="utf-8")


def _sandboxed_env() -> dict[str, str]:
    return {**os.environ, **GIT_SANDBOX}


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *GIT_IDENTITY, "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        env=_sandboxed_env(),
    )


@pytest.fixture
def bundle_repo(tmp_path: Path) -> Path:
    """A committed repository laid out the way `build-skill.sh` expects to find one.

    The script resolves its own repo root as `dirname $0`/../../.., so the copy has to
    sit at `skills/moviola/scripts/build-skill.sh` for the layout to be the real one.
    """
    if shutil.which("git") is None or shutil.which("unzip") is None:
        pytest.skip("build-skill.sh needs both git and unzip on PATH")
    version = _git_version()
    if version is not None and version < GIT_CONFIG_ENV_SINCE:
        pytest.skip(
            f"git {'.'.join(str(n) for n in version)} predates "
            f"{'.'.join(str(n) for n in GIT_CONFIG_ENV_SINCE)}, which is when "
            "GIT_CONFIG_GLOBAL/GIT_CONFIG_SYSTEM began to be honoured. An older git "
            "ignores them silently, so every case here would run against the "
            "developer's real git configuration instead of the sandbox."
        )

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


@pytest.fixture
def hostile_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A directory OUTSIDE the repository under test, to point git's config lookup at.

    Outside, so that writing a config tree into it cannot itself trip the untracked-file
    guard the tests below are trying to observe.
    """
    return tmp_path_factory.mktemp("hostile-git-config")


def _build(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the copied script exactly as a developer would, from an unrelated cwd."""
    return subprocess.run(
        ["bash", str(root / "skills" / "moviola" / "scripts" / "build-skill.sh")],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(root.parent),
        env=_sandboxed_env(),
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

    def test_an_untracked_doc_is_refused(self, bundle_repo: Path) -> None:
        """The same guard on a non-`.py` file, since nothing in it is extension-scoped.

        Named for what it drives. It shipped as `test_an_untracked_skill_md_is_refused`
        while writing `reference.md`, which promised the SKILL.md case a reader would
        then believe was covered; that case is the test below, and it is a different
        question because SKILL.md is also load-bearing for the count check.
        """
        (bundle_repo / "skills" / "moviola" / "reference.md").write_text(
            "# not added\n", encoding="utf-8"
        )

        result = _build(bundle_repo)

        assert result.returncode != 0, result.stdout
        assert "skills/moviola/reference.md" in result.stderr, result.stderr

    def test_an_untracked_skill_md_is_refused_before_the_count_check(
        self, bundle_repo: Path
    ) -> None:
        """SKILL.md itself present on disk but never committed — which guard speaks?

        Two guards could plausibly catch this and they say different things. The
        untracked guard names the file and explains the bundle would be incomplete;
        the SKILL.md count check reports `found 0`, which sends a developer looking
        for a second SKILL.md that does not exist. This pins the useful one, and the
        negative assertion is the half that makes it a test of ORDER rather than of
        failure. `git rm --cached` then a commit is how the tree gets back to clean
        with the file untracked, so the dirty-tree guard is not what fires.
        """
        _git(bundle_repo, "rm", "--cached", "-q", "skills/moviola/SKILL.md")
        _git(bundle_repo, "commit", "-qm", "SKILL.md was never committed")

        result = _build(bundle_repo)

        assert result.returncode != 0, (
            "an untracked SKILL.md built a bundle that could not contain it.\n"
            f"stdout:\n{result.stdout}"
        )
        assert "untracked files under skills/moviola" in result.stderr, result.stderr
        assert "skills/moviola/SKILL.md" in result.stderr, result.stderr
        assert "expected exactly one SKILL.md" not in result.stderr, (
            "the count check spoke first. Its message reports `found 0`, which "
            "describes the archive rather than the mistake, and sends a developer "
            f"looking for a duplicate that does not exist.\n{result.stderr}"
        )

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


class TestTheSandboxNeutralizesTheDevelopersGitConfig:
    """The isolation this file depends on, driven rather than trusted.

    Every other test here is only as meaningful as the sandbox above. A contributor
    whose global ignore file happens to name `newthing.py` would get an untracked guard
    that never fires and a green run that says nothing about this repository — the
    failure mode is a false PASS, which is the one a test suite cannot report on itself.
    So each case sets up the hostile configuration first and then asserts the guard it
    would disarm still bites.

    These are also the cases that found the gap. Emptying `GIT_CONFIG_GLOBAL` and
    `GIT_CONFIG_SYSTEM` suppresses an explicit `core.excludesFile`, and it was written
    up in a commit message as closing the question; git then falls back to a path those
    variables do not gate, and the guard stayed disarmed while `core.excludesFile` read
    as unset.

    NON-GOALS: three lookup locations, not all of them — see the file docstring. And
    each case pins that the guard still FIRES, not that git ignored the hostile file for
    the reason claimed; a sandbox that worked by accident would pass these.
    """

    def test_an_ignore_file_reached_through_xdg_does_not_disarm_the_untracked_guard(
        self,
        bundle_repo: Path,
        hostile_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`$XDG_CONFIG_HOME/git/ignore` — consulted when `core.excludesFile` is unset."""
        _write_git_config_dir(hostile_config, ignore="newthing.py\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(hostile_config))

        new_module = bundle_repo / "skills" / "moviola" / "scripts" / "newthing.py"
        new_module.write_text("# never added\n", encoding="utf-8")

        result = _build(bundle_repo)

        assert result.returncode != 0, (
            "a global ignore file found through XDG_CONFIG_HOME named the untracked "
            "module, so `--exclude-standard` skipped it and the guard reported a clean "
            "tree. Emptying GIT_CONFIG_GLOBAL/GIT_CONFIG_SYSTEM does not reach this "
            f"path; XDG_CONFIG_HOME has to be set too.\nstdout:\n{result.stdout}"
        )
        assert "skills/moviola/scripts/newthing.py" in result.stderr, result.stderr

    def test_an_attributes_file_reached_through_xdg_does_not_empty_the_bundle(
        self,
        bundle_repo: Path,
        hostile_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The silent half: `export-ignore` drops the runtime and the script says `built`.

        Asserted on the bundle's CONTENTS rather than on the exit status, because that
        is the whole shape of the defect — nothing fails, and the success message still
        reports a file count and a size.
        """
        _write_git_config_dir(hostile_config, attributes="*.py export-ignore\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(hostile_config))

        result = _build(bundle_repo)

        assert result.returncode == 0, (
            f"the build did not reach its success path.\nstderr:\n{result.stderr}"
        )
        listing = subprocess.run(
            ["unzip", "-l", str(bundle_repo / "dist" / "moviola.skill")],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
        assert "moviola/scripts/moviola.py" in listing, (
            "a global attributes file found through XDG_CONFIG_HOME applied "
            "`export-ignore` to the runtime, so `git archive` left it out and the "
            "script reported a successful build of a bundle with no code in it.\n"
            f"{listing}"
        )

    def test_an_ignore_file_reached_through_home_does_not_disarm_the_untracked_guard(
        self,
        bundle_repo: Path,
        hostile_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`$HOME/.config/git/ignore` — the fallback when XDG_CONFIG_HOME is unset.

        This is the case that makes SETTING XDG_CONFIG_HOME the fix rather than merely
        overriding a variable the developer happened to export: git consults `$HOME`
        only while XDG_CONFIG_HOME is absent, so setting it closes this path too.
        """
        _write_git_config_dir(hostile_config / ".config", ignore="newthing.py\n")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("HOME", str(hostile_config))

        new_module = bundle_repo / "skills" / "moviola" / "scripts" / "newthing.py"
        new_module.write_text("# never added\n", encoding="utf-8")

        result = _build(bundle_repo)

        assert result.returncode != 0, (
            "a global ignore file at $HOME/.config/git/ignore disarmed the untracked "
            "guard. git reads it when XDG_CONFIG_HOME is unset, and neither "
            "GIT_CONFIG_GLOBAL nor GIT_CONFIG_SYSTEM gates that lookup.\n"
            f"stdout:\n{result.stdout}"
        )
        assert "skills/moviola/scripts/newthing.py" in result.stderr, result.stderr
