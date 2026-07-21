# Design: Perfume auto-pricing from current FIFO cost

Date: 2026-07-17
Status: Approved — ready for implementation plan

## Goal

For products in the **Perfumes** category, keep the wholesale and retail prices
automatically derived from the current cost of goods, while letting a manager
**lock** a product to pin manual prices.

- Wholesale = ⌈ current FIFO cost + 10 ⌉ (round the +10 result UP to a whole number)
- Retail = wholesale + 12
- Recompute whenever the **current FIFO cost** changes (not on every inbound).
- A per-product **lock** exempts a product from auto-recompute (prices stay manual).

Example: current FIFO cost 12.34 → wholesale ⌈22.34⌉ = **23** → retail **35**.

## Context (current model)

- Cost of goods lives on `Purchase.cost_price` (FIFO batches), NOT on `Product`.
  A product may have several batches at different costs.
- `Product.current_fifo_cost_price()` returns the `cost_price` of the **oldest
  batch with `remaining > 0`** (the batch currently being sold); if no stock
  remains it falls back to `last_known_cost_price()` (newest purchase), else 0.
- `Product.default_price` (retail) and `Product.wholesale_price` are the stored
  selling prices used across the app.
- FIFO stock mutations go through `services/stock_ops.py`:
  `consume_stock_fifo` (sales) and `restore_stock_fifo` (order delete/edit) use
  **`QuerySet.update()` with `F()`** — a bulk update that does **not** fire
  `post_save` signals. `Purchase` creation (inbound) uses `Purchase.objects.create()`
  which **does** fire `post_save`.
- Categories in the live DB: Perfumes (227), Accessories (169), Shisha (0). The
  formula applies to **Perfumes only**.

## Confirmed decisions

- **Scope:** Perfumes category only (matched by category name).
- **Cost source:** `current_fifo_cost_price()` (the batch currently being sold),
  not the latest inbound cost.
- **Formula:** wholesale = ceil(cost + 10) to a whole number; retail = wholesale + 12.
- **Cost ≤ 0:** skip (a never-inbounded product is not auto-priced from cost 0).
- **Auto overrides manual by default**, BUT a per-product **lock** (`price_locked`)
  exempts the product from auto-recompute so its prices stay whatever was set
  manually. Default unlocked (auto).
- **Trigger:** whenever the current FIFO cost may have changed — idempotent, so
  it only actually changes a price when the current cost changed.
