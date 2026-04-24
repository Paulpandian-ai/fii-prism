"""Filing chunker.

Strategy:
1. If edgartools gave us section splits (Item 1, Item 1A, MD&A, etc.), use those as the
   primary unit — one chunk per section, further split if the section exceeds the token cap.
2. Otherwise fall back to a token-window split with overlap.

We use tiktoken's cl100k_base encoder as a language-agnostic token counter. Voyage and
Titan don't expose their exact tokenizers, but cl100k is close enough for sizing.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_TOKENS = 1500
DEFAULT_OVERLAP_TOKENS = 150

# tiktoken lazy-downloads encodings from a public blob on first use. In airgapped or
# offline environments that fails, so we fall back to a char-count estimator (≈4 chars
# per token for English prose). When tiktoken is available, we use it for accuracy.
_ENCODER = None


def _get_encoder():
    global _ENCODER
    if _ENCODER is not None:
        return _ENCODER
    try:
        import tiktoken

        _ENCODER = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _ENCODER = False  # sentinel: fell back to heuristic
    return _ENCODER


@dataclass
class Chunk:
    index: int
    section_name: str | None
    text: str
    token_count: int


def count_tokens(text: str) -> int:
    enc = _get_encoder()
    if enc:
        return len(enc.encode(text, disallowed_special=()))
    # Heuristic: ~4 chars per token for English.
    return max(1, len(text) // 4)


def chunk_filing(
    *,
    sections: dict[str, str] | None,
    full_text: str | None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Return ordered chunks. Prefers section-aware splitting when sections exist."""
    chunks: list[Chunk] = []
    idx = 0

    if sections:
        for section_name, section_text in sections.items():
            if not section_text:
                continue
            for piece in _token_window(section_text, max_tokens, overlap_tokens):
                chunks.append(Chunk(idx, section_name, piece, count_tokens(piece)))
                idx += 1
        return chunks

    if full_text:
        for piece in _token_window(full_text, max_tokens, overlap_tokens):
            chunks.append(Chunk(idx, None, piece, count_tokens(piece)))
            idx += 1

    return chunks


def _token_window(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    enc = _get_encoder()
    if enc:
        tokens = enc.encode(text, disallowed_special=())
        if len(tokens) <= max_tokens:
            return [text.strip()] if text.strip() else []
        step = max(1, max_tokens - overlap_tokens)
        pieces: list[str] = []
        i = 0
        while i < len(tokens):
            window = tokens[i : i + max_tokens]
            pieces.append(enc.decode(window).strip())
            if i + max_tokens >= len(tokens):
                break
            i += step
        return [p for p in pieces if p]

    # Heuristic fallback: split on approximate character windows.
    max_chars = max_tokens * 4
    overlap_chars = overlap_tokens * 4
    step = max(1, max_chars - overlap_chars)
    if len(text) <= max_chars:
        return [text.strip()] if text.strip() else []
    pieces = []
    i = 0
    while i < len(text):
        pieces.append(text[i : i + max_chars].strip())
        if i + max_chars >= len(text):
            break
        i += step
    return [p for p in pieces if p]
