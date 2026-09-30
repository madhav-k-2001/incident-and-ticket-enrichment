import re
from typing import List, Dict, Any
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """
    Estimates token count for Gemini embeddings.
    A safe and standard estimation is ~4 characters per token in English / European languages,
    or ~1.3 tokens per whitespace-separated word.
    We take max(len(text) // 4, int(len(text.split()) * 1.3), 1) for a conservative upper bound.
    """
    if not text:
        return 0
    char_est = len(text) // 4
    word_est = int(len(text.split()) * 1.3)
    return max(char_est, word_est, 1)


def split_text_recursive(
    text: str,
    max_chunk_size: int = 1000,
    overlap: int = 150
) -> List[str]:
    """
    Recursively splits text into chunks of at most `max_chunk_size` characters,
    with an overlap of `overlap` characters, respecting semantic boundaries.
    """
    if not text or not text.strip():
        return []

    text = text.strip()
    if len(text) <= max_chunk_size:
        return [text]

    separators = ["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " "]

    def _split(txt: str, seps: List[str]) -> List[str]:
        if not txt:
            return []
        if len(txt) <= max_chunk_size:
            return [txt]
        if not seps:
            # Hard character cut if no separators remain
            chunks = []
            start = 0
            while start < len(txt):
                end = min(start + max_chunk_size, len(txt))
                chunks.append(txt[start:end])
                start += max_chunk_size - overlap
            return chunks

        sep = seps[0]
        remaining_seps = seps[1:]
        parts = txt.split(sep)

        chunks = []
        current_chunk = []
        current_len = 0

        for part in parts:
            part_len = len(part) + (len(sep) if current_chunk else 0)
            if current_len + part_len <= max_chunk_size:
                current_chunk.append(part)
                current_len += part_len
            else:
                if current_chunk:
                    joined = sep.join(current_chunk)
                    chunks.append(joined)
                    # Prepare overlap from end of joined chunk
                    overlap_text = joined[-overlap:] if overlap > 0 else ""
                    current_chunk = [overlap_text, part] if overlap_text else [part]
                    current_len = sum(len(p) for p in current_chunk) + len(sep)
                else:
                    # Single part exceeds max_chunk_size: split with finer separators
                    sub_chunks = _split(part, remaining_seps)
                    chunks.extend(sub_chunks)
                    current_chunk = []
                    current_len = 0

        if current_chunk:
            joined = sep.join(current_chunk).strip()
            if joined:
                chunks.append(joined)

        return chunks

    raw_chunks = _split(text, separators)
    # Clean up any empty chunks
    return [c.strip() for c in raw_chunks if c.strip()]


def chunk_document_sections(
    sections: List[Dict[str, Any]],
    chunk_size: int = None,
    chunk_overlap: int = None
) -> List[Dict[str, Any]]:
    """
    Takes parsed document sections: [{"page_number": int, "text": str}]
    and chunks them into structured chunks with metadata and estimated token counts.
    """
    settings = get_settings()
    size = chunk_size or settings.CHUNK_SIZE
    overlap = chunk_overlap or settings.CHUNK_OVERLAP

    all_chunks = []
    chunk_index = 0

    for sec in sections:
        page_num = sec.get("page_number", 1)
        raw_text = sec.get("text", "")
        text_chunks = split_text_recursive(raw_text, max_chunk_size=size, overlap=overlap)

        # Markdown sections: sub-chunks after the first lose the heading line,
        # so prefix the heading path to keep each chunk self-describing.
        heading = sec.get("heading")
        if heading:
            text_chunks = [
                c if i == 0 else f"[{heading}]\n{c}"
                for i, c in enumerate(text_chunks)
            ]

        for text in text_chunks:
            all_chunks.append({
                "chunk_index": chunk_index,
                "page_number": page_num,
                "content": text,
                "char_count": len(text),
                "estimated_tokens": estimate_tokens(text)
            })
            chunk_index += 1

    return all_chunks


def create_adaptive_batches(
    chunks: List[Dict[str, Any]],
    max_batch_size: int = None,
    target_batch_tokens: int = None
) -> List[List[Dict[str, Any]]]:
    """
    Groups chunks into rate-limit-optimized batches.
    Batches are bounded by both MAX_BATCH_SIZE (default 30) and TARGET_BATCH_TOKENS (default 6000).
    This protects the 30K TPM ceiling while minimizing total API requests (saving the 1K RPD quota).
    """
    settings = get_settings()
    max_size = max_batch_size or settings.MAX_BATCH_SIZE
    target_tokens = target_batch_tokens or settings.TARGET_BATCH_TOKENS

    batches = []
    current_batch = []
    current_tokens = 0

    for chunk in chunks:
        chunk_tokens = chunk.get("estimated_tokens", estimate_tokens(chunk.get("content", "")))

        # If adding this chunk exceeds batch limits and batch is not empty, start new batch
        if current_batch and (len(current_batch) >= max_size or current_tokens + chunk_tokens > target_tokens):
            batches.append(current_batch)
            current_batch = [chunk]
            current_tokens = chunk_tokens
        else:
            current_batch.append(chunk)
            current_tokens += chunk_tokens

    if current_batch:
        batches.append(current_batch)

    return batches
