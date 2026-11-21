"""The weekly roundup, built from the week's new meeting stories.

The meetings have already been written, each by the report-local-news skill, each
with its own checked claim list. The roundup is not a fourth pass over the same
recaps: it is the new stories put in one order, the week's biggest news first.
Every paragraph keeps the claims it was published with, so the roundup's evidence
is the meetings' own evidence, and a paragraph that was not published in a
meeting story is not in the roundup.

The lead is the story whose opening carries the strongest news (a resignation
outranks a money, land or law vote; that outranks a divided vote). Each other
story gets one section under its own headline, and the meetings' "also" sections
become one "Also this week" at the end.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

from civiccast_brief.article.writer import Article, Paragraph, Section, news_value
from civiccast_brief.script.recap import Claim, Recap

_ALSO = re.compile(r"^also\b", re.IGNORECASE)


@dataclass(frozen=True)
class Story:
    """One meeting's published story and the claims it rests on."""

    article: Article
    recap: Recap


def load_story(article_path: Path) -> Story:
    """The story the article step saved, with the evidence it was checked against."""
    from civiccast_brief.article.writer import load_article
    from civiccast_brief.script.recap import load_recap

    article = load_article(article_path)
    evidence = article_path.with_name("story-recap.json")
    if not evidence.is_file():
        evidence = article_path.with_name("recap.json")
    recap = load_recap(evidence)
    return Story(article=article, recap=recap)


def _lead_rank(story: Story) -> int:
    first = story.article.paragraphs[0].text if story.article.paragraphs else ""
    claim = SimpleNamespace(topic="", text=f"{story.article.headline} {first}", quote="")
    return news_value(claim)


def _also_paragraphs(story: Story) -> list[Paragraph]:
    out: list[Paragraph] = []
    for section in story.article.sections:
        if section.heading and _ALSO.match(section.heading):
            out.extend(section.paragraphs)
    return out


def build(stories: list[Story]) -> tuple[Article, Recap]:
    """The roundup Article and the merged recap its paragraphs cite."""
    if not stories:
        raise ValueError("a roundup needs at least one meeting story")
    ranked = sorted(enumerate(stories), key=lambda pair: (-_lead_rank(pair[1]), pair[0]))
    lead_index = ranked[0][0]
    lead = stories[lead_index]
    claims: list[Claim] = []
    ids: dict[tuple[int, str], str] = {}
    for index, story in enumerate(stories):
        for claim in story.recap.claims:
            new_id = f"m{index + 1}-{claim.id}"
            ids[(index, claim.id)] = new_id
            claims.append(replace(claim, id=new_id))

    def carried(index: int, paragraphs: tuple[Paragraph, ...]) -> tuple[Paragraph, ...]:
        story = stories[index]
        known = {claim.id for claim in story.recap.claims}
        out = []
        for paragraph in paragraphs:
            cited = tuple(ids[(index, c)] for c in paragraph.claim_ids if c in known)
            if cited:
                # The roundup's evidence is the meetings' claims; a page id belongs to one
                # meeting's documents and is not re-cited here.
                out.append(replace(paragraph, claim_ids=cited, page_ids=()))
        return tuple(out)

    sections: list[Section] = []
    lead_paragraphs = carried(lead_index, tuple(lead.article.paragraphs[:3]))
    if lead_paragraphs:
        sections.append(Section(heading=None, paragraphs=lead_paragraphs))
    for index, story in enumerate(stories):
        if index == lead_index:
            continue
        body = tuple(
            p
            for section in story.article.sections
            if not (section.heading and _ALSO.match(section.heading))
            for p in section.paragraphs
        )[:3]
        paragraphs = carried(index, body)
        if paragraphs:
            sections.append(Section(heading=story.article.headline, paragraphs=paragraphs))
    also: list[Paragraph] = []
    for index, story in enumerate(stories):
        also.extend(carried(index, tuple(_also_paragraphs(story))))
    if also:
        # The roundup's "Also this week" is the week's routine items in one short section,
        # not every meeting's routine paragraphs again.
        sections.append(Section(heading="Also this week", paragraphs=tuple(also[:8])))
    headline = lead.article.headline
    # The lead story's dek can state facts from paragraphs the roundup does not carry, so
    # the roundup leaves it out rather than print a fact its paragraphs do not cite.
    dek = ""
    merged = Recap(
        city=lead.recap.city,
        body=lead.recap.body,
        body_label=lead.recap.body_label,
        meeting_date=lead.recap.meeting_date,
        video_id="roundup",
        claims=tuple(claims),
    )
    return Article(headline=headline, dek=dek, sections=tuple(sections)), merged


def from_files(paths: list[Path]) -> tuple[Article, Recap]:
    return build([load_story(path) for path in paths])


def checked_title_headline(recap: Recap, folder: Path, fallback: str) -> str:
    """The roundup's checked title from ``folder/title.json``, or ``fallback``.

    The title was written and checked against the same roundup recap (``article/title.py``), so
    it is the roundup's own headline, not the lead story's. A missing, unreadable or failing
    title keeps today's behaviour: the lead story's headline.
    """
    from civiccast_brief.article.title import check_title, load_title

    path = Path(folder) / "title.json"
    if not path.is_file():
        return fallback
    try:
        title = load_title(path)
    except (OSError, ValueError):
        return fallback
    return fallback if check_title(recap, title) else title.text


def story_paths_from_state(state_path: Path) -> list[Path]:
    """The article.json of every meeting this run finished, in date order."""
    state = json.loads(state_path.read_text(encoding="utf-8"))
    out: list[tuple[str, Path]] = []
    for entry in state.get("meetings", {}).values():
        if entry.get("status") != "done" or not entry.get("article"):
            continue
        out.append((str(entry.get("day") or ""), Path(entry["article"])))
    return [path for _day, path in sorted(out)]
