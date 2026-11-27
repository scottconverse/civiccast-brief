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

from civiccast_brief.article.names import (
    People,
    fixes,
    name_issues,
    person_issues,
    seat_fixes,
    spans,
)
from civiccast_brief.article.terms import Record, split_sentences, term_issues
from civiccast_brief.script.checker import (
    _POSSESSIVE_RE,
    _claim_words,
    _proper_nouns,
    header_text,
)
from civiccast_brief.script.numbers import number_atoms
from civiccast_brief.script.recap import Recap, named_bodies, named_meeting, seconds_of

#: Fixed words the story prints itself. The model never writes these.
NOTE = "Each paragraph links to the moment in the video it comes from."
#: The note for a story that also cites the meeting documents.
NOTE_DOCUMENTS = (
    "Each paragraph links to the moment in the video and the document pages it comes from."
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
        "asked",
        "answering",
        "responding",
        "speaking",
        "according",
        "citing",
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
_SENTENCE_START_RE = re.compile(r"(^|[.!?:][\"'”’)]?\s+)([A-Z][a-z]+)")
#: Caption window around a claim's time, in seconds: a little before the quote, more after it.
WINDOW_BEFORE = 20
WINDOW_AFTER = 60


#: A month written short is not a person: "met Oct. 26" is a date.
_SHORT_MONTHS = frozenset(
    {"jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
)


#: A street name the story spells out where the record abbreviates it ("High Plains Dr.").
_STREET_WORDS = {
    "drive": "dr",
    "street": "st",
    "avenue": "ave",
    "boulevard": "blvd",
    "road": "rd",
    "lane": "ln",
    "court": "ct",
    "parkway": "pkwy",
    "place": "pl",
    "circle": "cir",
}


def _rounded_from(missing: str, allowed: set[str]) -> bool:
    """Whether a round figure ("$41.3 million") is a cited amount ($41,321,246) rounded.

    Only amounts of a million or more, rounded to at least the ten-thousands: a story may
    round a budget, never a vote count or a small dollar figure.
    """
    if not missing.isdigit():
        return False
    value = int(missing)
    zeros = len(missing) - len(missing.rstrip("0"))
    if value < 1_000_000 or zeros < 4:
        return False
    step = 10**zeros
    return any(
        number.isdigit() and (int(number) + step // 2) // step * step == value for number in allowed
    )


def _names(text: str) -> list[str]:
    """Capitalised names in a text, after lowering a common word that starts a sentence."""

    def lower(match: re.Match[str]) -> str:
        word = match.group(2)
        return match.group(1) + (word.lower() if word.lower() in ARTICLE_OPENERS else word)

    return [
        name
        for name in _proper_nouns(_SENTENCE_START_RE.sub(lower, text))
        if name.lower() not in _SHORT_MONTHS
    ]


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
7. Announcements, event dates, tours, recognitions and reminders are not news: put the meeting's announcements in ONE sentence in one short paragraph under "Also on the agenda", never more. Never give them a section or a heading of their own. Never repeat an announcement, and never pass on words the captions garbled.
8. Space, most important first: the news (a resignation, a money, land or law vote) > other votes and amounts > staff presentations with numbers > the substance of public comment > board or committee applicant interviews > announcements. A board or committee applicant interview gets at most one short paragraph saying who applied for which board and what the council did; do not report the applicant's opinions on other subjects (e-bikes, DEI, trails).

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

WHO DID WHAT: never write "the same speaker", "that speaker" or "they also" to join two claims unless the claims carry the same "speaker" name; otherwise say who spoke for each claim. A recusal or conflict names a person or a seat ("the mayor", "Council Member Foster") only when the meeting documents say who recused; otherwise write "a council member recused" without a name. Do not give an official a gendered pronoun ("he", "she", "himself", "herself") unless the claim or its captions give it. Print a resolution or ordinance number and an item or company name exactly as the meeting documents print them; if the captions garble it, describe the item generically instead of copying the garble.
"""

ROUNDUP_PROMPT = """You write the week's news story for a local news site, covering several public meetings of one town.

FACTS: use only the claims listed at the end. Every fact, number, name, vote and date must come from a claim, and every claim's topic says which meeting it came from ("September 28, 2026: ..."). Add no causes, reasons, background or outside context. Do not invent who a speaker is: name a council or board member only when the claim or its captions name that person; otherwise use the role the record supports (someone resigning a council seat is "a council member", a presenter is "town staff", a public commenter is "a resident") or "a speaker". Never write "an unidentified speaker".

NEWS JUDGMENT: this is one story about the week, not a list of meetings. Each claim's "meeting" field says which meeting it came from. Lead with the biggest news across all the meetings: a resignation, removal or vacancy; a vote on money, land or a law; a divided or contested vote; a decision that changes what residents must do; public outcry. Then the next most important news, then the routine items at the end in a short section headed "Also this week". Name the meeting (its date and body) each group of facts comes from, so a reader knows which meeting a fact belongs to. Do not pad and do not repeat a fact. Use every claim that adds news. Announcements, event dates, tours, recognitions and reminders are not news: the roundup's "Also this week" holds the week's real agenda items, not announcements. Put a real item once, and never pass on words the captions garbled. Do not start sentence after sentence with "A speaker said": use the role the captions show (a resident, a business owner, town staff, a council member), fold the statement into its item, and use "a speaker" at most once in a section. A board or committee applicant interview gets at most one short paragraph (who applied for which board, and the outcome), never its opinions on other subjects.

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

WHO DID WHAT: never write "the same speaker", "that speaker" or "they also" to join two claims unless the claims carry the same "speaker" name. A recusal or conflict names a person or a seat only when the meeting documents say who recused; otherwise write "a council member recused". Do not give an official a gendered pronoun unless the claim or its captions give it. Print a resolution or ordinance number and an item or company name exactly as the meeting documents print them; describe the item generically when the captions garble the name.
"""

#: The act that makes a money, land or law claim the news. A claim that merely mentions a
#: contract ("The Superior contract started in 2021") is background, not a decision.
NEWS_ACTION_RE = re.compile(
    r"\b(approv\w*|adopt\w*|pass\w*|carried|carries|mov(?:e|es|ed|ing)\s+to|vote[sd]?|"
    r"authoriz\w*|deni\w*|reject\w*|resign\w*|announc\w*|second\w*)\b",
    re.IGNORECASE,
)
_MONEY_LAND_LAW_RE = re.compile(
    r"\b(budget|contract|fee|tax|levy|bond|purchase|grant|salary|rate|"
    r"annex\w*|zon(?:e|ing)|rezon\w*|development|subdivision|ordinance|resolution|"
    r"eminent domain|land|property|lease|agreement)\b|\$\s?\d",
    re.IGNORECASE,
)

#: What makes a claim the lead, high to low. A resignation outranks any vote; a vote on money,
#: land or a law outranks public comment; a routine consent item is last. A money, land or law
#: claim scores 70 only when it states a decision, never for a passing mention.
NEWS_WEIGHTS: tuple[tuple[int, re.Pattern[str], bool], ...] = (
    (
        100,
        re.compile(
            r"\b(resign|resignation|resigned|step(?:s|ped)? down|"
            r"removed from|expelled|recall)\b",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        60,
        re.compile(
            r"\b(voted no|divided|split vote|contested|failed|opposed|opposition|outcry|"
            r"protest|petition|packed|stood up|walked out)\b",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        0,
        re.compile(
            r"\b(consent agenda|minutes?|approve the agenda|roll[- ]call|proclamations?|"
            r"appointments?|meeting schedule|adjourns?)\b",
            re.IGNORECASE,
        ),
        False,
    ),
    (70, _MONEY_LAND_LAW_RE, True),
    (
        35,
        re.compile(r"\b(public comment|resident|speaker asked|asked the council)\b", re.IGNORECASE),
        False,
    ),
)


def news_value(claim: Any) -> int:
    """How much of a lead a claim is: a resignation 100, a money/land/law vote 70, a contested
    vote 60, public comment 35, a routine item 0, anything else 10 (the highest match wins)."""
    words = re.findall(r"[a-z]+", str(claim.text or "").lower())
    if all(word in _BARE_RESULT_WORDS for word in words):
        return 10
    text = " ".join(str(part or "") for part in (claim.topic, claim.text, claim.quote))
    for weight, pattern, needs_action in NEWS_WEIGHTS:
        if not pattern.search(text):
            continue
        if needs_action and not NEWS_ACTION_RE.search(text):
            continue
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


def top_news(recap: Recap) -> str | None:
    """The claim id of the meeting's biggest news: a resignation, else a money/land/law vote,
    else a contested vote. None when the recap holds nothing that strong."""
    for claim_id in news_order(recap):
        claim = recap.claim(claim_id)
        if claim is not None and news_value(claim) >= LEAD_MIN_VALUE:
            return claim_id
    return None


#: Words too common in a meeting story to show that a headline names its news.
_HEADLINE_STOP = frozenset(
    {
        "about",
        "after",
        "again",
        "also",
        "among",
        "and",
        "are",
        "been",
        "before",
        "being",
        "both",
        "but",
        "can",
        "city",
        "council",
        "could",
        "during",
        "each",
        "from",
        "have",
        "into",
        "its",
        "just",
        "like",
        "made",
        "make",
        "many",
        "more",
        "most",
        "much",
        "must",
        "meeting",
        "members",
        "member",
        "new",
        "next",
        "only",
        "other",
        "over",
        "said",
        "same",
        "second",
        "since",
        "some",
        "such",
        "superior",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "town",
        "under",
        "until",
        "very",
        "vote",
        "voted",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "will",
        "with",
        "would",
        "unidentified",
        "speaker",
        "speakers",
        "resident",
        "residents",
        "public",
        "board",
    }
)


def _news_terms(text: str) -> set[str]:
    """The words that show what a claim's news is, cut to a rough stem ("resignation" -> "resig")."""
    terms: set[str] = set()
    for word in re.findall(r"[a-z]+", text.lower()):
        if len(word) < 4 or word in _HEADLINE_STOP:
            continue
        terms.add(word[:5])
    return terms


def headline_names_news(headline: str, claim: Any) -> bool:
    """Whether a headline states the claim's news in the claim's own words (roughly stemmed)."""
    return bool(_news_terms(headline) & _news_terms(f"{claim.text} {claim.quote}"))


#: A headline that starts like a claim label ("[October 12, 2026 meeting] ...") is not a headline.
_CONTEXT_PREFIX_RE = re.compile(r"^\s*\[[^\]]*\]")
#: Words a headline cannot end on: a cut at one of these stops mid-phrase.
_CUT_ENDINGS = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "to",
        "from",
        "and",
        "or",
        "for",
        "with",
        "at",
        "in",
        "on",
        "by",
        "as",
        "its",
        "that",
        "this",
        "these",
        "those",
        "is",
        "was",
        "were",
        "be",
        "been",
        "about",
        "over",
        "under",
        "into",
        "onto",
        "than",
    }
)


def headline_form_issues(recap: Recap, headline: str) -> list[Issue]:
    """Problems that make a headline a copied claim, a cut-off claim, or a meeting label."""
    out: list[Issue] = []
    text = headline.strip()
    if not text:
        return [Issue("headline", "empty", "a story needs a headline")]
    if _CONTEXT_PREFIX_RE.match(text):
        out.append(
            Issue(
                "headline",
                "claim_label",
                "a headline never starts with a meeting label; write the news as a newspaper would",
            )
        )
    words = re.findall(r"[A-Za-z0-9$%.,'-]+", text)
    if words and words[-1].lower().strip(",.;:") in _CUT_ENDINGS:
        out.append(Issue("headline", "truncated", "the headline stops on a connecting word"))
    bare = _normal_label(text)
    bare_words = bare.split()
    for claim in recap.claims:
        claim_text = _normal_label(claim.text)
        if not claim_text:
            continue
        if bare == claim_text:
            out.append(
                Issue(
                    "headline",
                    "raw_claim",
                    f"the headline copies claim {claim.id}; write it as a news headline",
                )
            )
            break
        claim_words = claim_text.split()
        if len(bare_words) >= 5 and claim_words[: len(bare_words)] == bare_words:
            out.append(
                Issue(
                    "headline",
                    "raw_claim",
                    f"the headline is the opening of claim {claim.id}, cut short; write the news",
                )
            )
            break
    return out


def headline_issues(recap: Recap, headline: str, roundup: bool = False) -> list[Issue]:
    """Form problems, plus a headline that does not name the meeting's or week's biggest news."""
    out = headline_form_issues(recap, headline)
    news = top_news(recap)
    if news is not None and not headline_names_news(headline.strip(), recap.claim(news)):
        label = "the week's" if roundup else "the meeting's"
        out.append(
            Issue(
                "headline",
                "news_headline",
                f"the headline must name {label} biggest news (claim {news}: "
                f"{recap.claim(news).text[:120]})",
            )
        )
    return out


def _normal_label(text: str) -> str:
    """A claim's text without its "[<date> meeting] " label and its "A speaker said" opener."""
    stripped = re.sub(r"^\s*\[[^\]]*\]\s*", "", text)
    stripped = _SPOKEN_PREFIX_RE.sub("", stripped).rstrip(" .,:;!?")
    return " ".join(re.findall(r"[a-z0-9$%.]+", stripped.lower()))


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
        text = c.text
        entry: dict[str, str] = {"id": c.id, "text": text}
        if roundup:
            # The roundup's claims carry "[October 12, 2026 meeting] " so a reader of the claim
            # list knows which meeting each came from. The model must never copy that label into
            # the story or the headline; the date goes in a field of its own.
            label = re.match(r"^\s*\[([^\]]*?)\s+meeting\]\s*", text)
            if label:
                entry["text"] = text[label.end() :]
                entry["meeting"] = label.group(1)
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
            f"Week (from the recap header): the {named_meeting(recap)}, meetings of "
            f"{recap.meeting_date}."
            if roundup
            else f"Meeting (from the recap header): {named_meeting(recap)}, {recap.meeting_date}."
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


#: Ways a text joins a claim to a speaker the record has not joined it to.
_SAME_SPEAKER_RE = re.compile(
    r"\b(?:the|that)\s+same\s+(?:speaker|commenter|resident|person)\b"
    r"|\b(?:they|he|she)\s+also\b",
    re.IGNORECASE,
)
_SELF_PRONOUN_RE = re.compile(r"\b(himself|herself)\b", re.IGNORECASE)
_SUBJECT_PRONOUN_RE = re.compile(r"\b(he|she|his|her)\b", re.IGNORECASE)
_SEAT_RE = re.compile(r"\b(?:the\s+)?(mayor(?:\s+pro\s+tem)?|chair(?:person)?)\b", re.IGNORECASE)
_NAMED_MEMBER_RE = re.compile(r"\b(?i:council\s*member)\s+([A-Z][\w'’-]+)")
_RECUSAL_RE = re.compile(r"\brecus\w*|\bconflict\b", re.IGNORECASE)
#: Acts a story may tie to a named person or seat only when the meeting's documents say who:
#: a recusal, a resignation, and leaving office by not running again. The captions are
#: unlabelled, so "Thanks, Mike" after a farewell speech does not say who resigned.
PERSONAL_ACTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("recused", _RECUSAL_RE),
    ("resigned", re.compile(r"\bresign\w*|\bstep(?:s|ped|ping)?\s+down\b", re.IGNORECASE)),
    (
        "is not running again",
        re.compile(
            r"\bnot\s+(?:to\s+)?(?:run|running|seek|seeking)\b|\bwon't\s+run\b"
            r"|\bnot\s+(?:to\s+)?stand\s+for\b|\bretir\w*",
            re.IGNORECASE,
        ),
    ),
)


