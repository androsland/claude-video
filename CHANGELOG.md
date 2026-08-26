# Changelog

All notable changes to `/moviola` are documented here.

## [0.3.0] — 2026-08-26

### Added
- **On-device Whisper backend** — `pip install "faster-whisper>=1.0"` and transcription runs locally, with no API key and no audio leaving the machine. Uses CUDA when it is usable and falls back to CPU automatically; the fallback wraps the full transcription, not just model load, because CTranslate2 resolves its CUDA libraries lazily and a broken install only surfaces at the first matmul. pip-installed CUDA wheels (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) land outside the dynamic loader path, so their `.so` files are preloaded before the model is built.
- **`MOVIOLA_WHISPER`** — pin the backend to `auto` (default), `local`, `groq`, or `openai`; `--whisper local` does the same per-run.
- **`MOVIOLA_WHISPER_MODEL` / `MOVIOLA_WHISPER_DEVICE` / `MOVIOLA_WHISPER_COMPUTE` / `MOVIOLA_WHISPER_LANGUAGE`** — tune the local backend (model size or an HF repo id, `cpu`/`cuda`, quantization, forced language).
- `tests/test_local_whisper.py` — 84 tests covering availability detection, runtime resolution, CUDA preloading, VAD-vs-device error classification, backend precedence, dispatch, focused-range extraction, the `{start, end, text}` segment contract, progress reporting, and the cuda-to-cpu retry loop. The retry is tested in both shapes it has to survive: a failure at model load, and a failure part-way through the segment generator — the stub yields a segment and only then raises, which is what CTranslate2's lazy CUDA resolution actually does. The suite passes both with and without faster-whisper installed.
- `tests/test_every_backend_has_an_implementation.py` — every name `--whisper` offers must now reach code that can transcribe: a dispatch branch, a key lookup, a host entry, and an endpoint on that host. `auto` is asserted to have *no* implementation, because it is a sentinel meaning "no pin" and a branch for it would be the defect. The dispatch branches are read out of the AST rather than restated, so the check compares the offered set against the implemented one instead of comparing one literal to itself. It could not be written failing-first — all four names work today — and its docstring says so; its evidence is that six mutations, including a `deepgram` in the table and nowhere else and an `openai` branch copy-pasted from Groq's, each fail it.
- Tested against faster-whisper 1.2.1, CTranslate2 4.8.0, onnxruntime 1.23.2, huggingface-hub 1.19.0. The install command pins a floor (`>=1.0`) rather than that exact set — the floor guards against resolving a pre-1.0 release with an incompatible API, and is not a claim that every version above it was exercised.

### Changed
- **`--start` / `--end` now clip the audio before transcription.** `extract_audio` seeks on the input side (`-ss`/`-to` before `-i`) and segment timestamps are shifted back into source time, so a focused run transcribes ~30 seconds of audio instead of the whole video.
- **Setup treats local Whisper as a first-class way to satisfy the check.** `setup.py --check`, `--json`, and the `SessionStart` hook are now satisfied by *either* an API key or an importable `faster_whisper`, and the install path offers `pip install "faster-whisper>=1.0"` before the key placeholders.
- Backend resolution is local-first when unpinned: if faster-whisper is importable the audio never leaves the machine. `MOVIOLA_WHISPER=groq` or `openai` trades that for speed.
- The `SessionStart` hook no longer reads API key *values* into shell variables — it only tests for presence.

### Renamed

- **The skill and plugin are now `moviola`**, forked from `bradautomates/claude-video`.
  `/moviola` replaces `/watch`; config moves to `~/.config/moviola/.env` and the
  settings prefix is `MOVIOLA_*`.
