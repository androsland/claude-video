"""Pins that CI runs this suite, and runs the WHOLE of it.

Two claims, and only the second is load-bearing.

**Claim 1 — something runs the suite.** Until this commit the only workflow in
the repository was `release.yml`, triggered on `push: tags: v*`, and it had
never executed once. So the tag that publishes `moviola.skill` to the world was
gated by nothing except whatever the person cutting it happened to run in their
terminal. This is thin — it mostly asserts that a file this commit adds exists
— but it is not circular in the direction that matters: it fails if the
workflow is later deleted, renamed away from pytest, or narrowed so it no
longer fires on a pull request.

**Claim 2 — every module the suite can silently skip on is installed in CI.**
This is the real check, and it is the one worth reading. `pytest` reports a
skipped test as a dot-adjacent `s` and exits 0, so a runner missing an optional
dependency produces a GREEN run that covered less than it looks like it did.
Measured on this branch: with `yt_dlp` importable the suite is 712 passed / 0
skipped, and with it blocked it is **688 passed / 24 skipped** — 24 of the 34
tests in `test_the_fallback_stays_small.py`, the entire behavioural half of the
format-ladder work, vanishing behind an exit code of 0. Nothing would have said
so. The rule here couples the two files that must agree: whatever the suite
guards an import with, the workflow must install.

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

  * **It reads the workflow as TEXT, not as YAML.** There is no YAML parser in
    this project's dependency set and adding one to check a dependency rule
    would be its own joke. So a module name in a comment, in a step gated behind
    a false `if:`, or in a job that never runs, all read as installed. The
    `on:` block is found by indentation, which flow style (`on: {push: ...}`) or
    a quoted `"on":` would defeat — those fail loudly with "no `on:` block",
    not quietly.

  * **It cannot see whether CI actually ran, passed, or installed anything.** It
    reads files in the working tree. A workflow disabled in the repository's
    Actions settings, a runner where `apt-get install ffmpeg` failed, a `pytest`
    invocation narrowed by `-k` — all invisible here. `-rs` in the workflow's
    pytest call makes skips visible to a human reading the log; that is
    disclosure, not enforcement, and this file is the enforcement.

  * **It scans `tests/` only.** A conditional import inside
    `skills/moviola/scripts/` that makes a code path skip at runtime is a
    different gap and not this one.

  * **If the install ever moves into a requirements file, this test fails** even
    though CI would be correct, because it does not follow a reference out of
    the workflow. That is the safe direction to be wrong in, and the fix is to
    teach it to follow — not to loosen it.

  * **It says nothing about cost.** No assertion here concerns `concurrency`,
    job count, or trigger breadth. This repository is public, so Actions minutes
    are free; the day-one config in the workflow is there because it is cheap
    now and awkward to retrofit, not because anything is metered.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
TESTS_DIR = Path(__file__).resolve().parent

# Modules the suite may guard an import on that CI is NOT required to install.
# Key: the imported module name. Value: why, in one line. An entry without a
# real reason is an oversight with a decoration on it.
CI_NEED_NOT_INSTALL: dict[str, str] = {}


# --------------------------------------------------------------------------
# Reading the workflows
# --------------------------------------------------------------------------

def workflow_files() -> dict[str, str]:
    """Every workflow in `.github/workflows`, by filename."""
    if not WORKFLOW_DIR.is_dir():
        return {}
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(WORKFLOW_DIR.iterdir())
        if path.suffix in (".yml", ".yaml") and path.is_file()
    }


def suite_workflows() -> dict[str, str]:
    """The workflows that run pytest. Should be exactly one."""
    return {
        name: text
        for name, text in workflow_files().items()
        if "pytest" in text
    }


def the_suite_workflow() -> tuple[str, str]:
    """`(filename, text)` of the one workflow that runs pytest.

    Fails with the cause rather than unpacking blind. Every check below needs
    this workflow, and a bare `(name, text), = ...` on an empty dict raises
    `ValueError: not enough values to unpack` — loud, but it names the
    unpacking as the problem when the problem is that no workflow runs the
    suite at all.
    """
    running = suite_workflows()
    if not running:
        pytest.fail(
            "no workflow in .github/workflows runs pytest. Workflows present: "
            f"{sorted(workflow_files())}"
        )
    if len(running) > 1:
        pytest.fail(f"more than one workflow runs pytest: {sorted(running)}")
    return next(iter(running.items()))


def top_level_block(text: str, key: str) -> str:
    """The indented body under a column-zero `key:` line.

    Indentation-scoped, not parsed. Returns "" when there is no such block,
    so a caller asserting on the result fails rather than reading an empty
    string as an answer.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.rstrip() == f"{key}:" or line.startswith(f"{key}:"):
            if not line[:1].isspace():
                start = i
                break
    if start is None:
        return ""
    body = [lines[start][len(key) + 1:]]
    for line in lines[start + 1:]:
        if not line.strip():
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
    names = []
    if isinstance(handler.type, ast.Name):
        names = [handler.type.id]
    elif isinstance(handler.type, ast.Tuple):
        names = [e.id for e in handler.type.elts if isinstance(e, ast.Name)]
    return any(n in ("ImportError", "ModuleNotFoundError") for n in names)


