# Design: Change an order's store from Order Correction

Date: 2026-07-13
Status: Approved — ready for implementation plan

## Goal

Let an admin re-attribute a sale order to a different store from the Order
Correction form, to fix orders that were entered under the wrong store. Today
the correction form keeps an existing order locked to its own store on save.

## Context (current behavior)

- All three correction views (`sale_order_correction_center_view`,
  `..._create_view`, `..._edit_view`) are `@admin_required` — the page is
  already manager/admin-only, so no extra per-user gating is needed.
- `save_sale_order_correction(..., store=None)` at
  `stock/services/order_corrections.py:102` resolves
  `target_store = (order.store if order has store else None) or store` — so on
  **edit** the passed `store` is ignored (order keeps its store); on **create**
  it uses the active store. The view passes `store=store_for_new_sale(request)`.
- On save the service rebuilds line items and stamps `target_store` on the
  `SaleOrder` (`:121`) and on each `Sale` (`:139`). `SaleOrderPayment` has no
  store field (order-scoped) — nothing to change there.
- `snapshot_sale_order()` does **not** capture the store, so the audit log
  wouldn't record a store change.
- Corrections never touch `ARInvoice`.

## Confirmed decisions

- **Who:** admins only — satisfied inherently because the page is
  `@admin_required`. No new permission logic.
- **Approach:** inline store `<select>` in the existing correction form and save
  flow (chosen over a separate "move to store" list button, which would
  duplicate the move logic).
- **Scope of a store change:** the `SaleOrder` and all its (rebuilt) `Sale`
  line items move to the selected store. `SaleOrderPayment` unaffected (no store
  field). AR out of scope.
- **Default selection:** the order's current store on edit; the active store on
  create.
- **Options:** active stores only.
- **Audit:** record old→new store in the change-log snapshot.

## Changes

### 1. Service — `stock/services/order_corrections.py`

`save_sale_order_correction`: change target-store resolution so an explicitly
passed store wins (on edit as well as create):

```python
target_store = store if store is not None else (
    order.store if (order and order.store_id) else None
)
```

Everything downstream already applies `target_store` to the order and each
rebuilt `Sale`, so no other change is needed in this function.

`snapshot_sale_order`: add `store_id` and `store_name` to the returned dict so
before/after captures the store change.

### 2. View — `_sale_order_correction_view` (`stock/views.py`)

- Resolve the selected store from `request.POST.get('store')` to a `Store` that
  is **active**. If it is missing or invalid, fall back to the order's current
  store (edit) or the active store (create) — never null it.
- Pass the resolved store as `store=` to `save_sale_order_correction` (replacing
  the current unconditional `store_for_new_sale(request)`).
- Add template context: `available_stores` (active stores) and
  `selected_store_id` (default = `order.store_id` on edit, active store id on
  create).

### 3. Template — `stock/templates/stock/sale_order_correction_form.html`

Add a labeled `<select name="store">` in the order-level field area
(near customer / date / reason), listing `available_stores`, with
`selected_store_id` pre-selected. Follow existing form styling. No explanatory
paragraph — label + control only.

### 4. Tests — `stock/tests.py`

- Editing an order with a different active store selected moves
  `SaleOrder.store` **and** every `Sale.store` to it.
- Selecting the same store, or posting no/invalid store, keeps the order's
  current store (never null).
- The audit-log snapshot before/after reflects the store change
  (`store_id`/`store_name`).
- Existing correction tests still pass.

### 5. Docs

Update `docs/PRD.md` (F2.9.x), `docs/STATUS.md`, `docs/ARCHITECTURE.md`.

## Data flow

```
Admin edits order in correction form, picks a different Store, saves
  → view resolves POST['store'] to an active Store (else keeps current)
  → save_sale_order_correction(store=selected)
      → target_store = selected (explicit wins)
      → SaleOrder.store = selected
      → rebuilt Sale line items .store = selected
      → change log snapshot records old→new store
  → order (and its sales) now scoped to the selected store
```

## Error handling / edge cases

- Missing/invalid `store` in POST → keep the current/active store (no null).
- Selecting the same store → no-op.
- Only active stores selectable (an order can't be moved to an inactive store).
- Inventory unaffected (shared across stores).
- Daily summary: the existing `Sale` post_save/delete signal recalculates the
  affected date; store-scoped dashboards reflect the move at query time — no
  extra recalc code.

## Out of scope

- Moving a linked AR invoice (corrections don't touch AR).
- Bulk store reassignment (the `move_sales_to_store` management command already
  covers bulk/marker-customer moves).
- Any change to non-admin access.
