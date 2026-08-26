"""End-to-end routing of --detail through moviola.py on a local clip."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import moviola  # noqa: E402  (conftest puts scripts/ on sys.path)

MOVIOLA = Path(__file__).resolve().parent.parent / "skills" / "moviola" / "scripts" / "moviola.py"


class TestWhisperPinResolution:
    """--whisper vs MOVIOLA_WHISPER. `auto` is the case that had no expression."""

    def test_nothing_pinned_is_no_pin(self):
        assert moviola.resolve_whisper_choice(None, "auto") is None

    def test_the_config_pin_is_used_when_no_flag(self):
        assert moviola.resolve_whisper_choice(None, "groq") == "groq"

    def test_the_flag_wins_over_the_config(self):
        assert moviola.resolve_whisper_choice("local", "groq") == "local"

    def test_auto_on_the_flag_undoes_the_config_pin(self):
        # The gap this closes: --whisper could override MOVIOLA_WHISPER in every
        # direction except back to the default, because argparse rejected `auto`.
        assert moviola.resolve_whisper_choice("auto", "groq") is None

    def test_a_blank_config_value_is_not_a_pin(self):
        assert moviola.resolve_whisper_choice(None, "") is None

    def test_argparse_accepts_auto(self, cut_clip: Path):
        # Guards the choices list itself, which is where the rejection lived.
        _run(cut_clip, "--whisper", "auto", "--detail", "transcript")


def _run(clip: Path, *args: str, env_extra: dict | None = None) -> str:
    env = dict(os.environ)
    env.pop("MOVIOLA_DETAIL", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(MOVIOLA), str(clip), "--no-whisper", *args],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_efficient_uses_keyframe_engine(cut_clip: Path):
    out = _run(cut_clip, "--detail", "efficient")
    assert "(keyframe" in out
    assert "**Detail:** efficient" in out


def test_balanced_uses_scene_engine(cut_clip: Path):
    out = _run(cut_clip, "--detail", "balanced")
    assert "(scene" in out
    assert "**Detail:** balanced" in out


def test_token_burner_uses_scene_engine(cut_clip: Path):
    out = _run(cut_clip, "--detail", "token-burner")
    assert "(scene" in out


def test_transcript_skips_frames(cut_clip: Path):
    out = _run(cut_clip, "--detail", "transcript")
    assert "skipped" in out
    assert "frame_0000.jpg" not in out


def test_flag_overrides_env(cut_clip: Path):
    out = _run(cut_clip, "--detail", "efficient", env_extra={"MOVIOLA_DETAIL": "balanced"})
    assert "(keyframe" in out


def test_default_is_balanced(cut_clip: Path):
    out = _run(cut_clip)  # no flag, MOVIOLA_DETAIL cleared
    assert "**Detail:** balanced" in out
    assert "(scene" in out


def test_timestamps_add_cue_frames_to_detail(cut_clip: Path):
    out = _run(cut_clip, "--detail", "balanced", "--timestamps", "1,3")
    assert "reason=transcript-cue" in out
    assert "reason=scene-change" in out  # detail frames still present (additive)


def test_timestamps_with_transcript_detail_is_cue_only(cut_clip: Path):
    out = _run(cut_clip, "--detail", "transcript", "--timestamps", "1,3")
    assert "reason=transcript-cue" in out
    assert "reason=scene-change" not in out
    assert "reason=keyframe" not in out


def _frame_lines(out: str) -> int:
    return sum(1 for line in out.splitlines() if "/frames/frame_" in line and "(t=" in line)


def test_dedup_collapses_static_by_default(static_clip: Path):
    out = _run(static_clip)  # solid blue → identical frames collapse to one
    assert "near-duplicate" in out
    assert _frame_lines(out) == 1


def test_no_dedup_preserves_static_frames(static_clip: Path):
    out = _run(static_clip, "--no-dedup")
    assert "near-duplicate" not in out
    assert _frame_lines(out) > 1
