"""Place an approved cart through a store adapter and record the outcome."""
from __future__ import annotations

from datetime import date

from . import approval, db
from .stores import PlaceResult, get_adapter


def place_cart(conn, cart_id: int, adapter_kind: str = "auto", out_dir: str = "handoffs",
               today: date | None = None) -> PlaceResult:
    cart = approval.require_approved(conn, cart_id)  # the gate
    adapter = get_adapter(cart["store"], adapter_kind, out_dir)
    result = adapter.place(cart)

    # record per-line outcomes on the cart
    by_name = {l.name: l for l in result.lines}
    lines = []
    for line in cart["lines"]:
        r = by_name.get(line["name"])
        line = dict(line)
        if r is not None:
            line["result"] = "ok" if r.ok else "unavailable"
            line["detail"] = r.detail
            if r.unit_price:
                line["final_unit_price"] = r.unit_price
        lines.append(line)
    db.update_cart_lines(conn, cart_id, lines)

    note = result.message
    if result.confirmation:
        note += f" [confirmation {result.confirmation}]"
    if result.artifact:
        note += f" [{result.artifact}]"
    approval.transition(conn, cart_id, result.outcome, note)

    # a real placed order becomes purchase history, so cadence keeps learning
    if result.outcome == "placed":
        items = [
            {"name": r.name, "qty": r.qty, "unit_price": r.unit_price or 0.0}
            for r in result.lines if r.ok
        ]
        db.add_order(
            conn, cart["store"], result.confirmation or f"cart-{cart_id}",
            (today or date.today()).isoformat(), items, total=result.total, source="kirana",
        )
    return result
