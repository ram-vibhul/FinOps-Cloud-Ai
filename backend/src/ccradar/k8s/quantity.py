"""Kubernetes resource quantities ("250m", "1.5", "512Mi", "2G") as numbers."""

from __future__ import annotations

_BINARY = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60}
_DECIMAL = {
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
    "P": 1e15,
    "E": 1e18,
}
GIB = 2**30


def parse(value: object) -> float:
    """A quantity as a plain number (cores for CPU, bytes for memory). 0 when unreadable."""
    if value is None:
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    try:
        for suffix, factor in _BINARY.items():
            if text.endswith(suffix):
                return float(text[: -len(suffix)]) * factor
        if text and text[-1] in _DECIMAL:
            return float(text[:-1]) * _DECIMAL[text[-1]]
        return float(text)  # also handles 1e9
    except ValueError:
        return 0.0


def cores(value: object) -> float:
    return parse(value)


def gib(value: object) -> float:
    return parse(value) / GIB
