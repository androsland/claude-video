"""What `.github/workflows/release.yml` must be true of before a tag is pushed.

A tag is the one action in this repository that publishes something to people
who are not in the room. `release.yml` is what a tag executes, it holds
`contents: write`, and as of 2026-08-26 it had never run — every defect below
was visible in 31 lines that nobody had ever seen behave. This file is the
check that runs instead of the workflow.

**Claim 1 — the tag cannot contradict what the tag ships.** The suite already
pins `skills/moviola/SKILL.md`, `.claude-plugin/plugin.json` and
`.codex-plugin/plugin.json` to each other, so a version mismatch cannot exist
*inside* the repository. The tag is outside it, which
`test_the_docs_are_checked.py` states as an explicit NON-GOAL: "It does NOT
check the version against a git tag or a published release." The workflow is
the only place that can close that, so the workflow must carry the check, in
one step, that step must run unconditionally and first, and it must be able to
fail.

**Claim 2 — a release cannot be published by code nobody chose.** Every
`uses:` in every workflow resolves to a 40-character commit SHA. A floating
major tag under `contents: write` means whatever that tag resolves to at run
time can write releases and tags here. The checkout also has to drop the
token it is handed, because a repo-controlled build script runs after it.

**Claim 3 — a mis-spelled tag fails loudly rather than doing nothing.** `0.1.0`
is on origin beside four `v`-prefixed siblings, and under a `v*`-only trigger
it produced NO WORKFLOW RUN: upstream's `release.yml` has four runs on record
(v0.1.1, v0.1.2, v0.1.3, v0.2.0) and none for `0.1.0`. A release for it exists
anyway, published by hand fourteen minutes before the first run ever fired —
so the silence was real and somebody paid for it manually. Matching the
bare-numeric shape turns that silence into a red run, provided the guard then
rejects the shape it cannot publish.

**Claim 4 — the release notes describe this repository.** `moviola` is a fork
and carries `bradautomates/claude-video`'s history. `generate_release_notes`
builds from commits since the last release; there is no previous release here,
so the first run would describe upstream's commits and ignore the hand-written
`CHANGELOG.md` entry that exists precisely to say what changed.

**Claim 5 — a pre-release does not take over `/releases/latest`.** That URL is
what `README.md` sends people to. `prerelease:` hardcoded to `false` means a
`v0.3.0-rc1` tag publishes as a full release and becomes the download everyone
gets.

**Claim 6 — two tags pushed together do not race, and none of them is
dropped.** No `concurrency:` group meant two jobs creating releases at once.
`cancel-in-progress: false` alone is not the fix: GitHub keeps at most ONE
pending run per group, so a third tag cancels the one already waiting.

NON-GOALS — what this file cannot see, and what it must not fire on:

  * **It cannot run the workflow.** Every assertion here reads text. A `run:`
    whose shell is subtly wrong, an action whose inputs are misspelled and
    silently ignored, a YAML file that will not even parse — all of these pass
    every check in this file. The only thing that proves `release.yml` works
    is a tag, and the first tag is the first run. This file lowers the cost of
    getting that wrong; it does not remove it.

  * **The `if:` and `continue-on-error:` checks cover ONE step.** They are
    scoped to the tag/version guard, because that is the step whose silent
    skip publishes a wrong release. Every other step in the workflow can still
    be disabled by a false `if:` with nothing here going red, and a step
    written WITHOUT a `name:` is invisible to the split that finds them at all
    — legal YAML, folded into whichever named step precedes it.

  * **It cannot tell whether a pinned SHA is the version its comment claims.**
    `uses: actions/checkout@11d5960a...  # v4.4.0` and the same SHA commented
    `# v99.0.0` are identical to every check here. Verifying that would mean a
    network call to GitHub's API from a test, which this suite does not do —
    no test in this repository touches the network. The comment is a
    convenience for a human reading a diff, and a lie in it is undetectable
    here by construction.

  * **A pin is a FREEZE, and this file cannot tell a fresh pin from a rotting
    one.** Nothing here asserts that a pinned SHA is current, so a pin carrying
    a known advisory looks exactly like one cut this morning. Pinning trades an
    unreviewed upgrade for an unreviewed non-upgrade; that is the right trade
    under `contents: write` and it is still a trade. Bumping them is a filed
    `TODOS.md` entry, not something this file can enforce.

  * **It says nothing about whether the release actually publishes**, whether
    `dist/moviola.skill` is correct, whether the asset uploads, or whether
    anyone can install what came out. `build-skill.sh`'s refusals OTHER THAN the
    200-file cap are covered by `test_the_bundle_refuses_an_incomplete_tree.py` —
    the cap is left to inspection, as that file's own NON-GOALS say — and the file
    list of what it produces by `TestThePublishedBundleShipsWhatGitattributesClaims`,
    which re-implements the `git archive` call rather than running the script, so a
    change to the script's own archive invocation is invisible to it. Neither says
    the bundle is CORRECT, and the boundary here is the workflow file either way.
    This sentence read "`build-skill.sh` has its own coverage" while the script had
    none at all — a deferral to a test that did not exist. It then read "refusals
    are covered" while one of the four was not, which is the same shape one notch
    smaller.

  * **It must NOT fire on a legitimate `uses:` that has no SHA to pin.** A
    local action (`uses: ./.github/actions/x`) and a container action
    (`uses: docker://image:tag`) are both correct and neither takes a commit
    SHA. Both are exempt structurally rather than by listing, because a
    structural exemption cannot rot. A registry action pinned by digest
    (`docker://image@sha256:...`) is likewise fine and likewise not this
    file's business. A job-level `uses:` naming a reusable workflow IS matched
    and IS required to be pinned, deliberately: it decides what code runs.

  * **It does not check the tag's shape at push time.** Nothing on this machine
    or in this file stops `git tag 0.4.0` from being typed. What Claim 3 buys
    is that ONE mis-spelling shape — the bare-numeric one that has actually
    happened here — produces a red workflow run instead of silence. `V0.3.0`,
    `release-0.3.0` and `moviola-v0.3.0` still match no filter and still
    trigger nothing at all, and no tag that was never pushed is visible to
    anything.

  * **`queue: max` raises a cap; it does not remove one.** It takes the
    pending-run limit from 1 to 100. A hundred-and-first concurrent tag still
    loses a release, and nothing here would notice.

  * **The per-job timeout check reads job-level keys in this workflow only.**
    A step-level `timeout-minutes:` is not counted, a job that declares one is
    not inspected for whether the value is sane — 360 declared explicitly
    passes — and `tests.yml` is the sibling file's business, not this one's.

  * **It reads workflows as TEXT, not as YAML**, and reuses
    `test_ci_runs_the_whole_suite.py`'s helpers to do it rather than growing a
    second parser with its own bugs. That file's own NON-GOALS about text
    scanning apply here unchanged: a `#` inside a quoted shell string is
    truncated as if it were a comment, which can hide a match and can never
    invent one — wrong in the loud direction. Step and job splitting are
    indentation-scoped, so a workflow written in flow style (`steps: [{...}]`)
    would read as having no steps at all.
"""
from __future__ import annotations

