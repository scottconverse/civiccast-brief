# Runs on the voice PC in the Dia2 venv, not in this package; copied there at setup (docs/voice-bakeoff.md).
"""Chunked, reference-conditioned Dia2 renderer.

The script is split into groups of N lines. Each group is generated separately
with the same two reference clips as voice prefixes (speaker 1 and speaker 2),
then the groups are joined with a short silence gap. Prefix audio is cropped
from every chunk, as Dia2.generate() does by default.

Why not just call Dia2.generate() per chunk: generate() joins its input into
one string and parse_script() then sees one "line" (speaker index 0). A chunk
whose first word is [S2] therefore gets a stray [S1] token inserted before it.
This script builds the chunk entries with parse_script() on a 1- or 2-element
list (a leading empty line shifts the speaker index to 1 when the chunk starts
on [S2]). Everything else follows Dia2.generate() step for step.

--dry-run loads the model and prints the prefix transcript and the entry check
for every chunk, without generating audio.
"""

import argparse
import json
import os
import subprocess
import time

import numpy as np
from dia2 import Dia2, GenerationConfig, PrefixConfig, SamplingConfig
from dia2.audio.grid import undelay_frames, write_wav
from dia2.runtime.generator import (
    build_initial_state,
    decode_audio,
    run_generation_loop,
    warmup_with_prefix,
)
from dia2.runtime.script_parser import parse_script
from dia2.runtime.voice_clone import WhisperWord, build_prefix_plan


def make_transcribe(asr_model: str):
    """Prefix transcriber: whisper_timestamped word timings, cached per path.

    Dia2's default uses openai/whisper-large-v3, which is not in the local HF
    cache. This uses the local whisper model passed in (default small).
    """
    import whisper_timestamped as wts

    model_holder = {}
    cache = {}

    def transcribe(path, device):
        if path not in cache:
            if "m" not in model_holder:
                model_holder["m"] = wts.load_model(asr_model, device=str(device))
            res = wts.transcribe(model_holder["m"], path, language="en")
            words: list[WhisperWord] = []
            for seg in res.get("segments", []):
                for w in seg.get("words", []):
                    text = (w.get("text") or w.get("word") or "").strip()
                    if text:
                        words.append(
                            WhisperWord(
                                text=text,
                                start=float(w.get("start", 0.0)),
                                end=float(w.get("end", 0.0)),
                            )
                        )
            cache[path] = words
        return cache[path]

    return transcribe


def chunk_pad(lines: list[str]) -> int:
    """Speaker index shift so that parse_script assigns the chunk's first tag correctly."""
    return 1 if lines[0].lstrip().startswith("[S2]") else 0


def chunk_entries(runtime, lines: list[str]):
    """Script entries for one chunk, using the same parser as Dia2.generate()."""
    padded = [""] * chunk_pad(lines) + ["\n".join(lines)]
    return parse_script(padded, runtime.tokenizer, runtime.constants, runtime.frame_rate)


def generate_style_entries(runtime, lines: list[str]):
    """What Dia2.generate() would build for the same chunk (for the check only)."""
    return parse_script(
        ["\n".join(lines)], runtime.tokenizer, runtime.constants, runtime.frame_rate
    )


def dry_run(runtime, plan, chunks, n):
    tok = runtime.tokenizer
    fr = runtime.frame_rate
    print(f"prefix: {len(plan.entries)} words, {plan.aligned_frames / fr:.2f}s of reference audio")
    print("prefix transcript: " + " ".join(e.text for e in plan.entries))
    all_ok = True
    for i, cl in enumerate(chunks):
        mine = chunk_entries(runtime, cl)
        gen = generate_style_entries(runtime, cl)
        same = [(e.tokens, e.padding) for e in mine] == [(e.tokens, e.padding) for e in gen]
        if chunk_pad(cl) == 0:
            # S1-start chunks must match generate() exactly.
            all_ok = all_ok and same
        else:
            # S2-start chunks: generate() would insert a stray speaker tag. Report it.
            pass
        first_mine = tok.decode(mine[0].tokens) if mine else ""
        first_gen = tok.decode(gen[0].tokens) if gen else ""
        first_line = i * n + 1
        print(
            f"chunk {i + 1:02d} lines {first_line}-{first_line + len(cl) - 1} "
            f"words={len(mine)} start_tag_pad={chunk_pad(cl)} "
            f"matches_generate={same} first_word_mine={first_mine!r} first_word_generate={first_gen!r}"
        )
    print("S1-start chunks match Dia2.generate(): " + str(all_ok))


def render_chunk(runtime, plan, config, lines: list[str]):
    entries = list(plan.entries) + chunk_entries(runtime, lines)
    runtime.machine.initial_padding = config.initial_padding
    state = runtime.machine.new_state(entries)
    gen_state = build_initial_state(runtime, prefix=plan)
    start_step = warmup_with_prefix(runtime, plan, state, gen_state)
    first_word_frame, audio_buf = run_generation_loop(
        runtime,
        state=state,
        generation=gen_state,
        config=config,
        start_step=start_step,
        logger=None,
    )
    aligned = undelay_frames(
        audio_buf[0], runtime.audio_delays, runtime.constants.audio_pad
    ).unsqueeze(0)
    crop = max(int(first_word_frame), 0)
    if 0 < crop < aligned.shape[-1]:
        aligned = aligned[:, :, crop:]
    wav = decode_audio(runtime, aligned).detach().cpu().float().numpy()

    fr = runtime.frame_rate
    consumed = state.transcript[len(plan.entries) :]
    n_words = len(consumed)
    if n_words >= 2:
        span_s = (consumed[-1][1] - consumed[0][1]) / fr
        onset_wps = (n_words - 1) / max(span_s, 1e-6)
    else:
        onset_wps = float("nan")
    return wav, {
        "script_words": n_words,
        "model_onset_wps": round(onset_wps, 3),
        "crop_frames": crop,
    }


