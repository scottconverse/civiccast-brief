# Voice bake-off

Status: rounds 1–3 done. Default voice: Dia2 (Scott, 2026-11-12). Step-Audio-EditX, picked on 2026-11-06, is a Linux program and was removed from the program on 2026-11-12.

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

## Round 2 (2026-11-05)

Long script: 42 lines, 855 spoken words, written by DeepSeek from the transcript and checked claim by
claim. Numbers, codes and addresses are spelled as words.

| Engine | Setup | 90 s script | Long script | Notes (not yet heard by Scott) |
|---|---|---|---|---|
| Chatterbox Turbo | Built-in female (S1); male (S2) cloned from VibeVoice `en-Carter_man.wav` | 92.6 s, 3.7x realtime | 327 s, rendered in 86 s (3.8x), 3.3 GB | Whisper: all 42 lines present, in order; address now read correctly. Leads to check by ear: "remote" may be dropped (0:36), "two hundred six" (3:17), "seventy-eight" (3:50), "one thousand nine hundred seventy-six" (5:17) |
| Dia2-2B | Fixed per-host voice prefixes, 2 lines per generation, cfg 3.0, temp 0.6, ffmpeg atempo 0.85 | A/B/C variants; C picked (2.41 words/s, WER 8.4%) | 351 s, 336 s render on a shared GPU | Pace steady (147 wpm first and last minute). WER 12–13% at start and end; several middle chunks 28–49% (some is digits-vs-words scoring, some looks like real misreads). Dia2's own pace did not change with sampling; only atempo slowed it. `generate()` adds a stray `[S1]` to chunks that start on `[S2]`; the chunked renderer avoids it |

## Round 3 (2026-11-06)

Scott asked for more natural news voices and back-and-forth. Three newer open models and one hosted
engine rendered the same 42-line long script. Reference voices for the cloning engines:
`en_woman.wav` (S1, Maya) and `en_man.wav` (S2, Theo) from the Higgs Audio repo's `examples/voice_prompts`.

| Engine | License / use terms | Long script | Render time | Loudness | Misreads (Whisper medium.en, besides digits) |
|---|---|---|---|---|---|
| **Step-Audio-EditX** (picked 2026-11-06, removed 2026-11-12: Linux only) | Apache-2.0 | 364 s (6:04) | 214 s | -18.7 LUFS | "setback variance" heard as "sent back variants" |
| Higgs Audio v2 | Boson community license | 331 s (5:31) | 414 s | -17.7 LUFS | none; 74 samples at 0 dBFS (possible slight clipping) |
| Voxtral 4B TTS | CC BY-NC 4.0 (non-commercial) | 441 s (7:21) | 152 s | -33.8 LUFS raw | "Saint Vrain" heard as "st rain"; reads slowly |
| Gemini Flash TTS (hosted, free tier) | hosted service, not open source; outside PLAN.md §6 | rendered | n/a | n/a | read word for word |

Not run: Orpheus (gated on Hugging Face; needs Scott to accept its terms) and ElevenLabs (needs Scott's sign-in).

Step-Audio-EditX needs vLLM, which has no Windows build, so it ran only in WSL. Scott, 2026-11-12: this is a
Windows-first shop, and the Linux program is not an option at all. Its adapter was removed.

### Dia2 setup

On the voice PC (Windows, NVIDIA GPU, reached over ssh), as plain Windows Python:

1. ffmpeg on PATH (`winget install --scope user Gyan.FFmpeg`).
2. `git clone https://github.com/nari-labs/dia2 C:\dev\civiccast-brief-bakeoff\dia2`, then `uv sync` in it
   (torch 2.9.1+cu128 on 2026-11-12).
3. Make the folder `C:\dev\civiccast-brief-bakeoff\dia2tune` and put three files in it:
   - The chunked renderer. Copy `src/civiccast_brief/audio/box/render_dia2_chunked.py` from this repo to
     `C:\dev\civiccast-brief-bakeoff\dia2tuneender_dia2_chunked.py` (the `Dia2Config.renderer` path). It
     runs in the Dia2 venv from step 2, not in the CivicCast Brief environment.
   - Two reference voice clips, `ref_s1.wav` (host 1, Maya) and `ref_s2.wav` (host 2, Theo), at the
     `Dia2Config.ref_s1` and `Dia2Config.ref_s2` paths. The repo does not include clips. Bring your own: two
     clean, single-speaker WAV clips of about 10 seconds each, one per host, that you have the right to use.
4. The Dia2-2B and Whisper small models download on the first render.

The paths are the defaults in `Dia2Config` in `src/civiccast_brief/audio/engines.py`; a `--config` file
can change them. The ssh host is `RemoteConfig.host`.

## Decision

- **Default: Dia2-2B.** Scott picked it on 2026-11-12, after Step-Audio-EditX turned out to be a Linux
  program. The CLI uses it when `--engine` is not given (round 2 settings, variant C). Chatterbox Turbo
  stays available with `--engine chatterbox`.
- **Removed: Step-Audio-EditX.** Scott, 2026-11-06, had picked it "for now". Scott, 2026-11-12: the Linux
  program is not an option at all.

Dia2 check run (2026-11-12, RTX 5060 Ti): a 4-line test script through `python -m civiccast_brief.audio
--engine dia2` rendered 19.1 s; Whisper heard every word. Step-Audio-EditX check run after wiring it in (2026-11-06): a 4-line test script through
`python -m civiccast_brief.audio` rendered 25.5 s in 1 min 42 s including model load, -16.6 LUFS,
-1.4 dBTP. Whisper heard every word; the only differences were numbers written as digits.
