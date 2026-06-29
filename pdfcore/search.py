"""Find (and helpers for replace) over a page's editable spans.

Pure and Qt-free. Search is span-scoped: a query is matched *within* individual
spans (the common case — a word, number, or phrase inside one run). Matches that
straddle two spans are not found; that keeps replace able to reuse the proven
span-level edit path unchanged. This limitation is intentional for now.
"""

from __future__ import annotations

from dataclasses import dataclass

from .blocks import Span


@dataclass(frozen=True)
class Match:
    """A query hit inside a single span: ``span.text[start:end]`` is the match."""
    span: Span
    start: int
    end: int


def find_spans(spans: list[Span], query: str, case_sensitive: bool = False) -> list[Match]:
    """All occurrences of ``query`` within each span, in span order."""
    if not query:
        return []
    needle = query if case_sensitive else query.lower()
    out: list[Match] = []
    for s in spans:
        hay = s.text if case_sensitive else s.text.lower()
        i = hay.find(needle)
        while i >= 0:
            out.append(Match(s, i, i + len(query)))
            i = hay.find(needle, i + len(query))
    return out


def count_occurrences(text: str, query: str, case_sensitive: bool = False) -> int:
    if not query:
        return 0
    hay = text if case_sensitive else text.lower()
    needle = query if case_sensitive else query.lower()
    return hay.count(needle)


def replace_occurrences(text: str, query: str, replacement: str,
                        case_sensitive: bool = False) -> str:
    """Replace every ``query`` in ``text`` with ``replacement``.

    Case-insensitive mode preserves the surrounding text and replaces matched
    regions regardless of their case.
    """
    if not query:
        return text
    if case_sensitive:
        return text.replace(query, replacement)
    out = []
    low = text.lower()
    needle = query.lower()
    i = 0
    while i < len(text):
        j = low.find(needle, i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        out.append(replacement)
        i = j + len(query)
    return "".join(out)