def main():
    ap = argparse.ArgumentParser(description="Chunked Dia2 render with reference prefixes")
    ap.add_argument("--script", required=True)
    ap.add_argument("--out", required=True, help="final WAV path")
    ap.add_argument("--ref1", required=True, help="speaker 1 reference WAV")
    ap.add_argument("--ref2", required=True, help="speaker 2 reference WAV")
    ap.add_argument("--lines-per-chunk", type=int, default=3)
    ap.add_argument("--cfg", type=float, default=6.0)
    ap.add_argument(
        "--temp", type=float, default=0.8, help="text and audio temperature (as dia2 CLI)"
    )
    ap.add_argument("--topk", type=int, default=50)
    ap.add_argument("--gap", type=float, default=0.3, help="seconds of silence between chunks")
    ap.add_argument(
        "--atempo", type=float, default=1.0, help="if not 1.0, also write an ffmpeg atempo copy"
    )
    ap.add_argument("--asr-model", default="small", help="whisper model for prefix transcripts")
    ap.add_argument("--repo", default="nari-labs/Dia2-2B")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(args.script, encoding="utf-8", errors="replace") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    n = args.lines_per_chunk
    chunks = [lines[i : i + n] for i in range(0, len(lines), n)]

    t_load = time.perf_counter()
    dia = Dia2.from_repo(args.repo, device="cuda", dtype="bfloat16")
    runtime = dia._ensure_runtime()
    load_s = time.perf_counter() - t_load

    config = GenerationConfig(
        text=SamplingConfig(temperature=args.temp, top_k=args.topk),
        audio=SamplingConfig(temperature=args.temp, top_k=args.topk),
        cfg_scale=args.cfg,
        cfg_filter_k=50,
        initial_padding=2,
        prefix=PrefixConfig(speaker_1=args.ref1, speaker_2=args.ref2, include_audio=False),
        use_cuda_graph=True,
    )
    plan = build_prefix_plan(runtime, config.prefix, transcribe_fn=make_transcribe(args.asr_model))
    if plan is None:
        raise SystemExit("prefix plan is empty; check ref1/ref2 paths")

    if args.dry_run:
        dry_run(runtime, plan, chunks, n)
        print(f"model load {load_s:.1f}s; dry run only, no audio written")
        return

    rate = runtime.mimi.sample_rate
    stem = os.path.splitext(args.out)[0]
    chunk_dir = stem + "_chunks"
    os.makedirs(chunk_dir, exist_ok=True)
    gap = np.zeros(round(args.gap * rate), dtype=np.float32)

    pieces = []
    stats = []
    render_wall = 0.0
    for i, cl in enumerate(chunks):
        t0 = time.perf_counter()
        wav, st = render_chunk(runtime, plan, config, cl)
        wall = time.perf_counter() - t0
        render_wall += wall
        chunk_path = os.path.join(chunk_dir, f"chunk_{i + 1:02d}.wav")
        write_wav(chunk_path, wav, rate)
        if pieces:
            pieces.append(gap)
        pieces.append(wav)
        row = {
            "chunk": i + 1,
            "first_line": i * n + 1,
            "lines": len(cl),
            "audio_s": round(len(wav) / rate, 3),
            "gen_wall_s": round(wall, 2),
            **st,
            "text": " ".join(cl),
        }
        stats.append(row)
        print(
            f"chunk {i + 1:02d}/{len(chunks)} lines {row['first_line']}-{row['first_line'] + len(cl) - 1} "
            f"audio={row['audio_s']:.2f}s wall={wall:.1f}s words={st['script_words']} "
            f"onset_wps={st['model_onset_wps']}",
            flush=True,
        )

    final = np.concatenate(pieces).astype(np.float32)
    write_wav(args.out, final, rate)
    total_audio = len(final) / rate
    summary = {
        "script": args.script,
        "out": args.out,
        "ref1": args.ref1,
        "ref2": args.ref2,
        "lines_per_chunk": n,
        "cfg": args.cfg,
        "temp": args.temp,
        "topk": args.topk,
        "gap_s": args.gap,
        "asr_model": args.asr_model,
        "model_load_s": round(load_s, 2),
        "render_wall_s": round(render_wall, 2),
        "total_audio_s": round(total_audio, 3),
        "chunks": stats,
    }
    if args.atempo != 1.0:
        atempo_path = f"{stem}-atempo{args.atempo:.2f}.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                args.out,
                "-filter:a",
                f"atempo={args.atempo}",
                atempo_path,
            ],
            check=True,
        )
        summary["atempo_out"] = atempo_path
        summary["atempo"] = args.atempo
    with open(stem + ".json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(
        f"wrote {args.out} audio={total_audio:.2f}s render_wall={render_wall:.1f}s "
        f"model_load={load_s:.1f}s chunks={len(stats)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