- **The repository and the marketplace are now `moviola` too.** The fork is
  `androsland/moviola`, and the marketplace both manifests declare is `moviola`
  rather than `claude-video`. Two things follow for anyone who installed the
  earlier name: the install key is now `moviola@moviola`, and the plugin cache
  directory moves from `~/.claude/plugins/cache/claude-video/moviola/` to
  `~/.claude/plugins/cache/moviola/moviola/` — re-add the marketplace rather
  than expecting the old cache to be found. GitHub redirects the old repository
  URL, so existing clones keep fetching.

### Security

- **Remote text on stderr can no longer forge a line moviola wrote.** stderr goes to the
  same agent context the report does, and every line moviola writes there is identified
  only by its `[moviola] ` prefix — so an API error body carrying a line break ended the
  line it was interpolated into and handed the next one to whoever sent it. A new
  `scripts/untrusted.py` holds `stderr_line()`, which makes the two structural edits
  `md_inline` already made (line breaks collapse to spaces, unclosed bidi scopes are
  closed) and no more; it is not a sanitizer and the value is still reported in full.
  The error *body* is fenced where it is decoded, so one fence covers all four exits that
  print it. An HTTP response has a second remote half, though, and it needed its own pass:
  `str(HTTPError)` is `HTTP Error {code}: {reason}`, and `http.client` decodes the status
  line latin-1 and strips only its edges — so U+0085 and a bare CR survive into `.reason`
  and forge a line exactly as a body does. The three exits that interpolate the exception
  alongside the body are fenced individually, as is the failure line the local backend
  builds from a huggingface_hub exception. Three surfaces still carry remote text to stderr or stdout
  unfenced; all three are knowingly not covered and are documented as such: ffmpeg's captured stderr, which is legitimately multi-line and needs
  a block fence rather than a line fence, and which reaches the agent by two routes
  rather than one because `moviola.py`'s `except SystemExit` handler re-prints it; yt-dlp's output, which reaches stderr through an
  inherited file descriptor and never passes through this process at all; and `md_fence`
  on stdout, which escapes backtick runs correctly but applies no bidi balancing, so an
  override opened inside a hostile transcript still reorders display past the closing
  fence. All three are filed in `TODOS.md`.

- **The fence added above had a denial of service inside it, and the gate caught it.**
  `balance_bidi` matched a closer by scanning the open-scope stack from the top, so a
  closer that matched NOTHING walked the whole stack and deleted nothing: N openers of one
  kind followed by N closers of the other cost N squared. Measured, 32,000 characters —
  nothing for an HTTP response body — took eleven seconds of a synchronous process, and
  the growth does not stop there. It now keeps one index stack per closer kind, so
  matching is a pop and the cost is linear in the length of the value. Linear is not
  bounded, though, so the one call site with no bound of its own got one: the local
  backend's failure line embeds a `huggingface_hub` exception whose message carries the
  hub's entire response body, and it is now sliced to the same 400 characters
  `_read_error_body` uses before it is fenced. `whisper.py`'s `HTTPError` sites were never
  reachable this way — `http.client` decodes the status line latin-1, and no bidi control
  is representable below U+0100. The bound is each caller's responsibility at every site,
  and nothing enforces that, which is filed in `TODOS.md`.

- **An ambient API key is no longer consent to upload audio.** An unpinned run used to
  fall through to whatever `GROQ_API_KEY` or `OPENAI_API_KEY` it could see — including
  one exported for a different tool entirely — and send the audio. It now reads API keys
  from moviola's own config file only; pinning `MOVIOLA_WHISPER` (or `--whisper`) is the
  consent that restores the environment as a key source, and the no-backend hint says so.
- **`$PWD/.env` is not a key source, pinned or not.** Upstream reads it. For a plugin whose
  working directory is the user's checkout, a `.env` committed to a cloned repo would pick
  the provider account the audio is billed and disclosed to — and it was unreadable by
  both preflights, so every such upload was an unannounced one.
- **Every surface answers the consent question the same way.** `setup.py --check`, the
  `SessionStart` hook and the runtime resolver each had their own copy of the precedence
  rules and disagreed; a user who pinned `local` was told the API backend was what ran.
