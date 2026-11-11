# Goal: finish CivicCast Brief, 28 items A to F (set by Scott, 2026-11-09)

Scott: "merge PR #30 and do the P&Z rule first. Then implement ALL 28 items, phased by letter (A through F). ... Make this your goal."

The list is fixed at 28 items. Nothing is added without Scott's OK. An item is done only when its "done when" is shown, not claimed. FAILED, BLOCKED or SKIPPED never count as done.

Scott's decisions, 2026-11-09:
- Podcasts are the main product; news stories are second.
- No separate TownRecord install, and none for textflowkit. The code CivicCast Brief needs is copied into CivicCast Brief. TownRecord is not a finished product.
- City setup must be in the program and must not need an AI to run.
- No paid services. Email goes through Brevo's free plan (Scott creates the account and enters the key himself).
- Phase Three is all 10 Boulder County towns and cities (Boulder, Longmont, Louisville, Lafayette, Superior, Erie, Lyons, Nederland, Jamestown, Ward) plus the Boulder County Commissioners, each with its council, all boards and schools.
- Items 24 and 25 wait until Scott says it is time.
- 2026-11-10, Scott: "so, 23 to do. Do them all now. Make that you're goal. Go." The goal is every open item on this list, A to F.
- 2026-11-10, Scott: "There will be no spotify or apple podcast accounts for testing. just make sure they follow the required formats for someone to fill them in and use mockups to test. Same thing with brevo." Items 24 and 25 now mean: the formats are met, the settings are documented for a person to fill in, and the code is tested against stand-ins.

