"""Validation for the server-authoritative logical clock."""

from __future__ import annotations


def timestamp(value: int) -> int:
    """Reject client-like, non-integral logical timestamps."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("logical time must be an integer")
    return value
