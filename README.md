# kirana

A shopping agent that reorders from your purchase history. It learns what you
buy repeatedly, works out when it is due, builds a cart for any store, and
**waits for your approval** before anything is bought.

```
ingest order emails -> predict what is due -> plan carts -> approve -> place / hand off
```

## Chat UI

```bash
python3 -m kirana serve      # then open http://127.0.0.1:8765
```

Type what you want ("coffee and pistachios", "what's due?"). It picks the product you usually
buy from your history and builds a cart. Pressing **Approve cart** is the approval; checkout stays with you.
The server only listens on your own machine.

## Quick start

```bash
python3 -m kirana demo                       # whole flow on synthetic data + a fake store
python3 -m kirana ask "get me oat milk and eggs from safeway, pistachios x2 from target"
python3 -m kirana ingest your_orders.json
python3 -m kirana predict
python3 -m kirana plan --want "oat milk x2 @ safeway" --want "pistachios x2 @ target"
python3 -m kirana approve 1                  # shows the cart, asks y/N (or --yes)
python3 -m kirana place 1                    # checklist for real stores, mock store buys
python3 -m kirana complete 1                 # after you finish checkout yourself
```

Requires Python 3.10+, no dependencies. Run tests with `python3 -m pytest`.
Data lives in `~/.kirana/kirana.db` (override with `--db` or `$KIRANA_DB`).

## How it decides what to buy

- An item counts as **recurring** once it was bought on 2+ distinct dates
  (`--min-orders`). The cadence is the median gap; the quantity is the median
  quantity. Confidence is high/medium/low from how regular the gaps are.
- Items due now or within `--horizon` days (default 7) go into a cart for the
  store they were bought from.
- Items bought once are **not** predicted. If your history has no repeats,
  `predict` says so; add items yourself with `--want`, or look at `suggest`.
- `--want "name [xN] [@ store]"` works for **any store**, with or without
  history there. Prices come from your history when the item matches,
  otherwise the line is flagged "unpriced".

## Safety model

- `plan` never buys. `place` refuses unless the cart is `approved`
  (`proposed -> approved -> placed | handed_off`), and approval needs a
  y/N prompt or an explicit `--yes`.
- Real stores use the **handoff** adapter: it writes `handoffs/cart-N.md`
  with a search link per item. You (or a browser agent signed in as you) fill
  the cart; payment and the final "Place order" click stay with you. Run
  `kirana complete N` afterwards so the purchase becomes history.
- The **mock** store (`--want "x @ mock"`) exists for end-to-end tests and moves no money.
- Items that are unavailable are reported on the cart, never silently dropped.

## Getting history from Gmail

`ingest` reads `.json`, `.csv` (`merchant,order_id,date,item,qty,unit_price`),
or a plain-text order email:

```bash
kirana ingest email.txt --merchant target
kirana ingest email.txt --merchant amazon --date 2026-03-18   # Amazon emails carry no order date in the body
```

Amazon "Ordered:" confirmations (`auto-confirm@amazon.com`) are parsed directly:
several orders per email, shipment blocks merged by order number, gift cards and
non-USD lines skipped, item prices treated as unit prices. Target's format
(`Qty: N` / `$X / ea`) is parsed too. HTML-only merchants such as Old Navy show
no items in plain text and need their own parser or a CSV.

Your own exported history (e.g. `data/*.json`) is personal data and is git-ignored.

## Restock suggestions (guesses)

If nothing in your history repeats, `predict` has nothing to learn from.
`kirana suggest` lists consumables (wipes, vitamins, coffee, ...) bought **once**
45-400 days ago, matched on the product's leading clause only. These are guesses,
so they never enter a cart unless you pass `plan --include-suggested`, and each
line is labelled "confirm you need it". They still go through approval.

## Layout

```
kirana/parsers.py   email/JSON/CSV -> orders       kirana/predict.py   cadence + due dates
kirana/db.py        sqlite store                   kirana/planner.py   carts, --want parsing
kirana/approval.py  the gate / state machine       kirana/runner.py    place + record history
kirana/stores/      mock + browser handoff         kirana/cli.py       commands
```

## Adding a store adapter

Subclass `StoreAdapter`, implement `place(cart) -> PlaceResult`, and register
it in `kirana/stores/__init__.py`. Keep the rule: nothing is submitted
without an approved cart.
