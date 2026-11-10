"""Speaker turns, and roll-call speaker names. Anything not confidently named stays unknown.

Turns come from the ``>>`` marks in the captions: each mark starts a new speaker turn. When
diarization runs (diarize.py), each caption line also carries a voice label; a voice label is not
a name and never becomes one here.

A name is given only by the roll call. A chair's call that matches exactly one roster member (the
last name, plus the first name when the call gives one, plus "Mayor" only for the mayor), followed
by a short acknowledgment in the next turn ("present", "here", "yes"), names the member who
answered. Any other turn stays unknown: the calls that do not match are listed in the report, so a
reader can see what was not named and why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from civiccast_brief.meeting.roster import Member, Roster
from civiccast_brief.meeting.transcript import Segment, hms

UNKNOWN = "unknown"
MAX_ACK_GAP_S = 20.0
_TOKEN_RE = re.compile(r"[a-z][a-z'\-]*")
_ACK_WORDS = frozenset({"present", "here", "yes", "aye", "yep", "yeah", "am", "im", "i'm", "i"})
_ACK_NEEDS_ONE = frozenset({"present", "here", "yes", "aye", "yep", "yeah", "am"})
#: Member titles other bodies use before a name ("Supervisor Gauthier", "Vice President Mueller").
#: They are skipped like "council member"; they never name anyone on their own.
OTHER_TITLES = frozenset(
    {
        "supervisor", "commissioner", "trustee", "alderman", "alderwoman", "alderperson",
        "councilor", "councillor", "director", "president", "chair", "chairman", "chairwoman",
        "chairperson",
    }
)  # fmt: skip
DIARIZATION_NOT_REQUESTED = {
    "status": "not run",
    "reason": "diarization was not requested for this run (use --diarize)",
}


@dataclass
class Turn:
    index: int
    first: int
    last: int
    start: float
    end: float
    texts: list[str] = field(default_factory=list)
    speaker: str | None = None
    basis: str = "none"
    #: A title the roll call gives without a matched name ("council member"). It is not a name.
    title: str | None = None

    @property
    def text(self) -> str:
        return " ".join(self.texts)


def split_turns(segments: list[Segment]) -> list[Turn]:
    """Group caption lines into speaker turns. A ``turn_start`` line opens a new turn. Lines before
    the first mark (or a transcript with no marks at all) form turn 0 with no speaker."""
    turns: list[Turn] = []
    for position, segment in enumerate(segments):
        if segment.turn_start or not turns:
            turns.append(
                Turn(
                    index=len(turns),
                    first=position,
                    last=position,
                    start=segment.start,
                    end=segment.end,
                    texts=[segment.text],
                )
            )
        else:
            turn = turns[-1]
            turn.last = position
            turn.end = segment.end
            turn.texts.append(segment.text)
    return turns


@dataclass(frozen=True)
class Call:
    mayor_title: bool
    council_title: bool
    first: str | None
    last: str


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(
        text.lower().replace("pro-tem", "pro tem").replace("councilmember", "council member")
    )


def parse_call(text: str) -> Call | None:
    """A turn that is only a roll-call name: an optional title, then one or two name words.

    "Council member Popkin", "Mayor Susie Hidalgo-Fahring" and "SUPERVISOR GAUTHIER?" parse. "Mayor Prom McCoy" parses
    too (first word "prom"); it fails later when it matches the roster. A turn with more words
    ("Council member Pto is walking in the door") is not a call.
    """
    tokens = _tokens(text)
    i = 0
    mayor_title = False
    council_title = False
    if tokens[i : i + 3] == ["mayor", "pro", "tem"]:
        i += 3
    elif tokens[i : i + 1] == ["mayor"]:
        mayor_title = True
        i += 1
    if tokens[i : i + 2] == ["council", "member"]:
        council_title = True
        i += 2
    elif not mayor_title:
        if (
            tokens[i : i + 1] == ["vice"]
            and tokens[i + 1 : i + 2]
            and tokens[i + 1] in OTHER_TITLES
        ):
            i += 2
        elif tokens[i : i + 1] and tokens[i] in OTHER_TITLES:
            i += 1
    rest = tokens[i:]
    if not 1 <= len(rest) <= 2:
        return None
    first = rest[0] if len(rest) == 2 else None
    return Call(
        mayor_title=mayor_title,
        council_title=council_title,
        first=first,
        last=rest[-1],
    )


def match_member(call: Call, roster: Roster) -> Member | None:
    """The one roster member the call names, or None. Exact spelling only, no fuzzy matching."""
    candidates = [m for m in roster.members if m.last.lower() == call.last]
    if call.first is not None:
        candidates = [m for m in candidates if m.first.lower() == call.first]
    if call.mayor_title:
        candidates = [m for m in candidates if m.role == "Mayor"]
    return candidates[0] if len(candidates) == 1 else None


def is_acknowledgment(text: str) -> bool:
    tokens = _tokens(text)
    return (
        1 <= len(tokens) <= 3
        and all(t in _ACK_WORDS for t in tokens)
        and any(t in _ACK_NEEDS_ONE for t in tokens)
    )


def label_roll_call(turns: list[Turn], roster: Roster) -> dict[str, Any]:
    """Name the turns that answer a matched roll-call call. Returns the report of every call."""
    calls: list[dict[str, Any]] = []
    for k, turn in enumerate(turns[:-1]):
        call = parse_call(turn.text)
        if call is None:
            continue
        member = match_member(call, roster)
        following = turns[k + 1]
        entry: dict[str, Any] = {
            "turn": turn.index,
            "start": hms(turn.start),
            "call_text": turn.text[:80],
            "matched": member.name if member else None,
            "answer_turn": following.index,
            "answer_text": following.text[:40],
        }
        if not is_acknowledgment(following.text):
            entry["status"] = "next turn is not a roll-call answer, so not named"
        elif following.start - turn.end > MAX_ACK_GAP_S:
            entry["status"] = f"answer came more than {MAX_ACK_GAP_S:g} s after the call"
        elif member is not None:
            following.speaker = member.name
            following.basis = "roll_call"
            entry["status"] = "named: answer turn"
        elif call.council_title and not call.mayor_title:
            # The chair called a council member but the name does not match the roster.
            # The answer is a council member's; the name stays unknown.
            following.title = "council member"
            following.basis = "roll_call_title_only"
            entry["status"] = "title only: council member, name not on the roster"
        else:
            entry["status"] = "no roster member matches this call"
        calls.append(entry)
    return {"method": "roll_call", "calls": calls}


def apply_turns(segments: list[Segment], turns: list[Turn]) -> None:
    """Write each turn's speaker onto its caption lines. A turn with no matched name is UNKNOWN,
    including a turn that only has a title (the title is kept in speakers.json)."""
    for turn in turns:
        label = turn.speaker or UNKNOWN
        for position in range(turn.first, turn.last + 1):
            segments[position].speaker = label


def _turn_voice(segments: list[Segment], turn: Turn) -> str | None:
    """The voice on most of the turn's caption lines, or None when no line has one."""
    counts: dict[str, int] = {}
    for position in range(turn.first, turn.last + 1):
        voice = segments[position].voice
        if voice:
            counts[voice] = counts.get(voice, 0) + 1
    return max(sorted(counts), key=lambda v: counts[v]) if counts else None


