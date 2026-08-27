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
    scheduled job checking for `yt-dlp` drift — which is `drift.yml`, and now
    exists — a nightly slow-subset run, or `release.yml` hardened with its own
    pytest step are all legitimate and all run pytest. `NOT_THE_SUITE_WORKFLOW` is that escape
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

  * **It follows a `-r` out of the workflow, and only from a line that
    installs.** The install has now moved into `requirements-ci.txt`, so the
    bullet that stood here — "if the install ever moves into a requirements
    file, this test fails, and the fix is to teach it to follow, not to loosen
    it" — was taken up rather than deleted. The three bullets below are what
    the following can and cannot see. Restricting it to a line containing `pip
    install` is not tidiness: `pytest -q -r fE` carries a separated `-r`
    that means something else entirely, and reading that one as a filename
    would have this file demand a file named `fE` on a legal runner line.
    Inside a referenced file the rule inverts and every `-r` counts, because
    there that is the only thing it can mean.

    The case that decides it is pytest's own `-r`, spelled with a space:
    `python -m pytest -q -r fE` is a legal invocation whose `-r` is a report
    selector, and it is token-for-token identical to `-r <file>`. Nothing about
    the flag tells the two apart; only the line they sit on does. This
    workflow's own `-rs` is NOT that case and was named as it for one revision
    in error — an attached spelling is refused by the tokenizer, a level
    earlier, whatever this restriction is set to.

  * **Three reference spellings are followed and ONE legal one is not.**
    `-r F`, `--requirement F` and `--requirement=F` are followed. pip's
    attached `-rF` is not, and that is the real gap: it is a legal spelling
    that means "install from F", and it fails in the loud direction — an
    unfollowed reference reports its packages as uninstalled, so the suite goes
    red rather than quiet.

    `-r=F` is NOT a second gap and this bullet claimed it was for one revision.
    optparse binds the whole of `=F` as the value, so `-r=reqs.txt` asks pip
    for a file literally named `=reqs.txt`. Refusing it is CORRECT; following
    it as `reqs.txt` would be the bug. The distinction matters because "two
    legal spellings are unsupported" is an argument for adding support, and one
    of the two would have been a defect.

    A **backslash continuation** is the third thing not seen, and it is the
    likeliest of the three to be written by hand: `only_install_lines` keys on
    the LINE, and the continuation line of `pip install \\` + newline + `-r F`
    carries no `pip install`, so the reference goes unread. Loud direction
    again, but the failure names a missing package rather than an unreadable
    reference. Joining continued lines before tokenizing is a small change if
    it is ever wanted; it is not made on speculation.

    Neither is `working-directory:` nor a `cd` inside a `run:` block tracked —
    every top-level reference resolves against the repository root, which is
    where the job's checkout sits, and a nested one resolves against its
    referrer's directory, which is pip's own rule. That root is also the
    CONTAINMENT boundary: a reference resolving outside it is refused rather
    than read, because a file that is not in the checkout is not on the runner.

  * **A referenced file that cannot be read raises; it does not read as
    False.** Missing, and in a reference cycle, are one defect from here — a
    `-r` whose contents are unavailable — and both raise. Resolving them to
    "nothing installed" would produce the answer a genuinely deleted install
    gives, and the two need opposite fixes.

  * **It says nothing about cost.** No assertion here concerns `concurrency`,
    job count, or trigger breadth. This repository is public, so Actions minutes
    are free; the day-one config in the workflow is there because it is cheap
    now and awkward to retrofit, not because anything is metered.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from repo_files import REPO as REPO_ROOT, git_listed_paths

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
# publish tag is gated — along with `drift.yml`, which now exists, and a nightly
# subset, which does not. The assertions below need ONE unambiguous workflow to
# read; they do not need it to be the only one in the repository.
#
# An exemption is keyed by FILENAME, which means naming a workflow here removes
# it from every assertion in TestTheWholeSuiteRunsInCI at once. The reason
# string is prose and enforces nothing on its own — `TestTheExemptedDriftJob`
# below is what makes drift.yml's three claims checkable, and
# `test_no_exemption_outlives_the_workflow_it_names` is what stops a dead
# exemption sitting here silently after the workflow it names stops running
# pytest.
NOT_THE_SUITE_WORKFLOW: dict[str, str] = {
    "drift.yml": (
        "the scheduled unpinned-dependency check: it installs pytest and yt-dlp "
        "with no pins and no hashes so upstream movement is reported separately "
        "from a defect in this repository, and it deliberately does not run on "
        "pull_request"
    ),
}

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


# The spellings pip accepts for "install from this file instead of from names
# on the command line". The attached `-rFILE` is legal and deliberately absent
# — see the module docstring. `-r=FILE` is NOT the same case and was grouped
# with it in error: optparse binds `=FILE`, so that spelling asks for a file
# named `=FILE` and refusing it is the correct answer rather than a gap.
REQUIREMENT_FLAGS = ("-r", "--requirement")

# What makes a line an install. A `-r` anywhere else is not a reference, and
# pytest's own report flag is the case that decides it: `pytest -q -r fE` is a
# legal line whose `-r` takes a separated argument, exactly as `-r <file>` does.
# Read as a reference it would have this file demand a file named `fE`.
#
# Not the attached `-rs` this repository's own runner line uses — that one is
# refused by the tokenizer below, which follows no attached spelling at all,
# and it was named here as the deciding case in error.
INSTALL_MARKERS = ("pip install", "pip3 install")


