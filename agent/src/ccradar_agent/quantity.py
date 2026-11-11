"""Kubernetes quantities ("250m", "12345678n", "512Mi") as numbers."""

from __future__ import annotations

_BINARY = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}
_DECIMAL = {"n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
GIB = 2**30


def parse(value: object) -> float:
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
        return float(text)
    except ValueError:
        return 0.0
