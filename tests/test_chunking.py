"""Limites que impedem janelas sem avanço na ingestão."""

import pytest

from app.ai.chunking import chunk_text


@pytest.mark.parametrize(
    "size,overlap", [(0, 0), (-1, 0), (10, -1), (10, 10), (10, 11)]
)
def test_invalid_window_rejected(size, overlap):
    with pytest.raises(ValueError):
        chunk_text("source", size, overlap)


def test_sentence_cut_always_advances_even_with_large_overlap():
    text = " ".join("fato." for _ in range(500))
    parts = chunk_text(text, max_chars=80, overlap=75)
    assert 1 < len(parts) < len(text)
    assert all(0 < len(part) <= 80 for part in parts)
    assert parts[-1].endswith("fato.")
