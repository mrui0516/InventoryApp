# Design: "Affects inventory" toggle for Add Missing Historical Order

Date: 2026-07-14
Status: Approved — ready for implementation plan

## Goal

When adding a previously-missed historical order in the Order Correction
"Add Missing Historical Order" flow, let the admin choose whether the order
affects inventory. If it does not, the order still records revenue / payments /
customer history but does **not** decrement live stock (the physical goods
already left and current stock is already correct), and its profit is booked at
a flat 50% margin instead of FIFO.

## Context (current behavior)

- `Purchase.remaining` is live inventory. `save_sale_order_correction` calls
  `_consume_current_stock` on create; on edit it does
  `_restore_current_stock` (old lines) then `_consume_current_stock` (new
  lines); `delete_sale_order_correction` calls `_restore_current_stock`.
- Profit/cost is computed separately by
  `stock/services/profit.py::sale_profit_map_for_sale_ids`, which replays all
  `Purchase` + `Sale` events chronologically (by quantity, not `remaining`) to
  assign each sale a FIFO cost. It ignores `remaining`.
- Correction views are `@admin_required`.

## Confirmed decisions

- **Persisted flag:** new `SaleOrder.affects_stock` BooleanField, default `True`
  (a migration; all existing orders keep today's behavior).
- **UI scope:** the checkbox appears on the **create** page only ("Add Missing
  Historical Order"), default checked. The edit page does not show or change it.
- **A no-stock order (`affects_stock=False`):** never touches live stock on
  create, edit, or delete. It still creates the `SaleOrder`/`Sale`/payments
  normally, so revenue and reporting include it.
- **Profit for no-stock sales:** flat 50% margin (constant, tunable). Cost = 50%
  of revenue, profit = 50% of revenue. These sales do **not** consume FIFO
  batches in the replay (so they don't distort other sales' costs).
- **Normal orders (`affects_stock=True`):** unchanged in every respect.

## Changes

### 1. Model — `stock/models/sales.py`

Add to `SaleOrder`:

```python
affects_stock = models.BooleanField(default=True)
```

Generate the migration (adds the column, default `True` backfills existing rows).

### 2. Service — `stock/services/order_corrections.py`

`save_sale_order_correction(..., affects_stock=None)`:
- On **create** (`order is None`): set the new order's `affects_stock` from the
  argument (default to `True` when the argument is `None`).
- On **edit**: keep the existing order's `affects_stock` (ignore the argument).
- Gate stock operations on the order's effective `affects_stock`:
  - create: call `_consume_current_stock(line_items)` only if affects stock.
  - edit: call `_restore_current_stock(previous_line_items)` and
    `_consume_current_stock(line_items)` only if the order affects stock.
- `SaleOrder.objects.filter(pk=...).update(...)` should also persist
  `affects_stock` (set to the effective value).

`delete_sale_order_correction`: call `_restore_current_stock(previous_line_items)`
only if `order.affects_stock`.

`snapshot_sale_order`: add `affects_stock` to the returned dict (audit
completeness — the store field was added the same way).

### 3. Profit engine — `stock/services/profit.py`

- Add `BACKFILL_MARGIN = Decimal("0.50")` (profit fraction of revenue).
- Before the replay, load the set of no-stock sale ids up to `end_day`:
  `set(Sale.objects.filter(order__affects_stock=False, date__date__lte=end_day).values_list("id", flat=True))`.
- In the replay loop, for an `"out"` event whose sale id is in that set:
  - **do not** consume FIFO batches (skip the batch-drawing loop entirely);
  - if the sale is in `relevant_sale_ids`, set
    `revenue = amount * quantity`, `cost = (revenue * (1 - BACKFILL_MARGIN)).quantize(Decimal("0.01"))`,
    `profit = revenue - cost`.
- Normal sales keep the existing FIFO logic unchanged.

### 4. View — `_sale_order_correction_view` (`stock/views.py`)

- On create POST, parse `affects_stock = 'affects_stock' in request.POST` (a
  default-checked checkbox: present ⇒ True, absent ⇒ False) and pass it as
  `affects_stock=` to `save_sale_order_correction`. On edit, pass
  `affects_stock=None` (service keeps the stored value).
- Add context `affects_stock_default = True` for the create render.

### 5. Template — `stock/templates/stock/sale_order_correction_form.html`

In the order-header area, when creating (`{% if is_create %}`), add a checkbox:

```html
            {% if is_create %}
            <div class="col-12">
              <label class="form-label fw-bold">
                <input type="checkbox" name="affects_stock" id="affects-stock" checked>
                Affects inventory
              </label>
            </div>
            {% endif %}
```

Label + control only, no explanatory paragraph.

### 6. Tests — `stock/tests.py`

- Create with the checkbox unchecked → `order.affects_stock is False`, the
  Purchase `remaining` is unchanged (no consumption), and the `Sale` exists
  (revenue recorded).
- Create with it checked (default) → stock consumed exactly as today.
- Editing a no-stock order → `remaining` unchanged (no restore/consume) while
  lines/payments rebuild.
- Deleting a no-stock order → `remaining` unchanged (no restore).
- `sale_profit_map_for_sale_ids`: a no-stock sale returns
  `cost == profit == 50%` of its revenue; and a later normal sale's FIFO cost is
  unchanged whether or not the no-stock sale exists (no batch distortion).
- Existing correction/profit tests still pass.

### 7. Docs

Update `docs/STATUS.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md`.

## Data flow

```
Add Missing Historical Order, "Affects inventory" unchecked, save
  → view: affects_stock=False → save_sale_order_correction(affects_stock=False)
      → new SaleOrder.affects_stock=False
      → line items + payments created; _consume_current_stock SKIPPED
  → revenue/payments/reporting include the order; live stock unchanged
Later: profit report
  → sale_profit_map_for_sale_ids: this sale skips FIFO batches;
    cost=profit=revenue*0.5
```

## Error handling / edge cases

- Order-less legacy sales (no `order`) are treated as normal FIFO (not in the
  no-stock set).
- Default `True` keeps all existing data and normal-order behavior identical.
- No-stock order with no matching purchases → fine (flat margin, no FIFO needed).
- Editing/deleting a no-stock order never restores stock (the flag is persisted
  and honored).

## Out of scope

- Toggling `affects_stock` on the edit page (create-only, per decision).
- Making the 50% margin user-configurable in the UI (it is a code constant).
- Changing how normal (stock-affecting) orders compute FIFO cost.
