# TODOS

Deferred work and known issues. Anything not done lives here, not in a PR body.

## Local Whisper backend

- **No manifest means dependency CVE scanning has nothing to read.** `faster-whisper` is introduced only as a string in `setup.py`'s output and a lazy `import` — there is no `requirements.txt`, `pyproject.toml` or lockfile in the repo, which is correct for a project whose runtime is otherwise pure stdlib, but it means a manifest-driven scanner (`trivy fs`, `osv-scanner`) has no artifact to point at and will report clean without having checked anything. The dependency chain to check by hand is `faster-whisper` -> `ctranslate2`, `onnxruntime`, `huggingface-hub`, `tokenizers`, `av`. Do not read a clean scan of this repo as a clean bill for that chain. (supply-chain review, 2026-08-26)

- **No test loads a real Whisper model.** `tests/test_local_whisper.py` now drives `_collect()`, `_run()`'s VAD fallback and `transcribe_local()`'s cuda-to-cpu retry against stub objects shaped like faster-whisper's `Segment`/`TranscriptionInfo`, so the segment contract and the fallback loop are covered; seven mutations were each confirmed to fail the suite (segment rounding, the dropped CPU retry, kept-empty-text, the progress catch-up loop, the language-pin guard, the progress line's own format, and moving the drain outside the retry's `try` — that last one fails only the fail-mid-drain test, which is what proves the two fallback tests exercise different paths). What remains uncovered is the real library boundary: if faster-whisper renames an attribute or changes `WhisperModel(...)`/`transcribe(...)`'s signature, the stubs keep matching the old shape and the suite stays green. Closing that needs a real model load, which means a multi-hundred-MB download in a suite that is otherwise network-free. Verified by hand instead: `large-v3` int8_float16 on a GTX 1650 Ti transcribed a 38.6 s clip in 22 s including model load. If a CI runner ever gets a model cache, add a `tiny`-model smoke test behind an opt-in marker. **Corrected 2026-08-26:** this entry originally said "CI stays green" and "If CI ever gets a model cache" as though a CI ran the suite. None does — `release.yml` on tag push is the whole of `.github/workflows/`, and it has never executed once in this repository (no workflow run of any kind exists), so the only thing that has ever run these 564 tests is somebody's terminal. The wording is fixed above and the gap is its own entry under `## Documentation as a checked claim`. (ai-output review, 2026-08-26)

- **`MOVIOLA_WHISPER_MODEL` accepts an arbitrary Hugging Face repo id or path with no validation beyond what `huggingface_hub` does.** That is deliberate — it is how anyone uses a fine-tune or a local conversion — but it means a typo'd or hostile repo id is fetched and loaded on the user's behalf. Documented as a non-goal in SKILL.md's security section rather than fixed. (local-whisper branch, 2026-08-26)

- **`sys.path.insert(0, SCRIPT_DIR)` in `moviola.py:16` shadows the top-level `whisper` module of the `openai-whisper` package.** Our own `skills/moviola/scripts/whisper.py` wins for the lifetime of that process. Checked and accepted rather than fixed: of the eight modules in that directory (`config`, `download`, `frames`, `local_whisper`, `moviola`, `setup`, `transcribe`, `whisper`) **none** collide with a stdlib module name, and neither `faster_whisper` nor `ctranslate2` imports a bare `whisper` — so nothing this backend actually loads is affected. The shadow bites only a future dependency that imports `openai-whisper` by its top-level name from inside our process. The fix is renaming our module (`transcription.py`) and updating every import and test, which is a wide rename not worth doing mid-stack. Revisit if a dependency ever needs the real `whisper`. (whisper-runtime branch, 2026-08-26)

- **`_preload_cuda_libs()` is POSIX-only and does not support Windows.** pip's CUDA wheels put DLLs under `nvidia/*/bin` rather than `*/lib`, and Windows resolves them through `os.add_dll_directory()` rather than `ctypes.CDLL(..., RTLD_GLOBAL)` — a different mechanism, not a missing glob pattern. The POSIX path finds nothing there, so a Windows user with pip CUDA wheels falls back to CPU: correct, just slower. Not ported blind because it cannot be tested from this machine. Stated as an explicit non-goal in the function's docstring so the gap does not read as coverage. (whisper-runtime branch, 2026-08-26)

- **A dropped VAD filter is announced on stderr but leaves no mark in the report.** `_run()` prints `[moviola] VAD filter unavailable (...)` and retries without it, so the transcript is produced with silence-trimming off — usually a few extra empty-ish segments, occasionally noticeably more. Anyone reading the report later, or an agent consuming it, has no way to know. Surfacing it means threading a flag out of `_run()` through `transcribe_local()` and `whisper.py` into the report builder, which is invasive for a cosmetic marker. (whisper-runtime branch, 2026-08-26)

- **Progress seconds are clip-relative on a `--start`/`--end` run.** `_collect()` reports `{pct}% ({seg.end}s/{total}s)` from the segment stream of the extracted audio, so on a clip from 10:00 to 12:00 it counts 0s..120s rather than 600s..720s. The **percentage is correct** — both numbers come from the same clip — and the transcript timestamps are already shifted back into source time by `shift_segments()`; only the two absolute seconds in the progress line are clip-local. Fixing it means passing the offset down into `_collect()` purely for a log line. (whisper-runtime branch, 2026-08-26)

## Consent and key handling

- **The dotenv format is parsed by four independent implementations in two languages.** `whisper.py::_from_dotenv`, `setup.py::_read_env_key`, `config.py::read_env_file`, and the awk program in `hooks/scripts/check-setup.sh` (which appears twice in that file, byte-identical, under byte-identical comments). They already disagreed once — the hook was blind to an indented key until its awk learned to trim `$1`, which every Python caller had always honoured — and that is the mechanism behind the whole oracle-drift class this branch closes: four parsers cannot be kept in agreement by care, only by being one parser. The Python three can collapse into `config.read_env_file`; the bash one cannot, and that is the part worth stating as permanent. `tests/test_consent_oracles.py` compares the surfaces' ANSWERS, not their parsers, so a format they all get wrong the same way still passes. (consent-chain branch, 2026-08-26)

- **The key-file permission warning reads mode bits and nothing else.** `warn_if_key_file_is_exposed` tests `mode & 0o077`, which is blind to three real exposures: the containing directory's mode, a POSIX ACL granting access the mode never mentions, and a filesystem that does not implement modes at all. The last one is not hypothetical here — a checkout or a `$HOME` on `/mnt/c` under WSL reports whatever the driver invents and `chmod` is a no-op there, so an exposed key on a Windows drive reports clean and always will. Written into the function's docstring and `tests/test_key_file_permissions.py` as an explicit non-goal so a green run is not read as "the key is safe". Closing any of it needs a different check, not a wider mask. (consent-chain branch, 2026-08-26)

- **Dropping `$PWD/.env` removed the per-project-key workflow and put nothing in its place.** A user who kept a different provider key per checkout now has two options: move the key into `~/.config/moviola/.env`, which is global, or pin `MOVIOLA_WHISPER` and export the key into the environment, which is per-shell rather than per-project. That is the intended trade — the working directory is the user's checkout by construction for a Claude Code plugin, so that file may have been committed by someone they have never met — but it is a real loss, not an oversight. If it needs restoring, the shape is an explicit `--env-file` argument, which is consent by being typed. (consent-chain branch, 2026-08-26)

- **The SessionStart hook re-derives the consent precedence in bash and nothing structural keeps it aligned.** `check-setup.sh` reimplements local-first resolution, the file-only-when-unpinned rule, and the mode predicate, because a shell hook cannot import the Python that owns them. `tests/test_consent_oracles.py` and `tests/test_key_file_permissions.py` drive all three surfaces and hold them to one table, which catches drift but does not prevent it — and by construction they compare the surfaces to each other, so a change that moves all three the same wrong way passes. The only structural fix is deleting the bash implementation and having the hook shell out to `setup.py --json`, which costs a Python start on every SessionStart; that is the trade to weigh, not a bug to fix. (consent-chain branch, 2026-08-26)

- **Nothing establishes that the upload notice is ever seen.** `tests/test_upload_is_announced.py` proves the sentence is written to stderr before the first request and in the right order, which is the half that was missing. Whether an agent harness surfaces stderr to the human, buffers it until after the run, or discards it entirely is invisible from inside this repo, and no test here can see it. A notice that is printed and swallowed is not consent. (consent-chain branch, 2026-08-26)

- **The consent this program asks for is the operator's, not the recorded speakers'.**
  (forgeward privacy review, 2026-08-26) Every consent surface here answers "may this
  machine send YOUR audio to a third party". A video's audio carries other people's
  voices, and in some jurisdictions a voiceprint is biometric data with its own basis
  requirement — which the person running moviola cannot give on their behalf. Nothing
  in code fixes this; it is a note for whoever decides how the tool is used and, if it
  is ever used at scale, for counsel. Recorded so a green privacy review is not read as
  a statement about the speakers.

## Report as an untrusted document

- **ffmpeg's and ffprobe's captured stderr is remote-controlled and still unfenced.**
  (stderr review, 2026-08-26) `whisper.py:330`/`:373`/`:439` and `frames.py:136`/`:235`/
  `:299`/`:654` interpolate `result.stderr.strip()` into a `SystemExit` message. That is a
  banner echoing container metadata the video's author wrote, so it is as remote as the
  API bodies `stderr_line` now fences — but unlike them it is legitimately MULTI-LINE,
  and collapsing a forty-line diagnostic into one line destroys the only reason it is
  printed. So it was left alone rather than half-fixed: this surface needs a fenced
  BLOCK (a delimiter, or a prefix on every line) and not `stderr_line`. Deciding that
  shape is the work; the sites are already enumerated above. **Two of the seven are the
  live vector and five are conditional** (security review, 2026-08-26): `frames.py:299`
  and `:654` run ffmpeg at `-loglevel info`, which prints the container's
  `Metadata: title/comment` block verbatim on every run, so the author's text reaches
  stderr whether or not anything failed. The other five run at `-loglevel error` and
  carry it only when ffmpeg quotes the value back inside an error message. Start with
  the two. The three `whisper.py` sites also reach the agent a SECOND way, re-printed
  by `moviola.py:396`; fencing them at the raise closes both routes, and fencing the
  re-print site closes neither properly — see the entry on that below.