- **Employees may VIEW but not CHANGE prices (all products):** on the add/edit
  product form, the retail (`default_price`) and wholesale (`wholesale_price`)
  fields are **read-only for non-managers** — shown (so they can see the value)
  but not editable, and **server-enforced** (a tampered POST is ignored). Managers
  edit prices as before. Implemented with Django form `disabled=True` on those two
  fields for non-managers (renders disabled AND ignores submitted data in favor
  of the field's initial). This applies to ALL products, not only perfumes.

## Changes

### 1. Model — `stock/models/catalog.py`

Add to `Product`:

```python
price_locked = models.BooleanField(default=False)
```

Generate a migration (adds the column, default `False`).

### 2. Service — `stock/services/pricing.py` (new)

```python
import math
from decimal import Decimal

PERFUME_CATEGORY_NAME = 'Perfumes'
WHOLESALE_MARKUP = Decimal('10')
RETAIL_MARKUP = Decimal('12')


def is_perfume(product):
    cat = getattr(product, 'category', None)
    return bool(cat and (cat.name or '').strip().lower() == PERFUME_CATEGORY_NAME.lower())


def sync_perfume_price(product):
    """Recompute a perfume's wholesale/retail from the current FIFO cost.

    No-op unless the product is a perfume, is not price-locked, and has a
    positive current FIFO cost. Idempotent; writes only when a price changed.
    Returns True if it wrote new prices, else False.
    """
    from ..models import Product
    if product is None or getattr(product, 'price_locked', False) or not is_perfume(product):
        return False
    cost = product.current_fifo_cost_price() or Decimal('0.00')
    if cost <= 0:
        return False
    wholesale = Decimal(math.ceil(cost + WHOLESALE_MARKUP))
    retail = wholesale + RETAIL_MARKUP
    if product.wholesale_price == wholesale and product.default_price == retail:
        return False
    Product.objects.filter(pk=product.pk).update(
        wholesale_price=wholesale, default_price=retail
    )
    product.wholesale_price = wholesale
    product.default_price = retail
    return True
```

Uses `Product.objects.filter(...).update(...)` (no `save()`) to avoid save-signal
recursion. Runs inside whatever transaction the caller holds.

### 3. Triggers

- **Inbound (Purchase create):** a `post_save` receiver on `Purchase` in
  `stock/signals.py` calls `sync_perfume_price(instance.product)`. (Covers a new
  batch becoming the current one when stock was 0; idempotent otherwise.)
- **Sales / restores (bulk `update()`, no signal):** call
  `sync_perfume_price(product)` at the **end** of `consume_stock_fifo` and
  `restore_stock_fifo` (they already hold `product`, inside their atomic block).
- **Stock adjustments:** `api_adjust_purchase_stock` and `api_adjust_total_stock`
  change `remaining` directly — call `sync_perfume_price(product)` after a
  successful adjustment.

Because `sync_perfume_price` is idempotent, these broad call sites are safe:
when the current FIFO cost has not changed, no price is written.

### 4. Form / edit page — the lock control

Add `price_locked` to `ProductForm` (a checkbox "Lock price (don't auto-update
from cost)"). In `edit_product.html` / `add_product.html`, render it **gated to
managers** (`{% if user|is_manager_user %}`) — pricing policy is a manager
concern; employees may edit fields but not the lock. A locked product's prices
are never overwritten by `sync_perfume_price`.

### 5. Backfill command — `stock/management/commands/sync_perfume_prices.py`

Iterate all Perfume products and call `sync_perfume_price`; print how many were
updated / skipped (locked, no cost). Run once to price the existing 227 perfumes.
Support `--dry-run` to preview.

### 6. Tests — `stock/tests.py`

- Formula: cost 12.34 → wholesale 23, retail 35; ceil boundary (e.g. cost 12.00 →
  wholesale 22; cost 12.01 → wholesale 23).
- Cost ≤ 0 (no purchase) → no auto price.
- Non-perfume (Accessories) product → never auto-priced.
- Locked perfume → not overwritten even after an inbound/sale.
- Inbound of the first batch (stock was 0) → price set from that cost.
- A second batch at a different cost while the first still has stock → price
  unchanged; after the first batch is fully sold (consume), price switches to the
  second batch's cost.
- Auto overrides a prior manual price on an unlocked perfume after a stock event.
- Backfill command updates unlocked perfumes with cost, skips locked/no-cost.

### 7. Docs

Update `docs/PRD.md` (a pricing feature entry) and `docs/ARCHITECTURE.md`
(core business patterns — perfume auto-pricing + trigger points).

## Data flow

```
Manager inbounds a perfume at cost 12.34
  → Purchase.create → post_save → sync_perfume_price
      → not locked, perfume, cost 12.34 → wholesale ⌈22.34⌉=23, retail 35 → stored
POS sells the last unit of the current (cost 12.34) batch
  → consume_stock_fifo depletes it → sync_perfume_price at end
      → current FIFO cost now the next batch (say 13.00) → wholesale 23→24, retail 36
Manager locks a special-edition perfume, sets retail 99 manually
  → price_locked=True → sync_perfume_price is a no-op → stays 99
```

## Error handling / edge cases

- Cost ≤ 0 / no purchases → skipped (no pricing off 0).
- Locked → skipped.
- Non-perfume → skipped.
- Idempotent write-only-on-change → sale-heavy days don't churn identical writes.
- `ceil` uses `math.ceil(Decimal)` → returns an int; stored as `Decimal` euros.
- No `save()` in the sync (uses `.update()`) → no signal recursion.

## Out of scope

- Per-category configurable markups (hardcoded +10 / +12; Perfumes only).
- Applying to Accessories/Shisha.
- A price-change audit log (not requested).
- Changing how FIFO cost/profit itself is computed.
