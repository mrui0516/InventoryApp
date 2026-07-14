# Affects-Inventory Toggle for Historical Orders — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin add a missing historical order that records revenue but does NOT affect live inventory, booking its profit at a flat 50% margin.

**Architecture:** A persisted `SaleOrder.affects_stock` flag (default True) gates all stock consume/restore in the correction service (create/edit/delete). The FIFO profit replay skips no-stock sales (no batch draw) and books them at a flat 50% margin. A create-only checkbox drives the flag.

**Tech Stack:** Django 5.2, SQLite, Django `TestCase` (run via `manage.py test`).

## Global Constraints

- Run tests with system Python (the `.venv` lacks Django):
  `C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe manage.py test ...`
- New field: `SaleOrder.affects_stock = models.BooleanField(default=True)`. Existing rows keep today's behavior.
- `affects_stock` is settable only at **create**; on edit the stored value is kept (never changed by the correction save).
- A no-stock order (`affects_stock=False`) must skip stock ops in ALL of: create (`_consume_current_stock`), edit (`_restore_current_stock` AND `_consume_current_stock`), delete (`_restore_current_stock`). Normal orders are unchanged.
- Profit constant: `BACKFILL_MARGIN = Decimal("0.50")` (profit fraction of revenue). For a no-stock sale: `cost = (revenue * (Decimal("1") - BACKFILL_MARGIN)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`, `profit = revenue - cost`, and it does NOT consume FIFO batches. Normal sales keep the existing FIFO logic exactly.
- Checkbox is create-only, default checked (`name="affects_stock"`; present in POST ⇒ True, absent ⇒ False). Label + control only, no explanatory paragraph.
- `save_sale_order_correction` current signature (from the merged store feature): `save_sale_order_correction(*, order, customer, note, order_datetime, line_items, payment_totals, changed_by, reason, store=None)`. `line_items` items are `{'product': Product, 'quantity': int, 'unit_price': Decimal, 'payment_method': str}`.
- Order-less legacy sales (no `order`) are NOT no-stock (treated as normal FIFO).

---

### Task 1: Model field + migration

**Files:**
- Modify: `stock/models/sales.py` (`SaleOrder`)
- Create: `stock/migrations/0033_saleorder_affects_stock.py` (generated)
- Test: `stock/tests.py` (append `AffectsStockModelTests`)

**Interfaces:**
- Produces: `SaleOrder.affects_stock` (BooleanField, default `True`).

- [ ] **Step 1: Write the failing test**

Append to `stock/tests.py`:

```python
class AffectsStockModelTests(TestCase):
    def test_defaults_true(self):
        from stock.models import SaleOrder
        order = SaleOrder.objects.create(note="x")
        self.assertTrue(order.affects_stock)

    def test_can_set_false(self):
        from stock.models import SaleOrder
        order = SaleOrder.objects.create(note="x", affects_stock=False)
        order.refresh_from_db()
        self.assertFalse(order.affects_stock)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test stock.tests.AffectsStockModelTests -v 2`
Expected: FAIL — `TypeError`/`FieldError` (`affects_stock` is not a field).

- [ ] **Step 3: Add the field**

In `stock/models/sales.py`, inside `class SaleOrder`, add the field (next to the existing `store` field):

```python
    affects_stock = models.BooleanField(default=True)
```

- [ ] **Step 4: Generate the migration**

Run: `python manage.py makemigrations stock`
Expected: creates `stock/migrations/0033_saleorder_affects_stock.py` (an `AddField`, default `True`). If Django assigns a different number because a later migration already exists, use whatever it assigns.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python manage.py test stock.tests.AffectsStockModelTests -v 2`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add stock/models/sales.py stock/migrations/0033_saleorder_affects_stock.py stock/tests.py
git commit -m "feat: add SaleOrder.affects_stock flag (default True)"
```

---

### Task 2: Service — gate stock ops on `affects_stock`