def _check_who(
    where: str,
    text: str,
    claim_ids: tuple[str, ...],
    issues: list[Issue],
    *,
    speakers: dict[str, str] | None,
    sources: list[str],
    record: Record | None,
    people: People | None,
) -> None:
    """Reject a speaker link, a pronoun or a recusal the meeting record does not back.

    "The same speaker also backed ..." joins two claims to one person. It passes only when the
    record names the same speaker for every claim it cites. A gendered pronoun for an official
    is printed only when the transcript or the documents give it. A recusal names the seat or
    the person only when the documents' own minutes or packet name them; the captions' "mayor I
    need to recuse myself" does not, and "Mike's still recused" does not either.
    """
    if _SAME_SPEAKER_RE.search(text):
        named = [speakers.get(claim_id, "") for claim_id in claim_ids] if speakers else []
        named = [name for name in named if name]
        if len(named) != len(claim_ids) or len(set(named)) != 1:
            issues.append(
                Issue(
                    where,
                    "same_speaker",
                    "the text joins claims to one speaker, but the claims are not all the "
                    "record's same named speaker; say who spoke for each claim instead",
                )
            )
    joined = " \n ".join(sources)
    if record is not None:
        joined = f"{joined}\n{record.text}"
    for match in _SELF_PRONOUN_RE.finditer(text):
        if match.group(0).lower() not in joined.lower():
            issues.append(
                Issue(
                    where,
                    "pronoun",
                    f"the record does not give {match.group(0)!r} for this official; "
                    "use the neutral role",
                )
            )
    for sentence in split_sentences(text):
        acts = [(act, pattern) for act, pattern in PERSONAL_ACTS if pattern.search(sentence)]
        if not acts:
            continue
        if (
            _SEAT_RE.search(sentence)
            and _SUBJECT_PRONOUN_RE.search(sentence)
            and not _SUBJECT_PRONOUN_RE.search(joined)
        ):
            issues.append(
                Issue(
                    where,
                    "pronoun",
                    "the record does not give the official's pronoun; use the neutral role",
                )
            )
        labels = [match.group(1) for match in _SEAT_RE.finditer(sentence)]
        labels += [match.group(1) for match in _NAMED_MEMBER_RE.finditer(sentence)]
        if people is not None:
            for span in spans(sentence):
                if people.match(span.name, span.role) is not None:
                    labels.append(span.name)
        unbacked = [
            (label, act)
            for label in labels
            for act, pattern in acts
            if record is None
            or not (record.recusal_backed(label) if act == "recused" else False)
            and not record.act_backed(label, pattern)
        ]
        if unbacked:
            label, act = unbacked[0]
            issues.append(
                Issue(
                    where,
                    "recusal_name" if act == "recused" else "act_name",
                    f"the record does not say {label!r} {act}; "
                    "write 'a council member' without naming the seat or person",
                )
            )


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
    record: Record | None = None,
    spoken: bool = False,
    skill_evidence: bool = False,
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
    quoted = [known[c].quote for c in claim_ids if c in known]
    said = [windows[c] for c in claim_ids if windows and windows.get(c)]
    read = [pages[p].text for p in page_ids if pages and p in pages]
    said_by = [speakers[c] for c in claim_ids if speakers and speakers.get(c)]
    sources = [*cited, *quoted, *said, *said_by, *read, header_text(recap)]
    allowed_numbers: set[str] = set()
    for source in sources:
        allowed_numbers |= number_atoms(source)
    for missing in sorted(number_atoms(_ONE_SPEAKER_RE.sub("", text)) - allowed_numbers):
        if _rounded_from(missing, allowed_numbers):
            continue
        issues.append(Issue(where, "number", f"number {missing} is not in the cited claims"))
    allowed_words = _claim_words(sources) | HOUSE_WORDS
    for name in _names(text):
        if name.lower() in allowed_words:
            continue
        # "Lafayette's" is the city's name with an "'s": the script checker allows it too.
        if _POSSESSIVE_RE.sub("", name.lower()) in allowed_words:
            continue
        if _STREET_WORDS.get(name.lower()) in allowed_words:
            continue
        # A repaired name is the record's spelling of a name the quote held: it is not in the
        # claim's words, and it does not need to be.
        if people is not None and people.match(name) is not None:
            continue
        issues.append(Issue(where, "name", f"name {name!r} is not in the cited claims"))
    person_check = person_issues if skill_evidence else name_issues
    for code, message in person_check(text, people):
        issues.append(Issue(where, code, message))
    if record is not None:
        for code, message in term_issues(text, record):
            issues.append(Issue(where, code, message))
    if not spoken:
        _check_who(
            where,
            text,
            claim_ids,
            issues,
            speakers=speakers,
            sources=[*said, *read, *said_by],
            record=record,
            people=people,
        )


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
    record: Record | None = None,
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
        record=record,
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

