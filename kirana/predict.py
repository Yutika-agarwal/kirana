"""Work out which items you buy repeatedly and when the next one is due."""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from . import db


@dataclass
class Prediction:
    merchant: str
    name: str
    norm: str
    orders: int
    last_date: date
    interval_days: int
    due_date: date
    qty: int
    unit_price: float
    confidence: str  # high | medium | low
    status: str  # overdue | due | upcoming

    @property
    def est_total(self) -> float:
        return round(self.qty * self.unit_price, 2)


def _confidence(n: int, gaps: list[int]) -> str:
    if n < 3:
        return "low"
    mean = statistics.mean(gaps)
    if mean == 0:
        return "low"
    cv = statistics.pstdev(gaps) / mean
    if n >= 4 and cv <= 0.25:
        return "high"
    return "medium" if cv <= 0.5 else "low"


def recurring_items(conn, min_orders: int = 2, merchant: str | None = None) -> list[Prediction]:
    """Every item bought on >= min_orders distinct dates, with its cadence."""
    by_item: dict[tuple, dict[date, dict]] = defaultdict(dict)
    names: dict[tuple, str] = {}
    for r in db.purchase_rows(conn, merchant):
        key = (r["merchant"], r["norm"])
        d = date.fromisoformat(r["placed_on"])
        slot = by_item[key].setdefault(d, {"qty": 0, "price": r["unit_price"]})
        slot["qty"] += r["qty"]
        slot["price"] = r["unit_price"]  # latest price wins
        names[key] = r["name"]

    out = []
    for key, dates in by_item.items():
        if len(dates) < min_orders:
            continue
        ds = sorted(dates)
        gaps = [(b - a).days for a, b in zip(ds, ds[1:])]
        gaps = [g for g in gaps if g > 0] or [1]
        interval = max(1, round(statistics.median(gaps)))
        qty = max(1, round(statistics.median([dates[d]["qty"] for d in ds])))
        last = ds[-1]
        out.append(
            Prediction(
                merchant=key[0],
                name=names[key],
                norm=key[1],
                orders=len(ds),
                last_date=last,
                interval_days=interval,
                due_date=last + timedelta(days=interval),
                qty=qty,
                unit_price=dates[last]["price"],
                confidence=_confidence(len(ds), gaps),
                status="upcoming",
            )
        )
    return out


def predict_due(conn, today: date, horizon_days: int = 7, min_orders: int = 2,
                merchant: str | None = None, min_interval_days: int = 5,
                lapse_factor: float = 3.0, include_lapsed: bool = False) -> list[Prediction]:
    """Recurring items due now or within the horizon, most overdue first.

    Two sanity rules keep one-off buying bursts out of carts:
    - repeats closer than `min_interval_days` (5) are returns/replacements/bundles, not a cadence;
    - an item overdue by more than `lapse_factor` x its interval (and 60+ days) has
      lapsed - you most likely stopped buying it - so it is not proposed.
    """
    due = []
    for p in recurring_items(conn, min_orders, merchant):
        if p.due_date > today + timedelta(days=horizon_days):
            continue
        if p.interval_days < min_interval_days:
            continue
        # one-time gear (bottles, hose reels, cases) that happened to be bought twice
        # is not a replenishment cycle; only trust low-confidence repeats for consumables
        if p.confidence == "low" and not CONSUMABLE.search(p.name):
            continue
        overdue_by = (today - p.due_date).days
        if not include_lapsed and overdue_by > max(lapse_factor * p.interval_days, 60):
            continue
        p.status = "overdue" if p.due_date < today else ("due" if p.due_date == today else "upcoming")
        due.append(p)
    due.sort(key=lambda p: (p.due_date, p.merchant, p.name))
    return due


CONSUMABLE = re.compile(
    r"\b(wipes?|solution|vitamins?|supplements?|capsules?|tablets?|coffee|tea|sunscreen|"
    r"ketchup|toothpaste|detergent|soap|shampoo|conditioner|paper towels?|tissues?|diapers?|"
    r"crackers|pistachios|snacks?|rice|flour|oil|spice|sauce|batteries|trash bags?|"
    r"floss|lotion|cleaner|bandages?|gum|ointment|multivitamin|pops|formula|wipes)\b",
    re.IGNORECASE,
)


@dataclass
class Suggestion:
    merchant: str
    name: str
    norm: str
    qty: int
    unit_price: float
    last_date: date
    days_ago: int


def suggest_restock(conn, today: date, min_days: int = 45, max_days: int = 400,
                    merchant: str | None = None) -> list[Suggestion]:
    """Consumables bought ONCE a while ago. A guess, not a prediction: there is
    no cadence to learn from, so these are only ever added to a cart on request."""
    rec = {(p.merchant, p.norm) for p in recurring_items(conn, merchant=merchant)}
    latest: dict[tuple, Suggestion] = {}
    for r in db.purchase_rows(conn, merchant):
        key = (r["merchant"], r["norm"])
        # match the leading clause only: marketplace titles end in keyword soup
        # ("ice cube trays ... for Cocktails, Whiskey, Coffee") that is not the product
        head = re.split(r"[,|:;]| - ", r["name"], maxsplit=1)[0]
        if key in rec or not CONSUMABLE.search(head):
            continue
        d = date.fromisoformat(r["placed_on"])
        latest[key] = Suggestion(r["merchant"], r["name"], r["norm"], r["qty"],
                                 r["unit_price"], d, (today - d).days)
    out = [s for s in latest.values() if min_days <= s.days_ago <= max_days]
    out.sort(key=lambda s: s.days_ago)
    return out


def one_off_count(conn) -> int:
    """How many distinct items were bought only once (not predictable)."""
    seen = {(r["merchant"], r["norm"]) for r in db.purchase_rows(conn)}
    rec = {(p.merchant, p.norm) for p in recurring_items(conn)}
    return len(seen - rec)
