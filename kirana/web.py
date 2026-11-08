"""Local chat UI: `kirana serve` -> http://127.0.0.1:8765

Same engine as the CLI. Typing a request builds a proposed cart from your usual
products; nothing is bought. Pressing Approve is the approval; checkout stays with you.
"""
from __future__ import annotations

import json
import re
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import approval, db, planner, predict, runner
from .stores.handoff import search_url

STATIC = Path(__file__).parent / "static" / "index.html"
DUE_RE = re.compile(r"\b(due|running low|run out|need to (re)?order|what should i (buy|order)|restock)\b", re.I)


def cart_json(cart: dict) -> dict:
    lines = []
    for l in cart["lines"]:
        lines.append({
            "name": l["name"], "short": planner.display_name(l["name"], 70), "qty": l["qty"],
            "price": l.get("est_unit_price"), "reason": l.get("reason", ""), "source": l.get("source", ""),
            "link": search_url(cart["store"], l["name"]) if cart["store"] != "any" else None,
        })
    return {"id": cart["id"], "store": cart["store"], "status": cart["status"],
            "total": planner.total_label(cart["lines"]), "lines": lines}


def handle_chat(conn, message: str, today: date) -> dict:
    message = message.strip()
    if not message:
        return {"reply": "Tell me what you want, e.g. “coffee and pistachios”.", "carts": []}
    if DUE_RE.search(message):
        preds = predict.predict_due(conn, today, horizon_days=14)
        if not preds:
            return {"reply": "Nothing looks due. I only flag items you buy on a regular cycle; "
                             "ask for anything directly and I'll use your usual.", "carts": []}
        carts = planner.build_carts(preds, [])
        ids = planner.persist(conn, carts, today)
        return {"reply": f"{len(preds)} item(s) look due based on your buying cycle:",
                "carts": [cart_json(db.get_cart(conn, i)) for i in ids]}
    wants = planner.parse_request(message)
    notes = []
    for w in wants:
        best, alts = planner.resolve_preference(conn, w["name"], w["store"])
        if best:
            w.update(name=best["name"], est_unit_price=best["unit_price"] or None,
                     reason=f"your usual: bought {best['times']}x, last {best['last']}")
            if w["store"] == planner.DEFAULT_STORE:
                w["store"] = best["store"]
            if alts:
                notes.append("Also matched for “%s”: %s" % (
                    w["name"][:30], "; ".join(planner.display_name(a["name"], 45) for a in alts[:2])))
        else:
            if w["store"] == planner.DEFAULT_STORE:
                w["store"] = "amazon"
            w["est_unit_price"] = None
            w["reason"] = "no history for this - I'll link a store search, you pick"
    ids = planner.persist(conn, planner.build_carts([], wants), today)
    carts = [cart_json(db.get_cart(conn, i)) for i in ids]
    return {"reply": "Here's what I'd get. Nothing is bought until you approve:", "carts": carts, "notes": notes}


def handle_action(conn, action: str, cart_id: int, today: date) -> dict:
    if action == "cancel":
        approval.transition(conn, cart_id, "cancelled")
        return {"cart": cart_json(db.get_cart(conn, cart_id)), "message": "Cancelled."}
    if action == "approve":
        approval.approve(conn, cart_id)
        res = runner.place_cart(conn, cart_id, "auto", "handoffs", today)
        cart = cart_json(db.get_cart(conn, cart_id))
        return {"cart": cart, "message": res.message}
    if action == "complete":  # you finished checkout yourself
        cart = db.get_cart(conn, cart_id)
        approval.transition(conn, cart_id, "placed", "completed by user in browser")
        items = [{"name": l["name"], "qty": l["qty"], "unit_price": l.get("est_unit_price") or 0.0}
                 for l in cart["lines"]]
        db.add_order(conn, cart["store"], f"cart-{cart_id}", today.isoformat(), items, source="kirana")
        return {"cart": cart_json(db.get_cart(conn, cart_id)), "message": "Recorded as bought."}
    raise ValueError(f"unknown action {action}")


def make_handler(db_path: str | None, today_override: date | None):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self._send(200, STATIC.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/carts":
                conn = db.connect(db_path)
                try:
                    return self._send(200, [cart_json(c) for c in db.list_carts(conn)
                                            if c["status"] in ("proposed", "handed_off")])
                finally:
                    conn.close()
            self._send(404, {"error": "not found"})

        def do_POST(self):
            # only accept same-origin JSON so other websites can't drive the cart
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if origin and origin.split("://", 1)[-1] != host:
                return self._send(403, {"error": "bad origin"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            today = today_override or date.today()
            conn = db.connect(db_path)
            try:
                if self.path == "/api/chat":
                    return self._send(200, handle_chat(conn, str(req.get("message", "")), today))
                if self.path == "/api/action":
                    return self._send(200, handle_action(conn, str(req.get("action")), int(req["id"]), today))
                self._send(404, {"error": "not found"})
            except (approval.ApprovalError, ValueError, KeyError) as e:
                self._send(400, {"error": str(e)})
            finally:
                conn.close()
    return H


def serve(db_path=None, port=8765, today_override=None):
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(db_path, today_override))
    print(f"kirana chat running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
