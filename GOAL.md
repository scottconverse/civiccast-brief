# Goal: finish CivicCast Brief (set by Scott, 2026-11-05)

Scott: "finish all of these items. make it your goal."

The list is fixed at 24 items (from PLAN.md). Nothing is added without Scott's OK.
An item is done only when its "done when" is shown, not claimed. FAILED, BLOCKED or SKIPPED never count as done.

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Phase 0: TownRecord tests + one Longmont capture + `docs/townrecord-state.md` | running | |
| 2 | Phase 1: Scott listens to round 2 | waiting on Scott | files sent 2026-11-05 |
| 3 | Phase 1: default + backup voice recorded in `docs/voice-bakeoff.md` | after 2 | |
| 4 | Phase 2: recap → two-host script writer | running | |
| 5 | Phase 2: no-new-claims checker | running | |
| 6 | Phase 2: writer model + GPU load order decided; one Longmont script passes | running | |
| 7 | Phase 3: voice interface, Chatterbox + Dia2 | built, uncommitted | 134 tests pass, ruff clean (rerun by coordinator 2026-11-05); e2e on both engines |
| 8 | Phase 3: intro/outro, loudness, MP3, chapters, VTT | built; loudness choice waits on Scott | Chatterbox -17.0 LUFS plain, -16.1 with compressor; files sent 2026-11-05 |
| 9 | Phase 3: Scott plays one episode from a local feed | after 8 | |
| 10 | Phase 4: capture (TownRecord) + transcript with diarization (textflowkit) | open | |
| 11 | Phase 4: speaker names from agenda/roll call, else "unknown" | open | |
| 12 | Phase 4: cited recap; five meetings hand-checked | open | |
| 13 | Phase 5: producer screen; unapproved items cannot reach export | open | |
| 14 | Phase 6: templates ported + Craft player and look | open | |
| 15 | Phase 6: sitemap, JSON-LD, share images, transcript pages, search, corrections, how-made | open | |
| 16 | Phase 6: feed tags; 30 days of Longmont click-through + feed validator | open | |
| 17 | Phase 7: upload site and audio | host picked: Cloudflare Pages (site) + R2 (MP3s), Scott 2026-11-05 | |
| 18 | Phase 7: Apple Podcasts + Spotify listing | needs Scott (his accounts) | |
| 19 | Phase 8: email double opt-in + sender | needs Scott's mail provider pick | |
| 20 | Phase 9: weekly roundup episode | open | |
| 21 | Phase 9: Granicus adapter end to end | open | |
| 22 | Phase 10: per-city config, setup command, docs, GitHub release | open | |
| 23 | Push the 2 local docs commits | done | pushed 1300c6e to origin/main, 2026-11-05 |
| 24 | Commit + push Phase 0/2/3 code once tested | after 1, 4–8 | |
