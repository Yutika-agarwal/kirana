"""kirana command line."""
from __future__ import annotations

import argparse
import sys
from datetime import date

from . import approval, db, ingest, planner, predict, runner


def _today(args) -> date:
    return date.fromisoformat(args.today) if getattr(args, "today", None) else date.today()


def _fmt_cart(cart: dict) -> str:
    out = [f"Cart {cart['id']}  store={cart['store']}  status={cart['status']}  created={cart['created_on']}"]
    for l in cart["lines"]:
        price = f"~${l['est_unit_price']:.2f}" if l.get("est_unit_price") else "price unknown"
        res = f"  -> {l['result']}" if l.get("result") else ""
        out.append(f"  {l['qty']} x {planner.display_name(l['name'])}  ({price} ea)  "
                   f"[{l['source']}] {l['reason']}{res}")
    out.append(f"  estimated total: {planner.total_label(cart['lines'])}")
    if cart.get("note"):
        out.append(f"  note: {cart['note']}")
    return "\n".join(out)


def cmd_ingest(conn, args):
    total_added = 0
    for path in args.files:
        try:
            added, skipped = ingest.ingest_file(conn, path, args.merchant, args.date)
        except (ValueError, OSError, KeyError) as e:
            print(f"  skipped {path}: {e}", file=sys.stderr)
            continue
        total_added += added
        print(f"  {path}: {added} new order(s), {skipped} already known")
    print(f"ingested {total_added} new order(s)")
    return 0


def cmd_history(conn, args):
    rows = db.purchase_rows(conn, args.merchant)
    if not rows:
        print("no purchase history yet - run `kirana ingest FILE...`")
        return 0
    for r in rows:
        print(f"{r['placed_on']}  {r['merchant']:<10} {r['qty']:>3} x {r['name']}  ${r['unit_price']:.2f}")
    print(f"{len(rows)} line(s)")
    return 0


def cmd_predict(conn, args):
    today = _today(args)
    preds = predict.predict_due(conn, today, args.horizon, args.min_orders, args.merchant)
    if not preds:
        one_offs = predict.one_off_count(conn)
        print("nothing predicted as due.")
        if one_offs:
            print(
                f"{one_offs} item(s) in your history were bought only once, so there is no "
                f"cadence to learn from. Add more history, or request items directly with "
                f"`kirana plan --want \"oat milk x2 @ safeway\"`."
            )
        return 0
    for p in preds:
        print(f"{p.status:<9} {p.merchant:<10} {p.qty} x {p.name}  every ~{p.interval_days}d, "
              f"due {p.due_date.isoformat()}  ({p.confidence} confidence, {p.orders} orders)")
    return 0


def cmd_suggest(conn, args):
    sugg = predict.suggest_restock(conn, _today(args), args.min_days, merchant=args.merchant)
    if not sugg:
        print("no one-time consumables to suggest")
        return 0
    print("These were each bought once, so this is a guess, not a prediction:")
    for s in sugg:
        print(f"  {s.days_ago:>4}d ago  {s.merchant:<8} {s.qty} x {s.name[:70]}  ${s.unit_price:.2f}")
    print("Add them to a cart with: kirana plan --no-history --include-suggested")
    return 0


def cmd_plan(conn, args):
    today = _today(args)
    preds = [] if args.no_history else predict.predict_due(
        conn, today, args.horizon, args.min_orders, args.merchant)
    wants = [planner.parse_want(w) for w in args.want or []]
    for w in wants:  # price from your own history when the item matches
        w["est_unit_price"] = planner.estimate_price(conn, w["store"], w["name"])
    sugg = predict.suggest_restock(conn, today, merchant=args.merchant) if args.include_suggested else []
    carts = planner.build_carts(preds, wants, sugg)
    if not carts:
        print("nothing to buy: no recurring items are due and no --want requests were given.")
        return 0
    ids = planner.persist(conn, carts, today)
    for cid in ids:
        print(_fmt_cart(db.get_cart(conn, cid)))
        print()
    print(f"{len(ids)} cart(s) proposed. Nothing has been bought. "
          f"Review, then: kirana approve ID && kirana place ID")
    return 0


def cmd_ask(conn, args):
    """Plain-English request -> your usual product from history -> proposed cart (never buys)."""
    today = _today(args)
    wants = planner.parse_request(" ".join(args.prompt))
    for w in wants:
        best, alts = planner.resolve_preference(conn, w["name"], w["store"])
        if best:
            w.update(name=best["name"], est_unit_price=best["unit_price"] or None,
                     reason=f"your usual: bought {best['times']}x, last {best['last']}")
            if w["store"] == planner.DEFAULT_STORE:
                w["store"] = best["store"]
            for a in alts:
                print(f"  (also matched: {planner.display_name(a['name'], 60)} - {a['times']}x)")
        else:
            if w["store"] == planner.DEFAULT_STORE:
                w["store"] = "amazon"
            w["est_unit_price"] = None
            w["reason"] = "no history for this - will search the store, pick one yourself"
    preds = predict.predict_due(conn, today) if args.with_due else []
    carts = planner.build_carts(preds, wants)
    ids = planner.persist(conn, carts, today)
    for cid in ids:
        print(_fmt_cart(db.get_cart(conn, cid)))
        print()
    print(f"{len(ids)} cart(s) proposed. Nothing has been bought. "
          f"Approve with: kirana approve ID, then kirana place ID")
    return 0


def cmd_carts(conn, args):
    carts = db.list_carts(conn)
    if not carts:
        print("no carts yet")
    for c in carts:
        print(f"{c['id']:>3}  {c['status']:<10} {c['store']:<12} {len(c['lines'])} line(s)  "
              f"~{planner.total_label(c['lines'])}")
    return 0