- **Untrusted values cannot forge report structure.** The video's title, uploader and the
  source string are fenced against every line break `str.splitlines` recognises — not just
  `\n` and `\r` — with a backtick run that cannot occur inside the value, a non-empty
  body, and every bidi override it opens closed before the span ends.
- **No audio leaves the machine without stderr having said so first.** The upload notice
  was proven correct and never proven to be printed; both call sites could be deleted with
  every test still passing.

### Fixed

- **The download's size limit no longer inverts itself on the fallback.** The video
  format selector bounded its first two rungs at 720p and then fell back to
  `bv*+ba/b` — *best* video, no bound — so an upload with no 720p-or-below rendition
  downloaded at whatever the maximum was, 4K included, on the flag whose whole purpose is
  staying small. The tail is now `wv*+ba/w`, which takes the smallest rendition a ladder
  offers instead of the largest. It carries no height bound and deliberately so: a
  bounded tail matches *nothing* on a ladder whose smallest rendition is 4K, and a yt-dlp
  selector that matches nothing fails the download outright rather than falling back, so
  the bounded version would have turned an oversized download into no download at all.
  The guarantee is therefore monotonic rather than absolute — no rung can pick something
  larger than the rung above it, and a 4K-only upload is still 4K because there is
  nothing else to fetch. A ladder that *does* offer 720p selects exactly what it always
  did, and best audio is kept wherever audio is a separate stream, since the transcript
  is made from it.
- **…and no longer downgrades a source whose renditions carry no height.** Review of the
  above caught that `[height<=720]` *drops* a format whose height is unknown rather than
  keeping it, so an HLS manifest with no `RESOLUTION` attribute — or anything from the
  generic extractor — skipped both bounded rungs and reached the unbounded tail on every
  run, not only when it was too big. With no height there is no floor either, so the tail
  took the smallest thing on offer: 6000 kbps down to 150 against yt-dlp's own selector,
  which is not a usable visual input for a tool whose entire output is frames. The same
  gap sent a video-less source (a podcast URL with no flags) from 256 kbps audio to 64,
  and on that path the audio *is* the transcript. The ladder gained an unknown-tolerant
  pair, `bv*[height<=?720]+ba/b[height<=?720]`, between the bounded rungs and the tail;
  those sources now resolve at their best, exactly as they did before any of this. Two
  test defects that hid the regressions were fixed with it: the harness pinned
  `incomplete_formats=False` where yt-dlp computes it from the formats, and the size
  proxy read pixel heights only, so it returned `0` for every pick on a heightless ladder
  and the monotonicity assertion evaluated `0 <= 0`.
- `AUDIO_FORMAT` is `ba` rather than `ba/bestaudio`. `bestaudio` is the long form of the
  same selector, so the second rung could not fire on any ladder where the first did not;
  its test now asserts the property (every rung asks for best audio) instead of the
  literal string.
- `tests/test_the_fallback_stays_small.py` — the selectors are now module-level
  `VIDEO_FORMAT` / `AUDIO_FORMAT` constants, and the test reads the ladder structurally
  (no unbounded best-video selector survives anywhere in it) as well as behaviourally,
  driving yt-dlp's own format selector over eight synthetic ladders with no network and
  running the previous string beside the current one so the before/after is executed
  rather than asserted. Seven mutations fail it, including the finding's own tail, the
  rejected 1080-bounded tail, a shrink that took the audio down with the video, and a
  `--format-sort` that would redefine what "worst" means.
