# Perfume Auto-Pricing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For Perfumes-category products, auto-derive wholesale = ⌈FIFO cost + 10⌉ and retail = wholesale + 12 from the current FIFO cost, recomputed whenever that cost changes, with a per-product lock to pin manual prices.

**Architecture:** A pure idempotent service `sync_perfume_price(product)` computes and (only when changed) writes the two prices via `Product.objects.update()`. It is called from the points where FIFO cost can change: a `Purchase` `post_save` signal (inbound), the end of `consume_stock_fifo`/`restore_stock_fifo` (sales/restores use bulk `update()` and fire no signal), and the stock-adjustment APIs. A `price_locked` boolean exempts a product; a manager-only checkbox sets it. A backfill command prices existing perfumes.

**Tech Stack:** Django 5.2.4, SQLite, `django.test.TestCase`, `math.ceil`, `Decimal`.

Spec: `docs/superpowers/specs/2026-07-17-perfume-auto-pricing-design.md`

## Global Constraints

- Scope: **Perfumes category only** (match `category.name` case-insensitively equal to `Perfumes`).
- Cost source: `Product.current_fifo_cost_price()` (oldest batch with `remaining > 0`; falls back to newest purchase, else 0).
- Formula: `wholesale = Decimal(math.ceil(cost + 10))`; `retail = wholesale + 12`. Stored on `Product.wholesale_price` / `Product.default_price`.
- Skip (no write) when: not a perfume, `price_locked` is True, or cost ≤ 0.
- The sync must use `Product.objects.filter(pk=...).update(...)` — never `product.save()` — to avoid signal recursion. It writes only when a price actually changed (idempotent).
- No change to how FIFO cost/profit is computed. No per-category configurable markups (hardcoded 10 / 12).
- Run tests with the SYSTEM python: `C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe manage.py test stock`. Django prints to stderr — capture full output, don't tail.
- No UTF-8 BOM in any edited file (after editing tests.py, `git diff -- stock/tests.py` line 1 must not appear).
- Latest migration is `0033_saleorder_affects_stock`; the new one will be `0034_...`.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `stock/models/catalog.py` (modify) | Add `Product.price_locked` boolean. |
| `stock/migrations/0034_*.py` (create, via makemigrations) | The column migration. |
| `stock/services/pricing.py` (create) | `is_perfume`, `sync_perfume_price` — pure pricing logic. |
| `stock/signals.py` (modify) | `Purchase` `post_save` → `sync_perfume_price`. |
| `stock/services/stock_ops.py` (modify) | Call sync at end of `consume_stock_fifo` / `restore_stock_fifo`. |
| `stock/views.py` (modify) | Call sync in `api_adjust_purchase_stock` / `api_adjust_total_stock`. |
| `stock/forms.py` (modify) | Add `price_locked` to `ProductForm`. |
| `stock/templates/stock/edit_product.html`, `add_product.html` (modify) | Manager-only lock checkbox. |
| `stock/management/commands/sync_perfume_prices.py` (create) | Backfill existing perfumes. |
| `stock/tests.py` (modify) | Unit + integration + command tests. |
| `docs/PRD.md`, `docs/ARCHITECTURE.md` (modify) | Record the feature. |

Test models (`Category`, `Product`, `Purchase`, `Sale`, `SaleOrder`, `get_user_model`, `reverse`, `Decimal`, `timezone`) are imported at the top of `stock/tests.py`.

---

### Task 1: `Product.price_locked` field + migration

**Files:**
- Modify: `stock/models/catalog.py` (add field to `Product`, ~after line 69 `wholesale_price`)
- Create: `stock/migrations/0034_product_price_locked.py` (via makemigrations)
- Test: `stock/tests.py` (append)

**Interfaces:**
- Produces: `Product.price_locked` (BooleanField, default `False`).

- [ ] **Step 1: Write the failing test**

Append to `stock/tests.py`:

```python
class PerfumePriceLockedFieldTests(TestCase):
    def test_price_locked_defaults_false(self):
        from stock.models import Product
        p = Product.objects.create(name="X", barcode="8000000000001", brand="B")
        self.assertFalse(p.price_locked)
```