#: Words a claim's text may use while naming no subject at all: "The motion carried.",
#: "The item carried unanimously.", "The motion passed 6 to 1, with one member voting no."
_BARE_RESULT_WORDS = _RESULT_WORDS | frozenset(
    {
        "item",
        "it",
        "carries",
        "vote",
        "voted",
        "votes",
        "voting",
        "with",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "member",
        "members",
        "no",
        "yes",
        "to",
        "by",
        "of",
        "in",
        "opposition",
        "opposed",
        "against",
        "ayes",
        "nays",
        "fails",
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
    record: Record | None = None,
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
        record=record,
    )
    # The words the story prints are the claim's quote, with its mangled names repaired. A name
    # in the printed quote must be the record's too: a quote may not print "Craig Craft".
    _check_text(
        f"{where} words",
        quote_words(recap, quote, people),
        (quote.claim_id,),
        recap,
        issues,
        windows,
        speakers=speakers,
        people=people,
        record=record,
        spoken=True,
    )


def check_article(
    recap: Recap,
    article: Article,
    windows: dict[str, str] | None = None,
    pages: dict[str, DocPage] | None = None,
    speakers: dict[str, str] | None = None,
    people: People | None = None,
    record: Record | None = None,
    *,
    skill_evidence: bool = False,
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
                record,
                skill_evidence=skill_evidence,
            )
            section_ids.update(dict.fromkeys(paragraph.claim_ids))
            section_pages.update(dict.fromkeys(paragraph.page_ids))
        if section.heading and not skill_evidence:
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
                record,
                skill_evidence=skill_evidence,
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
        record,
        skill_evidence=skill_evidence,
    )
    if article.dek:
        _check_text(
            "dek",
            article.dek,
            cited,
            recap,
            issues,
            windows,
            cited_pages,
            pages,
            speakers,
            people,
            record,
            skill_evidence=skill_evidence,
        )
    for v_no, vote in enumerate(article.votes, 1):
        _check_vote(f"vote {v_no}", vote, recap, issues, windows, speakers, people, record)
    for q_no, quote in enumerate(article.quotes, 1):
        _check_quote(f"quote {q_no}", quote, recap, issues, windows, speakers, people, record)
    if not skill_evidence:
        # A meeting story written by the report-local-news skill carries its own claims and
        # news judgment; its evidence check is the claim-by-claim block, not this desk's
        # news order.
        issues.extend(headline_issues(recap, article.headline, recap.video_id == "roundup"))
        news = top_news(recap)
        if news is not None and article.paragraphs:
            lead = article.paragraphs[0]
            claim = recap.claim(news)
            # The lead must both cite and state the news: a paragraph that thanks the mayor
            # may list the resignation's claim and still bury it.
            if news not in lead.claim_ids or not headline_names_news(lead.text, claim):
                issues.append(
                    Issue(
                        "lead",
                        "news_lead",
                        f"the lead must state the biggest news (claim {news}: {claim.text[:120]})",
                    )
                )
    else:
        issue = skill_lead_issue(recap, article)
        if issue is not None:
            issues.append(issue)
    return report


