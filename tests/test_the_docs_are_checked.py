"""Every claim the docs make about the code is checked against the code.

The fork inherited a set of documentation claims that were true of upstream and
false here, and nothing in the suite could tell: the skill's frontmatter said
`0.2.0` while both plugin manifests said `0.3.0`, the author was still
`bradautomates` in one of the three, a star-history URL still pointed at the
upstream repository, `config.py` described a backend-resolution rule that the
consent work had replaced, `transcribe_video`'s docstring listed four of the five
option keys its caller actually builds, and the CHANGELOG claimed a test file had
42 tests when it had 84.

Every one of those is a claim with a machine-checkable referent, and every one of
them drifted because nothing compared the two halves. This file does the
comparing. It is the same discipline as `test_consistency.py` — that file pins
values repeated across files, this one pins prose against the code it describes.

NON-GOALS, so a green run is not read as more than it is:

  * The version test proves the three manifests AGREE on a string, and the
    CHANGELOG test proves that string is the one the newest entry is filed under.
    Neither proves it is the RIGHT version, that it was bumped for this change,
    or that it matches any tag — a tag lives outside a network-free suite, so
    `v0.3.0` can be absent, misplaced or never pushed with everything here green.
    Agreement plus a matching entry is as far as this file reaches; being correct
    is a release decision no test can make.
  * `tracked_text_files` asks git, so every file-sweeping test here audits what
    the repository SHIPS. Local scratch is invisible to them by design — and so
    is a real problem introduced in an untracked file that is about to be added.
    Where git cannot answer at all, those sweeps SKIP rather than guess, so a
    green run on such a machine is three checks lighter than it looks. `tests/
    repo_files.py` carries the reasoning and the rest of that helper's limits.
  * The flag test runs in ONE direction: every long flag `build_parser` defines
    must appear in README. The reverse would false-fire on correct docs — README
    also documents `setup.py`'s `--agent`, `--check`, `--copy` and `--list`,
    which are not moviola.py flags and are right to be there.
  * The upstream-URL test targets REPOSITORY urls. The README's links to the
    original author's YouTube channel and to Solaris Automation are attribution
    and must keep working; a rule that stripped every mention of the upstream
    author would delete the provenance this fork owes.
  * It cannot see a claim written in words no pattern matches. "roughly a hundred
    tests", "the default is balanced", "this is fast" — all unpinnable from here.
    A green run says the pinned claims agree with the code, never that the docs
    are true.
  * The CHANGELOG count is checked against COLLECTION. It says nothing about
    whether those tests assert anything worth asserting.
  * The self-reference test proves every URL names the SAME repository the
    manifest names. It cannot tell whether that repository exists, is reachable,
    or is the one that was meant: a rename to a typo'd name passes cleanly so
    long as every file agrees on the typo.
  * It only sees references written with the owner prefix. A bare relative link,
    a shortened URL, or a reference under a different owner is invisible to it,
    and a rename that leaves one of those behind still half-lands.
"""
from __future__ import annotations

import ast
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import moviola
import whisper
from config import DETAILS, WHISPER_BACKENDS

import test_check_setup_hook
import repo_files
from repo_files import tracked_text_files

REPO = Path(__file__).resolve().parent.parent
SKILL_MD = REPO / "skills" / "moviola" / "SKILL.md"
README = REPO / "README.md"
CHANGELOG = REPO / "CHANGELOG.md"
MANIFESTS = [REPO / ".claude-plugin" / "plugin.json", REPO / ".codex-plugin" / "plugin.json"]

SKILL_DIR = REPO / "skills" / "moviola"


def _frontmatter(path: Path) -> dict[str, str]:
    """The `key: value` lines of a markdown frontmatter block.

    Deliberately not a YAML parser: the frontmatter is flat scalars, and adding a
    dependency to read four fields would be a worse trade than a regex that
    cannot see nesting — which this block does not have.
    """
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path.name} has no frontmatter block"
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields


_ANY_HEADING = re.compile(r"^## +(\S.*?)\s*$", re.MULTILINE)
_VERSION_HEADING = re.compile(r"^\[(\d+\.\d+\.\d+)\]")
_UNRELEASED_HEADING = re.compile(r"^\[Unreleased\]", re.IGNORECASE)


def _newest_version_in(text: str) -> str:
    """The version in the topmost `## [x.y.z]` heading of a CHANGELOG.

    Topmost rather than highest: the file is newest-first by convention, and
    sorting the numbers instead would quietly accept an entry filed in the wrong
    place. If the convention is ever broken, this fails rather than papering over
    it — which is what it used to do. A `^## \\[(\\d+\\.\\d+\\.\\d+)\\]` search
    across the whole file SKIPS a heading it cannot parse and returns the next one
    down, so `## [0.4.0-rc.1]`, `## 0.4.0` and `## [v0.4.0]` above a `## [0.3.0]`
    entry all returned `0.3.0` and the caller then reported "CHANGELOG's newest
    entry is [0.3.0] but SKILL.md ships 0.4.0-rc.1" — pointing the reader at the
    wrong file. Reading the topmost heading and refusing to look past it is what
    makes the failure name the thing that is actually wrong.

    `## [Unreleased]` is the one heading skipped rather than rejected: it is the
    Keep a Changelog convention, not a broken version string, and treating it as a
    parse failure would fire on a legitimate file. See the NON-GOAL on the class
    below for what that skip costs.
    """
    seen: list[str] = []
    for match in _ANY_HEADING.finditer(text):
        heading = match.group(1)
        seen.append(heading)
        if _UNRELEASED_HEADING.match(heading):
            continue
        version = _VERSION_HEADING.match(heading)
        assert version, (
            f"the newest CHANGELOG entry is headed `## {heading}`, which is not the "
            "`## [x.y.z]` form this file is read newest-first by. Fix the heading "
            "rather than this test — a version string no parser can read is a "
            f"release nothing can check. Headings seen: {seen}"
        )
        return version.group(1)
    raise AssertionError(f"no `## [x.y.z]` heading found. Headings seen: {seen}")


