"""Store adapter interface.

An adapter turns an *approved* cart into an order at one store. Real
checkout (payment, login) is never automated here: the `handoff` adapter
prepares everything and leaves the final click to you.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LineResult:
    name: str
    qty: int
    ok: bool
    unit_price: float | None = None
    detail: str = ""


@dataclass
class PlaceResult:
    outcome: str  # "placed" | "handed_off" | "failed"
    confirmation: str | None = None
    lines: list[LineResult] = field(default_factory=list)
    total: float | None = None
    message: str = ""
    artifact: str | None = None  # path of a file produced (e.g. handoff checklist)


class StoreAdapter:
    name = "base"

    def place(self, cart: dict) -> PlaceResult:  # pragma: no cover - interface
        raise NotImplementedError