def skill_lead_issue(recap: Recap, article: Article) -> Issue | None:
    """A skill-written story must put the meeting's biggest news in its headline, dek or lead.

    The skill's paragraphs cite its own claims, not the recap's, so the test is on the words:
    a resignation must be stated as one ("resign"), anything else in the claim's own terms.
    """
    news = top_news(recap)
    if news is None or not article.paragraphs:
        return None
    claim = recap.claim(news)
    top = f"{article.headline} {article.dek} {article.paragraphs[0].text}"
    if news_value(claim) >= 100:
        stated = bool(NEWS_WEIGHTS[0][1].search(top))
    else:
        stated = headline_names_news(top, claim)
    if stated:
        return None
    return Issue(
        "lead",
        "news_lead",
        "the headline, dek or first paragraph must state the meeting's biggest news "
        f"(claim {news}: {claim.text[:160]})",
    )


@dataclass
class Dropped:
    paragraphs: list[Paragraph] = field(default_factory=list)
    votes: list[Vote] = field(default_factory=list)
    quotes: list[Quote] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)  # blanked headings and dek
    sentences: list[str] = field(default_factory=list)  # cut from the end of a kept paragraph

    def lines(self) -> list[str]:
        out = [f"dropped paragraph: {p.text[:100]}" for p in self.paragraphs]
        out += [f"dropped vote: {_vote_text(v)[:100]}" for v in self.votes]
        out += [f"dropped quote: claim {q.claim_id}" for q in self.quotes]
        out += [f"blanked: {t[:100]}" for t in self.texts]
        out += [f"dropped sentence: {t[:100]}" for t in self.sentences]
        return out