def _newest_changelog_version() -> str:
    return _newest_version_in(CHANGELOG.read_text(encoding="utf-8"))


def _alternations_after(flag: str) -> list[list[str]]:
    """Every `--flag a|b|c` listing in README, as lists of values.

    Requires at least one pipe, which is what separates a listing of what the
    flag ACCEPTS from the many places README uses the flag with one value as an
    example. Returns all of them so a second, stale listing is a failure rather
    than something the first match hides.
    """
    pattern = re.compile(r"`" + re.escape(flag) + r" ((?:[a-z-]+\|)+[a-z-]+)`")
    text = README.read_text(encoding="utf-8")
    return [m.group(1).split("|") for m in pattern.finditer(text)]


class TestTheThreeManifestsDescribeOnePlugin:
    """AGENTS.md states this as a release invariant. Nothing enforced it."""

    def test_the_version_is_the_same_number_in_all_three(self) -> None:
        # It was not: SKILL.md said 0.2.0 and both plugin.json files said 0.3.0.
        # A user reading `/moviola`'s frontmatter and a marketplace reading the
        # manifest saw different plugins.
        versions = {SKILL_MD.name: _frontmatter(SKILL_MD)["version"]}
        for path in MANIFESTS:
            versions[path.parent.name] = json.loads(path.read_text(encoding="utf-8"))["version"]
        assert len(set(versions.values())) == 1, versions

    def test_the_author_is_the_same_name_in_all_three(self) -> None:
        # The fork renamed itself in the manifests and left the skill's own
        # frontmatter crediting upstream, which is the one field a user sees.
        authors = {SKILL_MD.name: _frontmatter(SKILL_MD)["author"]}
        for path in MANIFESTS:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            authors[path.parent.name] = manifest["author"]["name"]
            interface = manifest.get("interface")
            if interface and "developerName" in interface:
                authors[path.parent.name + ":interface"] = interface["developerName"]
        assert len(set(authors.values())) == 1, authors

    def test_the_manifests_agree_with_the_skill_on_its_name(self) -> None:
        names = {_frontmatter(SKILL_MD)["name"]}
        names.update(json.loads(p.read_text(encoding="utf-8"))["name"] for p in MANIFESTS)
        assert names == {"moviola"}