| # | Item | Status | Evidence |
|---|---|---|---|
| **A** | **TownRecord Milestone 1** | | |
| 1 | Captions for Sustainability Board Oct 21 (other apps stay off YouTube) | open (partly) | TownRecord #35 (Data API key, merge d0fb0bd) and #36 (single-meeting match, merge 9d37c0f). Captions come through the Data API ladder; 0 429s, 6 quota units. youtubetotranscript.com answers 403 (Cloudflare); not worked around. The AI-tool refusal check on PrimeGov was not run |
| 2 | Show M1 passes: per-meeting result and the reason each non-aligned meeting did not align | open | Live M1 run: all 8 meetings with video captured and aligned (items aligned 3/14, 4/26, 41/48, 3/20, 6/17, 9/19, 25/34, 4/15); duplicate copy of video 72 caught. Not yet shown: why the unaligned items did not align (not reviewed); the two data-center checks cannot run from home. Report: scratch research/live-m1h/M1-REPORT.md |
| 3 | PR #30 description lists all 8 fixes | done | scottconverse/townrecord#30 body, 2026-11-09 |
| 4 | Merge PR #30 | done | merge commit a574028, 2026-11-09 |
| 5 | P&Z title rule (city name ignored; missing last word OK when one board fits; else skip and report) | done | scottconverse/townrecord#31, merge feecd4d. 5 tests, the main one fails on the old code. Live Longmont copy: P&Z titles find the Planning and Zoning Commission |
| **B** | **Phase One: the Longmont news site** | | |
| 6 | Evidence lookup (now inside CivicCast Brief, not a TownRecord server) | done | #5, merge a28d228. `python -m civiccast_brief evidence`; 77 tests. Real Oct 20 council record: 8,827 spans; a 4-sentence draft: 2 pass, the wrong tally and the uncited sentence fail |
| 7 | Speaker names: fix "unidentified speaker" (textflowkit code copied in) | open (improved) | #3, #8, #14 (merge 0d877be). Unidentified claims 107/159 (67%) to 93/169 (55%) on 5 meetings; 21 voices named. 12 spot checks against captions correct; not checked against audio. Naming more (captioner spellings, names not printed in the documents) needs Scott's OK |
| 8 | Article per Council meeting, every paragraph cited to the video and checked for new claims | done | #7, merge 26e58d3; #11 adds packet and minutes pages. 5 Longmont Council stories pass the no-new-claims check (claude -p); one walked through the desk and built as an episode page; video links open at the cited second |
| 9 | Automatic weekly run over all meetings and their documents | open | #9, #11, #13 merged. Live run 3 crashed on a closed portal client; fix in #15. Not yet shown: a full live run with documents, stories and auto-publish |
| 10 | Model picker per task (Claude `claude -p` for Council, DeepSeek for boards) | merged, not shown live for boards | #4 and #9. models.json: Claude for City Council, DeepSeek for other bodies. Council runs used claude -p live; no live board run yet |
| 11 | Editor desk: review, edit, redraft, hold, kill, publish; auto-publish allowed | merged, partly shown | #4. One story walked staged to Publish in the desk UI (#7). Hold, kill, redraft and auto-publish tested with stand-ins only |
| 12 | Add-source screen (part of the in-program city setup) | merged, not walked | #4 (Sources page) and #10 (Find a city's sources). Tests only; the screen has not been walked |
| 13 | Site pages; Corrections page and link removed | merged, not live | #6, merge ddec5ca. Waits on item 18 |
| 14 | Written roundup titles | done | #7. Real roundup title passed the check on the first try: "Longmont council weighs Ordinance 2026-63 and residential metropolitan districts" |
| 15 | civiccastbrief.org home page listing live cities | merged, not live | #6. Waits on item 18 |
| 16 | Logo A and the new look everywhere, self-hosted fonts | merged, not live | #6. Waits on item 18 |
| 17 | Cloudflare Web Analytics on every page; About, footer, README, docs | merged, not live | #6. Waits on item 18 |
| 18 | Deploy and walk the live site at desktop and phone widths | open | |
| **C** | **Phase Two** | | |
| 19 | All Longmont boards and commissions | done | #10, merge 4863890; #27, merge adc29b4 (PrimeGov looks back to 2015 for a board's last meeting). Live `city check` 2026-11-11: 28 of 29 PrimeGov committees read. The one not read, the Technology Policy Advisory Board, was created by Council on 2026-09-22 and has held no meeting yet |
| 20 | School district board(s) | done | #10. SVVSD 10/28/26 study session agenda read live |
| 21 | Charter school boards, if any | done | #10. Flagstaff Academy, SVCM and TPCA read live (TPCA through its Drive folder; its page answers 403) |
| 22 | In-program search-and-build step for a new city, no AI needed | done (Longmont) | #10. `city find` / `city check` use plain web requests, no model; Longmont 34 of 38 body sources read (37 of 38 after #27). Other towns are item 23 |
| **D** | **Phase Three** | | |
| 23 | All 10 Boulder County towns and cities plus the County Commissioners, each with council, boards and schools, set up with step 22 | done, one gap: Ward's town board posts no records online | Readers added: #22 and #23 (Laserfiche in the installed Chrome), #24 (documents to 150 MB), #25 (CivicClerk next pages), #26 (OneDrive shares and Word files), #27 (PrimeGov look-back). Live `city check` on main adc29b4, 2026-11-11, body sources read: County Commissioners 5/5, Boulder 22/22, Erie 11/11, Lafayette 21/21, Louisville 19/19, Lyons 16/16, Nederland 3/3, Superior 4/4, Jamestown 2/2, Longmont 37/38 (TPAB has not met yet), Ward 1/1 (Board of Education only). Ward has no town website: the old ward-co.org lapsed in 2022 (last minutes 2018-01-08) and Boulder County lists only a phone number and a Gmail address. Jamestown's Board of Trustees minutes are read from its OneDrive shares; no other Jamestown board posts records online. Louisville read 17/19 in three runs from 02:10 to 02:31 (first two Laserfiche folders timed out in Chrome), then 19/19 in two runs; cause not found. Rosters are filled for Longmont only |
| **E** | **Older open items** | | |
| 24 | Apple Podcasts and Spotify: feed meets both formats, settings documented, tested with stand-ins | done | scottconverse/civiccast-brief#2, merge 2891490. Live feed rebuilt and deployed 2026-11-10: no `<h2>`; Apple and Spotify stand-ins report no problems on the live feed. Branch podcast-and-brevo-formats: build checks in site/directories.py; tests/site/test_directories.py (24 pass locally). Live Longmont feed through the stand-ins: Spotify no problems; Apple flagged only the `<h2>` in episode notes, which this change removes (live site needs a rebuild). docs/podcast-directories.md. Not submitted to either service. Three live chapter titles are 59 to 62 characters; Apple's guide asks for 45 or fewer (not in Apple's submission checks) |
| 25 | Email through Brevo's API format, settings documented, tested with a stand-in; clear the `nobody@example.com` row | done | scottconverse/civiccast-brief#2, merge 2891490. Row sub-f5391f814da11cfbb8154536 deleted from live D1 2026-11-10 (1 change; Scott's confirmed row kept). worker/signup/src/brevo.ts, used when the BREVO_API_KEY secret is set; test/brevo.test.ts (13 pass locally, and fail when the api-key header or the header filter is broken). Setup steps in docs/email-signup.md. No real Brevo account used |
| **F** | **Issues** | | |
| 26 | YouTube shared limit: other apps paused for now; CivicCast Brief reads captions with the copied TownRecord code | open (partly) | Captions use the copied TownRecord ladder with Scott's Data API key (Credential Manager, not in the repo). 0 429s so far |
| 27 | Running on other machines: no separate installs; release work after A to E | open | |
| 28 | Someone other than me sets up a city with the in-program setup | open | |

