"""Cross-file invariants — things no single module can hold on its own.

These are the checks that a shared constant would normally cover. It cannot
here: the install command is printed from six runtime messages, but the same
version pin also lives in README.md, SKILL.md, CHANGELOG.md and the `.env`
template that setup.py scaffolds into the user's home directory — prose that
no Python constant can reach. Extracting a constant would fix six of nineteen
sites while adding a module-level import of `local_whisper` to `setup.py` and
`whisper.py`, where that import is deliberately lazy and wrapped in
`except Exception: return False` so a broken install degrades to "not
available" instead of crashing the preflight. So the invariant is enforced by
reading the files instead of by refactoring toward the constant.

Deliberately NOT covered: whether the pin is the RIGHT version. This checks
that nineteen copies agree, not that they agree on something correct.
"""
from __future__ import annotations

import re
from pathlib import Path

from repo_files import tracked_text_files

REPO = Path(__file__).resolve().parent.parent
# The trailing exclusions matter: these strings live inside Python source, so a
# match runs into the backslash of an escaped quote (\\"faster-whisper>=1.0\\")
# unless the class stops there too.
PIN = re.compile(r"faster-whisper\s*([<>=!~]=?[^\"'`\\\s,)]*)")


def test_the_faster_whisper_pin_is_identical_everywhere():
    found: dict[str, list[str]] = {}
    for path in tracked_text_files():
        for pin in PIN.findall(path.read_text(encoding="utf-8", errors="replace")):
            found.setdefault(pin, []).append(str(path.relative_to(REPO)))
    assert found, "no faster-whisper pin found at all — did the spelling change?"
    assert len(found) == 1, (
        "the faster-whisper version pin has drifted:\n"
        + "\n".join(f"  {pin}: {sorted(set(files))}" for pin, files in sorted(found.items()))
    )


def test_the_pin_is_quoted_wherever_it_is_a_shell_command():
    """`pip install faster-whisper>=1.0` unquoted makes the shell truncate the
    file `faster-whisper` and install the unpinned package. Every site that
    prints an install command has to carry the quotes."""
    unquoted = []
    for path in tracked_text_files():
        if path.name == Path(__file__).name:
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for match in re.finditer(r"pip3?\s+install\s+(\S+)", line):
                target = match.group(1)
                # A bare, PINNED spec is the hazard. Anything already wrapped —
                # "faster-whisper>=1.0", or \"faster-whisper>=1.0\" inside Python
                # source — does not start with the package name and is skipped.
                # Prose that says `pip install faster-whisper` with no version is
                # a legitimate sentence, not a shell hazard, and must not trip
                # this either.
                if target.startswith("faster-whisper") and PIN.search(target):
                    unquoted.append(f"{path.relative_to(REPO)}:{line_no}: {line.strip()[:100]}")
    assert not unquoted, "unquoted pip install of a pinned spec:\n" + "\n".join(unquoted)


ENV_TEMPLATE_RE = re.compile(r'ENV_TEMPLATE = """(.*?)"""', re.S)
# Only a NAME= at the start of a line, commented or not — that is the shape a
# user copies. A name mentioned mid-sentence in prose is not a setting.
SETTING_RE = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]+)=")

# Named by the template but read by whisper.py's load_api_key._from_dotenv
# rather than by config.get_config, so the spy below never sees them asked for.
READ_FROM_THE_FILE_ELSEWHERE = {"GROQ_API_KEY", "OPENAI_API_KEY"}


def test_every_setting_the_env_template_names_can_be_set_from_that_file(monkeypatch, tmp_path):
    """A setting the scaffolded `.env` tells you to write is useless unless
    something reads it back out of that file.

    `HF_HUB_OFFLINE=1` was recommended in the template's own comment while
    nothing read it from there: config.py reads `~/.config/moviola/.env` for
    MOVIOLA_* keys and exports nothing to the process environment, so
    huggingface_hub never saw it and the revision check the user had just
    turned off kept firing — silently, which is the whole problem.

    Two things this deliberately does not do. It must NOT fire on
    MOVIOLA_WHISPER_CPU_THREADS: local_whisper.py reads that from the process
    environment only, so its absence from the template is correct and adding it
    there would be the bug rather than the fix. And it reads settings, not
    prose — a name that appears mid-sentence inside a comment is invisible to
    it, so the template can still explain a variable it is telling you not to
    set here.
    """
    source = (REPO / "skills" / "moviola" / "scripts" / "setup.py").read_text(encoding="utf-8")
    template = ENV_TEMPLATE_RE.search(source)
    assert template, "ENV_TEMPLATE is no longer a plain triple-quoted literal"

    named = set()
    for line in template.group(1).splitlines():
        match = SETTING_RE.match(line)
        if match:
            named.add(match.group(1))
    assert named, "the template names no settings at all — did its format change?"

    import config  # on sys.path via conftest

    asked: list[str] = []
    real_setting = config._setting

    def spy(file_values, name, default=""):
        asked.append(name)
        return real_setting(file_values, name, default)

    monkeypatch.setattr(config, "_setting", spy)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "absent.env")
    config.get_config()

    unread = sorted(named - set(asked) - READ_FROM_THE_FILE_ELSEWHERE)
    assert not unread, (
        "the .env template tells the user to set names nothing reads back from "
        "that file, so writing them there is a silent no-op:\n"
        + "\n".join(f"  {name}" for name in unread)
    )


# The cost notice's two numbers live in whisper.py as constants and in prose that
# no constant reaches. Same shape as the version pin above, same remedy.
#
# NOT covered, and the distinction is the whole reason this is anchored on exact
# phrases rather than sweeping for "N MB": the providers' own cap is 25 MB and
# ours is 24, deliberately, so `whisper.py:40` and SKILL.md's "the API's 25 MB
# upload cap" are correct while naming a different number. Nothing here can tell
# which cap a sentence means — it only reads the two phrasings that are ours.
COST_PROSE = {60: "past an hour of audio"}
OUR_CAP = re.compile(r"(\d+) MB (?:upload cap|split)")


def test_the_cost_warning_threshold_matches_the_prose_in_skill_md():
    import whisper

    expected = COST_PROSE.get(whisper.COST_WARN_MINUTES)
    assert expected is not None, (
        f"COST_WARN_MINUTES is {whisper.COST_WARN_MINUTES}; SKILL.md's cost "
        f"paragraph is written for one of {sorted(COST_PROSE)}. Update the prose "
        "and add the new phrasing to COST_PROSE."
    )
    skill = (REPO / "skills" / "moviola" / "SKILL.md").read_text(encoding="utf-8")
    assert expected in skill


def test_our_own_upload_split_size_is_the_same_number_in_code_and_prose():
    import whisper

    expected = str(whisper.MAX_UPLOAD_BYTES // (1024 * 1024))
    source = (REPO / "skills" / "moviola" / "scripts" / "whisper.py").read_text(
        encoding="utf-8"
    )
    found = set(OUR_CAP.findall(source))
    assert found, "no prose names our split size — the anchor phrases moved"
    assert found == {expected}, f"prose says {sorted(found)} MB, code says {expected} MB"
