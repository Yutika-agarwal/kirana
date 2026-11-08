# Build your own kirana agent

kirana is a small, dependency-free Python project. Fork it and change what you like.

## 1. Get your order history in
`kirana ingest` reads `.json`, `.csv` (`merchant,order_id,date,item,qty,unit_price`) or a plain-text
order email. See `examples/orders.example.json` for the JSON shape. Put your own files in `data/`
(git-ignored).

Ways to collect history:
- **Order emails:** forward or export confirmation emails and ingest them (`--merchant amazon --date YYYY-MM-DD`).
- **Store account pages:** copy order history from your order page into a CSV.
- **With an AI assistant that can read your email/browser:** ask it to read your order emails or
  order-history page and write the JSON file for you, then run `kirana ingest`.

## 2. Ask for things
```bash
python3 -m kirana ask "coffee and oat milk"     # cart from your usual products
python3 -m kirana predict                       # recurring items that look due
python3 -m kirana serve                         # same thing in a chat UI
```
How "your usual" is chosen: every word of your request must appear in a past item's title; ties go
to what you bought on the most separate days, then the most recent. Nothing in history? kirana says so
and links a store search instead of guessing.

## 3. Approve, then check out yourself
`approve` (or the UI button) writes a checklist with a link per item. You add them to the store cart
and pay. `kirana complete N` records it so future suggestions learn from it.

## Customize
| Want to... | Edit |
|---|---|
| Add a store | `kirana/stores/handoff.py` (`SEARCH_URLS`), or subclass `StoreAdapter` in `kirana/stores/` |
| Support another order-email format | `kirana/parsers.py` (see `parse_amazon_email_text`) |
| Change what counts as a restock | `kirana/predict.py` (`CONSUMABLE`, `predict_due` thresholds) |
| Change how requests are understood | `kirana/planner.py` (`parse_request`, `resolve_preference`) |
| Restyle the chat | `kirana/static/index.html` |

## Letting an AI assistant fill the cart (optional)
If your assistant can use a browser where you are already signed in to the store, give it a prompt like:

> Use kirana. Read my order history, ingest it, run `kirana ask "<what I want>"`, and add the
> resulting items to my store cart. Stop before checkout and show me the total.

Rules worth keeping in your own version: never put store passwords or payment details in kirana,
the assistant, or this repo; keep the approval gate (`place` refuses unless a cart is approved);
and leave the final "Place order" click to the human.

## Safety checklist before you publish your fork
- `git status` shows no `data/`, `*.db`, `handoffs/` or `.env` files.
- Your name, addresses and order numbers are not in tests or examples.
