"""SQLite storage for orders, items and carts."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    merchant   TEXT NOT NULL,
    order_id   TEXT NOT NULL,
    placed_on  TEXT NOT NULL,
    total      REAL,
    source     TEXT,
    PRIMARY KEY (merchant, order_id)
);
CREATE TABLE IF NOT EXISTS items (
    merchant   TEXT NOT NULL,
    order_id   TEXT NOT NULL,
    name       TEXT NOT NULL,
    norm       TEXT NOT NULL,
    qty        INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    FOREIGN KEY (merchant, order_id) REFERENCES orders (merchant, order_id)
);
CREATE INDEX IF NOT EXISTS items_norm ON items (merchant, norm);
CREATE TABLE IF NOT EXISTS carts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_on TEXT NOT NULL,
    store      TEXT NOT NULL,
    status     TEXT NOT NULL,
    lines      TEXT NOT NULL,
    note       TEXT
);
"""


def default_db_path() -> str:
    return os.environ.get("KIRANA_DB") or str(Path.home() / ".kirana" / "kirana.db")


def normalize(name: str) -> str:
    """Normalise an item title so repeat purchases of the same product match."""
    s = name.lower()
    s = re.sub(r"[™®©]", "", s)
    s = re.sub(r"&#?\w+;", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    # Marketplace titles are long keyword soup; the first 60 chars identify the product
    # and keep keys stable if a later email shortens or lengthens the tail.
    return s[:60].rstrip(" -,")


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or default_db_path()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def add_order(conn, merchant, order_id, placed_on, items, total=None, source=None) -> bool:
    """Insert an order (idempotent). Returns True if it was new."""
    if isinstance(placed_on, date):
        placed_on = placed_on.isoformat()
    cur = conn.execute(
        "INSERT OR IGNORE INTO orders (merchant, order_id, placed_on, total, source) "
        "VALUES (?,?,?,?,?)",
        (merchant.strip().lower(), str(order_id), placed_on, total, source),
    )
    if cur.rowcount == 0:
        return False
    for it in items:
        conn.execute(
            "INSERT INTO items (merchant, order_id, name, norm, qty, unit_price) "
            "VALUES (?,?,?,?,?,?)",
            (
                merchant.strip().lower(),
                str(order_id),
                it["name"],
                normalize(it["name"]),
                int(it.get("qty", 1)),
                float(it.get("unit_price", 0.0)),
            ),
        )
    conn.commit()
    return True


def purchase_rows(conn, merchant: str | None = None):
    q = (
        "SELECT i.merchant, i.name, i.norm, i.qty, i.unit_price, o.placed_on, o.order_id "
        "FROM items i JOIN orders o ON o.merchant=i.merchant AND o.order_id=i.order_id"
    )
    args: tuple = ()
    if merchant:
        q += " WHERE i.merchant = ?"
        args = (merchant.lower(),)
    q += " ORDER BY o.placed_on, i.name"
    return conn.execute(q, args).fetchall()


# ---- carts -----------------------------------------------------------------

def save_cart(conn, store, lines, today: date, note: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO carts (created_on, store, status, lines, note) VALUES (?,?,?,?,?)",
        (today.isoformat(), store, "proposed", json.dumps(lines), note),
    )
    conn.commit()
    return cur.lastrowid


def get_cart(conn, cart_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM carts WHERE id=?", (cart_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["lines"] = json.loads(d["lines"])
    return d


def list_carts(conn):
    rows = conn.execute("SELECT * FROM carts ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["lines"] = json.loads(d["lines"])
        out.append(d)
    return out


def set_cart_status(conn, cart_id: int, status: str, note: str | None = None) -> None:
    if note is None:
        conn.execute("UPDATE carts SET status=? WHERE id=?", (status, cart_id))
    else:
        conn.execute("UPDATE carts SET status=?, note=? WHERE id=?", (status, note, cart_id))
    conn.commit()


def update_cart_lines(conn, cart_id: int, lines) -> None:
    conn.execute("UPDATE carts SET lines=? WHERE id=?", (json.dumps(lines), cart_id))
    conn.commit()
