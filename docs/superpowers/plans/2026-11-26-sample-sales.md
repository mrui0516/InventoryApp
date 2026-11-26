# Sample Sales (never blocked, always marked) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every sale path sells the shop samples when that is what is left, splitting the sample units into their own `is_sample` line, and every record and document marks them.

**Architecture:** One stock function decides how a line splits into full-price and sample parts at the moment it is consumed (`consume_for_sale`); Sell and order corrections both call it and write one `Sale` per part. Pedido gains per-line "Tester" and stops merging rows; the flag travels draft → Sell cart → sale, and to the B2B order.

**Tech Stack:** Django 5.2, SQLite, vanilla JS in templates, reportlab PDFs.

**Spec:** `docs/superpowers/specs/2026-11-26-sample-sales-design.md`

## Global Constraints

- Block only when all shops together hold fewer units than needed: `Not enough stock for {product.display_name}. Needed {n}, both shops have {m}.`
- Staff UI word: "Sample". Customer documents (Pedido page and PDF, B2B order, receipt): "Tester". Never "Amostra".
- A split-off sample line keeps the original unit price and payment method and follows the original line.
- Shopify stock rules, `samples_per_shop`, profit/cost rules: unchanged.
- Tests: `PYTHONIOENCODING=utf-8 /c/Users/maoru/AppData/Local/Programs/Python/Python313/python.exe manage.py test stock restock_alerts --noinput`; commit only when the log has `^OK`; `makemigrations --check --dry-run` clean.
- Commit message trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- No shops configured (`store=None`): nothing splits; an explicit sample still marks. → Task 1 test `test_without_shops_nothing_splits`.
- The same product twice in one Sell cart (a normal line and a Sample line): the stock check counts both lines together. → Task 2 test `test_two_lines_of_one_product_share_the_stock`.
- Editing an order that already holds a split pair: restoring and re-consuming gives the same pair, not three rows. → Task 3 test `test_editing_a_split_order_keeps_one_pair`.
- A Pedido draft saved before this change (lines without `sample`) reopens and sells as normal lines. → Task 4 test `test_an_old_draft_without_the_flag_sells_normally`.
- A Pedido line for a product with no units anywhere: Sell says so plainly; nothing is recorded. → Task 2 test `test_nothing_anywhere_is_still_refused`.

---

### Task 1: Stock core — split and consume

**Files:**
- Modify: `stock/services/store_stock.py` (next to `bring_in_for_sale`)
- Modify: `stock/services/stock_ops.py` (`consume_stock_fifo`, new `consume_for_sale`)
- Test: `stock/test_sample_sales.py` (new)

**Interfaces:**
- Produces: `store_stock.full_price_obtainable(product, store) -> int` — full-price units `store` can reach: own beyond its samples + each other active shop's beyond theirs + unassigned batches.
- Produces: `store_stock.bring_in_any(product, quantity, store, *, user=None, sale_order=None) -> int` — makes `store` hold `quantity` units counting its samples; shortfall from other shops' full-price units first, then their samples, then unassigned; via `move_units(... reason=StockTransfer.SALE)` (reuse the SALE transfer of `sale_order` like `bring_in_for_sale`). Raises the Global Constraints message when all shops together lack units.
- Produces: `stock_ops.split_for_sale(product, quantity, store, *, sample=False) -> list[tuple[int, bool]]` — `[(quantity, sample)]` when `store is None` or `sample`; else `[(quantity, False)]` if it fits `full_price_obtainable`, otherwise `[(full, False), (quantity - full, True)]`, dropping a zero part.
- Produces: `stock_ops.consume_for_sale(product, quantity, store, *, sample=False, user=None, sale_order=None) -> list[SalePart]` with `SalePart = namedtuple('SalePart', 'quantity is_sample cost_basis')`, consuming each part with `consume_stock_fifo`.
- Changes: `consume_stock_fifo(..., sample=True)` calls `bring_in_any` instead of requiring the shop's own units.

