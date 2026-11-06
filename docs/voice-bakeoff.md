# Voice bake-off

Status: round 1 done (90-second script, all five engines). Round 2 (5–7 minute script, top engines) in progress.

Phase 1 compares five free, open-source voice engines on the same two-host script, written from the
Longmont City Council regular session of October 20, 2026 (14 lines, 228 words). The script was
checked claim by claim against the meeting transcript before rendering.

Test box: nvideablackwell (RTX 5070 Ti 16 GB, Blackwell compute 12.0, PyTorch cu128 builds).

## Round 1 results (2026-11-05)

Listening verdicts are Scott's. Misreads come from a Whisper "small" pass over each output, so they are
leads to check by ear, not proof.

| Rank | Engine | License / use terms | Length | Speed | Peak VRAM | Listening verdict | Misreads (Whisper) |
|---|---|---|---|---|---|---|---|
| 1 | Chatterbox Turbo 0.1.7 | MIT; Perth watermark on every file; project says "retired research, no longer maintained" | 98 s | 3.7x realtime* | 3.2 GB | OK; slightly less stiff than Kokoro | "8513 St. Vrain" |
| 2 | Dia2-2B | Apache-2.0; README says "research and educational use"; 2 min cap per generation | 75 s | not measured alone | not measured | Natural for ~30 s, then quality drops and pace speeds up. Ranked 2nd if that is fixed | "8513", "Resolution", possibly "$200,000" |
| 3 | VibeVoice 1.5B (community fork) | MIT tag; model card: research use only; Microsoft removed the official TTS code 2025-09-05 | 101 s | 1.1x realtime* | 5.9 GB | Male voice good; female robotic; soft music under it. Ranked 3rd with the male voice only | "8513", "Ordinance", "700", "March 1" |
| 4 | Kokoro 0.9.4 | Apache-2.0 | 116 s | 20x realtime | small | Clear but stiff | "8513" |
| out | Qwen3-TTS 1.7B CustomVoice | Apache-2.0 | 120 s | 0.4x realtime* | 4.3 GB | Unusable; female voice has a Chinese accent (no native-English female preset) | "2026-69", "August 1" |

\* Rendered while other engines shared the GPU; solo speed not measured.

Every engine misread the street address "8513 St. Vrain Road". Scripts should spell numbers, codes and
addresses as spoken words ("eighty-five thirteen Saint Vrain Road"). This is a script-writer rule for Phase 2.

## Round 2 (in progress)

Long script: 42 lines, 855 spoken words, written by DeepSeek from the transcript and checked claim by
claim. Numbers, codes and addresses are spelled as words.

| Engine | Setup | 90 s script | Long script | Notes (not yet heard by Scott) |
|---|---|---|---|---|
| Chatterbox Turbo | Built-in female (S1); male (S2) cloned from VibeVoice `en-Carter_man.wav` | 92.6 s, 3.7x realtime | 327 s, rendered in 86 s (3.8x), 3.3 GB | Whisper: all 42 lines present, in order; address now read correctly. Leads to check by ear: "remote" may be dropped (0:36), "two hundred six" (3:17), "seventy-eight" (3:50), "one thousand nine hundred seventy-six" (5:17) |
| Dia2-2B | Fixed per-host voice prefixes, 2 lines per generation, cfg 3.0, temp 0.6, ffmpeg atempo 0.85 | A/B/C variants; C picked (2.41 words/s, WER 8.4%) | 351 s, 336 s render on a shared GPU | Pace steady (147 wpm first and last minute). WER 12–13% at start and end; several middle chunks 28–49% (some is digits-vs-words scoring, some looks like real misreads). Dia2's own pace did not change with sampling; only atempo slowed it. `generate()` adds a stray `[S1]` to chunks that start on `[S2]`; the chunked renderer avoids it |

Default and fallback are recorded here when Scott has listened.