- **yt-dlp's own output is structurally unreachable from inside this process.** (stderr
  review, 2026-08-26) `download.py:133` and `:209` run `subprocess.run(cmd,
  stdout=sys.stderr, stderr=sys.stderr)`, so yt-dlp inherits the file descriptor and
  writes to it directly. Not one of those bytes passes through Python, and no helper
  that edits an interpolated value can touch them — `stderr_line` covers exactly zero of
  the largest volume of remote text on this program's stderr. Fixing it means capturing
  the pipes and re-emitting them, which costs the live progress display yt-dlp gives a
  human watching a long download. Recorded as a known hole, not a plan.

- **Nothing pins SKILL.md's "Bundled scripts:" list against `scripts/`.** (stderr
  review, 2026-08-26) The list sits under "Review scripts before first use", so a
  missing entry is a security-relevant omission rather than a typo — and it was already
  wrong: `config.py` had never been listed. Found by hand while adding `untrusted.py`,
  which is exactly the drift a test would have caught. The shape is a test asserting the
  set of `scripts/*.py` equals the set the sentence names, with `build-skill.sh` (dev-
  only, `export-ignore`d) as the one deliberate exclusion. **Widened: there are THREE
  lists of that set, not one** (stderr review follow-up, 2026-08-26) — `SKILL.md`'s
  sentence, `AGENTS.md`'s `## Structure` bullets, and `README.md`'s `## Structure` tree.
  Adding `untrusted.py` had to touch all three by hand, and in the same pass `AGENTS.md`
  was found still missing `local_whisper.py` from a release earlier. One test should
  assert all three agree with the directory, because a fix that pins only the
  security-relevant list leaves two others free to drift and read as authoritative.
  Non-goal: it must NOT require the three to be worded identically — the tree, the
  bullets and the sentence describe the same set in deliberately different shapes, so
  the assertion is over the extracted set of filenames, never over the prose.

- **`md_fence` closes a backtick run and balances no bidi at all.** (stderr review,
  2026-08-26) `moviola.py:105` picks a fence the body cannot close early and correctly
  preserves line breaks — that is the whole point of a block. What it never gained is
  the second edit `md_inline` makes: a bidi override opened inside a hostile transcript
  is never closed, so it keeps reordering the display of every heading the report writes
  after the block, and the closing fence does not stop it because the control is still
  in the character stream. The fix is not `stderr_line` — that collapses line breaks and
  would destroy the transcript. It is `balance_bidi` applied to the body with the
  terminators appended before the closing fence, which needs a decision about whether
  appending inside a fenced block is acceptable rendering. Filed rather than done
  because the stderr branch had no business changing what stdout emits.

- **`stderr_line` closes bidi scopes and does nothing about ANSI, OSC, or the implicit
  marks — weighed, not overlooked.** (stderr review, 2026-08-26) SKILL.md documents
  running these scripts directly and `whisper.py` has a `__main__`, so a human at a
  terminal is a reachable reader. Three families are untouched: CSI sequences (`ESC[F`,
  `ESC[2K`, `ESC[2J`) move the cursor and erase, so a remote value can repaint lines
  already written; OSC 8 retargets a hyperlink and OSC 52 writes the viewer's clipboard;
  and U+200E/U+200F/U+061C reorder the run that follows them without opening a scope, so
  `balance_bidi` is structurally blind — there is nothing to close. Closing any of these
  means stripping or escaping, and `untrusted.py` is deliberately not a sanitizer: the
  value is reported in full because a caller debugging a failed request needs to read
  what the server actually said. Recorded as a known hole with a stated reason, not a
  plan. Non-goal: none of these forge a LINE, which is the property the tests pin, so
  this is not a gap in the fix that shipped.

- **`moviola.py:396` re-prints ffmpeg's captured stderr, which is a second path to a
  surface already known to be uncovered.** (security gate, 2026-08-26) the handler at
  `moviola.py:395` is `except SystemExit as exc:` and its body prints `{exc}` raw at
  `:396`. `extract_audio`
  (`whisper.py:330`), `audio_duration` (`:373`) and `split_audio` (`:439`) each raise
  `SystemExit` with `result.stderr.strip()` embedded, and all three are called from
  `transcribe_video`'s body (`:900`, `:920`, `:929`) — the only `except SystemExit` in
  `whisper.py` is at `:793` and guards `transcribe_one()`, the upload path, which is
  disjoint from them. So ffmpeg's and ffprobe's captured stderr reaches the agent context
  through this handler on any extraction, probe or split failure during a Whisper
  fallback, multi-line and unfenced. That value is already the first of the three
  surfaces the CHANGELOG names as knowingly uncovered; what was missing is that it
  arrives by two routes, and only the direct one was enumerated. **Do not fence it here.**
  `stderr_line` collapses line breaks to spaces, and collapsing a forty-line ffmpeg
  diagnostic into one line destroys the only reason it is printed. The fix belongs at the
  three raise sites, with whatever block-safe fence the ffmpeg-stderr work adopts —
  fixing there covers this handler, every other caller of those three functions, and the
  case where the `SystemExit` is not caught at all and the interpreter prints it.

- **Four stderr sites interpolate an exception raised while handling remote data, and the
  channel is latent rather than live.** (security gate, 2026-08-26) `download.py:168`
  (`info.json parse failed`), `moviola.py:233` and `:371` (`subtitle parse failed`), and
  `moviola.py:403` (`whisper fallback failed`, the `except Exception` arm) each print
  `{exc}` raw. A gate reviewer first flagged three of these as already-itemized uncovered
  surfaces; they are itemized nowhere, so they were audited from scratch. Every exception
  class reachable at these four builds its message from fixed strings and numbers:
  `json.JSONDecodeError` draws `msg` from a fixed internal set and appends
  line/column/char; `UnicodeDecodeError` reports a byte value and a position; the
  `ValueError` from `int()` on a huge VTT hours field says `Exceeds the limit (4300
  digits)` without quoting the digits; and a `KeyError` on a remote JSON object would name the
  key *this program* asked for rather than one the server chose. That last class is
  named for the shape of the audit and is **not** currently reachable. Establishing
  that took two wrong drafts of this entry — the first asserted the class flatly, the
  second cited `whisper.py:706-708` as a live path, and the third scoped the invariant
  to that one site. Segment dicts are subscripted rather than `.get()`-ed at three
  places, not one, and they do not share a set of feeders: `shift_segments`
  (`whisper.py:706-708`) sees only Whisper output; `_dedupe` (`transcribe.py:75-80`)
  sees only caption output, being called from inside `parse_vtt` at `:68`; and
  `filter_range` (`:96`) and `format_transcript` (`:102-104`) see BOTH, which is the
  whole point of the shape and is what `whisper.py`'s module docstring means by "the
  rest of the pipeline doesn't care where the transcript came from". Three producers feed
  those three sites, and every one of them constructs `start`, `end` and `text`
  unconditionally: `_segments_from_response` (`whisper.py:766`, and the whole-text
  fallback at `:774`), `local_whisper._collect` (`:392`), and `parse_vtt`
  (`transcribe.py:55`). So a key a server omitted is defaulted long before anything
  indexes it. That invariant is what makes the class unreachable, it has to hold across
  all three producers because two of the three indexing sites are shared, and it is
  recorded here so whoever adds a fourth knows what it has to hold. `moviola.py:403` is
  structurally safe from the ffmpeg value above it, because `SystemExit` subclasses
  `BaseException` and cannot land in an `except Exception`. `transcribe.py:62`
  interpolates `Path(path).name`, safe for a different reason — the yt-dlp output
  template is the fixed `video.%(ext)s` (`download.py:117`, `:183`), so the filename never
  carries the remote title. Nothing enforces any of this: one `raise ValueError(f"bad
  cue: {line}")` added inside `parse_vtt` opens the channel silently and no test would
  notice. Wrapping all four in `stderr_line` costs nothing on a message with no remote
  text in it and is the obvious cheap fix; it was NOT taken on the branch that added
  `stderr_line`, which was already at roughly 1,250 insertions when this was found.
  Non-goals: this is not one of the three surfaces the CHANGELOG lists — those carry
  remote text today and these do not, and conflating them overstates what shipped. It
  covers sites that interpolate an *exception* and says nothing about a future site
  interpolating remote data directly. And the audit is a snapshot of third-party message
  formats reachable today, not a guarantee about them.

- **Nothing stops a literal bidi control from being committed again.** (security gate,
  2026-08-26) Eighteen were removed from `untrusted.py` and fourteen more from
  `tests/test_stderr_is_untrusted.py`, and both batches were found by a census run by
  hand — the second only because the census was re-run over every file the branch
  touched rather than over the module being fixed. A test over the tracked tree
  asserting zero occurrences of the fourteen characters (U+202A-U+202E, U+2066-U+2069,
  U+200E, U+200F, U+061C, U+2028, U+2029) would pin it, and `tests/repo_files.py`
  already enumerates tracked files for the documentation checks, so the machinery
  exists. Write the character set as an explicit code-point tuple, never as a literal
  string: the literal form was mangled in transit once during this very pass, becoming a
  set that contained a space and reporting 54,749 false hits across the branch. Non-goals
  for whoever writes it: it must NOT fire on ordinary right-to-left text — Hebrew and
  Arabic letters need no control characters, and the `אבג` case in
  `test_balance_bidi_is_bounded.py` must stay legal — and it cannot see a control
  character arriving at RUNTIME, which is what `balance_bidi` is for; this is a
  source-hygiene check and nothing more. It must also enumerate from `git ls-files`
  rather than a hand-written list, or it cannot see the file someone forgets to add.

- **`tests/test_report_structure.py:58` keeps its own copy of the terminator table.**
  (stderr review, 2026-08-26) It is a hand-written list of ten characters that must stay
  a superset of `untrusted.LINE_BREAKS`, and nothing checks that it does.
  `tests/test_stderr_is_untrusted.py:172` has the same local copy but asserts the
  relation — `set(untrusted.LINE_BREAKS) - set(local)` must be empty, with a message
  naming what was widened — so widening the module's table fails that file loudly and
  the other file silently keeps testing the old set. Lift the same three lines into
  `test_report_structure.py`. Deliberately NOT the obvious fix of importing
  `untrusted.LINE_BREAKS` as the parametrization: that makes every case tautological,
  since the table under test would also be supplying the inputs.