def speakers_report(
    video_id: str,
    segments: list[Segment],
    turns: list[Turn],
    roll_call: dict[str, Any],
    diarization: dict[str, Any] | None = None,
    recognitions: dict[str, Any] | None = None,
    voices: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The speakers.json record. Counts are per caption line, from each line's speaker."""
    named_segments = sum(1 for segment in segments if segment.speaker not in (None, "", UNKNOWN))
    title_only_segments = sum(
        turn.last - turn.first + 1
        for turn in turns
        if turn.speaker is None and turn.title is not None
    )
    total = len(segments)
    diarized = (diarization or {}).get("status") == "run"
    return {
        "video_id": video_id,
        "diarization": diarization or DIARIZATION_NOT_REQUESTED,
        "method": (
            "names: roll call and chair recognitions; turns: caption turn marks; "
            + ("voices: speech-program diarization" if diarized else "voices: none")
        ),
        "roll_call": roll_call,
        "recognitions": recognitions or {"method": "chair_recognition", "recognitions": []},
        "voices": voices or {"method": "voice", "status": "not run (no diarization)"},
        "turns": [
            {
                "turn": turn.index,
                "start": hms(turn.start),
                "end": hms(turn.end),
                "first_segment": turn.first,
                "last_segment": turn.last,
                "speaker": turn.speaker or UNKNOWN,
                "voice": _turn_voice(segments, turn),
                "title": turn.title,
                "basis": turn.basis,
                "text": turn.text[:120],
            }
            for turn in turns
        ],
        "counts": {
            "segments": total,
            "named_segments": named_segments,
            "title_only_segments": title_only_segments,
            "unknown_segments": total - named_segments,
            "named_percent": round(100 * named_segments / total, 2) if total else 0.0,
            "turns": len(turns),
            "named_turns": sum(1 for t in turns if t.speaker is not None),
        },
    }


# --- Chair recognitions and voices -------------------------------------------------------------
#
# The roll call names only the few lines that answer it. Most of a meeting is people the chair
# hands the floor to ("Council member Marcen", "Mayor Pro Tem McCoy"), and the captioner spells
# those names by ear. The chair-recognition reader copied from TownRecord
# (civiccast_brief.record.records.speakers) reads each hand-off and matches the spoken name to the
# roster by sound, with a cutoff and a margin over the runner-up calibrated on Longmont captions.
# A recognition names the turn after it. When diarization ran, a voice whose named turns agree
# names that voice's other turns too.

#: A voice is named only when at least this many of its turns were named, and ...
MIN_VOICE_TURNS = 2
#: ... at least this share of its named turns name the same person.
MIN_VOICE_SHARE = 0.8
#: The chair's voice says at least this many recognitions, and this share of them.
MIN_CHAIR_RECOGNITIONS = 5
MIN_CHAIR_SHARE = 0.2
#: A named turn whose own voice has no name of its own keeps its name only when that voice is on
#: at least this share of the turn's voiced lines (the chair's not counted). A turn the captioner
#: did not split, such as a question and a staff answer, is left unknown.
MIN_OWN_SHARE = 0.8
#: A voice with no name of its own lends a turn's name to its lines only when at least this share
#: of all that voice's lines fall in turns named that person. A staff voice that presents at
#: length elsewhere fails it, so a staff answer inside a member's turn stays unknown.
MIN_HOME_SHARE = 0.5
_GLUED_PRO_TEM = re.compile(r"^(?:prom|port|pro)(?=[a-z]{3,})", re.IGNORECASE)


def _people(roster: Roster) -> tuple[list[Any], dict[str, Any]]:
    from civiccast_brief.record.repo.rows import Person

    people = [Person(id=i + 1, name=m.name, created_at="") for i, m in enumerate(roster.members)]
    holders: dict[str, Any] = {}
    for person, member in zip(people, roster.members, strict=True):
        role = member.role.casefold()
        if role in ("mayor", "mayor pro tem"):
            holders[role] = person
    return people, holders


def name_recognition(title_kind: str, spoken: str, roster: Roster) -> tuple[str | None, Any]:
    """The roster name one chair recognition gives, or None, with the match behind it.

    A seat title ("Mayor") names its holder. "Mayor Pro Tem McCoy" names McCoy by sound when the
    roster has no pro tem holder, because the name itself was said. The captioner sometimes glues
    the pro tem word onto the name ("Mayor Promcoy"); that is read as a pro tem naming of "mcoy",
    never as the mayor.
    """
    from civiccast_brief.record.records import speakers as rs

    people, holders = _people(roster)
    glued = _GLUED_PRO_TEM.match(spoken)
    if title_kind == "mayor" and glued:
        title_kind, spoken = "mayor_pro_tem", spoken[glued.end() :]
    if title_kind == "mayor_pro_tem" and "mayor pro tem" not in holders:
        rest = rs._without_the_title(spoken)
        if not rest:
            return None, None
        match = rs.match_name(rest, people)
    else:
        match = rs.read_naming(title_kind, spoken, officials=people, holders=holders)
    return (match.person.name if match.person else None), match


#: Words that may follow the name in a hand-off ("Council member Popkin, go ahead, please.").
_HAND_OFF_TAIL = frozenset(
    [
        "go",
        "ahead",
        "please",
        "thank",
        "thanks",
        "you",
        "quickly",
        "real",
        "quick",
        "yes",
        "okay",
        "ok",
        "um",
        "uh",
        "floor",
        "the",
        "have",
    ]
)
#: A title right after one of these words is a mention ("seconded by Council Member Popkin",
#: "carries six to one with Council Member Crist", "as Council Member Prieto said").
_MENTION_BEFORE = frozenset(["with", "by", "from", "to", "as", "what", "and", "of", "about", "for"])
_TITLE_WORDS = re.compile(r"\b(council\s*(?:member|man|woman)s?|mayor)\b", re.IGNORECASE)


def is_hand_off(evidence: str, spoken: str) -> bool:
    """True when the line hands the floor to the named person, not when it mentions them.

    The name must end the line, be followed only by words such as "go ahead" or "thank you",
    or open a question to them ("Council member Popkin, did you have another item?"). The
    title must not follow a word such as "with", "by" or "as". Measured on five Longmont
    meetings (October 2026): about one in eight recognitions read by the TownRecord code was a
    mention, and naming the next turn from it named the wrong person.
    """
    first = spoken.split()[0].lower() if spoken.split() else ""
    text = evidence.replace(">>", " ").strip()
    at = text.lower().rfind(first) if first else -1
    if at < 0:
        return False
    rest = text[at + len(first) :]
    tail = re.sub(r"[^\w\s']", " ", rest).lower().split()
    question = rest.lstrip().startswith(",") and text.endswith("?")
    if not question and any(word not in _HAND_OFF_TAIL for word in tail):
        return False
    titles = list(_TITLE_WORDS.finditer(text[:at]))
    if titles:
        before = re.sub(r"[^\w\s']", " ", text[: titles[-1].start()]).lower().split()
        if before and before[-1] in _MENTION_BEFORE:
            return False
    return True


def label_recognitions(
    segments: list[Segment], turns: list[Turn], roster: Roster
) -> dict[str, Any]:
    """Name each turn the chair hands the floor to. A turn the roll call named keeps its name."""
    from civiccast_brief.record.records import speakers as rs
    from civiccast_brief.record.repo.rows import Segment as RecordSegment

    rows = [
        RecordSegment(
            id=position,
            transcript_id=0,
            start_ms=round(s.start * 1000),
            end_ms=round(s.end * 1000),
            text=(f"{rs.CHANGE_MARKER} " if s.turn_start and position else "") + s.text,
            speaker_label=None,
        )
        for position, s in enumerate(segments)
    ]
    runs = rs.runs_of(rows)
    assert [r.start_segment_id for r in runs] == [t.first for t in turns], "runs differ from turns"
    found: list[dict[str, Any]] = []
    for run in runs[:-1]:
        recognition = rs.recognition_of(run)
        if recognition is None:
            continue
        name, match = name_recognition(recognition.title_kind, recognition.spoken, roster)
        target = turns[run.index + 1]
        entry: dict[str, Any] = {
            "turn": run.index,
            "start": hms(turns[run.index].start),
            "spoken": recognition.spoken,
            "title": recognition.title_kind,
            "matched": name,
            "score": match.score if match else None,
            "runner_up": match.runner_up if match else None,
            "named_turn": target.index,
            "evidence": recognition.evidence[:120],
        }
        if name is None:
            entry["status"] = "no roster member matches by sound"
        elif not is_hand_off(recognition.evidence, recognition.spoken):
            entry["status"] = "a mention, not a hand-off"
        elif target.speaker is not None:
            entry["status"] = f"turn already named by {target.basis}"
        else:
            target.speaker, target.basis = name, "recognition"
            entry["status"] = "named: next turn"
        found.append(entry)
    return {"method": "chair_recognition", "recognitions": found}


def chair_voice(
    segments: list[Segment], turns: list[Turn], recognitions: list[dict[str, Any]]
) -> str | None:
    """The voice that says the chair's hand-offs, or None.

    Each recognition is said on one caption line; the voice on that line is counted. The voice
    with the most is the chair's when it said at least MIN_CHAIR_RECOGNITIONS of them and
    MIN_CHAIR_SHARE of all of them. The chair's own lines are never given the name of the person
    the chair handed the floor to.
    """
    counts: dict[str, int] = {}
    for found in recognitions:
        turn = turns[found["turn"]]
        word = (found["spoken"].split() or [""])[0].casefold()
        said = next(
            (
                p
                for p in range(turn.last, turn.first - 1, -1)
                if word and word in segments[p].text.casefold()
            ),
            turn.last,
        )
        voice = segments[said].voice
        if voice:
            counts[voice] = counts.get(voice, 0) + 1
    if not counts:
        return None
    top = max(sorted(counts), key=lambda v: counts[v])
    total = sum(counts.values())
    if counts[top] >= MIN_CHAIR_RECOGNITIONS and counts[top] / total >= MIN_CHAIR_SHARE:
        return top
    return None


def _voice_counts(segments: list[Segment], turn: Turn, chair: str | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for position in range(turn.first, turn.last + 1):
        voice = segments[position].voice
        if voice and voice != chair:
            counts[voice] = counts.get(voice, 0) + 1
    return counts


def _own_voice(segments: list[Segment], turn: Turn, chair: str | None) -> str | None:
    """The voice on most of a turn's lines, leaving out the chair's voice."""
    counts = _voice_counts(segments, turn, chair)
    return max(sorted(counts), key=lambda v: counts[v]) if counts else None


#: The chair's voice is named the mayor only when, at least this many times, another voice says
#: "Mayor" (not "Mayor Pro Tem") and the chair's voice is the next voice heard, ...
MIN_MAYOR_ADDRESSES = 3
#: ... and at least this share of all such addresses are to the mayor, not to a pro tem.
MIN_MAYOR_SHARE = 0.8
#: The chair must speak within this many seconds of the line that addresses it.
MAX_ADDRESS_GAP_S = 10.0
#: How many caption lines ahead to look for the next voice.
_ADDRESS_LOOKAHEAD = 5
_MAYOR = re.compile(r"\bmayor\b", re.IGNORECASE)
#: "Mayor Pro Tem", and the captioner's "Mayor Prom", "Mayor Promcoy", "Mayor Port".
_MAYOR_PRO_TEM = re.compile(
    r"\bmayor\s*(?:pro\b|pro[\s\-]*(?:tem|team|tim)|prom|port)", re.IGNORECASE
)
_INTRODUCTION = re.compile(
    r"\bmy name is\s+(?:(?:uh|um),?\s+)*(?P<name>[A-Za-z][\w'\-]*(?:\s+[A-Za-z][\w'\-]*){0,2})",
    re.IGNORECASE,
)
_NAME_STOP = frozenset({"and", "i", "i'm", "im", "from", "with", "at", "the", "uh", "um", "good"})


def _addresses(text: str) -> tuple[int, int]:
    """(mayor, pro tem) addresses in one line. A line that says both counts as pro tem."""
    if _MAYOR_PRO_TEM.search(text):
        return 0, 1
    return (1, 0) if _MAYOR.search(text) else (0, 0)


def chair_name(segments: list[Segment], chair: str | None, roster: Roster | None) -> dict[str, Any]:
    """Whether the chair's voice is the roster's mayor, with the count behind the decision.

    The chair is whoever runs the meeting; that is the mayor unless the mayor is away and the
    pro tem chairs. So the chair's voice is named the mayor only when the room answers to it as
    "Mayor": another voice says "Mayor" and the chair's voice is the next one heard, at least
    MIN_MAYOR_ADDRESSES times, and at least MIN_MAYOR_SHARE of those addresses are not to a pro
    tem. On five Longmont meetings (Sep 22 to Nov 3, 2026) the chair answered to "Mayor" 5 to 19
    times and the chair's voice also recognized "Mayor Pro Tem McCoy", so it was not the pro tem.
    """
    result: dict[str, Any] = {
        "voice": chair,
        "name": None,
        "mayor_addresses": 0,
        "pro_tem_addresses": 0,
        "min_addresses": MIN_MAYOR_ADDRESSES,
        "min_share": MIN_MAYOR_SHARE,
    }
    mayors = [m for m in roster.members if m.role.casefold() == "mayor"] if roster else []
    if chair is None:
        result["status"] = "no chair voice"
        return result
    if len(mayors) != 1:
        result["status"] = "the roster does not list exactly one mayor"
        return result
    for position, segment in enumerate(segments):
        if segment.voice in (None, chair):
            continue
        mayor, pro_tem = _addresses(segment.text)
        if not mayor and not pro_tem:
            continue
        following = segments[position + 1 : position + 1 + _ADDRESS_LOOKAHEAD]
        nxt = next((x for x in following if x.voice and x.voice != segment.voice), None)
        if nxt is None or nxt.voice != chair or nxt.start - segment.end > MAX_ADDRESS_GAP_S:
            continue
        result["mayor_addresses"] += mayor
        result["pro_tem_addresses"] += pro_tem
    total = result["mayor_addresses"] + result["pro_tem_addresses"]
    if result["mayor_addresses"] < MIN_MAYOR_ADDRESSES:
        result["status"] = "too few lines address the chair as Mayor"
    elif result["mayor_addresses"] / total < MIN_MAYOR_SHARE:
        result["status"] = "the chair is also addressed as Mayor Pro Tem"
    else:
        result["name"] = mayors[0].name
        result["status"] = "named: the chair answers to Mayor"
    return result


def introductions(segments: list[Segment]) -> list[dict[str, Any]]:
    """Self-introductions ("My name is ..."), listed for the editor. They never name anyone.

    The sound match is built for names said to council members. A visitor's own name can sound
    like a member's: on the Longmont meetings "Joan Peek" (a resident) matched Matthew Popkin at
    0.85 and "Morgan Lundy" (city staff) matched Jake Marsing at 0.96. Names of people not on the
    roster are spelled by the captioner ("Espanzoriz", "Jack Bestl") and cannot be checked here.
    """
    found: list[dict[str, Any]] = []
    for position, segment in enumerate(segments):
        nxt = segments[position + 1].text if position + 1 < len(segments) else ""
        match = _INTRODUCTION.search(f"{segment.text} {nxt}")
        if match is None or match.start() >= len(segment.text):
            continue
        words: list[str] = []
        for word in match.group("name").split():
            if word.casefold().strip("'.,") in _NAME_STOP:
                break
            words.append(word.strip(".,"))
        if not words:
            continue
        found.append(
            {
                "start": hms(segment.start),
                "voice": segment.voice,
                "spoken": " ".join(words),
                "status": "not named: a self-introduction is not checked against a roster",
            }
        )
    return found


def label_voices(
    segments: list[Segment],
    turns: list[Turn],
    recognitions: list[dict[str, Any]],
    roster: Roster | None = None,
) -> dict[str, Any]:
    """Name caption lines by voice, after diarization. Writes each line's speaker.

    A turn named by the roll call or a recognition gives its name to its own voice (the voice on
    most of its lines, not counting the chair's). A voice is named when at least MIN_VOICE_TURNS
    of its turns were named and MIN_VOICE_SHARE of those name the same person. Then every line
    is labelled:
    - a line in the chair's voice stays unknown (the chair's words are not the member's);
    - a line in a named voice gets that voice's name;
    - a line of a named turn in the turn's own voice, or with no voice, keeps the turn's name,
      when that voice is on MIN_OWN_SHARE of the turn's voiced lines and, if the voice has no
      name, MIN_HOME_SHARE of the voice's lines are in turns named that person;
    - any other line stays unknown.
    """
    chair = chair_voice(segments, turns, recognitions)
    tally: dict[str, dict[str, int]] = {}
    for turn in turns:
        voice = _own_voice(segments, turn, chair)
        if voice and turn.speaker is not None:
            counts = tally.setdefault(voice, {})
            counts[turn.speaker] = counts.get(turn.speaker, 0) + 1
    voices: list[dict[str, Any]] = []
    named: dict[str, str] = {}
    for voice in sorted(tally):
        counts = tally[voice]
        total = sum(counts.values())
        top = max(sorted(counts), key=lambda n: counts[n])
        agree = total >= MIN_VOICE_TURNS and counts[top] / total >= MIN_VOICE_SHARE
        if agree:
            named[voice] = top
        voices.append(
            {
                "voice": voice,
                "named_turns": dict(sorted(counts.items())),
                "name": top if agree else None,
                "status": "named" if agree else "named turns too few or disagree",
            }
        )
    chair_found = chair_name(segments, chair, roster)
    if chair_found["name"] is not None and chair_found["name"] in named.values():
        chair_found["name"] = None
        chair_found["status"] = "another voice already has the mayor's name"
    voice_lines: dict[str, int] = {}
    home_lines: dict[tuple[str, str], int] = {}
    for turn in turns:
        for position in range(turn.first, turn.last + 1):
            voice = segments[position].voice
            if voice is None:
                continue
            voice_lines[voice] = voice_lines.get(voice, 0) + 1
            if turn.speaker is not None:
                key = (voice, turn.speaker)
                home_lines[key] = home_lines.get(key, 0) + 1
    by_voice = 0
    for turn in turns:
        counts = _voice_counts(segments, turn, chair)
        own = _own_voice(segments, turn, chair)
        if own is not None and counts[own] / sum(counts.values()) < MIN_OWN_SHARE:
            own = None
        if (
            own is not None
            and own not in named
            and turn.speaker is not None
            and home_lines.get((own, turn.speaker), 0) / voice_lines[own] < MIN_HOME_SHARE
        ):
            own = None
        for position in range(turn.first, turn.last + 1):
            segment = segments[position]
            voice = segment.voice
            segment.basis = None
            if voice is not None and voice == chair:
                label = chair_found["name"]
                segment.basis = "chair" if label else None
            elif voice in named:
                label = named[voice]
                if label != turn.speaker:
                    by_voice += 1
            elif turn.speaker is not None and (voice is None or voice == own):
                label = turn.speaker
            else:
                label = None
            segment.speaker = label or UNKNOWN
    return {
        "method": "voice",
        "chair_voice": chair,
        "chair": chair_found,
        "introductions": introductions(segments),
        "min_turns": MIN_VOICE_TURNS,
        "min_share": MIN_VOICE_SHARE,
        "voices": voices,
        "lines_named_by_voice": by_voice,
    }
