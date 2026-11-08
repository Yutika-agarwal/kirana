"""Turn predictions and ad-hoc requests into per-store carts."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from . import db
from .predict import Prediction

DEFAULT_STORE = "any"  # requests with no store; resolved at approval time


def parse_want(text: str) -> dict:
    """Parse free text like 'oat milk x2 @ safeway' or '2 dozen eggs from costco'."""
    t = text.strip()
    store = DEFAULT_STORE
    m = re.search(r"\s+(?:@|from|at)\s+([A-Za-z0-9 .&'-]+)$", t, re.IGNORECASE)
    if m:
        store = m.group(1).strip().lower()
        t = t[: m.start()].strip()
    qty = 1
    m = re.search(r"\s+[x×]\s*(\d+)$", t, re.IGNORECASE)
    if m:
        qty = int(m.group(1))
        t = t[: m.start()].strip()
    else:
        m = re.match(r"^(\d+)\s+[x×]?\s*(.+)$", t, re.IGNORECASE)
        if m:
            qty = int(m.group(1))
            t = m.group(2).strip()
    if not t:
        raise ValueError(f"could not understand request: {text!r}")
    return {"name": t, "qty": qty, "store": store}


_LEAD = re.compile(r"^\s*(?:please\s+)?(?:can you\s+)?(?:get|buy|order|fetch|add|grab|i need|i want|restock|reorder)(?:\s+me)?\s+", re.I)
_STORE_TAIL = re.compile(r"\s+(?:@|from|at)\s+([A-Za-z0-9 .&'-]+)$", re.I)


def parse_request(text: str) -> list[dict]:
    """Free-text prompt -> wants. 'get me oat milk and eggs from safeway, pistachios x2 from target'.

    Segments split on commas/semicolons; inside a segment, ' and ' separates items.
    A trailing 'from <store>' applies to every item in that segment.
    """
    t = _LEAD.sub("", text.strip().rstrip("."))
    wants: list[dict] = []
    for seg in re.split(r"[;,]\s*", t):
        seg = _LEAD.sub("", seg.strip())
        if not seg:
            continue
        store = DEFAULT_STORE
        m = _STORE_TAIL.search(seg)
        if m:
            store = m.group(1).strip().lower()
            seg = seg[: m.start()].strip()
        for part in re.split(r"\s+and\s+|\s*&\s*", seg, flags=re.I):
            part = part.strip()
            if part:
                w = parse_want(part)
                w["store"] = store
                wants.append(w)
    if not wants:
        raise ValueError(f"could not find any items in: {text!r}")
    return wants


def build_carts(predictions: list[Prediction], wants: list[dict],
                suggestions: list | None = None) -> dict[str, list[dict]]:
    """Group lines by store. Predicted lines come from history; wants from the user;
    suggestions are one-time consumables the user opted in to."""
    carts: dict[str, list[dict]] = defaultdict(list)
    for s in suggestions or []:
        carts[s.merchant].append(
            {
                "name": s.name,
                "qty": s.qty,
                "est_unit_price": s.unit_price,
                "source": "suggested",
                "reason": (f"bought once, {s.days_ago}d ago ({s.last_date.isoformat()}); "
                           f"consumable - confirm you need it"),
            }
        )
    for p in predictions:
        carts[p.merchant].append(
            {
                "name": p.name,
                "qty": p.qty,
                "est_unit_price": p.unit_price,
                "source": "predicted",
                "reason": (
                    f"bought {p.orders}x, every ~{p.interval_days}d, "
                    f"last {p.last_date.isoformat()} ({p.status}, {p.confidence} confidence)"
                ),
            }
        )
    for w in wants:
        carts[w["store"]].append(
            {
                "name": w["name"],
                "qty": w["qty"],
                "est_unit_price": w.get("est_unit_price"),
                "source": "requested",
                "reason": "requested by you",
            }
        )
    return dict(carts)


def display_name(name: str, width: int = 80) -> str:
    """Shorten keyword-soup titles for terminals and checklists (data keeps the full title)."""
    return name if len(name) <= width else name[: width - 1].rstrip(" ,-") + "…"


def cart_total(lines: list[dict]) -> float:
    """Total of the lines that have a price (see unpriced_count for the rest)."""
    return round(
        sum(l["qty"] * (l.get("est_unit_price") or 0.0) for l in lines), 2
    )


def unpriced_count(lines: list[dict]) -> int:
    return sum(1 for l in lines if not l.get("est_unit_price"))


def total_label(lines: list[dict]) -> str:
    n = unpriced_count(lines)
    label = f"${cart_total(lines):.2f}"
    return label + (f" + {n} unpriced item(s)" if n else "")


def estimate_price(conn, store: str, name: str) -> float | None:
    """Latest price paid at this store for an item whose title contains `name`."""
    needle = db.normalize(name)
    best = None
    for r in db.purchase_rows(conn, None if store == DEFAULT_STORE else store):
        if needle and needle in r["norm"]:
            best = r["unit_price"]  # rows are ordered by date; latest wins
    return best


def persist(conn, carts: dict[str, list[dict]], today: date) -> list[int]:
    return [db.save_cart(conn, store, lines, today) for store, lines in carts.items() if lines]