- **The report interpolates ffprobe's `width` and `height` raw, and that is a decision
  rather than an oversight.** (report-injection review, 2026-08-26) Every other
  attacker-reachable value in the report is fenced; these two are not, because ffprobe
  emits them as JSON numbers for a video stream and there is no evidence they can carry
  text. Fencing them would render as ``` `1920`x`1080` ```, which is worse to read for a
  risk nobody has demonstrated. Recorded so the next reader knows it was weighed. If
  ffprobe is ever seen emitting a string there, fence them and take the ugly line.

- **`balance_bidi` approximates UAX#9 rather than implementing it.** (report-injection
  review, 2026-08-26) It matches a closer to the nearest open scope of the same kind,
  where the real algorithm resolves matching within an isolating run sequence. On a
  pathological interleaving it appends a terminator that was not strictly needed, which
  is harmless, and the tests pin the direction rather than the exact count. A real
  implementation is a dependency and a lot of code for a report generator; this is
  deliberately the cheap version.

- **Nothing checks the report end-to-end against a markdown parser.** (report-injection
  review, 2026-08-26) The invariants in `test_report_structure.py` are stated over the
  fenced value — one line, a delimiter that does not occur inside, balanced bidi — and
  two of them are checked against the real report. What is NOT checked is that the
  assembled document PARSES the way this program intends: no CommonMark parser is a test
  dependency, so a structural bug in the report's own scaffolding (an unclosed fence in
  the transcript block, say) would not be caught by anything here. Adding `markdown-it-py`
  as a dev dependency and asserting the heading tree is the shape.

## Quiet failures

- **A partial transcript reaches the report with nothing marking it partial.**
  (ai-output review, 2026-08-26) `transcribe_chunks` at `whisper.py:779` counts
  `failures` and uses the count for exactly one thing: raising when it equals the number
  of chunks. Nine chunks out of ten succeeding returns the concatenation as an ordinary
  list of segments, and the report on stdout is then indistinguishable from a complete
  one — the only trace is the `chunk N/M failed — skipping` line on stderr, a channel a
  reader may not have and a summariser will not weigh. The gap is worst where it matters
  most: a dropped chunk is a HOLE in the middle of the timeline, so the transcript reads
  as continuous across a gap it never covered. The shape is to thread `failures` and
  `len(chunks)` out of the function and have the report say which time ranges are
  missing. Non-goal: skipping rather than failing is the correct behaviour and must not
  change — one bad slice discarding a whole transcript is the trade this was built to
  avoid; the fix is disclosure, not strictness. Non-goal: this says nothing about the
  single-file path, which has no chunks and either succeeds or raises.

- **A frame can still be paired with a timestamp that is not its own, when ffmpeg
  reports FEWER of them than it wrote frames.** (quiet-failures review, 2026-08-26)
  `extract_scene_candidates` and `extract_keyframes` both do
  `ts = timestamps[i] if i < len(timestamps) else offset`, so once showinfo's output is
  shorter than the frame list every remaining image is labelled with the START of the
  requested range. That is a plausible number in the right units, which is what makes it
  bad: a report saying "at 0:00" for a frame from minute nine looks like ordinary output.
  Sorting the frames numerically (this pass) removed the reason the two lists diverge in
  the common case, but not the fallback itself — showinfo can drop lines under `-loglevel`
  changes, and a filter graph that emits a frame without a `pts_time` would do it too. The
  honest fix is to treat a length mismatch as an error, or carry the frame NUMBER through
  from the filename and index the timestamps by it rather than by position.

- **`frames_in_order` sorts on the last run of digits in the name and cannot see a
  directory that mixes two naming schemes.** (quiet-failures review, 2026-08-26) Every
  caller writes `frame_%04d.jpg` into a directory it has just emptied of `frame_*.jpg`,
  so today there is exactly one scheme and the sort is total. Nothing enforces that. If a
  future extractor writes `frame_a_0001.jpg` alongside `frame_0001.jpg`, both parse to 1
  and the tiebreak is the filename, which is the lexicographic bug again in a smaller
  room. Naming the scheme in one constant that the writer and the sorter share would
  close it.

- **The stale-file guard compares (mtime, size) and cannot see a second run writing into
  the same `--out-dir`.** (quiet-failures review, 2026-08-26) `snapshot_dir` answers "did
  THIS run produce this file", which is the right question for a reused directory and the
  wrong one for a shared directory: a file another moviola process writes while yt-dlp is
  running is new-since-the-snapshot and reads as ours. Two concurrent runs pointed at one
  `--out-dir` also clobber each other's `video.*` and `frame_*.jpg` outright, which is the
  larger problem the guard does not address. A per-run subdirectory, or a lock file, is
  the shape.

- **`parse_vtt` warns on a caption track that is legitimately empty.** (quiet-failures
  review, 2026-08-26) The warning fires whenever a subtitle file yields zero segments,
  and a video whose caption track exists but contains no cues will trip it. That is a
  deliberate false positive: the cost of a spurious stderr line is one line, and the cost
  of the silence it replaces was a paid API upload for a transcript already on disk. Worth
  revisiting only if the line turns out to be common enough to train people to ignore it.

## Bounded failures

- **`stderr_line` imposes no length bound of its own, and every caller is responsible for
  supplying one.** (security gate, 2026-08-26) `balance_bidi` is linear rather than
  quadratic as of this branch, so a hostile value costs time proportional to its length
  instead of its length squared — but linear is not bounded, and the function still walks
  and holds every character it is handed. The bound lives at the call sites, and they do
  not agree — and the stdout half of them does not bound at all. On stderr:
  `_read_error_body` slices 400 characters, the two `payload[:200]` sites slice 200,
  `local_whisper.py`'s hub-failure line now slices 400, and `str(HTTPError)` is bounded
  only by `http.client._MAXLINE` — 65536 bytes of status line, an underscore-private
  constant this program neither sets nor should rely on. On stdout, `md_inline` calls
  `stderr_line` and caps nothing: `moviola.py:441` and `:443` interpolate `info['title']`
  and `info['uploader']`, which are `raw.get("title")` / `raw.get("uploader")` off
  yt-dlp's JSON with no cap anywhere between that site and the report, and `:451` does the
  same for the container's codec string out of ffprobe. `moviola.py:439` is `args.source`,
  a local CLI argument rather than a remote value. With `balance_bidi` linear these cost
  time proportional to their length rather than to its square, so this is report bloat and
  context flooding and NOT the denial of service that was fixed — a multi-megabyte title
  makes an unreadable report, not a hung process. Capping them changes the report's own
  output and belongs with whoever owns that decision. Pushing the
  cap down into `stderr_line` itself was considered and rejected on this branch:
  `md_inline` calls it, so a bound there would silently truncate values in the stdout
  report as well, which is a report change wearing a stderr fix's clothes. Non-goal: this
  is not the forgery channel and does not reopen it, and it is not the unbounded READ that
  the `exc.read()` entry below describes — every call site slices before it fences. Filed
  so the next caller added knows the rule is caller-side and that nothing enforces it.

- **`exc.read()` at `whisper.py:644` reads an error body with no size limit.** (stderr
  review, 2026-08-26) The truncation on the next line is `[:400]`, which bounds what is
  *printed* and not what is *read* — a server answering a 400 with a gigabyte-long body
  gets the whole gigabyte into memory before Python slices 400 characters off the front.
  It is on an error path, so it costs nothing on a healthy run, and the failure mode is
  a MemoryError inside the handler for the error being reported rather than anything
  silent. The fix is `exc.read(<n>)`, with `<n>` a few KB rather than 400 bytes so the
  decode still sees whole multi-byte sequences. Non-goal: this is not the forgery
  channel `stderr_line` closed and does not reopen it — a truncated body is fenced
  either way; it is a resource bound, filed under the section that owns those.

- **`Retry-After` is honoured only in its seconds form.** (bounded-failures review, 2026-08-26)
  RFC 9110 also allows an HTTP-date, and `float()` rejects one, so a server answering
  `Retry-After: Wed, 26 Aug 2026 12:00:00 GMT` gets the exponential ladder instead of the
  wait it asked for. That is a deliberate under-read — the ladder is bounded and correct,
  and honouring a date needs a clock comparison with its own failure modes (skew, a server
  that sends a date in the past) — but it does mean moviola can retry sooner than a
  provider told it to, which on a strict rate limiter is how a 429 becomes a ban.

- **`MAX_RETRY_DELAY` is a fixed 60 seconds with no way to change it.** (bounded-failures
  review, 2026-08-26) The number was picked to be longer than any legitimate backoff and
  far shorter than a parked run; nothing measured it. A user on a provider that genuinely
  wants a five-minute wait has no setting to give it one, and the only signal that the cap
  bit is that the retry notice prints a smaller number than the server sent.

- **Chunk cleanup cannot tell whose chunks it is deleting.** (bounded-failures review,
  2026-08-26) `cleanup_chunks` removes `chunk_*.mp3` from the work directory, and two
  moviola runs sharing one `--out-dir` will delete each other's in-flight chunks — the same
  shared-work-directory limit the download path already documents. The real fix is a
  per-run subdirectory, which is a wider change than this pass; the narrow fix is that a
  single run now leaks nothing.

- **The extracted audio and the work directory itself still outlive the run.**
  (bounded-failures review, 2026-08-26) Chunks are cleaned up; `audio.mp3`, the downloaded
  video, and the frames are not, and with `--out-dir` they accumulate across runs. That is
  partly by design — SKILL.md tells the agent to Read the frame paths after the script
  exits, so deleting them would break the report — but nothing ever removes them
  afterwards, and nothing tells the user how much disk a week of use costs.

- **The parser and the config are proven to AGREE, not to be right.**
  (bounded-failures review, 2026-08-26) `build_parser()` reads `config.DETAILS` and
  `config.WHISPER_BACKENDS`, and the tests compare the two. A value that is wrong in the
  config is now wrong in the flag as well, consistently, and invisibly from here. The
  `WHISPER_BACKENDS` half of this is closed — `test_every_backend_has_an_implementation`
  now requires every offered name to reach a dispatch branch, a key lookup, a host entry
  and an endpoint on that host — but `config.DETAILS` has no equivalent check, and
  neither half says the sets are the ones moviola OUGHT to offer. A provider it should
  support and does not is still invisible from every test in the suite.