#: Ways a claim says who spoke, which a headline does not need.
_SPOKEN_PREFIX_RE = re.compile(
    r"^(?:An?\s+(?:unidentified\s+)?speaker\s+said\s+|A\s+council\s+member\s+said\s+|"
    r"The\s+council\s+heard\s+that\s+)",
    re.IGNORECASE,
)


def drop_unsupported(
    recap: Recap,
    article: Article,
    windows: dict[str, str] | None = None,
    pages: dict[str, DocPage] | None = None,
    speakers: dict[str, str] | None = None,
    people: People | None = None,
    record: Record | None = None,
    *,
    skill_evidence: bool = False,
) -> tuple[Article, Dropped]:
    """Remove the parts that fail the check. The headline is kept as it is."""
    dropped = Dropped()

    def fails(where: str, text: str, ids: tuple[str, ...], page_ids: tuple[str, ...]) -> bool:
        issues: list[Issue] = []
        _check_text(
            where,
            text,
            ids,
            recap,
            issues,
            windows,
            page_ids,
            pages,
            speakers,
            people,
            record,
            skill_evidence=skill_evidence,
        )
        return bool(issues)

    sections = []
    for section in article.sections:
        kept = []
        for paragraph in section.paragraphs:
            if not fails("paragraph", paragraph.text, paragraph.claim_ids, paragraph.page_ids):
                kept.append(paragraph)
                continue
            # Keep the sentences up to the first one that fails: a later sentence that has no
            # evidence must not take the sourced sentences before it down with it. Sentences
            # after a failing one are dropped too, since they may lean on it.
            sentences = split_sentences(paragraph.text)
            good: list[str] = []
            for sentence in sentences:
                if fails("paragraph", sentence, paragraph.claim_ids, paragraph.page_ids):
                    break
                good.append(sentence)
            whole = " ".join(good)
            if (
                good
                and len(good) < len(sentences)
                and not fails("paragraph", whole, paragraph.claim_ids, paragraph.page_ids)
            ):
                dropped.sentences.extend(sentences[len(good) :])
                kept.append(replace(paragraph, text=whole))
            else:
                dropped.paragraphs.append(paragraph)
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
    votes, quotes = [], []
    for vote in out.votes:
        issues: list[Issue] = []
        _check_vote("vote", vote, recap, issues, windows, speakers, people, record)
        (dropped.votes if issues else votes).append(vote)
    for quote in out.quotes:
        issues = []
        _check_quote("quote", quote, recap, issues, windows, speakers, people, record)
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
    re.compile(r"\b(ballots?|postmarks?|elections?)\b", re.IGNORECASE),
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
    announcement_kept = False
    sections: list[Section] = []
    for section in article.sections:
        also = (section.heading or "").strip().lower() in _ALSO_HEADINGS
        paragraphs: list[Paragraph] = []
        for paragraph in section.paragraphs:
            kept: list[str] = []
            for sentence in split_sentences(paragraph.text):
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
                    if announcement_kept:
                        changes.append(f"dropped a second announcement: {sentence[:80]}")
                        continue
                    announcement_kept = True
                kept.append(sentence)
            if kept:
                paragraphs.append(replace(paragraph, text=" ".join(kept)))
        if paragraphs:
            sections.append(replace(section, paragraphs=tuple(paragraphs)))
    if not changes:
        return article, []
    return replace(article, sections=tuple(sections)), changes


