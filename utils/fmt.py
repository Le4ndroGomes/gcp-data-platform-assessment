"""Formatação numérica pt-BR (milhar '.', decimal ',')."""
from __future__ import annotations

NA = "—"


def n(v, dec: int = 0) -> str:
    if v is None or v == "":
        return NA
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    s = f"{f:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def b(v) -> str:
    if v is None:
        return NA
    v = float(v)
    for unit, div in (("PB", 1e15), ("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if abs(v) >= div:
            return f"{n(v / div, 2)} {unit}"
    return f"{n(v)} B"