class RequirementsReferenceError(Exception):
    """A workflow installs from a requirements file that cannot be read.

    Missing, circular, outside the checkout, and nested past
    `MAX_REFERENCE_DEPTH` are one defect from here — a `-r` whose contents are
    unavailable — and all four raise rather than resolving to "nothing
    installed". That answer is byte-identical to the one a genuinely deleted
    install produces, and the two need opposite fixes.

    All four, deliberately: this is the one exception type the module promises,
    so every unreadable reference has to arrive as it. A deep acyclic chain
    used to escape as `RecursionError` — true, loud, and outside the contract,
    which made it indistinguishable from a bug in this file rather than a
    statement about the workflow.
    """


# How deep a chain of `-r` references may nest before it is refused. Not a
# performance number and not tuned: the real bound is that every referenced
# file must be tracked, and nobody commits 32 nested requirements files. It is
# here so the recursion has a stated limit that raises the module's own
# exception, rather than an unstated one that raises Python's.
MAX_REFERENCE_DEPTH = 32


def referenced_requirements(text: str, *, only_install_lines: bool) -> list[str]:
    """Every path `text` installs from by reference, in the order it names them.

    `only_install_lines` is the whole difference between the two kinds of text
    this reads. In a WORKFLOW a `-r` is a requirements file only on a line that
    installs; in a REQUIREMENTS FILE it is the only thing a `-r` can be, so
    every line is eligible.
    """
    found: list[str] = []
    for line in without_comments(text).splitlines():
        if only_install_lines and not any(m in line for m in INSTALL_MARKERS):
            continue
        tokens = line.split()
        for i, token in enumerate(tokens):
            name = None
            if token in REQUIREMENT_FLAGS and i + 1 < len(tokens):
                name = tokens[i + 1]
            elif token.startswith("--requirement="):
                name = token[len("--requirement="):]
            if name:
                found.append(name.strip("'\""))
    return found


def _walk_requirements(path: Path, seen: tuple[Path, ...], root: Path):
    """Yield `(resolved path, comment-stripped body)` for `path` and its refs.

    A generator rather than a string because the traversal has two consumers
    that want different halves of it — `expand_requirements` wants the bodies,
    `referenced_requirement_paths` wants the paths — and a recursion written
    twice is a recursion with two places to drift.

    A nested reference resolves against the REFERRING file's directory rather
    than against `root`, which is pip's own rule. The two are only
    distinguishable when the referring file is ITSELF in a subdirectory; a
    fixture that puts the subdirectory on the referenced side instead makes
    both rules produce the same path, which is how this went a revision
    untested.

    `root` is the containment boundary, not the resolution base — see the
    check below for why the distinction matters.
    """
    resolved = path.resolve()
    # Containment first, before the file is read or even stat'd for existence.
    # `root / name` DISCARDS root when `name` is absolute — `Path("/repo") /
    # "/etc/passwd"` is `/etc/passwd` — so without this a workflow line reading
    # `pip install -r /etc/passwd` opened that file, and `-r ../../../x` walked
    # out of the checkout. Not an escalation: the only text this parses is
    # committed workflow YAML, and anyone who can edit that already runs
    # arbitrary code in the job. It is refused because a requirements file CI
    # installs from must ship with the checkout to exist on the runner at all,
    # which `test_every_referenced_requirements_file_is_tracked` already
    # demands — so containment refuses no legitimate case.
    if not resolved.is_relative_to(root.resolve()):
        raise RequirementsReferenceError(
            f"a workflow installs from {path}, which resolves to {resolved}, "
            f"outside {root}. A requirements file that is not in the checkout "
            "is not on the runner either."
        )
    if resolved in seen:
        chain = " -> ".join(p.name for p in seen + (resolved,))
        raise RequirementsReferenceError(
            f"requirements files reference each other in a cycle: {chain}"
        )
    if len(seen) >= MAX_REFERENCE_DEPTH:
        chain = " -> ".join(p.name for p in seen + (resolved,))
        raise RequirementsReferenceError(
            f"requirements references nest more than {MAX_REFERENCE_DEPTH} "
            f"deep: {chain}"
        )
    if not resolved.is_file():
        raise RequirementsReferenceError(
            f"a workflow installs from {path}, which is not a file here. A `-r` "
            "pointing at nothing installs nothing, and the job fails at install "
            "time rather than at test time — so the reference is checked as a "
            "reference rather than reported as a missing package."
        )
    body = without_comments(resolved.read_text(encoding="utf-8"))
    yield resolved, body
    for name in referenced_requirements(body, only_install_lines=False):
        yield from _walk_requirements(
            resolved.parent / name, seen + (resolved,), root
        )


def expand_requirements(text: str, root: Path = REPO_ROOT) -> str:
    """`text` with the contents of every file it installs from appended.

    Appended rather than substituted in place: nothing downstream cares WHERE a
    name appears, only whether the workflow causes it to be installed, and
    appending leaves the workflow's own line structure intact for the reader
    that does care — `top_level_block` walks it by indentation.
    """
    parts = [text]
    for name in referenced_requirements(text, only_install_lines=True):
        parts.extend(body for _, body in _walk_requirements(root / name, (), root))
    return "\n".join(parts)


