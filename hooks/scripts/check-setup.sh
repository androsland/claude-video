#!/usr/bin/env bash
# SessionStart hook for /moviola — one-line status so users know what's wired up.
# Silent on ready state to avoid spam. Points at the installer when something
# is missing.
set -euo pipefail

CONFIG_FILE="$HOME/.config/moviola/.env"

# Warn if anyone but the owner can reach the secrets file. The predicate is
# `mode & 0o077`, the same one whisper.warn_if_key_file_is_exposed applies, and
# it has to be arithmetic: this used to read `perms != "600" && perms != "400"`,
# a string comparison against two literals, so it warned about 700 — owner-only,
# with nothing to warn about — and any mode not spelled exactly those three
# digits was a warning whether or not it granted anyone anything.
if [[ -f "$CONFIG_FILE" ]]; then
  perms=$(stat -c '%a' "$CONFIG_FILE" 2>/dev/null || stat -f '%Lp' "$CONFIG_FILE" 2>/dev/null || echo "")
  if [[ "$perms" =~ ^[0-7]+$ ]] && (( 8#$perms & 8#77 )); then
    echo "/moviola: WARNING — $CONFIG_FILE has permissions $perms — other users on this machine can reach your API key."
    echo "  Fix: chmod 600 $CONFIG_FILE"
  fi
fi

# Report only WHETHER a key is set — never read its value into a variable.
# The single question asked here is "is one configured", and answering it with
# the secret itself puts it in the shell's memory and in awk's output for no gain.
have_key() {
  local name="$1"
  if [[ -n "${!name:-}" ]]; then
    echo "yes"
    return
  fi
  have_file_key "$name"
}

# The config-file half on its own. An UNPINNED run ignores the environment
# entirely (whisper.resolve_backend passes allow_env=False), so the auto branch
# below must ask this and not have_key — otherwise the hook announces an API
# backend that a real run would decline to use.
have_file_key() {
  local name="$1"
  if [[ -f "$CONFIG_FILE" ]]; then
    awk -F= -v k="$name" '
      /^[[:space:]]*#/ { next }
      # Trim $1 before comparing: read_env_file in config.py strips the line
      # first, so an indented key is honoured by every Python caller and was
      # invisible to this hook alone. (No apostrophes in here — the whole awk
      # program is a single-quoted shell string.)
      { key = $1; sub(/^[[:space:]]+/, "", key); sub(/[[:space:]]+$/, "", key) }
      key == k {
        sub(/^[[:space:]]*/, "", $2); sub(/[[:space:]]*$/, "", $2);
        gsub(/^["'\'']|["'\'']$/, "", $2);
        if ($2 != "") print "yes";
        exit
      }
    ' "$CONFIG_FILE"
  fi
}

# SETUP_COMPLETE is a marker, not a secret, so its value is read directly.
read_flag() {
  local name="$1"
  if [[ -n "${!name:-}" ]]; then
    echo "${!name}"
    return
  fi
  if [[ -f "$CONFIG_FILE" ]]; then
    awk -F= -v k="$name" '
      /^[[:space:]]*#/ { next }
      # Trim $1 before comparing: read_env_file in config.py strips the line
      # first, so an indented key is honoured by every Python caller and was
      # invisible to this hook alone. (No apostrophes in here — the whole awk
      # program is a single-quoted shell string.)
      { key = $1; sub(/^[[:space:]]+/, "", key); sub(/[[:space:]]+$/, "", key) }
      key == k {
        sub(/^[[:space:]]*/, "", $2); sub(/[[:space:]]*$/, "", $2);
        gsub(/^["'\'']|["'\'']$/, "", $2);
        print $2; exit
      }
    ' "$CONFIG_FILE"
  fi
}

# find_spec rather than a real import: this runs at every SessionStart, and
# importing faster-whisper pulls in CTranslate2 and its CUDA bindings. The
# trade-off is that find_spec answers "the module is on the path", not "it
# imports cleanly" — a broken install reads as present here. That is fine for
# a one-line status hint; setup.py --check and moviola.py both do a real import.
has_local_whisper() {
  command -v python3 >/dev/null 2>&1 || return 1
  python3 -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('faster_whisper') else 1)" 2>/dev/null
}

HAS_FFMPEG=""
HAS_YTDLP=""
command -v ffmpeg >/dev/null 2>&1 && HAS_FFMPEG="yes"
command -v yt-dlp >/dev/null 2>&1 && HAS_YTDLP="yes"

HAS_GROQ="$(have_key GROQ_API_KEY)"
HAS_OPENAI="$(have_key OPENAI_API_KEY)"
FILE_GROQ="$(have_file_key GROQ_API_KEY)"
FILE_OPENAI="$(have_file_key OPENAI_API_KEY)"
SETUP_COMPLETE="$(read_flag SETUP_COMPLETE)"
WHISPER_PIN="$(read_flag MOVIOLA_WHISPER)"
WHISPER_PIN="${WHISPER_PIN:-auto}"
# config.get_config lowercases this before validating it, so MOVIOLA_WHISPER=LOCAL
# is a real pin to every Python caller. Reading it case-sensitively here sent it
# to the *) arm below — the UNPINNED resolution — so a machine with a config-file
# groq key and no faster-whisper was told "ready via the groq API" while a real
# run would refuse to upload. That is the same lie the pin fix above closed,
# reached by a different route.
WHISPER_PIN="$(printf '%s' "$WHISPER_PIN" | tr '[:upper:]' '[:lower:]')"

# Whether the pin NAMES a backend at all, which is a different question from
# whether that backend can run here, and used to share one message with it.
# An unrecognised value is dropped by get_config and resolves as if unset, so
# "install it, or set the matching API key" is advice about a backend that does
# not exist. The four names are cross-pinned to config.WHISPER_BACKENDS by
# tests/test_check_setup_hook.py, so adding one there and not here fails.
WHISPER_KNOWN=""
case "$WHISPER_PIN" in
  auto|local|groq|openai) WHISPER_KNOWN="yes" ;;
