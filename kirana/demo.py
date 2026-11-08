"""End-to-end demo on SYNTHETIC grocery history and a FAKE store.

Nothing here touches your real data or any real store. The demo auto-approves
its mock cart purely to show the full flow; the real CLI never does.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import approval, db, planner, predict, runner


def _synthetic_orders(today: date):
    """Milk weekly, eggs weekly, rice every ~3 weeks, ghee monthly, bread twice."""
    def series(item, price, qty, every, count, last_ago):
        return [(item, price, qty, today - timedelta(days=last_ago + every * i))
                for i in range(count)][::-1]

    rows = []
    rows += series("whole milk 1 gal", 4.29, 2, 7, 5, 8)
    rows += series("eggs dozen", 3.99, 1, 7, 5, 9)
    rows += series("basmati rice 5lb", 9.99, 1, 21, 3, 22)
    rows += series("ghee 16oz", 12.99, 1, 30, 3, 31)
    rows += series("sourdough bread", 5.49, 1, 14, 2, 2)  # not due yet
    return rows


def run_demo() -> int:
    today = date(2026, 11, 7)
    conn = db.connect(":memory:")
    for n, (item, price, qty, d) in enumerate(_synthetic_orders(today)):
        db.add_order(conn, "mock", f"D{n}", d, [{"name": item, "qty": qty, "unit_price": price}])

    print("== 1. history (synthetic) ==")
    print(f"{len(db.purchase_rows(conn))} purchase lines across "
          f"{len(predict.recurring_items(conn))} recurring items\n")

    print("== 2. what is due ==")
    preds = predict.predict_due(conn, today, horizon_days=7)
    for p in preds:
        print(f"  {p.status:<9} {p.qty} x {p.name}  every ~{p.interval_days}d, due {p.due_date}")

    print("\n== 3. plan: due items + a one-off request at the same store ==")
    carts = planner.build_carts(preds, [planner.parse_want("oat milk 64oz x2 @ mock")])
    ids = planner.persist(conn, carts, today)
    cart = db.get_cart(conn, ids[0])
    for l in cart["lines"]:
        print(f"  {l['qty']} x {l['name']}  [{l['source']}]")

    print("\n== 4. gate: placing before approval must fail ==")
    try:
        runner.place_cart(conn, ids[0], today=today)
        print("  ERROR: placed without approval")
        return 1
    except approval.ApprovalError as e:
        print(f"  refused as expected: {e}")

    print("\n== 5. approve (demo only) and place at the mock store ==")
    approval.approve(conn, ids[0])
    result = runner.place_cart(conn, ids[0], today=today)
    print(f"  {result.outcome}: {result.message}  confirmation {result.confirmation}")
    for l in result.lines:
        print(f"    [{'ok ' if l.ok else 'MISS'}] {l.qty} x {l.name} {l.detail}")

    print("\n== 6. the order feeds back into history ==")
    print(f"  {len(db.purchase_rows(conn))} purchase lines now")
    return 0
