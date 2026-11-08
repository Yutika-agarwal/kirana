"""The approval gate. Nothing is bought unless a cart was explicitly approved."""
from __future__ import annotations

from . import db

# status -> allowed next statuses
TRANSITIONS = {
    "proposed": {"approved", "cancelled"},
    "approved": {"placed", "handed_off", "failed", "cancelled"},
    "placed": set(),
    "handed_off": {"placed"},  # user finished checkout in the browser
    "failed": {"approved", "cancelled"},
    "cancelled": set(),
}


class ApprovalError(Exception):
    pass


def _cart(conn, cart_id):
    cart = db.get_cart(conn, cart_id)
    if cart is None:
        raise ApprovalError(f"no cart with id {cart_id}")
    return cart


def transition(conn, cart_id: int, new_status: str, note: str | None = None) -> dict:
    cart = _cart(conn, cart_id)
    if new_status not in TRANSITIONS[cart["status"]]:
        raise ApprovalError(
            f"cart {cart_id} is '{cart['status']}'; cannot move to '{new_status}'"
        )
    db.set_cart_status(conn, cart_id, new_status, note)
    return _cart(conn, cart_id)


def approve(conn, cart_id: int) -> dict:
    cart = _cart(conn, cart_id)
    if cart["store"] == "any":
        raise ApprovalError(
            f"cart {cart_id} has no store yet; re-plan with '@ store' so kirana knows where to buy"
        )
    if not cart["lines"]:
        raise ApprovalError(f"cart {cart_id} is empty")
    return transition(conn, cart_id, "approved")


def require_approved(conn, cart_id: int) -> dict:
    cart = _cart(conn, cart_id)
    if cart["status"] != "approved":
        raise ApprovalError(
            f"cart {cart_id} is '{cart['status']}', not 'approved'. "
            f"Review it with `kirana show {cart_id}` and approve it first."
        )
    return cart
