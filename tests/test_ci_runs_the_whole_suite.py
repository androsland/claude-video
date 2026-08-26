"""Pins that CI runs this suite, and runs the WHOLE of it.

Two claims, and only the second is load-bearing.

**Claim 1 — something runs the suite.** Until this commit the only workflow in
the repository was `release.yml`, triggered on `push: tags: v*`, and it had
never executed once. So the tag that publishes `moviola.skill` to the world was
gated by nothing except whatever the person cutting it happened to run in their
terminal. This is thin — it mostly asserts that a file this commit adds exists
— but it is not circular in the direction that matters: it fails if the
workflow is later deleted, renamed away from pytest, narrowed so it no longer
fires on a pull request, pointed at `pull_request_target` (which never sees the
PR's own diff), allowed to fail, or handed a `-k` that runs part of the suite
instead of all of it.

**Claim 2 — every module the suite can silently skip on is installed in CI.**
This is the real check, and it is the one worth reading. `pytest` reports a
skipped test as a dot-adjacent `s` and exits 0, so a runner missing an optional
dependency produces a GREEN run that covered less than it looks like it did.
Measured on this branch: blocking `yt_dlp` takes **24 of the 34 tests** in
`test_the_fallback_stays_small.py` — the entire behavioural half of the
format-ladder work — out of the run, behind an exit code of 0. Nothing would
have said so. The delta is quoted rather than a pair of totals because a total
goes stale the moment anyone adds a test, and this file has already done that
to itself once. The rule here couples the two files that must agree: whatever
the suite guards an import with, the workflow must install.

The rule is stated against three instances rather than the one that prompted
it, because a rule shaped around a single case is a sample wearing a spec's
clothes:

  1. `yt_dlp` — today's, and the reason this file exists.
  2. `markdown-it-py`, already filed in `TODOS.md` under
     `## Report as an untrusted document`: asserting the report's heading tree
     needs a real markdown parser as a dev dependency. When that lands it will
     arrive as exactly this shape and must be installed in CI, or the report
     structure tests go quiet.
  3. `faster_whisper` — filed under `## Local Whisper backend` as a `tiny`-model
     smoke test to add *if a runner ever gets a model cache*. This one is the
     counter-example, not another example: see the exemption below.

NON-GOALS — what this file does not do, and the configuration it must not fire
on:

  * **It must NOT force CI to install every optional import.** Instance 3 is the
    case: a `faster_whisper` smoke test means a multi-hundred-MB model download
    in a suite that is deliberately network-free, and requiring the workflow to
    carry it would make this rule an argument for slowing CI down rather than
    for covering it. `CI_NEED_NOT_INSTALL` is the escape hatch — a module named
    there is exempt, and the reason is required beside it because an unexplained
    exemption is indistinguishable from an oversight. It is empty today, which
    is why `test_an_exemption_is_honoured` drives the mechanism against a
    synthetic module rather than waiting for a real one to prove it works.

  * **It must NOT forbid a second workflow that happens to run pytest.** A
    scheduled job checking for `yt-dlp` drift (filed in `TODOS.md`), a nightly
    slow-subset run, or `release.yml` hardened with its own pytest step are all
    legitimate and all run pytest. `NOT_THE_SUITE_WORKFLOW` is that escape
    hatch, with the same reason-required contract. What the file needs is one
    UNAMBIGUOUS workflow to point the rest of its assertions at, not a monopoly.

  * **It reads the workflow as TEXT, not as YAML.** There is no YAML parser in
    this project's dependency set and adding one to check a dependency rule
    would be its own joke. What it does before matching is strip the two places
    a name can appear without being installed — `#` comments and `name:` display
    labels — because the workflow this rule guards mentions `yt-dlp` four times
    in its own prose, and without stripping, deleting the install line left
    every assertion here green. What survives that and is still NOT seen: a step
    behind a false `if:`, a job that never runs, and a name in a command that is
    not an install (`echo yt-dlp`). One shape fails in the loud direction: a `#`
    inside a quoted shell string is truncated as though it were a comment, which
    can hide an install and can never invent one.

  * **The `on:` block is found by indentation, and only one spelling is
    supported.** A quoted `"on":` yields an empty block and fails loudly with
    "no `on:` block". Flow style (`on: {pull_request: null}`) and the list form
    (`on: [push, pull_request]`) happen to read correctly, by accident of
    substring matching rather than by design — do not rely on it.

  * **It cannot see whether CI actually ran, passed, or installed anything.** It
    reads files in the working tree. A workflow disabled in the repository's
    Actions settings, a runner where `apt-get install ffmpeg` failed, a job
    whose `runs-on` label has no runner — all invisible here. `-rs` in the
    workflow's pytest call makes skips visible to a human reading the log; that
    is disclosure, not enforcement, and this file is the enforcement.

  * **It sees only the skips that are guarded IMPORTS.** The suite already has
    silent skips that are not: with `git` shimmed to exit 128 the full run is
    **718 passed, 8 skipped, green** — 7 from `repo_files.py` (git cannot list
    the checkout) and 1 from `test_the_docs_are_checked.py` (git archive), with
    a third site in `test_key_file_permissions.py` that fires on a filesystem
    not honouring POSIX modes. That is the same green-but-hollow failure this
    file is named for, at a fraction of the blast radius, and `optional_imports`
    structurally cannot see any of it. Filed in `TODOS.md`; disclosed here so
    "runs the WHOLE of it" is not read as covering it.

  * **The vacuity guard catches a scanner that stopped entirely, not one that
    stopped partially.** `test_the_scan_finds_something_to_check` pins that
    `yt_dlp` is still found — exactly one module, because exactly one real guard
    exists. A future optional dependency written in a shape the scanner misses
    is silently uncovered and nothing goes red. The four shapes it missed on the
    day it was written are now driven against synthetic input in
    `TestTheRuleItself`; that list is not a proof of completeness, and
    `pytestmark = pytest.mark.skipif(find_spec("x") is None)` is a known
    remaining blind spot, filed rather than fixed.

  * **It scans `tests/` by walking the working tree, deliberately.**
    `repo_files.tracked_text_files()` exists because that walk was a bug
    elsewhere, and it is the right helper for auditing what the repository
    CLAIMS — but it SKIPS when git cannot answer, and putting the load-bearing
    check of this file behind a skip would move it into the bullet above. The
    cost of the walk is a loud local failure on an untracked scratch test that
    CI would never see; that is the safe direction, so the walk stays.

  * **If the install ever moves into a requirements file, this test fails** even
    though CI would be correct, because it does not follow a reference out of
    the workflow. That is the safe direction to be wrong in, and the fix is to
    teach it to follow — not to loosen it. This is now true; while the raw text
    was searched it was a claim the file did not actually have, because the
    step's own comment kept the match alive.

  * **It says nothing about cost.** No assertion here concerns `concurrency`,
    job count, or trigger breadth. This repository is public, so Actions minutes
    are free; the day-one config in the workflow is there because it is cheap
    now and awkward to retrofit, not because anything is metered.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from repo_files import REPO as REPO_ROOT

WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
TESTS_DIR = Path(__file__).resolve().parent

# Modules the suite may guard an import on that CI is NOT required to install.
# Key: the imported module name. Value: why, in one line. An entry without a
# real reason is an oversight with a decoration on it — which is why the value
# being non-empty is enforced by a test rather than hoped for.
CI_NEED_NOT_INSTALL: dict[str, str] = {}

# Workflows that run pytest but are NOT the one gating pull requests. Key: the
# filename. Value: why. Same contract, same enforcement.
#
# This exists because "exactly one workflow runs pytest" would otherwise ban the
# hardening claim 1 argues for — giving `release.yml` its own pytest step so the
# publish tag is gated — along with a scheduled drift job and a nightly subset.
# The assertions below need ONE unambiguous workflow to read; they do not need
# it to be the only one in the repository.
NOT_THE_SUITE_WORKFLOW: dict[str, str] = {}

# Flags that narrow a pytest run without turning it red. Each leaves the word
# `pytest` in the workflow while shrinking what it covers, which is claim 2's
# failure mode expressed in the command line rather than in the install.
NARROWING_FLAGS = ("-k", "-m", "--ignore", "--ignore-glob", "--deselect")

# pytest flags that consume the following token, so a value like `tests/x.py`
# after one of them is not a positional path. Deliberately short: an unlisted
# value-taking flag makes the positional check fire on a legitimate command,
# which is loud and fixable, where the reverse is a narrowed run going unseen.
FLAGS_TAKING_A_VALUE = ("-p", "-n", "-c", "-o", "--rootdir", "--junitxml")


# --------------------------------------------------------------------------
# Reading the workflows
# --------------------------------------------------------------------------

def workflow_files() -> dict[str, str]:
    """Every workflow in `.github/workflows`, by filename."""
    if not WORKFLOW_DIR.is_dir():
        return {}
    found = {}
    for path in sorted(WORKFLOW_DIR.iterdir()):
        if path.suffix not in (".yml", ".yaml") or not path.is_file():
            continue
        try:
            found[path.name] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            # Name the file. The default message carries the codec and the byte
            # offset and not the path, which is the one thing needed to fix it.
            pytest.fail(f"{path} is not valid UTF-8: {exc}")
    return found


def without_comments(text: str) -> str:
    """`text` with YAML comments removed, line structure preserved.

    A comment starts at a `#` that begins a line or follows whitespace — which
    is why `pip install git+https://host/x.git#egg=yt_dlp` keeps its fragment
    and is still read as an install. A `#` inside a quoted shell string is
    truncated as if it were a comment; that can hide an install and cannot
    invent one, so the error is in the loud direction.
    """
    kept = []
    for line in text.splitlines():
        cut = None
        for i, char in enumerate(line):
            if char == "#" and (i == 0 or line[i - 1].isspace()):
                cut = i
                break
        kept.append(line if cut is None else line[:cut].rstrip())
    return "\n".join(kept)


def workflow_claims(text: str) -> str:
    """`text` with everything that cannot install anything removed.

    Comments, and `name:` values — the workflow's display label and each step's.
    Both are prose, and both named `yt-dlp` in the workflow this rule was
    written against, which was enough to keep every assertion here green after
    the install line was deleted.
    """
    kept = [
        line
        for line in without_comments(text).splitlines()
        if not line.lstrip().lstrip("-").lstrip().startswith("name:")
    ]
    return "\n".join(kept)


def pytest_invocations(text: str) -> list[str]:
    """Every line that RUNS pytest, as opposed to installing or naming it."""
    return [
        line.strip()
        for line in without_comments(text).splitlines()
        if "pytest" in line
        and "pip install" not in line
        and not line.lstrip().lstrip("-").lstrip().startswith("name:")
    ]


def suite_workflows() -> dict[str, str]:
    """The workflows that run pytest. Should be exactly one."""
    return {
        name: text
        for name, text in workflow_files().items()
        if name not in NOT_THE_SUITE_WORKFLOW and pytest_invocations(text)
    }


def the_suite_workflow() -> tuple[str, str]:
    """`(filename, text)` of the one workflow that runs pytest.

    Fails with the cause rather than unpacking blind. Every check below needs
    this workflow, and a bare `(name, text), = ...` on an empty dict raises
    `ValueError: not enough values to unpack` — loud, but it names the
    unpacking as the problem when the problem is that no workflow runs the
    suite at all. Both conditions live here rather than being restated in a
    test, because a restated rule is a rule with two copies to drift apart.
    """
    running = suite_workflows()
    if not running:
        pytest.fail(
            "no workflow in .github/workflows runs pytest — the suite is gated "
            f"by nothing. Workflows present: {sorted(workflow_files())}"
        )
    if len(running) > 1:
        pytest.fail(
            f"more than one workflow runs pytest: {sorted(running)}. The checks "
            "in this file need one unambiguous workflow to read. A second one "
            "that legitimately runs pytest — a scheduled drift job, a nightly "
            "subset, a hardened release.yml — goes in NOT_THE_SUITE_WORKFLOW "
            "with a reason."
        )
    return next(iter(running.items()))


def top_level_block(text: str, key: str) -> str:
    """The indented body under a column-zero `key:` line.

    Indentation-scoped, not parsed. Returns "" when there is no such block, so
    a caller asserting on the result fails rather than reading an empty string
    as an answer. A column-zero COMMENT does not end the block: the workflow
    this reads is heavily commented at column zero, and terminating on one
    truncated the `on:` block and failed a correct file.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"{key}:") and not line[:1].isspace():
            start = i
            break
    if start is None:
        return ""
    body = [lines[start][len(key) + 1:]]
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            body.append(line)
            continue
        if line[:1].isspace():
            body.append(line)
        else:
            break
    return "\n".join(body)


