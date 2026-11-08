"""Parse order data out of emails / files.

Supported inputs:
  * Target-style plain-text order confirmation emails ("Qty: N" / "$X / ea")
  * JSON  ({"orders": [...]}) and CSV (merchant,order_id,date,item,qty,unit_price)

HTML-only merchants (e.g. Old Navy) expose no items in their plain-text part;
feed those through JSON/CSV or add a parser here.
"""
from __future__ import annotations

import csv
import html
import io
import json
import re
from datetime import datetime

MONEY = r"\$\s*([\d,]+\.\d{2})"


def _clean(text: str) -> str:
    text = html.unescape(text)
    text = text.replace(" ", " ")
    # drop tracking-link lines and invisible preheader filler
    lines = [ln for ln in text.splitlines() if not re.match(r"\s*https?://", ln)]
    text = "\n".join(lines)
    text = re.sub(r"[ -‏͏⁠﻿]", "", text)
    return text


def _money(s: str) -> float:
    return float(s.replace(",", ""))


def _parse_date(s: str):
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {s!r}")


def parse_order_email_text(body: str, merchant: str, fallback_date=None) -> dict | None:
    """Parse a plain-text order confirmation. Returns None if no items found."""
    text = _clean(body)

    m = re.search(r"Order\s*#\s*:?\s*([A-Za-z0-9\-]+)", text)
    order_id = m.group(1) if m else None

    placed = None
    m = re.search(r"Placed\s+([A-Z][a-z]+\.? \d{1,2}, \d{4})", text)
    if m:
        placed = _parse_date(m.group(1))
    elif fallback_date:
        placed = fallback_date

    total = None
    m = re.search(r"Order total\s*\n\s*" + MONEY, text)
    if m:
        total = _money(m.group(1))

    items = []
    pat = re.compile(
        r"\n([^\n]+?)\s*\n\s*\n\s*Qty:\s*(\d+)\s*\n\s*\n\s*" + MONEY + r"\s*/\s*ea"
    )
    for m in pat.finditer("\n" + text):
        items.append(
            {"name": m.group(1).strip(), "qty": int(m.group(2)), "unit_price": _money(m.group(3))}
        )

    if not (order_id and placed and items):
        return None
    return {
        "merchant": merchant,
        "order_id": order_id,
        "placed_on": placed.isoformat(),
        "total": total,
        "items": items,
    }


AMAZON_ORDER = re.compile(r"Order\s*#\s*\n?\s*(\d{3}-\d{7}-\d{7})")
AMAZON_ITEM = re.compile(
    r"\*\s+([^\n]+?)\s*\n\s*Quantity:\s*(\d+)\s*\n\s*([\d,]+(?:\.\d+)?)\s+([A-Z]{3})"
)
AMAZON_TOTAL = re.compile(r"\n\s*(?:Grand )?Total:?\s*\n")


def parse_amazon_email_text(body: str, sent_on) -> list[dict]:
    """Parse an Amazon 'Ordered:' confirmation (auto-confirm@amazon.com).

    One email can hold several orders, and one order can repeat across
    shipment blocks; blocks with the same order number are merged. The date
    comes from the email header (`sent_on`). Item prices are taken as unit
    prices. Gift cards and non-USD lines are skipped.
    """
    if hasattr(sent_on, "isoformat"):
        sent_on = sent_on.isoformat()
    text = _clean(body)
    marks = list(AMAZON_ORDER.finditer(text))
    orders: dict[str, dict] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        seg = text[m.end():end]
        cut = AMAZON_TOTAL.search("\n" + seg)
        if cut:
            seg = ("\n" + seg)[: cut.start()]
        o = orders.setdefault(
            m.group(1),
            {"merchant": "amazon", "order_id": m.group(1), "placed_on": sent_on,
             "total": None, "items": []},
        )
        for im in AMAZON_ITEM.finditer(seg):
            name, qty, price, cur = im.groups()
            if cur != "USD" or name.lower().startswith("amazon egift card"):
                continue
            o["items"].append({"name": name.strip(), "qty": int(qty),
                               "unit_price": _money(price)})
    out = [o for o in orders.values() if o["items"]]
    if len(out) == 1:
        t = re.findall(r"(?:Grand )?Total:?\s*\n\s*([\d,]+(?:\.\d+)?)\s*USD", text)
        if t:
            out[0]["total"] = _money(t[-1])
    return out


def parse_email(merchant: str, body: str, sent_on=None) -> list[dict]:
    """Dispatch a plain-text order email to the right parser. Returns orders."""
    if merchant.lower() == "amazon":
        if not sent_on:
            raise ValueError("amazon emails need --date (the date the email was sent)")
        return parse_amazon_email_text(body, sent_on)
    o = parse_order_email_text(body, merchant, sent_on)
    return [o] if o else []


def parse_json(data: str) -> list[dict]:
    obj = json.loads(data)
    orders = obj["orders"] if isinstance(obj, dict) else obj
    out = []
    for o in orders:
        out.append(
            {
                "merchant": o["merchant"],
                "order_id": str(o["order_id"]),
                "placed_on": _parse_date(o["placed_on"]).isoformat(),
                "total": o.get("total"),
                "items": [
                    {
                        "name": i["name"],
                        "qty": int(i.get("qty", 1)),
                        "unit_price": float(i.get("unit_price", 0)),
                    }
                    for i in o["items"]
                ],
            }
        )
    return out


def parse_csv(data: str) -> list[dict]:
    rows = csv.DictReader(io.StringIO(data))
    grouped: dict[tuple, dict] = {}
    for r in rows:
        key = (r["merchant"].strip(), r["order_id"].strip())
        o = grouped.setdefault(
            key,
            {
                "merchant": key[0],
                "order_id": key[1],
                "placed_on": _parse_date(r["date"]).isoformat(),
                "total": None,
                "items": [],
            },
        )
        o["items"].append(
            {
                "name": r["item"].strip(),
                "qty": int(r.get("qty") or 1),
                "unit_price": float(r.get("unit_price") or 0),
            }
        )
    return list(grouped.values())
