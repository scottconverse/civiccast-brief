#!/usr/bin/env python3
"""
audit.py -- mechanical desk audit for the report-local-news skill, v3.

Usage:
  python audit.py draft.txt
  python audit.py draft.txt --source article1.txt --source article2.txt
  python audit.py draft.txt --meeting

--meeting turns on meeting mode for a story built from a public meeting
(transcript, video, agenda, packet, or minutes). Meeting mode also turns on
by itself when the draft has an ALSO AT THE MEETING heading or a MEETING
LEDGER section. In meeting mode the EDITOR FILE must carry a MEETING LEDGER
that gives every newsworthy item from the meeting one status, and every
covered item must be findable in the story.

Exit code 0 = no violations. Exit code 1 = violations. Exit code 2 = usage.

VIOLATION = the skill bans it outright. Fix it.
WARNING   = the skill needs a judgment call. Review it and keep only what
            the reporting earns.

This script checks characters, structure, word lists, rhythm, the claims
appendix format, link hygiene, and phrase overlap with supplied source
articles. It does NOT verify facts, quotes, links, legal status, or
fairness. Those passes stay with the model and must each leave an
evidence line.

Method credit: the coefficient-of-variation, cadence-alternation, and
n-gram repetition checks follow the approach in sloptrim (Apache-2.0,
github.com/seyedehsanhadi/sloptrim). The code here is original.
"""
import argparse
import collections
import re
import statistics
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit, parse_qsl

VERSION = "report-local-news audit v3.0"

# ---------------------------------------------------------------- data


@dataclass
class Finding:
    severity: str   # VIOLATION | WARNING
    code: str
    line: int       # 1-based; 0 = whole draft
    message: str
    snippet: str


VOCAB = [
    # 2023 residue
    "delve", "tapestry", "testament", "pivotal", "crucial", "vital",
    "robust", "seamless", "meticulous", "nuanced", "multifaceted",
    "intricate", "intricacies", "interplay", "myriad", "plethora",
    "transformative", "innovative", "unlock", "empower", "elevate",
    "resonate", "foster", "fostering", "leverage", "harness", "garner",
    "bolster", "enduring", "indelible", "deeply rooted",
    # current residue
    "underscore", "underscores", "underscoring", "highlight",
    "highlights", "highlighting", "emphasize", "emphasizing", "showcase",
    "showcases", "showcasing", "enhance", "enhancing", "ensure",
    "ensuring", "align", "aligning", "reflecting", "symbolizing",
    "encompassing", "contributing to", "cultivating", "boast", "boasts",
    "vibrant", "profound", "renowned", "groundbreaking", "nestled",
    "valuable insights", "diverse array", "commitment to",
    "in the heart of", "stakeholders",
]
BOOSTER = [
    "charming", "quaint", "hidden gem", "best-kept secret",
    "small-town feel", "tight-knit", "vibrant downtown",
    "gateway to the mountains", "300 days of sunshine", "world-class",
    "our beautiful city",
]
ABSTRACTIONS = [
    "landscape", "realm", "journey", "ecosystem", "fabric", "sphere",
    "arena",
]
TRANSITIONS = [
    "at its core", "that being said", "it's important to note",
    "it is important to note", "it's worth noting", "it is worth noting",
    "in today's fast-paced world", "in an era of", "at the end of the day",
    "in conclusion", "in summary", "a testament to", "the bottom line is",
    "needless to say", "when it comes to", "moreover", "furthermore",
    "additionally", "let that sink in", "this moment demands",
    "only time will tell", "remains to be seen",
]
HIPROB = [
    "raises serious questions", "deeply troubling", "profit-driven",
    "corporate greed", "working families", "hold accountable",
    "transparency and accountability", "must do better",
    "sent shockwaves", "sparked outrage", "sparked concern",
]
FORMULAS = [
    "isn't merely", "is not merely", "whether you're", "whether you are",
    "everything from",
]
# "not just X but Y" is the formula; a literal "not just students" is fine.
NOT_JUST = re.compile(r"\bnot (?:just|only|merely)\b(?:\W+\w+){0,8}?\W+but\b",
                      re.I)
ATTRIB = [
    "experts say", "experts argue", "critics say", "critics argue",
    "critics note", "observers note", "observers say", "studies show",
    "reports suggest", "sources indicate", "some believe",
    "many have pointed out", "industry reports", "some residents",
    "many residents", "some parents", "many parents", "some say",
    "many say", "people are saying", "it is believed", "it is thought",
]
RESTATE = ["in short,", "in other words,", "put simply,", "to be clear,",
           "as noted"]
COPULA = ["serves as", "stands as", "functions as", "operates as"]
COPULA_SOFT = ["marks a", "represents a significant", "represents a major",
               "represents a shift", "represents a new"]
# Literal uses of words on the stock list.
VOCAB_EXEMPT = ["foster care", "foster home", "foster parent", "foster child",
                "foster famil", "foster youth", "vital records", "vital signs",
                "vital statistics", "highlight reel"]
TAIL_WORDS = ("highlighting|underscoring|emphasizing|showcasing|reflecting|"
              "symbolizing|marking|cementing|ensuring|paving|contributing|"
              "demonstrating|signaling|signalling")
# Straight news carries no verdict. These words are the reporter's opinion.
VERDICT = [
    "shocking", "outrageous", "disgraceful", "shameful", "heartbreaking",
    "sadly", "unfortunately", "fortunately", "alarming", "stunning",
    "egregious", "appalling", "devastating", "tragically", "thankfully",
    "incredibly", "amazingly",
]
# Loaded attribution verbs. "Said" is almost always right.
LOADED_VERBS = [
    "admitted", "admits", "claimed", "claims", "insisted", "insists",
    "conceded", "concedes", "slammed", "slams", "blasted", "blasts",
    "touted", "touts", "lashed out", "refused to say", "doubled down",
]
# Trend words need a denominator.
TREND = ["wave of", "surge in", "flurry of", "spate of", "epidemic of",
         "skyrocket", "skyrocketed", "plummeted", "soared"]
# Outreach phrases are true only if a request was actually sent and logged.
OUTREACH = ["did not respond", "didn't respond", "declined to comment",
            "could not be reached", "couldn't be reached",
            "did not return", "didn't return", "no response"]
ABBREV = ("a.m", "p.m", "Mr", "Mrs", "Ms", "Dr", "St", "Ave", "Blvd", "Rd",
          "vs", "etc",
          "e.g", "i.e", "U.S", "U.K", "Jan", "Feb", "Mar", "Apr", "Aug",
          "Sept", "Sep", "Oct", "Nov", "Dec", "Inc", "Corp", "Co", "No",
          "Gov", "Sen", "Rep", "Lt", "Sgt", "Capt", "Jr", "Sr", "p", "pp")

