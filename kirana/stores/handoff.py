"""Browser hand-off for real stores.

Writes a checklist (markdown + json) with a search link per item, so you or
a browser agent (e.g. Claude in Chrome, already logged in) can fill the cart
at the store. Payment and the final 'Place order' click stay with you.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote_plus

from .base import LineResult, PlaceResult, StoreAdapter

SEARCH_URLS = {
    "target": "https://www.target.com/s?searchTerm={q}",
    "amazon": "https://www.amazon.com/s?k={q}",
    "amazon fresh": "https://www.amazon.com/alm/storefront?almBrandId=QW1hem9uIEZyZXNo&k={q}",
    "walmart": "https://www.walmart.com/search?q={q}",
    "costco": "https://www.costco.com/CatalogSearch?keyword={q}",
    "instacart": "https://www.instacart.com/store/s?k={q}",
    "safeway": "https://www.safeway.com/shop/search-results.html?q={q}",
    "trader joe's": "https://www.traderjoes.com/home/search?q={q}",
    "whole foods": "https://www.amazon.com/s?k={q}&i=wholefoods",
}


def short_query(item: str, max_words: int = 8) -> str:
    """Marketplace titles are keyword soup; the first clause searches far better."""
    head = re.split(r"[,|:;(]| - ", item, maxsplit=1)[0]
    return " ".join(head.split()[:max_words]) or item


def search_url(store: str, item: str) -> str:
    item = short_query(item)
    tpl = SEARCH_URLS.get(store.lower())
    if tpl:
        return tpl.format(q=quote_plus(item))
    # unknown store: a web search scoped to that store's name
    return "https://www.google.com/search?q=" + quote_plus(f"{item} {store}")


class HandoffStore(StoreAdapter):
    name = "handoff"

    def __init__(self, out_dir: str | Path = "handoffs"):
        self.out_dir = Path(out_dir)

    def place(self, cart: dict) -> PlaceResult:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        store = cart["store"]
        lines = [
            LineResult(l["name"], l["qty"], True, l.get("est_unit_price"), search_url(store, l["name"]))
            for l in cart["lines"]
        ]
        est = round(sum(l.qty * (l.unit_price or 0) for l in lines), 2)

        md = [f"# Cart {cart['id']} - {store}", "",
              "Approved by you. Add these items, review the total, and place the order yourself.", ""]
        for l in lines:
            price = f" (~${l.unit_price:.2f} ea)" if l.unit_price else ""
            md.append(f"- [ ] {l.qty} x {l.name[:90]}{price} - {l.detail}")
        unpriced = sum(1 for l in lines if not l.unit_price)
        extra = f" + {unpriced} unpriced item(s)" if unpriced else ""
        md += ["", f"Estimated total: ${est:.2f}{extra} (before tax/shipping)"]
        md_path = self.out_dir / f"cart-{cart['id']}.md"
        md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
        (self.out_dir / f"cart-{cart['id']}.json").write_text(
            json.dumps({"cart": cart["id"], "store": store, "estimated_total": est,
                        "lines": [l.__dict__ for l in lines]}, indent=2),
            encoding="utf-8",
        )
        return PlaceResult(
            "handed_off", None, lines, est,
            f"checklist written for {store}; finish checkout in your browser",
            str(md_path),
        )