- [ ] **Step 2: Run to verify it fails**

Run: `...python.exe manage.py test stock.tests.PerfumePriceLockedFieldTests -v 2`
Expected: FAIL — `AttributeError`/`FieldError`: `price_locked` does not exist.

- [ ] **Step 3: Add the field**

In `stock/models/catalog.py`, in `class Product`, right after the `wholesale_price` field:

```python
    price_locked = models.BooleanField(default=False)
```

- [ ] **Step 4: Make + run the migration, then the test**

```
...python.exe manage.py makemigrations stock
...python.exe manage.py migrate
...python.exe manage.py test stock.tests.PerfumePriceLockedFieldTests -v 2
```
Expected: a migration `0034_product_price_locked` is created; test PASSES.

- [ ] **Step 5: Commit**

```
git add stock/models/catalog.py stock/migrations/0034_product_price_locked.py stock/tests.py
git commit -m "feat: add Product.price_locked field"
```

---

### Task 2: Pricing service (`sync_perfume_price`)

**Files:**
- Create: `stock/services/pricing.py`
- Test: `stock/tests.py` (append)

**Interfaces:**
- Consumes: `Product.price_locked` (Task 1); `Product.current_fifo_cost_price()`.
- Produces:
  - `PERFUME_CATEGORY_NAME = 'Perfumes'`, `WHOLESALE_MARKUP = Decimal('10')`, `RETAIL_MARKUP = Decimal('12')`
  - `is_perfume(product) -> bool`
  - `sync_perfume_price(product) -> bool` — writes `wholesale_price`/`default_price` when they change; returns whether it wrote. No-op (returns False) if product is None / not a perfume / `price_locked` / cost ≤ 0.

- [ ] **Step 1: Write the failing tests**

Append to `stock/tests.py`:

```python
class PerfumePricingServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.perfumes = Category.objects.create(name="Perfumes")
        cls.accessories = Category.objects.create(name="Accessories")

    def _perfume(self, barcode, cost=None, remaining=5, locked=False):
        from stock.models import Product, Purchase
        p = Product.objects.create(name="P", barcode=barcode, brand="B",
                                   category=self.perfumes, price_locked=locked)
        if cost is not None:
            Purchase.objects.create(product=p, quantity=remaining, remaining=remaining,
                                    cost_price=Decimal(str(cost)))
        return p

    def test_formula_rounds_up(self):
        from stock.services.pricing import sync_perfume_price
        p = self._perfume("8100000000001", cost="12.34")
        self.assertTrue(sync_perfume_price(p))
        self.assertEqual(p.wholesale_price, Decimal("23"))   # ceil(12.34+10)=23
        self.assertEqual(p.default_price, Decimal("35"))      # 23+12

    def test_ceil_boundary(self):
        from stock.services.pricing import sync_perfume_price
        exact = self._perfume("8100000000002", cost="12.00")
        sync_perfume_price(exact)
        self.assertEqual(exact.wholesale_price, Decimal("22"))  # ceil(22.00)=22
        over = self._perfume("8100000000003", cost="12.01")
        sync_perfume_price(over)
        self.assertEqual(over.wholesale_price, Decimal("23"))   # ceil(22.01)=23

    def test_no_cost_skips(self):
        from stock.services.pricing import sync_perfume_price
        p = self._perfume("8100000000004", cost=None)   # no purchase -> cost 0
        p.default_price = Decimal("99")
        p.save(update_fields=["default_price"])
        self.assertFalse(sync_perfume_price(p))
        p.refresh_from_db()
        self.assertEqual(p.default_price, Decimal("99"))

    def test_locked_skips(self):
        from stock.services.pricing import sync_perfume_price
        p = self._perfume("8100000000005", cost="12.34", locked=True)
        p.default_price = Decimal("99")
        p.save(update_fields=["default_price"])
        self.assertFalse(sync_perfume_price(p))
        p.refresh_from_db()
        self.assertEqual(p.default_price, Decimal("99"))

    def test_non_perfume_skips(self):
        from stock.models import Product, Purchase
        from stock.services.pricing import sync_perfume_price
        p = Product.objects.create(name="A", barcode="8100000000006", brand="B",
                                   category=self.accessories, default_price=Decimal("5"))
        Purchase.objects.create(product=p, quantity=3, remaining=3, cost_price=Decimal("12.34"))
        self.assertFalse(sync_perfume_price(p))
        p.refresh_from_db()
        self.assertEqual(p.default_price, Decimal("5"))

    def test_idempotent(self):
        from stock.services.pricing import sync_perfume_price
        p = self._perfume("8100000000007", cost="12.34")
        self.assertTrue(sync_perfume_price(p))
        self.assertFalse(sync_perfume_price(p))   # second call: no change
```

