"""Tokenizer for filename atoms.

Filenames carry a noisy mix of: title text, year, season/episode markers,
quality tokens, edition tokens, release-group tags, episode titles, separator
characters (dots, underscores, double-spaces), and bracketed groups. This
module pulls those atoms out in a deterministic order and returns a
``TokenizedName`` shape that :mod:`plex_renamer.parser.extract` then folds
into a :class:`ParseResult`.

The approach is "peel tokens off, then clean what's left." We do NOT try to
parse the title with a single regex; we strip recognized atoms in order and
the residue becomes the title candidate.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from html import unescape

# --- Token patterns -----------------------------------------------------------

# Bracketed season/episode marker used by Amazon/MAX: [S03.E12], [S03.E12-E13]
_BRACKET_SE_RE = re.compile(
    r"\[\s*S(?P<season>\d{1,3})\s*[.\-_x]\s*E(?P<ep>\d{1,3})"
    r"(?:\s*[\-_]\s*E(?P<ep_end>\d{1,3}))?\s*\]",
    re.IGNORECASE,
)

# Plain S/E marker: S01E26, S01E01-E02, s1e1, 1x12.
_PLAIN_SE_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"S(?P<season>\d{1,3})\s*E(?P<ep>\d{1,3})"
    r"(?:\s*[-_]\s*E?(?P<ep_end>\d{1,3}))?"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# Cross-format season/episode: 1x12, 01x12.
_CROSS_SE_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<season>\d{1,2})x(?P<ep>\d{1,3})"
    r"(?:[-_](?P<ep_end>\d{1,3}))?"
    r"(?![A-Za-z0-9])",
)

# Season-only token inside a longer name: "Season 5", "Season 05".
_SEASON_WORD_RE = re.compile(
    r"(?<![A-Za-z])Season\s+(?P<season>\d{1,3})(?![A-Za-z0-9])", re.IGNORECASE
)

# Year: 1900-2099. Allow surrounding brackets/parens.
_YEAR_RE = re.compile(r"(?<![\d])(?P<year>(?:19|20)\d{2})(?![\d])")

# Date-based: YYYY-MM-DD.
_DATE_RE = re.compile(r"(?<![\d])(?P<date>(?:19|20)\d{2}-\d{2}-\d{2})(?![\d])")

# Multi-part movie markers: pt1, pt2, cd1, disc1, part2.
_PART_MARKER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<part>(?:pt|cd|disc|part)\s*\d+)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# Quality / codec / HDR tokens. Order matters for readability only.
_QUALITY_TOKENS = (
    "2160p",
    "1080p",
    "720p",
    "576p",
    "480p",
    "4k",
    "uhd",
    "hdr",
    "hdr10",
    "hdr10+",
    "dv",
    "dolby",
    "atmos",
    "ddp5.1",
    "ac3",
    "dts",
    "dts-hd",
    "truehd",
    "aac",
    "mp3",
    "flac",
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "xvid",
    "divx",
    "av1",
    "10bit",
    "bluray",
    "blu-ray",
    "brrip",
    "bdrip",
    "webrip",
    "web-dl",
    "webdl",
    "hdtv",
    "dvdrip",
    "remux",
    "proper",
    "repack",
    "extended",
    "uncut",
    "limited",
    "internal",
)

# Edition tokens (curly-brace style after Plex naming): {edition-Director's Cut}
_EDITION_BRACE_RE = re.compile(
    r"\{\s*edition[-=]\s*(?P<edition>[^}]+?)\s*\}",
    re.IGNORECASE,
)

# Edition tokens spotted bare in the filename (common ones).
_BARE_EDITION_TOKENS = (
    "director's cut",
    "directors cut",
    "extended cut",
    "extended edition",
    "theatrical cut",
    "theatrical edition",
    "ultimate edition",
    "special edition",
    "unrated",
    "uncut",
    "remastered",
    "imax",
    "criterion",
)

# Square bracket groups: [Group] / [RG] / [2021]. Year-in-brackets is handled
# by the year regex above, but the bracket itself goes through this matcher
# first so that the contents are inspected then removed cleanly.
_BRACKET_GROUP_RE = re.compile(r"\[(?P<inner>[^\[\]]+)\]")

# Curly-brace anchor tokens: {tmdb-12345}, {imdb-tt12345}.
_ANCHOR_BRACE_RE = re.compile(r"\{(?:tmdb|imdb)[-=][^}]+\}", re.IGNORECASE)

# A run of separators we collapse to single spaces.
_SEPARATOR_RE = re.compile(r"[._]+")

# Whitespace including multiple spaces collapses to a single space.
_MULTISPACE_RE = re.compile(r"\s+")


# --- Tokenized name shape -----------------------------------------------------


@dataclass
class TokenizedName:
    """Result of running the tokenizer on a single filename (no extension).

    ``residue`` is what's left after all recognized tokens are stripped; it is
    the candidate for title / episode-title once split on the S/E or year
    boundary.
    """

    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    year: int | None = None
    date: str | None = None
    quality_tokens: list[str] = field(default_factory=list)
    edition_tokens: list[str] = field(default_factory=list)
    group_tag: str | None = None
    part_marker: str | None = None
    se_span: tuple[int, int] | None = None
    """(start, end) span of the S/E marker in the residue, if found."""

    year_span: tuple[int, int] | None = None
    """(start, end) span of the year token in the residue, if found."""

    residue: str = ""
    """The cleaned filename with recognized atoms removed and dots/underscores
    converted to spaces. Multiple spaces preserved as a single space."""


# --- Public entry points ------------------------------------------------------


def normalize_unicode(text: str) -> str:
    """Normalize text to NFC and decode common HTML entities."""
    # HTML entities first (some sources emit &#8211; for an en-dash).
    decoded = unescape(text)
    # NFC keeps composed characters as single codepoints; NFD inputs become NFC.
    return unicodedata.normalize("NFC", decoded)


def tokenize(stem: str) -> TokenizedName:
    """Tokenize a filename stem (no extension) into a :class:`TokenizedName`.

    The stem is processed in this order:

    1. Unicode normalize and decode HTML entities.
    2. Strip anchor braces (``{tmdb-...}``) and Plex edition braces.
    3. Extract bracketed groups (``[Group]``, ``[2021]``, ``[S01.E01]``).
    4. Extract S/E markers (bracketed, plain, cross-format).
    5. Extract season-only word marker (``Season 5``).
    6. Extract year and date.
    7. Extract part marker (``pt1`` / ``cd2``).
    8. Extract quality and bare edition tokens.
    9. Normalize separators and collapse whitespace; the result is the residue.
    """
    text = normalize_unicode(stem)
    result = TokenizedName()

    # 2. Anchor braces — strip silently; we never preserve them through parse.
    text = _ANCHOR_BRACE_RE.sub(" ", text)

    # 2b. Edition braces — preserve the edition label.
    def _edition_brace_sub(m: re.Match[str]) -> str:
        label = m.group("edition").strip()
        if label and label not in result.edition_tokens:
            result.edition_tokens.append(label)
        return " "

    text = _EDITION_BRACE_RE.sub(_edition_brace_sub, text)

    # 3. Bracket groups — inspect, classify, remove.
    text = _consume_bracket_groups(text, result)

    # 4a. Plain S/E first (it's the most specific). _consume_bracket_groups
    #     already handled bracketed [Sxx.Eyy].
    text = _consume_plain_se(text, result)

    # 4b. Cross-format SxxEyy is handled above; do 1x12 form here.
    if result.season is None:
        text = _consume_cross_se(text, result)

    # 5. Season-only word ("Season 5") if we don't already have a season.
    if result.season is None:
        text = _consume_season_word(text, result)

    # 6. Year / date.
    text = _consume_date(text, result)
    text = _consume_year(text, result)

    # 7. Part marker.
    text = _consume_part_marker(text, result)

    # 8. Quality + bare edition tokens.
    text = _consume_quality_and_edition(text, result)

    # 9. Normalize separators and whitespace; assign residue.
    text = _SEPARATOR_RE.sub(" ", text)
    text = _MULTISPACE_RE.sub(" ", text).strip(" -_")
    result.residue = text
    return result


# --- Internal helpers ---------------------------------------------------------


def _consume_bracket_groups(text: str, result: TokenizedName) -> str:
    """Walk every ``[...]`` group, classify it, and remove it from ``text``.

    Bracketed S/E (``[S03.E12]`` etc.) is detected here directly so the
    sentinel ``⟦SE⟧`` lands in the residue at the right spot — that's how
    :func:`_split_title` later separates the title from the episode title.
    """
    pieces: list[str] = []
    cursor = 0
    for match in _BRACKET_GROUP_RE.finditer(text):
        pieces.append(text[cursor : match.start()])
        inner = match.group("inner").strip()

        # Bracketed S/E: leave a sentinel so split_title can find the
        # title/episode-title boundary.
        se_match = _BRACKET_SE_RE.fullmatch(f"[{inner}]")
        if se_match:
            result.season = int(se_match.group("season"))
            result.episode = int(se_match.group("ep"))
            if se_match.group("ep_end"):
                result.episode_end = int(se_match.group("ep_end"))
            placeholder_start = sum(len(p) for p in pieces)
            pieces.append(" ⟦SE⟧ ")
            result.se_span = (placeholder_start, placeholder_start + len(" ⟦SE⟧ "))
            cursor = match.end()
            continue

        classified = _classify_bracket_inner(inner, result)
        if classified:
            pieces.append(" ")
        else:
            pieces.append(f" {inner} ")
        cursor = match.end()
    pieces.append(text[cursor:])
    return "".join(pieces)


def _classify_bracket_inner(inner: str, result: TokenizedName) -> bool:
    """Inspect a bracket's contents and pull out anchors, S/E, year, group.

    Returns True if the bracket was consumed (recognized), False if we want to
    keep the inner text floating in the residue.

    Note: the ``[Sxx.Eyy]`` shape is handled directly in
    :func:`_consume_bracket_groups` so it can leave a ``⟦SE⟧`` sentinel in
    the residue for the title/episode-title split.
    """

    # Year in brackets like [2021].
    year_match = _YEAR_RE.fullmatch(inner)
    if year_match:
        result.year = int(year_match.group("year"))
        return True

    # Group tag: a short alphanumeric (with possible dash/dot) sequence.
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\- ]{0,30}", inner) and not _looks_like_title(inner):
        # Only first bracket wins as group_tag; later ones we still strip.
        if result.group_tag is None:
            result.group_tag = inner.strip()
        return True

    return False


def _looks_like_title(text: str) -> bool:
    """Heuristic: contains spaces or multiple words, so probably a title not a group tag."""
    cleaned = text.strip()
    return " " in cleaned and len(cleaned.split()) >= 3


def _consume_plain_se(text: str, result: TokenizedName) -> str:
    """Pull S01E01 / S01E01-E02 / S1E1 forms out of ``text``."""
    match = _PLAIN_SE_RE.search(text)
    if not match:
        return text
    if result.season is None:
        result.season = int(match.group("season"))
        result.episode = int(match.group("ep"))
        if match.group("ep_end"):
            result.episode_end = int(match.group("ep_end"))
    result.se_span = (match.start(), match.end())
    return text[: match.start()] + " ⟦SE⟧ " + text[match.end() :]


def _consume_cross_se(text: str, result: TokenizedName) -> str:
    """Pull 1x12 / 01x12 forms out of ``text``."""
    match = _CROSS_SE_RE.search(text)
    if not match:
        return text
    season = int(match.group("season"))
    # 1x12 form: season must be plausible (<= 99); episode any range.
    if season > 99:
        return text
    result.season = season
    result.episode = int(match.group("ep"))
    if match.group("ep_end"):
        result.episode_end = int(match.group("ep_end"))
    result.se_span = (match.start(), match.end())
    return text[: match.start()] + " ⟦SE⟧ " + text[match.end() :]


def _consume_season_word(text: str, result: TokenizedName) -> str:
    """Pull ``Season 5`` markers out (used by flat Doctor Who Classic shape)."""
    match = _SEASON_WORD_RE.search(text)
    if not match:
        return text
    result.season = int(match.group("season"))
    return text[: match.start()] + " " + text[match.end() :]


def _consume_year(text: str, result: TokenizedName) -> str:
    """Pull a four-digit year out, preferring the last year that appears.

    The last year wins because release groups sometimes prepend their own
    project year before the title (e.g. ``2010.Inception.2010.1080p`` is rare
    but we still want 2010 for the movie).
    """
    matches = list(_YEAR_RE.finditer(text))
    if not matches:
        return text
    last = matches[-1]
    result.year = int(last.group("year"))
    result.year_span = (last.start(), last.end())
    # Strip the year and any wrapping ()/[] punctuation around it.
    start, end = last.start(), last.end()
    # Eat surrounding paren or bracket if present.
    if start > 0 and text[start - 1] in "([":
        start -= 1
    if end < len(text) and text[end] in ")]":
        end += 1
    return text[:start] + " " + text[end:]


def _consume_date(text: str, result: TokenizedName) -> str:
    """Pull a YYYY-MM-DD date out.

    Sets ``date`` and, when ``year`` is still unset, lifts the year off the
    date. The date is removed from the residue so :func:`_consume_year` does
    not double-extract it.
    """
    match = _DATE_RE.search(text)
    if not match:
        return text
    result.date = match.group("date")
    if result.year is None:
        result.year = int(match.group("date")[:4])
    return text[: match.start()] + " " + text[match.end() :]


def _consume_part_marker(text: str, result: TokenizedName) -> str:
    """Pull ``pt1`` / ``cd2`` / ``disc1`` markers out."""
    match = _PART_MARKER_RE.search(text)
    if not match:
        return text
    raw = match.group("part").strip().lower().replace(" ", "")
    result.part_marker = raw
    return text[: match.start()] + " " + text[match.end() :]


def _consume_quality_and_edition(text: str, result: TokenizedName) -> str:
    """Strip quality and bare edition tokens; preserve them in the result."""
    # Lower-case the text once for matching. We rebuild from the original text
    # so casing of the title is preserved.
    lower = text.lower()
    # We process longest tokens first so "blu-ray" wins over "ray".
    tokens_sorted = sorted(_QUALITY_TOKENS, key=len, reverse=True)
    spans_to_remove: list[tuple[int, int]] = []
    for tok in tokens_sorted:
        for match in _whole_token_finditer(lower, tok):
            spans_to_remove.append(match)
            if tok not in result.quality_tokens:
                result.quality_tokens.append(tok)

    for edition in _BARE_EDITION_TOKENS:
        for match in _whole_token_finditer(lower, edition):
            spans_to_remove.append(match)
            label = _titlecase_edition(edition)
            if label not in result.edition_tokens:
                result.edition_tokens.append(label)

    if not spans_to_remove:
        return text

    # Apply removals back-to-front so earlier offsets stay valid.
    spans_to_remove.sort(reverse=True)
    out = text
    for start, end in spans_to_remove:
        out = out[:start] + " " + out[end:]
    return out


def _whole_token_finditer(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Find every whole-token occurrence of ``needle`` in ``haystack``.

    A whole token is bounded by non-alphanumeric characters on both sides
    (or string edges). The needle may itself contain non-alphanumerics
    (``blu-ray``, ``ddp5.1``); we only require external boundaries.
    """
    # Escape the needle for regex; require boundary chars or edges around it.
    pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])")
    return [(m.start(), m.end()) for m in pat.finditer(haystack)]


def _titlecase_edition(label: str) -> str:
    """Render a bare edition token in titlecase, preserving punctuation."""
    # Special-case "director's cut" → "Director's Cut".
    parts = label.split(" ")
    out: list[str] = []
    for part in parts:
        if "'" in part:
            head, _, tail = part.partition("'")
            out.append(head.capitalize() + "'" + tail.capitalize())
        else:
            out.append(part.capitalize())
    return " ".join(out)
