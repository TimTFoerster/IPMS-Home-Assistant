"""Numeric marker decoding and encoding helpers."""

from __future__ import annotations

def decode_numeric(
    raw: int | None,
    mode: str,
    *,
    high: int | None = None,
    low: int | None = None,
) -> int | None:
    """Decode byte/word values, including two's-complement signed values."""
    if mode in ("word", "signed_word") and high is not None and low is not None:
        raw = (high << 8) | low
    if raw is None:
        return None

    bit_width = 16 if mode in ("word", "signed_word") else 8
    if not 0 <= raw < 1 << bit_width:
        return None
    if mode in ("signed_byte", "signed_word") and raw & (1 << (bit_width - 1)):
        return raw - (1 << bit_width)
    return raw


def encode_numeric(value: float, mode: str, scale: float, offset: float) -> int:
    """Apply inverse scale/offset and encode signed values as two's complement."""
    if scale == 0:
        raise ValueError("Numeric scale must not be zero")
    raw = round((value - offset) / scale)
    bit_width = 16 if mode in ("word", "signed_word") else 8
    signed = mode in ("signed_byte", "signed_word")
    minimum = -(1 << (bit_width - 1)) if signed else 0
    maximum = (1 << (bit_width - (1 if signed else 0))) - 1
    if not minimum <= raw <= maximum:
        raise ValueError(
            f"Value {value} encodes to {raw}, outside {mode} range "
            f"{minimum}..{maximum}"
        )
    return raw % (1 << bit_width)


def scaled_value(raw: int | None, scale: float, offset: float) -> float | None:
    """Convert a raw marker value to its scaled entity value."""
    if raw is None:
        return None
    return raw * scale + offset
