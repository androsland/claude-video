#!/usr/bin/env python3
"""Shared /moviola configuration helpers."""
from __future__ import annotations

import os
from pathlib import Path


CONFIG_DIR = Path.home() / ".config" / "moviola"
CONFIG_FILE = CONFIG_DIR / ".env"

DEFAULT_DETAIL = "balanced"

# Declaration order is the cost progression, and `--help` renders it verbatim,
# so this is a tuple rather than a set. moviola.build_parser() reads it instead
# of repeating it: adding a value here adds it to the flag.
DETAILS = ("transcript", "efficient", "balanced", "token-burner")

# Which speech-to-text backend to use when a video has no caption track.
# "auto" resolves at runtime and is NOT simply "local, else Groq, else OpenAI":
# an unpinned run reads API keys from moviola's own config file only, because an
# ambient environment key is not consent to upload audio. Naming a backend here
# is that consent, and turns a missing prerequisite into an error instead of a
# silent fallback. whisper.resolve_backend() states the full rule and its limits.
DEFAULT_WHISPER = "auto"

WHISPER_BACKENDS = ("auto", "local", "groq", "openai")


def read_env_file(path: Path | None = None) -> dict[str, str]:
    if path is None:
        path = CONFIG_FILE
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
            value = value[1:-1]
        else:
            # Strip an inline comment (a '#' preceded by whitespace) from an
            # unquoted value. Without this, `MOVIOLA_DETAIL=balanced  # note`
            # parses as "balanced  # note", fails validation, and silently
            # falls back to the default. Keeps '#' inside quotes / API keys.
            for i, ch in enumerate(value):
                if ch == "#" and i > 0 and value[i - 1] in " \t":
                    value = value[:i].rstrip()
                    break
        values[key.strip()] = value
    return values


def _setting(file_values: dict[str, str], name: str, default: str = "") -> str:
    """Env var wins, then the config file, then the default.

    `or` rather than a None-check on purpose: a blank-but-present value (common
    when a key is scaffolded and never filled in) should fall through to the next
    source, not be honoured as an empty setting.
    """
    return (os.environ.get(name) or file_values.get(name) or default).strip()


def _truthy(value: str) -> bool | None:
    """"" -> None (unset), "0"/"false"/"no"/"off" -> False, anything else -> True."""
    if not value:
        return None
    return value.lower() not in ("0", "false", "no", "off")


def get_config() -> dict[str, object]:
    file_values = read_env_file()

    # What was discarded, as FACTS rather than a message. Both settings used to
    # fall back in silence, so a typo'd value resolved exactly like an unset one
    # and nothing anywhere said the difference. Formatting stays out of here on
    # purpose: this module imports only `os` and `pathlib` and has no output
    # channel, and giving it one would put a print inside a function the tests,
    # setup.py and the entry point all call. Each entry carries the value the
    # user actually wrote and the LIVE tuple it was checked against, so a caller
    # never re-derives either — re-deriving the allowed set in a second place is
    # the drift this exists to end, not a shape to repeat.
    rejected: list[dict[str, object]] = []

    detail = _setting(file_values, "MOVIOLA_DETAIL", DEFAULT_DETAIL)
    if detail not in DETAILS:
        rejected.append({
            "name": "MOVIOLA_DETAIL",
            "value": detail,
            "allowed": DETAILS,
            "fallback": DEFAULT_DETAIL,
        })
        detail = DEFAULT_DETAIL

    # Lowercased before validating, so MOVIOLA_WHISPER=LOCAL is a real pin.
    # hooks/scripts/check-setup.sh does the same, and did not until 2026-08-28 —
    # it read the pin case-sensitively, fell through to its unpinned arm, and
    # could announce an API backend to someone who had pinned `local`.
    raw_whisper = _setting(file_values, "MOVIOLA_WHISPER", DEFAULT_WHISPER)
    whisper = raw_whisper.lower()
    if whisper not in WHISPER_BACKENDS:
        rejected.append({
            "name": "MOVIOLA_WHISPER",
            # The value as the user wrote it, not the lowercased copy. They have
            # to find this string in their own config file to fix it.
            "value": raw_whisper,
            "allowed": WHISPER_BACKENDS,
            "fallback": DEFAULT_WHISPER,
        })
        whisper = DEFAULT_WHISPER

    return {
        "detail": detail,
        "whisper": whisper,
        # Empty whenever the configuration is valid, which is the state a caller
        # loops over without a special case.
        "rejected": tuple(rejected),
        # Empty means "let the local backend pick its own default" — validating
        # these here would mean hardcoding a model list that goes stale.
        "whisper_model": _setting(file_values, "MOVIOLA_WHISPER_MODEL"),
        "whisper_device": _setting(file_values, "MOVIOLA_WHISPER_DEVICE"),
        "whisper_compute": _setting(file_values, "MOVIOLA_WHISPER_COMPUTE"),
        "whisper_language": _setting(file_values, "MOVIOLA_WHISPER_LANGUAGE"),
        # Tri-state on purpose: "" means "not configured here, let the backend
        # read HF_HUB_OFFLINE from the environment", which is not the same as an
        # explicit "0". Only the local backend reads it.
        "whisper_offline": _truthy(_setting(file_values, "MOVIOLA_WHISPER_OFFLINE")),
        "config_file": str(CONFIG_FILE),
    }


def frame_cap(detail: str) -> int | None:
    if detail == "efficient":
        return 50
    if detail == "balanced":
        return 100
    if detail == "token-burner":
        return None
    if detail == "transcript":
        return None
    return 100
