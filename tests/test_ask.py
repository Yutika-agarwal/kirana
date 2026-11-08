from kirana import planner


def test_parse_request_multi_store():
    w = planner.parse_request("get me oat milk and eggs from safeway, pistachios x2 from target")
    assert [(x["name"], x["qty"], x["store"]) for x in w] == [
        ("oat milk", 1, "safeway"), ("eggs", 1, "safeway"), ("pistachios", 2, "target")]


def test_parse_request_no_store():
    w = planner.parse_request("buy 3 toothpaste")
    assert w[0]["qty"] == 3 and w[0]["store"] == "any"


def test_resolve_preference_picks_most_bought(tmp_path):
    from datetime import date
    from kirana import db
    conn = db.connect(str(tmp_path / "p.db"))
    for i, d in enumerate(["2026-01-01", "2026-02-01", "2026-03-01"]):
        db.add_order(conn, "amazon", f"a{i}", d, [{"name": "Example Dark Roast Coffee", "qty": 1, "unit_price": 20.0}])
    db.add_order(conn, "amazon", "b", "2026-04-01", [{"name": "Other Brand Coffee", "qty": 1, "unit_price": 9.0}])
    best, alts = planner.resolve_preference(conn, "coffee")
    assert best["name"].startswith("Example Dark") and best["times"] == 3
    assert alts[0]["name"].startswith("Other")
    assert planner.resolve_preference(conn, "caviar")[0] is None


def test_web_chat_and_approval_flow(tmp_path):
    from datetime import date
    from kirana import db, web, approval
    conn = db.connect(str(tmp_path / "w.db"))
    for i, d in enumerate(["2026-01-01", "2026-02-01"]):
        db.add_order(conn, "amazon", f"a{i}", d, [{"name": "Example Dark Roast Coffee", "qty": 1, "unit_price": 20.0}])
    out = web.handle_chat(conn, "get me coffee", date(2026, 11, 8))
    cart = out["carts"][0]
    assert cart["store"] == "amazon" and cart["lines"][0]["short"].startswith("Example Dark")
    assert cart["status"] == "proposed" and cart["lines"][0]["link"].startswith("https://www.amazon.com/s?k=")
    res = web.handle_action(conn, "approve", cart["id"], date(2026, 11, 8))
    assert res["cart"]["status"] == "handed_off"
    assert web.handle_action(conn, "complete", cart["id"], date(2026, 11, 8))["cart"]["status"] == "placed"
