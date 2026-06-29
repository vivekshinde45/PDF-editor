from __future__ import annotations

from pdfcore.blocks import Span
from pdfcore.search import count_occurrences, find_spans, replace_occurrences


def _span(text: str) -> Span:
    return Span(text=text, font_name="helv", size=12.0, color=(0, 0, 0),
                bbox=(0, 0, 10, 10), origin=(0, 10))


def test_find_locates_all_occurrences():
    spans = [_span("total 12345"), _span("ref 12345 again")]
    hits = find_spans(spans, "12345")
    assert len(hits) == 2
    assert all(s.span.text[s.start:s.end] == "12345" for s in hits)


def test_find_case_insensitive_by_default():
    hits = find_spans([_span("Hello WORLD")], "world")
    assert len(hits) == 1


def test_find_case_sensitive_when_requested():
    assert find_spans([_span("Hello WORLD")], "world", case_sensitive=True) == []
    assert len(find_spans([_span("Hello world")], "world", case_sensitive=True)) == 1


def test_find_empty_query_returns_nothing():
    assert find_spans([_span("anything")], "") == []


def test_count_occurrences():
    assert count_occurrences("a-a-A", "a") == 3  # case-insensitive: matches the A too
    assert count_occurrences("a-a-A", "a", case_sensitive=True) == 2
    assert count_occurrences("aAaA", "a") == 4


def test_replace_occurrences_case_insensitive_preserves_surrounding():
    assert replace_occurrences("The CAT sat", "cat", "dog") == "The dog sat"
    assert replace_occurrences("aAa", "a", "x") == "xxx"


def test_replace_occurrences_case_sensitive():
    assert replace_occurrences("aAa", "a", "x", case_sensitive=True) == "xAx"