**Files:**
- Modify: `stock/services/order_corrections.py` (`save_sale_order_correction`, `delete_sale_order_correction`, `snapshot_sale_order`)
- Test: `stock/tests.py` (append `AffectsStockServiceTests`)

**Interfaces:**
- Consumes: `SaleOrder.affects_stock` (Task 1).
- Produces: `save_sale_order_correction(..., affects_stock=None)` — on create sets the order's `affects_stock` (`None` ⇒ `True`); on edit keeps the stored value; stock consume/restore only run when the order affects stock. `delete_sale_order_correction` restores stock only when `order.affects_stock`. `snapshot_sale_order` includes `affects_stock`.

- [ ] **Step 1: Write the failing tests**

Append to `stock/tests.py`:

```python
class AffectsStockServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from stock.models import Category, Customer, Product
        cls.admin = get_user_model().objects.create_superuser(username="affstock_admin", password="pw123456")
        cls.category = Category.objects.create(name="Aff Cat")
        cls.customer = Customer.objects.create(nif="900900900", name="Aff Cust")
        cls.product = Product.objects.create(
            name="Asad", barcode="8880001888", brand="Lattafa",
            category=cls.category, default_price=Decimal("25.00"),
        )

    def _purchase(self, remaining=5):
        from stock.models import Purchase
        return Purchase.objects.create(product=self.product, supplier=None, quantity=5,
                                       cost_price=Decimal("10.00"), remaining=remaining)

    def _line_items(self, qty=2):
        return [{"product": self.product, "quantity": qty,
                 "unit_price": Decimal("25.00"), "payment_method": "cash"}]

    def test_create_no_stock_skips_consume(self):
        from stock.services.order_corrections import save_sale_order_correction
        purchase = self._purchase(remaining=5)
        order = save_sale_order_correction(
            order=None, customer=self.customer, note="backfill", order_datetime=timezone.now(),
            line_items=self._line_items(2), payment_totals={"cash": Decimal("50.00")},
            changed_by=self.admin, reason="missed", affects_stock=False,
        )
        purchase.refresh_from_db()
        self.assertFalse(order.affects_stock)
        self.assertEqual(purchase.remaining, 5)          # not consumed
        self.assertEqual(order.items.count(), 1)          # sale recorded

    def test_create_default_consumes(self):
        from stock.services.order_corrections import save_sale_order_correction
        purchase = self._purchase(remaining=5)
        order = save_sale_order_correction(
            order=None, customer=self.customer, note="normal", order_datetime=timezone.now(),
            line_items=self._line_items(2), payment_totals={"cash": Decimal("50.00")},
            changed_by=self.admin, reason="x", affects_stock=None,
        )
        purchase.refresh_from_db()
        self.assertTrue(order.affects_stock)
        self.assertEqual(purchase.remaining, 3)          # consumed 2

    def test_edit_no_stock_order_leaves_stock_untouched(self):
        from stock.models import SaleOrder, Sale
        from stock.services.order_corrections import save_sale_order_correction
        purchase = self._purchase(remaining=5)
        order = SaleOrder.objects.create(customer=self.customer, note="b", affects_stock=False)
        Sale.objects.create(order=order, product=self.product, customer=self.customer,
                            quantity=2, unit_price=Decimal("25.00"), payment_method="cash")
        save_sale_order_correction(
            order=order, customer=self.customer, note="b2", order_datetime=timezone.now(),
            line_items=self._line_items(3), payment_totals={"cash": Decimal("75.00")},
            changed_by=self.admin, reason="x", affects_stock=None,
        )
        purchase.refresh_from_db()
        order.refresh_from_db()
        self.assertFalse(order.affects_stock)             # kept
        self.assertEqual(purchase.remaining, 5)           # neither restored nor consumed
        self.assertEqual(order.items.get().quantity, 3)   # rebuilt

    def test_delete_no_stock_order_does_not_restore(self):
        from stock.models import SaleOrder, Sale
        from stock.services.order_corrections import delete_sale_order_correction
        purchase = self._purchase(remaining=5)
        order = SaleOrder.objects.create(customer=self.customer, note="b", affects_stock=False)
        Sale.objects.create(order=order, product=self.product, customer=self.customer,
                            quantity=2, unit_price=Decimal("25.00"), payment_method="cash")
        delete_sale_order_correction(order=order, changed_by=self.admin, reason="x")
        purchase.refresh_from_db()
        self.assertEqual(purchase.remaining, 5)           # not restored

    def test_snapshot_includes_affects_stock(self):
        from stock.models import SaleOrder
        from stock.services.order_corrections import snapshot_sale_order
        order = SaleOrder.objects.create(customer=self.customer, note="b", affects_stock=False)
        self.assertFalse(snapshot_sale_order(order)["affects_stock"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test stock.tests.AffectsStockServiceTests -v 2`
