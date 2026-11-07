# Goal: finish CivicCast Brief (set by Scott, 2026-11-05)

Scott: "finish all of these items. make it your goal."

The list is fixed at 24 items (from PLAN.md). Nothing is added without Scott's OK.
An item is done only when its "done when" is shown, not claimed. FAILED, BLOCKED or SKIPPED never count as done.

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Phase 0: TownRecord tests + one Longmont capture + `docs/townrecord-state.md` | done | docs/townrecord-state.md, commit 6b801b1: local 1113 pass/2 skip; origin/main 1466 pass/41 skip; grounding 67 pass/6 skip; one capture done, 8,032 segments |
| 2 | Phase 1: Scott listens to round 2 | done | Scott listened to rounds 2 and 3 and picked Step-Audio-EditX, 2026-11-06: "step audio - use that one for now." |
| 3 | Phase 1: default + backup voice recorded in `docs/voice-bakeoff.md` | done | docs/voice-bakeoff.md "Decision": default Step-Audio-EditX (Scott, 2026-11-06); backup Dia2 (Scott, 2026-11-07) |
| 4 | Phase 2: recap → two-host script writer | built, committed | src/civiccast_brief/script; 72 script tests |
| 5 | Phase 2: no-new-claims checker | built, committed | checker + repair step; real script PASS after dropping 4 lines |
| 6 | Phase 2: writer model + GPU load order decided; one Longmont script passes | done | PLAN.md §6 (2026-11-06): deepseek-v4.1-flash:1017-cloud via Ollama; order diarize → writer/judge → voice. All 5 Longmont scripts from Phase 4 recaps pass the check (Oct 6 on rewrite; 4 after repair); no caption-garbled names in any script ("Prom McCoy" stays only inside a verbatim quote) |
| 7 | Phase 3: voice interface, Chatterbox + Dia2 | built, committed | commit 6b801b1, pushed; e2e on both engines. Step-Audio-EditX adapter (`stepaudio`, the default) added 2026-11-06 with a real e2e render |
| 8 | Phase 3: intro/outro, loudness, MP3, chapters, VTT | done | Scott 2026-11-07: plain loudnorm, no compressor. 7 Step-Audio episodes (2026-11-07): -16.5 to -16.6 LUFS integrated (target -16), true peak -1.0 to -1.3 dBTP (target -1.5, so up to 0.5 dB over) |
| 9 | Phase 3: Scott plays one episode from a local feed | waits on Scott's approvals | 7 episodes rendered and staged in scratch phase4/review.db (2026-11-07) |
| 10 | Phase 4: capture (TownRecord) + transcript with diarization (textflowkit) | done | commit a9423a7; 5 Longmont meetings (Sep 22, Oct 6, Oct 20, Oct 27, Nov 3, 2026) captured and diarized with pyannote 3.1 on the GPU, 36-54 voices each (scratch phase4, 2026-11-06) |
| 11 | Phase 4: speaker names from agenda/roll call, else "unknown" | done | names only by exact roll-call match to the roster; 1 named per Longmont meeting (YouTube captions garble most roll calls), the rest "unknown"; Granicus San Mateo: all 5 supervisors named |
| 12 | Phase 4: cited recap; five meetings | done | 27-49 claims per meeting after the claim judge, each with a verbatim quote and time; verify-report.json per meeting. Caveat: the checker confirms the quote is in the transcript, not that the claim follows from it |
| 13 | Phase 5: producer screen; unapproved items cannot reach export | done | commit e5225b6; tests/review/test_review_export_gate.py (approved item exports; approved-then-edited is refused until approved again); test_review_cli.py exports only approved items |
| 14 | Phase 6: templates ported + Craft player and look | built | commit e5225b6; templates home, episode (player block), transcript, about, how, corrections, search; Craft kit styles noted in base.html |
| 15 | Phase 6: sitemap, JSON-LD, share images, transcript pages, search, corrections, how-made | built | commit e5225b6; site/seo.py, images.py, static/search.js, corrections.html, how.html; tests/site (seo, images, pages, a11y) |
| 16 | Phase 6: feed tags; 30 days of Longmont click-through + feed validator | waits on Scott's approvals | feed tags and link checker built and tested on fixtures (tests/site/test_feed.py, test_links.py). Scott 2026-11-07: check the feed by pasting it into the W3C Feed Validator. A demo feed already passed it (scratch w3c-feed-result2.html). The Longmont build needs the 5 meetings approved; audio for all 5 rendered 2026-11-07 |
| 17 | Phase 7: upload site and audio | parked by Scott | host: Cloudflare Pages (site) + R2 (MP3s), Scott 2026-11-05. Scott 2026-11-05: wire up civiccast.org later, not before. Scott 2026-11-07: keep parked |
| 18 | Phase 7: Apple Podcasts + Spotify listing | parked by Scott | needs the live site (17) and Scott's accounts. Scott 2026-11-07: keep parked |
| 19 | Phase 8: email double opt-in + sender | parked by Scott | needs a sending domain and Scott's mail provider account. Scott 2026-11-07: keep parked |
| 20 | Phase 9: weekly roundup episode | audio done; waits on Scott's approval | commit 752ac2e (meetings in date order); roundup script passes after repair (782 words), staged as item 6; audio rendered 2026-11-07 (313 s) |
| 21 | Phase 9: Granicus adapter end to end | audio done; waits on Scott's approval | commit 414918a; Oakland clip 7806: capture, recap, script, repair exit 0; staged as item 7, audio rendered 2026-11-07 (265 s). San Mateo County clip 1984 also ran through script |
| 22 | Phase 10: per-city config, setup command, docs, GitHub release | built; release after the items above | commit 9bf8e74 (city folders, `setup` command), docs/set-up-a-city.md, San Mateo County walk-through. Scott 2026-11-07: publish v0.1.0 once everything except 17-19 is done |
| 23 | Push the 2 local docs commits | done | pushed 1300c6e to origin/main, 2026-11-05 |
| 24 | Commit + push Phase 0/2/3 code once tested | done | commits 6b801b1, a695138 on origin/main |