HEDGES = re.compile(
    r"\b(?:arguably|perhaps|possibly|potentially|seemingly|apparently"
    r"|somewhat|fairly|quite|largely|generally|might|it is possible"
    r"|in some ways|to some extent|more or less|appears? to|seems? to)\b",
    re.I)

SYNONYM_CLUSTERS = [
    ["company", "firm", "corporation", "conglomerate", "enterprise"],
    ["agency", "regulator", "commission", "authority", "watchdog"],
    ["resident", "citizen", "taxpayer", "constituent", "ratepayer"],
    ["district", "institution", "organization", "education provider"],
    ["report", "study", "analysis", "review"],
    ["law", "statute", "regulation", "ordinance"],
]

EMOJI = re.compile("[%s-%s%s-%s%s-%s%s]" % tuple(
    chr(c) for c in (0x1F000, 0x1FAFF, 0x2600, 0x27BF, 0x2B00, 0x2BFF,
                     0xFE0F)))
INVISIBLE = frozenset(
    [0xA0, 0x200B, 0x200C, 0x200D, 0x202F, 0x2060, 0xFEFF, 0x00AD]
    + list(range(0x2000, 0x200B)))
CONFUSABLE = re.compile("[%s-%s%s-%s]" % tuple(
    chr(c) for c in (0x0400, 0x04FF, 0x0370, 0x03FF)))
DASHES = (chr(0x2014), chr(0x2013))          # em dash, en dash
CURLY = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019, 0x2026))
URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.I)
WORD_RE = re.compile(r"[A-Za-z0-9']+")

APPENDIX_HEAD = re.compile(r"^\s*CLAIMS AND SOURCES\s*$")
EDITOR_HEAD = re.compile(r"^\s*EDITOR FILE\b.*$")
REQUIRED_SECTIONS = ["CLAIM CHECK", "RECORDS READ", "OUTREACH LOG",
                     "AUDIT LOG"]
OPTIONAL_SECTIONS = ["EXCLUDED OR UNRESOLVED", "EDITOR'S FACT SHEET",
                     "MEETING LEDGER"]
ROUNDUP_HEAD = re.compile(r"^\s*ALSO AT THE MEETING\s*$")
LEDGER_TYPES = {"vote", "motion", "staff report", "presentation",
                "public comment", "public hearing", "council comment",
                "manager update", "attorney update", "proclamation",
                "announcement", "consent item", "other"}
LEDGER_STATUS = {"lead", "roundup", "excluded"}
LEDGER_MIN_ITEMS = 6      # a long meeting with fewer listed items is suspect
EXCLUDED_SHARE_WARN = 0.34
PUBLIC_SOURCE = re.compile(
    r"^\s*Source \((primary record|secondary source)[^)]*\):\s*\S", re.I)
PUBLIC_LABELS = ("claim:", "source (", "math:", "first reported by:")
FACT_SIGNAL = re.compile(
    r"\d|\"|\b(?:said|says|wrote|writes|voted|votes|approved|approves|"
    r"denied|rejected|adopted|according to|announced|reported|told|filed|"
    r"ruled|signed|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|"
    r"Sunday|January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Aug|Sept|Oct|Nov|Dec)\b")
DOMAIN = re.compile(r"\b[\w-]+\.(?:gov|com|org|net|us|edu|co|io|news)\b",
                    re.I)
STOP = {"the", "a", "an", "of", "to", "in", "and", "on", "for", "said",
        "is", "was", "that", "it", "at", "by", "with", "as", "be", "has",
        "have", "will", "from", "its", "their", "this", "are", "were",
        "which", "who", "not", "but", "or", "than", "after", "before"}
AUDIT_PASSES = 16   # Pass 0 through Pass 15
# A hyphenated number after one of these words is a record number.
RECORD_WORDS = {"resolution", "ordinance", "bill", "no", "number", "case",
                "docket", "file", "permit", "item", "rule", "regulation",
                "order", "application", "cv", "cr", "hb", "sb", "#"}
ROMAN_CONTEXT = {"title", "phase", "class", "war", "stage", "level", "type",
                 "part", "section", "article", "chapter", "district",
                 "division", "zone", "tier", "grade", "unit", "schedule"}
MEMO_HEAD = re.compile(r"^\s*(REPORTING MEMO|\[.*\])\s*$")
STATUS_OK = re.compile(r"^\s*Status:\s*(primary record|secondary source)\b",
                       re.I)
SHORTENERS = {"t.co", "bit.ly", "tinyurl.com", "ow.ly", "buff.ly",
              "goo.gl", "lnkd.in", "fb.me"}

# Thresholds. Straight news runs a little more even than a column, so
# these sit below the opinion skill's 0.45.
SENT_CV_FAIL = 0.30
SENT_CV_WARN = 0.40
ZIGZAG_MAX = 0.85
PARA_CV_MIN = 0.25
MIN_SENTENCES_FOR_CV = 12
MIN_PARAGRAPHS_FOR_CV = 8
MIN_WORDS_FOR_NGRAM = 250
NGRAM_N = 6
SOURCE_RUN_FAIL = 8      # shared words in a row with a source, outside quotes
SOURCE_RUN_WARN = 6
SOURCE_SHARE_FAIL = 0.12  # share of body 5-grams found in one source
QUOTE_BORROW_WARN = 25   # words of quoted text lifted from one source

# ---------------------------------------------------------------- helpers


def split_sentences(paragraph):
    text = paragraph
    for a in ABBREV:
        text = re.sub(r"\b" + re.escape(a) + r"\.", a + "\x00", text)
    parts = re.split(r"(?<=[.!?])[\"']?\s+(?=[\"'A-Z0-9])", text)
    return [p.replace("\x00", ".").strip() for p in parts if p.strip()]


def is_url_line(line):
    return "http://" in line or "https://" in line


def paragraphs(lines, start, end):
    """Yield (first_line_number, joined_text) for each paragraph."""
    buf, first = [], None
    for i in range(start, end):
        if lines[i].strip():
            if first is None:
                first = i + 1
            buf.append(lines[i].strip())
        elif buf:
            yield first, " ".join(buf)
            buf, first = [], None
    if buf:
        yield first, " ".join(buf)


def heading(line):
    return line.strip().rstrip(":").strip().upper()