# --------------------------------------------------------------------------
# Reading the suite's optional imports
# --------------------------------------------------------------------------

def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    """Does this handler swallow an ImportError?

    A bare `except:` and `except Exception:` both do. Reading them as guards
    over-detects — a `try` around something else entirely gets its imports
    reported — and over-detection here is a LOUD failure with a documented
    escape hatch, where under-detection is a module quietly uncovered forever.
    """
    if handler.type is None:
        return True
    names = []
    if isinstance(handler.type, ast.Name):
        names = [handler.type.id]
    elif isinstance(handler.type, ast.Tuple):
        names = [e.id for e in handler.type.elts if isinstance(e, ast.Name)]
    return any(
        n in ("ImportError", "ModuleNotFoundError", "Exception", "BaseException")
        for n in names
    )


def optional_imports(paths: list[Path]) -> dict[str, set[str]]:
    """`{module: {filenames that can skip on it}}` for guarded imports.

    Three guard shapes are recognised, because all three are idiomatic and this
    file would be worthless if it saw only the one currently in use:

      * `try: import X` / `except ImportError:` — the shape
        `test_the_fallback_stays_small.py` uses. The try body is walked in
        full rather than by direct children: an import one block deep inside an
        `if` or a `with` was invisible, and invisible here means green.
      * `try: importlib.import_module("X")` — the same guard, spelled
        dynamically.
      * `pytest.importorskip("X")` — the shape a future module is at least as
        likely to reach for.

    A NON-LITERAL module name in either dynamic form fails loudly rather than
    being dropped, because dropping it is a silent hole of exactly the kind
    this file exists to close.

    Only the top-level package name is reported (`a.b.c` -> `a`), because that
    is what gets installed.
    """
    found: dict[str, set[str]] = {}

    def note(module: str, where: Path) -> None:
        found.setdefault(module.split(".")[0], set()).add(where.name)

    def literal_arg(call: ast.Call, path: Path, what: str) -> str | None:
        if not call.args:
            return None
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        pytest.fail(
            f"{path.name}:{call.lineno} calls {what} with a non-literal "
            "argument, so this scan cannot tell which module the suite can "
            "skip on. Pass the name as a string literal, or the coupling this "
            "file enforces stops covering it — silently."
        )
        return None

    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Try) and any(
                _catches_import_error(h) for h in node.handlers
            ):
                for stmt in node.body:
                    for sub in ast.walk(stmt):
                        if isinstance(sub, ast.Import):
                            for alias in sub.names:
                                note(alias.name, path)
                        elif isinstance(sub, ast.ImportFrom) and sub.module and not sub.level:
                            note(sub.module, path)
                        elif isinstance(sub, ast.Call):
                            called = sub.func
                            called_name = (
                                called.attr
                                if isinstance(called, ast.Attribute)
                                else getattr(called, "id", "")
                            )
                            if called_name == "import_module":
                                module = literal_arg(sub, path, "import_module")
                                if module:
                                    note(module, path)
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name == "importorskip":
                    module = literal_arg(node, path, "pytest.importorskip")
                    if module:
                        note(module, path)
    return found


