import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from kirana import approval, db, planner, predict, runner
from kirana.cli import main
from kirana.parsers import parse_email, parse_order_email_text

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 7)

# Same shape as a Target confirmation email, with made-up names and numbers.
TARGET_EMAIL = """
https://click.oe1.target.com/?qs=abc
Order #100000000000001

 Thanks for your order, Alex!

 Placed October 2, 2026

 Order total
$41.26

https://click.oe1.target.com/?qs=def
Example Brand&#174; Cotton Beanie - Ivory

 Qty: 1

 $8.40 / ea

 Arrives Wed, Oct 7

https://click.oe1.target.com/?qs=ghi
Sea Salt Roasted Pistachios - 7oz - Good &#38; Gather&#8482;

 Qty: 9

 $3.99 / ea

 Arrives Sun, Oct 18
"""


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def seed_weekly(conn, item="whole milk", every=7, count=4, last_ago=8, qty=2, price=4.29, merchant="mock"):
    for i in range(count):
        d = TODAY - timedelta(days=last_ago + every * (count - 1 - i))
        db.add_order(conn, merchant, f"{item}-{i}", d, [{"name": item, "qty": qty, "unit_price": price}])


# ---- parsing ---------------------------------------------------------------

def test_parse_target_email():
    o = parse_order_email_text(TARGET_EMAIL, "target")
    assert o["order_id"] == "100000000000001"
    assert o["placed_on"] == "2026-10-02"
    assert o["total"] == 41.26
    assert [i["qty"] for i in o["items"]] == [1, 9]
    assert o["items"][0]["name"].startswith("Example Brand")
    assert o["items"][1]["unit_price"] == 3.99


def test_parse_returns_none_when_no_items():
    assert parse_order_email_text("Order #1\nPlaced October 2, 2026\nhello", "x") is None


def test_ingest_is_idempotent(conn, tmp_path):
    f = tmp_path / "email.txt"
    f.write_text(TARGET_EMAIL)
    from kirana import ingest
    assert ingest.ingest_file(conn, str(f), "target") == (1, 0)
    assert ingest.ingest_file(conn, str(f), "target") == (0, 1)
    assert len(db.purchase_rows(conn)) == 2


# Same shape as a real Amazon confirmation: two orders in one email, one of them
# split across two shipment blocks, plus an eGift card line that must be skipped.
AMAZON_EMAIL = """
Your Orders

https://www.amazon.com/gp/css/order-history?ref_=fed_yo_default

    Thanks for your order, alex!
Ordered

Shipped

Arriving Saturday

Order #
111-1111111-1111111

View or edit order
https://www.amazon.com/gp/css/order-details?orderID=111-1111111-1111111&ref_=p_btn_fed_veo

* Welcome Garden Flag for Yard Outside Floral
  Quantity: 1
  9.99 USD

Arriving Monday

Order #
111-1111111-1111111

View or edit order

* Example Plush Elephant Toy, Gray, 1 Count (Pack of 1)
  Quantity: 2
  13.99 USD

* Amazon eGift Card - Birthday
  Quantity: 1
  100 USD

Grand Total:
59.35 USD

Arriving March 25

Order #
111-2222222-2222222

View or edit order

* Example Party Gold Boho Decorations
  Quantity: 1
  11.99 USD

Grand Total:
13.11 USD
"""


def test_parse_amazon_multi_order_email():
    orders = parse_email("amazon", AMAZON_EMAIL, "2026-03-18")
    assert [o["order_id"] for o in orders] == ["111-1111111-1111111", "111-2222222-2222222"]
    first, second = orders
    assert first["placed_on"] == "2026-03-18" and first["merchant"] == "amazon"
    # shipment blocks of the same order are merged; the eGift card is dropped
    assert [(i["name"][:20], i["qty"], i["unit_price"]) for i in first["items"]] == [
        ("Welcome Garden Flag ", 1, 9.99), ("Example Plush Elepha", 2, 13.99)]
    assert second["items"][0]["unit_price"] == 11.99


def test_amazon_email_needs_a_date():
    with pytest.raises(ValueError, match="--date"):
        parse_email("amazon", AMAZON_EMAIL)


def test_amazon_email_ingest_via_cli(tmp_path, capsys):
    f = tmp_path / "a.txt"
    f.write_text(AMAZON_EMAIL)
    base = ["--db", str(tmp_path / "k.db")]
    assert main(base + ["ingest", str(f), "--merchant", "amazon", "--date", "2026-03-18"]) == 0
    assert "2 new order(s)" in capsys.readouterr().out
    assert main(base + ["ingest", str(f), "--merchant", "amazon", "--date", "2026-03-18"]) == 0
    assert "0 new order(s)" in capsys.readouterr().out


def test_long_titles_normalize_to_stable_keys():
    a = db.normalize("Biotrue Contact Solution, Multi-Purpose Solution with Lens Case, Cleans, Conditions")
    b = db.normalize("Biotrue Contact Solution, Multi-Purpose Solution with Lens Case, Cleans, Hydrates, X")
    assert a == b and len(a) <= 60