## Earlier goal: the 24 PLAN.md items (set by Scott, 2026-11-05)

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
| 9 | Phase 3: Scott plays one episode from a local feed | done (checked by Claude) | Scott 2026-11-07: "i am not your tester. YOU check it." Local build served at 127.0.0.1:8770 (Longmont) and :8771 (Oakland). Claude played the roundup MP3 from its feed enclosure link and the player on the Nov 3 and Oakland episode pages: each played (time advancing, no media error). Scott has not played it himself |
| 10 | Phase 4: capture (TownRecord) + transcript with diarization (textflowkit) | done | commit a9423a7; 5 Longmont meetings (Sep 22, Oct 6, Oct 20, Oct 27, Nov 3, 2026) captured and diarized with pyannote 3.1 on the GPU, 36-54 voices each (scratch phase4, 2026-11-06) |
| 11 | Phase 4: speaker names from agenda/roll call, else "unknown" | done | names only by exact roll-call match to the roster; 1 named per Longmont meeting (YouTube captions garble most roll calls), the rest "unknown"; Granicus San Mateo: all 5 supervisors named |
| 12 | Phase 4: cited recap; five meetings | done | 27-49 claims per meeting after the claim judge, each with a verbatim quote and time; verify-report.json per meeting. Caveat: the checker confirms the quote is in the transcript, not that the claim follows from it |
| 13 | Phase 5: producer screen; unapproved items cannot reach export | done | commit e5225b6; tests/review/test_review_export_gate.py (approved item exports; approved-then-edited is refused until approved again); test_review_cli.py exports only approved items |
| 14 | Phase 6: templates ported + Craft player and look | built | commit e5225b6; templates home, episode (player block), transcript, about, how, corrections, search; Craft kit styles noted in base.html |
| 15 | Phase 6: sitemap, JSON-LD, share images, transcript pages, search, corrections, how-made | built | commit e5225b6; site/seo.py, images.py, static/search.js, corrections.html, how.html; tests/site (seo, images, pages, a11y) |
| 16 | Phase 6: feed tags; 30 days of Longmont click-through + feed validator | done | 2026-11-07: all 7 items approved in the producer screen 12:28-12:29 PM MST; Claude pressed Approve on Scott's instruction ("Approved", then option 2). Longmont site from the 6 approved episodes (Sep 22 to Nov 3, 2026). Link checker: 16 pages, 461 internal links, 0 broken (Oakland: 7 pages, 175 internal, 0 broken). Browser walk of all 23 pages: one heading each, no sideways scroll, no script errors, search works. Fixed 2 bugs found on the walk: home episode list squeezed to 0 px wide at 900-1300 px, and episodes published together listed oldest first. W3C Feed Validator (direct input), both feeds: "Congratulations! valid RSS". Two notes, expected for a pasted local feed: self link does not match the pasted location; "implausible date" on the Nov 7, 2026 publish time |
| 17 | Phase 7: upload site and audio | live | Scott 2026-11-07: "Unpark them". Domain civiccastbrief.org (Scott 2026-11-06; civiccast.org later, not before). https://longmont.civiccastbrief.org on Pages project civiccast-brief-longmont; MP3, chapters and VTT in R2 bucket civiccast-brief-media at https://media.civiccastbrief.org/longmont/. Checked 2026-11-07: 16 pages, 0 broken links, one h1 each, no overflow; audio plays from the media domain (range requests 206); W3C: valid RSS, no warnings. Fixed 2026-11-07: Cloudflare added a Web Analytics script on the custom domain (zone RUM off, no RUM site for this zone); the site now sends Cache-Control no-transform from a _headers file, so nothing is injected. Deploy with `wrangler pages deploy` (cf CLI direct upload ignores _headers). Checked in browser: no injected script, no CSP errors, no failed requests |
| 18 | Phase 7: Apple Podcasts + Spotify listing | unparked; waits on Scott | Scott 2026-11-07: "Unpark them". Feed live at https://longmont.civiccastbrief.org/feed.xml with owner email podcast@civiccastbrief.org (Scott 2026-11-07, option 1); Email Routing forwards it to sconverse@gmail.com. Needs Scott's own sign-in to Apple Podcasts Connect and Spotify for Creators |
| 19 | Phase 8: email double opt-in + sender | built and deployed; end-to-end test waits on Scott's confirm | Scott 2026-11-07: "Unpark them". Worker civiccast-brief-signup at https://signup.civiccastbrief.org (D1 civiccast-brief-signup, cron every 15 min, 22 tests). Free plan (Scott 2026-11-07, option 2): sends only to verified addresses; public sends need Workers Paid, Scott's purchase. Form taken off the site until then (Scott 2026-11-07). Done when a test address signs up, confirms and receives a new-episode email |
| 20 | Phase 9: weekly roundup episode | done | commit 752ac2e (meetings in date order); roundup script passes after repair (782 words); approved 2026-11-07 (item 6); audio 313 s; on the local Longmont site |
| 21 | Phase 9: Granicus adapter end to end | done | commit 414918a; Oakland clip 7806: capture, recap, script, repair exit 0; approved 2026-11-07 (item 7); audio 265 s; local Oakland site built, link check 0 broken, feed valid, audio plays. San Mateo County clip 1984 also ran through script |
| 22 | Phase 10: per-city config, setup command, docs, GitHub release | done | commit 9bf8e74 (city folders, `setup` command), docs/set-up-a-city.md, San Mateo County walk-through. Released v0.1.0 on 2026-11-07 at commit 2e7f06f (CI green): https://github.com/scottconverse/civiccast-brief/releases/tag/v0.1.0 |
| 23 | Push the 2 local docs commits | done | pushed 1300c6e to origin/main, 2026-11-05 |
| 24 | Commit + push Phase 0/2/3 code once tested | done | commits 6b801b1, a695138 on origin/main |
