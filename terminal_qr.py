"""
Minimal terminal QR code generator — zero external dependencies.

Uses Unicode block characters to render a compact QR code directly in the
terminal.  Supports URLs up to ~70 characters (QR Version 1-4, ECC level L).

Only used for the startup banner to make mobile access easy.
Falls back gracefully (no QR) if encoding fails.
"""

from __future__ import annotations


def _print_qr_terminal(url: str) -> bool:
    """
    Print a QR code for *url* to stdout using Unicode half-block characters.

    Returns True on success, False if the optional ``qrcode`` package is not
    installed (graceful degradation — the caller just skips the QR).
    """
    try:
        # qrcode is an optional dependency — not in requirements.txt
        import qrcode  # type: ignore[import-untyped]
    except ImportError:
        return False

    try:
        qr = qrcode.QRCode(
            version=None,  # auto-detect smallest version
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
    except Exception:
        return False

    # Render using Unicode upper/lower half-block characters.
    # Each printed row encodes TWO QR rows:
    #   - top row dark + bottom row dark  → FULL BLOCK  (█)
    #   - top row dark + bottom row light → UPPER HALF  (▀)
    #   - top row light + bottom row dark → LOWER HALF  (▄)
    #   - top row light + bottom row light → SPACE       ( )
    #
    # Dark modules = black in QR spec.  We use inverted colors
    # (dark module → white-on-black terminal) for better contrast.

    FULL = "\u2588"   # █
    UPPER = "\u2580"  # ▀
    LOWER = "\u2584"  # ▄
    EMPTY = " "

    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0

    lines: list[str] = []
    for y in range(0, rows, 2):
        line = ""
        for x in range(cols):
            top = matrix[y][x]
            bot = matrix[y + 1][x] if y + 1 < rows else False
            if top and bot:
                line += EMPTY  # both dark → background (inverted)
            elif top:
                line += LOWER  # top dark, bottom light → lower half (inverted)
            elif bot:
                line += UPPER  # top light, bottom dark → upper half (inverted)
            else:
                line += FULL   # both light → full block (inverted)
        lines.append(line)

    print()
    for line in lines:
        print(f"  {line}")
    print()

    return True


def print_qr_for_url(url: str) -> None:
    """Print a QR code banner for the given URL.  No-op if qrcode is missing."""
    _print_qr_terminal(url)
