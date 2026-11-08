"""A fake store for end-to-end runs and tests. Moves no real money."""
from __future__ import annotations

import hashlib

from .base import LineResult, PlaceResult, StoreAdapter

# A tiny catalog; unknown items get a deterministic price so demos are stable.
CATALOG = {
    "whole milk 1 gal": 4.29,
    "eggs dozen": 3.99,
    "sourdough bread": 5.49,
    "basmati rice 5lb": 9.99,
    "toor dal 2lb": 6.49,
    "ghee 16oz": 12.99,
    "oat milk 64oz": 4.79,
}
OUT_OF_STOCK = {"ghee 16oz"}


def _fallback_price(name: str) -> float:
    h = int(hashlib.sha256(name.lower().encode()).hexdigest()[:6], 16)
    return round(2 + (h % 1800) / 100, 2)


class MockStore(StoreAdapter):
    name = "mock"

    def __init__(self, out_of_stock=None):
        self.out_of_stock = set(OUT_OF_STOCK if out_of_stock is None else out_of_stock)

    def place(self, cart: dict) -> PlaceResult:
        results, total = [], 0.0
        for line in cart["lines"]:
            key = line["name"].lower()
            if key in self.out_of_stock:
                results.append(LineResult(line["name"], line["qty"], False, None, "out of stock"))
                continue
            price = CATALOG.get(key) or line.get("est_unit_price") or _fallback_price(key)
            total += price * line["qty"]
            results.append(LineResult(line["name"], line["qty"], True, price, "added"))
        ok = [r for r in results if r.ok]
        if not ok:
            return PlaceResult("failed", None, results, 0.0, "nothing in the cart was available")
        conf = "MOCK-" + hashlib.sha256(repr(cart["lines"]).encode()).hexdigest()[:8].upper()
        missed = len(results) - len(ok)
        msg = f"placed {len(ok)} line(s)" + (f"; {missed} unavailable" if missed else "")
        return PlaceResult("placed", conf, results, round(total, 2), msg)
