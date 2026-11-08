"""Load order files into the database."""
from __future__ import annotations

from pathlib import Path

from . import db, parsers


def ingest_file(conn, path: str, merchant: str | None = None,
                sent_on: str | None = None) -> tuple[int, int]:
    """Ingest one file. Returns (orders_added, orders_skipped)."""
    p = Path(path)
    data = p.read_text(encoding="utf-8", errors="replace")
    suffix = p.suffix.lower()
    if suffix == ".json":
        orders = parsers.parse_json(data)
    elif suffix == ".csv":
        orders = parsers.parse_csv(data)
    else:
        if not merchant:
            raise ValueError(f"{path}: plain-text emails need --merchant")
        orders = parsers.parse_email(merchant, data, sent_on)
        if not orders:
            raise ValueError(f"{path}: no order/items could be parsed from this email")

    added = skipped = 0
    for o in orders:
        is_new = db.add_order(
            conn,
            o["merchant"],
            o["order_id"],
            o["placed_on"],
            o["items"],
            total=o.get("total"),
            source=p.name,
        )
        added += is_new
        skipped += not is_new
    return added, skipped