- **A backend can be routed to the right host by the wrong path or model.**
  (bounded-failures review, 2026-08-26) `test_each_branch_posts_to_its_own_host` ties
  the dispatch branch, the endpoint constant and `API_HOSTS` together, so a branch
  copy-pasted from the other provider's fails. It compares hosts only:
  `https://api.openai.com/v1/chat/completions` and a `model` id the provider retired
  both satisfy it. Those are discoverable against the live API and nowhere else, which
  is why this is filed rather than fixed — a network-free suite structurally cannot see
  them. Non-goal: this is not an argument for a live-API test in the suite; it is a note
  that green here is not a claim either endpoint still answers.

- **`--detail transcript` still prints the whole transcript with no cap.**
  (bounded-failures review, 2026-08-26) A three-hour video's transcript goes to stdout in
  one piece and straight into an agent's context. Every other output in the report is
  bounded — frames by `frame_cap`, uploads by `MAX_UPLOAD_BYTES`, retries by
  `MAX_RETRY_DELAY` — and this one is not. A cap needs a decision about what to drop
  (middle, tail, or by speaker turn), which is why it is here rather than fixed.

- **An unknown duration is 0.0, and the report states it as a fact.** (bounded-failures
  review, 2026-08-26) `get_metadata` answers `duration_seconds: 0.0` when ffprobe cannot
  tell it how long the video is, and `moviola.py` prints that as
  `- **Duration:** 00:00 (0.0s)` — indistinguishable in the report from a genuinely
  empty file. `auto_fps(0)` then budgets exactly one frame for the whole video. Neither
  is new: an ABSENT `duration` key has always produced them, and `finite_float` only
  routes one more input to the same place. The fix is a sentinel the report can render
  as "unknown" rather than a number, which is a report change and wants the same owner
  as the `--detail transcript` cap. Non-goal: this is not the ValueError entry that
  `finite_float` closed, and it does not reopen it.

- **`finite_float` is caller-side, and nothing enforces that a new parse uses it.**
  (bounded-failures review, 2026-08-26) Two producers call it today — ffprobe via
  `frames.get_metadata`, yt-dlp via `moviola.metadata_from_info` — and they are the only
  two. A third site that ever parses a number out of somebody else's output is guarded
  only if whoever writes it remembers, which is exactly the limitation the `stderr_line`
  entry above already records for the same reason. Filed so the next one knows the rule.

- **The video format fallback has no height cap.** (bounded-failures review, 2026-08-26)
  `bv*[height<=720]+ba/b[height<=720]/bv+ba/b` ends in two unrestricted selectors, so a
  video with no 720p-or-below variant downloads at whatever the highest rendition is —
  4K, on a flag whose entire point is to stay small. It is a fallback that silently
  inverts the intent of the two selectors before it.

- **A pinned API backend never falls back to the other one.** (forgeward ai-output
  review, 2026-08-26) When `--whisper groq` exhausts its retry ladder the run stops with
  a named error and the documented remedy is for the user to re-run with
  `--whisper openai`. That is deliberate — silently spending money at a provider the
  user did not name is exactly the consent boundary the rest of this work draws — but it
  means a provider outage costs a whole run rather than a slower one. If it is ever
  changed, the failover has to announce the second provider before the first byte goes
  out, the same way `_announce_upload` does today.

## Documentation as a checked claim

- **The README-to-parser direction is not checked, and cannot be.** (docs-are-checked
  review, 2026-08-26) `test_the_docs_are_checked.py` proves every long flag
  `build_parser()` defines appears in README. The reverse would false-fire, because
  README also documents `setup.py`'s `--agent`, `--check`, `--copy` and `--list`, which
  are correct entries for a different program. So a flag README documents that nothing
  implements — a removed flag, or a typo — is invisible. The fix is a second, narrower
  invariant that knows which README section belongs to which parser, and that means
  giving those sections machine-readable boundaries first.

- **`setup.py`'s own flags are pinned to nothing.** (docs-are-checked review,
  2026-08-26) Same class as above, from the other end: the four flags above are
  documented in README and defined in `setup.py`, and no test compares the two sets in
  either direction.

- **Agreement is not correctness.** (docs-are-checked review, 2026-08-26) The version
  test proves `SKILL.md`, `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`
  carry the same string. Nothing checks that string against a git tag, against the
  CHANGELOG's newest heading, or that it was bumped at all when behaviour changed — so
  three files agreeing on a stale number passes cleanly.

- **SKILL.md's body is unchecked; only its frontmatter is.** (docs-are-checked review,
  2026-08-26) The frontmatter's `version`, `author` and `name` are now pinned. The body
  is several hundred lines of instructions to an agent — flag spellings, file paths,
  the work-directory contract — and none of it is compared to the scripts it describes.
  It is the single largest unpinned prose surface in the repo.

- **The README benchmark table is unpinnable prose.** (docs-are-checked review,
  2026-08-26) Frame counts, extraction times and token totals from one 49:08 video.
  They are measurements, not claims about the code, so no invariant can hold them —
  but they will age, and nothing will say so. If they are ever regenerated, date the
  table rather than replacing the numbers in place.

- **The hook's API-backend sentences carry the same claim shape as the local one.**
  (docs-are-checked review, 2026-08-26) `ready — transcription via the $BACKEND API` is
  said on the strength of a key being present in moviola's own config file. That is
  weaker evidence than it sounds — the key may be revoked, out of credit, or wrong —
  but it is also the whole prerequisite moviola needs in order to *try*, which is why
  the local sentence was rewritten and these were left. Revisit only if a preflight
  request is ever added; until then the honest fix would be a wording change with no
  new evidence behind it.

- **The released 0.2.0 changelog entry says 25 MB where the code says 24.** (docs-are-
  checked review, 2026-08-26) The providers' documented cap is 25 MB and moviola splits
  at 24 deliberately. `test_consistency.py` records that *distinction* in a comment above
  `OUR_CAP`, but it does not cover this line: it reads `whisper.py` and `SKILL.md` and
  never opens `CHANGELOG.md`, so nothing in the suite sees `CHANGELOG.md:84`. Rewriting a
  released changelog entry is the wrong fix; the entry describes what shipped that day.

- **The work directory is printed with bare backticks while SKILL.md tells the agent to
  `rm -rf` it.** (docs-are-checked review, 2026-08-26) `skills/moviola/SKILL.md:193`
  instructs the agent to remove the work directory when it is done. The path reaches
  that instruction through the report as an un-fenced value, and unlike the title and
  uploader it is a path this program constructed — but `--out-dir` is user-supplied, so
  the value is not ours. Fencing it is cheap; deciding whether the `rm -rf` instruction
  should exist at all is the larger question and belongs with it.

- **A `--out-dir` work directory is created with the default umask.** (docs-are-checked
  review, 2026-08-26; corrected after the forgeward privacy review, 2026-08-26)
  `~/.config/moviola/.env` is written 0600 and checked for it. The work directory holds
  the extracted audio, the downloaded video and every frame. **The default path is not
  affected**: `moviola.py:259` uses `tempfile.mkdtemp`, which hardcodes 0700 regardless
  of umask — verified directly under `umask 000` (`mkdtemp` → 0700, plain `mkdir` →
  0777). Only the explicit `--out-dir` branch at `moviola.py:257` calls `Path.mkdir()`
  and so inherits the shell's umask. This entry originally claimed the exposure for
  every run; that was wrong, and it is recorded here rather than silently rewritten
  because the overclaim is the sort of thing that sends someone to harden a path that
  was already private.

- **An invalid `MOVIOLA_WHISPER` is described two different ways.** (forgeward privacy
  review, 2026-08-26) `config.py:85-87` normalizes an unrecognised value to `auto`
  silently; `hooks/scripts/check-setup.sh:158` prints that the backend "is pinned but
  that backend is not usable here" for the same input. Both then resolve identically, so
  this is message drift and not a consent-boundary bug — but it is the third surface
  that re-derives the same precedence in its own words, which is the drift `## Quiet
  failures` already warns about. A typo'd backend name should say "not a backend name"
  in both places.

- **The self-reference audit reads `<owner>/<word>` as a repo slug, and a branch name
  has that shape.** (release staging, 2026-08-26)
  `test_every_reference_under_this_owner_names_the_same_repository` flagged the `chore`
  and `fix` halves of `<owner>/chore/...` and `<owner>/fix/...` as wrong repositories
  when they were branch names quoted inside a merge-commit subject. The helper now lives once, as
  `tracked_text_files` in `tests/repo_files.py`, and asks `git ls-files` instead of
  walking the working tree, which stops the audits reading local scratch — that was the
  actual failure, and it is fixed. (It was fixed *incompletely* first: the helper was
  byte-identical in two test modules and only one copy moved to git, so half the audits
  kept walking the tree while the commit message said the problem was closed. Both
  callers now import the single copy.) But **consulting git
  narrowed the file set, not the pattern**. A *tracked* file quoting a branch name in
  prose (a CHANGELOG line, a runbook, a migration note) trips it exactly the same way,
  and the workaround is to write the slug in a form the regex cannot see. Fixing the
  pattern means teaching it which slugs are repositories, which needs either a list or
  a rule about what follows the second slash. Recorded rather than fixed because the
  false positive is currently harmless and the fix is guesswork about future prose.

- **The README's download link points at a release the fork has never published.**
  (rename to moviola, 2026-08-26) `README.md:136` tells a claude.ai user to download
  `moviola.skill` from the latest release, and `gh release list` on `androsland/moviola`
  returns nothing — the five tags here (`0.1.0`, `v0.1.1`, `v0.1.2`, `v0.1.3`, `v0.2.0`)
  are inherited from upstream and predate the fork; note the first has no `v`. The
  instruction has been dead since the fork, the
  rename did not cause it, and no test in `test_the_docs_are_checked.py` can see it: the
  suite checks that the docs agree with the code, and a release is neither. Either cut a
  `v0.3.0` release with the built `.skill` asset, or say plainly that the web path is not
  available yet.
  **Staged, not closed (2026-08-26). Half this path is verified; say which half.** The
  BUILD half is verified locally: `build-skill.sh` produces `dist/moviola.skill` rooted
  at `moviola/`, 9 files in 65,203 bytes. (The script prints `11 files` — its counter
  reads `unzip -l`'s total line, which counts the two directory entries too. Same
  bundle, different question.) Until the `skills/moviola/.gitattributes` added in this
  commit, that bundle was 11 files / 66,354 bytes and shipped `scripts/build-skill.sh`
  and `.skillignore` to every claude.ai user, because `export-ignore` matches relative
  to the *archive root* and the patterns were written at the repo root.
  The PUBLISH half is **not** verified and must not be written as if it were:
  `release.yml` has never executed once in this repository. `gh run list` returns no run
  of any workflow, ever — the five `v*` tags on origin arrived *with the fork* rather
  than through a push, so no `push: tags` event has ever fired here. Cutting `v0.3.0`
  is that workflow's first ever run, and it will run with the defects filed under
  "Release workflow" below still in it. Pushing a tag publishes a GitHub release to the
  world, so it is deliberately not automated:
  `git tag -a v0.3.0 -m "v0.3.0"`, then push that tag.
  **This entry closes when that tag exists, not when this PR merges** — until then
  `README.md:136` still points at a 404. Nothing in the suite can tell the difference;
  see the new NON-GOALS bullet in `test_the_docs_are_checked.py` saying so directly.

