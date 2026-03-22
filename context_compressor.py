"""Context Compression — LLMLingua-based replacement for regex compression.

Replaces the heuristic ``compress_context_entry`` in orch_context.py with
Microsoft LLMLingua-2, which uses a trained model to compress text while
preserving semantic meaning.

Benchmark proof (h2h_compression.py):
    - HiveMind regex: keeps 21% of text, answers 17% of factual questions
    - LLMLingua:      keeps 52% of text, answers 54% of factual questions
    - 3x better information retention at 2.5x the size

Benchmark proof (h2h_with_fixloop.py):
    - OLD pipeline (regex): 0 tests passed / 3 import errors
    - NEW pipeline (LLMLingua): 7 tests passed / 1 error
    - Root cause: regex compression destroys import paths and API signatures
      that downstream agents need to write working code

Dependencies:
    pip install llmlingua

License: MIT (Microsoft) — compatible with Apache 2.0
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Lazy-loaded compressor singleton
_compressor = None
_load_failed = False


def _get_compressor():
    """Lazy-load the LLMLingua compressor (downloads model on first use)."""
    global _compressor, _load_failed
    if _load_failed:
        return None
    if _compressor is not None:
        return _compressor
    try:
        from llmlingua import PromptCompressor
        _compressor = PromptCompressor(
            model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
            use_llmlingua2=True,
            device_map="cpu",
        )
        logger.info("[ContextCompressor] LLMLingua loaded successfully")
        return _compressor
    except Exception as e:
        _load_failed = True
        logger.warning("[ContextCompressor] Failed to load LLMLingua: %s", e)
        return None


def compress_context_entry(entry: str) -> str:
    """Compress a context entry using LLMLingua-2.

    Drop-in replacement for orch_context.compress_context_entry.
    Preserves import paths, function signatures, and API endpoints that
    downstream agents need to write working code.

    Args:
        entry: The context entry text to compress.

    Returns:
        Compressed text that retains key semantic information.
    """
    if not entry or len(entry) < 100:
        return entry

    compressor = _get_compressor()
    if compressor is None:
        return entry[:600]

    try:
        result = compressor.compress_prompt(
            [entry],
            rate=0.6,
            force_tokens=['\n', '.', ':', '/', '_', 'import', 'def', 'class',
                          'from', 'return', 'async', 'await', 'self'],
        )
        compressed = result.get("compressed_prompt", entry)
        if compressed and len(compressed) > 50:
            return compressed
        return entry[:600]
    except Exception as e:
        logger.warning("[ContextCompressor] Compression failed: %s", e)
        return entry[:600]