def referenced_requirement_paths(text: str, root: Path = REPO_ROOT) -> list[Path]:
    """Every requirements file `text` installs from, transitively, resolved.

    The transitive part is the point. `referenced_requirements` answers what a
    single text NAMES, which for the tracked-file check is the top level only —
    and a nested reference is exactly as absent from a clean checkout as a
    top-level one if nobody tracked it.
    """
    found: list[Path] = []
    for name in referenced_requirements(text, only_install_lines=True):
        found.extend(p for p, _ in _walk_requirements(root / name, (), root))
    return found


def workflow_claims(text: str, root: Path = REPO_ROOT) -> str:
    """`text` with everything that cannot install anything removed.

    Comments, and `name:` values — the workflow's display label and each step's.
    Both are prose, and both named `yt-dlp` in the workflow this rule was
    written against, which was enough to keep every assertion here green after
    the install line was deleted.

    Then the opposite operation: every requirements file the text installs from
    is read and appended. The two pull against each other on purpose — prose
    that names a package is not an install, and an install that names no
    package at all still is one.
    """
    kept = [
        line
        for line in without_comments(expand_requirements(text, root)).splitlines()
        if not line.lstrip().lstrip("-").lstrip().startswith("name:")
    ]
    return "\n".join(kept)


def matrix_python_versions(text: str) -> list[str]:
    """Every interpreter version a `python-version:` matrix line lists.

    Regex rather than a YAML parse, for the reason the whole file is regex
    rather than a YAML parse: this suite installs no YAML library, and the one
    shape being read here — a flow sequence on one line — is the shape the
    workflow uses. A block sequence would read as no versions at all, which
    fails loud in the test that consumes this rather than quietly returning a
    subset.
    """
    match = re.search(r"python-version:\s*\[([^\]]*)\]", without_comments(text))
    if not match:
        return []
    return [v.strip().strip("'\"") for v in match.group(1).split(",") if v.strip()]