#: A section about board or committee applicants. The story reports who applied for what and
#: the outcome, never the applicant's opinions on other subjects.
_INTERVIEW_HEADING_RE = re.compile(
    r"\b(interviews?|applicants?|candidates?|advisory committee|committee member|board member|"
    r"vacanc(?:y|ies)|appointments?)\b",
    re.IGNORECASE,
)


def limit_interviews(article: Article) -> tuple[Article, list[str]]:
    """Keep each applicant-interview section to one short paragraph of two sentences."""
    changes: list[str] = []
    sections: list[Section] = []
    for section in article.sections:
        heading = (section.heading or "").strip()
        if (
            not section.heading
            or heading.lower() in _ALSO_HEADINGS
            or not _INTERVIEW_HEADING_RE.search(heading)
            or not section.paragraphs
        ):
            sections.append(section)
            continue
        first = section.paragraphs[0]
        sentences = _SENTENCE_RE.split(first.text.strip())
        short = first if len(sentences) <= 2 else replace(first, text=" ".join(sentences[:2]))
        if short.text != first.text or len(section.paragraphs) > 1:
            changes.append(
                f"cut an interview section to one short paragraph: {heading} "
                f"({len(section.paragraphs)} -> 1)"
            )
        sections.append(replace(section, paragraphs=(short,)))
    if not changes:
        return article, []
    return replace(article, sections=tuple(sections)), changes


