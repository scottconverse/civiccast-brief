"""Voice adapter and audio finishing (Phase 3).

- lines: parse `[S1]` / `[S2]` lines and `[CHAPTER Title]` marks.
- engines: the VoiceEngine protocol, ChatterboxRemote, Dia2Remote and FakeEngine.
- finish: stitching, loudness, MP3, chapters.json and WebVTT.
- cli: `python -m civiccast_brief.audio`.
"""
