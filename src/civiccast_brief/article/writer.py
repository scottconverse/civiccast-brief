"""Recap to news story, and the check that the story adds no claim the evidence lacks.

The writer sees the recap's claims and, for each claim, the captions said around its moment in
the meeting video. The model returns one JSON object: headline, dek, sections of paragraphs,
votes and quotes. Every paragraph, vote and quote names the claims it relies on. Each claim
carries a verbatim quote and the time in the video where it starts, so every part of the story
is cited to the video through its claims.

With the meeting's documents (``documents.json``: the agenda packet and the minutes), the
writer also sees the pages that share names and numbers with the claims, and a paragraph may
cite those pages as well as its claims.

The check is deterministic and runs on every part:

- every paragraph, vote and quote cites at least one claim, and only claims the recap holds;
  a paragraph cites only pages the documents hold;
- every number in it is in the cited claims, their captions or the cited pages (digits and
  number words alike);
- every capitalised name in it is in the cited claims, their captions, the cited pages, or the
  meeting header.

The headline and dek may use any claim the story cites; a section heading, any claim its
paragraphs cite. A quote's words are the claim's own verbatim quote, never the model's.
``drop_unsupported`` removes failing paragraphs, votes and quotes, and blanks a failing heading
or dek. It can only take facts out. A failing headline cannot be dropped, so the story fails.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from civiccast_brief.article.names import People, fixes, name_issues
from civiccast_brief.script.checker import _claim_words, _proper_nouns, header_text
from civiccast_brief.script.numbers import number_atoms
from civiccast_brief.script.recap import Recap, seconds_of

#: Fixed words the story prints itself. The model never writes these.
NOTE = (
    "This article was written by AI from the public meeting video. Each paragraph links to the "
    "moment in the video it comes from. Check the official record."
)
#: The note for a story that also cites the meeting documents.
NOTE_DOCUMENTS = (
    "This article was written by AI from the public meeting video and the meeting documents. "
    "Each paragraph links to the moment in the video and the document pages it comes from. "
    "Check the official record."
)
#: How many document pages the writer is shown, and how much of each.
PAGE_LIMIT = 12
PAGE_CHARS = 2500
#: Words a headline or paragraph may use without a claim: the publication's own name.
HOUSE_WORDS = frozenset({"civiccast", "brief"})
#: "One speaker" is how the prompt tells the writer to name an unidentified speaker. It is not
#: a count.
_ONE_SPEAKER_RE = re.compile(r"\b[Oo]ne (?=(?:speaker|resident|commenter)\b)")
#: Common words that start a news sentence. Capitalised there, they are not names.
ARTICLE_OPENERS = frozenset(
    [
        "also",
        "later",
        "earlier",
        "during",
        "following",
        "before",
        "after",
        "separately",
        "meanwhile",
        "in",
        "on",
        "at",
        "with",
        "under",
        "without",
        "among",
        "several",
        "both",
        "other",
        "others",
        "most",
        "no",
        "none",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "members",
        "speakers",
        "residents",
        "public",
        "a",
        "an",
        "the",
    ]
)
_SENTENCE_START_RE = re.compile(r"(^|[.!?:]\s+)([A-Z][a-z]+)")
#: Caption window around a claim's time, in seconds: a little before the quote, more after it.
WINDOW_BEFORE = 20
WINDOW_AFTER = 60


def _names(text: str) -> list[str]:
    """Capitalised names in a text, after lowering a common word that starts a sentence."""

    def lower(match: re.Match[str]) -> str:
        word = match.group(2)
        return match.group(1) + (word.lower() if word.lower() in ARTICLE_OPENERS else word)

    return _proper_nouns(_SENTENCE_START_RE.sub(lower, text))


class ArticleFormatError(ValueError):
    """The model answer is not a story in the agreed format."""


@dataclass(frozen=True)
class Paragraph:
    text: str
    claim_ids: tuple[str, ...]
    #: Document pages the paragraph also cites ("d1p17": document 1, page 17).
    page_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocPage:
    """One page of a meeting document, as ``documents.json`` holds it."""

    id: str
    kind: str
    url: str
    page: int
    text: str

    @property
    def link(self) -> str:
        return f"{self.url}#page={self.page}"


def load_documents(path: Path) -> dict[str, DocPage]:
    """Every page of ``documents.json`` by its id, "d<document>p<page>" counting from 1."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pages: dict[str, DocPage] = {}
    for number, document in enumerate(data.get("documents") or [], start=1):
        for page in document.get("pages") or []:
            page_id = f"d{number}p{int(page['page'])}"
            pages[page_id] = DocPage(
                page_id,
                str(document.get("kind", "")),
                str(document["url"]),
                int(page["page"]),
                str(page.get("text") or ""),
            )
    return pages


@dataclass(frozen=True)
class Section:
    heading: str | None
    paragraphs: tuple[Paragraph, ...]


@dataclass(frozen=True)
class Vote:
    motion: str
    result: str
    tally: str | None
    claim_id: str


@dataclass(frozen=True)
class Quote:
    claim_id: str
    speaker: str