import re

import pytest

from repo_files import REPO as REPO_ROOT
from test_ci_runs_the_whole_suite import (
    top_level_block,
    unexplained_exemptions,
    without_comments,
    workflow_claims,
    workflow_files,
)

RELEASE_WORKFLOW = "release.yml"

# The three files the suite already pins to each other. The release workflow
# has to read all of them, because the tag is the fourth version and the only
# one that lives outside the repository.
VERSION_SOURCES = (
    "skills/moviola/SKILL.md",
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
)

# `owner/repo@ref` — the only `uses:` shape that has a commit SHA to pin. A
# leading `./` (local action) or `docker://` (container action) is excluded by
# the pattern itself rather than by an exemption list, because a structural
# exemption cannot go stale and a listed one can.
MARKETPLACE_USES = re.compile(
    r"^\s*(?:-\s*)?uses:\s*(?P<ref>[\w.-]+/[\w.-]+(?:/[\w.-]+)*@\S+)"
)

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

# An exit that actually fails the step. `sys.exit(` alone matched `sys.exit(0)`,
# which is the mutation this is here to catch — a guard that finds the mismatch,
# reports it, and returns success.
FAILING_EXIT = re.compile(r"(?:\bexit\s+[1-9]\d*\b|\bsys\.exit\(\s*[1-9])")

# A version in a pin comment. `# v4` is as legitimate as `# v4.4.0` — the
# major-only form is what a repo pinning a floating major's current commit
# writes, and rejecting it pushes someone towards a bare SHA, which is
# strictly worse. Nothing here checks the comment is TRUE; see NON-GOALS.
PIN_VERSION_COMMENT = re.compile(r"v?\d+(?:\.\d+)*\b")

# Values a YAML reader calls false. Compared against the raw text of the value
# because this file does not parse YAML: `false`, `"false"` and `'false'` are
# three different strings and one intent.
FALSEY = {"false", "no", "off", "0", ""}

# A `uses:` that legitimately cannot be pinned to a commit SHA, mapped to the
# reason. Empty on purpose: every marketplace action in this repository can be
# pinned, and an entry here is a claim that one cannot. Same reason-required
# contract as `CI_NEED_NOT_INSTALL` in test_ci_runs_the_whole_suite.py.
UNPINNABLE_USES: dict[str, str] = {}