def unexplained_exemptions(exemptions: dict[str, str]) -> list[str]:
    """Exempt keys whose reason is blank.

    An unexplained exemption is indistinguishable from an oversight, which is
    the whole point of the value being required. Lives here rather than inside
    the test because both exemption dicts are empty today, so the rule has to
    be callable against synthetic input to be tested at all.
    """
    return sorted(key for key, why in exemptions.items() if not why.strip())


def suite_test_files() -> list[Path]:
    # A working-tree walk, deliberately — see the NON-GOAL. `repo_files
    # .tracked_text_files()` is the repo's helper for "what does this repository
    # CLAIM", and it skips when git cannot answer. A skip in the load-bearing
    # check of a file about silent skips is the wrong trade.
    return sorted(TESTS_DIR.rglob("*.py"))


def installed_by(text: str, module: str) -> bool:
    """Does this workflow text name `module` in either import or dist spelling?

    `yt_dlp` is installed as `yt-dlp`; the two spellings are the same name with
    a different separator, and nothing here needs to know which is which.

    Matched against `workflow_claims`, not raw text: the workflow names
    `yt-dlp` four times in comments and once in a step label, so against raw
    text this returned True with the install line deleted.
    """
    lowered = workflow_claims(text).lower()
    return module.lower() in lowered or module.lower().replace("_", "-") in lowered


