# TODOS

Deferred work and known issues. Anything not done lives here, not in a PR body.

## Documentation accuracy

- **`skills/watch/SKILL.md:257` overstates image tokens by 3-4x.** It claims "80 frames at 512px wide is roughly 50-80k image tokens". Anthropic's current image token cost is `ceil(width/28) * ceil(height/28)`, so a 512x288 frame is `19 * 11 = 209` tokens and 80 of them is **~17k**; at 4:3 (512x384) it is `19 * 14 = 266` and **~21k**. The "50-80k" figure would only be right under a formula roughly 3x more expensive than the real one, and it makes the skill look far more costly than it is. The neighbouring claim on line 259 — `--resolution 1024` "roughly quadruples" per-frame tokens — does hold: 1024x576 is `37 * 21 = 777`, i.e. 3.7x. (local-whisper branch, 2026-08-26)

- **`README.md:91` cites the deprecated `(width x height) / 750` formula.** It yields ~197 tokens for a 512x288 frame against the correct 209, so the measured table above it (9.8k / 19.7k / 22.8k) is ~6% low — the right values are ~10.5k / ~20.9k / ~24.2k. Small, but the formula itself is the thing to replace, since it drifts further at other aspect ratios. Fix alongside the SKILL.md entry so the two documents don't disagree. (local-whisper branch, 2026-08-26)

## Untrusted input handling

- **`skills/watch/scripts/watch.py:296-299` prints `info['title']` and `info['uploader']` into the report unescaped.** Both come from `yt-dlp` metadata on an arbitrary remote video, and the report is markdown that goes straight into an agent's context. A title containing markdown, a fenced block, or instruction-shaped text is rendered as report structure rather than as data. Wrap both in backticks or strip control/markdown characters before printing. (local-whisper branch, 2026-08-26)

- **`skills/watch/scripts/watch.py:387-389` fences the transcript with a bare three-backtick fence.** A transcript line that itself contains three backticks closes the fence early and the remainder of the transcript escapes into the report body — the same injection surface as the title, reached through captions or Whisper output. Use a fence longer than the longest run of backticks in the content. (local-whisper branch, 2026-08-26)

## Local Whisper backend

- **No test covers a real local transcription.** `tests/test_local_whisper.py` covers availability, runtime resolution, CUDA preloading, error classification, precedence and dispatch, but every path that would actually run a model is mocked — a real end-to-end test needs a multi-hundred-MB model download and does not belong in a suite that is otherwise network-free. Verified by hand instead: `large-v3` int8_float16 on a GTX 1650 Ti transcribed a 38.6 s clip in 22 s including model load. If a CI job ever gets a model cache, add a `tiny`-model smoke test behind an opt-in marker. (local-whisper branch, 2026-08-26)

- **`WATCH_WHISPER_MODEL` accepts an arbitrary Hugging Face repo id or path with no validation beyond what `huggingface_hub` does.** That is deliberate — it is how anyone uses a fine-tune or a local conversion — but it means a typo'd or hostile repo id is fetched and loaded on the user's behalf. Documented as a non-goal in SKILL.md's security section rather than fixed. (local-whisper branch, 2026-08-26)

## Completed

- **On-device Whisper backend via faster-whisper.** No API key, no audio upload; CUDA with automatic CPU fallback around the full transcription (not just model load, since CTranslate2 resolves CUDA libraries lazily); pip CUDA wheels preloaded so `libcublas` resolves. Backend precedence is API-first when unpinned so existing key holders are unaffected. (local-whisper branch, 2026-08-26)
- **`--start` / `--end` now clip the audio before transcription.** Input-side ffmpeg seeking plus a timestamp shift back into source time, so a focused run transcribes the range instead of the whole video. (local-whisper branch, 2026-08-26)