def strip_quotes(text):
    """Blank out text inside straight double quotes (a speaker's words),
    keeping newlines so line numbers hold. Paragraph by paragraph: an
    unclosed quote runs to the end of its paragraph (AP continuing quote)."""
    out = []
    for block in re.split(r"(\n\s*\n)", text):
        if not block.strip():
            out.append(block)
            continue
        inside, buf = False, []
        for ch in block:
            if ch == '"':
                inside = not inside
                buf.append(" ")
            elif inside and ch != "\n":
                buf.append(" ")
            else:
                buf.append(ch)
        out.append("".join(buf))
    return "".join(out)


def quoted_spans(text):
    return re.findall(r"\"([^\"]*)\"", text)


def words(text):
    return [w.lower().strip("'") for w in WORD_RE.findall(text)]


def cv(nums):
    if len(nums) < 2:
        return 0.0
    m = statistics.mean(nums)
    return statistics.pstdev(nums) / m if m else 0.0


def zigzag(nums):
    signs = [1 if b > a else -1 if b < a else 0
             for a, b in zip(nums, nums[1:])]
    pairs = [(a, b) for a, b in zip(signs, signs[1:]) if a and b]
    if not pairs:
        return 0.0
    return sum(1 for a, b in pairs if a == -b) / len(pairs)


# ---------------------------------------------------------------- checks