def optional_imports(paths: list[Path]) -> dict[str, set[str]]:
    """`{module: {filenames that can skip on it}}` for guarded imports.

    Two guard shapes are recognised, because both are idiomatic and this file
    would be worthless if it saw only the one currently in use:

      * `try: import X` / `except ImportError:` — the shape
        `test_the_fallback_stays_small.py` uses.
      * `pytest.importorskip("X")` — the shape a future module is at least as
        likely to reach for.

    Only the top-level package name is reported (`a.b.c` -> `a`), because that
    is what gets installed.
    """
    found: dict[str, set[str]] = {}

    def note(module: str, where: Path) -> None:
        found.setdefault(module.split(".")[0], set()).add(where.name)

    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Try) and any(
                _catches_import_error(h) for h in node.handlers
            ):
                for stmt in node.body:
                    if isinstance(stmt, ast.Import):
                        for alias in stmt.names:
                            note(alias.name, path)
                    elif isinstance(stmt, ast.ImportFrom) and stmt.module and not stmt.level:
                        note(stmt.module, path)
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name == "importorskip" and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        note(first.value, path)
    return found


def suite_test_files() -> list[Path]:
    return sorted(TESTS_DIR.rglob("*.py"))


def installed_by(text: str, module: str) -> bool:
    """Does this workflow text name `module` in either import or dist spelling?

    `yt_dlp` is installed as `yt-dlp`; the two spellings are the same name with
    a different separator, and nothing here needs to know which is which.
    """
    lowered = text.lower()
    return module.lower() in lowered or module.lower().replace("_", "-") in lowered


# --------------------------------------------------------------------------


class TestSomethingRunsTheSuite:
    def test_exactly_one_workflow_runs_pytest(self):
        running = suite_workflows()
        assert running, (
            "no workflow in .github/workflows runs pytest — the suite is gated "
            f"by nothing. Workflows present: {sorted(workflow_files())}"
        )
        assert len(running) == 1, (
            f"more than one workflow runs pytest: {sorted(running)}. "
            "One job, one workflow — see the day-one config in TODOS.md."
        )

    def test_it_fires_on_pull_request(self):
        name, text = the_suite_workflow()
        triggers = top_level_block(text, "on")
        assert triggers, f"{name} has no column-zero `on:` block"
        assert "pull_request" in triggers, (
            f"{name} does not run on pull_request, so a PR can be merged "
            "without the suite ever having run against it"
        )

    def test_the_release_workflow_is_not_the_one_running_it(self):
        # Not a style preference. release.yml fires on `push: tags: v*`, which
        # is after the decision to publish, and TODOS.md keeps its defects in a
        # section of their own. A suite that only runs at tag time tells you
        # the release is broken instead of stopping it.
        assert "release.yml" not in suite_workflows()


class TestTheWholeSuiteRunsInCI:
    def test_the_scan_finds_something_to_check(self):
        # Vacuity guard. Every assertion below iterates the scan's output, so
        # a scanner that silently stopped matching would turn all of them into
        # passes over an empty set. yt_dlp is the instance that exists today.
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
        # because the failure is loud but the cause is not obvious.
        _, text = the_suite_workflow()
        assert "ffmpeg" in text


class TestTheRuleItself:
    """The scanner and its escape hatch, driven against synthetic input.

    The classes above assert what the rule currently answers. These assert the
    rule can still answer — a distinction that matters because both guard
    shapes and the exemption path are otherwise exercised by exactly zero, one
    and zero real cases respectively.
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

        monkeypatch.setitem(
            CI_NEED_NOT_INSTALL,
            "faster_whisper",
            "a real model load is a multi-hundred-MB download in a network-free suite",
        )
        workflow = "steps:\n  - run: pip install pytest\n"
        missing = [
            module
            for module in found
            if module not in CI_NEED_NOT_INSTALL and not installed_by(workflow, module)
        ]
        assert missing == []

    def test_the_dist_spelling_counts_as_installed(self, tmp_path):
        assert installed_by("pip install yt-dlp\n", "yt_dlp")
        assert installed_by("pip install yt_dlp\n", "yt_dlp")
        assert not installed_by("pip install pytest\n", "yt_dlp")

    @pytest.mark.parametrize(
        "text, key, expected_in",
        [
            ("on:\n  push:\n    branches: [main]\n", "on", "push"),
            ("name: x\non:\n  pull_request:\njobs:\n  a:\n", "on", "pull_request"),
        ],
        ids=["push-block", "block-ends-at-column-zero"],
    )
    def test_the_block_reader_is_bounded_by_indentation(self, text, key, expected_in):
        block = top_level_block(text, key)
        assert expected_in in block
        assert "jobs" not in block

    def test_a_missing_block_reads_as_empty_not_as_absent_evidence(self):
        assert top_level_block("name: x\njobs:\n  a:\n", "on") == ""