Expected: FAIL — `save_sale_order_correction()` got an unexpected keyword `affects_stock` (and snapshot KeyError).

- [ ] **Step 3: Gate the save service on `affects_stock`**

In `stock/services/order_corrections.py`, change `save_sale_order_correction`. Add `affects_stock=None` to the signature:

```python
def save_sale_order_correction(*, order, customer, note, order_datetime, line_items, payment_totals, changed_by, reason, store=None, affects_stock=None):
```

Right after `action = 'update' if order else 'create'`, compute the effective flag:

```python
    if action == 'create':
        effective_affects_stock = True if affects_stock is None else bool(affects_stock)
    else:
        effective_affects_stock = order.affects_stock
```

Gate the update-branch restore (wrap the existing `_restore_current_stock(previous_line_items)` call):

```python
    if action == 'update':
        previous_line_items = [
            {'product': item.product, 'quantity': item.quantity}
            for item in order.items.select_related('product').order_by('id')
        ]
        if effective_affects_stock:
            _restore_current_stock(previous_line_items)
```

When creating the order, set the flag:

```python
    if order is None:
        order = SaleOrder.objects.create(customer=customer, note=note or '', store=target_store, affects_stock=effective_affects_stock)
```

Add `affects_stock=effective_affects_stock` to the `SaleOrder.objects.filter(pk=order.pk).update(...)` call (alongside the existing `store=target_store`):

```python
    SaleOrder.objects.filter(pk=order.pk).update(
        customer=customer,
        note=note or '',
        created_at=order_datetime,
        store=target_store,
        affects_stock=effective_affects_stock,
    )
```

Gate the consume (wrap the existing `_consume_current_stock(line_items)` call):

```python
    if effective_affects_stock:
        _consume_current_stock(line_items)
```

- [ ] **Step 4: Gate the delete service**

In `delete_sale_order_correction`, wrap the existing `_restore_current_stock(previous_line_items)`:

```python
    if order.affects_stock:
        _restore_current_stock(previous_line_items)
    order.delete()
```

- [ ] **Step 5: Add `affects_stock` to the snapshot**

In `snapshot_sale_order`'s return dict, add the key right after `'order_id': order.id,` (next to the existing `store_id`/`store_name`):

```python
        'affects_stock': order.affects_stock,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python manage.py test stock.tests.AffectsStockServiceTests -v 2`
Expected: PASS (5 tests).

- [ ] **Step 7: Run the correction suite for regressions**

Run: `python manage.py test stock.tests.SaleOrderCorrectionTests stock.tests.CorrectionStoreChangeServiceTests -v 1`
Expected: PASS (unchanged).

- [ ] **Step 8: Commit**

```bash
git add stock/services/order_corrections.py stock/tests.py
git commit -m "feat: gate correction stock ops on order.affects_stock"
```

---

### Task 3: Profit engine — flat 50% for no-stock sales