#: A sum of money a story leaves out. A dollar figure a claim states is a fact the story owes
#: the reader; a rewrite is asked once to carry it.
_MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")


def missing_money(recap: Recap, article: Article) -> list[str]:
    """The dollar amounts a claim states that the story does not print, largest first."""
    parts = [article.headline, article.dek]
    parts += [paragraph.text for paragraph in article.paragraphs]
    parts += [_vote_text(vote) for vote in article.votes]
    printed = " ".join(parts).replace(" ", "")
    out: list[tuple[int, str, str]] = []
    for claim in recap.claims:
        for amount in _MONEY_RE.findall(f"{claim.text} {claim.quote}"):
            value = int(re.sub(r"[^\d]", "", amount.split(".")[0]) or 0)
            if value < 10_000:
                continue
            if amount.replace(" ", "") not in printed:
                out.append((value, amount, claim.id))
    out.sort(reverse=True)
    return [f"{amount} (claim {claim_id})" for _, amount, claim_id in out]


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


def has_video_moment(timestamp: str) -> bool:
    """Whether a claim's timestamp names a real point in the meeting video.

    A claim taken from the meeting documents carries ``0:00:00``: the recap has no moment for
    it, so it gets no video link. A genuine moment at the very start of the video (``0:00:01``
    or later) is kept.
    """
    return seconds_of(timestamp) > 0