- [ ] **Step 1: Write failing tests** in `stock/test_sample_sales.py`, `SplitTests(TestCase)` with two shops (`keeps_samples=True`, `samples_per_shop` default 1): `test_fits_full_price` (own 3 → sell 2 → `[(2, False)]`); `test_one_over_takes_a_sample` (own 2, other 1 → sell 2 → `[(1, False), (1, True)]`, other shop untouched); `test_only_samples_left` (own 1, other 1 → sell 2 → `[(2, True)]` and one transfer from the other shop); `test_explicit_sample_from_the_other_shop` (own 0, other 1 → `consume_for_sale(..., sample=True)` → `[SalePart(1, True, cost)]`, transfer recorded); `test_nothing_anywhere_is_refused` (`ValidationError` with "Needed 1, both shops have 0"); `test_without_shops_nothing_splits` (no active shops → `[(2, False)]`, and `sample=True` → `[(2, True)]`).
- [ ] **Step 2: Run** `manage.py test stock.test_sample_sales` → FAIL (names undefined).
- [ ] **Step 3: Implement** the four functions above; keep `bring_in_for_sale` behaviour for full-price parts.
- [ ] **Step 4: Run** the same → PASS; then the full suite → note failures caused by the new sample rule for Task 2/3 to fix (do not change them here unless they test `consume_stock_fifo(sample=True)` directly).
- [ ] **Step 5: Commit** `feat: split a sale line into full-price and sample units`.

### Task 2: Sell — server and till

**Files:**
- Modify: `stock/views.py` (`outbound_view` sale loop; `_sell_line_ctx`; `check_barcode` JSON)
- Modify: `stock/templates/stock/outbound.html` (line modal, `lineValid`, `lineWarnings`, cart flags, Pedido payload)
- Test: `stock/test_sample_sales.py` (`SellTests`); update existing Sell sample tests in `stock/tests.py`