- [ ] **Step 2: Run to verify it fails**

Run: `...python.exe manage.py test stock.tests.PerfumePricingServiceTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock.services.pricing'`.

- [ ] **Step 3: Create the service**

Create `stock/services/pricing.py`:

```python
"""Auto-pricing for Perfumes: derive selling prices from the current FIFO cost.

wholesale = ceil(current FIFO cost + 10); retail = wholesale + 12. Perfumes only,
skipped when the product is price-locked or has no positive cost. Idempotent:
writes via Product.objects.update() only when a price actually changes.
"""
import math
from decimal import Decimal

PERFUME_CATEGORY_NAME = 'Perfumes'
WHOLESALE_MARKUP = Decimal('10')
RETAIL_MARKUP = Decimal('12')


def is_perfume(product):
    cat = getattr(product, 'category', None)
    return bool(cat and (cat.name or '').strip().lower() == PERFUME_CATEGORY_NAME.lower())


def sync_perfume_price(product):
    """Recompute a perfume's prices from its current FIFO cost. Returns True if written."""
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

- [ ] **Step 4: Run to verify it passes**

Run: `...python.exe manage.py test stock.tests.PerfumePricingServiceTests -v 2`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```
git add stock/services/pricing.py stock/tests.py
git commit -m "feat: perfume auto-pricing service (ceil FIFO cost +10 / +12)"
```

---

### Task 3: Wire the triggers (inbound signal, consume/restore, adjust APIs)

**Files:**
- Modify: `stock/signals.py` (Purchase `post_save`)
- Modify: `stock/services/stock_ops.py` (end of `consume_stock_fifo`, `restore_stock_fifo`)
- Modify: `stock/views.py` (`api_adjust_purchase_stock` ~5448-5465, `api_adjust_total_stock`)
- Test: `stock/tests.py` (append)

**Interfaces:**
- Consumes: `sync_perfume_price` (Task 2).

- [ ] **Step 1: Write the failing tests**

Append to `stock/tests.py`:

```python
class PerfumePricingTriggerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.perfumes = Category.objects.create(name="Perfumes")

    def _perfume(self, barcode):
        from stock.models import Product
        return Product.objects.create(name="P", barcode=barcode, brand="B", category=self.perfumes)

    def test_inbound_first_batch_sets_price(self):
        from stock.models import Purchase
        p = self._perfume("8200000000001")
        Purchase.objects.create(product=p, quantity=5, remaining=5, cost_price=Decimal("12.34"))
        p.refresh_from_db()
        self.assertEqual(p.wholesale_price, Decimal("23"))
        self.assertEqual(p.default_price, Decimal("35"))

    def _two_batches(self, product, cheap_cost, cheap_qty, pricey_cost, pricey_qty):
        # Purchase.date is auto_now_add=True, so create(date=...) is IGNORED.
        # Force distinct dates with .update() (bypasses auto_now_add) so the
        # cheap batch is unambiguously the oldest (current FIFO), then re-sync
        # because the create-time signal ran before the dates were fixed.
        from stock.models import Purchase
        from stock.services.pricing import sync_perfume_price
        cheap = Purchase.objects.create(product=product, quantity=cheap_qty, remaining=cheap_qty,
                                        cost_price=Decimal(str(cheap_cost)))
        pricey = Purchase.objects.create(product=product, quantity=pricey_qty, remaining=pricey_qty,
                                         cost_price=Decimal(str(pricey_cost)))
        Purchase.objects.filter(pk=cheap.pk).update(date=timezone.now() - timedelta(days=2))
        Purchase.objects.filter(pk=pricey.pk).update(date=timezone.now())
        sync_perfume_price(product)
        return cheap, pricey

    def test_sale_batch_transition_changes_price(self):
        from stock.services.stock_ops import consume_stock_fifo
        p = self._perfume("8200000000002")
        self._two_batches(p, cheap_cost="10.00", cheap_qty=1, pricey_cost="20.00", pricey_qty=5)
        p.refresh_from_db()
        self.assertEqual(p.wholesale_price, Decimal("20"))   # current = cheap batch: ceil(10+10)
        consume_stock_fifo(p, 1)                              # deplete the cheap batch
        p.refresh_from_db()
        self.assertEqual(p.wholesale_price, Decimal("30"))   # now current = pricier: ceil(20+10)
        self.assertEqual(p.default_price, Decimal("42"))

    def test_adjust_purchase_stock_reprices(self):
        p = self._perfume("8200000000003")
        cheap, _ = self._two_batches(p, cheap_cost="10.00", cheap_qty=2, pricey_cost="20.00", pricey_qty=5)
        p.refresh_from_db()
        self.assertEqual(p.wholesale_price, Decimal("20"))
        user_model = get_user_model()
        user_model.objects.create_user(username="adj_mgr", password="pw123456", is_staff=True)
        self.client.login(username="adj_mgr", password="pw123456")
        self.client.post(reverse("api_adjust_purchase_stock"),
                         data={"purchase_id": cheap.id, "new_remaining": 0},
                         content_type="application/json")
        p.refresh_from_db()
        self.assertEqual(p.wholesale_price, Decimal("30"))   # cheap batch emptied -> pricier current
