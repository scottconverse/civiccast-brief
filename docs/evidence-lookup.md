# Look up the record for a meeting

`python -m civiccast_brief evidence` shows what the record database holds for one meeting and
checks a draft against it. It only reads the database.

```
python -m civiccast_brief evidence --video uHFuCfd5MVQ --out oct20.json
python -m civiccast_brief evidence --meeting 4 --check draft.txt --out check.json
```

- `--video` takes the YouTube video id; `--meeting` takes the record's meeting id.
- `--db` names the database (default `~/.civiccast-brief/record/record.db`).
- The pack holds the meeting's timed caption lines, the pages of its agenda, packet and minutes,
  and its stored votes. Each has a handle (`[E12]`) and the SHA-256 of the file it came from.
- The captions come from the video asked for, else the primary video. When that video has no
  transcript, another video of the same meeting that has one is used, and the first line says so.
- `--check` reads a draft. Every sentence must cite a handle, quote it word for word, and print
  no number, tally or name that the cited evidence does not hold. The command prints how many
  sentences stand and exits 2 when any does not.

On Longmont's October 20, 2026 council meeting the pack held 8,827 spans (764 record pages,
8,040 caption lines, 23 votes). A four-sentence test draft passed two sentences. It failed a
wrong tally ("7 to 0" for a 6 to 1 vote) and a sentence with no handle.

The checking code is copied from TownRecord (`record/grounding.py`).