- **Nothing runs the 564-test suite in CI.** (release staging, 2026-08-26)
  `.github/workflows/release.yml` is the *only* workflow in the repo and it triggers on
  `push: tags: v*`. So the tag that publishes `moviola.skill` to the world is gated by
  nothing except whatever the person cutting it ran locally. The suite is stdlib +
  pytest and needs `ffmpeg` for the frame tests, so a runner is cheap and the repo is
  public, which makes the minutes free. The fix is one workflow with day-one config:
  `push` on `main` only, a `concurrency` group keyed on the ref with
  `cancel-in-progress` off the default branch, and one job.

- **`AGENTS.md` documents a `.venv` that no longer exists in this checkout.**
  (release staging, 2026-08-26) Its Commands block gives
  `.venv/bin/pytest -q                # or: python3 -m pytest -q`, and there is no
  `.venv/` here — the first form fails outright and only the fallback works. Left as a
  finding rather than fixed in the same breath because the honest repair is a decision
  about whether this project wants a checked-in venv convention at all, and that is
  wider than a docs edit. `python3 -m pytest -q` collects 564 as of this commit.

- **The file set the repo-wide audits read is a subset under a sparse checkout, and
  nothing says so.** (release staging, 2026-08-26) `tracked_text_files` asks
  `git ls-files`, which reports the whole index, and then drops anything failing
  `is_file()`. Under a sparse checkout the cone has not materialised most of that index,
  so the audits sweep whatever happens to be on disk and go green. This is written into
  `tests/repo_files.py` as an explicit NON-GOAL rather than fixed, because the fix is a
  judgement call: `git config core.sparseCheckout` could gate a skip, but a skip that
  fires on a legitimate CI shallow-clone setup trades a real blind spot for a suite that
  refuses to run. Nobody has hit this yet; it is filed so the next person does not
  discover it as a mystery.

- **`build-skill.sh`'s untracked-file guard has no test.** (release staging, 2026-08-26)
  The guard added in this commit — `git ls-files --others --exclude-standard --
  skills/moviola` — closes a real hole: `git diff` sees no untracked files and the
  archive reads `HEAD`, so a new-but-uncommitted runtime module produced a silently
  incomplete bundle under a success message. It is verified by hand, not by the suite.
  Testing it means driving a shell script that hard-exits on a dirty tree from inside
  pytest, which wants a scratch clone per case; that is a fixture worth building when a
  second script-level guard needs one, not for a single four-line `if`.

- **A repo-root `.skillignore` still ships in the `/plugin install` archive.**
  (release staging, 2026-08-26) The root `.gitattributes` export-ignores `.gitignore`
  and `.gitattributes` but not `.skillignore`, so a 33-line scanner config lands in
  every Claude Code plugin install. Same class as the bug this commit fixes and a
  one-line fix, deliberately not taken here: it is the full-repo archive, not the
  claude.ai bundle, and `TestThePublishedBundleShipsWhatGitattributesClaims` only reads
  the subtree. Fixing it without extending that test to the root archive would be an
  unpinned change at the tail of a branch that is otherwise ready.

## Release workflow

Everything here is a defect in `.github/workflows/release.yml`, which has **never run**
— see the README download entry above. It is filed separately and not fixed on the
release branch because CI logic is executable behaviour and gets its own PR rather than
riding along behind prose.

- **Nothing checks the tag against the version the skill actually ships.**
  (release staging, 2026-08-26) The workflow never compares `${GITHUB_REF_NAME#v}` to
  the `version:` in `skills/moviola/SKILL.md` frontmatter or to either `plugin.json`.
  The suite pins those four to each other, so a mismatch cannot exist *inside* the repo
  — but the tag is outside it, which `test_the_docs_are_checked.py:205` already states
  as a NON-GOAL: "It does NOT check the version against a git tag or a published
  release." Tagging `v0.3.1` on a tree that says `0.3.0` publishes a release whose asset
  contradicts its own name, and nothing anywhere notices. The workflow is the only place
  that can close this, which is why it is filed here and not there.

- **`tags: - "v*"` publishes a pre-release as `latest`, which is where the README
  sends people.** (release staging, 2026-08-26) `v*` matches `v0.3.0-rc1`, and the job
  hardcodes `prerelease: false`, so an rc tag becomes a full release and takes over
  `/releases/latest` — the exact URL `README.md:104` and `README.md:136` point at. Fix
  is either a stricter pattern (`v[0-9]+.[0-9]+.[0-9]+`) or deriving `prerelease` from
  whether the ref contains a hyphen.

- **A tag spelled without the `v` triggers nothing, silently.** (release staging,
  2026-08-26) This is not hypothetical: `0.1.0` is on origin right now alongside four
  `v`-prefixed siblings, so the spelling has already been got wrong once in this repo's
  history. A release cut that way produces no run, no asset and no error — the person
  cutting it has to notice the absence.

- **`generate_release_notes: true` will synthesise notes from upstream churn.**
  (release staging, 2026-08-26) GitHub builds those notes from commits since the last
  release. There is no previous release *here*, and the fork carries upstream's history,
  so the first run produces notes describing `bradautomates/claude-video`'s commits and
  ignores the hand-written `CHANGELOG.md` entry that exists precisely to say what
  changed. Use `body_path: CHANGELOG.md` or an extracted section instead.

- **`softprops/action-gh-release@v2` is a floating major tag, not a pinned SHA.**
  (release staging, 2026-08-26) The job holds `contents: write`, so whatever that tag
  resolves to at run time can write releases and tags in this repo. Pin to a commit SHA
  with the version in a trailing comment. (`actions/checkout@v4` is the same shape and
  the same fix; it is first-party, which lowers the odds, not the blast radius.)

- **No `concurrency:` group, and `fetch-depth: 0` buys nothing.** (release staging,
  2026-08-26) Two tags pushed together run two jobs racing to create releases. And the
  full-history fetch is there for a `git describe` the workflow does not do —
  `build-skill.sh` archives `HEAD:skills/moviola`, which needs one commit. Minor next to
  the rest; grouped here so the workflow PR closes them in one pass.

## Housekeeping

- **`moviola.py` resolves its own directory differently from every other script.**
  (stderr review, 2026-08-26) `moviola.py:15` is `Path(__file__).parent.resolve()`;
  `whisper.py:34`, `setup.py:31` and `local_whisper.py:33` are
  `Path(__file__).resolve().parent`. They agree on an ordinary checkout and diverge the
  moment the script itself is a symlink: `.parent.resolve()` takes the directory the
  link SITS in and resolves that, while `.resolve().parent` follows the link to its
  target and takes the directory the real file sits in. An installer that symlinks
  `moviola.py` into a bin directory would therefore have it insert the wrong path on
  `sys.path` and fail to import its own siblings, while the other three would still
  work. No installer does that today, which is why this is housekeeping and not a bug.
  Make `moviola.py` match the other three. Non-goal: this says nothing about the plugin
  cache or `dev-sync.sh`, which copy files rather than linking them and are unaffected
  either way.

- **This file is over the ~50KB archive threshold, and the split is deferred on a
  judgement, not on arithmetic.** (2026-08-26) Measured with
  `awk '/^## Completed/{f=1} f' TODOS.md | wc -c`: 93,804 bytes total, of which
  `## Completed` is 36,750 — 39%, a genuine mass and not a rounding error. (At the merge
  base for the stderr branch it was 53,933 / 51.2%; both halves have grown since, the
  live sections faster than the completed one.)
  **The previous version of this entry said the split would "move nothing" because there
  are "only 4 entries". That was an eyeballed count and it decided the outcome.**
  `## Completed` holds 6 `###` subsections *and* 26 bulleted findings, and the archive
  rule — keep the 5 most recent — never says which of those is an entry. Counted by
  subsection, one moves and the file barely shrinks. Counted by bullet, 21 of 26 move
  and roughly 21KB with them, so "moving them would move nothing" is false by very
  nearly the entire payoff of the split. The ambiguity is the finding; the arithmetic was never the
  reason. (The count was 4 subsections when this was written and is 6 now; nothing
  re-measures it, which is why it is stated with its date.)
  The actual reason to defer: all 26 came from one investigation on one day, and 20,097
  of the 36,750 bytes are still a single subsection. Archiving by bullet would cut that
  investigation in half across two files and leave the surviving five as orphans of an
  argument that lives elsewhere. Cut at the next *distinct* body of work, when there is
  a seam to cut along. Until then the file stays over the threshold for a reason
  archiving cannot fix.
  When it does run, the entries here carry reversed decisions worth lifting into
  `AGENTS.md` as constraints rather than leaving as narrative — the `find_spec`
  rejection and the ambient-key rule are both in that class. Nothing verifies that
  extraction step, so a pass that archives without lifting them is a silent regression.

## Completed

### A second pass over the bounded-failures review

(bounded-failures review, 2026-08-26 — `fix/bounded-failures-ii`)

**Every name a user may pin now has to reach an implementation.**
`config.WHISPER_BACKENDS` is what argparse renders as `--whisper`'s choices and what
`get_config` validates `MOVIOLA_WHISPER` against, and the suite already proved the
parser and the config reader agree with it — which proves the three restate one literal,
not that the literal is right. A name added to the tuple and nowhere else would be
offered in `--help`, accepted by argparse, preserved by `get_config`, and then die at
`Unknown whisper backend:` *after* the video was downloaded, the frames extracted and
the audio encoded.