- **A number out of a subprocess is now parsed as a string a stranger wrote.** ffprobe's
  `format.duration` and `format.size`, and yt-dlp's `info.json` `duration` on the path
  with no video to probe, were all handed to a bare `float()` / `int()`. A new
  `untrusted.finite_float` gives all three one guard: anything that is not a finite
  number becomes the caller's default, so an unparseable field degrades to "duration
  unknown" instead of ending a run that could have carried on with the frames it can
  still extract. Non-finite is rejected as well as non-numeric, because `float()` accepts
  `"nan"` and `"inf"` and the crash then lands two functions downstream in the frame-budget
  helper. **The reported trigger for this was wrong and is corrected here:** `N/A` is what
  ffprobe's *default* writer prints, while the JSON writer moviola actually asks for omits
  the key entirely — which the old code already handled — so the `ValueError` was not
  reachable through moviola's own command line. The guard ships as defence in depth
  (`-show_optional_fields always` puts the string into JSON, and the yt-dlp half has no
  writer guarantee at all), and two tests pin both halves of that contrast so the suite
  says so if it ever stops holding. The `or` chain it replaces had a second defect the
  report did not name: `"N/A"` is truthy, so an unparseable format duration was taken and
  the stream that knew the answer was never asked. Review of the guard itself found one
  class it still let through, and the guard raised the very exception it exists to
  prevent: a Python int has no maximum, and `float()` answers one too large for a double
  with `OverflowError` rather than `ValueError`. That is exactly the shape `json.loads`
  produces from a bare integer literal, so it is reachable on the yt-dlp half, where
  `info.json` is parsed as real JSON rather than read as strings. `OverflowError` joins
  the caught set and a 400-digit `duration` degrades to "unknown" like everything else.
- **A failed download could report on the previous run's video.** `--out-dir` is documented
  and reused, so "a file named `video.*` is in this directory" never meant "this run
  downloaded it". Right filename, wrong film, no error anywhere.
- **Spec-legal WebVTT parsed to zero cues.** The hours component is optional and may exceed
  two digits, so `01:30.000` and `100:00:00.000` both yielded nothing — and zero cues is
  indistinguishable from "this video has no captions", so moviola paid for a transcript
  that was already on disk.
- **Frames past 9999 carried other frames' timestamps.** `%04d` sets a minimum width, not a
  maximum, and `frame_10000.jpg` sorts lexicographically between `frame_1000.jpg` and
  `frame_1001.jpg`. Frames are paired to timestamps by position, so uncapped scene
  detection on a long video mislabelled everything from frame 10000 on.
- **A `Retry-After` header could park a run indefinitely.** It went straight to
  `time.sleep`; `Retry-After: 86400` is a real answer real services give. Every wait is now
  capped, and a negative or NaN header falls back to the exponential ladder instead of
  raising ValueError from inside the retry handler.
- **Audio chunk files were never deleted.** Chunking only happens above the upload cap, so
  the leak was proportional to the longest videos, and a reused `--out-dir` accumulated
  them across runs.
- **A malformed API response could cost the whole report.** A missing or non-list
  `segments` key raised instead of being reported as a transcription failure.

## [0.2.0] — 2026-06-29

### Added
- **`--detail` dial** with four modes — `transcript` (captions only, no frames), `efficient` (fast keyframe pass, cap 50), `balanced` (scene-aware, cap 100, default), and `token-burner` (scene-aware, uncapped). Set the default with `MOVIOLA_DETAIL` in `~/.config/moviola/.env`.
- **Frame deduplication** (default on; `--no-dedup` to disable). Before the budget cap, a pass downscales each frame to a 16×16 grayscale thumbnail and drops frames whose mean per-pixel difference from the last *kept* frame is within threshold — so the budget goes to distinct content instead of held slides and static recordings. The **Frames** report line shows how many near-duplicates were dropped.
- **Whisper auto-chunking.** Audio over the 25 MB upload cap is split into evenly sized chunks, transcribed per chunk, with segment timestamps shifted back into source time. Partial failures are tolerated — transcription only fails if *every* chunk fails, so length alone no longer breaks it.
- **`--timestamps T1,T2,…`** — grab a frame at each absolute timestamp; reserved against the cap, and the only frames produced under `--detail transcript`.
- **`--no-whisper`** — disable transcription entirely (frames only).
- pytest suite covering config, dedup, download, fixtures, frames, setup, timestamps, moviola, and whisper (no network; ffmpeg-synthesized clips).

