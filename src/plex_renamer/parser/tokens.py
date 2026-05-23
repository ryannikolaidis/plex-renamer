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
#
# Curated to avoid eating real English words from movie titles. We do NOT
# include: extended, limited, internal, uncut, proper, remux. Those words
# appear as legitimate title tokens often enough that the false-positive cost
# dominates the recall benefit on release-scene names. ``uncut`` and
# ``extended`` are still recognized as edition tokens via the bare-edition
# list below — that path is fine because edition extraction preserves the
# token rather than silently consuming it.
_QUALITY_TOKENS = (
    "2160p",
    "1080p",
    "720p",
    "576p",
    "480p",
    "4k",
    "8k",
    "uhd",
    "hdr",
    "hdr10",
    "hdr10+",
    "dv",
    "dolbyvision",
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
    "repack",
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

    year_at_stem_start: bool = False
    """True if the original stem begins with a year-shaped 4-digit run.

    Used by the "year-as-title" recovery in :func:`extract.parse_file` so
    titles like ``1984.mp4`` and ``2001 A Space Odyssey.mp4`` don't lose
    the year to the year-extraction pass."""

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
    result.year_at_stem_start = bool(re.match(r"\s*(?:19|20)\d{2}(?![\d])", text))

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
    text = _collapse_dangling_separators(text)

    # 6. Year / date. Collapse separators between passes so a date followed
    # by a stripped year doesn't leave a bare ``- - `` in the residue.
    text = _consume_date(text, result)
    text = _collapse_dangling_separators(text)
    text = _consume_year(text, result)
    text = _collapse_dangling_separators(text)

    # 7. Part marker.
    text = _consume_part_marker(text, result)
    text = _collapse_dangling_separators(text)

    # 8. Quality + bare edition tokens.
    text = _consume_quality_and_edition(text, result)

    # 8b. Trailing scene-style ``-Group`` suffix on the residue: when at least
    # one quality token has been peeled, a trailing ``-<Word>`` is a group tag
    # rather than part of the title. Without a quality context (no scene-style
    # peel happened), a trailing dash-suffix is almost certainly title content
    # (``Foo-Bar.mkv``) and we leave it alone. MUST run BEFORE the dangling-
    # separator collapse, otherwise the dash between the title and the group
    # would already have been folded into the title.
    text = _consume_trailing_group_suffix(text, result)
    text = _collapse_dangling_separators(text)

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

    Classification order matters: quality and edition checks come BEFORE the
    group-tag fallback so ``[1080p]Title.mkv`` does not record ``1080p`` as a
    group tag, and ``[Director Cut]`` does not lose its edition classification
    to the short-token group bucket.
    """

    # Year in brackets like [2021].
    year_match = _YEAR_RE.fullmatch(inner)
    if year_match:
        result.year = int(year_match.group("year"))
        return True

    inner_clean = inner.strip()
    inner_lower = inner_clean.lower()

    # Quality token in brackets: [1080p], [HDR], [x264].
    if inner_lower in {tok.lower() for tok in _QUALITY_TOKENS}:
        if inner_lower not in result.quality_tokens:
            result.quality_tokens.append(inner_lower)
        return True

    # Bare edition phrase in brackets: [Director Cut], [Theatrical Cut].
    if inner_lower in {edition.lower() for edition in _BARE_EDITION_TOKENS}:
        label = _titlecase_edition(inner_lower)
        if label not in result.edition_tokens:
            result.edition_tokens.append(label)
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
    """Pull 1x12 / 01x12 forms out of ``text``.

    The ``<N>x<NN>`` pattern is too generic to match unconditionally:
    ``Some Movie 1x12.mkv`` would otherwise be classified as TV with
    season=1/episode=12, and resolution-shaped phrases like ``Foo 4x4.mkv``
    would also bite. We require:

    * The season number is ``<= 50`` (most shows don't exceed this).
    * The marker is followed by either end-of-name, or a separator and then
      a residue that doesn't look like the rest of an English title — in
      practice we require either end-of-name OR a separator-and-rest. A
      separator-and-rest matches TV (``Show - 1x12 - Title``); end-of-name
      matches both TV (``show.1x12.mkv``) and the false-positive
      (``Some Movie 1x12.mkv``).

    The practical disambiguator is the leading context: we only treat the
    marker as TV when it sits at a separator boundary AND is followed by
    either end-of-name or a separator+residue. ``Some Movie 1x12`` has the
    leading separator but is followed by end-of-name; that's still TV-shaped
    under this rule. To eliminate that case we require the marker to either
    sit at the end with no whitespace-padded preamble, OR be followed by a
    title separator pattern.
    """
    match = _CROSS_SE_RE.search(text)
    if not match:
        return text
    season = int(match.group("season"))
    if season > 50:
        return text
    start, end = match.start(), match.end()
    trailing_raw = text[end:]
    leading_raw = text[:start]
    # A strong separator is dash, dot, or underscore — the explicit
    # token-boundary markers a TV-shape filename uses. A bare space alone
    # is too weak: ``Some Movie 1x12`` ends with a space-and-marker but is
    # NOT a TV file.
    has_leading_strong_sep = bool(re.search(r"[\-_.]\s*$", leading_raw))
    has_trailing_strong_sep = bool(re.match(r"\s*[\-_.]\s*\S", trailing_raw))
    # Allow plain TV shape "1x12" as the whole stem (no leading title at all).
    is_bare_marker = leading_raw.strip() == "" and trailing_raw.strip() == ""
    if not (is_bare_marker or has_leading_strong_sep or has_trailing_strong_sep):
        return text
    result.season = season
    result.episode = int(match.group("ep"))
    if match.group("ep_end"):
        result.episode_end = int(match.group("ep_end"))
    result.se_span = (start, end)
    return text[:start] + " ⟦SE⟧ " + text[end:]


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

    # Dedupe by (start, end) before applying removal. A token that lives in
    # both _QUALITY_TOKENS and _BARE_EDITION_TOKENS (or a future overlap of
    # the same span coming from any pair of lists) would otherwise register
    # twice on the removal list and double-cut the residue when applied
    # back-to-front. This is a robustness fix even after the lists are
    # disjoint, because future additions could recreate the overlap.
    spans_to_remove = sorted(set(spans_to_remove), reverse=True)
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


def _collapse_dangling_separators(text: str) -> str:
    """Collapse runs of dashes/dots/underscores/whitespace into single spaces.

    When the token peeler removes a date / year / S/E span the residue often
    keeps the dashes that surrounded it (e.g. ``The Daily Show -  - Guest
    Episode``). Without a normalization pass between extractions those
    dangling separators bleed into the title. We keep the operation
    intentionally narrow: it only touches consecutive separator characters
    and trims edges; it never reorders characters or drops content tokens.
    """
    # Repeated runs of separator chars (with optional whitespace between
    # them) collapse to a single space.
    collapsed = re.sub(r"(?:[\s\-_.]){2,}", " ", text)
    # Trim trailing/leading separators.
    return collapsed.strip(" -_.\t")


def _consume_trailing_group_suffix(text: str, result: TokenizedName) -> str:
    """Detect and peel a trailing ``-<Word>`` scene-style group suffix.

    Scene release names commonly end with ``-Group`` after the quality block
    (e.g. ``The.Lighthouse.2019.HDR.HEVC-Group``). After quality tokens have
    been stripped, the residue ends with a bare ``- Group``. We peel it only
    when at least one quality token has already been recorded: without a
    quality context, a trailing dash-suffix on a residue is almost certainly
    title content (``Foo-Bar.mkv``) and we leave it alone.

    The first existing group_tag wins; if a leading ``[RG]`` already filled
    ``group_tag``, the trailing word is still removed from the residue but
    the group_tag value is not overwritten.
    """
    if not result.quality_tokens:
        return text

    match = re.search(r"(?:\s*[-_]\s*)([A-Za-z][A-Za-z0-9]{1,30})\s*$", text)
    if not match:
        return text
    candidate = match.group(1)
    # Don't peel a trailing word that is itself a recognized token.
    candidate_lower = candidate.lower()
    if candidate_lower in {tok.lower() for tok in _QUALITY_TOKENS}:
        return text
    if candidate_lower in {edition.lower() for edition in _BARE_EDITION_TOKENS}:
        return text
    if result.group_tag is None:
        result.group_tag = candidate
    return text[: match.start()].rstrip(" -_.")


def _titlecase_edition(label: str) -> str:
    """Render a bare edition token in titlecase, preserving punctuation.

    The possessive ``'s`` tail stays lowercase so ``director's cut`` becomes
    ``Director's Cut`` rather than ``Director'S Cut``.
    """
    parts = label.split(" ")
    out: list[str] = []
    for part in parts:
        if "'" in part:
            head, _, tail = part.partition("'")
            # Possessive tail (single letter) stays lowercase. Longer tails
            # get title-cased like the head.
            tail_render = tail.lower() if len(tail) <= 1 else tail.capitalize()
            out.append(head.capitalize() + "'" + tail_render)
        else:
            out.append(part.capitalize())
    return " ".join(out)