The new test drives the whole of `transcribe_video` once per name, with the names taken
from the tuple rather than written into the test, and reads the dispatch branches out of
`_transcribe_file`'s AST rather than restating the same list a second time. `auto` is
asserted to have NO implementation, because it is a sentinel `resolve_whisper_choice`
converts to `None` and a dispatch branch for it would be the defect. Each API name is
required to reach four things, not one: an `API_CANDIDATES` entry so its key can be
found, a dispatch branch so the upload happens, an `API_HOSTS` entry so
`_announce_upload` names a hostname rather than falling through `.get(backend, backend)`
and printing the backend's own name, and an endpoint on that host.

**It could not be written RED and says so in its own docstring.** All four names have
implementations today, so every assertion passed on first run; its only evidence is the
KILL, which is weaker than a normal RED->GREEN and is the strongest available for a
finding whose subject is a missing check rather than a broken behaviour. Six mutations,
six killed: a `deepgram` in the table and nowhere else (six tests), `openai` dropped
from `API_HOSTS` (three), its dispatch branch removed (three), its branch copy-pasted
from Groq's so the right name posts to the wrong provider (two), `openai` dropped from
`API_CANDIDATES` (one), and the sentinel given a dispatch branch (two). The
copy-paste mutation survived the first version of the test and the gap was written up
as a NON-GOAL before it was closed; closing it took tying three separately-maintained
tables together, and the NON-GOAL now records the narrower blind spot that remains.

**`duration_seconds` no longer raises on a non-numeric field — and the finding's premise
was wrong.** The entry said a container reporting `N/A` takes down the whole run. `N/A`
is real, but it belongs to ffprobe's **default** writer, not to the JSON writer moviola
actually asks for. Proved on one real file with one real ffprobe: the default writer
prints `duration=N/A`, `start_time=N/A`, `bit_rate=N/A`; `-print_format json` omits those
keys entirely, and an absent key was already handled by the `or 0` chain. So the
`ValueError` was **not reachable** through moviola's own command line. Two tests pin both
halves of that contrast, so if ffprobe ever starts emitting the string into JSON the
suite says so rather than the guard silently becoming load-bearing.

The guard shipped anyway, as disclosed defence in depth rather than a fix for a live
crash: moviola pins no ffprobe version and no yt-dlp version, `-show_optional_fields
always` (ffmpeg >= 5.1) is a documented way to put `N/A` *into* the JSON, and the yt-dlp
half — `info.json`'s `duration`, read on the transcript-only path where there is no video
to probe — has no writer guarantee behind it at all. `untrusted.finite_float` is the one
definition both callers share; it lives in the leaf module because `frames.py` and
`moviola.py` are on opposite sides of an import edge and a second copy is the failure
mode this repo has already been bitten by.

Non-finite is rejected as well as non-numeric, and that is not pedantry: `float()`
accepts `"nan"` and `"inf"` and returns them happily, so leaving them through moves the
crash two functions downstream into `_clamp_fps`, where `int(round(nan))` raises
`ValueError: cannot convert float NaN to integer` and `int(round(inf))` raises
`OverflowError` — both naming a frame-budget helper the user never heard of instead of
the metadata that was bad.

The replacement is also strictly better than the `or` chain it replaces, which is a
separate defect the entry did not name. `fmt["duration"] or video_stream["duration"]`
takes `"N/A"` — a truthy string — and never consults the stream that knew the answer.
Nesting the two `finite_float` calls makes "could not parse" fall through to the
fallback, which is what the fallback was for. The same guard was widened to
`size_bytes`, parsed with a bare `int()` two lines below, on the same argument.

