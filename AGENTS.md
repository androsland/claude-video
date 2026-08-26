# moviola

Agent Skills package that gives an agent a video input. Installable across Claude Code (most common host), Codex, Cursor, GitHub Copilot, and 50+ other [Agent Skills](https://agentskills.io) hosts. Pure-stdlib Python that orchestrates `yt-dlp` + `ffmpeg` and an optional Whisper API.

## Structure

- `skills/moviola/SKILL.md` — canonical skill contract the model reads when `/moviola` fires. Source of truth for behavior across every host.
- `skills/moviola/scripts/moviola.py` — entry point; orchestrates download → frames → transcript.
- `skills/moviola/scripts/{download,frames,transcribe,whisper,setup,config}.py` — yt-dlp wrapper, ffmpeg frame extraction + auto-fps, caption/Whisper transcription, preflight/installer, shared config.
- `skills/moviola/scripts/build-skill.sh` — builds `dist/moviola.skill` for claude.ai upload (dev-only).
- `hooks/` — Claude Code SessionStart setup-status hook (Claude Code only).
- `.claude-plugin/` — `plugin.json` + `marketplace.json` (Claude Code plugin + local marketplace).
- `.codex-plugin/plugin.json` — Codex/agents manifest; `"skills": "./skills/"` points the Agent Skills CLI at the self-contained skill folder.
- `.agents/plugins/marketplace.json` — agents marketplace listing pointing at the repo-root plugin.
- `CLAUDE.md` → `@AGENTS.md` — generic-agent entry point.
- `tests/` — pytest suite (ffmpeg-synthesized clips; no network).

## Orientation

- The product is the slash-command-invoked skill (`/moviola <url-or-path> [question]`), not a CLI. `scripts/moviola.py` is implementation. Features must work across every harness the skill installs into, not just Claude Code.
- **The skill is one self-contained folder: `skills/moviola/`.** SKILL.md and `scripts/` are siblings inside it. This is what lets `npx skills add` copy a working skill as a unit — do NOT move SKILL.md or `scripts/` back to the repo root, or non-Claude installers will copy SKILL.md without the scripts.
- **Path resolution is harness-agnostic.** SKILL.md resolves `SKILL_DIR` as the directory of the SKILL.md the model just Read, then runs `${SKILL_DIR}/scripts/...`. Do NOT reintroduce `${CLAUDE_SKILL_DIR}` (Claude-Code-only) — it is unset on Codex/Cursor/agents and breaks every script call there.
- **No `commands/` wrapper.** `/moviola` is derived from SKILL.md frontmatter (`name: moviola` + `user-invocable: true`). A separate command file creates a duplicate slash command.

## Install surfaces

| Surface | Install |
|---------|---------|
| Claude Code | `/plugin marketplace add androsland/claude-video` then `/plugin install moviola@claude-video` |
| Codex / Cursor / Copilot / +50 | `npx skills add androsland/claude-video -g` |
| claude.ai (web) | upload `dist/moviola.skill` (built by `skills/moviola/scripts/build-skill.sh`) |

## Commands

```bash
# Tests (stdlib + pytest; ffmpeg required for frame tests)
.venv/bin/pytest -q                # or: python3 -m pytest -q

# Build the claude.ai upload bundle (archives skills/moviola/ as the bundle root)
bash skills/moviola/scripts/build-skill.sh   # → dist/moviola.skill

# Dev: mirror the working tree into the installed Claude Code plugin cache
./dev-sync.sh                       # --dry-run to preview
```

## Rules

- Keep the version in sync across `skills/moviola/SKILL.md` (frontmatter), `.claude-plugin/plugin.json`, and `.codex-plugin/plugin.json` when cutting a release.
- Releasing: tag `vX.Y.Z` and push the tag; `.github/workflows/release.yml` builds `dist/moviola.skill` and attaches it to the GitHub release.
- Never commit real API keys or `.env` contents; keys live in `~/.config/moviola/.env` (mode `0600`) at runtime.
