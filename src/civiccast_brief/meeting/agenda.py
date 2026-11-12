"""The official agenda text: the spellings a recap may use for boards, streets and items.

The agenda is a plain-text file saved from the city's public agenda page, with a header line that
says where it came from. Its words are the vocabulary for the term check in verify.py: a capitalised
word in a claim must appear in the agenda or the roster, so a misheard board or street name (for
example "Gulf Course" for "Golf Course") is dropped rather than printed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

HEADER_PREFIXES = ("Source:", "Retrieved ")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")

#: Capitalised words that a recap may use without the agenda or roster naming them. The roster
#: adds its own city and state names (see verify.py).
GENERIC_WORDS = frozenset(
    {
        "Council", "Mayor", "Member", "Members", "Pro", "Tem", "Ward", "Representative",
        "Staff", "Resident", "Residents", "Speaker", "Chair", "City", "County", "State",
        "Public", "The", "A", "An", "I",
        # Days and months are dates, not names a caption could mishear.
        "January", "February", "March", "April", "May", "June", "July", "August",
        "September", "October", "November", "December",
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Agenda:
    source: str
    text: str
    vocabulary: frozenset[str]

    def excerpt(self, limit: int = 24000) -> str:
        """The agenda body, cut at a line boundary, for the recap prompt."""
        body = self.text
        if len(body) <= limit:
            return body
        cut = body.rfind("\n", 0, limit)
        return body[: cut if cut > 0 else limit] + "\n[... agenda continues]"


def _body_lines(raw: str) -> tuple[str, str]:
    """The source line and the agenda body. The body starts at the AGENDA heading when there is one,
    which drops the web page's own menu text saved above it."""
    source = ""
    body: list[str] = []
    for line in raw.splitlines():
        if line.startswith("Source:"):
            source = line.split(":", 1)[1].strip()
            continue
        if line.startswith(HEADER_PREFIXES):
            continue
        body.append(line)
    if "AGENDA" in body:
        body = body[body.index("AGENDA") :]
    return source, "\n".join(body).strip()


def load_agenda(path: Path | None) -> Agenda | None:
    """Read an agenda file. None when no agenda file is given: the term check then has less to go on."""
    if path is None:
        return None
    raw = Path(path).read_text(encoding="utf-8")
    source, body = _body_lines(raw)
    vocabulary = frozenset(w.lower() for w in _WORD_RE.findall(body) if len(w) > 1)
    return Agenda(source=source, text=body, vocabulary=vocabulary)