def cmd_show(conn, args):
    cart = db.get_cart(conn, args.cart_id)
    if not cart:
        print(f"no cart {args.cart_id}", file=sys.stderr)
        return 1
    print(_fmt_cart(cart))
    return 0


def cmd_approve(conn, args):
    cart = db.get_cart(conn, args.cart_id)
    if not cart:
        print(f"no cart {args.cart_id}", file=sys.stderr)
        return 1
    print(_fmt_cart(cart))
    if not args.yes:
        if not sys.stdin.isatty():
            print("\nnot approved: pass --yes to approve non-interactively", file=sys.stderr)
            return 1
        if input("\nApprove this cart? [y/N] ").strip().lower() not in ("y", "yes"):
            print("not approved")
            return 1
    approval.approve(conn, args.cart_id)
    print(f"cart {args.cart_id} approved")
    return 0


def cmd_cancel(conn, args):
    approval.transition(conn, args.cart_id, "cancelled")
    print(f"cart {args.cart_id} cancelled")
    return 0


def cmd_place(conn, args):
    result = runner.place_cart(conn, args.cart_id, args.adapter, args.out_dir, _today(args))
    print(f"cart {args.cart_id}: {result.outcome} - {result.message}")
    for l in result.lines:
        mark = "ok " if l.ok else "MISS"
        print(f"  [{mark}] {l.qty} x {planner.display_name(l.name)}  {l.detail}")
    if result.total is not None:
        print(f"  total: ${result.total:.2f}")
    if result.confirmation:
        print(f"  confirmation: {result.confirmation}")
    if result.artifact:
        print(f"  checklist: {result.artifact}")
    return 0 if result.outcome != "failed" else 2


def cmd_complete(conn, args):
    """Mark a handed-off cart as bought once you finished checkout yourself."""
    approval.transition(conn, args.cart_id, "placed", "completed by user in browser")
    cart = db.get_cart(conn, args.cart_id)
    items = [{"name": l["name"], "qty": l["qty"], "unit_price": l.get("est_unit_price") or 0.0}
             for l in cart["lines"]]
    db.add_order(conn, cart["store"], f"cart-{args.cart_id}", _today(args).isoformat(),
                 items, source="kirana")
    print(f"cart {args.cart_id} recorded as purchased; it now counts as purchase history")
    return 0


def cmd_serve(args):
    from .web import serve
    return serve(args.db, args.port, date.fromisoformat(args.today) if args.today else None)


def cmd_demo(args):
    from .demo import run_demo
    return run_demo()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kirana", description="Shopping agent driven by your purchase history.")
    p.add_argument("--db", help="database path (default ~/.kirana/kirana.db or $KIRANA_DB)")
    p.add_argument("--today", help="override today's date (YYYY-MM-DD), for testing")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="load orders from .json/.csv or plain-text order emails")
    s.add_argument("files", nargs="+")
    s.add_argument("--merchant", help="merchant name, required for plain-text emails")
    s.add_argument("--date", help="email sent date YYYY-MM-DD (needed for amazon emails)")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("history", help="show purchase history")
    s.add_argument("--merchant")
    s.set_defaults(fn=cmd_history)

    for name, fn, help_ in (("predict", cmd_predict, "show recurring items that are due"),
                            ("plan", cmd_plan, "propose carts (predicted + requested items)")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--horizon", type=int, default=7, help="include items due within N days")
        s.add_argument("--min-orders", type=int, default=2, help="orders needed to call an item recurring")
        s.add_argument("--merchant")
        if name == "plan":
            s.add_argument("--want", action="append",
                           help='any item, any store: "oat milk x2 @ safeway" (repeatable)')
            s.add_argument("--no-history", action="store_true", help="only plan --want items")
            s.add_argument("--include-suggested", action="store_true",
                           help="also add one-time consumables from `kirana suggest` (guesses)")
        s.set_defaults(fn=fn)

    s = sub.add_parser("suggest", help="one-time consumables you may need to restock (guesses)")
    s.add_argument("--min-days", type=int, default=45)
    s.add_argument("--merchant")
    s.set_defaults(fn=cmd_suggest)

    s = sub.add_parser("ask", help='plain-English request: kirana ask "get me oat milk from safeway and pistachios x2 from target"')
    s.add_argument("prompt", nargs="+")
    s.add_argument("--with-due", action="store_true", help="also add recurring items that are due")
    s.set_defaults(fn=cmd_ask)

    sub.add_parser("carts", help="list carts").set_defaults(fn=cmd_carts)
    for name, fn, help_ in (("show", cmd_show, "show a cart"),
                            ("cancel", cmd_cancel, "cancel a cart")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("cart_id", type=int)
        s.set_defaults(fn=fn)

    s = sub.add_parser("approve", help="approve a cart (required before placing)")
    s.add_argument("cart_id", type=int)
    s.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("place", help="place an approved cart (mock) or hand it off to your browser")
    s.add_argument("cart_id", type=int)
    s.add_argument("--adapter", choices=["auto", "mock", "handoff"], default="auto")
    s.add_argument("--out-dir", default="handoffs")
    s.set_defaults(fn=cmd_place)

    s = sub.add_parser("complete", help="record a handed-off cart as bought after you checked out")
    s.add_argument("cart_id", type=int)
    s.set_defaults(fn=cmd_complete)

    s = sub.add_parser("serve", help="open the chat UI in your browser (http://127.0.0.1:8765)")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_serve, no_db=True)

    sub.add_parser("demo", help="run the whole flow on synthetic data and a fake store").set_defaults(fn=cmd_demo, no_db=True)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "no_db", False):
        return args.fn(args)
    conn = db.connect(args.db)
    try:
        return args.fn(conn, args)
    except approval.ApprovalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