```

(Confirm the URL name for the adjust endpoint in `stock/urls.py` — it should be `api_adjust_purchase_stock`; if different, use the actual name.)

- [ ] **Step 2: Run to verify it fails**

Run: `...python.exe manage.py test stock.tests.PerfumePricingTriggerTests -v 2`
Expected: FAIL — prices not updated (no triggers yet).

- [ ] **Step 3: Add the Purchase post_save signal**

In `stock/signals.py`, extend imports and add a receiver:

```python
from .models import Purchase   # add Purchase to the existing `.models` import line
```

Append at the end of the file:

```python
@receiver(post_save, sender=Purchase)
def reprice_perfume_on_purchase(sender, instance, **kwargs):
    """A new/updated purchase batch may change a perfume's current FIFO cost."""
    from .services.pricing import sync_perfume_price
    try:
        sync_perfume_price(instance.product)
    except Exception:  # never let pricing break an inbound
        logger.exception('perfume reprice on purchase failed for %s', getattr(instance, 'product_id', '?'))
```

(`post_save`, `receiver`, and `logger` are already imported/defined at the top of signals.py.)

- [ ] **Step 4: Call sync at the end of the FIFO stock helpers**

In `stock/services/stock_ops.py`, at the **end** of `consume_stock_fifo` (after the final `if left > 0` guard, still inside the function) and at the **end** of `restore_stock_fifo`, add:

```python
    from .pricing import sync_perfume_price
    sync_perfume_price(product)
```

(Import inside the function to avoid a circular import at module load. Both functions already have `product` in scope and run inside `@transaction.atomic`.)

- [ ] **Step 5: Call sync in the stock-adjustment APIs**

In `stock/views.py`, in `api_adjust_purchase_stock`, right after the `StockAdjustmentLog.objects.create(...)` / `inventory_snapshot = build_inventory_snapshot(...)` block (still inside `with transaction.atomic():`), add:

```python
            from .services.pricing import sync_perfume_price
            sync_perfume_price(purchase.product)