**Interfaces:**
- Consumes: `consume_for_sale` (Task 1).
- Produces: line context key `total` (int: units in all active shops + unassigned) in `_sell_line_ctx` and `check_barcode` (`total`), alongside `stock` and `sampleHere`.
- Produces: Pedido cart lines carry `sample: bool` (from Task 4's `sell_payload`; default False).

- [ ] **Step 1: Write failing tests** (`SellTests`, `TestCase`, one active store with samples): `test_one_over_full_price_is_recorded_as_two_lines` (own 2 → post qty 2 → Sales `[(1, False), (1, True)]`, same unit price and payment); `test_a_sample_line_of_two` (own 1, other 1, `sample: true`, qty 2 → one Sale qty 2 `is_sample`); `test_two_lines_of_one_product_share_the_stock` (own 2: normal qty 1 + sample qty 1 → recorded; normal 2 + sample 1 → "Not enough stock"); `test_nothing_anywhere_is_still_refused` (0 units → error page, no SaleOrder); `test_check_barcode_reports_the_total` (`total` = 2 when own 1 + other 1, `stock` = 0).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the sale loop: for each row `parts = consume_for_sale(product, qty, active_store, sample=row['sample'], user=request.user, sale_order=order)`, one `Sale` per part (`quantity=part.quantity`, `cost_basis=part.cost_basis`, `is_sample=part.is_sample`). Add `total`.
- [ ] **Step 4: Implement the till**: remove the `data-kind="sample"` price option; add a "Sample" checkbox in the line modal (label "Sample", under the price toggle); a Sample line allows any qty up to `total` minus what other lines of the barcode hold; `lineValid`: sum of all lines of the barcode ≤ `total`; `lineWarnings`: on a normal line whose barcode's normal lines exceed `stock`, tone `warn`, text `${excess} from the shop samples`; keep the cart's grey `Sample` flag; Pedido payload line `sample` sets `item.sample`. Price kinds stay Retail / Wholesale / Last for both.
- [ ] **Step 5: Update existing tests** that asserted the old rules (sample qty must be 1, price typed, "Only the shop samples are left" at Sell) to the new behaviour; run full suite → OK.
- [ ] **Step 6: UI check** with the Node Playwright script (session cookie, font route, watchdog; see memory `feedback_look_before_claiming_ui`): line modal with Sample ticked and a 60-character product name at 360/390/820/1366 — no overflow, the warning does not shift the cart row (reserve its slot).
- [ ] **Step 7: Commit** `feat: the till sells shop samples as marked lines instead of refusing`.

### Task 3: Order corrections — per-line sample and split

**Files:**
- Modify: `stock/services/order_corrections.py` (`save_sale_order_correction`, `_consume_current_stock`)
- Modify: `stock/views.py` (`_correction_cart_item`, `_build_correction_cart`, `_enrich_correction_items`, `_parse_correction_cart`)
- Modify: `stock/templates/stock/sale_order_correction_form.html` (line modal checkbox, cart row flag, `items_json`)
- Test: `stock/test_sample_sales.py` (`CorrectionTests`)

**Interfaces:**
- Consumes: `consume_for_sale` (Task 1).
- Produces: correction line dicts `{'product', 'quantity', 'unit_price', 'payment'|'payment_method', 'sample': bool}`; `_correction_cart_item(..., sample=False)` adds `'sample'`.

- [ ] **Step 1: Write failing tests**: `test_a_past_sale_with_only_samples_left_saves` (own 1 → create order qty 1 affects_stock → saved, one Sale `is_sample`); `test_a_past_sale_splits_like_the_till` (own 2, qty 2 → `[(1, False), (1, True)]`); `test_editing_a_split_order_keeps_one_pair` (edit the order above without changes → still exactly those two Sales, stock unchanged); `test_a_ticked_line_without_stock_effect_is_marked_only` (affects_stock off, `sample: true` → one Sale `is_sample`, no stock moved); `test_the_form_reloads_each_lines_flag` (edit page `items` JSON carries `"sample": true` on the sample line).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: with stock effect, per line `consume_for_sale(...)` then one `Sale` per part (`date=order_datetime` as now); without, one `Sale` with `is_sample=line['sample']`; drop the `sample_products` set; restoring stays grouped by product. Parse and reload `sample`; add the "Sample" checkbox and flag to the form.
- [ ] **Step 4: Run** full suite → OK. **UI check** the correction line modal and a flagged cart row at four widths.
- [ ] **Step 5: Commit** `feat: past sales and corrections sell samples as marked lines`.

### Task 4: Pedido — one row per entry, Tester per row

**Files:**
- Modify: `stock/templates/stock/pedido.html` (`addRow`, row markup)
- Modify: `stock/pedido_views.py` (`_read_lines`, `_form_rows`, `_save_draft`)
- Modify: `stock/services/pedido_drafts.py` (`draft_lines_for_form`, `sell_payload`)
- Modify: `stock/services/pedido_pdf.py` (`_product_cell`)
- Modify: `stock/views.py` (`_pedido_cart` passes `sample`)
- Test: `stock/test_sample_sales.py` (`PedidoTests`); update tests in `stock/test_pedido_picks.py` / `stock/tests.py` that relied on merging

**Interfaces:**
- Produces: posted parallel list `sample` (`'1'` or `''`) beside `product_id`; line dict and draft line JSON key `sample: bool` (written only when true; missing = False); `sell_payload` lines carry `sample`.

- [ ] **Step 1: Write failing tests**: `test_two_entries_stay_two_lines` (post the same product twice → draft has two lines); `test_tester_is_kept_and_printed` (second line `sample=1` → draft line `sample: True`; PDF text contains "Tester"); `test_a_tester_line_reaches_the_till` (`_pedido_cart(draft)['lines'][1]['sample'] is True`, price = quoted); `test_an_old_draft_without_the_flag_sells_normally` (draft lines without the key → all `sample` False).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: `addRow` never merges; each row gets a checkbox labelled "Tester" and `<input type="hidden" name="sample">` kept in sync; a Tester row shows "Tester" in Disponibilidade; `_read_lines` reads `request.POST.getlist('sample')`; `_product_cell` adds a small "Tester" line under the title (same style as picks).
- [ ] **Step 4: Run** full suite → OK. **UI check** Pedido rows (phone two-line layout included) at four widths with the checkbox.
- [ ] **Step 5: Commit** `feat: Pedido rows are never merged and can be Tester`.

### Task 5: B2B order follows the Tester mark

**Files:**
- Modify: `stock/models/wholesale.py` (`OrderRequestLine.is_sample = BooleanField(default=False)`)
- Create: `stock/migrations/0097_orderrequestline_is_sample.py` (check the number against `origin/master` and `origin/codex/*` first)
- Modify: `stock/services/wholesale_orders.py` (`_follows_quote`, `save_to_pedido`)
- Modify: the customer order template(s) and the shop's wholesale order page that list `OrderRequestLine`s
- Test: `stock/test_sample_sales.py` (`WholesaleOrderTests`)

- [ ] **Step 1: Write failing tests**: `test_the_order_follows_a_tester_line` (order with 1 line; quote adds a Tester line of the same product → order has 2 lines, the second `is_sample`); `test_picks_stay_with_their_own_line` (two lines of one pick-mode product with different picks → each order line keeps its own picks); `test_saved_to_pedido_keeps_the_flag`; `test_the_customer_sees_tester` (customer order page contains "Tester").
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: compare and copy `(product_id, quantity, price, picks, sample)` per quoted line, using each line's own picks; write `sample` in `save_to_pedido`; tag in templates.
- [ ] **Step 4: Run** full suite + `makemigrations --check` → OK.
- [ ] **Step 5: Commit** `feat: B2B orders carry the Tester mark from the quote`.

### Task 6: Show the mark wherever a sale line is listed

**Files (each lists sale lines):** `stock/templates/stock/sale_order_detail.html` (table and receipt-style list), the Orders detail layer (`stock/orders_views.py` ~line 219 and its template), `sales_records_employee.html`, `customer_detail.html`, `customer_orders_employee.html`, `_daily_order_item.html`, the product page sales list (`product_detail.html` / `panels/product.html` / `product_sales_history.html`), `stock/services/receipt_pdf.py` via its caller in `stock/views.py` (~line 3100).
- Test: `stock/test_sample_sales.py` (`MarkTests`)

- [ ] **Step 1: Write failing tests**: an order with one normal and one sample line; staff order detail and Orders detail layer contain the `Sample` tag once; employee sales records and customer page contain it; receipt PDF text contains "Tester".
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** a small tag next to the product name (reuse `.line-flag.sample` styling: grey pill, `font-size >= .85rem`), reserved inline so rows keep their height; receipt line title gets ` (Tester)`.
- [ ] **Step 4: Run** full suite → OK. **UI check** order detail and Orders detail layer at four widths with the longest product name.
- [ ] **Step 5: Commit** `feat: mark sample lines in records, receipts and the product page`.

### Task 7: Docs, final checks, push

- [ ] Update `docs/PRD.md` (F2.4 sample rules; Pedido rows; corrections; B2B), `docs/ARCHITECTURE.md` (`consume_for_sale`, `bring_in_any`, migration 0097), `docs/STATUS.md` (test count, latest migration, changelog line).
- [ ] `git fetch --all`; merge any new `origin/master` / `origin/codex/*` with `--no-ff`; re-run the full suite and `makemigrations --check`.
- [ ] Commit guarded on `^OK`; push; give the PA commands (migration 0097 adds a column only; `backup_db` kept; no static change unless CSS files under `stock/static` were touched).