**Files:**
- Modify: `stock/services/profit.py` (`sale_profit_map_for_sale_ids`, imports, new constant)
- Test: `stock/tests.py` (append `AffectsStockProfitTests`)

**Interfaces:**
- Consumes: `SaleOrder.affects_stock` (Task 1).
- Produces: `BACKFILL_MARGIN = Decimal("0.50")`; `sale_profit_map_for_sale_ids` books no-stock sales at cost=profit=50% of revenue and excludes them from FIFO batch consumption.

- [ ] **Step 1: Write the failing tests**

Append to `stock/tests.py`:

```python
class AffectsStockProfitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from stock.models import Category, Customer, Product
        cls.category = Category.objects.create(name="Prof Cat")
        cls.customer = Customer.objects.create(nif="700700700", name="Prof Cust")
        cls.product = Product.objects.create(
            name="Asad", barcode="6660001666", brand="Lattafa",
            category=cls.category, default_price=Decimal("25.00"),
        )

    def _purchase(self, qty, cost, when):
        from stock.models import Purchase
        p = Purchase.objects.create(product=self.product, supplier=None, quantity=qty,
                                    cost_price=Decimal(cost), remaining=qty)
        Purchase.objects.filter(pk=p.pk).update(date=when)
        return p

    def _sale(self, qty, price, when, affects_stock=True):
        from stock.models import SaleOrder, Sale
        order = SaleOrder.objects.create(customer=self.customer, affects_stock=affects_stock)
        s = Sale.objects.create(order=order, product=self.product, customer=self.customer,
                                quantity=qty, unit_price=Decimal(price), payment_method="cash")
        Sale.objects.filter(pk=s.pk).update(date=when)
        return Sale.objects.get(pk=s.pk)

    def test_no_stock_sale_booked_at_50pct(self):
        from stock.services.profit import sale_profit_map_for_sale_ids
        base = timezone.now() - timedelta(days=5)
        self._purchase(10, "10.00", base)
        s = self._sale(2, "25.00", base + timedelta(hours=1), affects_stock=False)
        m = sale_profit_map_for_sale_ids([s.id])[s.id]
        self.assertEqual(m["revenue"], Decimal("50.00"))
        self.assertEqual(m["cost"], Decimal("25.00"))
        self.assertEqual(m["profit"], Decimal("25.00"))

    def test_no_stock_sale_does_not_distort_normal_fifo(self):
        from stock.services.profit import sale_profit_map_for_sale_ids
        base = timezone.now() - timedelta(days=5)
        self._purchase(3, "10.00", base)                                   # only 3 units @10
        no_stock = self._sale(2, "25.00", base + timedelta(hours=1), affects_stock=False)
        normal = self._sale(3, "25.00", base + timedelta(hours=2), affects_stock=True)
        m = sale_profit_map_for_sale_ids([normal.id, no_stock.id])
        # normal sale draws all 3 purchased units @10 => cost 30, unaffected by the no-stock sale
        self.assertEqual(m[normal.id]["cost"], Decimal("30.00"))
        self.assertEqual(m[normal.id]["profit"], Decimal("45.00"))
        # no-stock sale is flat 50%
        self.assertEqual(m[no_stock.id]["cost"], Decimal("25.00"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test stock.tests.AffectsStockProfitTests -v 2`
Expected: FAIL — under current FIFO the no-stock sale draws batch cost (cost `20.00`, not `25.00`) and consumes units so the normal sale's cost is wrong.

- [ ] **Step 3: Implement the flat-margin path**

In `stock/services/profit.py`, update the import and add the constant near the top:

```python
from decimal import Decimal, ROUND_HALF_UP
```

```python
BACKFILL_MARGIN = Decimal("0.50")  # no-stock backfill orders book a flat 50% profit margin
```

After `end_day` is computed (before building `purchase_events`), load the no-stock sale ids:

```python
    no_stock_sale_ids = set(
        Sale.objects.filter(order__affects_stock=False, date__date__lte=end_day)
        .values_list("id", flat=True)
    )
```

In the event loop, handle no-stock `"out"` events before the FIFO block. Replace the body that starts at `remaining = quantity` so that a no-stock sale skips batch consumption:

```python
    for event_type, event_dt, product_id, quantity, amount, event_id in events:
        if event_type == "in":
            stock_batches[product_id].append([quantity, amount or Decimal("0.00")])
            continue

        if event_id in no_stock_sale_ids:
            # Backfill order: does not consume FIFO batches; flat margin.
            if event_id in relevant_sale_ids:
                revenue = (amount or Decimal("0.00")) * quantity
                cost = (revenue * (Decimal("1") - BACKFILL_MARGIN)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                profit_map[event_id] = {"revenue": revenue, "cost": cost, "profit": revenue - cost}
            continue

        remaining = quantity
        line_cost = Decimal("0.00")

        while remaining > 0 and stock_batches[product_id]:
            batch_quantity, batch_cost = stock_batches[product_id][0]
            used = min(batch_quantity, remaining)
            line_cost += Decimal(used) * (batch_cost or Decimal("0.00"))
            batch_quantity -= used
            remaining -= used

            if batch_quantity == 0:
                stock_batches[product_id].pop(0)
            else:
                stock_batches[product_id][0][0] = batch_quantity

        if event_id in relevant_sale_ids:
            revenue = (amount or Decimal("0.00")) * quantity
            profit_map[event_id] = {
                "revenue": revenue,
                "cost": line_cost,
                "profit": revenue - line_cost,
            }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test stock.tests.AffectsStockProfitTests -v 2`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the profit/dashboard suites for regressions**

Run: `python manage.py test stock.tests -v 0`
Expected: OK (all tests pass; existing profit/dashboard behavior for normal sales unchanged).

- [ ] **Step 6: Commit**

```bash
git add stock/services/profit.py stock/tests.py
git commit -m "feat: book no-stock backfill sales at a flat 50% margin"
```

---

### Task 4: View + template — create-only checkbox

**Files:**
- Modify: `stock/views.py` (`_sale_order_correction_view`)
- Modify: `stock/templates/stock/sale_order_correction_form.html` (order-header area)
- Test: `stock/tests.py` (append `AffectsStockViewTests`)

**Interfaces:**
- Consumes: `save_sale_order_correction(..., affects_stock=...)` (Task 2).
- Produces: the create page renders a checked `name="affects_stock"` checkbox; posting it unchecked creates a no-stock order.

- [ ] **Step 1: Write the failing tests**

Append to `stock/tests.py`:

```python
class AffectsStockViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from stock.models import Category, Customer, Product
        cls.admin = get_user_model().objects.create_superuser(username="affview_admin", password="pw123456")
        cls.category = Category.objects.create(name="AffView Cat")
        cls.customer = Customer.objects.create(nif="500500500", name="AffView Cust")
        cls.product = Product.objects.create(
            name="Asad", barcode="5550001555", brand="Lattafa",
            category=cls.category, default_price=Decimal("25.00"),
        )

    def _purchase(self, remaining=5):
        from stock.models import Purchase
        return Purchase.objects.create(product=self.product, supplier=None, quantity=5,
                                       cost_price=Decimal("10.00"), remaining=remaining)

    def _payload(self, **overrides):
        data = {
            "customer": self.customer.id,
            "order_datetime": timezone.localtime(timezone.now()).strftime("%Y-%m-%dT%H:%M"),
            "note": "backfill",
            "reason": "missed historical order",
            "items_json": json.dumps([{"product_id": self.product.id, "qty": 2, "price": "25.00", "payment": "cash"}]),
            "payments_json": json.dumps([{"method": "cash", "amount": "50.00"}]),
        }
        data.update(overrides)
        return data

    def test_create_page_shows_checkbox_checked(self):
        self.client.login(username="affview_admin", password="pw123456")
        resp = self.client.get(reverse("sale_order_correction_create"))
        self.assertContains(resp, 'name="affects_stock"')
        self.assertContains(resp, 'id="affects-stock"')

    def test_unchecked_creates_no_stock_order(self):
        from stock.models import SaleOrder
        purchase = self._purchase(remaining=5)
        self.client.login(username="affview_admin", password="pw123456")
        # affects_stock omitted from POST => unchecked
        resp = self.client.post(reverse("sale_order_correction_create"), data=self._payload())
        self.assertEqual(resp.status_code, 302)
        order = SaleOrder.objects.latest("id")
        purchase.refresh_from_db()
        self.assertFalse(order.affects_stock)
        self.assertEqual(purchase.remaining, 5)          # not consumed

    def test_checked_creates_stock_order(self):
        from stock.models import SaleOrder
        purchase = self._purchase(remaining=5)
        self.client.login(username="affview_admin", password="pw123456")
        resp = self.client.post(reverse("sale_order_correction_create"), data=self._payload(affects_stock="on"))
        self.assertEqual(resp.status_code, 302)
        order = SaleOrder.objects.latest("id")
        purchase.refresh_from_db()
        self.assertTrue(order.affects_stock)
        self.assertEqual(purchase.remaining, 3)          # consumed 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test stock.tests.AffectsStockViewTests -v 2`
Expected: FAIL — no `name="affects_stock"` in the page; and the unchecked POST currently consumes stock (order defaults `affects_stock=True`).

- [ ] **Step 3: Parse and pass the flag in the view**

In `stock/views.py`, inside `_sale_order_correction_view`'s `if form.is_valid():` block, just before the `saved_order = save_sale_order_correction(` call (which already passes `store=selected_store`), add the parse and pass it as a new argument:

```python
                    affects_stock = ('affects_stock' in request.POST) if order is None else None
                    saved_order = save_sale_order_correction(
                        order=order,
                        customer=form.cleaned_data.get('customer'),
                        note=form.cleaned_data.get('note'),
                        order_datetime=form.cleaned_data['order_datetime'],
                        line_items=line_items,
                        payment_totals=payment_totals,
                        changed_by=request.user,
                        reason=form.cleaned_data['reason'],
                        store=selected_store,
                        affects_stock=affects_stock,
                    )
```

- [ ] **Step 4: Add the checkbox render state to context**

In `_sale_order_correction_view`, just before the final `return render(...)`, compute the checked state (default checked; on a POST re-render reflect what was posted):

```python
    affects_stock_checked = ('affects_stock' in request.POST) if request.method == 'POST' else True
```

Add this key to the render context dict:

```python
        'affects_stock_checked': affects_stock_checked,
```

- [ ] **Step 5: Add the checkbox to the template**

