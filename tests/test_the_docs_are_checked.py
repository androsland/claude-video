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
  * `_tracked_text_files` asks git, so every file-sweeping test here audits what
    the repository SHIPS. Local scratch is invisible to them by design — and so
    is a real problem introduced in an untracked file that is about to be added.
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
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import moviola
import whisper
from config import DETAILS, WHISPER_BACKENDS

import test_check_setup_hook

REPO = Path(__file__).resolve().parent.parent
SKILL_MD = REPO / "skills" / "moviola" / "SKILL.md"
README = REPO / "README.md"
CHANGELOG = REPO / "CHANGELOG.md"
MANIFESTS = [REPO / ".claude-plugin" / "plugin.json", REPO / ".codex-plugin" / "plugin.json"]

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".sh", ".txt", ".yml", ".yaml"}


def _tracked_text_files() -> list[Path]:
    """The repo's own text files, as git sees them.

    Asks git rather than walking the working tree, because the name is the whole
    point: every caller audits these files as CLAIMS THE REPOSITORY MAKES, and an
    untracked file makes no claim to anyone but the person who wrote it. The
    walk scanned local scratch — `SESSION.md` from the write-session plugin,
    `LOOP.md`, an editor backup, a downloaded sample — and reported findings
    against files that ship with nobody. It failed exactly that way here: a
    merge-commit line in `SESSION.md` quoting a branch name of the form
    `<owner>/chore/<slug>` parsed as a reference to a repository called
    `chore`, and the self-reference audit went red in a working tree while a
    clean checkout of the same commit stayed green.

    Note the shape of that false positive, because tracking does not remove it:
    the audit reads `<owner>/<word>` as a repo slug, and a branch name written
    in prose has the same shape. A tracked file quoting one — a CHANGELOG line,
    a runbook — would still trip it. Consulting git fixes the file SET, not the
    pattern.

    Falls back to the walk when git cannot answer — a source export, a vendored
    copy, a checkout with no `.git`. In that situation there is no untracked/
    tracked distinction to honour anyway, and a suite that refuses to run is
    worse than one that scans a little too much.
    """
    try:
        listed = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "-z"],
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8")
        candidates = [REPO / rel for rel in listed.split("\0") if rel]
    except (OSError, subprocess.CalledProcessError):
        candidates = list(REPO.rglob("*"))

    out = []
    for path in candidates:
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if SKIP_DIRS & set(path.relative_to(REPO).parts):
            continue
        out.append(path)
    return out


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


_CHANGELOG_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)


def _newest_changelog_version() -> str:
    """The version in the topmost `## [x.y.z]` heading of the CHANGELOG.

    Topmost rather than highest: the file is newest-first by convention, and
    sorting the numbers instead would quietly accept an entry filed in the wrong
    place. If the convention is ever broken, this should fail rather than paper
    over it.
    """
    match = _CHANGELOG_HEADING.search(CHANGELOG.read_text(encoding="utf-8"))
    assert match, "CHANGELOG.md has no `## [x.y.z]` heading"
    return match.group(1)


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
        every test here still passes. The README's claim that a `.skill` is
        downloadable from the latest release is checkable by nothing in this repo.
      * It does NOT prove the version was bumped for the change being shipped.
        A release that edits behaviour and moves neither the manifests nor the
        CHANGELOG is invisible to it — both halves are consistent at the old
        number.
      * It reads the NEWEST heading only. An older entry rewritten after the fact
        passes; `test_consistency.py` records one such case deliberately (the
        released 0.2.0 entry says 25 MB where the code says 24, and rewriting a
        shipped entry is the wrong fix).
      * It says nothing about the entry's CONTENT. A heading at the right number
        above a body describing different work is exactly as green as a correct one.
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


class TestNoUrlStillPointsAtTheRepositoryThisWasForkedFrom:
    """A fork's own badges pointing upstream send its users to someone else."""

    # Both the plain path form and the percent-encoded form that appears inside
    # query strings — the star-history image was correct and its LINK was not,
    # because the link encoded the slug and a grep for the plain form missed it.
    UPSTREAM = re.compile(r"bradautomates(/|%2F)claude-video", re.IGNORECASE)

    def test_no_tracked_file_links_to_the_upstream_repository(self) -> None:
        offenders = []
        for path in _tracked_text_files():
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
        for path in _tracked_text_files():
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
        for path in _tracked_text_files():
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