# ---- restock suggestions ----------------------------------------------------

def test_suggest_only_old_one_time_consumables(conn):
    old = TODAY - timedelta(days=120)
    db.add_order(conn, "amazon", "1", old, [
        {"name": "Lysol Disinfectant Wipes, Pack of 4", "qty": 1, "unit_price": 11.22},
        {"name": "Apple Pencil (USB-C)", "qty": 1, "unit_price": 69}])
    db.add_order(conn, "amazon", "2", TODAY - timedelta(days=10),
                 [{"name": "Example Espresso Coffee", "qty": 1, "unit_price": 17.49}])
    names = [s.name for s in predict.suggest_restock(conn, TODAY)]
    assert names == ["Lysol Disinfectant Wipes, Pack of 4"]  # not the pencil, not the fresh coffee


def test_suggestions_are_labelled_and_opt_in(conn):
    db.add_order(conn, "amazon", "1", TODAY - timedelta(days=120),
                 [{"name": "Lysol Disinfectant Wipes, Pack of 4", "qty": 1, "unit_price": 11.22}])
    assert planner.build_carts(predict.predict_due(conn, TODAY), []) == {}  # never automatic
    carts = planner.build_carts([], [], predict.suggest_restock(conn, TODAY))
    line = carts["amazon"][0]
    assert line["source"] == "suggested" and "confirm" in line["reason"]


def test_handoff_search_uses_short_query(tmp_path, conn):
    from kirana.stores.handoff import short_query
    long = ("Biotrue Contact Solution, Multi-Purpose Solution with Lens Case, Cleans, "
            "Conditions, Hydrates, Disinfects")
    assert short_query(long) == "Biotrue Contact Solution"
    cid = planner.persist(conn, {"amazon": [{"name": long, "qty": 1, "est_unit_price": 16.99,
                                             "source": "suggested", "reason": "x"}]}, TODAY)[0]
    approval.approve(conn, cid)
    runner.place_cart(conn, cid, out_dir=str(tmp_path), today=TODAY)
    assert "amazon.com/s?k=Biotrue+Contact+Solution" in (tmp_path / f"cart-{cid}.md").read_text()


# ---- prediction ------------------------------------------------------------

def test_weekly_item_is_due(conn):
    seed_weekly(conn)
    preds = predict.predict_due(conn, TODAY)
    assert len(preds) == 1
    p = preds[0]
    assert p.interval_days == 7 and p.qty == 2
    assert p.status == "overdue"  # last bought 8 days ago, cadence 7
    assert p.confidence in ("high", "medium")


def test_not_due_item_is_excluded(conn):
    seed_weekly(conn, every=30, count=3, last_ago=2)
    assert predict.predict_due(conn, TODAY, horizon_days=7) == []


def test_one_off_items_are_not_predicted(conn):
    db.add_order(conn, "target", "1", TODAY - timedelta(days=20),
                 [{"name": "Diya kit", "qty": 9, "unit_price": 5.0}])
    assert predict.predict_due(conn, TODAY) == []
    assert predict.one_off_count(conn) == 1


def test_same_day_duplicates_do_not_create_cadence(conn):
    d = TODAY - timedelta(days=10)
    db.add_order(conn, "target", "a", d, [{"name": "Ornament", "qty": 1, "unit_price": 3}])
    db.add_order(conn, "target", "b", d, [{"name": "Ornament", "qty": 1, "unit_price": 3}])
    assert predict.recurring_items(conn) == []


# ---- planning --------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("oat milk x2 @ safeway", {"name": "oat milk", "qty": 2, "store": "safeway"}),
    ("3 bananas from Trader Joe's", {"name": "bananas", "qty": 3, "store": "trader joe's"}),
    ("paper towels", {"name": "paper towels", "qty": 1, "store": "any"}),
    ("2 x ghee at costco", {"name": "ghee", "qty": 2, "store": "costco"}),
])
def test_parse_want(text, expected):
    assert planner.parse_want(text) == expected


def test_carts_split_by_store_and_mix_sources(conn):
    seed_weekly(conn, merchant="mock")
    preds = predict.predict_due(conn, TODAY)
    carts = planner.build_carts(preds, [planner.parse_want("tea @ costco"),
                                         planner.parse_want("jam @ mock")])
    assert set(carts) == {"mock", "costco"}
    assert {l["source"] for l in carts["mock"]} == {"predicted", "requested"}


def test_want_is_priced_from_history_and_unpriced_is_flagged(conn):
    from kirana import ingest
    ingest.ingest_file(conn, str(ROOT / "tests" / "fixtures" / "sample_orders.json"))
    assert planner.estimate_price(conn, "target", "pistachios") == 3.99
    assert planner.estimate_price(conn, "safeway", "pistachios") is None
    lines = [{"name": "a", "qty": 2, "est_unit_price": 3.99}, {"name": "b", "qty": 1, "est_unit_price": None}]
    assert planner.total_label(lines) == "$7.98 + 1 unpriced item(s)"


