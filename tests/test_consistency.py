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

REPO = Path(__file__).resolve().parent.parent
# The trailing exclusions matter: these strings live inside Python source, so a
# match runs into the backslash of an escaped quote (\\"faster-whisper>=1.0\\")
# unless the class stops there too.
PIN = re.compile(r"faster-whisper\s*([<>=!~]=?[^\"'`\\\s,)]*)")
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".sh", ".txt", ".yml", ".yaml"}


def _tracked_text_files() -> list[Path]:
    out = []
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if SKIP_DIRS & set(path.relative_to(REPO).parts):
            continue
        out.append(path)
    return out


def test_the_faster_whisper_pin_is_identical_everywhere():
    found: dict[str, list[str]] = {}
    for path in _tracked_text_files():
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
    for path in _tracked_text_files():
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