# --------------------------------------------------------------------------


class TestSomethingRunsTheSuite:
    def test_exactly_one_workflow_runs_the_suite(self):
        # The rule and its two failure messages live in `the_suite_workflow()`,
        # which every other test here calls. Restating them is how two copies
        # of one rule drift apart.
        name, _ = the_suite_workflow()
        assert name in workflow_files()

    def test_it_fires_on_pull_request(self):
        name, text = the_suite_workflow()
        triggers = top_level_block(without_comments(text), "on")
        assert triggers, f"{name} has no column-zero `on:` block"
        # `pull_request_target` CONTAINS `pull_request`, so it is removed before
        # the check rather than trusted to be a different string.
        assert "pull_request" in triggers.replace("pull_request_target", ""), (
            f"{name} does not run on pull_request, so a PR can be merged "
            "without the suite ever having run against it"
        )

    def test_it_does_not_fire_on_pull_request_target(self):
        # `actions/checkout` with no `ref:` under `pull_request_target` checks
        # out the BASE commit, so every PR gets a green check that never saw its
        # own diff — claim 1 satisfied in form and empty in substance. It is
        # also the standard privilege-escalation trigger, running against the
        # base repository's secrets with a write token.
        name, text = the_suite_workflow()
        triggers = top_level_block(without_comments(text), "on")
        assert "pull_request_target" not in triggers, (
            f"{name} fires on pull_request_target. The suite would run against "
            "the base commit rather than the PR, and the check would be green "
            "for a diff nothing tested."
        )

    def test_the_suite_step_is_not_allowed_to_fail(self):
        # `continue-on-error: true` on the pytest step makes the whole suite
        # advisory: every test can be red and the job still reports success.
        name, text = the_suite_workflow()
        assert "continue-on-error" not in without_comments(text), (
            f"{name} sets continue-on-error, so a red suite reports green"
        )

    def test_the_pytest_invocation_is_not_narrowed(self):
        # Claim 2's failure mode written in the command line instead of the
        # install: each of these shrinks what runs while leaving the word
        # `pytest` in the file, so every other assertion here stays green.
        name, text = the_suite_workflow()
        lines = pytest_invocations(text)
        assert lines, f"{name} has no pytest command line"
        for line in lines:
            tokens = line.split()
            if "pytest" not in tokens:
                # `python -mpytest` and friends. Conservative: unrecognised
                # spelling is skipped rather than guessed at.
                continue
            # Only what follows `pytest` — `python -m pytest` puts a `-m` in
            # the line that is the interpreter's, not pytest's marker flag.
            after = tokens[tokens.index("pytest") + 1:]
            for flag in NARROWING_FLAGS:
                assert not any(
                    token == flag or token.startswith(f"{flag}=") for token in after
                ), f"{name} narrows the run with {flag}: {line!r}"
            skip_next = False
            for token in after:
                if skip_next:
                    skip_next = False
                    continue
                if token.startswith("-"):
                    skip_next = token in FLAGS_TAKING_A_VALUE
                    continue
                pytest.fail(
                    f"{name} passes a positional argument to pytest ({token!r}), "
                    f"which runs part of the suite instead of all of it: {line!r}"
                )

    def test_the_release_workflow_is_not_the_one_running_it(self):
        # Not a style preference. release.yml fires on `push: tags: v*`, which
        # is after the decision to publish, and TODOS.md keeps its defects in a
        # section of their own. A suite that only runs at tag time tells you
        # the release is broken instead of stopping it. If release.yml is ever
        # hardened with a pytest step of its own that is a good change, and it
        # belongs in NOT_THE_SUITE_WORKFLOW rather than being blocked by this.
        assert "release.yml" not in suite_workflows()