def the_release_workflow() -> str:
    """The release workflow's text, or a failure that names the cause.

    Fails rather than skipping. A missing release workflow is not a reason to
    stop checking the release workflow — it is the loudest possible finding
    about it, and a skip would let the file be deleted without anything going
    red.
    """
    found = workflow_files()
    if RELEASE_WORKFLOW not in found:
        pytest.fail(
            f"{RELEASE_WORKFLOW} is not in .github/workflows. A tag publishes "
            "through it; without it a tag does nothing at all. Workflows "
            f"present: {sorted(found)}"
        )
    return found[RELEASE_WORKFLOW]


def tag_patterns(text: str) -> list[str]:
    """The tag globs the workflow triggers on, as written.

    Read out of the `on:` block so a `tags:` key somewhere else — a filter on
    a different event, a comment, a step input — cannot be mistaken for the
    trigger.
    """
    block = without_comments(top_level_block(text, "on"))
    patterns: list[str] = []
    inside = False
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("tags:"):
            inside = True
            inline = stripped[len("tags:"):].strip()
            if inline:
                patterns.extend(_unquote_list(inline))
                inside = False
            continue
        if inside:
            if stripped.startswith("-"):
                patterns.append(_unquote(stripped.lstrip("-").strip()))
                continue
            inside = False
    return [p for p in patterns if p]


def _unquote(value: str) -> str:
    return value.strip().strip('"').strip("'")