Five mutations, five killed: the finding's own (`float()` restored, 3 tests), the bare
`int()` on size (1), the bare `float()` on yt-dlp's duration (1), the finiteness check
dropped (7, across both the guard's own tests and the frame-budget one), and the nesting
flattened back to an `or` chain (1 — the fall-through). 651 tests green.

### stderr was a second document into the agent's context, and nothing fenced it

(stderr review, 2026-08-26 — `fix/stderr-is-untrusted`)

The report on stdout has been treated as untrusted since `md_inline` landed. stderr never
was, and it lands in the same place. Every line moviola writes there carries a `[moviola] `
prefix, which is the entire attribution the reader gets — so a remote value that ends its
own line hands an attacker the next one.

`_read_error_body` is the live instance. It takes up to 400 bytes of whatever a server
answered a failing request with and interpolates them into a `SystemExit` message, so a
body reading `quota exceeded` + newline + `[moviola] transcript complete — no further
action needed` forges a progress line for the price of a 400 response. **`stderr_line()`**
now makes the two structural edits `md_inline` makes and stops there: line breaks collapse
to spaces, unclosed bidi scopes are closed, no backtick wrap because stderr is not
markdown. It is not a sanitizer and strips nothing — the body is still reported in full,
because whoever is debugging a failed request needs to read what the server actually said.

Fenced at `_read_error_body` rather than at the four exits that print it, which is what
makes it hold: both `Whisper request failed` raises, the after-N-attempts raise, and
`transcribe_chunks` — which catches one of those `SystemExit`s and prints it again — all
get the body from that one function, and so would the fifth site somebody adds next. Two
values that never pass through it needed their own fence: `payload[:200]` on a 200 that is
not JSON, and `URLError`'s str in the network-retry notice, which for a TLS failure is text
the far end chose. The fence goes AFTER the 400-character truncation on purpose, so a
bidi scope opened inside the first 400 characters and cut off by the slice still gets
closed.

`stderr_line` lives in a new leaf module, `scripts/untrusted.py`, together with
`LINE_BREAKS` and `balance_bidi` moved out of `moviola.py`. `whisper.py` needed them and
`moviola.py` imports `whisper.py`, so leaving them where they were meant either a cycle or
a second copy — and a second copy is the failure mode `tests/repo_files.py` had just been
consolidated to fix, where the U+2028 widening reached one implementation and not the
other. `md_inline` now calls `stderr_line` and adds only the markdown wrap; one test
asserts the two share a definition rather than agreeing by coincidence.

Five mutations, each confirmed to fail the new tests. One of them nearly passed on stale
bytecode: three of the mutations remove exactly 13 characters, so the mutated files are
byte-identical in size, and within the same mtime second Python reused the previous
mutant's `.pyc` and reported the previous mutant's failure. Clearing `__pycache__` between
mutations is now part of the loop.

Two assertions in the new file had to be rewritten before they meant anything. `assert
line.startswith("[moviola] ")` over every stderr line **cannot fail against this attack** —
the forged line starts with `[moviola] ` too; that is what makes it a forgery. Both were
replaced with a count of the lines the program intended to write. A third test asserted a
cost notice that `_post_whisper` does not emit at all, and was pointed at the retry notice
it does.

`SKILL.md`'s "Bundled scripts:" list gained `untrusted.py` — and `config.py`, which had
never been listed despite the sentence above it reading "Review scripts before first use".
Nothing pins that list against `scripts/`; filed.

Two surfaces deliberately left alone and filed rather than half-fixed: ffmpeg's and
ffprobe's captured stderr, which is equally remote but legitimately multi-line and needs a
block fence rather than a line fence, and yt-dlp's output, which reaches stderr through an
inherited file descriptor and cannot be touched by any helper that edits an interpolated
value. That second one is the largest volume of remote text on this program's stderr and
`stderr_line` covers none of it.

### Documentation claims that no test could see

(docs-are-checked review, 2026-08-26 — `fix/docs-are-checked`)

Six claims the docs made about the code, each with a machine-checkable referent, each
wrong, and nothing in the suite able to tell.

- **The version was two different numbers.** `SKILL.md` said `0.2.0`; both plugin
  manifests said `0.3.0`. `AGENTS.md:48` states keeping the three in sync as a release
  invariant and nothing enforced it.
- **The author was two different people.** The manifests had been updated for the fork
  and the skill's own frontmatter — the one a user reads — still credited upstream.
- **A star-history link still pointed at the upstream repository.** The three image URLs
  beside it had been updated; the anchor's `href` encoded the slug as `%2F` and a grep
  for the plain form missed it.
- **`config.py` described a backend-resolution rule the consent work had replaced.** The
  comment said `auto` was "local, else Groq, else OpenAI"; since the consent fix, an
  unpinned run reads API keys from moviola's own config file only.
- **`transcribe_video`'s docstring named four of the five option keys its caller
  builds** — omitting `offline`, the one key whose third state is load-bearing.
- **The SessionStart hook said "ready — transcription runs on this machine"** on the
  strength of a `find_spec` probe. `find_spec` proves the package is on the path, not
  that importing it works: a half-installed CTranslate2, a numpy ABI mismatch or a
  missing libstdc++ all pass it and fail at the first transcription. The probe is the
  right trade at every SessionStart; the sentence now says "faster-whisper is
  installed". An earlier lead — change `setup._have_local_whisper` to `find_spec` too —
  was investigated and rejected: that call site does a real import on purpose and keeps
  `_IMPORT_ERROR` so the failure is reported verbatim.
- **The CHANGELOG claimed a test file had 42 tests.** It has 84.

12 new tests (539 → 551). Six mutations re-applied and all six now fail: version drift,
author drift, the upstream link, the docstring dropping `offline`, the hook's old
sentence, and a stale test count.

### Four unbounded or dishonest failure modes

(bounded-failures review, 2026-08-26 — `fix/bounded-failures`)

Two failure modes with no ceiling, and two tests that were not testing what they
appeared to.

- **A server could park the run for as long as it liked.** `_retry_after` returned
  whatever number the `Retry-After` header held and that value went straight to
  `time.sleep`. `Retry-After: 86400` is a real answer real services give, and honouring
  it meant a run that never returned and never said anything more. A negative value was
  worse than a long one: it reached `time.sleep` and raised ValueError from inside the
  handler for the error being retried, and so did `nan`, which `float()` parses happily.
  `MAX_RETRY_DELAY` (60s) now caps every wait, `_bounded_delay` rejects NaN and negatives,
  and a non-positive `Retry-After` falls back to the ladder rather than being obeyed.

- **Chunk files were written and never deleted.** Chunking only happens on audio over the
  24 MB upload cap, so the leak was proportional to the LONGEST videos, and with a reused
  `--out-dir` it accumulated across runs instead of dying with a temp directory.
  `split_audio` now clears stale `chunk_*.mp3` before writing — a run producing fewer
  chunks than the last one used to leave the tail of the old set behind, with names
  indistinguishable from real chunks — and `transcribe_video` cleans up in a `finally`, so
  the failing path leaks nothing either.

- **The CLI's `choices` duplicated the config's sets.** `--detail` and `--whisper` carried
  string literals repeating `config.DETAILS` and `config.WHISPER_BACKENDS`, with nothing
  comparing them: adding a backend to the config left the flag rejecting it, and
  argparse's error reads as "that backend does not exist" rather than "that flag is
  stale". `build_parser()` is now a function so a test can hold the two up against each
  other, and both sets are tuples so `--help` keeps its cost progression.

- **The hook tests damaged the process they ran in.** `_run` popped four variables out of
  `os.environ` with no monkeypatch and no restore. It was dead code — `subprocess.run(env=env)`
  hands the child a closed dict, so the child never saw the parent's environment either
  way — and its only effect was on pytest itself, silently deleting those names for every
  test that ran afterwards. Deleted, with a test that pins the isolation actually in use.

Also pinned: `_truthy`'s tri-state, which nothing tested. `whisper_offline` is `None`
when unset, `False` for the documented falsey words, `True` otherwise — and the
difference between `None` and `False` decides whether `HF_HUB_OFFLINE` gets a say.

28 new tests (511 → 539). Five mutations re-applied and all five now fail: unclamped
`Retry-After`, `split_audio` not clearing, cleanup skipped on the failing path, hardcoded
`choices`, and the environment-popping loop restored.

### Four quiet failures in the download and pairing paths

Each of these produced a confident, well-formed result that was not true, and said
nothing — which is worse than a crash, because a traceback stops the user and a plausible
report does not: it goes into an agent's context and gets acted on.

`download_url` treated "a file matching `video*` exists in the output directory" as proof
the download worked and never looked at yt-dlp's exit code. `--out-dir` is a documented
flag and the skill tells the agent to reuse the directory, so a run whose download failed
outright picked up the PREVIOUS run's video and reported on it: right filename, wrong
film. `snapshot_dir` now records (mtime, size) per name before yt-dlp starts, and
`_pick_video`, `_pick_subtitle` and `_read_info` only accept files that are new or changed
since. The exit code is no longer swallowed either — a non-zero exit that still produced a
video is a real and expected case (a subtitle variant 429s) and continues, but says so.

`TS_RE` demanded exactly two-digit hours. WebVTT's hours component is OPTIONAL and may be
longer than two digits, so spec-legal `MM:SS.mmm` and `100:00:00.000` files both parsed to
zero segments — and zero segments is indistinguishable from "this video has no captions"
at every call site, so moviola escalated to a PAID upload while a perfectly good
transcript sat on disk. The pattern now makes hours optional and unbounded, and a subtitle
file that yields nothing says so on stderr while its name is still in scope.

Frames were paired with timestamps by position after a LEXICOGRAPHIC sort of
`frame_%04d.jpg`. `%04d` is a minimum width, not a maximum: past 9999 ffmpeg writes
`frame_10000.jpg`, which sorts between `frame_1000.jpg` and `frame_1001.jpg` (`.` is 0x2E,
`0` is 0x30), and from there every image carries somebody else's timestamp. Uncapped scene
detection on a long video reaches that count. `frames_in_order` sorts on the trailing digit
run and all three call sites go through it.

`tests/test_quiet_failures.py` states each as an invariant rather than as a regression
case. All five mutations died: accepting stale files (2 failed), swallowing the exit code
(1), the old two-digit `TS_RE` (5), dropping the zero-cue warning (1), and reverting to a
lexicographic sort (2).


### The report's fencing was built from the exploit, not from the boundary

`md_inline` closed the structural channel against the two characters somebody had
demonstrated — `\n` and `\r` — and stopped there. Three ways past it survived, and all
three are the same mistake in different clothes: the fix was scoped to the sample rather
than to the property.

`str.splitlines()` breaks on ten characters, not two. A title carrying U+2028 was one
line to `md_inline` and two lines to every renderer, pager and terminal downstream, so
the "no line break can escape the list item" guarantee held only against the two that
had been tried. `md_inline("")` returned two adjacent backticks, which is not an empty
code span at all — it is an unpaired backtick run that pairs with the NEXT one in the
document and swallows every line between them. And a bidi override opened inside a value
was never closed, so it kept reordering the display of the report's own headings for the
rest of the document; fencing the value as code does not help, because the control
characters are still in the character stream.

The replacement states the boundary instead of listing exploits: whatever goes in, what
comes out is one line, opened and closed by a backtick run that does not occur inside it,
with every bidi scope it opens closed again before it ends. `test_report_structure.py`
runs a 24-value hostile corpus through all five clauses, and separately drives the real
`moviola.main()` with a hostile title and a hostile uploader, which is the test that
fails if a call site ever stops fencing. Each fix was re-checked by restoring the bug:
unfencing Title and Uploader fails 3, the three-character collapse fails 10, dropping the
bidi balance fails 9, dropping the empty-value guard fails 1.

Deliberately still open, and written into the code and into `## Report as an untrusted
document` above rather than left implied: stderr is unfenced and carries yt-dlp's output
verbatim; ffprobe's width and height are interpolated raw on the evidence that ffprobe
emits them as numbers; `balance_bidi` is an approximation of UAX#9; and no markdown
parser checks the assembled document.

- **The three consent oracles now give one answer, and the fourth question got an owner.** moviola answers "will this upload my audio?" in three places and two languages — `whisper.resolve_backend()` (the runtime, and the only one that actually uploads), `setup.py --json` (what the agent parses), and `hooks/scripts/check-setup.sh` (the line the human reads at SessionStart) — and they disagreed. `$PWD/.env` was a key source for the runtime alone; both preflights read the config file only, so a key sitting in the working directory's `.env` uploaded audio underneath a "no backend configured" notice. That file is the checkout the user happens to be standing in, which for a Claude Code plugin may have been committed by someone they have never met, so it was dropped outright rather than taught to the preflights: reading it is the ambient-environment mistake wearing a different hat, taking a credential's presence for its owner's permission. A second, unreported bug surfaced while fixing the first, in mirror image — `setup.py::_have_api_key` re-derived the rule as "does a string named GROQ_API_KEY exist anywhere I can see", so an **ambient environment** key made `has_api_key: true`, `has_transcription: true`, `status: ready` while an unpinned run refused that same key and did frames only; `status` and `can_proceed` are both downstream of that one boolean. Both halves are the failure `_effective_backend`'s own docstring already warns about — do not re-derive precedence — never applied to `has_api_key`, and `cmd_install` had reintroduced it a third time one function away (`backend or 'local'` reported "groq" for an unpinned machine that would run local). The preflight now routes through `whisper.load_api_key` under the runtime's own rule, and reads the pin before the key question, because whether a key counts at all depends on the pin. `tests/test_consent_oracles.py` drives all three surfaces through their own front doors — two Python subprocesses and the real bash script, no monkeypatching of the thing under test — over a 12-row matrix, three tests per row so a failure names WHICH surface drifted; it went in RED at 11 failures, and every hook case passed, which is what identified the runtime rather than the preflights as the thing to change. **The fourth oracle** — "can anyone else read my key file?" — had drifted the same way: `setup.py` tested `mode & 0o044` (READ only) and stayed silent on a group-writable file, which is the worse case since another user can replace the key and bill their uploads to you; the bash hook tested `perms != "600" && perms != "400"`, a string comparison that warned about `700`, where nobody else has any access; and `whisper.py`, the surface that actually reads the key, never asked at all. One predicate now, `mode & 0o077`, owned by `whisper.warn_if_key_file_is_exposed` and called by all three, with the bash copy rewritten as the same arithmetic rather than a string match. Separately, `_announce_upload` was already unit-tested five ways and **all five passed with both of its call sites deleted** — it was proven correct and never proven to be called, so `tests/test_upload_is_announced.py` drives the real `transcribe_video` and snapshots stderr at the moment the first request is entered, making it an ordering assertion as well as a presence one. 72 new tests (281 -> 353). Six mutations each fail the suite: restoring `$PWD/.env` as a key source, re-opening the environment inside the preflight, both permission predicates reverted one at a time, and the two announcement call sites removed. **Non-goals, written into the code and the tests:** the oracle tests compare the three surfaces to each other and to a table, so a change moving all three the same wrong way passes; the permission check reads mode bits and is blind to directory modes, ACLs and mode-less filesystems including `/mnt/c` under WSL; consent is judged from where the key SITS, which cannot distinguish a config file the user wrote from one an installer wrote for them; and nothing here can tell whether the announcement is ever surfaced to a human. (consent-chain branch, 2026-08-26)

- **The paid API path is tested, shape-guarded, and announces what it is about to spend.** `_post_whisper`, `_build_multipart`, `_retry_after` and `_segments_from_response` had no tests at all — `tests/test_whisper.py` covers the pure maths around the call (chunk planning, timestamp shifting, range guards) and stops at the edge of the network. That gap is what let `_segments_from_response` trust the response shape: it called `.get()` on whatever a 200 body decoded to, so a JSON array or a bare string — a captive portal or a misrouted proxy answering 200 with HTML is the ordinary way to get one — raised `AttributeError` through a `try` that caught only `SystemExit`, taking the already-extracted frames and the whole report down with it. The parser now validates each level and degrades per-segment (a garbled timestamp costs the timestamp, not the text); `moviola.py`'s fallback block catches `Exception` as well, so an unexpected failure costs the transcript and leaves the report standing. `KeyboardInterrupt` is not an `Exception`, so Ctrl-C still ends a long local run — pinned by a test. Cost visibility was the other half: the frame path warns before it spends and this one did not, so `_announce_upload` now prints size, destination host and request count before the first request, and warns past an hour of audio naming `--start`/`--end`, `--no-whisper` and the local backend. 48 new tests (`tests/test_whisper_api.py`, plus a degradation class in `tests/test_moviola.py` driving `main()` against a clip that actually carries an audio stream — every shared fixture is `a=0`, which is why nothing had ever entered the whisper block). Five mutations each fail the suite: the naive parser, the dropped cost warning, `_retry_after` without its `ValueError` catch, 4xx retrying like a 5xx, and the removed broad catch. The re-review of that commit found the guard incomplete on its own terms and it was fixed before the gate passed: `float()` and `round()` accept `NaN` and the infinities without complaint, and `json.loads` admits the non-standard `NaN`/`Infinity`/`-Infinity` tokens by default, so those survived `_as_seconds` and blew up later at `int(seg["start"])` in `format_transcript` — which runs over the whole concatenated transcript, so one bad timestamp from one chunk would have discarded every segment, the exact trade the docstring says it avoids. `math.isfinite` is the check that sees them. The test named for that guarantee had `float("nan")` in its parametrize list and asserted only that a segment survived, never the value — a placebo for the one input where the guarantee did not hold. It now asserts the value, and `tests/test_consistency.py` gained two doc-vs-code checks so `COST_WARN_MINUTES` and `MAX_UPLOAD_BYTES` cannot drift away from the prose that quotes them; both are anchored on exact phrasings because the providers' cap is 25 MB and ours is 24 by design, and nothing can tell which cap a sentence means. **Four non-goals, stated in the code and pinned by tests:** the announcement does not cap or refuse anything — the enforcement boundary for a tool running under the user's own key is a per-key spend limit at the provider, now pointed at in SKILL.md — and its minutes are estimated from encoded bytes rather than probed with ffprobe; the tests never open a socket, so whether the real endpoints accept the multipart body this builds is outside what they can see; and the broad catch converts a total loss into a partial result with a named reason — it does not retry and does not make the transcript succeed. (ai-output review, 2026-08-26)
- **An API key in the process environment no longer counts as consent to upload.** The local-first flip was documented as meaning "a key present for some unrelated tool never silently causes an upload", and the code only delivered that on a machine where faster-whisper was importable — which is not the state a machine starts in. Without it, an unpinned run fell through to whatever `GROQ_API_KEY`/`OPENAI_API_KEY` it could see and uploaded the audio. The sentence was mine, written in the commit that was supposed to fix the precedence, and it outran its own evidence. `load_api_key` now takes `allow_env`, and `resolve_backend` passes `allow_env=False` when nothing is pinned, so an unpinned lookup reads keys only from `~/.config/moviola/.env` and the working directory's `.env`. A pin from either source restores the full lookup — pinning is the consent — and `MOVIOLA_WHISPER` is itself readable from the environment, so CI can still opt in without a config file. The refusal explains itself in three places (`transcribe_video`, `moviola.py`'s hint, and the SessionStart hook) and names the pin; none of them print the key. `hooks/scripts/check-setup.sh` duplicates this precedence in bash and was corrected in the same commit, since a hook announcing "ready — via the groq API" while a real run declines is the same class of lie as the pin bug it already carries a test for. Three layers mutation-tested: reverting `allow_env=False`, ignoring `allow_env` inside `load_api_key`, and reverting the hook's file-only lookup each fail the suite. **Two non-goals, written into the docstring, SKILL.md and a test so they cannot drift:** it cannot tell a key exported *for* moviola from one exported for another tool — both are just `os.environ` — so that user now needs a pin too; and it treats the working directory's `.env` as deliberate, as upstream does, though a project `.env` may belong to something else entirely — **that second non-goal was reversed on the consent-chain branch: `$PWD/.env` is no longer a key source at all, pinned or not.** (privacy review, 2026-08-26)
- **The offline switch now names a variable that file can actually deliver.** Six sites told the user to set `HF_HUB_OFFLINE=1` to stop the revision check against huggingface.co. That name works only as a real environment variable, and `~/.config/moviola/.env` is read by `config.py` for `MOVIOLA_*` keys and never exported into `os.environ` — so a user following the scaffolded template wrote a line that reached nothing and kept making the network call they had just turned off. Traced the working switch end to end before recommending it (`config.py:96` -> `moviola.py:155` -> `whisper.py:531` -> `local_whisper.py:449`) and pointed all six at `MOVIOLA_WHISPER_OFFLINE=1`, keeping the HF name documented as the environment-variable equivalent. A guard test now fails when the template names a setting nothing reads back out of that file; it was mutation-tested against the original bug and two variants. Two non-goals are written into its docstring: it must not fire on `MOVIOLA_WHISPER_CPU_THREADS`, which is read from the process environment by design, and it reads settings, not prose — a name mentioned mid-sentence in a comment is invisible to it. (privacy review, 2026-08-26)
- **`SKILL.md`'s "Which one runs" no longer states the opposite of the code.** It described API-first precedence — a key present wins, local is the fallback — after the default had been flipped to local-first, so the one paragraph a reader consults to answer "will this upload my audio?" answered it backwards. Raised independently by the ai-output reviewer (High) and privacy (Medium), and checked against `resolve_backend()` before acting rather than taken on report. It was true when written on the API-first upstream branch, carried through the rename with only a `WATCH_`->`MOVIOLA_` substitution, and invalidated by the flip; the fix was amended into the flip commit so that commit stops contradicting itself. That commit's own message had claimed "every place the precedence is observable now states the new ordering" while listing a subset — the false universal is precisely how the missed sentence shipped — and now enumerates all ten sites instead. (ai-output review, 2026-08-26)
- **The audio range is checked where it becomes an ffmpeg command line.** `--end` was validated only when `--start` was also given, so `--end 0` and `--end -5` reached ffmpeg and failed with "`-to` value smaller than `-ss`" — flags the user never typed. Reproduced, then fixed by comparing against the effective start; the same shape check now sits in `extract_audio()` so `transcribe_video()`'s callers get a named error too. It is deliberately not a bounds check: it cannot see the video's duration, so a range past the end of the file is still ffmpeg's to report. (report-escaping branch, 2026-08-26)
- **The faster-whisper version pin is held by a test, not a constant.** It appears in fourteen places — six runtime messages, the scaffolded `.env` template, `hooks/scripts/check-setup.sh` (found while writing the test), and README/SKILL/CHANGELOG prose. A shared constant reaches six of those and costs a module-level `local_whisper` import in `setup.py` and `whisper.py`, where the import is deliberately lazy and wrapped so a broken install degrades instead of crashing the preflight. `tests/test_consistency.py` reads the files instead: every pin must be the same string, and any pinned spec on a `pip install` line must be quoted. It does not check the pin is the *right* version — fourteen copies agreeing is not agreeing on something correct. (report-escaping branch, 2026-08-26)
- **Untrusted values in the report are now fenced as data.** `md_inline()` wraps yt-dlp's title and uploader, ffprobe's codec name and the source path in a backtick run one longer than the longest run inside the value (padded on both sides when an end is a backtick — CommonMark strips the pad only when it is symmetric), collapsing newlines because a line break ends the list item and lets everything after it become top-level markdown. `md_fence()` opens the transcript block with a fence the body cannot close early. Nothing is stripped or escaped: an ordinary title comes out as itself. Seven mutations were each confirmed to fail the suite. Two limits stated in the docstrings rather than fixed — this closes the STRUCTURAL channel only, so a transcript that says "ignore your previous instructions" is still legible text in the agent's context; and it cannot see the frames at all, so text rendered inside a video image is untouched. The title and uploader call sites are wired identically to the source path but are not exercised end-to-end, because reaching them needs yt-dlp metadata and the suite is network-free. (report-escaping branch, 2026-08-26)
- **Whisper runtime settings that faster-whisper defaults badly are now set deliberately.** `cpu_threads` is passed as the machine's *physical* core count (its literal default is `0`, which makes CTranslate2 choose 4 regardless of machine size); measured on a 6-core/12-thread Ryzen 4800H over 120 s of audio, `small`/int8/CPU, three rounds: 6 threads 9.5-9.7 s, 4 threads 11.2-12.4 s, 8 threads 11.6-12.1 s, 12 threads 11.8-12.0 s. `local_files_only` is now settable via `MOVIOLA_WHISPER_OFFLINE`, closing the one network call this backend makes (a revision check against huggingface.co on every warm load). A pre-set `OMP_NUM_THREADS` and a `taskset` affinity mask are both honoured; a platform whose core count cannot be read gets `0` and keeps the library default unchanged. (whisper-runtime branch, 2026-08-26)
- **Five smaller runtime defects fixed in the same pass.** The CPU compute type was hard-coded to `int8` while the CUDA side probed, with no fallback behind it; `.alt.` CUDA builds were preloaded alongside their non-alt twins, putting two definitions of the same symbols in one `RTLD_GLOBAL` namespace; `is_available()` discarded the import error, so a broken CTranslate2 or numpy ABI mismatch was reported as "faster-whisper is not installed"; `--whisper auto` was rejected by argparse, leaving no way to undo a `MOVIOLA_WHISPER` pin for one run; and `shift_segments()` returned the caller's own list at offset 0 while its docstring promised a copy. Each fix was mutation-tested — the mutant fails at least one test. (whisper-runtime branch, 2026-08-26)
- **Image-token arithmetic corrected in both documents.** `SKILL.md` claimed 80 frames at 512px cost 50-80k tokens; Anthropic charges `ceil(w/28) * ceil(h/28)`, so a 512x288 frame is 209 and 80 of them is ~17k (~21k at 4:3). `README.md`'s measured table used the deprecated `(w*h)/750` and ran ~6% low; it now carries exact products (10,450 / 20,900 / 24,244). The `--resolution 1024` claim was re-derived rather than assumed and restated as 3.7x, not "quadruples". (local-whisper branch, 2026-08-26)
- **Preflight-cost claim replaced with measured numbers.** `SKILL.md` and `README.md` both said the faster-whisper check was a "<100ms lookup"; measured five runs each way it is ~50 ms without the package and 250-300 ms with it, because the check does a real import. The import is deliberate — a present-but-broken install has to read as absent — so the documents were fixed, not the code. (local-whisper branch, 2026-08-26)
- **`SKILL.md:86` now carries the warm-load caveat.** The compact decision-aid bullet said only that the model is downloaded on first use; it now names the later revision check and points at **Security & Permissions**, matching line 228. (privacy review, 2026-08-26)
- **On-device Whisper backend via faster-whisper.** No API key, no audio upload; CUDA with automatic CPU fallback around the full transcription (not just model load, since CTranslate2 resolves CUDA libraries lazily); pip CUDA wheels preloaded so `libcublas` resolves. Backend precedence is local-first when unpinned so audio never leaves the machine by accident. (local-whisper branch, 2026-08-26)
- **`--start` / `--end` now clip the audio before transcription.** Input-side ffmpeg seeking plus a timestamp shift back into source time, so a focused run transcribes the range instead of the whole video. (local-whisper branch, 2026-08-26)