class TestTheWholeSuiteRunsInCI:
    def test_the_scan_finds_something_to_check(self):
        # Vacuity guard. Every assertion below iterates the scan's output, so
        # a scanner that silently stopped matching would turn all of them into
        # passes over an empty set. yt_dlp is the instance that exists today —
        # and it is the ONLY one, which is why a PARTIAL scanner failure is a
        # stated NON-GOAL rather than something this catches.
        found = optional_imports(suite_test_files())
        assert found, "the optional-import scan found nothing — it has stopped working"
        assert "yt_dlp" in found, (
            "yt_dlp is no longer detected as a guarded import. If the guard was "
            f"genuinely removed, delete this assertion. Found: {sorted(found)}"
        )

    def test_every_module_the_suite_can_skip_on_is_installed(self):
        name, text = the_suite_workflow()
        missing = {
            module: sorted(files)
            for module, files in optional_imports(suite_test_files()).items()
            if module not in CI_NEED_NOT_INSTALL and not installed_by(text, module)
        }
        assert not missing, (
            f"{name} does not install {sorted(missing)}, and the suite guards "
            f"an import on each: {missing}. pytest exits 0 on a skip, so those "
            "tests would go quiet and the run would still be green. Install it "
            "in the workflow, or add it to CI_NEED_NOT_INSTALL with a reason."
        )

    def test_ffmpeg_is_installed(self):
        # Not an import guard, so the scan above cannot see it: conftest.py
        # synthesizes every clip by shelling out to ffmpeg, and a runner
        # without it fails the fixture rather than skipping. Named separately
        # because the failure is loud but the cause is not obvious. Read from
        # `workflow_claims` for the same reason as `installed_by`: the step's
        # own label and comment both say "ffmpeg", and satisfied a raw-text
        # match with the entire install step deleted.
        _, text = the_suite_workflow()
        assert "ffmpeg" in workflow_claims(text)

    def test_the_job_has_a_timeout(self):
        # GitHub's default is 360 MINUTES. A hung apt mirror or a wedged ffmpeg
        # consumes all of it before anyone notices, and `cancel-in-progress`
        # does not help because the concurrency group is per-ref. As thin as
        # the ffmpeg check and for the same reason: the only failure mode it
        # has is the line going missing, which is exactly what it catches.
        name, text = the_suite_workflow()
        assert "timeout-minutes:" in without_comments(text), (
            f"{name} sets no timeout-minutes, so a stuck job runs for GitHub's "
            "360-minute default"
        )


