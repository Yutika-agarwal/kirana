from kirana import planner


def test_parse_request_multi_store():
    w = planner.parse_request("get me oat milk and eggs from safeway, pistachios x2 from target")
    assert [(x["name"], x["qty"], x["store"]) for x in w] == [
        ("oat milk", 1, "safeway"), ("eggs", 1, "safeway"), ("pistachios", 2, "target")]


def test_parse_request_no_store():
    w = planner.parse_request("buy 3 toothpaste")
    assert w[0]["qty"] == 3 and w[0]["store"] == "any"