@dataclass(frozen=True)
class Article:
    headline: str
    sections: tuple[Section, ...]
    dek: str = ""
    votes: tuple[Vote, ...] = ()
    quotes: tuple[Quote, ...] = ()

    @property
    def paragraphs(self) -> tuple[Paragraph, ...]:
        return tuple(p for s in self.sections for p in s.paragraphs)

    @property
    def cited(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for paragraph in self.paragraphs:
            for claim_id in paragraph.claim_ids:
                seen.setdefault(claim_id, None)
        return tuple(seen)

    @property
    def cited_pages(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(i for p in self.paragraphs for i in p.page_ids))


def caption_windows(
    recap: Recap, segments: list[Any], before: int = WINDOW_BEFORE, after: int = WINDOW_AFTER
) -> dict[str, str]:
    """For each claim, the meeting captions said around its time, verbatim.

    The claims are short and the recap's topics are not verified, so without these the story
    cannot say which item a vote was on. A name or number in the window was said in the meeting
    at that moment, so the check accepts it for a part that cites the claim.

    A turn whose speaker the transcript names (from the roll call, a chair hand-off or the
    producer) starts with that name and a colon, so the story may say who spoke.
    """
    windows: dict[str, str] = {}
    for claim in recap.claims:
        at = seconds_of(claim.timestamp)
        words = []
        previous = ""
        for s in segments:
            if not (at - before <= s.start <= at + after and s.text):
                continue
            name = str(getattr(s, "speaker", None) or "").strip()
            known = name and name.lower() != "unknown"
            # A caption mark opens a turn; a whisper transcript has none, so a change of the
            # named speaker opens one too.
            named = known and (s.turn_start or name != previous)
            words.append(f"{name}: {s.text}" if named else s.text)
            previous = name if known else previous
        windows[claim.id] = " ".join(" ".join(words).replace(">>", " ").split())
    return windows


def claim_speakers(recap: Recap, segments: list[Any], roster: Any) -> dict[str, str]:
    """For each claim whose quote starts on a line the record names, the speaker's name.

    The names come from the transcript's own roll call and chair recognitions, read with the
    roster (:mod:`civiccast_brief.meeting.speakers`). A claim whose moment has no name stays
    out of the mapping, so the story says "a speaker" rather than guessing. The transcript is
    labelled in place, so a later caption window shows the same names.
    """
    from civiccast_brief.meeting.speakers import (
        apply_turns,
        label_recognitions,
        label_roll_call,
        split_turns,
    )

    if not segments or roster is None:
        return {}
    turns = split_turns(segments)
    label_roll_call(turns, roster)
    label_recognitions(segments, turns, roster)
    apply_turns(segments, turns)
    out: dict[str, str] = {}
    for claim in recap.claims:
        at = seconds_of(claim.timestamp)
        found = None
        for segment in segments:
            if segment.start <= at:
                found = segment
            else:
                break
        name = str(getattr(found, "speaker", None) or "").strip() if found else ""
        if name and name.lower() != "unknown":
            out[claim.id] = name
    return out


PROMPT = """You write a news story about one public meeting for a local news site.

FACTS: use only the claims listed at the end. Every fact, number, name, vote, date and outcome must come from a claim. Add no causes, reasons, background or outside context. Do not invent who a speaker is: when a claim says "a speaker" or "an unidentified speaker", write "one speaker" or "a speaker", never "an unidentified speaker", unless the record supports a role: someone who says they are resigning a council seat is "a council member", and a town employee presenting an item is "town staff". A claim may carry "speaker": the meeting record's own name for the person who said it; use that name instead of "a speaker". A claim may carry "captions": the words said in the meeting around that claim's moment, from automatic captions. Use them for the detail a reader needs (what the item is, the ordinance or resolution number, the address, the project, the board, what was said) and to say who spoke when the captions name them, so that no paragraph leaves the reader asking which item it means. Every fact itself must still be stated by a claim. Captions can be garbled; leave out a name or number that looks garbled. Never add a name the claims and captions do not give: a council member or staff member is named only when the claim or its captions name that person, and a member of the public only when they give their name on the record.

NEWS JUDGMENT (this decides the order, not the agenda's):
1. Lead with the most newsworthy thing in the meeting: a resignation, removal or vacancy; a vote on money (a budget, contract, fee, tax, purchase, grant), land (annexation, zoning, development) or a law (ordinance, resolution); a divided or contested vote; a decision that changes what residents must do; public outcry or a crowd at public comment.
2. A routine consent agenda, the approval of minutes, a proclamation, an appointment or a meeting date is not the lead. Put those at the end in one short section headed "Also on the agenda".
3. Within the lead, say what happened, who did it (the body and, when the record names them, the members), the vote if there was one, who voted how, the amount or date if there was one, and what happens next when a claim says it.
4. Give context a reader needs from the meeting itself: what the item does, who spoke for and against it, and the numbers. Use every claim that adds news: each vote, figure, date, outcome, presenter point and point of public comment. Do not pad and do not repeat a fact to reach a length.
5. A vote claim whose captions name the members in a roll call ("Neal Shah: Yes.") is written out: name each member and how they voted, and the tally. The captions are the record of the vote.
6. Do not start sentence after sentence with "A speaker said". When the record gives no name, use the role the captions show (a resident during public comment, a business owner, town staff, the applicant, a council member) or fold the statement into its item ("The council heard that...", "Under the contract, ..."). "A speaker" belongs in a section at most once, and never twice in one paragraph.
7. Announcements, event dates, tours, recognitions and reminders are not news: put each one once, in one sentence, in ONE short paragraph under "Also on the agenda". Never give them a section or a heading of their own. Never repeat an announcement, and never pass on words the captions garbled.

OUTPUT: one JSON object and nothing else, no code fence:
{"headline": "...", "dek": "...",
 "sections": [{"heading": "..." or null, "paragraphs": [{"text": "...", "claim_ids": ["c03", "c04"]}]}],
 "votes": [{"motion": "...", "result": "passed" or "failed", "tally": "6-1" or null, "claim_id": "c05"}],
 "quotes": [{"claim_id": "c07", "speaker": "..."}]}
- claim_ids lists every claim the paragraph relies on. Every paragraph cites at least one claim.
- headline: the actual news, naming the town and what happened, in 14 words or fewer, the way a newspaper would print it ("Superior council member announces resignation", "Superior council splits on $2 million budget"). Say what happened, not that the council "heard" or "discussed" it. It must not be a consent-agenda item when the meeting has bigger news. dek: one sentence, 30 words or fewer, with the next most important news.
- sections: the first has heading null and gives the lead, naming the body and the meeting date from the header. Then one section per other item or theme, most important first, with a short heading naming the item; a section on public comment if claims cover it; "Also on the agenda" last for the routine items.
- votes: every vote a claim states, one entry each, with the claim that states it. tally only when the claim gives the count. When the claims or captions say how individual members voted, give the tally and say in the body who voted how.
- quotes: two to four claims whose quote is worth printing. The quote's words are taken from the claim, so give only its id and the speaker as the claim or its captions name them, else "A speaker".

STYLE: plain news writing in the past tense, inverted pyramid: the most important facts first, the least important last. Use digits for numbers (6-1, $200,000, Ordinance 2026-69). Every paragraph is two to four full sentences; a one-sentence paragraph is too thin to publish. Write 8 to 14 paragraphs and roughly 600 to 900 words when the meeting has that much news, fewer only when it does not. Never repeat a fact. Spell boards, streets and items as the claims or captions do.
"""

ROUNDUP_PROMPT = """You write the week's news story for a local news site, covering several public meetings of one town.

FACTS: use only the claims listed at the end. Every fact, number, name, vote and date must come from a claim, and every claim's topic says which meeting it came from ("September 28, 2026: ..."). Add no causes, reasons, background or outside context. Do not invent who a speaker is: name a council or board member only when the claim or its captions name that person; otherwise use the role the record supports (someone resigning a council seat is "a council member", a presenter is "town staff", a public commenter is "a resident") or "a speaker". Never write "an unidentified speaker".

NEWS JUDGMENT: this is one story about the week, not a list of meetings. Lead with the biggest news across all the meetings: a resignation, removal or vacancy; a vote on money, land or a law; a divided or contested vote; a decision that changes what residents must do; public outcry. Then the next most important news, then the routine items at the end in a short section headed "Also this week". Name the meeting (its date and body) each group of facts comes from, so a reader knows which meeting a fact belongs to. Do not pad and do not repeat a fact. Use every claim that adds news. Announcements, event dates, tours, recognitions and reminders are not news: the roundup's "Also this week" holds the week's real agenda items, not announcements. Put a real item once, and never pass on words the captions garbled. Do not start sentence after sentence with "A speaker said": use the role the captions show (a resident, a business owner, town staff, a council member), fold the statement into its item, and use "a speaker" at most once in a section.

OUTPUT: one JSON object and nothing else, no code fence:
{"headline": "...", "dek": "...",
 "sections": [{"heading": "..." or null, "paragraphs": [{"text": "...", "claim_ids": ["c03", "c04"]}]}],
 "votes": [{"motion": "...", "result": "passed" or "failed", "tally": "6-1" or null, "claim_id": "c05"}],
 "quotes": [{"claim_id": "c07", "speaker": "..."}]}
- claim_ids lists every claim the paragraph relies on. Every paragraph cites at least one claim.
- headline: the week's biggest news, naming the town, in 14 words or fewer, the way a newspaper would print it ("Superior council member announces resignation", "Superior council splits on $2 million budget"). Never write "council speaker" or "a speaker" in the headline when the news itself can be named. dek: one sentence, 30 words or fewer, with the next most important news.
- sections: the first has heading null and gives the lead. Then one section per theme, most important first, and "Also this week" last.
- votes: every vote a claim states, with the claim that states it; tally only when the claim gives the count.
- quotes: two to four claims whose quote is worth printing, with the speaker as the claim or its captions name them, else "A speaker".

STYLE: plain news writing in the past tense, inverted pyramid. Use digits for numbers. Two to four sentences per paragraph; 8 to 14 paragraphs and roughly 600 to 900 words when the week has that much news. Never repeat a fact.
"""

#: What makes a claim the lead, high to low. A resignation outranks any vote; a vote on money,
#: land or a law outranks public comment; a routine consent item is last.
NEWS_WEIGHTS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (
        100,
        re.compile(
            r"\b(resign|resignation|resigned|step(?:s|ped)? down|vacan(?:t|cy)|withdraw|"
            r"removed from|expelled|recall)\b",
            re.IGNORECASE,
        ),
    ),
    (
        70,
        re.compile(
            r"\b(budget|contract|fee|tax|levy|bond|purchase|grant|salary|rate|"
            r"annex|zoning|rezoning|development|subdivision|ordinance|resolution|"
            r"eminent domain|land|property|lease)\b|\$\s?\d",
            re.IGNORECASE,
        ),
    ),
    (
        60,
        re.compile(
            r"\b(voted no|divided|split vote|contested|failed|opposed|opposition|outcry|"
            r"protest|petition|packed|stood up|walked out)\b",
            re.IGNORECASE,
        ),
    ),
    (
        35,
        re.compile(r"\b(public comment|resident|speaker asked|asked the council)\b", re.IGNORECASE),
    ),
    (
        0,
        re.compile(
            r"\b(consent agenda|minutes|approve the agenda|roll call|proclamation|"
            r"appointment|meeting schedule|adjourn)\b",
            re.IGNORECASE,
        ),
    ),
)