def run_checks(text, sources, publish_only=False, meeting=False):
    out = []

    def add(sev, code, ln, msg, snip=""):
        out.append(Finding(sev, code, ln, msg, snip.strip()[:90]))

    lines = text.rstrip().split("\n")   # trailing blank lines are harmless

    # ---- sections: PUBLISH COPY (story + public CLAIMS AND SOURCES),
    # then the EDITOR FILE
    appendix = None     # index of the CLAIMS AND SOURCES heading
    editor = None       # index of the EDITOR FILE heading
    for i, ln in enumerate(lines):
        if appendix is None and APPENDIX_HEAD.match(ln):
            appendix = i
        if editor is None and EDITOR_HEAD.match(ln):
            editor = i
    if appendix is not None and editor is not None and editor < appendix:
        add("VIOLATION", "ORDER", editor + 1,
            "EDITOR FILE comes before CLAIMS AND SOURCES; the publish copy "
            "comes first")
    if publish_only:
        if editor is not None:
            add("VIOLATION", "PUBLISH-ONLY", editor + 1,
                "publish-only run, but an EDITOR FILE is present")
    else:
        if appendix is None:
            add("VIOLATION", "NO-APPENDIX", 0,
                'no "CLAIMS AND SOURCES" heading on its own line after the '
                "story")
        if editor is None:
            add("VIOLATION", "NO-EDITOR-FILE", 0,
                'no "EDITOR FILE" line after the public sources; the editor '
                "file is required")
    if editor is None:
        editor = len(lines)
    if appendix is None:
        appendix = editor
    tail_upper = [heading(l) for l in lines[editor:]]
    roundup = None      # index of the ALSO AT THE MEETING heading
    for i in range(appendix):
        if ROUNDUP_HEAD.match(lines[i]):
            if roundup is None:
                roundup = i
            else:
                add("VIOLATION", "ROUNDUP", i + 1,
                    "ALSO AT THE MEETING appears more than once")
    meeting = meeting or roundup is not None or \
        "MEETING LEDGER" in tail_upper
    required = REQUIRED_SECTIONS + (["MEETING LEDGER"] if meeting else [])
    if editor < len(lines):
        for sec in required:
            if sec not in tail_upper:
                add("VIOLATION", "NO-SECTION", editor + 1,
                    f'EDITOR FILE is missing the "{sec}" section')

    # ---- whole-document character and URL law
    blanks = 0
    for i, ln in enumerate(lines, 1):
        if any(d in ln for d in DASHES):
            add("VIOLATION", "CHAR-DASH", i,
                "em or en dash; use a period, comma, or parentheses", ln)
        if re.search(r"(?<!-)--(?!-)", ln) and not is_url_line(ln):
            add("VIOLATION", "CHAR-DASH", i,
                "double hyphen standing in for a dash", ln)
        for ch in CURLY:
            if ch in ln:
                add("VIOLATION", "CHAR-CURLY", i,
                    "curly quote, curly apostrophe, or ellipsis character", ln)
                break
        if EMOJI.search(ln):
            add("VIOLATION", "CHAR-EMOJI", i, "emoji", ln)
        for ch in ln:
            if ord(ch) in INVISIBLE:
                add("VIOLATION", "CHAR-INVIS", i,
                    f"invisible or nonstandard space U+{ord(ch):04X}; "
                    "paste artifact", ln)
                break
        if CONFUSABLE.search(ln) and re.search(r"[A-Za-z]", ln):
            add("VIOLATION", "CHAR-HOMOGLYPH", i,
                "Cyrillic or Greek letter in Latin text; paste artifact", ln)
        if re.match(r"^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s)", ln) or "**" in ln \
                or re.search(r"\[[^\]]+\]\(https?://", ln):
            add("VIOLATION", "MARKDOWN", i,
                "markdown heading, bullet, bold, or link; output is plain "
                "text", ln)
        if not is_url_line(ln):
            toks = ln.split()
            for ti, tok in enumerate(toks):
                prev = toks[ti - 1].lower().strip("(.,;:") if ti else ""
                if prev in RECORD_WORDS or \
                        re.fullmatch(r"[(\[]?(?:19|20)\d\d-\d\d[)\].,;:]?",
                                     tok):
                    continue   # Resolution 2026-45, Bill 24-005, 2026-27
                if re.fullmatch(r"[(\[]?\d{1,4}-\d{1,4}[)\].,;:]?", tok):
                    add("VIOLATION", "RANGE-HYPHEN", i,
                        "numeric range or score with a hyphen; write "
                        '"4 to 2" or "from 2019 to 2024"', ln)
        for m in URL_RE.finditer(ln):
            url = m.group(0).rstrip(".,;:")
            parts = urlsplit(url)
            keys = {k.lower() for k, _ in parse_qsl(parts.query)}
            host = parts.netloc.lower()
            if any(k.startswith("utm_") for k in keys) or \
                    keys & {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid"}:
                add("VIOLATION", "URL", i, "tracking parameter in URL", url)
            if host in SHORTENERS or "/amp/" in parts.path.lower() or \
                    host.endswith("google.com") and parts.path == "/url":
                add("VIOLATION", "URL", i,
                    "shortener, AMP, or redirect URL; link the page itself",
                    url)
            if "primegov.com" in host and re.search(
                    r"compiled|download|getfile|filestream",
                    parts.path + parts.query, re.I):
                add("VIOLATION", "URL-EXPIRING", i,
                    "PrimeGov download link expires; cite portal, meeting, "
                    "item, and packet page instead", url)
        blanks = blanks + 1 if not ln.strip() else 0
        if blanks == 2:
            add("VIOLATION", "SPACING", i,
                "two or more blank lines; use exactly one", "(blank lines)")

    # ---- story structure: headline, dek, body
    story = [(ln, p) for ln, p in paragraphs(lines, 0, appendix)
             if not ROUNDUP_HEAD.match(p)]
    memo = False
    while story and MEMO_HEAD.match(story[0][1]):
        memo = memo or "REPORTING MEMO" in story[0][1]
        story.pop(0)
    if len(story) < 3:
        add("VIOLATION", "STRUCTURE", 0,
            "expected a headline, a dek, and body paragraphs")
    headline = story[0] if story else (0, "")
    dek = story[1] if len(story) > 1 else (0, "")
    body_paras = story[2:]

    hl_ln, hl = headline
    if hl:
        if hl.rstrip().endswith("?"):
            add("VIOLATION", "HEADLINE", hl_ln,
                "question headline; state the news", hl)
        if hl.isupper() and len(hl) > 12:
            add("VIOLATION", "HEADLINE", hl_ln, "all-caps headline", hl)
        hw = [w for w in re.findall(r"[A-Za-z][A-Za-z']*", hl)[1:]
              if len(w) > 3]
        caps = [w for w in hw if w[0].isupper()]
        if len(hw) >= 4 and len(caps) / len(hw) > 0.6:
            add("WARNING", "HEADLINE", hl_ln,
                "headline looks like Title Case; use sentence case unless "
                "those words are proper nouns", hl)
        if len(words(hl)) > 14:
            add("WARNING", "HEADLINE", hl_ln,
                f"headline is {len(words(hl))} words; 14 or fewer",
                hl)
    dk_ln, dk = dek
    if dk:
        ds = split_sentences(dk)
        if len(ds) > 1:
            add("VIOLATION", "DEK", dk_ln,
                f"dek has {len(ds)} sentences; one only", dk)
        if len(words(dk)) > 30:
            add("WARNING", "DEK", dk_ln,
                f"dek is {len(words(dk))} words; 30 or fewer", dk)
        if hl and len(set(words(hl)) & set(words(dk))) >= \
                max(4, int(0.6 * len(set(words(hl))))):
            add("WARNING", "DEK", dk_ln,
                "dek repeats the headline; add a new fact instead", dk)

    # ---- prose checks on headline, dek, and body
    prose_lines = []
    for i in range(appendix):
        prose_lines.append("" if is_url_line(lines[i]) or i == roundup
                           else lines[i])
    body = "\n".join(prose_lines)
    low = body.lower()
    body_nq = strip_quotes(body)
    low_nq = body_nq.lower()

    def line_of(pos, src=body):
        return src.count("\n", 0, pos) + 1

    def phrase_hits(phrases, code, sev, msg, hay=None, orig=None):
        hay = low if hay is None else hay
        orig = body if orig is None else orig
        for p in phrases:
            for m in re.finditer(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])",
                                 hay):
                add(sev, code, line_of(m.start(), orig),
                    msg + f': "{p}"',
                    orig[max(0, m.start() - 25):m.start() + 45])

    for word in VOCAB:
        hits = [h for h in re.finditer(r"(?<![a-z])" + re.escape(word) +
                                       r"(?![a-z])", low_nq)
                if not any(low_nq.startswith(x, h.start())
                           for x in VOCAB_EXEMPT)]
        for n, m in enumerate(hits):
            sev = "WARNING" if n == 0 else "VIOLATION"
            note = ("first use; replace with a fact if you can" if n == 0
                    else "second use; replace with a fact")
            add(sev, "VOCAB", line_of(m.start(), body_nq),
                f'stock word "{word}" ({note})',
                body_nq[max(0, m.start() - 25):m.start() + 45])

    phrase_hits(BOOSTER, "BOOSTER", "VIOLATION",
                "booster language; describe the place with a fact",
                low_nq, body_nq)
    phrase_hits(ABSTRACTIONS, "ABSTRACT", "WARNING",
                "abstraction; name the actual thing unless it is literal",
                low_nq, body_nq)
    phrase_hits(TRANSITIONS, "TRANSITION", "VIOLATION",
                "empty transition or closer", low_nq, body_nq)
    phrase_hits(HIPROB, "PHRASE", "VIOLATION",
                "stock phrase; state what happened", low_nq, body_nq)
    phrase_hits(FORMULAS, "FORMULA", "VIOLATION",
                "rhetorical formula", low_nq, body_nq)
    phrase_hits(ATTRIB, "ATTRIB", "VIOLATION",
                "unnamed attribution; name the person, record, or outlet",
                low_nq, body_nq)
    phrase_hits(COPULA, "COPULA", "VIOLATION",
                "dressed-up verb; use is, was, or has", low_nq, body_nq)
    phrase_hits(COPULA_SOFT, "COPULA", "WARNING",
                "significance verb; keep only if literal", low_nq, body_nq)
    for m in NOT_JUST.finditer(body_nq):
        add("VIOLATION", "FORMULA", line_of(m.start(), body_nq),
            'rhetorical formula "not just X but Y"; state the fact',
            m.group(0))
    phrase_hits(VERDICT, "VERDICT", "VIOLATION",
                "reporter's verdict word; straight news states the fact "
                "and lets a named source judge it", low_nq, body_nq)
    phrase_hits(LOADED_VERBS, "LOADED-VERB", "WARNING",
                'loaded attribution verb; "said" is neutral', low_nq,
                body_nq)
    phrase_hits(TREND, "TREND", "WARNING",
                "trend word; needs a period, a count, and a denominator "
                "from a record", low_nq, body_nq)
    phrase_hits(OUTREACH, "OUTREACH", "WARNING",
                "outreach claim; true only if a request was actually sent "
                "and appears in the OUTREACH LOG", low_nq, body_nq)

    # first person outside quotation marks
    for m in re.finditer(r"\b(?:I|I'm|I've|I'd|I'll)\b|\b(?i:we|we're|we've|"
                         r"we'd|we'll|our|ours|us|my|me)\b", body_nq):
        tok = m.group(0)
        if tok.isupper() and len(tok) > 1:
            continue   # US 287, OUR Center, MY Chevy
        if tok == "I":
            before = body_nq[max(0, m.start() - 12):m.start()].split()
            after = body_nq[m.end():m.end() + 2]
            if re.match(r"-\s?\d", after) or (before and before[-1].lower()
                                              in ROMAN_CONTEXT):
                continue   # I-25, Title I, Phase I
        add("VIOLATION", "FIRST-PERSON", line_of(m.start(), body_nq),
            f'first person "{tok}" outside a quote; the reporter is not '
            "in the story",
            body_nq[max(0, m.start() - 25):m.start() + 35])

    # rhetorical questions outside quotes
    for m in re.finditer(r"[^.!?\"\n]{8,}\?", body_nq):
        add("WARNING", "QUESTION", line_of(m.start(), body_nq),
            "question in reporter's voice; state the open question as a "
            "fact about what is unknown", m.group(0))

    for p in RESTATE:
        for m in re.finditer(r"(?:^|\n|\. )" + re.escape(p), low_nq):
            add("VIOLATION", "RESTATE", line_of(m.start(), body_nq),
                f'restatement opener "{p}"', body_nq[m.start():m.start() + 60])

    for m in re.finditer(r",\s+(?:" + TAIL_WORDS + r")\b[^.!?\n]*[.!?]",
                         body_nq, re.I):
        add("VIOLATION", "TAIL", line_of(m.start(), body_nq),
            "significance tail; promote to a sentence with a fact, or "
            "delete", m.group())
    for m in re.finditer(r",\s+(\w+ing)\b[^.!?\n]{0,50}[.!?]", body_nq):
        if not re.match(TAIL_WORDS, m.group(1), re.I) and \
                m.group(1).lower() not in ("including", "according"):
            add("WARNING", "TAIL", line_of(m.start(), body_nq),
                "possible participle tail; keep only if it adds a fact",
                m.group())

    for m in re.finditer(r":\s+\w+(?:\s+\w+){0,2}[.!]", body_nq):
        add("WARNING", "COLON", line_of(m.start(), body_nq),
            "punch colon; use a period and a new sentence", m.group())

    for m in re.finditer(r"\b(\w+), (\w+),? and \w+\b", body_nq):
        before = body_nq[max(0, m.start() - 25):m.start()]
        if re.fullmatch(r"(?:19|20)\d\d", m.group(2)) or \
                "," in before.rsplit(".", 1)[-1]:
            continue   # "Oct. 14, 2026, and" or a list of four or more
        add("WARNING", "TRIAD", line_of(m.start(), body_nq),
            "three-item list; keep only when the record has exactly three",
            m.group())

    # ISO dates in the story
    for m in re.finditer(r"\b20\d\d-\d\d-\d\d\b", body):
        add("VIOLATION", "DATE-FORMAT", line_of(m.start()),
            'ISO date in story text; write "Sept. 28, 2026"', m.group())

    # ---- paragraph-level checks, body only
    openers = {}
    sent_lengths = []
    para_words = []
    body_words_all = []
    for first_ln, para in body_paras:
        sents = split_sentences(para)
        if len(sents) > 3:
            add("VIOLATION", "PARA-LEN", first_ln,
                f"{len(sents)} sentences in one paragraph; cap is 3", para)
        wc = len(words(para))
        para_words.append(wc)
        body_words_all.extend(words(para))
        if wc > 70:
            add("WARNING", "PARA-WORDS", first_ln,
                f"{wc} words in one paragraph; hard to read on a phone",
                para)
        w = re.match(r"[\"']?(\w+)(?:\s+(\w+))?", para)
        if w:
            key = " ".join(x for x in w.groups() if x).lower()
            openers.setdefault(key, []).append(first_ln)
        for s in sents:
            n = len(words(s))
            sent_lengths.append((first_ln, n))
            if n > 40:
                add("WARNING", "LONG-SENT", first_ln,
                    f"{n}-word sentence; split it", s)
            hs = HEDGES.findall(strip_quotes(s))
            if len(hs) >= 2:
                add("VIOLATION", "HEDGE-STACK", first_ln,
                    f"{len(hs)} hedges in one sentence ({', '.join(hs[:3])}); "
                    "state what is known and what is not", s)

    for key, lns in openers.items():
        if len(lns) >= 3:
            add("WARNING", "OPENER", lns[2],
                f'"{key}" opens {len(lns)} paragraphs (lines '
                f'{", ".join(map(str, lns))}); vary the sentence, not the '
                "entity name", key)

    run = 0
    for ln, n in sent_lengths:
        run = run + 1 if n <= 6 else 0
        if run == 3:
            add("WARNING", "SHORT-RUN", ln,
                "three sentences in a row of six words or fewer")

    for i in range(len(sent_lengths) - 3):
        window = [n for _, n in sent_lengths[i:i + 4]]
        if max(window) - min(window) <= 3 and min(window) >= 9:
            add("WARNING", "MONOTONY", sent_lengths[i][0],
                f"four sentences in a row of similar length {window}")
            break

    # synonym cycling
    for cluster in SYNONYM_CLUSTERS:
        used = [t for t in cluster if re.search(r"\b" + t + r"s?\b", low_nq)]
        if len(used) >= 3:
            add("WARNING", "SYNONYM", 0,
                f"possible name rotation: {', '.join(used)}; one entity, "
                "one name", ", ".join(used))

    # ---- distributional metrics (always reported)
    nums = [n for _, n in sent_lengths]
    metrics = {"MODE": "memo" if memo else "story",
               "WORDS": len(body_words_all), "SENTENCES": len(nums),
               "PARAGRAPHS": len(para_words)}
    if len(nums) >= MIN_SENTENCES_FOR_CV:
        c = cv(nums)
        z = zigzag(nums)
        metrics["CV-SENT"] = round(c, 2)
        metrics["ZIGZAG"] = round(z, 2)
        if c < SENT_CV_FAIL:
            add("VIOLATION", "CV-SENT", 0,
                f"sentence-length variation {c:.2f}, under {SENT_CV_FAIL}; "
                "every sentence is the same size. Combine two related facts "
                "into one longer sentence and give a deadline its own "
                "short one")
        elif c < SENT_CV_WARN:
            add("WARNING", "CV-SENT", 0,
                f"sentence-length variation {c:.2f}, under {SENT_CV_WARN}; "
                "reads even. Read it aloud")
        if z >= ZIGZAG_MAX:
            add("WARNING", "ZIGZAG", 0,
                f"sentence lengths alternate {z:.0%} of the time; let two "
                "sentences of similar length sit together")
    if len(para_words) >= MIN_PARAGRAPHS_FOR_CV:
        pc = cv(para_words)
        metrics["CV-PARA"] = round(pc, 2)
        if pc < PARA_CV_MIN:
            add("WARNING", "CV-PARA", 0,
                f"paragraph-length variation {pc:.2f}, under {PARA_CV_MIN}; "
                "every paragraph is the same size")
    if nums and not any(n <= 8 for n in nums):
        add("WARNING", "NO-SHORT", 0,
            "no sentence of eight words or fewer; a date or a deadline "
            "often reads best alone")

    if len(body_words_all) >= MIN_WORDS_FOR_NGRAM:
        bq = words(strip_quotes(" ".join(p for _, p in body_paras)))
        grams = collections.Counter(tuple(bq[i:i + NGRAM_N])
                                    for i in range(len(bq) - NGRAM_N + 1))
        reps = sorted(((g, c) for g, c in grams.items() if c > 1),
                      key=lambda kv: (-kv[1], kv[0]))
        for g, c in reps[:3]:
            add("WARNING", "REPEAT", 0,
                f"{NGRAM_N} words repeat {c} times: \"{' '.join(g)}\"",
                " ".join(g))

    # ---- public CLAIMS AND SOURCES: claim by claim, in story order
    def content(t):
        return {w for w in words(t) if w not in STOP and len(w) > 2}

    pub_claims = []
    if appendix < editor:
        blocks = list(paragraphs(lines, appendix + 1, editor))
        for ln, block in blocks:
            raw = []
            k = ln - 1
            while k < editor and lines[k].strip():
                raw.append(lines[k].strip())
                k += 1
            if not raw[0].lower().startswith("claim:"):
                add("VIOLATION", "SOURCES", ln,
                    'public entry does not start with "Claim:"', raw[0])
                continue
            for r in raw[1:]:
                if not r.lower().startswith(PUBLIC_LABELS):
                    add("VIOLATION", "SOURCES", ln,
                        "public entry line must be Claim, Source (...), "
                        "Math, or First reported by; editor detail goes "
                        "in the CLAIM CHECK", r)
            srcs = [r for r in raw if r.lower().startswith("source (")]
            if not srcs:
                add("VIOLATION", "SOURCES", ln,
                    'claim has no "Source (primary record):" or "Source '
                    '(secondary source):" line', raw[0])
            for s in srcs:
                if not PUBLIC_SOURCE.match(s):
                    add("VIOLATION", "SOURCES", ln,
                        'source label must read "Source (primary record):" '
                        'or "Source (secondary source):"', s)
                elif not URL_RE.search(s) and \
                        "no public url" not in s.lower() and \
                        "url not verified" not in s.lower():
                    if DOMAIN.search(s):
                        add("WARNING", "SOURCES", ln,
                            "bare web address; write it in full starting "
                            "https:// so it becomes a link when pasted", s)
                    else:
                        add("WARNING", "SOURCES", ln,
                            "source gives no address a reader can follow; "
                            'add the URL, or say "no public URL"', s)
            pub_claims.append((ln, raw[0][6:].strip()))
        if not pub_claims and not publish_only:
            add("VIOLATION", "SOURCES", appendix + 1,
                "CLAIMS AND SOURCES has no entries")
    metrics["CLAIMS"] = len(pub_claims)

    # order: each claim's place in the story must not move backward
    para_sets = [content(p) for _, p in body_paras]
    positions = []
    for ln, claim in pub_claims:
        c = content(claim)
        if not c or not para_sets:
            continue
        scores = [len(c & p) / len(c) for p in para_sets]
        best = max(range(len(scores)), key=scores.__getitem__)
        if scores[best] >= 0.5:
            positions.append((ln, best, claim))
        else:
            add("VIOLATION", "CLAIM-MATCH", ln,
                "claim does not match the story's words; write the Claim "
                "line as the story states it, so readers can find it and "
                "the order check can place it", claim)
    # the longest run already in story order stays; every other entry
    # is the one out of place
    n = len(positions)
    best_len, prev = [1] * n, [-1] * n
    for i in range(n):
        for j in range(i):
            if positions[j][1] <= positions[i][1] and \
                    best_len[j] + 1 > best_len[i]:
                best_len[i], prev[i] = best_len[j] + 1, j
    keep = set()
    if n:
        k = max(range(n), key=best_len.__getitem__)
        while k != -1:
            keep.add(k)
            k = prev[k]
    for i, (ln, pos, claim) in enumerate(positions):
        if i not in keep:
            add("VIOLATION", "SOURCE-FLOW", ln,
                f"claim is out of order: it matches story paragraph "
                f"{pos + 1}; move this entry to where that paragraph's "
                "claims are listed", claim)
    metrics["CLAIMS-PLACED"] = f"{len(positions)}/{len(pub_claims)}"

    # coverage: a body paragraph with a figure, date, or quote needs a claim
    if pub_claims:
        covered = {pos for _, pos, _ in positions}
        claim_sets = [content(c) for _, c in pub_claims]
        for idx, (ln, para) in enumerate(body_paras):
            if idx in covered:
                continue
            if not FACT_SIGNAL.search(para):
                continue
            p = para_sets[idx]
            if p and max((len(c & p) / len(c) for c in claim_sets if c),
                         default=0) >= 0.5:
                continue
            add("VIOLATION", "UNSOURCED", ln,
                "paragraph states a checkable fact (a figure, date, quote, "
                "or attributed statement) but no claim in CLAIMS AND "
                "SOURCES matches it; add one in the story's words, or fix a "
                "nearby claim that was reworded", para)

    # ---- EDITOR FILE: CLAIM CHECK entries, one per public claim
    if editor < len(lines):
        def section_range(name):
            if name not in tail_upper:
                return None
            s = editor + tail_upper.index(name) + 1
            e = len(lines)
            for j in range(s, len(lines)):
                if heading(lines[j]) in REQUIRED_SECTIONS + \
                        OPTIONAL_SECTIONS:
                    e = j
                    break
            return s, e

        rng = section_range("CLAIM CHECK")
        if rng:
            checks = list(paragraphs(lines, *rng))
            nums = []
            for ln, block in checks:
                m = re.match(r"\s*Check (\d+):", block)
                if not m:
                    add("VIOLATION", "CLAIM-CHECK", ln,
                        'CLAIM CHECK entry must start "Check N:"', block)
                    continue
                nums.append(int(m.group(1)))
                raw = []
                k = ln - 1
                while k < rng[1] and lines[k].strip():
                    raw.append(lines[k].strip())
                    k += 1
                labels = {r.split(":", 1)[0].strip().lower()
                          for r in raw if ":" in r}
                for need in ("establishes", "status", "accessed"):
                    if need not in labels:
                        add("VIOLATION", "CLAIM-CHECK", ln,
                            f'Check {m.group(1)} is missing a '
                            f'"{need.title()}:" line', block)
                st = [r for r in raw if r.lower().startswith("status:")]
                if st and not STATUS_OK.match(st[0]):
                    add("VIOLATION", "CLAIM-CHECK", ln,
                        'Status must begin "primary record" or "secondary '
                        'source"', st[0])
                acc = " ".join(r for r in raw
                               if r.lower().startswith("accessed"))
                if re.search(r"\b20\d\d-\d\d-\d\d\b", acc):
                    add("VIOLATION", "CLAIM-CHECK", ln,
                        'write the access date as "Sept. 28, 2026"', acc)
            if nums != list(range(1, len(nums) + 1)):
                add("VIOLATION", "CLAIM-CHECK", rng[0],
                    "Check numbers must run 1, 2, 3 in order, matching the "
                    "public claims", " ".join(map(str, nums)))
            if len(nums) != len(pub_claims):
                add("VIOLATION", "CLAIM-CHECK", rng[0],
                    f"{len(nums)} checks for {len(pub_claims)} public "
                    "claims; every public claim gets one check, in order")

        rng = section_range("AUDIT LOG")
        if rng:
            log = "\n".join(lines[rng[0]:rng[1]])
            missing = [str(n) for n in range(AUDIT_PASSES)
                       if not re.search(r"(?m)^\s*Pass %d:" % n, log)]
            if missing:
                add("VIOLATION", "AUDIT-LOG", rng[0],
                    "AUDIT LOG has no evidence line for Pass "
                    + ", ".join(missing) + ". A pass with no line did not run")
            p13 = re.search(r"(?m)^\s*Pass 13:.*$", log)
            if p13 and not re.search(r"CV-SENT|n/a", p13.group(0)):
                add("VIOLATION", "AUDIT-LOG", rng[0],
                    "Pass 13 line must report CV-SENT from both runs, or "
                    '"n/a" when the story is under the metric threshold',
                    p13.group(0))

    # ---- MEETING LEDGER: every newsworthy item from the meeting, one status
    metrics["MODE"] = metrics["MODE"] + ("+meeting" if meeting else "")
    if meeting and editor < len(lines) and "MEETING LEDGER" in tail_upper:
        s0 = editor + tail_upper.index("MEETING LEDGER") + 1
        e0 = len(lines)
        for j in range(s0, len(lines)):
            if heading(lines[j]) in REQUIRED_SECTIONS + OPTIONAL_SECTIONS:
                e0 = j
                break
        items = []
        for ln, block in paragraphs(lines, s0, e0):
            raw = []
            k = ln - 1
            while k < e0 and lines[k].strip():
                raw.append(lines[k].strip())
                k += 1
            m = re.match(r"Item (\d+):\s*(.+)$", raw[0])
            if not m:
                add("VIOLATION", "LEDGER", ln,
                    'MEETING LEDGER entry must start "Item N: <the item, '
                    'in the story\'s words>"', raw[0])
                continue
            fields = {}
            for r in raw[1:]:
                if ":" in r:
                    key, val = r.split(":", 1)
                    fields[key.strip().lower()] = val.strip()
            items.append((ln, int(m.group(1)), m.group(2).strip(), fields))
        nums = [n for _, n, _, _ in items]
        if nums != list(range(1, len(nums) + 1)):
            add("VIOLATION", "LEDGER", s0,
                "Item numbers must run 1, 2, 3 in order",
                " ".join(map(str, nums)))
        if not items:
            add("VIOLATION", "LEDGER", s0, "MEETING LEDGER has no items")
        elif len(items) < LEDGER_MIN_ITEMS:
            add("WARNING", "LEDGER-SHORT", s0,
                f"only {len(items)} items in the MEETING LEDGER; list every "
                "vote, motion, staff report with new facts, hearing, public "
                "comment theme, and council or manager announcement")
        body_sets = [(idx, content(p)) for idx, (_, p) in
                     enumerate(body_paras)]
        # which body paragraphs sit under the ALSO AT THE MEETING heading
        under = {idx for idx, (pl, _) in enumerate(body_paras)
                 if roundup is not None and pl - 1 > roundup}
        n_roundup = n_excluded = n_covered = 0
        for ln, n, label, f in items:
            typ = f.get("type", "").lower()
            st = f.get("status", "").lower()
            reason = f.get("reason", "")
            if typ not in LEDGER_TYPES:
                add("VIOLATION", "LEDGER", ln,
                    f"Item {n} needs a Type: line, one of "
                    + ", ".join(sorted(LEDGER_TYPES)), label)
            if st not in LEDGER_STATUS:
                add("VIOLATION", "LEDGER", ln,
                    f"Item {n} needs a Status: line reading lead, roundup, "
                    "or excluded", label)
                continue
            if st == "excluded":
                n_excluded += 1
                if not reason:
                    add("VIOLATION", "LEDGER", ln,
                        f"Item {n} is excluded with no Reason: line; an "
                        "item is never dropped silently", label)
                elif typ in ("vote", "motion") and \
                        not reason.lower().startswith("procedural"):
                    add("VIOLATION", "LEDGER-VOTE", ln,
                        f"Item {n} is a {typ} and is excluded; a vote or "
                        "motion runs in the story unless it is procedural "
                        '(the Reason: line then starts "procedural")', label)
                continue
            n_covered += 1
            if st == "roundup":
                n_roundup += 1
            c = content(label)
            hits = [idx for idx, b in body_sets
                    if c and len(c & b) / len(c) >= 0.5]
            if not hits:
                add("VIOLATION", "LEDGER-MATCH", ln,
                    f"Item {n} is marked {st} but no story paragraph "
                    "matches it; write the item in the story's words, or "
                    "add the item to the story", label)
            elif roundup is not None:
                if st == "lead" and all(h in under for h in hits):
                    add("WARNING", "LEDGER-PLACE", ln,
                        f"Item {n} is marked lead but appears only under "
                        "ALSO AT THE MEETING", label)
                if st == "roundup" and not any(h in under for h in hits):
                    add("WARNING", "LEDGER-PLACE", ln,
                        f"Item {n} is marked roundup but appears only in "
                        "the main story", label)
        if n_roundup and roundup is None:
            add("VIOLATION", "NO-ROUNDUP", s0,
                f"{n_roundup} ledger items are marked roundup but the story "
                "has no ALSO AT THE MEETING heading")
        if items and n_excluded / len(items) > EXCLUDED_SHARE_WARN:
            add("WARNING", "LEDGER-EXCLUDED", s0,
                f"{n_excluded} of {len(items)} meeting items are excluded; "
                "check that each reason would satisfy a reader who was in "
                "the room")
        metrics["LEDGER"] = (f"{len(items)} items, {n_covered} covered, "
                             f"{n_roundup} roundup, {n_excluded} excluded")
    elif meeting and roundup is None:
        add("WARNING", "NO-ROUNDUP", 0,
            "meeting mode with no ALSO AT THE MEETING section; correct only "
            "when the meeting had a single newsworthy item")

    # ---- overlap with supplied source articles
    body_text = " ".join(p for _, p in [headline, dek] + body_paras)
    bw = words(strip_quotes(body_text))
    for name, stext in sources:
        sw = words(stext)
        if len(sw) < 20 or len(bw) < 20:
            continue
        s_run = {tuple(sw[i:i + SOURCE_RUN_WARN])
                 for i in range(len(sw) - SOURCE_RUN_WARN + 1)}
        # every shared run of SOURCE_RUN_WARN+ words, outside quotes
        i = 0
        while i <= len(bw) - SOURCE_RUN_WARN:
            if tuple(bw[i:i + SOURCE_RUN_WARN]) in s_run:
                j = i
                while j <= len(bw) - SOURCE_RUN_WARN and \
                        tuple(bw[j:j + SOURCE_RUN_WARN]) in s_run:
                    j += 1
                length = j - i + SOURCE_RUN_WARN - 1
                phrase = " ".join(bw[i:i + length])
                sev = "VIOLATION" if length >= SOURCE_RUN_FAIL else "WARNING"
                add(sev, "SOURCE-COPY", 0,
                    f"{length} words in a row match {name} outside quotation "
                    "marks; rewrite from your notes", phrase)
                i = j + SOURCE_RUN_WARN - 1
            else:
                i += 1
        s5 = {tuple(sw[i:i + 5]) for i in range(len(sw) - 4)}
        b5 = [tuple(bw[i:i + 5]) for i in range(len(bw) - 4)]
        share = sum(1 for g in b5 if g in s5) / max(1, len(b5))
        metrics[f"SHARE[{name}]"] = round(share, 3)
        if share >= SOURCE_SHARE_FAIL:
            add("VIOLATION", "SOURCE-SHARE", 0,
                f"{share:.0%} of the story's five-word phrases appear in "
                f"{name}; this is a rewrite, not a story")
        # quoted words lifted from the source
        lifted = 0
        for q in quoted_spans(body_text):
            qw = words(q)
            if len(qw) >= 4 and " ".join(qw) in " ".join(sw):
                lifted += len(qw)
        if lifted > QUOTE_BORROW_WARN:
            add("WARNING", "SOURCE-QUOTES", 0,
                f"{lifted} quoted words come from {name}; paraphrase with "
                "credit and keep only the essential excerpt")
        # paragraph order following the source
        sparas = [p for p in re.split(r"\n\s*\n", stext) if len(words(p)) > 8]
        stop = {"the", "a", "an", "of", "to", "in", "and", "on", "for",
                "said", "is", "was", "that", "it", "at", "by", "with", "as",
                "be", "has", "have", "will", "from", "its", "their", "this"}

        def cw(t):
            return {w for w in words(t) if w not in stop and len(w) > 2}
        s_sets = [cw(p) for p in sparas]
        order = []
        for _, p in body_paras:
            b = cw(p)
            if not b or not s_sets:
                continue
            scores = [len(b & s) / len(b | s) if b | s else 0
                      for s in s_sets]
            best = max(range(len(scores)), key=scores.__getitem__)
            if scores[best] >= 0.2:
                order.append(best)
        if len(order) >= 5:
            agree = sum(1 for a, b in zip(order, order[1:]) if b > a)
            ratio = agree / (len(order) - 1)
            metrics[f"ORDER[{name}]"] = round(ratio, 2)
            if ratio >= 0.75:
                add("WARNING", "SOURCE-ORDER", 0,
                    f"{len(order)} paragraphs track {name} and "
                    f"{ratio:.0%} of them follow its order; rebuild the "
                    "outline from your own news question")
        # lede follows source lede
        if body_paras and sparas:
            lb, ls = cw(body_paras[0][1]), cw(sparas[0])
            if lb and ls and len(lb & ls) / len(lb | ls) >= 0.3:
                add("WARNING", "SOURCE-LEDE", body_paras[0][0],
                    f"lede shares most of its key words with the lede of "
                    f"{name}; find your own entry point")

    return out, metrics