class TestTheRuleItself:
    """The scanner and its escape hatches, driven against synthetic input.

    The classes above assert what the rule currently answers. These assert the
    rule can still answer — a distinction that matters because the guard shapes
    and both exemption paths are otherwise exercised by exactly one, zero and
    zero real cases respectively.
    """

    def test_both_guard_shapes_are_seen(self, tmp_path):
        sample = tmp_path / "test_sample.py"
        sample.write_text(
            "import pytest\n"
            "try:\n"
            "    import alpha.beta\n"
            "except ImportError:\n"
            "    alpha = None\n"
            "gamma = pytest.importorskip('gamma.delta')\n",
            encoding="utf-8",
        )
        found = optional_imports([sample])
        assert set(found) == {"alpha", "gamma"}, (
            "the scan must see both a try/except ImportError guard and an "
            f"importorskip call, reduced to top-level names. Saw: {sorted(found)}"
        )

    @pytest.mark.parametrize(
        "source, expected",
        [
            (
                "try:\n    if True:\n        import alpha\n"
                "except ImportError:\n    alpha = None\n",
                "alpha",
            ),
            ("try:\n    import beta\nexcept Exception:\n    beta = None\n", "beta"),
            ("try:\n    import gamma\nexcept:\n    gamma = None\n", "gamma"),
            (
                "import importlib\ntry:\n"
                "    delta = importlib.import_module('delta.core')\n"
                "except ImportError:\n    delta = None\n",
                "delta",
            ),
        ],
        ids=["nested-in-the-try-body", "except-Exception", "bare-except", "import_module"],
    )
    def test_the_guard_shapes_that_were_once_invisible(self, tmp_path, source, expected):
        # Each of these returned nothing when this file was first written, and
        # a false negative here is silent in the dangerous direction: fewer
        # modules found means `missing` is empty means green.
        sample = tmp_path / "test_sample.py"
        sample.write_text(source, encoding="utf-8")
        assert expected in optional_imports([sample])

    @pytest.mark.parametrize(
        "source",
        [
            "import pytest\nMOD = 'delta'\ndelta = pytest.importorskip(MOD)\n",
            "import importlib\nMOD = 'delta'\ntry:\n"
            "    delta = importlib.import_module(MOD)\n"
            "except ImportError:\n    delta = None\n",
        ],
        ids=["importorskip", "import_module"],
    )
    def test_a_non_literal_module_name_fails_loudly(self, tmp_path, source):
        # The alternative is dropping it, which is how a covered module stops
        # being covered without anything going red.
        sample = tmp_path / "test_sample.py"
        sample.write_text(source, encoding="utf-8")
        with pytest.raises(BaseException, match="non-literal"):
            optional_imports([sample])

    def test_a_bare_import_is_not_treated_as_optional(self, tmp_path):
        # The direction that would make this file useless in the other way: an
        # unguarded import cannot skip, so requiring CI to install every import
        # in the suite would flag `download`, `moviola` and the stdlib.
        sample = tmp_path / "test_sample.py"
        sample.write_text("import json\nimport download\n", encoding="utf-8")
        assert optional_imports([sample]) == {}

    def test_a_try_that_catches_something_else_is_not_an_import_guard(self, tmp_path):
        sample = tmp_path / "test_sample.py"
        sample.write_text(
            "try:\n    import alpha\nexcept ValueError:\n    alpha = None\n",
            encoding="utf-8",
        )
        assert optional_imports([sample]) == {}

    def test_an_exemption_is_honoured(self, tmp_path, monkeypatch):
        # CI_NEED_NOT_INSTALL is empty today, so without this the first real
        # exemption would be the first time the mechanism ever ran.
        sample = tmp_path / "test_sample.py"
        sample.write_text(
            "try:\n    import faster_whisper\nexcept ImportError:\n"
            "    faster_whisper = None\n",
            encoding="utf-8",
        )
        found = optional_imports([sample])
        assert "faster_whisper" in found

        workflow = "steps:\n  - run: pip install pytest\n"

        def still_missing() -> list[str]:
            return [
                module
                for module in found
                if module not in CI_NEED_NOT_INSTALL and not installed_by(workflow, module)
            ]

        # The negative half. Without it an `installed_by()` hardwired to True
        # passes this test identically, and the exemption path would be
        # untested while looking tested.
        assert still_missing() == ["faster_whisper"]

        monkeypatch.setitem(
            CI_NEED_NOT_INSTALL,
            "faster_whisper",
            "a real model load is a multi-hundred-MB download in a network-free suite",
        )
        assert still_missing() == []

    @pytest.mark.parametrize(
        "exemptions",
        [CI_NEED_NOT_INSTALL, NOT_THE_SUITE_WORKFLOW],
        ids=["modules", "workflows"],
    )
    def test_every_exemption_carries_a_reason(self, exemptions):
        # Both dicts document that the reason is required. Both are empty
        # today, which is exactly when this guard is free to add — the first
        # real entry can otherwise ship as `""` or `"TODO"` and pass.
        assert unexplained_exemptions(exemptions) == []

    @pytest.mark.parametrize("reason", ["", "   ", "\n"], ids=["empty", "spaces", "newline"])
    def test_the_reason_requirement_is_not_vacuous(self, reason, monkeypatch):
        # The test above passes over an empty dict no matter what the rule says
        # — hardwiring it to `[]` survived a mutation run. Driving the helper
        # against a synthetic blank reason is what makes the rule itself real.
        monkeypatch.setitem(CI_NEED_NOT_INSTALL, "synthetic", reason)
        assert unexplained_exemptions(CI_NEED_NOT_INSTALL) == ["synthetic"]

    def test_the_dist_spelling_counts_as_installed(self):
        assert installed_by("pip install yt-dlp\n", "yt_dlp")
        assert installed_by("pip install yt_dlp\n", "yt_dlp")
        assert not installed_by("pip install pytest\n", "yt_dlp")

    @pytest.mark.parametrize(
        "text, module, expected",
        [
            ("      - run: pip install pytest yt-dlp   # dev only\n", "yt_dlp", True),
            ("      - run: pip install git+https://host/x.git#egg=yt_dlp\n", "yt_dlp", True),
            (
                "      - name: Install deps\n"
                "        # yt-dlp is a DEV dependency, not a runtime one.\n"
                "        run: pip install pytest\n",
                "yt_dlp",
                False,
            ),
            (
                "      - name: install yt-dlp\n        run: pip install pytest\n",
                "yt_dlp",
                False,
            ),
            (
                "      - name: Install deps\n"
                "        # yt-dlp is a DEV dependency\n"
                "        run: pip install -r requirements-dev.txt\n",
                "yt_dlp",
                False,
            ),
            (
                "      - name: Install ffmpeg\n"
                "        # conftest.py shells out to ffmpeg\n"
                "        run: sudo apt-get install -y nothing\n",
                "ffmpeg",
                False,
            ),
        ],
        ids=[
            "an-inline-comment-does-not-blind-the-match",
            "a-url-fragment-is-not-a-comment",
            "a-comment-alone-is-not-an-install",
            "a-step-label-alone-is-not-an-install",
            "moved-into-a-requirements-file-fails-loudly",
            "the-same-shape-for-ffmpeg",
        ],
    )
    def test_only_text_that_could_install_counts(self, text, module, expected):
        # The first two are the configurations this must NOT fire on; the rest
        # are what it exists to catch. Stripping comments naively — cutting at
        # the first `#` — would break the URL fragment, which is why the strip
        # follows YAML's actual rule: a `#` at line start or after whitespace.
        assert installed_by(text, module) is expected

    def test_a_workflow_whose_only_pytest_is_a_label_is_not_the_suite(
        self, tmp_path, monkeypatch
    ):
        # `"pytest" in text` was satisfied by the job's own `name: pytest`, so
        # the pytest step could be deleted outright and the workflow still
        # counted as running the suite. `globals()` rather than an import name
        # because this module is the one under test.
        (tmp_path / "lint.yml").write_text(
            "name: Lint\njobs:\n  lint:\n    name: pytest\n"
            "    steps:\n      - run: ruff check .   # no pytest here\n",
            encoding="utf-8",
        )
        monkeypatch.setitem(globals(), "WORKFLOW_DIR", tmp_path)
        assert suite_workflows() == {}

    def test_a_second_workflow_can_be_excluded_with_a_reason(self, tmp_path, monkeypatch):
        (tmp_path / "tests.yml").write_text(
            "on:\n  pull_request:\njobs:\n  suite:\n"
            "    steps:\n      - run: python -m pytest -q\n",
            encoding="utf-8",
        )
        (tmp_path / "drift.yml").write_text(
            "on:\n  schedule:\n    - cron: '0 3 * * *'\njobs:\n  drift:\n"
            "    steps:\n      - run: python -m pytest -q\n",
            encoding="utf-8",
        )
        monkeypatch.setitem(globals(), "WORKFLOW_DIR", tmp_path)
        assert sorted(suite_workflows()) == ["drift.yml", "tests.yml"]

        monkeypatch.setitem(
            NOT_THE_SUITE_WORKFLOW,
            "drift.yml",
            "scheduled unpinned-yt-dlp drift check, not the pull-request gate",
        )
        assert the_suite_workflow()[0] == "tests.yml"

    @pytest.mark.parametrize(
        "text, expected_in",
        [
            ("on:\n  push:\n    branches: [main]\njobs:\n  a:\n", "push"),
            ("name: x\non:\n  pull_request:\njobs:\n  a:\n", "pull_request"),
            (
                "on:\n  push:\n# a column-zero comment\n  pull_request:\njobs:\n  a:\n",
                "pull_request",
            ),
        ],
        ids=["push-block", "block-ends-at-column-zero", "a-comment-does-not-end-it"],
    )
    def test_the_block_reader_is_bounded_by_indentation(self, text, expected_in):
        # Every case carries a trailing `jobs:` so `"jobs" not in block` is a
        # real boundary assertion rather than one passing over text that never
        # contained the word.
        block = top_level_block(text, "on")
        assert expected_in in block
        assert "jobs" not in block

    def test_a_missing_block_reads_as_empty_not_as_absent_evidence(self):
        assert top_level_block("name: x\njobs:\n  a:\n", "on") == ""