def news_value(claim: Any) -> int:
    """How much of a lead a claim is: a resignation 100, a money/land/law vote 70, a contested
    vote 60, public comment 35, a routine item 0, anything else 10 (the highest match wins)."""
    text = " ".join(str(part or "") for part in (claim.topic, claim.text, claim.quote))
    for weight, pattern in NEWS_WEIGHTS:
        if pattern.search(text):
            return weight
    return 10


def news_order(recap: Recap) -> list[str]:
    """Claim ids, the strongest news first, then in the recap's own order."""
    ranked = sorted(enumerate(recap.claims), key=lambda pair: (-news_value(pair[1]), pair[0]))
    return [claim.id for _, claim in ranked]


#: A lead on nothing but routine items, while the story cites a resignation or a vote on money.
LEAD_MIN_VALUE = 60


def lead_is_routine(recap: Recap, article: Article) -> str | None:
    """The claim id that should have led, when the lead rests only on routine claims.

    The lead is the first paragraph. It is routine when every claim it cites scores below
    :data:`LEAD_MIN_VALUE`, and the story cites a claim that scores at least that high.
    Returns that higher claim's id, or None.
    """
    lead = article.paragraphs[0] if article.paragraphs else None
    if lead is None:
        return None
    known = {c.id: c for c in recap.claims}
    lead_value = max((news_value(known[i]) for i in lead.claim_ids if i in known), default=0)
    if lead_value >= LEAD_MIN_VALUE:
        return None
    order = news_order(recap)
    if not order:
        return None
    best = order[0]
    if news_value(known[best]) >= LEAD_MIN_VALUE and best not in lead.claim_ids:
        return best
    return None


DOCUMENTS_PROMPT = """DOCUMENTS: the pages listed under "Document pages" are from the meeting's own documents (the agenda packet, the minutes). A paragraph may also rely on them: list the page ids it uses in "pages", for example {"text": "...", "claim_ids": ["c03"], "pages": ["d1p17"]}. Every paragraph still cites at least one claim, and the page must be about the same item as that claim. Use a page for the item's number, title, amounts and names, and for a vote as the minutes record it. Never cite a page for something it does not say.
"""


def _tokens(text: str) -> set[str]:
    """The names and numbers of a text that can tie a page to a claim."""
    numbers = {n for n in number_atoms(text) if len(n) >= 3}
    return numbers | {n.lower() for n in _names(text) if len(n) >= 4}


