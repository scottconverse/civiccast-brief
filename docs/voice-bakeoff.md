# Voice bake-off

Status: in progress

Phase 1 compares five free, open-source voice engines on the same two-host script: one 90-second
script from a real Longmont meeting, hand-written for this test. The default and fallback choices,
with licence verdicts, are recorded here when the bake-off ends.

## Engines

- **Dia2**: Apache-2.0. Native two-speaker dialogue.
- **Kokoro**: Apache-2.0. CPU-capable. Turns are stitched together.
- **Chatterbox Turbo**: MIT. Voice cloning. Output is watermarked.
- **Qwen3-TTS**: Apache-2.0 per an article. Not confirmed on the model page.
- **VibeVoice**: tagged MIT, but the model card says research use only. Output includes an audible disclaimer and a watermark.

## Results

| Engine | License | Runs on 5070 Ti | Naturalness | Civic text | Speed (x realtime) | Install effort | Verdict |
|---|---|---|---|---|---|---|---|