def citations(recap: Recap, paragraph: Paragraph) -> list[dict[str, str]]:
    """The video moments a paragraph rests on, in its claim order.

    A claim with no real video moment (``0:00:00``, a fact from the documents) is not a video
    moment and gets no entry: its document pages are cited separately.
    """
    out = []
    for claim_id in paragraph.claim_ids:
        claim = recap.claim(claim_id)
        if claim is not None and has_video_moment(claim.timestamp):
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
    for wrong, right in [*seat_fixes(text, people), *fixes(text, people)]:
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
        "body": named_bodies(recap),
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
    out += [f"*{named_meeting(recap)}, {recap.meeting_date}*", ""]
    for section in article.sections:
        if section.heading:
            out += [f"## {section.heading}", ""]
        for paragraph in section.paragraphs:
            links = ", ".join(
                f"[{c['timestamp']}]({c['url']})" if c["url"] else c["timestamp"]
                for c in citations(recap, paragraph)
            )
            sources = f"Video: {links}" if links else ""
            if paragraph.page_ids:
                docs = ", ".join(
                    f"[{c['kind']} p. {c['page']}]({c['url']})"
                    for c in page_citations(paragraph, pages)
                )
                sources = f"{sources}; Documents: {docs}" if sources else f"Documents: {docs}"
            out += [f"{paragraph.text} ({sources or 'The meeting record'})", ""]
    if article.votes:
        out += ["## Votes", ""]
        for vote in article.votes:
            claim = recap.claim(vote.claim_id)
            link = ""
            if claim is not None and has_video_moment(claim.timestamp):
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
            stamp = f", {claim.timestamp}" if has_video_moment(claim.timestamp) else ""
            out += [
                f'> "{quote_words(recap, quote, people)}" ({quote.speaker}{stamp})',
                "",
            ]
    out.append(f"*{note_for(article)}*")
    return "\n".join(out) + "\n"


def cited_recap(recap: Recap, article: Article) -> Recap:
    """The recap cut to the claims the story cites: its paragraphs, its votes and its quotes.

    The page lists the evidence a story stands on. Recap claims the story never used (a caption
    garble, "the same speaker said ...") are not evidence for it and are not published.
    """
    wanted = set(article.cited)
    wanted |= {vote.claim_id for vote in article.votes}
    wanted |= {quote.claim_id for quote in article.quotes}
    return replace(recap, claims=tuple(c for c in recap.claims if c.id in wanted))


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