def pick_pages(
    recap: Recap,
    pages: dict[str, DocPage],
    windows: dict[str, str] | None = None,
    limit: int = PAGE_LIMIT,
) -> list[DocPage]:
    """The pages that share the most names and numbers with the claims and their captions.

    Minutes pages come before packet pages at the same score: they say what the body did.
    """
    wanted: set[str] = set()
    for claim in recap.claims:
        wanted |= _tokens(claim.text)
        if windows and windows.get(claim.id):
            wanted |= _tokens(windows[claim.id])
    scored = []
    for order, page in enumerate(pages.values()):
        score = len(_tokens(page.text) & wanted)
        if score:
            scored.append((-score, page.kind != "minutes", order, page))
    return [page for *_, page in sorted(scored, key=lambda row: row[:3])[:limit]]


#: A redraft from the desk: the editor's note and the draft it is about.
EDITOR_PROMPT = (
    "The editor read the draft below and asks for this change: {note}\n"
    "Make that change. Every rule above still holds: use only the claims (and pages) given "
    "here, and output the whole story again in the same format.\n"
    "The draft:\n{draft}"
)


def build_prompt(
    recap: Recap,
    feedback: str = "",
    windows: dict[str, str] | None = None,
    pages: list[DocPage] | None = None,
    editor: tuple[str, str] | None = None,
    roundup: bool = False,
    speakers: dict[str, str] | None = None,
    people: People | None = None,
) -> str:
    # No topics: the recap model writes them and nothing verifies them, so a name taken from a
    # topic would be a name the evidence does not back. The captions said around each claim are
    # verbatim meeting words, so they are shown and the check accepts them.
    claims: list[dict[str, str]] = []
    for c in recap.claims:
        entry = {"id": c.id, "text": c.text}
        if windows and windows.get(c.id):
            entry["captions"] = windows[c.id]
        if speakers and speakers.get(c.id):
            entry["speaker"] = speakers[c.id]
        claims.append(entry)
    order = news_order(recap)
    parts = [
        ROUNDUP_PROMPT if roundup else PROMPT,
        *([DOCUMENTS_PROMPT] if pages else []),
        (
            f"Week (from the recap header): the {recap.body} of {recap.city}, meetings of "
            f"{recap.meeting_date}."
            if roundup
            else f"Meeting (from the recap header): {recap.body} of {recap.city}, "
            f"{recap.meeting_date}."
        ),
        "News value, strongest first (the lead should rest on the first of these that the story "
        "cites): " + ", ".join(order) + ".",
    ]
    if people is not None:
        parts.append(
            "PERSON NAMES (the records' spellings): when a claim or its captions name a person, "
            "print the record's spelling from this list, never the captions' spelling. A person "
            "not on this list is not named: use the role instead (a council member, town staff, "
            "a resident).\n"
            + "\n".join(
                f"- {person.name} — {person.role} ({person.source})"
                for person in people.persons[:60]
            )
        )
    if editor is not None:
        parts.append(EDITOR_PROMPT.format(note=editor[0].strip(), draft=editor[1].strip()))
    if feedback.strip():
        parts.append(
            "A previous draft failed the no-new-claims check. Fix every problem below and output "
            "the whole story again in the same format:\n" + feedback.strip()
        )
    parts.append(
        "Claims (the only facts you may use):\n" + json.dumps(claims, indent=1, ensure_ascii=False)
    )
    if pages:
        shown = [
            {"id": p.id, "document": p.kind, "page": p.page, "text": p.text[:PAGE_CHARS]}
            for p in pages
        ]
        parts.append("Document pages:\n" + json.dumps(shown, indent=1, ensure_ascii=False))
    return "\n\n".join(parts) + "\n"