def documented_ci_versions(text: str) -> list[str] | None:
    """The versions a doc's "CI runs …" sentence names, or None if it has none.

    The slice runs from `CI runs` to the first `;` or newline. That boundary is
    doing real work in README.md, where the sentence continues "; 3.11 and 3.12
    are not tested" — versions that are named precisely because CI does NOT run
    them, and reading them as a claim would invert the sentence.
    """
    start = text.find("CI runs")
    if start == -1:
        return None
    rest = text[start + len("CI runs"):]
    end = min(
        (i for i in (rest.find(";"), rest.find("\n")) if i != -1),
        default=len(rest),
    )
    return re.findall(r"\b\d+\.\d+\b", rest[:end])


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
    the test because it has to be callable against synthetic input to be tested
    at all: `CI_NEED_NOT_INSTALL` is still empty, and `NOT_THE_SUITE_WORKFLOW`
    holds exactly one entry, so neither dict can drive the blank-reason branch
    on its own.
    """
    return sorted(key for key, why in exemptions.items() if not why.strip())


def suite_test_files() -> list[Path]:
    # A working-tree walk, deliberately — see the NON-GOAL. `repo_files
    # .tracked_text_files()` is the repo's helper for "what does this repository
    # CLAIM", and it skips when git cannot answer. A skip in the load-bearing
    # check of a file about silent skips is the wrong trade.
    return sorted(TESTS_DIR.rglob("*.py"))


def installed_by(text: str, module: str, root: Path = REPO_ROOT) -> bool:
    """Does this workflow text name `module` in either import or dist spelling?

    `yt_dlp` is installed as `yt-dlp`; the two spellings are the same name with
    a different separator, and nothing here needs to know which is which.

    Matched against `workflow_claims`, not raw text: the workflow names
    `yt-dlp` four times in comments and once in a step label, so against raw
    text this returned True with the install line deleted. `workflow_claims`
    also expands `-r`, which is why the workflow no longer needs to name the
    package at all — `requirements-ci.txt` does.

    `root` is where a top-level reference resolves, and it is a parameter so the
    tests below can drive the follower against a tmp_path rather than against
    the repository's own file.
    """
    lowered = workflow_claims(text, root).lower()
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

    def test_every_requirements_reference_resolves(self):
        # The loud half of following a reference. `installed_by` raises rather
        # than returning False when a `-r` points at nothing, and this is where
        # that becomes a named failure instead of an error surfacing inside
        # whichever assertion happened to read the workflow first.
        for name, text in workflow_files().items():
            try:
                expand_requirements(text)
            except RequirementsReferenceError as exc:
                pytest.fail(f"{name}: {exc}")

    def test_every_referenced_requirements_file_is_tracked(self):
        # Present on disk is not the same claim as ships with the branch. A
        # gitignored requirements file reads perfectly here — this file walks
        # the working tree — and is absent from a clean checkout, where the job
        # fails at install time. Skips rather than guesses when git cannot
        # answer, for the reason `repo_files` documents.
        #
        # TRANSITIVELY. This walked only top-level references for one revision,
        # which left the one shape the rule is actually for — a nested `-r`
        # pointing at a file nobody added — passing a check written to catch
        # exactly that. A nested file is as absent from a clean checkout as a
        # top-level one.
        listed = git_listed_paths()
        if listed is None:
            pytest.skip("git cannot list this checkout")
        checked = 0
        for name, text in workflow_files().items():
            for path in referenced_requirement_paths(text):
                rel = path.relative_to(REPO_ROOT).as_posix()
                checked += 1
                assert rel in listed, (
                    f"{name} installs from {rel}, which git does not track, so "
                    "it would be absent from a clean checkout"
                )
        # The guard `test_the_scan_finds_something_to_check` gives the import
        # scan, for the same reason: a loop over an empty list is green and
        # says nothing, and this rule's whole subject is a file being absent.
        assert checked, (
            "no workflow installs from a requirements file, so the loop above "
            "iterated over nothing and passed on an empty set"
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

    def test_the_gate_installs_a_pinned_set(self):
        # The split this repository now runs on. The workflow that GATES a pull
        # request installs from a hash-pinned file, so a red run there is this
        # repository's doing; drift.yml installs the same packages unpinned on a
        # schedule, so upstream movement is still reported. Without the flag the
        # split collapses in the quiet direction: pip reads a requirements file
        # full of hashes and enforces none of them.
        #
        # NON-GOAL: this does not forbid going back to `pip install pytest
        # yt-dlp`. It makes doing so a red test rather than a silent change,
        # which is the whole difference between a decision and a drift.
        #
        # Read the way every other claim in this class is read, and for the
        # reason `workflow_claims` exists. `"--require-hashes" in
        # without_comments(text)` was satisfied by a step `name:` — renaming the
        # step to "Install test dependencies (--require-hashes)" and deleting
        # the flag from the command left this green. Worse than the ffmpeg case
        # it repeated, because a label cannot install ffmpeg but it also cannot
        # enforce a hash, and here the thing being asserted IS the enforcement.
        #
        # And on an INSTALL line specifically. `workflow_claims` alone still
        # accepts the flag anywhere in the file — including inside the
        # requirements file it appends, where pip never reads it as an argument.
        name, text = the_suite_workflow()
        installs = [
            line
            for line in workflow_claims(text).splitlines()
            if any(m in line for m in INSTALL_MARKERS)
        ]
        assert installs, f"{name} has no pip install line at all"
        assert any("--require-hashes" in line for line in installs), (
            f"{name} does not pass --require-hashes on any install line. pip "
            "accepts a requirements file with hashes and ignores them without "
            "it, so the pins would be documentation rather than enforcement. "
            f"Install lines found: {installs}"
        )

    @pytest.mark.parametrize("doc", ["README.md", "AGENTS.md"], ids=["readme", "agents"])
    def test_the_documented_interpreters_are_the_ones_the_matrix_runs(self, doc):
        # Both files state which interpreters CI runs, in prose, and until this
        # test nothing read either. The workflow comment goes further and
        # asserts a result per rung — "993 passed on 3.10.12 and 993 on CPython
        # 3.13.13" — so dropping a rung leaves three separate places claiming a
        # run that no longer happens.
        #
        # Asserted in BOTH directions. A version in the matrix and not in the
        # sentence is a doc that undersells its own coverage; a version in the
        # sentence and not in the matrix is a doc that claims a run nobody makes,
        # which is the one that costs somebody an afternoon.
        #
        # NON-GOALS. It reads a sentence-shaped SLICE — from "CI runs" to the
        # first `;` or newline — not prose. A claim written differently, or
        # spread across a line break before the semicolon, reads as fewer
        # versions and fails here rather than passing quietly; that is the
        # intended direction, but it does mean this test constrains the phrasing
        # and not only the facts. It also says nothing about whether either rung
        # PASSED, or ever ran — a matrix entry is a request, and GitHub is where
        # the answer lives.
        _, workflow = the_suite_workflow()
        matrix = matrix_python_versions(workflow)
        assert len(matrix) >= 2, (
            f"the suite workflow runs on {matrix or 'no declared version'}. The "
            "second rung is the only thing in this repository that has ever run "
            "the suite on an interpreter other than the floor — if it is going "
            "deliberately, the sentences in README.md and AGENTS.md go with it."
        )
        documented = documented_ci_versions((REPO_ROOT / doc).read_text(encoding="utf-8"))
        assert documented is not None, (
            f"{doc} no longer says which interpreters CI runs. The matrix is "
            f"{matrix}; a reader has nowhere to find that out."
        )
        assert sorted(documented) == sorted(matrix), (
            f"{doc} says CI runs {documented}; the matrix runs {matrix}. "
            "Whichever is right, the other is telling somebody a version is "
            "covered when it is not, or hiding one that is."
        )

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


class TestTheExemptedDriftJob:
    """The three claims `NOT_THE_SUITE_WORKFLOW["drift.yml"]` makes, enforced.

    An exemption is keyed by FILENAME. Naming a workflow there is not "this one
    is checked differently" — it is "this one is checked by nothing", because
    every assertion in `TestTheWholeSuiteRunsInCI` reads `the_suite_workflow()`
    and that function filters the dict. So the reason string went from being a
    note beside a check to being the whole of it, and prose enforces nothing.

    What the exemption's own text claims, and what is asserted below:

      * it does not run on `pull_request` — the property that makes exempting
        it correct rather than a hole. A drift job wired into a merge decision
        hands the merge button to whoever publishes upstream next.
      * it installs what the suite guards an import on — the property that
        makes it worth running. Without it the job is green for the reason the
        gate exists to prevent: pytest exits 0 on a skip.
      * it is the UNPINNED half — the property that makes it a different
        question from tests.yml rather than a second copy of the same one.

    NON-GOALS, deliberately:

      * **It does not check that the schedule ever fires, or that anybody
        reads it.** A `cron:` in a file is not a run, GitHub disables scheduled
        workflows on inactive repositories without asking, and nothing here can
        see either. The workflow's own NON-GOALS say the notification story is
        the Actions tab and GitHub's scheduled-run email; this file cannot
        verify that story and does not try.
      * **It does not generalise to a second exemption.** Every assertion below
        reads `drift.yml` by name, because the three properties are that
        workflow's, not a category's — a hardened `release.yml` would be
        exempt for entirely different reasons and would need its own class. The
        one thing that IS generic is
        `test_no_exemption_outlives_the_workflow_it_names`.
      * **It cannot tell an unpinned install from an unpinnABLE one.** The apt
        step here installs `ffmpeg` with no version, exactly as tests.yml does,
        and this class reads only the pip lines. The pinning split this
        repository runs on is a split about PyPI; TODOS.md carries the rest.
    """

    def the_drift_workflow(self) -> str:
        text = workflow_files().get("drift.yml")
        if text is None:
            pytest.fail(
                "drift.yml is named in NOT_THE_SUITE_WORKFLOW but is not in "
                ".github/workflows — the exemption outlived the workflow"
            )
        return text

    def test_it_does_not_gate_a_pull_request(self):
        # The load-bearing one. `pull_request_target` is listed alongside
        # `pull_request` because it is the more dangerous spelling of the same
        # mistake — it runs with the base repository's token — and a rule that
        # named only the safer one would be an invitation.
        triggers = top_level_block(self.the_drift_workflow(), "on")
        assert triggers, "drift.yml has no `on:` block"
        for gate in ("pull_request", "push"):
            assert gate not in triggers, (
                f"drift.yml triggers on {gate}. It installs UNPINNED "
                "dependencies, so a red run is news about PyPI rather than a "
                "verdict on a diff — gating on it hands the merge button to "
                "whoever publishes upstream next."
            )

    def test_it_installs_what_the_suite_guards_an_import_on(self):
        # The same rule `test_every_module_the_suite_can_skip_on_is_installed`
        # applies to the gate, applied here to the job the exemption removed
        # from it. A drift job that does not install yt-dlp skips the 24 tests
        # that drive it and reports green — which is the exact failure the whole
        # file exists to stop, arriving through the exemption door.
        text = self.the_drift_workflow()
        missing = sorted(
            module
            for module in optional_imports(suite_test_files())
            if module not in CI_NEED_NOT_INSTALL and not installed_by(text, module)
        )
        assert not missing, (
            f"drift.yml does not install {missing}, which the suite guards an "
            "import on. Those tests would skip, the run would be green, and the "
            "drift job would be reporting on dependencies it never exercised."
        )

    def test_it_is_the_unpinned_half(self):
        # What makes it a different question from tests.yml rather than a
        # second copy of it. Both halves asserted: no hash enforcement, and no
        # reference to the pinned file — either one alone would let the job
        # quietly become a duplicate of the gate.
        claims = workflow_claims(self.the_drift_workflow())
        assert "--require-hashes" not in claims, (
            "drift.yml passes --require-hashes, so it now asks the same "
            "question tests.yml already answers. The point of this job is to "
            "resolve whatever PyPI publishes today."
        )
        refs = referenced_requirements(
            self.the_drift_workflow(), only_install_lines=True
        )
        assert not refs, (
            f"drift.yml installs from {refs}. A drift job reading the pinned "
            "file is a second run of the gate on a schedule."
        )

    def test_no_exemption_outlives_the_workflow_it_names(self):
        # The generic half, and the one that survives a second exemption being
        # added. A key here suppresses every assertion in
        # `TestTheWholeSuiteRunsInCI` for that filename; if the workflow is
        # deleted, or stops running pytest, the entry keeps suppressing nothing
        # in particular and reads as deliberate coverage that no longer exists.
        present = workflow_files()
        for name in NOT_THE_SUITE_WORKFLOW:
            assert name in present, (
                f"NOT_THE_SUITE_WORKFLOW names {name}, which is not in "
                ".github/workflows. Delete the entry, or restore the workflow."
            )
            assert pytest_invocations(present[name]), (
                f"NOT_THE_SUITE_WORKFLOW names {name}, but it no longer runs "
                "pytest — so the exemption excludes it from a filter that would "
                "not have selected it anyway. Delete the entry."
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
        # Both dicts document that the reason is required. It was free to add
        # while both were empty, and `NOT_THE_SUITE_WORKFLOW` has since gained
        # a real entry — `drift.yml` — which is the first one it actually
        # judges rather than passes over.
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
                "        # nothing on this step names the package\n"
                "        run: pip install --require-hashes -r requirements-ci.txt\n",
                "yt_dlp",
                True,
            ),
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
            "a-reference-is-followed-out-of-the-workflow",
            "a-comment-alone-is-not-an-install",
            "a-step-label-alone-is-not-an-install",
            "the-same-shape-for-ffmpeg",
        ],
    )
    def test_only_text_that_could_install_counts(self, text, module, expected):
        # The first three are configurations this must NOT fire on; the rest
        # are what it exists to catch. Stripping comments naively — cutting at
        # the first `#` — would break the URL fragment, which is why the strip
        # follows YAML's actual rule: a `#` at line start or after whitespace.
        # The third is the repository's OWN requirements file rather than a
        # synthetic one, so this case also pins that the file CI installs from
        # still carries yt-dlp; the synthetic cases below own the mechanism.
        assert installed_by(text, module) is expected

    def test_a_reference_is_followed_out_of_the_workflow(self, tmp_path):
        (tmp_path / "reqs.txt").write_text("yt-dlp==1.2.3\n", encoding="utf-8")
        text = "      - run: pip install -r reqs.txt\n"
        assert installed_by(text, "yt_dlp", tmp_path)
        # The negative half, in the same test and against the same file: an
        # expansion hardwired to True passes the line above identically.
        assert not installed_by(text, "faster_whisper", tmp_path)

    @pytest.mark.parametrize(
        "flag",
        ["-r reqs.txt", "--requirement reqs.txt", "--requirement=reqs.txt"],
        ids=["short", "long", "long-with-equals"],
    )
    def test_every_supported_spelling_is_followed(self, flag, tmp_path):
        (tmp_path / "reqs.txt").write_text("yt-dlp==1.2.3\n", encoding="utf-8")
        assert installed_by(f"      - run: pip install {flag}\n", "yt_dlp", tmp_path)

    def test_the_attached_spelling_is_not_followed(self, tmp_path):
        # The one real blind spot, driven rather than assumed. `-rreqs.txt` is
        # legal pip and is not seen, which reports yt_dlp as uninstalled and
        # turns the suite red — the loud direction. If it is ever wanted, this
        # is the test that has to change, which is the point of writing it down.
        (tmp_path / "reqs.txt").write_text("yt-dlp==1.2.3\n", encoding="utf-8")
        assert not installed_by(
            "      - run: pip install -rreqs.txt\n", "yt_dlp", tmp_path
        )

    def test_a_short_flag_with_an_equals_is_not_a_missed_reference(self, tmp_path):
        # NOT a blind spot, and it sat beside the test above under an id reading
        # `short-with-equals` and a shared comment reading "Both are legal pip"
        # for a revision. That framing made it an argument for adding support,
        # and adding support would have been the defect.
        #
        # This file's answer is the same as for the attached spelling — one
        # token, matching neither flag, so nothing is named. The DIFFERENCE is
        # on pip's side, so it is proved on pip's side rather than asserted in
        # prose: optparse is what pip's CLI parser is built on, it is stdlib,
        # and it binds the whole of `=reqs.txt`. So `pip install -r=reqs.txt`
        # asks for a file literally named `=reqs.txt` — not for `reqs.txt` —
        # and a walker that followed this spelling to `reqs.txt` would be
        # reading a file pip never opens.
        #
        # Imported here rather than at module scope: nothing else in this file
        # needs it, and its presence at the top would read as a dependency of
        # the scanner rather than of one test's evidence.
        import optparse

        parser = optparse.OptionParser()
        parser.add_option("-r", "--requirement", action="append", dest="req",
                          default=[])
        assert parser.parse_args(["-r=reqs.txt"])[0].req == ["=reqs.txt"]
        # And the contrast, from the same parser, so "legal spelling" is a
        # measured claim about `-rF` rather than a shared one about both.
        assert parser.parse_args(["-rreqs.txt"])[0].req[-1:] == ["reqs.txt"]

        line = "      - run: pip install -r=reqs.txt\n"
        assert referenced_requirements(line, only_install_lines=True) == []
        (tmp_path / "reqs.txt").write_text("yt-dlp==1.2.3\n", encoding="utf-8")
        assert not installed_by(line, "yt_dlp", tmp_path)

    def test_a_backslash_continuation_hides_a_reference(self, tmp_path):
        # The third thing not seen, and the likeliest of the three to be typed
        # by hand. `only_install_lines` keys on the LINE, and the continuation
        # line carries no `pip install`, so its `-r` goes unread.
        #
        # Pinned as the CURRENT behaviour, not endorsed as the right one. It
        # fails loud — yt_dlp reads as uninstalled and the suite goes red — but
        # the failure names a missing package rather than an unreadable
        # reference, which is the wrong sentence for the reader. Joining
        # continued lines before tokenizing is the fix if it is ever wanted;
        # this test is what turns making it into a deliberate edit.
        (tmp_path / "reqs.txt").write_text("yt-dlp==1.2.3\n", encoding="utf-8")
        wrapped = "      - run: |\n          pip install \\\n            -r reqs.txt\n"
        assert referenced_requirements(wrapped, only_install_lines=True) == []
        assert not installed_by(wrapped, "yt_dlp", tmp_path)
        # The negative half: the same reference on ONE line is followed, so the
        # assertion above is about the continuation and not about the fixture.
        joined = "      - run: pip install -r reqs.txt\n"
        assert installed_by(joined, "yt_dlp", tmp_path)

    @pytest.mark.parametrize(
        "spelling",
        ["-r 'reqs.txt'", '-r "reqs.txt"', "--requirement='reqs.txt'"],
        ids=["single", "double", "long-equals-single"],
    )
    def test_a_quoted_path_is_unquoted(self, spelling, tmp_path):
        # Shell quoting is ordinary in a `run:` block and reaches pip stripped.
        # A tokenizer that kept the quotes would look for a file whose name
        # begins with an apostrophe and raise — loud, but for a reason that has
        # nothing to do with the workflow.
        (tmp_path / "reqs.txt").write_text("yt-dlp==1.2.3\n", encoding="utf-8")
        assert installed_by(
            f"      - run: pip install {spelling}\n", "yt_dlp", tmp_path
        )

    def test_pip3_is_an_install_marker(self, tmp_path):
        # `pip3 install` is the same command under the name Debian images
        # usually carry. Both markers are in INSTALL_MARKERS and only one was
        # ever driven.
        (tmp_path / "reqs.txt").write_text("yt-dlp==1.2.3\n", encoding="utf-8")
        assert installed_by("      - run: pip3 install -r reqs.txt\n", "yt_dlp", tmp_path)

    def test_a_trailing_dash_r_names_nothing(self):
        # `-r` as the last token has no argument to bind. The tokenizer's
        # `i + 1 < len(tokens)` guard is what stops it reading past the end;
        # without it this raises IndexError, which is a bug in this file
        # wearing the costume of a finding about the workflow.
        assert referenced_requirements(
            "      - run: pip install -r\n", only_install_lines=True
        ) == []

    def test_a_dash_r_outside_an_install_is_not_a_reference(self):
        # The collision that decides the `pip install` restriction: pytest's own
        # `-r` reporting flag, SEPARATED from its argument. `pytest -q -r fE` is
        # a legal line, and its `-r fE` is token-for-token what `-r reqs.txt`
        # is; only the line they sit on tells them apart.
        #
        # Driven with the separated spelling because the attached one does not
        # test this rule. An earlier revision of this test used `-rs`, which is
        # one token the tokenizer never reads as a flag — so the test passed
        # with the restriction removed, naming a rule it did not exercise. Found
        # by mutating `only_install_lines=True` to `False` and watching it stay
        # green.
        report = "      - run: python -m pytest -q -r fE\n"
        assert referenced_requirements(report, only_install_lines=True) == []
        assert expand_requirements(report) == report
        # Without the restriction the same line names `fE`, which is the failure
        # the restriction exists to prevent. Asserted here so the two halves sit
        # together rather than one being inferred from the other's absence.
        assert referenced_requirements(report, only_install_lines=False) == ["fE"]

    def test_an_attached_dash_r_is_refused_by_the_tokenizer_not_the_line(self):
        # The `-rs` this repository's own runner line carries. It is not a
        # reference either, but for a different reason: no attached spelling is
        # followed anywhere, so it is refused with the restriction lifted too.
        # Separated from the test above because conflating the two is exactly
        # the error that made that one vacuous.
        runner = "      - run: python -m pytest -q -rs\n"
        assert referenced_requirements(runner, only_install_lines=True) == []
        assert referenced_requirements(runner, only_install_lines=False) == []
        assert expand_requirements(runner) == runner

    def test_a_name_in_a_requirements_comment_is_not_an_install(self, tmp_path):
        # The workflow's own comments are stripped for this reason; a file one
        # reference further out gets the same treatment, by the same function.
        (tmp_path / "reqs.txt").write_text(
            "# yt-dlp used to be pinned here\npytest==9.1.1\n", encoding="utf-8"
        )
        text = "      - run: pip install -r reqs.txt\n"
        assert installed_by(text, "pytest", tmp_path)
        assert not installed_by(text, "yt_dlp", tmp_path)

    def test_a_nested_reference_resolves_against_its_referrer(self, tmp_path):
        # pip resolves a nested `-r` against the REFERRING file's directory. The
        # subdirectory has to be on the referring side for that to be
        # observable: an earlier revision put `sub/` on the REFERENCED side —
        # `a.txt` at the root saying `-r sub/b.txt` — where referrer-resolution
        # and root-resolution both produce `tmp_path/sub/b.txt`. It passed
        # against either rule, so it named one without testing it.
        #
        # Here `sub/a.txt` says `-r b.txt` with no directory at all. Referrer
        # resolution finds `sub/b.txt`; root resolution looks for `b.txt` at the
        # root and raises. Only one of the two can pass.
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.txt").write_text("-r b.txt\n", encoding="utf-8")
        (tmp_path / "sub" / "b.txt").write_text("yt-dlp==1.2.3\n", encoding="utf-8")
        assert installed_by(
            "      - run: pip install -r sub/a.txt\n", "yt_dlp", tmp_path
        )

    def test_a_nested_reference_is_not_resolved_against_the_root(self, tmp_path):
        # The mirror. Same shape, with the referenced file sitting at the ROOT
        # instead of beside its referrer — which is where a root-resolving
        # walker would look, and where pip does not. It must raise rather than
        # quietly install from the wrong file.
        #
        # Written as a separate test rather than a second assertion above so a
        # failure names which direction broke. The positive going red means the
        # walk stopped following; this going red means it started resolving
        # against the wrong base, and those are different bugs.
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.txt").write_text("-r b.txt\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("yt-dlp==1.2.3\n", encoding="utf-8")
        with pytest.raises(RequirementsReferenceError) as caught:
            installed_by("      - run: pip install -r sub/a.txt\n", "yt_dlp", tmp_path)
        assert "b.txt" in str(caught.value)

    @pytest.mark.parametrize(
        "spelling",
        ["/etc/passwd", "../outside.txt", "sub/../../outside.txt"],
        ids=["absolute", "dot-dot", "dot-dot-through-a-subdirectory"],
    )
    def test_a_reference_outside_the_checkout_is_refused(self, spelling, tmp_path):
        # `root / name` DISCARDS root when `name` is absolute — `Path("/repo") /
        # "/etc/passwd"` is `/etc/passwd`, not `/repo/etc/passwd` — so before
        # the containment check this read whatever the line named. The dot-dot
        # cases are the same escape by a different spelling, and the third goes
        # out THROUGH a subdirectory, which a check written as "does not start
        # with ..\" would miss and a resolve-then-compare does not.
        #
        # Not an escalation, and the fixture says so: the only text this parses
        # is committed workflow YAML, and whoever can edit that already runs
        # arbitrary code in the job. It is refused because a requirements file
        # CI installs from has to ship with the checkout to exist on the runner
        # at all — so containment refuses no legitimate case.
        root = tmp_path / "repo"
        root.mkdir()
        (root / "sub").mkdir()
        (tmp_path / "outside.txt").write_text("yt-dlp==1.2.3\n", encoding="utf-8")
        with pytest.raises(RequirementsReferenceError) as caught:
            installed_by(f"      - run: pip install -r {spelling}\n", "yt_dlp", root)
        assert "outside" in str(caught.value)

    def test_a_reference_inside_a_subdirectory_is_not_refused(self, tmp_path):
        # The negative half of containment, in its own test because a guard
        # that refuses everything passes every test above. `sub/reqs.txt` is
        # inside the checkout and ordinary.
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "reqs.txt").write_text("yt-dlp==1.2.3\n", encoding="utf-8")
        assert installed_by(
            "      - run: pip install -r sub/reqs.txt\n", "yt_dlp", tmp_path
        )

    def test_a_chain_deeper_than_the_cap_raises_this_module_s_error(self, tmp_path):
        # A long ACYCLIC chain is not a cycle, so the cycle guard never fires on
        # it, and before the cap it exhausted the interpreter's stack and left
        # as `RecursionError` — true, loud, and outside the one exception type
        # this module promises, which made it read as a bug in this file rather
        # than a statement about the workflow.
        depth = MAX_REFERENCE_DEPTH + 5
        for i in range(depth):
            nxt = f"-r r{i + 1}.txt\n" if i + 1 < depth else "yt-dlp==1.2.3\n"
            (tmp_path / f"r{i}.txt").write_text(nxt, encoding="utf-8")
        with pytest.raises(RequirementsReferenceError) as caught:
            installed_by("      - run: pip install -r r0.txt\n", "yt_dlp", tmp_path)
        assert str(MAX_REFERENCE_DEPTH) in str(caught.value)

    def test_a_chain_at_the_cap_is_followed(self, tmp_path):
        # The boundary, from the legal side. A cap that fired one file early
        # would pass the test above and refuse a legitimate chain, and the two
        # are indistinguishable without this.
        depth = MAX_REFERENCE_DEPTH
        for i in range(depth):
            nxt = f"-r r{i + 1}.txt\n" if i + 1 < depth else "yt-dlp==1.2.3\n"
            (tmp_path / f"r{i}.txt").write_text(nxt, encoding="utf-8")
        assert installed_by("      - run: pip install -r r0.txt\n", "yt_dlp", tmp_path)

    def test_the_path_walker_is_transitive(self, tmp_path):
        # What `test_every_referenced_requirements_file_is_tracked` needs and
        # cannot check for itself: this repository's one requirements file
        # references nothing, so a walker that stopped after the top level
        # returns a byte-identical answer on the real tree. Only a synthetic
        # chain separates them.
        (tmp_path / "a.txt").write_text("-r b.txt\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("-r c.txt\n", encoding="utf-8")
        (tmp_path / "c.txt").write_text("yt-dlp==1.2.3\n", encoding="utf-8")
        found = referenced_requirement_paths(
            "      - run: pip install -r a.txt\n", tmp_path
        )
        assert [p.name for p in found] == ["a.txt", "b.txt", "c.txt"]

    def test_a_missing_requirements_file_is_loud(self, tmp_path):
        with pytest.raises(RequirementsReferenceError) as caught:
            installed_by("      - run: pip install -r nope.txt\n", "yt_dlp", tmp_path)
        assert "nope.txt" in str(caught.value)

    def test_a_cycle_between_requirements_files_is_loud(self, tmp_path):
        (tmp_path / "a.txt").write_text("-r b.txt\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("-r a.txt\n", encoding="utf-8")
        with pytest.raises(RequirementsReferenceError) as caught:
            installed_by("      - run: pip install -r a.txt\n", "yt_dlp", tmp_path)
        assert "cycle" in str(caught.value)

    def test_one_file_referenced_twice_is_not_a_cycle(self, tmp_path):
        # The cycle guard is per-chain, not per-run. A repository that installs
        # the same file from two steps is ordinary, and a guard keyed on "seen
        # anywhere" would call it circular.
        (tmp_path / "reqs.txt").write_text("yt-dlp==1.2.3\n", encoding="utf-8")
        assert installed_by(
            "      - run: pip install -r reqs.txt\n"
            "      - run: pip install -r reqs.txt\n",
            "yt_dlp",
            tmp_path,
        )

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
        # `nightly.yml` rather than `drift.yml`: this drove the mechanism with a
        # name that was synthetic when it was written and is now a real entry in
        # `NOT_THE_SUITE_WORKFLOW`, which would make the first assertion below
        # pass over a one-element dict and stop testing the exclusion at all.
        (tmp_path / "tests.yml").write_text(
            "on:\n  pull_request:\njobs:\n  suite:\n"
            "    steps:\n      - run: python -m pytest -q\n",
            encoding="utf-8",
        )
        (tmp_path / "nightly.yml").write_text(
            "on:\n  schedule:\n    - cron: '0 3 * * *'\njobs:\n  nightly:\n"
            "    steps:\n      - run: python -m pytest -q --slow\n",
            encoding="utf-8",
        )
        monkeypatch.setitem(globals(), "WORKFLOW_DIR", tmp_path)
        assert sorted(suite_workflows()) == ["nightly.yml", "tests.yml"]

        monkeypatch.setitem(
            NOT_THE_SUITE_WORKFLOW,
            "nightly.yml",
            "the slow-subset run, not the pull-request gate",
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