In `stock/templates/stock/sale_order_correction_form.html`, in the order-header `.row g-3` (after the `reason` field's `</div>`, before the row closes), add a create-only checkbox:

```html
            {% if is_create %}
            <div class="col-12">
              <label class="form-label fw-bold">
                <input type="checkbox" name="affects_stock" id="affects-stock" {% if affects_stock_checked %}checked{% endif %}>
                Affects inventory
              </label>
            </div>
            {% endif %}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python manage.py test stock.tests.AffectsStockViewTests -v 2`
Expected: PASS (3 tests).

- [ ] **Step 7: Run the correction suite for regressions**

Run: `python manage.py test stock.tests.SaleOrderCorrectionTests stock.tests.CorrectionStoreChangeViewTests -v 1`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add stock/views.py stock/templates/stock/sale_order_correction_form.html stock/tests.py
git commit -m "feat: Affects-inventory checkbox on Add Missing Historical Order"
```

---

### Task 5: Documentation

**Files:**
- Modify: `docs/STATUS.md`, `docs/ARCHITECTURE.md`, `docs/PRD.md`

- [ ] **Step 1: Update STATUS.md**

In `docs/STATUS.md`, insert a dated changelog entry (matching the surrounding Chinese style) immediately before the line starting `- 2026-07-13：**订单修正支持改店铺**`:

```markdown
- 2026-07-14：**新增历史订单支持「是否影响库存」**（补录遗漏订单不重复扣库存）。`SaleOrder` 加 `affects_stock` 布尔字段（默认 True，迁移 0033）；新增页（Add Missing Historical Order）加复选框「Affects inventory」（默认勾选，仅创建页，编辑页不显示）。取消勾选的单：创建/编辑/删除全程不扣不还库存（`save_sale_order_correction`/`delete_sale_order_correction` 据 `affects_stock` 门控），仍正常记销售额/支付/客户历史。利润引擎 `sale_profit_map_for_sale_ids`：这类单**不参与 FIFO 批次消耗**、成本/利润各按销售额 50%（常量 `BACKFILL_MARGIN`）；普通单 FIFO 不变。`snapshot_sale_order` 快照加 `affects_stock`。新增测试（模型默认/服务门控创建·编辑·删除/利润 50% 且不干扰普通单 FIFO/视图复选框），全测试通过。文档同步 PRD F2.9.x。
```

- [ ] **Step 2: Update ARCHITECTURE.md**

In `docs/ARCHITECTURE.md` section 5.5 (历史订单修正与审计), append:

```markdown
- 补录订单可选「不影响库存」：`SaleOrder.affects_stock`（默认 True）门控创建/编辑/删除的库存扣减与归还；`sale_profit_map_for_sale_ids` 对 `affects_stock=False` 的销售跳过 FIFO 批次、成本/利润各取销售额 50%（`BACKFILL_MARGIN`）。
```

- [ ] **Step 3: Update PRD.md**

In `docs/PRD.md`, under 模块 2.9 (订单修正), add a bullet (use the next free F2.9 sub-number after the existing store-change bullet):

```markdown
- **F2.9.6 补录订单可不影响库存**：新增历史订单时可取消「Affects inventory」，该单只记销售额/支付、不扣库存（用于货已出、库存已对账的遗漏订单），利润按销售额 50% 估算，且不参与 FIFO 成本；默认勾选＝正常扣库存。
```

- [ ] **Step 4: Commit**

```bash
git add docs/STATUS.md docs/ARCHITECTURE.md docs/PRD.md
git commit -m "docs: document affects-inventory toggle for historical orders"
```

---

## Self-Review

**Spec coverage:**
- `SaleOrder.affects_stock` field + migration → Task 1. ✓
- Create-only checkbox, default checked → Task 4 Steps 4-5. ✓
- No-stock skips consume (create), restore+consume (edit), restore (delete) → Task 2 Steps 3-4. ✓
- `affects_stock` settable on create, kept on edit → Task 2 Step 3. ✓
- Flat 50% margin + no FIFO batch draw for no-stock sales → Task 3 Step 3. ✓
- Normal orders/sales unchanged → gating leaves the True path identical; profit change only touches no-stock ids. ✓
- Snapshot includes affects_stock → Task 2 Step 5. ✓
- Order-less sales treated as normal FIFO → the no-stock set is `order__affects_stock=False`, which excludes null-order sales. ✓
- Tests (model, service create/edit/delete, profit 50% + no distortion, view checkbox) → Tasks 1-4. ✓
- Docs → Task 5. ✓

**Placeholder scan:** none — every step carries concrete code/commands.

**Type consistency:** `save_sale_order_correction(..., affects_stock=None)`, `SaleOrder.affects_stock` (bool), `BACKFILL_MARGIN` (`Decimal("0.50")`), profit_map dict keys `revenue`/`cost`/`profit`, template var `affects_stock_checked`, and POST field `affects_stock` are used consistently across tasks.