def parse_article(answer: str) -> Article:
    start, end = answer.find("{"), answer.rfind("}")
    if start == -1 or end <= start:
        raise ArticleFormatError("no JSON object in the model answer")
    try:
        data = json.loads(answer[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ArticleFormatError(f"model answer is not valid JSON: {exc}") from exc
    return article_from_dict(data)


def _ids(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ArticleFormatError(f"{where}: claim_ids must be a list")
    return tuple(str(i) for i in value)


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def article_from_dict(data: Any) -> Article:
    if not isinstance(data, dict):
        raise ArticleFormatError("article JSON must be an object")
    headline = _one_line(data.get("headline"))
    raw_sections = data.get("sections")
    if not headline or not isinstance(raw_sections, list) or not raw_sections:
        raise ArticleFormatError("the story needs a headline and a list of sections")
    sections = []
    for s_no, raw in enumerate(raw_sections, 1):
        if not isinstance(raw, dict) or not isinstance(raw.get("paragraphs"), list):
            raise ArticleFormatError(f"section {s_no} needs a list of paragraphs")
        paragraphs = []
        for item in raw["paragraphs"]:
            if not isinstance(item, dict) or not str(item.get("text", "")).strip():
                raise ArticleFormatError(f"section {s_no}: bad paragraph {json.dumps(item)[:120]}")
            # The model writes "claim_ids"; the desk stores the same ids as "cites".
            ids = _ids(item.get("claim_ids", item.get("cites", [])), f"section {s_no}")
            page_ids = _ids(item.get("pages", []), f"section {s_no} pages")
            paragraphs.append(
                Paragraph(text=str(item["text"]).strip(), claim_ids=ids, page_ids=page_ids)
            )
        heading = _one_line(raw.get("heading")) or None
        sections.append(Section(heading=heading, paragraphs=tuple(paragraphs)))
    votes = []
    for item in data.get("votes") or []:
        if not isinstance(item, dict):
            raise ArticleFormatError("each vote must be an object")
        tally = _one_line(item.get("tally")) or None
        votes.append(
            Vote(
                motion=_one_line(item.get("motion")),
                result=_one_line(item.get("result")),
                tally=tally,
                claim_id=str(item.get("claim_id", "")),
            )
        )
    quotes = []
    for item in data.get("quotes") or []:
        if not isinstance(item, dict):
            raise ArticleFormatError("each quote must be an object")
        quotes.append(
            Quote(
                claim_id=str(item.get("claim_id", "")),
                speaker=_one_line(item.get("speaker")) or "A speaker",
            )
        )
    return Article(
        headline=headline,
        dek=_one_line(data.get("dek")),
        sections=tuple(sections),
        votes=tuple(votes),
        quotes=tuple(quotes),
    )


def _paragraph_dict(paragraph: Paragraph) -> dict[str, Any]:
    out: dict[str, Any] = {"text": paragraph.text, "cites": list(paragraph.claim_ids)}
    if paragraph.page_ids:
        out["pages"] = list(paragraph.page_ids)
    return out


def article_to_dict(article: Article) -> dict[str, Any]:
    """The story in the desk's format: each paragraph's claim ids are its "cites"."""
    return {
        "headline": article.headline,
        "dek": article.dek,
        "sections": [
            {
                "heading": s.heading,
                "paragraphs": [_paragraph_dict(p) for p in s.paragraphs],
            }
            for s in article.sections
        ],
        "votes": [
            {"motion": v.motion, "result": v.result, "tally": v.tally, "claim_id": v.claim_id}
            for v in article.votes
        ],
        "quotes": [{"claim_id": q.claim_id, "speaker": q.speaker} for q in article.quotes],
    }


def write_article(
    recap: Recap,
    complete: Callable[[str], str],
    *,
    feedback: str = "",
    windows: dict[str, str] | None = None,
    pages: dict[str, DocPage] | None = None,
    editor: tuple[str, str] | None = None,
    roundup: bool = False,
    speakers: dict[str, str] | None = None,
    people: People | None = None,
) -> Article:
    """``editor`` is (the editor's note, the draft it is about), for a redraft from the desk."""
    shown = pick_pages(recap, pages, windows) if pages else None
    return parse_article(
        complete(
            build_prompt(
                recap,
                feedback,
                windows,
                shown,
                editor,
                roundup=roundup,
                speakers=speakers,
                people=people,
            )
        )
    )


@dataclass(frozen=True)
class Issue:
    where: str  # "headline", "dek", "section 2 heading", "paragraph N", "vote N", "quote N"
    code: str
    message: str


@dataclass
class ArticleReport:
    article: Article
    issues: list[Issue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.issues

    def render(self) -> str:
        words = sum(len(p.text.split()) for p in self.article.paragraphs)
        out = [
            f"CHECK RESULT: {'PASS' if self.passed else 'FAIL'}",
            (
                f"Article: {len(self.article.sections)} sections, "
                f"{len(self.article.paragraphs)} paragraphs, {words} words, "
                f"{len(self.article.cited)} claims cited, {len(self.article.votes)} votes, "
                f"{len(self.article.quotes)} quotes"
            ),
        ]
        out += [f"  {i.where} {i.code}: {i.message}" for i in self.issues] or ["No issues."]
        return "\n".join(out) + "\n"


def _check_text(
    where: str,
    text: str,
    claim_ids: tuple[str, ...],
    recap: Recap,
    issues: list[Issue],
    windows: dict[str, str] | None = None,
    page_ids: tuple[str, ...] = (),
    pages: dict[str, DocPage] | None = None,
    speakers: dict[str, str] | None = None,
    people: People | None = None,
) -> None:
    known = {c.id: c for c in recap.claims}
    for claim_id in claim_ids:
        if claim_id not in known:
            issues.append(Issue(where, "unknown_claim", f"cites unknown claim {claim_id}"))
    for page_id in page_ids:
        if not pages or page_id not in pages:
            issues.append(Issue(where, "unknown_page", f"cites unknown document page {page_id}"))
    cited = [known[c].text for c in claim_ids if c in known]
    if not cited:
        issues.append(Issue(where, "uncited", "states facts but cites no claim"))
        return
    said = [windows[c] for c in claim_ids if windows and windows.get(c)]
    read = [pages[p].text for p in page_ids if pages and p in pages]
    said_by = [speakers[c] for c in claim_ids if speakers and speakers.get(c)]
    sources = [*cited, *said, *said_by, *read, header_text(recap)]
    allowed_numbers: set[str] = set()
    for source in sources:
        allowed_numbers |= number_atoms(source)
    for missing in sorted(number_atoms(_ONE_SPEAKER_RE.sub("", text)) - allowed_numbers):
        issues.append(Issue(where, "number", f"number {missing} is not in the cited claims"))
    allowed_words = _claim_words(sources) | HOUSE_WORDS
    for name in _names(text):
        if name.lower() not in allowed_words:
            issues.append(Issue(where, "name", f"name {name!r} is not in the cited claims"))
    for code, message in name_issues(text, people):
        issues.append(Issue(where, code, message))


def _vote_text(vote: Vote) -> str:
    return " ".join(part for part in (vote.motion, vote.result, vote.tally or "") if part)


def _check_vote(
    where: str,
    vote: Vote,
    recap: Recap,
    issues: list[Issue],
    windows,
    speakers: dict[str, str] | None = None,
    people: People | None = None,
) -> None:
    if not vote.motion or not vote.result:
        issues.append(Issue(where, "format", "a vote needs a motion and a result"))
    if _vote_has_no_subject(vote):
        issues.append(
            Issue(
                where,
                "no_subject",
                "a vote must say what was voted on; drop the line when the record gives no subject",
            )
        )
    _check_text(
        where,
        _vote_text(vote),
        (vote.claim_id,),
        recap,
        issues,
        windows,
        speakers=speakers,
        people=people,
    )


#: Words a vote line may use without naming what was voted on.
_RESULT_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "motion",
        "received",
        "second",
        "and",
        "passed",
        "passes",
        "carried",
        "failed",
        "unanimously",
        "unanimous",
        "was",
        "approved",
    }
)


def _vote_has_no_subject(vote: Vote) -> bool:
    words = re.findall(r"[a-z]+", vote.motion.lower())
    if not words:
        return True
    return all(word in _RESULT_WORDS for word in words)


def _check_quote(
    where: str,
    quote: Quote,
    recap: Recap,
    issues: list[Issue],
    windows,
    speakers: dict[str, str] | None = None,
    people: People | None = None,
) -> None:
    claim = recap.claim(quote.claim_id)
    if claim is None:
        issues.append(Issue(where, "unknown_claim", f"cites unknown claim {quote.claim_id}"))
        return
    if not claim.quote.strip():
        issues.append(Issue(where, "no_quote", f"claim {claim.id} has no quote"))
    _check_text(
        where,
        quote.speaker,
        (quote.claim_id,),
        recap,
        issues,
        windows,
        speakers=speakers,
        people=people,
    )


def check_article(
    recap: Recap,
    article: Article,
    windows: dict[str, str] | None = None,
    pages: dict[str, DocPage] | None = None,
    speakers: dict[str, str] | None = None,
    people: People | None = None,
) -> ArticleReport:
    report = ArticleReport(article=article)
    issues = report.issues
    number = 0
    for s_no, section in enumerate(article.sections, 1):
        section_ids: dict[str, None] = {}
        section_pages: dict[str, None] = {}
        for paragraph in section.paragraphs:
            number += 1
            _check_text(
                f"paragraph {number}",
                paragraph.text,
                paragraph.claim_ids,
                recap,
                issues,
                windows,
                paragraph.page_ids,
                pages,
                speakers,
                people,
            )
            section_ids.update(dict.fromkeys(paragraph.claim_ids))
            section_pages.update(dict.fromkeys(paragraph.page_ids))
        if section.heading:
            _check_text(
                f"section {s_no} heading",
                section.heading,
                tuple(section_ids),
                recap,
                issues,
                windows,
                tuple(section_pages),
                pages,
                speakers,
                people,
            )
    cited, cited_pages = article.cited, article.cited_pages
    _check_text(
        "headline",
        article.headline,
        cited,
        recap,
        issues,
        windows,
        cited_pages,
        pages,
        speakers,
        people,
    )
    if article.dek:
        _check_text(
            "dek", article.dek, cited, recap, issues, windows, cited_pages, pages, speakers, people
        )
    for v_no, vote in enumerate(article.votes, 1):
        _check_vote(f"vote {v_no}", vote, recap, issues, windows, speakers, people)
    for q_no, quote in enumerate(article.quotes, 1):
        _check_quote(f"quote {q_no}", quote, recap, issues, windows, speakers, people)
    return report


@dataclass
class Dropped:
    paragraphs: list[Paragraph] = field(default_factory=list)
    votes: list[Vote] = field(default_factory=list)
    quotes: list[Quote] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)  # blanked headings and dek

    def lines(self) -> list[str]:
        out = [f"dropped paragraph: {p.text[:100]}" for p in self.paragraphs]
        out += [f"dropped vote: {_vote_text(v)[:100]}" for v in self.votes]
        out += [f"dropped quote: claim {q.claim_id}" for q in self.quotes]
        out += [f"blanked: {t[:100]}" for t in self.texts]
        return out


#: Ways a claim says who spoke, which a headline does not need.
_SPOKEN_PREFIX_RE = re.compile(
    r"^(?:An?\s+(?:unidentified\s+)?speaker\s+said\s+|A\s+council\s+member\s+said\s+|"
    r"The\s+council\s+heard\s+that\s+)",
    re.IGNORECASE,
)


def _headline_from_lead(article: Article, recap: Recap) -> str:
    """A headline built from the lead paragraph's first claim, 14 words or fewer."""
    lead = article.paragraphs[0]
    claim = next(
        (recap.claim(claim_id) for claim_id in lead.claim_ids if recap.claim(claim_id)), None
    )
    if claim is None:
        return ""
    text = _SPOKEN_PREFIX_RE.sub("", claim.text).strip()
    if not text:
        return ""
    words = text.split()
    if len(words) > 14:
        text = " ".join(words[:14]).rstrip(" ,.;:")
    return text[:1].upper() + text[1:]


def drop_unsupported(
    recap: Recap,
    article: Article,
    windows: dict[str, str] | None = None,
    pages: dict[str, DocPage] | None = None,
    speakers: dict[str, str] | None = None,
    people: People | None = None,
) -> tuple[Article, Dropped]:
    """Remove the parts that fail the check. The headline is kept as it is."""
    dropped = Dropped()

    def fails(where: str, text: str, ids: tuple[str, ...], page_ids: tuple[str, ...]) -> bool:
        issues: list[Issue] = []
        _check_text(where, text, ids, recap, issues, windows, page_ids, pages, speakers, people)
        return bool(issues)

    sections = []
    for section in article.sections:
        kept = []
        for paragraph in section.paragraphs:
            if fails("paragraph", paragraph.text, paragraph.claim_ids, paragraph.page_ids):
                dropped.paragraphs.append(paragraph)
            else:
                kept.append(paragraph)
        if not kept:
            continue
        heading = section.heading
        ids = tuple(dict.fromkeys(i for p in kept for i in p.claim_ids))
        page_ids = tuple(dict.fromkeys(i for p in kept for i in p.page_ids))
        if heading and fails("heading", heading, ids, page_ids):
            dropped.texts.append(heading)
            heading = None
        sections.append(Section(heading=heading, paragraphs=tuple(kept)))
    out = replace(article, sections=tuple(sections))
    if out.dek and fails("dek", out.dek, out.cited, out.cited_pages):
        dropped.texts.append(out.dek)
        out = replace(out, dek="")
    if out.paragraphs and fails("headline", out.headline, out.cited, out.cited_pages):
        # The story may have dropped the paragraph that carried the headline's fact. State the
        # lead paragraph's own claim instead, so the headline cannot outrun the kept story.
        fallback = _headline_from_lead(out, recap)
        if (
            fallback
            and fallback != out.headline
            and not fails("headline", fallback, out.cited, out.cited_pages)
        ):
            dropped.texts.append(out.headline)
            out = replace(out, headline=fallback)
    votes, quotes = [], []
    for vote in out.votes:
        issues: list[Issue] = []
        _check_vote("vote", vote, recap, issues, windows, speakers, people)
        (dropped.votes if issues else votes).append(vote)
    for quote in out.quotes:
        issues = []
        _check_quote("quote", quote, recap, issues, windows, speakers, people)
        (dropped.quotes if issues else quotes).append(quote)
    return replace(out, votes=tuple(votes), quotes=tuple(quotes)), dropped


#: A heading about an announcement, event or reminder gets no section of its own.
_ANNOUNCEMENT_RE = re.compile(
    r"\b(announcement|reminder|cemetery|tombstone|tour|holiday|celebration|recognition|"
    r"volunteer|event|ballot|deadline)\b",
    re.IGNORECASE,
)
_ALSO_HEADINGS = frozenset({"also on the agenda", "also this week"})


def merge_announcements(article: Article, roundup: bool = False) -> tuple[Article, list[str]]:
    """Move announcement sections into the one "Also..." section that ends the story.

    Tours, event dates, holiday programs and reminders are not a section of their own. The
    paragraphs keep their claims and order; the writer only loses the headings.
    """
    kept: list[Section] = []
    moved: list[Paragraph] = []
    also_at: int | None = None
    for section in article.sections:
        heading = (section.heading or "").strip().lower()
        if section.heading and heading not in _ALSO_HEADINGS and _ANNOUNCEMENT_RE.search(heading):
            moved.extend(section.paragraphs)
            continue
        if heading in _ALSO_HEADINGS:
            also_at = len(kept)
        kept.append(section)
    if not moved:
        return article, []
    changes = [f"moved {len(moved)} announcement paragraph(s) into the Also section"]
    if also_at is None:
        kept.append(Section("Also this week" if roundup else "Also on the agenda", tuple(moved)))
    else:
        target = kept[also_at]
        kept[also_at] = replace(target, paragraphs=target.paragraphs + tuple(moved))
    return replace(article, sections=tuple(kept)), changes


#: An automatic-caption phrase that sends readers to a publication that does not exist ("read
#: the Superior sample for accurate dates"). It is speech-to-text noise, not a fact.
_GARBLED_SENTENCE_RE = re.compile(
    r"\b(?:read|reading|see|check|consult)\b[^.]*\bsample\b[^.]*\b(?:accurate|correct)\b"
    # A name the record does not hold was replaced with a role ("a speaker") in a place where
    # no person was meant: "the a speaker project", "training with a speaker staff".
    r"|\b(?:a|an|the)\s+(?:a|an)\s+speaker\b"
    r"|\ba speaker\s+(?:staff|project|business|company|place|program|series|office|department)\b",
    re.IGNORECASE,
)
#: The events that make a sentence an announcement instead of news. Each can appear once, in one
#: sentence, in the "Also..." section; a roundup's "Also this week" holds only real items.
_ANNOUNCEMENT_GROUPS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(cemetery|tombstone|tour)\b", re.IGNORECASE),
    re.compile(r"\b(celebration|festival|resilient)\b", re.IGNORECASE),
    re.compile(r"\b(holiday|passport|sign up|sign-up)\b", re.IGNORECASE),
    re.compile(r"\b(ballot|postmark|election)\b", re.IGNORECASE),
    re.compile(r"\b(volunteer|recognition|proclamation)\b", re.IGNORECASE),
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _announcement_group(sentence: str) -> int | None:
    for number, pattern in enumerate(_ANNOUNCEMENT_GROUPS):
        if pattern.search(sentence):
            return number
    return None


def condense_announcements(article: Article, roundup: bool = False) -> tuple[Article, list[str]]:
    """Keep announcements to one sentence each in the "Also..." section, and no filler in a roundup.

    A garbled caption phrase is dropped wherever it appears. A duplicate announcement is dropped.
    The roundup's "Also this week" keeps real agenda items, not tours, celebrations or reminders.
    """
    changes: list[str] = []
    seen: set[int] = set()
    sections: list[Section] = []
    for section in article.sections:
        also = (section.heading or "").strip().lower() in _ALSO_HEADINGS
        paragraphs: list[Paragraph] = []
        for paragraph in section.paragraphs:
            kept: list[str] = []
            for sentence in _SENTENCE_RE.split(paragraph.text.strip()):
                if not sentence:
                    continue
                if _GARBLED_SENTENCE_RE.search(sentence):
                    changes.append(f"dropped a garbled caption phrase: {sentence[:80]}")
                    continue
                group = _announcement_group(sentence) if also else None
                if group is not None:
                    if roundup:
                        changes.append(f"dropped an announcement from the roundup: {sentence[:80]}")
                        continue
                    if group in seen:
                        changes.append(f"dropped a repeated announcement: {sentence[:80]}")
                        continue
                    seen.add(group)
                kept.append(sentence)
            if kept:
                paragraphs.append(replace(paragraph, text=" ".join(kept)))
        if paragraphs:
            sections.append(replace(section, paragraphs=tuple(paragraphs)))
    if not changes:
        return article, []
    return replace(article, sections=tuple(sections)), changes


def story_words(article: Article) -> int:
    return sum(len(p.text.split()) for p in article.paragraphs)


#: A story under this many words is thin when the recap holds enough facts to fill it.
MIN_STORY_WORDS = 600
#: The claim count that makes the length target fair: fewer facts cannot support 600 words.
MIN_CLAIMS_FOR_LENGTH = 15


def story_too_short(recap: Recap, article: Article) -> bool:
    """Whether a passing story is too thin for the facts the recap holds."""
    return len(recap.claims) >= MIN_CLAIMS_FOR_LENGTH and story_words(article) < MIN_STORY_WORDS


def video_link(recap: Recap, timestamp: str) -> str:
    """The moment in the meeting video; a weekly roundup has no one video, so no link.

    A meeting recorded outside YouTube (CivicClerk, eScribe, Vimeo) has an id that is not a
    YouTube id. Its ``media_url`` is the real media, and the browser plays it at ``#t=``
    seconds. Without one there is no working time link, so no link is made: a
    ``youtube.com/watch?v=civicclerk-...`` address is always dead.
    """
    if recap.video_id == "roundup":
        return ""
    seconds = seconds_of(timestamp)
    if recap.media_url:
        return f"{recap.media_url}#t={seconds}"
    if re.match(r"^civicclerk-|^[a-z]+-[a-z]+-\d+$", recap.video_id):
        return ""
    return f"https://www.youtube.com/watch?v={recap.video_id}&t={seconds}s"


def citations(recap: Recap, paragraph: Paragraph) -> list[dict[str, str]]:
    """The video moments a paragraph rests on, in its claim order."""
    out = []
    for claim_id in paragraph.claim_ids:
        claim = recap.claim(claim_id)
        if claim is not None:
            out.append(
                {
                    "claim_id": claim.id,
                    "timestamp": claim.timestamp,
                    "url": video_link(recap, claim.timestamp),
                    "quote": claim.quote,
                }
            )
    return out


def page_citations(paragraph: Paragraph, pages: dict[str, DocPage] | None) -> list[dict[str, Any]]:
    """The document pages a paragraph rests on, each with its ``#page=N`` link."""
    out = []
    for page_id in paragraph.page_ids:
        page = (pages or {}).get(page_id)
        if page is None:
            raise ArticleFormatError(f"the story cites page {page_id}, which the documents lack")
        out.append({"page_id": page.id, "kind": page.kind, "page": page.page, "url": page.link})
    return out


def note_for(article: Article) -> str:
    """The fixed note: it names the documents when the story cites them."""
    return NOTE_DOCUMENTS if article.cited_pages else NOTE


def quote_words(recap: Recap, quote: Quote, people: People | None = None) -> str:
    """The claim's verbatim words, with a mangled name set to the record's spelling.

    A quote is the record's own words, but a name in it came through the same speech-to-text as
    the rest. It is repaired like every other printed name; the words are otherwise unchanged.
    """
    claim = recap.claim(quote.claim_id)
    if claim is None:
        return ""
    text = claim.quote
    # Only a name that matches the record is changed. A place the record does not print here
    # ("Rock Creek village") stays as the speaker said it; a role in place of it would rewrite
    # the quote into something nobody said.
    for wrong, right in fixes(text, people):
        text = text.replace(wrong, right)
    return text


def site_article(
    recap: Recap,
    article: Article,
    pages: dict[str, DocPage] | None = None,
    people: People | None = None,
) -> dict[str, Any]:
    """The story in the site's episode format: cites are claim positions and start times.

    A paragraph that cites document pages lists them under "pages", each with its document's
    URL and page number. Raises ArticleFormatError when a part cites a claim the recap lacks
    or a page the documents lack, so a story edited on the desk cannot point at nothing.
    """
    position = {c.id: n for n, c in enumerate(recap.claims)}

    def claim_of(claim_id: str) -> Any:
        claim = recap.claim(claim_id)
        if claim is None:
            raise ArticleFormatError(f"the story cites {claim_id}, which the recap lacks")
        return claim

    def start(claim_id: str) -> int:
        return seconds_of(claim_of(claim_id).timestamp)

    def paragraph(p: Paragraph) -> dict[str, Any]:
        out: dict[str, Any] = {
            "text": p.text,
            "cites": [
                {"claim": position[claim_of(c).id], "start_s": start(c)} for c in p.claim_ids
            ],
        }
        if p.page_ids:
            out["pages"] = [
                {"kind": c["kind"], "url": c["url"].split("#", 1)[0], "page": c["page"]}
                for c in page_citations(p, pages)
            ]
        return out

    return {
        "headline": article.headline,
        "dek": article.dek,
        "sections": [
            {
                "heading": s.heading,
                "paragraphs": [paragraph(p) for p in s.paragraphs],
            }
            for s in article.sections
        ],
        "votes": [
            {
                "motion": v.motion,
                "result": v.result,
                "tally": v.tally,
                "start_s": start(v.claim_id),
            }
            for v in article.votes
        ],
        "quotes": [
            {
                "text": quote_words(recap, q, people),
                "speaker": q.speaker,
                "start_s": start(q.claim_id),
            }
            for q in article.quotes
        ],
        "topics": [],
    }


def published_dict(
    recap: Recap, article: Article, pages: dict[str, DocPage] | None = None
) -> dict[str, Any]:
    """The story as the desk stores it: the model's parts plus each paragraph's citations."""
    data = article_to_dict(article)
    for section, raw in zip(article.sections, data["sections"], strict=True):
        for paragraph, raw_p in zip(section.paragraphs, raw["paragraphs"], strict=True):
            raw_p["citations"] = citations(recap, paragraph)
            if paragraph.page_ids:
                raw_p["documents"] = page_citations(paragraph, pages)
    return {
        "city": recap.city,
        "body": recap.body,
        "meeting_date": recap.meeting_date,
        "video_id": recap.video_id,
        "note": note_for(article),
        **data,
    }


def render_markdown(
    recap: Recap,
    article: Article,
    pages: dict[str, DocPage] | None = None,
    people: People | None = None,
) -> str:
    out = [f"# {article.headline}", ""]
    if article.dek:
        out += [f"**{article.dek}**", ""]
    out += [f"*{recap.body} of {recap.city}, {recap.meeting_date}*", ""]
    for section in article.sections:
        if section.heading:
            out += [f"## {section.heading}", ""]
        for paragraph in section.paragraphs:
            links = ", ".join(
                f"[{c['timestamp']}]({c['url']})" if c["url"] else c["timestamp"]
                for c in citations(recap, paragraph)
            )
            sources = f"Video: {links}" if links else "The meeting video"
            if paragraph.page_ids:
                docs = ", ".join(
                    f"[{c['kind']} p. {c['page']}]({c['url']})"
                    for c in page_citations(paragraph, pages)
                )
                sources += f"; Documents: {docs}"
            out += [f"{paragraph.text} ({sources})", ""]
    if article.votes:
        out += ["## Votes", ""]
        for vote in article.votes:
            claim = recap.claim(vote.claim_id)
            link = ""
            if claim is not None:
                url = video_link(recap, claim.timestamp)
                link = f" ([{claim.timestamp}]({url}))" if url else f" ({claim.timestamp})"
            tally = f" {vote.tally}" if vote.tally else ""
            motion = vote.motion.rstrip(" .:;,")
            result = vote.result.rstrip(" .:;,")
            out.append(f"- {motion}: {result}{tally}{link}")
        out.append("")
    for quote in article.quotes:
        claim = recap.claim(quote.claim_id)
        if claim is not None:
            out += [
                f'> "{quote_words(recap, quote, people)}" ({quote.speaker}, {claim.timestamp})',
                "",
            ]
    out.append(f"*{note_for(article)}*")
    return "\n".join(out) + "\n"


def save_article(
    recap: Recap,
    article: Article,
    out: Path,
    pages: dict[str, DocPage] | None = None,
    people: People | None = None,
) -> tuple[Path, Path]:
    """Write ``article.json`` (with citations) and ``article.md`` next to it."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    json_path = out.with_suffix(".json")
    md_path = out.with_suffix(".md")
    json_path.write_text(
        json.dumps(published_dict(recap, article, pages), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(recap, article, pages, people), encoding="utf-8")
    return json_path, md_path


def load_article(path: Path) -> Article:
    return article_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