def test_store_less_cart_cannot_be_approved(conn):
    ids = planner.persist(conn, planner.build_carts([], [planner.parse_want("tea")]), TODAY)
    with pytest.raises(approval.ApprovalError, match="no store"):
        approval.approve(conn, ids[0])


# ---- approval gate + placing ----------------------------------------------

def make_cart(conn, store="mock", wants=("whole milk 1 gal x2 @ mock", "ghee 16oz @ mock")):
    carts = planner.build_carts([], [planner.parse_want(w.replace("@ mock", f"@ {store}")) for w in wants])
    return planner.persist(conn, carts, TODAY)[0]


def test_cannot_place_unapproved_cart(conn):
    cid = make_cart(conn)
    with pytest.raises(approval.ApprovalError, match="not 'approved'"):
        runner.place_cart(conn, cid, today=TODAY)
    assert db.get_cart(conn, cid)["status"] == "proposed"


def test_mock_place_reports_unavailable_and_records_history(conn):
    cid = make_cart(conn)
    approval.approve(conn, cid)
    result = runner.place_cart(conn, cid, today=TODAY)
    assert result.outcome == "placed"
    cart = db.get_cart(conn, cid)
    assert cart["status"] == "placed"
    by = {l["name"]: l for l in cart["lines"]}
    assert by["whole milk 1 gal"]["result"] == "ok"
    assert by["ghee 16oz"]["result"] == "unavailable"  # reported, never silently dropped
    names = {r["name"] for r in db.purchase_rows(conn)}
    assert names == {"whole milk 1 gal"}  # only what was actually bought


def test_cannot_place_twice(conn):
    cid = make_cart(conn)
    approval.approve(conn, cid)
    runner.place_cart(conn, cid, today=TODAY)
    with pytest.raises(approval.ApprovalError):
        runner.place_cart(conn, cid, today=TODAY)


def test_all_unavailable_fails_and_can_be_retried(conn):
    cid = make_cart(conn, wants=("ghee 16oz @ mock",))
    approval.approve(conn, cid)
    assert runner.place_cart(conn, cid, today=TODAY).outcome == "failed"
    assert db.get_cart(conn, cid)["status"] == "failed"
    approval.approve(conn, cid)  # allowed again after failure


def test_real_store_hands_off_instead_of_buying(conn, tmp_path):
    cid = make_cart(conn, store="target", wants=("pistachios x2 @ target",))
    approval.approve(conn, cid)
    result = runner.place_cart(conn, cid, out_dir=str(tmp_path), today=TODAY)
    assert result.outcome == "handed_off"
    md = (tmp_path / f"cart-{cid}.md").read_text()
    assert "target.com/s?searchTerm=pistachios" in md
    assert db.get_cart(conn, cid)["status"] == "handed_off"
    assert db.purchase_rows(conn) == []  # not history until you finish checkout


def test_unknown_store_gets_a_search_link(tmp_path, conn):
    cid = make_cart(conn, store="patel brothers", wants=("toor dal @ patel brothers",))
    approval.approve(conn, cid)
    runner.place_cart(conn, cid, out_dir=str(tmp_path), today=TODAY)
    assert "google.com/search?q=toor+dal+patel+brothers" in (tmp_path / f"cart-{cid}.md").read_text()


# ---- CLI end to end --------------------------------------------------------

def test_cli_end_to_end(tmp_path, capsys):
    dbp = str(tmp_path / "k.db")
    base = ["--db", dbp, "--today", TODAY.isoformat()]
    assert main(base + ["ingest", str(ROOT / "tests" / "fixtures" / "sample_orders.json")]) == 0
    capsys.readouterr()

    # real history has no recurring items -> says so honestly
    assert main(base + ["predict"]) == 0
    assert "bought only once" in capsys.readouterr().out

    # but any item at any store can be requested
    assert main(base + ["plan", "--no-history", "--want", "pistachios x2 @ target",
                        "--want", "oat milk @ safeway"]) == 0
    capsys.readouterr()
    assert main(base + ["carts"]) == 0
    assert capsys.readouterr().out.count("proposed") == 2

    # non-interactive approve without --yes is refused
    assert main(base + ["approve", "1"]) == 1
    capsys.readouterr()
    assert main(base + ["place", "1", "--out-dir", str(tmp_path / "h")]) == 1  # gate
    assert "not 'approved'" in capsys.readouterr().err

    assert main(base + ["approve", "1", "--yes"]) == 0
    assert main(base + ["place", "1", "--out-dir", str(tmp_path / "h")]) == 0
    assert (tmp_path / "h" / "cart-1.md").exists()
    assert main(base + ["complete", "1"]) == 0
    capsys.readouterr()
    assert main(base + ["history", "--merchant", "target"]) == 0
    assert "pistachios" in capsys.readouterr().out


def test_demo_runs_clean():
    out = subprocess.run([sys.executable, "-m", "kirana", "demo"], cwd=ROOT,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "refused as expected" in out.stdout
    assert "ghee 16oz" in out.stdout and "MISS" in out.stdout