class TestTheChangelogDescribesTheVersionBeingShipped:
    """The three manifests agreeing on a string says nothing about which string.

    `TestTheThreeManifestsDescribeOnePlugin` proves the number is the same in all
    three places. Three files agreeing on a stale number passes that cleanly — so
    a release that bumped the manifests and forgot the CHANGELOG, or wrote the
    entry under the previous number, looked identical to a correct one.

    This pins the manifests to the one other file that has to move with them: the
    newest CHANGELOG heading. It is the half of "agreement is not correctness"
    that a network-free suite can actually reach.

    NON-GOALS, because the gap this leaves is the larger half:

      * It does NOT check the version against a git tag or a published release.
        That needs the network, or a `git` invocation whose answer depends on
        which refs happen to be fetched, and this suite is neither. `v0.3.0` can
        be absent, point at the wrong commit, or never be pushed at all, and
        every test here still passes. Read that as a limit of THIS FILE, not of
        the repository: `release.yml` already knows the tag as `GITHUB_REF_NAME`
        and could compare `${GITHUB_REF_NAME#v}` against the frontmatter before
        it builds the asset. It does not, and that gap is filed in TODOS.
      * It does NOT prove the version was bumped for the change being shipped.
        A release that edits behaviour and moves neither the manifests nor the
        CHANGELOG is invisible to it — both halves are consistent at the old
        number.
      * It reads the NEWEST heading only. An older entry rewritten after the fact
        passes — `TODOS.md` records one such case deliberately: the released 0.2.0
        entry at `CHANGELOG.md:84` says 25 MB where the code says 24, and rewriting
        a shipped entry is the wrong fix. (`test_consistency.py` records the 25-vs-24
        DISTINCTION, in the comment above `OUR_CAP`, but it reads `whisper.py` only
        and never opens the CHANGELOG — so nothing in the suite sees that line.)
      * It says nothing about the entry's CONTENT. A heading at the right number
        above a body describing different work is exactly as green as a correct one.

    The legitimate configuration it must NOT fire on is a Keep a Changelog
    `## [Unreleased]` section above the newest release: that is a convention, not a
    broken version heading, and `_newest_version_in` skips past it rather than
    rejecting it. The cost of that skip, stated so it is not discovered later: while
    an `## [Unreleased]` section is open, these tests pin the manifests to the last
    RELEASED number, so bumping the manifests ahead of cutting the entry goes red.
    This repo has no such section today; adopting one is a workflow decision that
    should revisit these two tests rather than be silently absorbed by them.
    """

    def test_the_newest_changelog_heading_is_the_version_in_the_frontmatter(self) -> None:
        # Not hypothetical in the other direction: the fork shipped SKILL.md at
        # 0.2.0 while both manifests said 0.3.0, and the CHANGELOG was the only
        # file that could have said which was meant.
        newest = _newest_changelog_version()
        assert newest == _frontmatter(SKILL_MD)["version"], (
            f"CHANGELOG's newest entry is [{newest}] but SKILL.md ships "
            f"{_frontmatter(SKILL_MD)['version']}"
        )

    def test_the_newest_changelog_heading_is_the_version_in_both_manifests(self) -> None:
        newest = _newest_changelog_version()
        for path in MANIFESTS:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            assert manifest["version"] == newest, (
                f"{path.parent.name} ships {manifest['version']}, "
                f"CHANGELOG's newest entry is [{newest}]"
            )

    # The parser's own guarantees, on crafted text rather than the real file: the
    # real CHANGELOG is correct, so it cannot demonstrate a broken convention.

    def test_a_heading_the_parser_cannot_read_fails_instead_of_skipping(self) -> None:
        # The regression this replaced: `^## \[(\d+\.\d+\.\d+)\]` searched the WHOLE
        # file and returned the first heading it could parse, so every one of these
        # silently reported 0.3.0 — the entry BELOW the one being shipped — and the
        # caller then blamed SKILL.md for a mismatch the CHANGELOG had caused.
        for broken in ("[0.4.0-rc.1]", "0.4.0", "[v0.4.0]"):
            text = f"# Changelog\n\n## {broken}\n\nwork\n\n## [0.3.0]\n\nold\n"
            with pytest.raises(AssertionError) as caught:
                _newest_version_in(text)
            assert broken in str(caught.value), (
                f"the failure for `## {broken}` has to name that heading — naming "
                "0.3.0 instead is what sent the reader to the wrong file"
            )

    def test_an_unreleased_section_is_a_convention_not_a_broken_heading(self) -> None:
        # The legitimate configuration this must NOT fire on.
        text = "# Changelog\n\n## [Unreleased]\n\npending\n\n## [0.3.0]\n\nshipped\n"
        assert _newest_version_in(text) == "0.3.0"

    def test_it_reads_the_topmost_entry_not_the_highest_number(self) -> None:
        # A hotfix filed above a larger number is exactly what sorting gets wrong.
        text = "# Changelog\n\n## [0.2.1]\n\nhotfix\n\n## [0.3.0]\n\nolder\n"
        assert _newest_version_in(text) == "0.2.1"


class TestTheFileSweepReadsOnlyWhatTheRepositoryShips:
    """The three repo-wide audits are only as honest as the file set they read.

    This is the guarantee `tracked_text_files` exists for, and nothing pinned it.
    The helper was changed from a working-tree walk to `git ls-files` and the suite
    stayed green either way — 18 passed before, 18 passed after, and 18 passed again
    with the change reverted — so the fix had no evidence behind it and a revert
    would have been silent. These two tests are that evidence.

    NON-GOALS:

      * It does not check WHICH tracked files come back beyond the suffix and
        skip-dir rules — only that an untracked one does not.
      * It cannot run where git cannot answer, and skips there for the same reason
        the helper does, so on such a machine this guarantee is unproven too.
    """

    PROBE = REPO / "zz-untracked-sweep-probe.md"

    # Concatenated rather than written out. A literal upstream URL here would make
    # THIS file a tracked file linking upstream, which is what the audit three
    # classes down forbids — the test would break the rule it exists to test. It
    # went red exactly that way when first written.
    POISON = "https://github.com/" + "bradautomates" + "/claude-video"

    def test_an_untracked_file_is_not_audited(self) -> None:
        # The probe carries the exact string the upstream-URL audit hunts for, so
        # under the working-tree walk it both APPEARS in the sweep and makes that
        # audit fail. Under `git ls-files` it is invisible, which is the point.
        self.PROBE.write_text(f"see {self.POISON}\n", encoding="utf-8")
        try:
            swept = tracked_text_files()
        finally:
            self.PROBE.unlink()
        assert self.PROBE not in swept, (
            "an untracked file reached the audit — the sweep is walking the working "
            "tree again, and local scratch is being read as a claim the repo makes"
        )

    def test_a_tracked_file_is_audited(self) -> None:
        # The guard above must not be satisfiable by returning nothing at all.
        assert CHANGELOG in tracked_text_files()

    def test_an_empty_listing_fails_instead_of_passing_over_nothing(self, monkeypatch) -> None:
        # git answering "no files" is not the same as git being unable to answer,
        # and only the second one skips. Without this guard all three repo-wide
        # audits assert `[] == []` and report green having read nothing — which
        # happens for real when REPO resolves inside a different repository that
        # has no commits yet.
        monkeypatch.setattr(repo_files, "git_listed_paths", lambda: [])
        with pytest.raises(AssertionError, match="no text files"):
            repo_files.tracked_text_files()