### Changed
- **Restructured into a self-contained `skills/moviola/` package** so `SKILL.md` and its `scripts/` runtime are siblings in one folder. This fixes installs on Codex, Cursor, Copilot, and other Agent Skills hosts: `npx skills add` now copies the skill as a working unit instead of grabbing the root `SKILL.md` without its scripts.
- **Harness-agnostic path resolution** — `SKILL.md` resolves `$SKILL_DIR` from where it was Read instead of the Claude-Code-only `${CLAUDE_SKILL_DIR}`, so script calls work on every host.
- `/moviola` is now derived from `SKILL.md` frontmatter; the separate `commands/moviola.md` wrapper was dropped to avoid a duplicate slash command.
- `balanced` now full-decodes to detect every scene cut across the whole video. The previous early-exit was faster but kept only the first cuts and dropped the tail of long videos.
- `token-burner` is exempt from the long-video "sparse scan" warning, since it keeps every scene-change frame.
- `--max-frames` is now an override on top of each mode's default cap, rather than a fixed default of 80.

### Fixed
- Non-Claude installs (`npx skills add`) were dead on arrival — the installer copied `SKILL.md` without the `scripts/` it shells out to. The self-contained package layout resolves this.

### Removed
- `V2_PLAN.md` and `V2_CONCERNS.md` planning docs.

## [0.1.3] — 2026-05-09

### Fixed
- Windows: `video.info.json` is read as UTF-8 (#4). Previously `Path.read_text()` defaulted to cp1252 on Windows and crashed on yt-dlp's UTF-8 output, silently dropping Title/Uploader from the report. Same fix applied to `.env` reads/writes in `whisper.py` and `setup.py`.
- `download.py` now logs info.json parse failures to stderr instead of swallowing them.

### Security
- Hardened subprocess argv against option injection (#2): inserted `--` before the URL in the yt-dlp argv, and tightened `is_url` to reject `-`-prefixed sources and require a non-empty netloc. Resolved video/audio paths to absolute via `Path.resolve()` before passing to `ffmpeg`/`ffprobe`, so a relative path starting with `-` can't be misinterpreted as a flag.

## [0.1.2] — 2026-04-24

### Fixed
- Windows console crash: removed the emoji from the long-video warning in `moviola.py`; cp1252 consoles couldn't encode it.
- `setup.py` now prints `winget` / `pip` install commands on Windows instead of "unsupported platform" — matches what the README already promised.

### Changed
- `SKILL.md` notes that on Windows the scripts must be invoked with `python`, not `python3` (the latter is the Microsoft Store stub on Windows).

## [0.1.1] — 2026-04-24

### Fixed
- Added `commands/moviola.md` shim so `/moviola` is callable when installed as a Claude Code plugin. Without it, the plugin loaded but the skill wasn't exposed as a slash command.
- `scripts/build-skill.sh` now strips `commands/` from the claude.ai `.skill` bundle alongside `hooks/` and `.claude-plugin/`.

## [0.1.0] — 2026-04-24

Initial marketplace release.

### Added
- `/moviola <url-or-path> [question]` slash command.
- yt-dlp download with native caption extraction (manual + auto-subs).
- ffmpeg frame extraction with auto-scaled fps (≤2 fps, ≤100 frames, duration-aware budget).
- `--start` / `--end` focused mode with denser frame budget and transcript range filtering.
- Whisper fallback (Groq preferred, OpenAI secondary) for videos without captions.
- `setup.py` preflight: silent `--check`, structured `--json`, and installer that auto-runs `brew install` on macOS.
- Session-start hook that prints a one-line status on first run / partial config.
- `.skill` bundle packaging for claude.ai upload via `scripts/build-skill.sh`.