```

In `api_adjust_total_stock`, after its stock changes are applied (just before it builds its success response / snapshot), add the same two lines using that view's product object (read the view to get the local variable name for the product; it fetches a `Product` by `product_id`).

- [ ] **Step 6: Run the trigger tests, then the full suite**

```
...python.exe manage.py test stock.tests.PerfumePricingTriggerTests -v 2
...python.exe manage.py test stock
```
Expected: trigger tests PASS; full suite OK (179 baseline + new tests, 0 failures). If any existing test now fails because a perfume's price auto-changed, inspect: a test that created a Perfumes product + a Purchase and asserted a hardcoded price it set manually would now be overwritten — update that test to the computed price or lock the product; do not disable the trigger.

- [ ] **Step 7: Commit**

```
git add stock/signals.py stock/services/stock_ops.py stock/views.py stock/tests.py
git commit -m "feat: reprice perfumes when FIFO cost changes (inbound/sale/restore/adjust)"
```

---

### Task 4: Manager-only lock checkbox on the product form

**Files:**
- Modify: `stock/forms.py` (`ProductForm.Meta.fields` ~line 50-61)
- Modify: `stock/templates/stock/edit_product.html`, `stock/templates/stock/add_product.html`
- Test: `stock/tests.py` (append)

**Interfaces:**
- Consumes: `Product.price_locked` (Task 1); `is_manager_user` template filter.

- [ ] **Step 1: Write the failing test**

```python
class PerfumePriceLockFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.perfumes = Category.objects.create(name="Perfumes")

    def test_manager_can_lock_and_lock_prevents_reprice(self):
        from stock.models import Product, Purchase
        user_model = get_user_model()
        user_model.objects.create_user(username="lock_mgr", password="pw123456", is_staff=True)
        self.client.login(username="lock_mgr", password="pw123456")
        p = Product.objects.create(name="P", barcode="8300000000001", brand="B",
                                   category=self.perfumes, default_price=Decimal("99"),
                                   wholesale_price=Decimal("88"))
        resp = self.client.post(reverse("edit_product", args=[p.pk]), {
            "barcode": "8300000000001", "category": self.perfumes.id,
            "new_brand_name": "B", "name": "P", "default_price": "99",
            "wholesale_price": "88", "price_locked": "on",
        })
        self.assertIn(resp.status_code, (200, 302))
        p.refresh_from_db()
        self.assertTrue(p.price_locked)
        # An inbound must NOT overwrite the locked prices.
        Purchase.objects.create(product=p, quantity=5, remaining=5, cost_price=Decimal("12.34"))
        p.refresh_from_db()
        self.assertEqual(p.default_price, Decimal("99"))
        self.assertEqual(p.wholesale_price, Decimal("88"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `...python.exe manage.py test stock.tests.PerfumePriceLockFormTests -v 2`
Expected: FAIL — `price_locked` not saved (not on the form), so it stays False and the inbound reprices.

- [ ] **Step 3: Add `price_locked` to the form**

In `stock/forms.py`, add `'price_locked'` to `ProductForm.Meta.fields` (after `'wholesale_price'`).

- [ ] **Step 4: Render the checkbox (manager-only) in the templates**

In `stock/templates/stock/edit_product.html` and `stock/templates/stock/add_product.html`, near the price inputs, add (ensure `{% load access_tags %}` is present at the top — add it only if missing):

```html
{% if user|is_manager_user %}
<div class="mb-2 form-check">
  <input type="checkbox" class="form-check-input" id="price_locked" name="price_locked" {% if form.price_locked.value %}checked{% endif %}>
  <label class="form-check-label" for="price_locked">Lock price (don't auto-update from cost)</label>
</div>
{% endif %}
```

(The field is a plain Bootstrap checkbox posting `price_locked=on`; the `ProductForm` handles it. Non-managers never see it, so employees can't change the lock. No intro paragraph.)

- [ ] **Step 5: Run the test, then the full suite**

```
...python.exe manage.py test stock.tests.PerfumePriceLockFormTests -v 2
...python.exe manage.py test stock
```
Expected: PASS; full suite OK.

- [ ] **Step 6: Commit**

```
git add stock/forms.py stock/templates/stock/edit_product.html stock/templates/stock/add_product.html stock/tests.py
git commit -m "feat: manager-only 'lock price' checkbox on the product form"
```

---

### Task 5: Backfill command `sync_perfume_prices`

**Files:**
- Create: `stock/management/commands/sync_perfume_prices.py`
- Test: `stock/tests.py` (append)

**Interfaces:**
- Consumes: `sync_perfume_price`, `is_perfume`, `PERFUME_CATEGORY_NAME` (Task 2).

- [ ] **Step 1: Write the failing test**

```python
class PerfumeBackfillCommandTests(TestCase):
    def test_backfill_prices_unlocked_perfumes(self):
        from django.core.management import call_command
        from stock.models import Category, Product, Purchase
        perfumes = Category.objects.create(name="Perfumes")
        p = Product.objects.create(name="P", barcode="8400000000001", brand="B", category=perfumes)
        # Create the batch WITHOUT triggering the signal path would be hard; instead
        # set price back to a stale value, then let the command recompute.
        Purchase.objects.create(product=p, quantity=5, remaining=5, cost_price=Decimal("12.34"))
        Product.objects.filter(pk=p.pk).update(default_price=Decimal("1"), wholesale_price=Decimal("1"))
        call_command("sync_perfume_prices")
        p.refresh_from_db()
        self.assertEqual(p.wholesale_price, Decimal("23"))
        self.assertEqual(p.default_price, Decimal("35"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `...python.exe manage.py test stock.tests.PerfumeBackfillCommandTests -v 2`
Expected: FAIL — `Unknown command: 'sync_perfume_prices'`.

- [ ] **Step 3: Create the command**

Create `stock/management/commands/sync_perfume_prices.py`:

```python
from django.core.management.base import BaseCommand

from stock.models import Product
from stock.services.pricing import PERFUME_CATEGORY_NAME, sync_perfume_price


class Command(BaseCommand):
    help = "Recompute wholesale/retail for all Perfumes from their current FIFO cost."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="Report without writing.")

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        qs = Product.objects.filter(category__name__iexact=PERFUME_CATEGORY_NAME).select_related('category')
        updated = skipped = 0
        for product in qs:
            if dry_run:
                # Peek without writing: replicate the skip conditions loosely.
                self.stdout.write(f"[dry-run] would sync {product.barcode} {product.name}")
                continue
            if sync_perfume_price(product):
                updated += 1
            else:
                skipped += 1
        self.stdout.write(self.style.SUCCESS(
            f"Perfume pricing: {updated} updated, {skipped} unchanged/locked/no-cost"
            + (" (dry-run: nothing written)" if dry_run else "")
        ))
```

- [ ] **Step 4: Run the test, then the full suite**

```
...python.exe manage.py test stock.tests.PerfumeBackfillCommandTests -v 2
...python.exe manage.py test stock
```
Expected: PASS; full suite OK.

- [ ] **Step 5: Commit**

```
git add stock/management/commands/sync_perfume_prices.py stock/tests.py
git commit -m "feat: sync_perfume_prices backfill command"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md` (core business patterns), `docs/PRD.md` (a pricing feature entry)

- [ ] **Step 1: Update `docs/ARCHITECTURE.md`**

In the core-business-patterns section (§5), add a subsection describing perfume auto-pricing: the formula (wholesale = ⌈FIFO cost + 10⌉, retail = wholesale + 12), Perfumes-only, `services/pricing.py::sync_perfume_price` (idempotent, `.update()` write, skips locked/non-perfume/cost≤0), the trigger points (Purchase `post_save`; end of `consume_stock_fifo`/`restore_stock_fifo`; the two stock-adjustment APIs), the `Product.price_locked` lock (manager-only checkbox), and the `sync_perfume_prices` backfill command.

- [ ] **Step 2: Update `docs/PRD.md`**

Add a feature entry (under product management / pricing) following neighbouring formatting: perfumes auto-price from current FIFO cost (+10 ceil / +12), recomputed when that cost changes, with a manager lock to pin manual prices.

- [ ] **Step 3: Commit**

```
git add docs/ARCHITECTURE.md docs/PRD.md
git commit -m "docs: record perfume auto-pricing"
```

---

## Manual verification (after Task 6)

Not a task — a smoke check:

1. `...python.exe manage.py sync_perfume_prices --dry-run` then without `--dry-run` → prices the 227 existing perfumes.
2. In the app (as a manager): inbound a perfume at a new cost → its wholesale/retail update to ⌈cost+10⌉ / +12. Tick "Lock price", save → later inbounds no longer change it.
