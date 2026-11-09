"""Speech on this machine: turning meeting audio into text, and telling voices apart.

This code was copied from textflowkit (https://github.com/scottconverse/textflowkit,
Apache-2.0) at commit 4c57e6e, so CivicCast Brief does not need textflowkit installed.
Only the parts CivicCast Brief uses were copied:

- ``whistle`` and ``whistle_assets``: the Whistle speech-to-text engine. It runs a small
  native program over one model file. Both are fetched once, checked by size and SHA-256,
  and kept in a per-user cache.
- ``diarize``: the speaker splitter. It needs the optional ``diarize`` extra (pyannote.audio).
- ``model``, ``cancel``, ``windows_process``, ``privacy``: what those modules import.

Run it as ``python -m civiccast_brief.speech``; see ``__main__``.
"""

#: The textflowkit commit this code was copied from.
COPIED_FROM = "textflowkit 4c57e6e"

#: The version this package reports. It names the engine, so a stored transcript says
#: which code made it.
VERSION = "whistle (copied from textflowkit 4c57e6e)"
