"""Context Compressor — LLMLingua-powered prompt compression.

Outsources context compression to Microsoft's LLMLingua library, which uses
a small language model to identify and remove redundant tokens while preserving
semantic meaning.  This replaces the hand-rolled heuristic compression in
``orch_context.compress_context_entry()`` with a research-backed approach that
achieves 2x–5x compression with minimal quality loss.

The compressor is designed as a **drop-in enhancement**: if LLMLingua is not
installed or the model fails to load, it falls back transparently to the
original heuristic compression.

Architecture
------------
    orch_context.py
      └─ calls ``compress_context_smart(entry)``
           └─ tries LLMLingua first
           └─ falls back to heuristic if unavailable

Configuration via environment:
    LLMLINGUA_ENABLED      — Enable/disable (default: true)
    LLMLINGUA_MODEL        — Small model for compression (default: microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank)
    LLMLINGUA_RATE         — Target compression rate 0.0-1.0 (default: 0.5 = 50% compression)
    LLMLINGUA_DEVICE       — Device for inference: cpu/cuda (default: cpu)

References:
    - LLMLingua: https://github.com/microsoft/LLMLingua
    - Paper: "LLMLingua-2: Data Distillation for Efficient and Faithful Task-Agnostic Prompt Compression"
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────

LLMLINGUA_ENABLED = os.getenv("LLMLINGUA_ENABLED", "true").lower() in ("true", "1", "yes")
LLMLINGUA_MODEL = os.getenv(
    "LLMLINGUA_MODEL",
    "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
)
LLMLINGUA_RATE = float(os.getenv("LLMLINGUA_RATE", "0.5"))
LLMLINGUA_DEVICE = os.getenv("LLMLINGUA_DEVICE", "cpu")

# ── Lazy singleton ───────────────────────────────────────────────────────

_compressor: Any = None
_compressor_lock = threading.Lock()
_compressor_failed = False


def _get_compressor():
    """Lazy-load the LLMLingua compressor (singleton, thread-safe).

    Returns None if LLMLingua is not installed or model loading fails.
    """
    global _compressor, _compressor_failed

    if not LLMLINGUA_ENABLED:
        return None

    if _compressor is not None:
        return _compressor

    if _compressor_failed:
        return None

    with _compressor_lock:
        # Double-check after acquiring lock
        if _compressor is not None:
            return _compressor
        if _compressor_failed:
            return None

        try:
            from llmlingua import PromptCompressor

            _compressor = PromptCompressor(
                model_name=LLMLINGUA_MODEL,
                use_llmlingua2=True,
                device_map=LLMLINGUA_DEVICE,
            )
            logger.info(
                "[ContextCompressor] LLMLingua loaded successfully "
                f"(model={LLMLINGUA_MODEL}, rate={LLMLINGUA_RATE}, device={LLMLINGUA_DEVICE})"
            )
            return _compressor

        except ImportError:
            logger.info(
                "[ContextCompressor] LLMLingua not installed — using heuristic compression. "
                "Install with: pip install llmlingua"
            )
            _compressor_failed = True
            return None

        except Exception as e:
            logger.warning(
                f"[ContextCompressor] Failed to load LLMLingua model: {e} — "
                "falling back to heuristic compression"
            )
            _compressor_failed = True
            return None


# ── Public API ───────────────────────────────────────────────────────────


def compress_text_llmlingua(text: str, target_ratio: float | None = None) -> str | None:
    """Compress text using LLMLingua-2.

    Args:
        text: The text to compress.
        target_ratio: Compression ratio (0.0 = maximum compression, 1.0 = no compression).
                      Defaults to LLMLINGUA_RATE from environment.

    Returns:
        Compressed text, or None if LLMLingua is unavailable.
    """
    compressor = _get_compressor()
    if compressor is None:
        return None

    if not text or len(text) < 100:
        # Too short to benefit from compression
        return text

    ratio = target_ratio if target_ratio is not None else LLMLINGUA_RATE

    try:
        result = compressor.compress_prompt(
            text,
            rate=ratio,
            force_tokens=["\n", ".", ":", "[", "]", "Status", "FAILED", "ERROR", "Issues"],
            drop_consecutive=True,
        )
        compressed = result.get("compressed_prompt", text)

        original_tokens = len(text.split())
        compressed_tokens = len(compressed.split())
        if original_tokens > 0:
            actual_ratio = compressed_tokens / original_tokens
            logger.debug(
                f"[ContextCompressor] Compressed {original_tokens} → {compressed_tokens} tokens "
                f"(ratio={actual_ratio:.2f}, target={ratio:.2f})"
            )

        return compressed

    except Exception as e:
        logger.warning(f"[ContextCompressor] Compression failed: {e} — returning original text")
        return None


def compress_context_smart(entry: str, target_ratio: float | None = None) -> str:
    """Smart context compression: tries LLMLingua first, falls back to heuristic.

    This is the main entry point used by orch_context.py.  It provides a
    seamless upgrade path: if LLMLingua is installed, use it; otherwise,
    fall back to the existing heuristic compression.

    Args:
        entry: A shared_context entry string.
        target_ratio: Optional compression ratio override.

    Returns:
        Compressed entry string (always returns something, never None).
    """
    if not entry:
        return entry

    # Try LLMLingua first
    compressed = compress_text_llmlingua(entry, target_ratio=target_ratio)
    if compressed is not None:
        return compressed

    # Fall back to the original heuristic compression
    return _heuristic_compress(entry)


def _heuristic_compress(entry: str) -> str:
    """Original heuristic compression (preserved as fallback).

    Keeps: role/status header, status line, issues, file changes.
    Truncates: raw output, verbose descriptions.
    """
    if not entry:
        return entry
    lines = entry.split("\n")
    essential = []
    for line in lines:
        ls = line.strip()
        if ls.startswith(("[", "Status:", "Files changed:", "Issues:", "Commands:")):
            essential.append(line[:200])
        elif ls.startswith("Output:"):
            essential.append(line[:120])
        elif ls.startswith("Test results:"):
            essential.append(line[:150])
        elif ls.startswith("Diff summary:"):
            essential.append(line[:120])
        elif len(essential) < 4:
            essential.append(line[:150])
    if not essential:
        return entry[:300]
    return "\n".join(essential)


def get_compressor_status() -> dict:
    """Return status information about the compressor for diagnostics."""
    return {
        "enabled": LLMLINGUA_ENABLED,
        "model": LLMLINGUA_MODEL,
        "target_rate": LLMLINGUA_RATE,
        "device": LLMLINGUA_DEVICE,
        "loaded": _compressor is not None,
        "failed": _compressor_failed,
        "backend": "llmlingua" if _compressor is not None else "heuristic",
    }