# ---------------------------------------------------------------- cli


def main():
    ap = argparse.ArgumentParser(description=VERSION)
    ap.add_argument("draft")
    ap.add_argument("--publish-only", action="store_true",
                    help="the user asked for the publish copy only; no "
                    "EDITOR FILE, and CLAIMS AND SOURCES is checked only if "
                    "present")
    ap.add_argument("--meeting", action="store_true",
                    help="meeting mode: story built from a public meeting; "
                    "requires a MEETING LEDGER in the EDITOR FILE")
    ap.add_argument("--source", action="append", default=[],
                    help="a supplied article to compare against; repeatable")
    args = ap.parse_args()
    try:
        with open(args.draft, encoding="utf-8-sig") as f:
            text = f.read()
        sources = []
        for path in args.source:
            with open(path, encoding="utf-8-sig") as f:
                sources.append((path.rsplit("/", 1)[-1], f.read()))
    except (OSError, UnicodeError) as exc:
        print(f"Cannot read input: {exc}", file=sys.stderr)
        return 2
    publish_only = args.publish_only
    findings, metrics = run_checks(text, sources, publish_only,
                                   args.meeting)
    v = [f for f in findings if f.severity == "VIOLATION"]
    w = [f for f in findings if f.severity == "WARNING"]
    print(VERSION)
    print("METRICS: " + "  ".join(f"{k}={val}" for k, val in metrics.items()))
    if not sources:
        print("SOURCES: none supplied; overlap check did not run")
    for title, group in (("VIOLATIONS (fix all)", v),
                         ("WARNINGS (judge each)", w)):
        print(f"\n== {title}: {len(group)}")
        for f in sorted(group, key=lambda x: (x.line, x.code)):
            loc = f"line {f.line}" if f.line else "whole draft"
            print(f"  [{f.code}] {loc}: {f.message}")
            if f.snippet:
                print(f"      > {f.snippet}")
    if v:
        print(f"\nRESULT: FAIL ({len(v)} violations, {len(w)} warnings)")
    else:
        print(f"\nRESULT: MECHANICAL CHECKS CLEAR ({len(w)} warnings to "
              "judge)")
        print("This is NOT a full pass. Facts, quotes, links, fairness, "
              "credit, and legal risk are unchecked.")
        if publish_only:
            print("Still owed: Passes 1 through 15, each with one evidence "
                  "line, reported in chat after the deliverable.")
        else:
            print("Still owed, each with one evidence line in the AUDIT "
                  "LOG: Passes 1 through 15.")
    return 1 if v else 0


if __name__ == "__main__":
    sys.exit(main())