esac
if [[ -z "$WHISPER_KNOWN" ]]; then
  echo "/moviola: MOVIOLA_WHISPER=$WHISPER_PIN is not a backend name, so it is ignored and moviola resolves as if nothing were pinned. Recognised values: auto, local, groq, openai."
fi

# Fully configured → silent (Claude can surface status on demand via --check).
# The notice above deliberately precedes this, the way the permissions warning at
# the top of the file does: "fully configured" means there is no STATUS to report,
# and a setting that is being ignored is news whatever the status is. It is also
# the only surface that tells a user about their config file without them running
# anything.
if [[ "$SETUP_COMPLETE" == "true" && -n "$HAS_FFMPEG" && -n "$HAS_YTDLP" ]]; then
  exit 0
fi

# Which backend would ACTUALLY run — same precedence as whisper.resolve_backend():
# an explicit MOVIOLA_WHISPER pin wins outright and is not silently replaced when
# it turns out to be unusable, and an unpinned machine resolves local-first.
# This used to announce "ready" on the strength of a key alone, ignoring the pin
# entirely, so a user who pinned local was told the API backend was what ran.
#
# The unpinned branch reads FILE_* rather than HAS_*: without a pin, a key is
# only consent when it is in moviola's own config file, so an ambient
# environment key must not make this hook say "ready".
#
# has_local_whisper spawns python3, so it is called only on the branch that needs
# it — this hook runs at every SessionStart. It uses find_spec rather than a real
# import, which is why the local line says "installed" and not "ready": find_spec
# proves the package is on the path, not that importing it works. A half-installed
# CTranslate2 or a numpy ABI mismatch passes this probe and fails at the first
# transcription. local_whisper.is_available() does the real import and reports the
# error verbatim; paying 200-odd ms for that at every SessionStart is the trade
# this hook declines, so the sentence is written to claim only what was checked.
BACKEND=""
case "$WHISPER_PIN" in
  local)
    if has_local_whisper; then BACKEND="local"; fi
    ;;
  groq)
    if [[ -n "$HAS_GROQ" ]]; then BACKEND="groq"; fi
    ;;
  openai)
    if [[ -n "$HAS_OPENAI" ]]; then BACKEND="openai"; fi
    ;;
  *)
    if has_local_whisper; then
      BACKEND="local"
    elif [[ -n "$FILE_GROQ" ]]; then
      BACKEND="groq"
    elif [[ -n "$FILE_OPENAI" ]]; then
      BACKEND="openai"
    fi
    ;;
esac

# First-run / partially-configured → one-line hint.
if [[ -z "$HAS_FFMPEG" || -z "$HAS_YTDLP" ]]; then
  echo "/moviola: needs ffmpeg + yt-dlp. Run \`python3 \$CLAUDE_PLUGIN_ROOT/skills/moviola/scripts/setup.py\` once to install and scaffold config."
elif [[ "$BACKEND" == "local" ]]; then
  echo "/moviola: faster-whisper is installed — transcription runs on this machine, no API key needed."
elif [[ -n "$BACKEND" ]]; then
  echo "/moviola: ready — transcription via the $BACKEND API."
elif [[ -n "$WHISPER_KNOWN" && "$WHISPER_PIN" != "auto" ]]; then
  echo "/moviola: MOVIOLA_WHISPER=$WHISPER_PIN is pinned but that backend is not usable here, so videos without captions get frames only. Either install it (\`pip install \"faster-whisper>=1.0\"\` for local, or set the matching API key in ~/.config/moviola/.env) or unset the pin."
elif [[ -n "$HAS_GROQ" || -n "$HAS_OPENAI" ]]; then
  ambient="groq"; [[ -n "$HAS_OPENAI" && -z "$HAS_GROQ" ]] && ambient="openai"
  echo "/moviola: ready for videos with native captions. An API key is set in this environment, but an unpinned run will not upload audio on the strength of an environment variable alone — set MOVIOLA_WHISPER=$ambient in ~/.config/moviola/.env to opt in, or \`pip install \"faster-whisper>=1.0\"\` to transcribe on this machine."
else
  echo "/moviola: ready for videos with native captions. For the rest, either \`pip install \"faster-whisper>=1.0\"\` (runs locally, no key) or add GROQ_API_KEY / OPENAI_API_KEY to ~/.config/moviola/.env."
fi