def _unquote_list(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        return [_unquote(part) for part in value[1:-1].split(",")]
    return [_unquote(value)]


def value_of(text: str, key: str) -> str | None:
    """The unquoted value written against `key:`, or None if the key is absent.

    Exists because these checks used to test for a LITERAL. `"fetch-depth: 0"
    not in text` is green against `fetch-depth: '0'`, and
    `"generate_release_notes: true" not in text` is green against
    `generate_release_notes: "true"` — both of which are the same input to
    GitHub and the opposite input to a substring test. Reads the value, then
    judges it.
    """
    match = re.search(rf"^\s*{re.escape(key)}:\s*(.*)$", text, re.MULTILINE)
    if match is None:
        return None
    return _unquote(match.group(1))


def is_true(value: str | None) -> bool:
    """Whether a written YAML scalar reads as true. Absent is not true."""
    return value is not None and _unquote(value).lower() not in FALSEY


def release_steps() -> list[str]:
    """The release job's steps, each as one text block, comments stripped.

    Split on `- name:` at the indentation the FIRST such line under `steps:`
    uses, so a `- name:` nested inside an action's `with:` input cannot be
    read as a step. Comments are stripped first: this was the only reader in
    the file that kept them, which meant a claim written in a comment could
    satisfy an assertion about what a step DOES.

    NON-GOAL of its own: a step written WITHOUT a `name:` is legal YAML and
    invisible to this split, so it is folded into whichever named step
    precedes it.
    """
    lines = without_comments(the_release_workflow()).splitlines()
    start = next(
        (i for i, line in enumerate(lines) if re.match(r"^\s*steps:\s*$", line)),
        None,
    )
    if start is None:
        pytest.fail(
            f"{RELEASE_WORKFLOW} has no `steps:` key on its own line, so this "
            "file cannot find any step to check. Either the job was deleted "
            "or the workflow is written in a flow style this reader does not "
            "understand — see NON-GOALS."
        )
    indent: int | None = None
    blocks: list[str] = []
    current: list[str] | None = None
    for line in lines[start + 1:]:
        match = re.match(r"^(\s*)-\s*name:", line)
        if match and (indent is None or len(match.group(1)) == indent):
            indent = len(match.group(1))
            if current is not None:
                blocks.append("\n".join(current))
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        blocks.append("\n".join(current))
    return blocks


def release_jobs() -> dict[str, str]:
    """`{job id: its body}` for the release workflow, comments stripped.

    Indentation-scoped like everything else: a job id is a two-space key
    directly under `jobs:`. Needed because a `timeout-minutes:` anywhere in
    the file used to satisfy a check whose name promised every job had one.
    """
    block = without_comments(top_level_block(the_release_workflow(), "jobs"))
    jobs: dict[str, list[str]] = {}
    name: str | None = None
    for line in block.splitlines():
        match = re.match(r"^ {2}([\w.-]+):\s*$", line)
        if match:
            name = match.group(1)
            jobs[name] = []
            continue
        if name is not None:
            jobs[name].append(line)
    return {job: "\n".join(body) for job, body in jobs.items()}


def the_guard_step() -> str:
    """The one step comparing the tag to all three in-repo version sources."""
    guarding = [
        step
        for step in release_steps()
        if ("github.ref_name" in step or "GITHUB_REF_NAME" in step)
        and all(source in step for source in VERSION_SOURCES)
    ]
    if len(guarding) != 1:
        pytest.fail(
            f"expected exactly one step comparing the tag to all of "
            f"{list(VERSION_SOURCES)}, found {len(guarding)}. Zero means the "
            "guard was deleted or split across steps — and split is worse "
            "than absent, because a check for those strings anywhere in the "
            "file would stay green while no single step does the comparison."
        )
    return guarding[0]


def uses_lines() -> list[tuple[str, int, str, str]]:
    """`(workflow, lineno, ref, raw_line)` for every marketplace `uses:`."""
    found = []
    for name, text in sorted(workflow_files().items()):
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = MARKETPLACE_USES.match(line)
            if match:
                found.append((name, lineno, match.group("ref"), line))
    return found


# --------------------------------------------------------------------------
# Claim 1 — the tag cannot contradict what the tag ships
# --------------------------------------------------------------------------

class TestTheTagMatchesWhatShips:
    """The workflow must reject a tag whose version the tree does not carry."""

    def test_the_workflow_reads_the_tag_name(self):
        claims = workflow_claims(the_release_workflow())
        assert "GITHUB_REF_NAME" in claims or "github.ref_name" in claims, (
            f"{RELEASE_WORKFLOW} never reads the tag it was triggered by, so "
            "it cannot compare the tag to anything. Tagging v0.3.1 on a tree "
            "that says 0.3.0 would publish an asset contradicting its own "
            "name. Read of the workflow with comments and `name:` labels "
            "stripped, so mentioning it in prose does not count."
        )

    @pytest.mark.parametrize("source", VERSION_SOURCES)
    def test_the_workflow_checks_the_tag_against_each_version_source(self, source):
        claims = workflow_claims(the_release_workflow())
        assert source in claims, (
            f"{RELEASE_WORKFLOW} does not read {source}, which carries one of "
            "the four versions a release has to agree on. The suite pins the "
            "three in-repo files to each other; the tag is the fourth and "
            "only the workflow can see it."
        )

    def test_the_guard_lives_in_one_step_that_always_runs(self):
        """The mutation that exposed this survived every other check here.

        Deleting the guard step's `name:` line while leaving its `run:` body
        attached to a disabled step kept every string assertion above green:
        they ask whether the workflow MENTIONS the tag and the three version
        files, not whether a step that runs does. This narrows that for the
        one step where it matters most. It does NOT close the general case —
        this file's NON-GOALS still say a step behind a false `if:` is
        invisible, and that remains true of every other step.

        Deliberately strict: a version guard has no legitimate reason to be
        conditional, or to be allowed to fail without failing the job. If one
        ever appears, change this test and say why, so the reason is recorded
        rather than inferred from a green run.
        """
        step = the_guard_step()
        conditional = [
            line for line in step.splitlines() if re.match(r"^\s+if:", line)
        ]
        assert not conditional, (
            f"the tag/version guard carries a condition: {conditional}. A "
            "step behind an `if:` that evaluates false is skipped silently "
            "and the release publishes anyway — which is the whole failure "
            "this step exists to stop."
        )
        tolerated = [
            line.strip()
            for line in step.splitlines()
            if re.match(r"^\s+continue-on-error:", line)
            and is_true(line.partition(":")[2])
        ]
        assert not tolerated, (
            f"the tag/version guard is marked {tolerated}, so it reports a "
            "mismatch and the job carries on to publish anyway. That is the "
            "same outcome as deleting the step, arrived at more quietly."
        )

    def test_the_job_holding_the_guard_is_not_itself_conditional(self):
        """A false `if:` on the JOB skips the guard and the publish together.

        Survivable — nothing is released — but indistinguishable from a green
        run, so a tag would appear to have published. Scoped to the job that
        carries the guard rather than to all jobs, because a conditional job
        is a perfectly legitimate thing to write in general.
        """
        holders = {
            job: body
            for job, body in release_jobs().items()
            if "github.ref_name" in body or "GITHUB_REF_NAME" in body
        }
        assert holders, (
            "no job in the release workflow reads the tag name, so either the "
            "guard moved out of the file or release_jobs() stopped reading "
            "the shape the workflow is written in."
        )
        offenders = {
            job: [
                line.strip()
                for line in body.splitlines()
                if re.match(r"^ {4}if:", line)
            ]
            for job, body in holders.items()
        }
        offenders = {job: found for job, found in offenders.items() if found}
        assert not offenders, (
            f"the job carrying the tag guard is conditional: {offenders}. A "
            "job whose `if:` evaluates false is SKIPPED and the workflow run "
            "is still green, so a tag would look published and would not be."
        )

    def test_nothing_runs_before_the_guard(self):
        """`contents: write` is granted to the job, not to the step.

        The guard gates the publish only because it is the first step that
        executes anything. A cache restore, a setup action or a `run:` slipped
        above it executes under write access against an unvalidated tag, and
        every other assertion in this class stays green while it does.
        """
        steps = release_steps()
        running = [
            index
            for index, step in enumerate(steps)
            if re.search(r"^\s+run:", step, re.MULTILINE)
        ]
        assert running, (
            f"no step in {RELEASE_WORKFLOW} has a `run:` block, so either the "
            "workflow does nothing or release_steps() stopped reading it."
        )
        first = steps[running[0]].strip().splitlines()[0].strip()
        assert steps[running[0]] == the_guard_step(), (
            f"the first step that runs anything is {first!r}, not the "
            "tag/version guard. The guard gates the job only by being first — "
            "anything above it executes under `contents: write` against a tag "
            "nothing has checked."
        )

    def test_the_version_check_can_fail_the_job(self):
        """A comparison that cannot exit non-zero is decoration.

        Scoped to the guard step. `"exit 1" in <the whole file>` was satisfied
        by an `exit 1` anywhere at all — including one in an unrelated step,
        or in the tag-shape check while the version comparison below it
        printed its findings and carried on.
        """
        step = the_guard_step()
        assert re.search(r"(?<![\w.])(!=|==)(?![\w.])", step), (
            f"the tag/version guard in {RELEASE_WORKFLOW} names the three "
            "version files but compares nothing. Reading a file and printing "
            "what is in it is not a check."
        )
        assert FAILING_EXIT.search(step), (
            f"{RELEASE_WORKFLOW} compares versions but nothing in that step "
            "exits non-zero, so a mismatch would be reported and published "
            "anyway. The check has to fail the job."
        )


# --------------------------------------------------------------------------
# Claim 2 — a release cannot be published by code nobody chose
# --------------------------------------------------------------------------

class TestEveryActionIsPinnedToACommit:
    """`uses:` on a floating tag is an unreviewed upgrade under write access."""

    def test_there_is_something_to_check(self):
        """Guard against the parametrized checks passing over an empty set."""
        assert uses_lines(), (
            "no marketplace `uses:` found in any workflow. Either the "
            "workflows stopped using actions — in which case delete this "
            "class — or MARKETPLACE_USES stopped matching the shape they are "
            "written in, which would make every check below vacuous."
        )

    def test_every_marketplace_uses_is_a_full_commit_sha(self):
        floating = []
        for name, lineno, ref, _ in uses_lines():
            action, _, version = ref.partition("@")
            if ref in UNPINNABLE_USES or action in UNPINNABLE_USES:
                continue
            if not FULL_SHA.match(version):
                floating.append(f"{name}:{lineno} {ref}")
        assert not floating, (
            "these actions float: "
            + ", ".join(floating)
            + ". A tag can be re-pointed at any commit, and release.yml holds "
            "`contents: write`, so whatever the tag resolves to at run time "
            "can write releases and tags in this repository. Pin to a 40-char "
            "commit SHA with the version in a trailing comment."
        )

    def test_every_pin_says_which_version_it_is(self):
        """A bare SHA is unreadable in a diff and unbumpable by hand."""
        bare = []
        for name, lineno, ref, line in uses_lines():
            _, _, version = ref.partition("@")
            if not FULL_SHA.match(version):
                continue  # the check above owns this case
            comment = line.partition("#")[2].strip()
            if not PIN_VERSION_COMMENT.search(comment):
                bare.append(f"{name}:{lineno}")
        assert not bare, (
            "these pins carry no version comment: "
            + ", ".join(bare)
            + ". A 40-character SHA with nothing beside it cannot be reviewed "
            "in a diff or bumped without a lookup. Write `# v4.4.0` after it. "
            "NOTE: nothing here verifies the comment is TRUE — see this "
            "file's NON-GOALS."
        )

    def test_unpinnable_exemptions_carry_a_reason(self):
        unexplained = unexplained_exemptions(UNPINNABLE_USES)
        assert not unexplained, (
            f"{unexplained} are exempt from SHA pinning with no reason given. "
            "An exemption without a reason is indistinguishable from an "
            "oversight the next time someone reads this."
        )

    def test_an_exemption_without_a_reason_is_caught(self):
        """UNPINNABLE_USES is empty, so the rule above is vacuous today.

        Drive it with synthetic input, the way `test_an_exemption_is_honoured`
        does for `CI_NEED_NOT_INSTALL` in test_ci_runs_the_whole_suite.py. A
        contract only ever evaluated against `{}` is a contract nobody has run.
        """
        assert unexplained_exemptions({"some/action": "   "}) == ["some/action"]
        assert unexplained_exemptions({"some/action": "no SHA exists"}) == []

    def test_the_checkout_does_not_leave_the_token_behind(self):
        """`actions/checkout` writes the job token into `.git/config`.

        `persist-credentials` defaults to TRUE, and the steps after the
        checkout run `build-skill.sh` — a script read from the TAGGED tree,
        not from the workflow file. A `contents: write` credential sitting on
        disk while repo-controlled code executes is the shape this closes.
        """
        checkouts = [step for step in release_steps() if "actions/checkout@" in step]
        assert checkouts, (
            f"{RELEASE_WORKFLOW} has no `actions/checkout` step, so either it "
            "stopped checking the repository out or release_steps() stopped "
            "reading it."
        )
        for step in checkouts:
            value = value_of(step, "persist-credentials")
            assert value is not None and not is_true(value), (
                "the release checkout does not set `persist-credentials: "
                "false`, so the job's `contents: write` token is written into "
                ".git/config and is still there while build-skill.sh — read "
                f"from the TAGGED tree — runs. Found: {value!r}."
            )


# --------------------------------------------------------------------------
# Claim 3 — a mis-spelled tag fails loudly rather than doing nothing
# --------------------------------------------------------------------------

class TestAMisSpelledTagIsNotSilent:
    """`0.1.0` is already on origin. Under `v*` it triggered no run at all."""

    def test_the_trigger_matches_the_tags_this_repo_actually_cuts(self):
        """Widening must not become replacing.

        Deleting `"v*"` and keeping `"[0-9]*"` satisfied every other check in
        this class while making every real release — all of which are
        `v`-prefixed — trigger nothing at all.
        """
        patterns = tag_patterns(the_release_workflow())
        assert patterns, (
            f"{RELEASE_WORKFLOW} has no tag patterns in its `on:` block, so "
            "either it no longer triggers on tags or tag_patterns() stopped "
            "reading the shape it is written in."
        )
        assert any(p.startswith("v") for p in patterns), (
            f"no tag pattern in {RELEASE_WORKFLOW} matches a `v`-prefixed tag "
            f"({patterns}). Every release this repository has ever cut is "
            "spelled `vX.Y.Z`; a trigger that misses them publishes nothing."
        )

    def test_the_trigger_is_not_exclusively_v_prefixed(self):
        patterns = tag_patterns(the_release_workflow())
        assert any(not p.startswith("v") for p in patterns), (
            f"every tag pattern in {RELEASE_WORKFLOW} starts with `v` "
            f"({patterns}), so a tag spelled `0.4.0` triggers nothing: no "
            "run, no error. `0.1.0` is on origin now beside four v-prefixed "
            "siblings and produced no workflow run at all, so this has "
            "already happened once. Match the bare-numeric shape too and let "
            "the version guard reject it loudly."
        )

    def test_the_trigger_is_not_widened_to_everything(self):
        """`"*"` satisfies both checks above and is not the fix.

        A catch-all runs the release job under `contents: write` for every tag
        anyone pushes, scratch and annotation tags included. The point is to
        match the two shapes a release is plausibly spelled as and reject what
        does not parse — not to run on all of them.
        """
        patterns = tag_patterns(the_release_workflow())
        catchall = [p for p in patterns if p.strip("*") == ""]
        assert not catchall, (
            f"{RELEASE_WORKFLOW} triggers on {catchall}, which matches every "
            "tag in the repository. Every scratch tag would start a job "
            "holding `contents: write`. Match the shapes a release is spelled "
            "as, and reject the rest in the guard."
        )

    def test_the_workflow_rejects_a_tag_it_cannot_parse(self):
        """Matching a bare tag is only an improvement if it then fails.

        Distinct from `test_the_version_check_can_fail_the_job`: that one is
        about the version COMPARISON, this one about the tag's SHAPE. The two
        were byte-identical assertions until a shell `case` glob turned out to
        accept `v1.2.3.4` and `v1.2.3-anything` — the comment above it claimed
        a strictness the pattern did not have, because `.` is literal in a
        glob and a trailing `*` swallows the rest.
        """
        step = the_guard_step()
        anchored = re.search(r"\^v\[0-9\]", step) or re.search(r"\^v\\d", step)
        assert anchored, (
            f"the tag/version guard in {RELEASE_WORKFLOW} does not test the "
            "tag against an ANCHORED numeric pattern. A shell `case` glob "
            "cannot express `vX.Y.Z`: `.` is literal and `*` swallows the "
            "rest, so `v1.2.3.4` and `v1.2.3-whatever` both match it. Use a "
            "regex anchored at `^v[0-9]`."
        )
        assert FAILING_EXIT.search(step), (
            f"{RELEASE_WORKFLOW} matches tags it cannot publish but never "
            "exits non-zero. Broadening the trigger without a guard turns a "
            "silent no-op into a silent wrong release, which is worse."
        )


# --------------------------------------------------------------------------
# Claim 4 — the release notes describe THIS repository
# --------------------------------------------------------------------------

class TestTheNotesComeFromThisRepo:
    """moviola is a fork and carries upstream's commit history."""

    def test_generated_release_notes_are_off(self):
        """Reads the VALUE. `generate_release_notes: "true"` is still true."""
        claims = workflow_claims(the_release_workflow())
        value = value_of(claims, "generate_release_notes")
        assert not is_true(value), (
            f"`generate_release_notes: {value}` builds notes from commits "
            "since the last release. There is no previous release here and "
            "this fork carries bradautomates/claude-video's history, so the "
            "first run would describe upstream's commits and ignore "
            "CHANGELOG.md."
        )

    def test_the_body_comes_from_a_file_this_workflow_writes(self):
        """A `body_path:` nothing writes publishes an empty release.

        The filename is compared, not merely present. `body_path:` pointing at
        `notes.md` while the extraction step writes `release-notes.md` is a
        green check and an empty release — the two halves used to be asserted
        independently and never against each other.

        The comparison needs a PATH BOUNDARY, not `in`. `"notes.md" in
        "release-notes.md"` is true, so a plain substring test calls the
        extraction step a writer of a file it does not write and passes the
        exact mutation this exists to catch.
        """
        claims = workflow_claims(the_release_workflow())
        body_path = value_of(claims, "body_path")
        assert body_path, (
            f"{RELEASE_WORKFLOW} supplies no `body_path:`, so the release "
            "body is whatever GitHub synthesises. CHANGELOG.md exists to say "
            "what changed; the release should publish that."
        )
        names = re.compile(rf"(?<![\w.\-/]){re.escape(body_path)}(?![\w.\-/])")
        writers = [
            step
            for step in release_steps()
            if names.search(step) and "body_path" not in step
        ]
        assert writers, (
            f"`body_path: {body_path}` names a file no other step in "
            f"{RELEASE_WORKFLOW} writes. The release body would be empty and "
            "the run would be green while it was."
        )

    def test_the_notes_are_built_from_the_changelog(self):
        claims = workflow_claims(the_release_workflow())
        assert "CHANGELOG.md" in claims, (
            f"{RELEASE_WORKFLOW} never reads CHANGELOG.md. A `body_path:` "
            "pointing at a file nothing writes publishes an empty release."
        )

    def test_the_changelog_has_a_non_empty_section_for_the_shipped_version(self):
        """The workflow's extraction has to have something to extract.

        Structural, and it runs everywhere: it reads the repository, not the
        workflow. If this fails, cutting a release publishes an empty body no
        matter how correct release.yml is — and an empty section is exactly as
        empty as a missing one, which is why the heading alone was never
        enough to assert.
        """
        skill = (REPO_ROOT / "skills" / "moviola" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        match = re.search(r'^version:\s*"?([^"\s]+)"?', skill, re.MULTILINE)
        assert match, "SKILL.md frontmatter carries no `version:`"
        version = match.group(1)
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        lines = changelog.splitlines()
        heading = re.compile(rf"^## \[{re.escape(version)}\]")
        start = next((i for i, line in enumerate(lines) if heading.match(line)), None)
        assert start is not None, (
            f"CHANGELOG.md has no `## [{version}]` section, but that is the "
            "version SKILL.md ships and the one a release would be cut at. "
            "The release body would be empty."
        )
        body = []
        for line in lines[start + 1:]:
            if line.startswith("## ["):
                break
            body.append(line)
        assert "\n".join(body).strip(), (
            f"CHANGELOG.md's `## [{version}]` section is empty. The workflow "
            "extracts the text between that heading and the next one and "
            "publishes it as the release body; there is nothing to extract."
        )


# --------------------------------------------------------------------------
# Claim 5 — a pre-release does not take over /releases/latest
# --------------------------------------------------------------------------

class TestAPreReleaseStaysAPreRelease:
    """README.md sends people to /releases/latest."""

    def test_prerelease_is_not_hardcoded(self):
        claims = workflow_claims(the_release_workflow())
        hardcoded = re.search(
            r"prerelease:\s*[\"']?(true|false)[\"']?\s*$", claims, re.MULTILINE
        )
        assert not hardcoded, (
            "`prerelease:` is hardcoded to "
            f"`{hardcoded.group(1) if hardcoded else ''}`. The trigger matches "
            "`v0.3.0-rc1`, so an rc would publish as a full release and take "
            "over /releases/latest — the URL README.md points at. Derive it "
            "from the ref instead."
        )

    def test_prerelease_is_derived_from_the_ref(self):
        claims = workflow_claims(the_release_workflow())
        assert "prerelease:" in claims, (
            f"{RELEASE_WORKFLOW} sets no `prerelease:` at all, so the action "
            "falls back to its own default and a pre-release tag is published "
            "as a full release."
        )
        line = next(line for line in claims.splitlines() if "prerelease:" in line)
        assert "github.ref" in line or "contains(" in line, (
            f"`prerelease:` is set to `{line.strip()}`, which does not read "
            "the ref. Whether a tag is a pre-release is a property of the tag."
        )

    def test_an_unmatched_asset_does_not_publish_silently(self):
        """`fail_on_unmatched_files` defaults to FALSE.

        A `files:` pattern that stops matching — a renamed build output, a
        changed dist path — creates the release with no asset attached and
        reports success. `test -f` in the build step catches the same case one
        step earlier; this is the backstop for when it does not.
        """
        claims = workflow_claims(the_release_workflow())
        assert value_of(claims, "files"), (
            f"{RELEASE_WORKFLOW} attaches no `files:` to the release, so a "
            "tag would publish notes and no artifact."
        )
        assert is_true(value_of(claims, "fail_on_unmatched_files")), (
            "`fail_on_unmatched_files` is unset or false, so a `files:` "
            "pattern matching nothing publishes a release with no asset "
            "attached and the run stays green."
        )


# --------------------------------------------------------------------------
# Claim 6 — two tags pushed together do not race, and none is dropped
# --------------------------------------------------------------------------

class TestTheReleaseCannotRaceItself:

    def test_the_release_workflow_has_a_concurrency_group(self):
        text = the_release_workflow()
        assert top_level_block(text, "concurrency").strip(), (
            f"{RELEASE_WORKFLOW} has no top-level `concurrency:` group. Two "
            "tags pushed together run two jobs racing to create releases."
        )

    def test_the_group_is_not_scoped_to_the_ref(self):
        """One group per tag is the same as no group at all.

        `group: release-${{ github.ref }}` reads as a concurrency control and
        gives every tag its own group, so two tags pushed together sit in two
        groups and race exactly as before. This is the mutation the check
        above cannot see.
        """
        block = without_comments(top_level_block(the_release_workflow(), "concurrency"))
        group = value_of(block, "group")
        assert group, (
            f"{RELEASE_WORKFLOW}'s `concurrency:` block declares no `group:`, "
            "which is a required key — the block does nothing without it."
        )
        per_ref = [
            token
            for token in ("github.ref", "github.sha", "github.run_id", "matrix.")
            if token in group
        ]
        assert not per_ref, (
            f"the concurrency group is `{group}`, which varies per {per_ref}. "
            "Every tag then gets its own group and two tags pushed together "
            "race to create releases — the exact failure this block exists to "
            "stop."
        )

    def test_a_release_run_is_not_cancelled_midway(self):
        """Cancelling a half-finished publish is worse than queueing it.

        Reads the VALUE: `cancel-in-progress: ${{ true }}` and
        `cancel-in-progress: "true"` are both true to GitHub and neither is
        the literal the old substring check looked for.
        """
        block = without_comments(top_level_block(the_release_workflow(), "concurrency"))
        assert not is_true(value_of(block, "cancel-in-progress")), (
            "the release workflow cancels in-progress runs. A cancelled "
            "release can leave a created release with no asset attached. "
            "Queue them instead: a release is not a preview to be replaced."
        )

    def test_a_pending_release_is_not_dropped(self):
        """`cancel-in-progress: false` protects the RUNNING job only.

        GitHub keeps at most ONE pending run per concurrency group, and a
        third arrival cancels the one already waiting. `git push origin
        --tags` carrying three new tags therefore loses the middle release
        with a status of *cancelled*, which emails nobody. `queue: max` raises
        the pending limit to 100 — see NON-GOALS: a cap raised, not removed.
        """
        block = without_comments(top_level_block(the_release_workflow(), "concurrency"))
        assert value_of(block, "queue") == "max", (
            "the release workflow's concurrency block does not set "
            "`queue: max`. With the default only one run may pend, so a third "
            "tag silently cancels the second one's release. Note that "
            "`queue: max` is a validation error alongside "
            "`cancel-in-progress: true` — the two go together."
        )

    def test_every_job_declares_a_timeout(self):
        """GitHub's default is 360 minutes, and release.yml can write.

        Not one of the six filed findings — added with the rewrite because a
        job holding `contents: write` with no ceiling is the one place a hung
        step is expensive.

        Reads JOB bodies, not file text. `"timeout-minutes:" in text` is
        satisfied by one job declaring it while a second, added later, does
        not — which is the shape this exists to catch. Scoped to the release
        workflow's own jobs; the sibling file owns tests.yml.
        """
        jobs = release_jobs()
        assert jobs, (
            f"{RELEASE_WORKFLOW} declares no jobs, so either it does nothing "
            "or release_jobs() stopped reading the shape it is written in."
        )
        missing = [
            job
            for job, body in sorted(jobs.items())
            if not re.search(r"^ {4}timeout-minutes:", body, re.MULTILINE)
        ]
        assert not missing, (
            f"{missing} declare no job-level `timeout-minutes:`, so a hung "
            "step runs for GitHub's default of 360 minutes before anything "
            "stops it, under `contents: write`. `concurrency` does not help — "
            "the group is per-workflow and a stuck run holds it."
        )

    def test_the_checkout_does_not_fetch_the_full_history(self):
        """Reads the VALUE. `fetch-depth: '0'` is still the whole history."""
        claims = workflow_claims(the_release_workflow())
        depth = value_of(claims, "fetch-depth")
        assert depth is None or depth.strip() != "0", (
            "`fetch-depth: 0` fetches the whole history for a `git describe` "
            "this workflow does not do. build-skill.sh archives "
            "`HEAD:skills/moviola`, which needs one commit. On a fork "
            "carrying upstream's history that is the slowest step in the job."
        )