class TestThePublishedBundleShipsWhatGitattributesClaims:
    """`.gitattributes` says two dev files stay out of the claude.ai bundle. They did not.

    `export-ignore` patterns match relative to the ARCHIVE ROOT. `build-skill.sh`
    runs `git archive HEAD:skills/moviola`, so the root is `skills/moviola/` and a
    repo-root pattern spelled `skills/moviola/scripts/build-skill.sh` could never
    match. Both files shipped in every bundle, and the shipped `.skillignore` even
    named `scripts/build-skill.sh` — so the bundle carried a dev script together
    with an instruction telling install-time scanners not to look at it.

    NON-GOALS:

      * It archives HEAD, not the working tree, which is what actually ships but
        means an uncommitted fix here still reads as broken.
      * It checks the file LIST, not the contents. A runtime script that is present
        but wrong is exactly as green as a correct one.
      * It says nothing about the other two install surfaces. `/plugin install`
        takes a full-repo archive and `npx skills add` copies the directory
        wholesale; only the first is covered by the same `.gitattributes`.
    """

    def test_the_bundle_excludes_the_files_export_ignore_names(self) -> None:
        try:
            archive = subprocess.run(
                ["git", "-C", str(REPO), "archive", "--format=zip", "HEAD:skills/moviola"],
                capture_output=True,
                check=True,
                timeout=60,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            pytest.skip(f"git archive cannot run here: {exc}")

        names = zipfile.ZipFile(io.BytesIO(archive)).namelist()
        assert "SKILL.md" in names, f"the bundle lost its SKILL.md: {names}"
        for dev_only in ("scripts/build-skill.sh", ".skillignore", ".gitattributes"):
            assert dev_only not in names, (
                f"{dev_only} ships inside dist/moviola.skill. `export-ignore` matches "
                "relative to the archive root, so the pattern has to live in "
                "skills/moviola/.gitattributes — a repo-root pattern cannot reach it."
            )


class TestThePluginInstallArchiveShipsNoScannerConfig:
    """The `/plugin install` archive is the SECOND surface, and `.gitattributes` missed it.

    `TestThePublishedBundleShipsWhatGitattributesClaims` above covers the claude.ai
    bundle — `git archive HEAD:skills/moviola` — and its own NON-GOALS say it says
    nothing about the other two install surfaces. This is the one it named: Claude
    Code's `/plugin install` fetches a FULL-REPO archive, so the archive root is the
    repository root and a different set of `export-ignore` patterns applies.

    The repo-root `.skillignore` shipped in it. That file is scanner configuration —
    a list of paths install-time security scanners are told to skip — and shipping it
    is the same defect one directory up from the one already fixed: the bundle
    carried an instruction not to look at things. It is worse than useless here,
    because every path it names is ALREADY absent from this archive (`tests/`,
    `.github/`, `.agents/`, `dev-sync.sh` and `skills/moviola/scripts/build-skill.sh`
    are all `export-ignore`d), so what ships is an exclusion list whose only live
    effect would be on files a user added themselves.

    NON-GOALS, so a green run here is not read as more than it is:

      * It checks the archive's file LIST, not any file's contents. A runtime script
        that is present but wrong is exactly as green as a correct one.

      * It archives HEAD, not the working tree. An uncommitted fix still reads as
        broken here, which is deliberate — HEAD is what a user's `/plugin install`
        actually fetches.

      * **The legitimate configuration it must NOT fire on is the repo-root
        `.skillignore` continuing to exist.** `npx skills add` copies the directory
        wholesale and never runs `git archive`, so that surface still needs the file
        and still gets it. `export-ignore` is what separates the two; DELETING the
        file would fix this archive and break that one. Nothing here would catch
        that, because a deleted file is also an absent one.

      * It does not verify that any scanner honours `.skillignore`, or that the
        paths it names are the right ones. Whether the exclusion list is correct is
        a different question from whether it should ship.

      * It pins four entries that must STAY, not the whole roster. An
        `export-ignore` added tomorrow that drops something else from the archive is
        invisible here unless it drops one of those four.
    """

    # Present on purpose, and the repo-root .gitattributes carries a NOTE saying so:
    # /plugin install fetches the full-repo archive, so the plugin manifest and both
    # halves of the SessionStart hook — its config and the script it names — have to be
    # inside it or the plugin does not install. They are
    # asserted here because this class's whole subject is what `export-ignore` removes,
    # and an over-broad pattern is the failure mode a test about exclusions invites.
    MUST_SHIP = (
        "skills/moviola/SKILL.md",
        ".claude-plugin/plugin.json",
        "hooks/hooks.json",
        "hooks/scripts/check-setup.sh",
    )

    def _archive_names(self) -> list[str]:
        try:
            archive = subprocess.run(
                ["git", "-C", str(REPO), "archive", "--format=zip", "HEAD"],
                capture_output=True,
                check=True,
                timeout=60,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            pytest.skip(f"git archive cannot run here: {exc}")
        return zipfile.ZipFile(io.BytesIO(archive)).namelist()

    def test_the_archive_is_the_one_plugin_install_would_fetch(self) -> None:
        """The positive control, so the exclusion assertions cannot pass over nothing."""
        names = self._archive_names()

        assert names, "git archive produced an empty full-repo archive"
        for required in self.MUST_SHIP:
            assert required in names, (
                f"{required} is missing from the full-repo archive, so `/plugin "
                "install` would not install. An `export-ignore` pattern is too "
                "broad — the repo-root .gitattributes NOTE explains why hooks/ and "
                ".claude-plugin/ have to stay."
            )

    def test_no_skillignore_ships_in_the_plugin_archive(self) -> None:
        names = self._archive_names()

        shipped = [name for name in names if Path(name).name == ".skillignore"]
        assert not shipped, (
            f"{shipped} ships inside the archive `/plugin install` fetches. "
            ".skillignore is install-time scanner configuration: it tells a scanner "
            "which paths not to read. Every path it names is already export-ignored "
            "out of this archive, so shipping it adds no exclusion a user wants and "
            "one they did not ask for. Fix it with `/.skillignore export-ignore` in "
            "the repo-root .gitattributes, NOT by deleting the file — `npx skills "
            "add` copies the directory wholesale and still needs it."
        )


class TestNoUrlStillPointsAtTheRepositoryThisWasForkedFrom:
    """A fork's own badges pointing upstream send its users to someone else."""

    # Both the plain path form and the percent-encoded form that appears inside
    # query strings — the star-history image was correct and its LINK was not,
    # because the link encoded the slug and a grep for the plain form missed it.
    UPSTREAM = re.compile(r"bradautomates(/|%2F)claude-video", re.IGNORECASE)

    def test_no_tracked_file_links_to_the_upstream_repository(self) -> None:
        offenders = []
        for path in tracked_text_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not self.UPSTREAM.search(line):
                    continue
                # Prose provenance is not a link. The CHANGELOG says what this
                # was forked from, in words, and deleting that would be worse
                # than the badge bug this test exists for.
                if "http" in line or "github.com" in line:
                    offenders.append(f"{path.relative_to(REPO)}:{number}")
        assert offenders == []

    def test_the_attribution_links_are_still_there(self) -> None:
        # The guard above must not be satisfiable by deleting the credit. This
        # is the legitimate configuration it is required NOT to fire on.
        text = README.read_text(encoding="utf-8")
        assert "youtube.com/@bradbonanno" in text
        assert "solarisautomation.io" in text



class TestEverySelfReferenceNamesTheSameRepository:
    """A rename is a claim made in twenty places, and it half-lands by default.

    Renaming `claude-video` to `moviola` had to touch nine files: two plugin
    manifests, two marketplace manifests, the skill frontmatter, the skill's
    plugin-cache path, the README's install commands and clone path, AGENTS.md,
    and `dev-sync.sh`'s plugin key. Nothing checked any of them against anything,
    so a miss anywhere reads as a working install command and fails only in a
    stranger's terminal.

    The manifest's `repository` field is the one that has to be right — it is
    what a package registry reads — so it is the referent here and everything
    else is compared to it.
    """

    @staticmethod
    def _slug() -> tuple[str, str]:
        """`(owner, name)` from the manifest's repository URL."""
        url = json.loads(MANIFESTS[0].read_text(encoding="utf-8"))["repository"]
        owner, _, name = url.rstrip("/").removesuffix(".git").rpartition("/")
        return owner.rsplit("/", 1)[-1], name

    def test_every_reference_under_this_owner_names_the_same_repository(self) -> None:
        owner, name = self._slug()
        pattern = re.compile(re.escape(owner) + r"(?:/|%2F)([A-Za-z0-9._-]+)")
        offenders = []
        for path in tracked_text_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for match in pattern.finditer(line):
                    got = match.group(1).removesuffix(".git")
                    if got != name:
                        offenders.append(f"{path.relative_to(REPO)}:{number} -> {got}")
        assert offenders == [], f"expected {name!r}: {offenders}"

    def test_both_marketplaces_are_named_for_the_repository(self) -> None:
        # The marketplace name is not cosmetic: it is the `@` half of the
        # install key AND the directory the plugin is cached into
        # (~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/), so a
        # stale one breaks a documented path as well as a command.
        _, name = self._slug()
        for rel in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"):
            data = json.loads((REPO / rel).read_text(encoding="utf-8"))
            assert data["name"] == name, rel

    def test_every_install_key_matches_the_plugin_and_the_marketplace(self) -> None:
        plugin = json.loads(MANIFESTS[0].read_text(encoding="utf-8"))["name"]
        marketplace = json.loads(
            (REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )["name"]
        pattern = re.compile(re.escape(plugin) + r"@([A-Za-z0-9._-]+)")
        offenders = []
        for path in tracked_text_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for match in pattern.finditer(line):
                    if match.group(1) != marketplace:
                        offenders.append(f"{path.relative_to(REPO)}:{number}")
        assert offenders == [], f"expected {plugin}@{marketplace}: {offenders}"

    def test_the_cache_path_the_skill_documents_is_the_one_the_names_produce(self) -> None:
        # SKILL.md tells the agent where to find itself when it was installed as
        # a plugin. That path is assembled from two names this test already
        # pins, and it is the one reference a reader cannot verify by clicking.
        plugin = json.loads(MANIFESTS[0].read_text(encoding="utf-8"))["name"]
        marketplace = json.loads(
            (REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )["name"]
        expected = f"plugins/cache/{marketplace}/{plugin}/"
        assert expected in SKILL_MD.read_text(encoding="utf-8")


class TestTheReadmeDocumentsTheFlagsThatExist:
    """argparse is the source of truth; README is a copy that drifts."""

    @staticmethod
    def _long_flags() -> set[str]:
        flags = set()
        for action in moviola.build_parser()._actions:  # type: ignore[attr-defined]
            flags.update(opt for opt in action.option_strings if opt.startswith("--"))
        return flags - {"--help"}

    def test_every_flag_the_parser_defines_is_in_the_readme(self) -> None:
        text = README.read_text(encoding="utf-8")
        assert sorted(f for f in self._long_flags() if f not in text) == []

    def test_the_detail_values_readme_lists_are_the_ones_the_config_defines(self) -> None:
        # README spells them as a pipe-separated alternation in the flag list.
        # Adding a detail level to config.DETAILS without touching README leaves
        # a documented dial that is missing its newest setting.
        # The alternation, not any `--detail <one-value>` example: README uses
        # the latter half a dozen times in prose and matching one of those
        # compares the config against a single word.
        listings = _alternations_after("--detail")
        assert listings, "README no longer spells out the --detail values"
        assert listings == [list(DETAILS)] * len(listings)

    def test_the_whisper_values_readme_lists_are_the_ones_the_config_defines(self) -> None:
        listings = _alternations_after("--whisper")
        assert listings, "README no longer spells out the --whisper values"
        # `auto` is the default and is not a value you pass, so the documented
        # set is the backends minus it. Stated as an equality rather than a
        # subset so that adding a backend fails here too.
        expected = [b for b in WHISPER_BACKENDS if b != "auto"]
        assert listings == [expected] * len(listings)


class TestTheDocstringListsTheOptionsThatAreActuallyPassed:
    """`options` is a bare dict, so a missing key is silent at every layer."""

    @staticmethod
    def _keys_main_builds() -> set[str]:
        tree = ast.parse((REPO / "skills" / "moviola" / "scripts" / "moviola.py").read_text("utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
                continue
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "whisper_options" in targets:
                return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
        raise AssertionError("moviola.main() no longer builds a whisper_options dict")

    def test_the_documented_keys_are_the_keys_that_are_built(self) -> None:
        # `offline` was passed and undocumented, and it is the one key whose
        # third state is load-bearing — so the docstring omitted exactly the
        # option a reader most needed explained.
        doc = whisper.transcribe_video.__doc__ or ""
        sentence = doc.split("and is ignored by the API backends")[0]
        documented = set(re.findall(r"`(\w+)`", sentence)) - {"options"}
        assert documented == self._keys_main_builds()


class TestTheHookClaimsNoMoreThanItChecked:
    """find_spec proves the package is on the path, not that it imports."""

    def test_the_local_line_says_installed_and_not_ready(self, tmp_path: Path) -> None:
        # The hook probes with find_spec to stay off the SessionStart hot path —
        # a deliberate trade, documented in the script. What was not deliberate
        # was the sentence: it said "ready — transcription runs on this machine"
        # on the strength of a probe that a half-installed CTranslate2, a numpy
        # ABI mismatch, or a missing libstdc++ all pass.
        result = test_check_setup_hook._run(tmp_path, binaries=True, local_whisper=True)
        assert "faster-whisper is installed" in result.stdout
        assert "ready — transcription runs on this machine" not in result.stdout

    def test_the_api_lines_still_say_ready(self, tmp_path: Path) -> None:
        # The scope of the fix, stated as a test. An API key present in
        # moviola's own config file IS the whole prerequisite for trying that
        # backend, so "ready" claims no more than was checked there. Weakening
        # those sentences too would have been a change nothing had evidence for.
        result = test_check_setup_hook._run(
            tmp_path, env_body="MOVIOLA_WHISPER=groq\nGROQ_API_KEY=x\n", binaries=True
        )
        assert "ready — transcription via the groq API" in result.stdout


class TestNoHeadingWasSplitThroughAnInlineCodeSpan:
    """A heading with an odd number of backticks is a line that got cut in half.

    This exists because it happened. An edit to `TODOS.md` anchored on the
    string `## Completed` — and that string also appears, inside backticks, in
    the middle of a bullet that discusses the section by name. The insert
    matched the mention rather than the heading, split the line there, and left
    the tail at column zero as a real H2 reading

        ## Completed`, leaving yt-dlp's inherited descriptor and `md_fence` ...

    so the document had two `## Completed` headings and roughly a hundred lines
    of OPEN work filed under the second one. Nothing caught it: the file parses,
    the suite was green, and a duplicate-heading check does not fire either,
    because the two lines are not equal as strings.

    The odd backtick is what gives it away. Inline code spans come in pairs, so
    a heading holding an unmatched one is a heading that was cut through the
    middle of a span — which is exactly the shape a mis-anchored insert makes.
    Measured across every tracked markdown file in the repository, the corrupt
    line is the ONLY hit, so this is a signal and not a style rule.

    NON-GOALS:

      * **It sees one signature, not corruption in general.** A heading split
        somewhere without backticks, or a heading duplicated cleanly, is
        invisible here. This catches the shape that actually occurred.
      * **It reads headings only.** The same mis-anchored insert landing in the
        middle of a paragraph mangles a bullet without ever producing a
        heading, and nothing in this class would notice.
      * **A heading that legitimately wants one backtick would fire.** None
        does today. If one is ever needed, write the character as a pair or as
        an entity rather than loosening this check — the false-positive rate
        measured across the repository is zero, which is the only reason a
        check this blunt is worth having.
      * **It does not check that headings are unique, ordered, or nested
        correctly.** Those are different properties with different owners.
      * **It does not track fenced code blocks.** A line inside a fence that
        begins with a hash and a space — a shell comment in a bash example — is
        read as a heading here, so an unmatched backtick in one would be
        reported as heading corruption. Measured across the tracked markdown:
        nine such lines today, none of them odd, so this is dormant rather than
        a live false positive. Named rather than fixed, because a fence tracker
        is a second parser with its own edge cases and this check earns its
        keep only while it stays blunt.
    """

    def test_no_tracked_heading_holds_an_unmatched_backtick(self) -> None:
        offenders = []
        for path in tracked_text_files():
            if path.suffix != ".md":
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if not line.startswith("#"):
                    continue
                if not line.lstrip("#").startswith(" "):
                    continue
                if line.count("`") % 2:
                    offenders.append(
                        f"{path.relative_to(REPO)}:{number}: {line[:90]}"
                    )
        assert not offenders, (
            "a heading holds an unmatched backtick, which is what a line split "
            "through an inline code span looks like:\n  " + "\n  ".join(offenders)
        )


class TestTheChangelogsTestCountsAreReal:
    """A count in prose is a claim about a file, and it goes stale silently."""

    CLAIM = re.compile(r"`tests/(test_\w+\.py)`\s*—\s*(\d+) tests")

    def test_every_count_the_changelog_claims_matches_collection(self) -> None:
        claims = self.CLAIM.findall(CHANGELOG.read_text(encoding="utf-8"))
        assert claims, "the CHANGELOG no longer claims a test count anywhere"
        for filename, claimed in claims:
            path = REPO / "tests" / filename
            assert path.exists(), f"CHANGELOG names {filename}, which does not exist"
            if path.resolve() == Path(__file__).resolve():
                pytest.skip("a file cannot collect itself from inside its own run")
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", str(path), "--collect-only", "-q"],
                capture_output=True, text=True, cwd=REPO,
            )
            found = re.search(r"(\d+) tests? collected", proc.stdout)
            assert found, proc.stdout[-2000:]
            assert found.group(1) == claimed, f"{filename}: says {claimed}, collects {found.group(1)}"


class TestAFunctionReturnsWhatItSaysItReturns:
    """A return-value claim is prose about code, and it drifts the same way.

    This file exists because `transcribe_video`'s docstring named four of the
    five option keys its caller builds. The quiet-failures work then made the
    same function return a THIRD value — the transcript's missing ranges — and
    both of its other descriptions stayed at two: the annotation still read
    `tuple[list[dict], str]` and the docstring still said
    `Returns (segments, backend_used)`. Neither is decoration. The annotation is
    what a reader and a type checker are handed, and the sentence is what the
    next person copies when they write a call site.

    Two claims, checked against the code rather than against each other:

      * every `return (...)` literal has the arity its `tuple[...]` annotation
        declares — the check that catches this class of drift at the source;
      * where a docstring also spells the tuple out as `Returns (a, b)`, that
        sentence has the same arity as the annotation.

    NON-GOALS — what this cannot see, and what it must not fire on:

      * TYPES are not checked, only COUNT. A function annotated
        `tuple[list[dict], str]` that returns `(str, list)` passes here. Arity
        is what the two drifted descriptions actually disagreed on, and a real
        type checker is the tool for the other half.
      * A `return` whose value is a NAME or a CALL rather than a tuple literal
        is skipped, not guessed at — `return pair_with_timestamps(...)` is a
        legitimate two-value return this cannot count, and inventing an arity
        for it would make the test fire on correct code. Seventeen of the
        eighteen annotated functions in `scripts/` are green today; that is the
        must-not-fire half, and it is why the predicate is narrow.
      * Only functions annotated `tuple[...]` are in scope. A bare `tuple`, a
        `Sequence`, an aliased NamedTuple return (`-> ChunkOutcome`) or a union
        such as `tuple[str, str] | tuple[None, None]` is invisible here — the
        last one deliberately, since its two arms may legitimately differ.
      * The docstring half is a check on a sentence that ONE function in the
        codebase currently writes. It is not a house style being enforced: a
        function with no `Returns (...)` sentence is complete as it stands and
        the test says nothing about it.
      * Nested `def`s are excluded from their parent's scan, so a closure
        returning a differently-shaped tuple is neither attributed to the outer
        function nor checked on its own.
    """

    RETURNS_SENTENCE = re.compile(r"Returns \(([^)]*)\)")

    @staticmethod
    def _annotated_arity(node: ast.FunctionDef) -> int | None:
        """The element count of a `tuple[...]` return annotation, else None."""
        ann = node.returns
        if not isinstance(ann, ast.Subscript):
            return None
        if not (isinstance(ann.value, ast.Name) and ann.value.id == "tuple"):
            return None
        return len(ann.slice.elts) if isinstance(ann.slice, ast.Tuple) else 1

    @staticmethod
    def _own_returns(node: ast.FunctionDef) -> list[ast.Return]:
        """This function's own `return`s, not those of any `def` inside it."""
        found: list[ast.Return] = []

        def walk(body: list[ast.stmt]) -> None:
            for stmt in body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if isinstance(stmt, ast.Return):
                    found.append(stmt)
                for field in ("body", "orelse", "finalbody"):
                    walk(getattr(stmt, field, []) or [])
                for handler in getattr(stmt, "handlers", []) or []:
                    walk(handler.body)

        walk(node.body)
        return found

    def _annotated_functions(self):
        for path in sorted((REPO / "skills" / "moviola" / "scripts").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                arity = self._annotated_arity(node)
                if arity is not None:
                    yield path.name, node, arity

    def test_every_tuple_return_has_the_arity_its_annotation_declares(self) -> None:
        checked = 0
        for filename, node, arity in self._annotated_functions():
            for ret in self._own_returns(node):
                if not isinstance(ret.value, ast.Tuple):
                    continue
                checked += 1
                assert len(ret.value.elts) == arity, (
                    f"{filename}:{ret.lineno} {node.name}() returns "
                    f"{len(ret.value.elts)} values; its annotation declares {arity}"
                )
        assert checked >= 10, f"the scan found only {checked} tuple returns to check"

    def test_a_returns_sentence_counts_the_same_values_as_the_annotation(self) -> None:
        checked = 0
        for filename, node, arity in self._annotated_functions():
            match = self.RETURNS_SENTENCE.search(ast.get_docstring(node) or "")
            if not match:
                continue
            checked += 1
            named = [part.strip() for part in match.group(1).split(",") if part.strip()]
            assert len(named) == arity, (
                f"{filename}:{node.lineno} {node.name}() documents "
                f"{len(named)} return values ({match.group(1)}); "
                f"its annotation declares {arity}"
            )
        assert checked, "no function documents its return tuple any more"


class TestNoDocstringIsWrittenWhereNothingReadsIt:
    """A string nothing binds is a discarded expression, not documentation.

    Every other test in this file compares a claim to the code it describes.
    This one asks a prior question: is the claim anywhere a reader will find
    it? `_describe_holder` acquired a second triple-quoted block when a fix
    rewrote its docstring without deleting the old one. Python evaluates a
    bare string statement and throws the value away, so `help()`, `__doc__`,
    every IDE hover and every doc generator showed the FIRST block while the
    second sat in the file looking authoritative — the reader in the editor
    and the reader in the terminal were being told different things, and the
    one in the terminal could not tell.

    It is the same failure mode as a stale comment, with one difference that
    makes it worse: a stale comment is at least displayed. This is prose that
    is present, plausible, maintained by nobody, and invisible to everything
    that reads documentation programmatically.

    NON-GOALS, so a green run is not read as more than it is:

      * It cannot see whether a docstring is TRUE, only whether it is reachable.
        The rest of this file does the other half.
      * It exempts the PEP 258 attribute-docstring convention — a string
        directly after an assignment: `TIMEOUT = 30` followed by a bare
        string. That is a discarded expression too, but Sphinx reads it and it
        is a legitimate way to document a module constant. `scripts/` uses none
        today, so the exemption exists for code written later; without it this
        test would fire on correct work the first time someone reached for it.
      * It scans `scripts/` — what the skill ships. `tests/` and `hooks/` are
        out of scope, and a stray literal there stays invisible here.
      * A string statement is only findable when it is a plain literal. One
        built by concatenation at runtime, or an f-string, is not a `Constant`
        and is not seen — nor should it be, since neither was ever going to be
        a docstring.
      * It says nothing about a docstring that is MISSING. Requiring one is a
        house-style rule; this is a defect check.
    """

    @staticmethod
    def _orphans(path: Path) -> list[tuple[int, str]]:
        """Every string statement in `path` that no name and no `__doc__` binds."""
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            body = node.body
            # `body[1:]`: position 0 IS the docstring, which is the one case
            # here that is bound to anything.
            for index, stmt in enumerate(body[1:], start=1):
                if not (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    continue
                if isinstance(body[index - 1], (ast.Assign, ast.AnnAssign)):
                    continue  # PEP 258 attribute docstring — see NON-GOALS
                found.append((stmt.lineno, getattr(node, "name", "<module>")))
        return found

    def test_no_shipped_module_carries_a_string_nothing_reads(self) -> None:
        scripts = sorted((REPO / "skills" / "moviola" / "scripts").glob("*.py"))
        assert scripts, "the scan found no modules to check"
        offenders = [
            f"{path.name}:{line} in {owner}"
            for path in scripts
            for line, owner in self._orphans(path)
        ]
        assert not offenders, (
            "a string statement is evaluated and discarded, so this prose is in "
            "no `__doc__` and no `help()` output: " + ", ".join(offenders)
        )

    def test_it_finds_a_second_docstring_and_spares_the_first(self, tmp_path: Path) -> None:
        # The must-fire half, and the boundary beside it: one docstring is the
        # normal case and has to stay silent, or the test above is vacuous.
        one = tmp_path / "one.py"
        one.write_text('def f():\n    """Real."""\n    return 1\n', encoding="utf-8")
        assert self._orphans(one) == []

        two = tmp_path / "two.py"
        two.write_text(
            'def f():\n    """Real."""\n    """Orphan."""\n    return 1\n',
            encoding="utf-8",
        )
        assert [line for line, _ in self._orphans(two)] == [3]

    def test_an_attribute_docstring_is_not_an_orphan(self, tmp_path: Path) -> None:
        # The must-not-fire half, stated as a test rather than only as prose:
        # this shape is a discarded expression too, and it is correct code.
        module = tmp_path / "attr.py"
        module.write_text(
            '"""Module."""\nTIMEOUT = 30\n"""How long to wait."""\n',
            encoding="utf-8",
        )
        assert self._orphans(module) == []

