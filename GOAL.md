# Goal: finish CivicCast Brief (set by Scott, 2026-11-05)

Scott: "finish all of these items. make it your goal."

The list is fixed at 24 items (from PLAN.md). Nothing is added without Scott's OK.
An item is done only when its "done when" is shown, not claimed. FAILED, BLOCKED or SKIPPED never count as done.

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Phase 0: TownRecord tests + one Longmont capture + `docs/townrecord-state.md` | done | docs/townrecord-state.md, commit 6b801b1: local 1113 pass/2 skip; origin/main 1466 pass/41 skip; grounding 67 pass/6 skip; one capture done, 8,032 segments |
| 2 | Phase 1: Scott listens to round 2 | done | Scott listened to rounds 2 and 3 and picked Step-Audio-EditX, 2026-11-06: "step audio - use that one for now." |
| 3 | Phase 1: default + backup voice recorded in `docs/voice-bakeoff.md` | default recorded; backup waits on Scott | docs/voice-bakeoff.md "Decision": default Step-Audio-EditX; backup not picked |
| 4 | Phase 2: recap → two-host script writer | built, committed | src/civiccast_brief/script; 72 script tests |
| 5 | Phase 2: no-new-claims checker | built, committed | checker + repair step; real script PASS after dropping 4 lines |
| 6 | Phase 2: writer model + GPU load order decided; one Longmont script passes | done | PLAN.md §6 (2026-11-06): deepseek-v4.1-flash:1017-cloud via Ollama; order diarize → writer/judge → voice. All 5 Longmont scripts from Phase 4 recaps pass the check (Oct 6 on rewrite; 4 after repair); no caption-garbled names in any script ("Prom McCoy" stays only inside a verbatim quote) |
| 7 | Phase 3: voice interface, Chatterbox + Dia2 | built, committed | commit 6b801b1, pushed; e2e on both engines. Step-Audio-EditX adapter (`stepaudio`, the default) added 2026-11-06 with a real e2e render |
| 8 | Phase 3: intro/outro, loudness, MP3, chapters, VTT | built; loudness choice waits on Scott | Chatterbox -17.0 LUFS plain, -16.1 with compressor; files sent 2026-11-05 |
| 9 | Phase 3: Scott plays one episode from a local feed | after 8 | |
| 10 | Phase 4: capture (TownRecord) + transcript with diarization (textflowkit) | done | commit a9423a7; 5 Longmont meetings (Sep 22, Oct 6, Oct 20, Oct 27, Nov 3, 2026) captured and diarized with pyannote 3.1 on the GPU, 36-54 voices each (scratch phase4, 2026-11-06) |
| 11 | Phase 4: speaker names from agenda/roll call, else "unknown" | done | names only by exact roll-call match to the roster; 1 named per Longmont meeting (YouTube captions garble most roll calls), the rest "unknown"; Granicus San Mateo: all 5 supervisors named |
| 12 | Phase 4: cited recap; five meetings | done | 27-49 claims per meeting after the claim judge, each with a verbatim quote and time; verify-report.json per meeting. Caveat: the checker confirms the quote is in the transcript, not that the claim follows from it |
| 13 | Phase 5: producer screen; unapproved items cannot reach export | done | commit e5225b6; tests/review/test_review_export_gate.py (approved item exports; approved-then-edited is refused until approved again); test_review_cli.py exports only approved items |
| 14 | Phase 6: templates ported + Craft player and look | built | commit e5225b6; templates home, episode (player block), transcript, about, how, corrections, search; Craft kit styles noted in base.html |
| 15 | Phase 6: sitemap, JSON-LD, share images, transcript pages, search, corrections, how-made | built | commit e5225b6; site/seo.py, images.py, static/search.js, corrections.html, how.html; tests/site (seo, images, pages, a11y) |
| 16 | Phase 6: feed tags; 30 days of Longmont click-through + feed validator | built; Longmont run waits on Scott | feed tags and link checker built and tested on fixtures (tests/site/test_feed.py, test_links.py). The site publishes only approved episodes with audio, so the 30-day Longmont build needs Scott's approvals and the voice pick (items 2-3). External feed validator not run yet |
| 17 | Phase 7: upload site and audio | parked by Scott | host: Cloudflare Pages (site) + R2 (MP3s), Scott 2026-11-05. Scott 2026-11-05: wire up civiccast.org later, not before |
| 18 | Phase 7: Apple Podcasts + Spotify listing | needs Scott (his accounts) | |
| 19 | Phase 8: email double opt-in + sender | needs Scott's mail provider pick | |
| 20 | Phase 9: weekly roundup episode | through script; audio waits on voice pick | commit 752ac2e (meetings in date order); roundup of the 5 Longmont recaps: 180 claims, script passes after repair (782 words), staged as draft item 6 in scratch phase4/review.db for Scott's review |
| 21 | Phase 9: Granicus adapter end to end | through script; audio waits on voice pick | commit 414918a; Oakland clip 7806: capture, recap, script, repair all exit 0 (scratch run 2026-11-06). San Mateo County clip 1984 also ran through script |
| 22 | Phase 10: per-city config, setup command, docs, GitHub release | built; release waits on voice pick | commit 9bf8e74 (city folders, `setup` command), docs/set-up-a-city.md. Walk-through from the guide only: San Mateo County Board of Supervisors (Granicus), meeting + script + repair exit 0, all 5 supervisors named from the roll call (title fix in 12aaf0e). Release held: the audio step needs the default voice (items 2-3) |
| 23 | Push the 2 local docs commits | done | pushed 1300c6e to origin/main, 2026-11-05 |
| 24 | Commit + push Phase 0/2/3 code once tested | done | commits 6b801b1, a695138 on origin/main |
