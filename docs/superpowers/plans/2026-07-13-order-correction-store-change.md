# Order Correction Store Change Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin re-attribute a sale order to a different store from the Order Correction form, to fix orders entered under the wrong store.

**Architecture:** Add a store `<select>` to the (already admin-only) correction form. On save, the selected store wins in `save_sale_order_correction`, which already stamps the store onto the `SaleOrder` and every rebuilt `Sale` line. The audit snapshot gains the store so the change log records old→new.

**Tech Stack:** Django 5.2, SQLite, Django `TestCase` (run via `manage.py test`).

## Global Constraints

- Run tests with system Python (the `.venv` lacks Django):
  `C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe manage.py test ...`
- The correction views are already `@admin_required`; do NOT add or change access control.
- A store change moves the `SaleOrder` **and** every rebuilt `Sale` line to the selected store. `SaleOrderPayment` has no store field — do not touch it. Do NOT touch `ARInvoice`.
- Selectable stores are **active stores only** (`stock.stores.available_stores()`), which returns `list(Store.objects.filter(is_active=True))`.
- A missing/invalid posted store must fall back to the order's current store (edit) or the active store (create) — never null the store.
- Default selected store: the order's current store on edit; the active store (`store_for_new_sale(request)`) on create.
- UI: label + control only, no explanatory paragraph.
- `save_sale_order_correction` keyword signature (unchanged): `save_sale_order_correction(*, order, customer, note, order_datetime, line_items, payment_totals, changed_by, reason, store=None)`. `line_items` is a list of `{'product': Product, 'quantity': int, 'unit_price': Decimal, 'payment_method': str}`; `payment_totals` is `{method: Decimal}`.

---

### Task 1: Service — selected store wins + snapshot captures store

**Files:**
- Modify: `stock/services/order_corrections.py` (`snapshot_sale_order` ~line 21-27; `save_sale_order_correction` target-store line 102)
- Test: `stock/tests.py` (append `CorrectionStoreChangeServiceTests`)

**Interfaces:**
- Produces: `snapshot_sale_order(order)` return dict now includes `store_id` and `store_name`. `save_sale_order_correction(..., store=<Store>)` re-attributes an existing order (and its rebuilt sales) to `store` when `store` is not None.

- [ ] **Step 1: Write the failing tests**

Append to `stock/tests.py` (imports `Decimal`, `timezone`, `get_user_model`, and models are already used elsewhere in this file; add any that are missing at the top of the file):

```python
class CorrectionStoreChangeServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from stock.models import Store, Category, Customer, Product
        cls.admin = get_user_model().objects.create_superuser(username="corr_store_svc_admin", password="pw123456")
        cls.store_a = Store.objects.create(name="Store A", code="CSA")
        cls.store_b = Store.objects.create(name="Store B", code="CSB")
        cls.category = Category.objects.create(name="Corr Store Cat")
        cls.customer = Customer.objects.create(nif="111222333", name="Corr Store Cust")
        cls.product = Product.objects.create(
            name="Asad", barcode="7770001777", brand="Lattafa",
            category=cls.category, default_price=Decimal("25.00"),
        )

    def _make_order_in_store(self, store):
        from stock.models import Purchase, SaleOrder, Sale
        Purchase.objects.create(product=self.product, supplier=None, quantity=10,
                                cost_price=Decimal("10.00"), remaining=10)
        order = SaleOrder.objects.create(customer=self.customer, note="o", store=store)
        Sale.objects.create(order=order, product=self.product, customer=self.customer,
                            store=store, quantity=2, unit_price=Decimal("25.00"),
                            payment_method="cash")
        return order

    def _line_items(self):
        return [{"product": self.product, "quantity": 2,
                 "unit_price": Decimal("25.00"), "payment_method": "cash"}]

    def test_selected_store_moves_order_and_sales(self):
        from stock.services.order_corrections import save_sale_order_correction
        order = self._make_order_in_store(self.store_a)
        save_sale_order_correction(
            order=order, customer=self.customer, note="o", order_datetime=timezone.now(),
            line_items=self._line_items(), payment_totals={"cash": Decimal("50.00")},
            changed_by=self.admin, reason="wrong store", store=self.store_b,
        )
        order.refresh_from_db()
        self.assertEqual(order.store_id, self.store_b.id)
        self.assertTrue(all(s.store_id == self.store_b.id for s in order.items.all()))

    def test_no_store_passed_keeps_current(self):
        from stock.services.order_corrections import save_sale_order_correction
        order = self._make_order_in_store(self.store_a)
        save_sale_order_correction(
            order=order, customer=self.customer, note="o", order_datetime=timezone.now(),
            line_items=self._line_items(), payment_totals={"cash": Decimal("50.00")},
            changed_by=self.admin, reason="x", store=None,
        )
        order.refresh_from_db()
        self.assertEqual(order.store_id, self.store_a.id)

    def test_snapshot_captures_store(self):
        from stock.services.order_corrections import snapshot_sale_order
        order = self._make_order_in_store(self.store_a)
        snap = snapshot_sale_order(order)
        self.assertEqual(snap["store_id"], self.store_a.id)
        self.assertEqual(snap["store_name"], "Store A")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test stock.tests.CorrectionStoreChangeServiceTests -v 2`
Expected: `test_selected_store_moves_order_and_sales` FAILS (store stays Store A) and `test_snapshot_captures_store` FAILS (KeyError `store_id`). `test_no_store_passed_keeps_current` may already pass.

- [ ] **Step 3: Capture store in the snapshot**

In `stock/services/order_corrections.py`, in `snapshot_sale_order`'s return dict, add the two store keys right after `'order_id': order.id,`:

```python
    return {
        'order_id': order.id,
        'store_id': order.store_id,
        'store_name': order.store.name if order.store else '',
        'customer_id': order.customer_id,
```

- [ ] **Step 4: Make the passed store win**

In `save_sale_order_correction`, replace the `target_store` line (currently line 102):

```python
    target_store = (order.store if (order and order.store_id) else None) or store
```

with:

```python
    # An explicitly selected store wins (lets a correction move the order between
    # stores); otherwise keep the existing order's store.
    target_store = store if store is not None else (
        order.store if (order and order.store_id) else None
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python manage.py test stock.tests.CorrectionStoreChangeServiceTests -v 2`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add stock/services/order_corrections.py stock/tests.py
git commit -m "feat: let correction store selection move an order between stores"
```

---

### Task 2: View + template — store selector wiring

**Files:**
- Modify: `stock/views.py` (`_sale_order_correction_view`, ~lines 2380-2451; and the `.stores` import)
- Modify: `stock/templates/stock/sale_order_correction_form.html` (order-header row, after line 111)
- Test: `stock/tests.py` (append `CorrectionStoreChangeViewTests`)

**Interfaces:**
- Consumes: `save_sale_order_correction(..., store=<Store>)` (Task 1); `stock.stores.available_stores()` and `store_for_new_sale(request)`.
- Produces: correction form renders `<select name="store">` of active stores with the current store preselected; posting `store=<id>` moves the order there.

- [ ] **Step 1: Write the failing tests**

Append to `stock/tests.py`:

```python
class CorrectionStoreChangeViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from stock.models import Store, Category, Customer, Product
        cls.admin = get_user_model().objects.create_superuser(username="corr_store_view_admin", password="pw123456")
        cls.store_a = Store.objects.create(name="Store A", code="VSA")
        cls.store_b = Store.objects.create(name="Store B", code="VSB")
        cls.category = Category.objects.create(name="Corr View Cat")
        cls.customer = Customer.objects.create(nif="222333444", name="Corr View Cust")
        cls.product = Product.objects.create(
            name="Asad", barcode="7770002777", brand="Lattafa",
            category=cls.category, default_price=Decimal("25.00"),
        )

    def _order_in_a(self):
        from stock.models import Purchase, SaleOrder, Sale
        Purchase.objects.create(product=self.product, supplier=None, quantity=10,
                                cost_price=Decimal("10.00"), remaining=10)
        order = SaleOrder.objects.create(customer=self.customer, note="o", store=self.store_a)
        Sale.objects.create(order=order, product=self.product, customer=self.customer,
                            store=self.store_a, quantity=2, unit_price=Decimal("25.00"),
                            payment_method="cash")
        return order

    def test_form_shows_store_selector(self):
        order = self._order_in_a()
        self.client.login(username="corr_store_view_admin", password="pw123456")
        resp = self.client.get(reverse("sale_order_correction_edit", args=[order.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="store"')
        self.assertContains(resp, "Store B")

    def test_post_moves_order_to_selected_store(self):
        order = self._order_in_a()
        self.client.login(username="corr_store_view_admin", password="pw123456")
        resp = self.client.post(reverse("sale_order_correction_edit", args=[order.id]), data={
            "customer": self.customer.id,
            "order_datetime": timezone.localtime(timezone.now()).strftime("%Y-%m-%dT%H:%M"),
            "note": "o",
            "reason": "Entered under the wrong store",
            "store": self.store_b.id,
            "items_json": json.dumps([{"product_id": self.product.id, "qty": 2, "price": "25.00", "payment": "cash"}]),
            "payments_json": json.dumps([{"method": "cash", "amount": "50.00"}]),
        })
        self.assertEqual(resp.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.store_id, self.store_b.id)
        self.assertTrue(all(s.store_id == self.store_b.id for s in order.items.all()))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test stock.tests.CorrectionStoreChangeViewTests -v 2`
Expected: `test_form_shows_store_selector` FAILS (no `name="store"`); `test_post_moves_order_to_selected_store` FAILS (order stays Store A, because the view still passes `store_for_new_sale(request)` and Task 1's service keeps the order store when the active store differs).

- [ ] **Step 3: Import `available_stores` in the view**

In `stock/views.py`, find the existing import of `store_for_new_sale` from `.stores` and add `available_stores`. It currently reads (search for `store_for_new_sale`):

```python
from .stores import store_for_new_sale
```

Change to:

```python
from .stores import available_stores, store_for_new_sale
```

(If `store_for_new_sale` is imported on a multi-name line, just add `available_stores` to that line's name list instead.)

- [ ] **Step 4: Resolve the selected store and pass it to the service**

In `_sale_order_correction_view`, inside the `if form.is_valid():` block, replace the `save_sale_order_correction(...)` call's `store=store_for_new_sale(request),` argument. Immediately before the `saved_order = save_sale_order_correction(` line, insert the resolution, and change the argument:

```python
                    _active_stores = available_stores()
                    _raw_store = request.POST.get('store')
                    selected_store = next(
                        (s for s in _active_stores if str(s.id) == str(_raw_store)), None
                    )
                    if selected_store is None:
                        selected_store = (order.store if (order and order.store_id) else None) or store_for_new_sale(request)
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
                    )
```

- [ ] **Step 5: Add store context for the template**

In `_sale_order_correction_view`, just before the final `return render(request, 'stock/sale_order_correction_form.html', {`, compute the store list and default selection:

```python
    stores_for_template = available_stores()
    _raw_selected = request.POST.get('store') if request.method == 'POST' else None
    selected_store_id = None
    if _raw_selected:
        _match = next((s for s in stores_for_template if str(s.id) == str(_raw_selected)), None)
        selected_store_id = _match.id if _match else None
    if selected_store_id is None:
        _default_store = (order.store if (order and order.store_id) else None) or store_for_new_sale(request)
        selected_store_id = _default_store.id if _default_store else None
```

Then add these two keys to the render context dict:

```python
        'available_stores': stores_for_template,
        'selected_store_id': selected_store_id,
```

- [ ] **Step 6: Add the store `<select>` to the template**

In `stock/templates/stock/sale_order_correction_form.html`, insert this block immediately after the `Order datetime` field's closing `</div>` (after line 111, before the `Internal note` `<div class="col-12">`):

```html
            {% if available_stores %}
            <div class="col-md-6">
              <label class="form-label fw-bold" for="order-store">Store</label>
              <select name="store" id="order-store" class="form-select">
                {% for s in available_stores %}
                  <option value="{{ s.id }}" {% if s.id == selected_store_id %}selected{% endif %}>{{ s.name }}</option>
                {% endfor %}
              </select>
            </div>
            {% endif %}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python manage.py test stock.tests.CorrectionStoreChangeViewTests -v 2`
Expected: PASS (2 tests).

- [ ] **Step 8: Run the correction test suite for regressions**

Run: `python manage.py test stock.tests.SaleOrderCorrectionTests -v 1`
Expected: PASS (existing correction tests unaffected).

- [ ] **Step 9: Commit**

```bash
git add stock/views.py stock/templates/stock/sale_order_correction_form.html stock/tests.py
git commit -m "feat: store selector on Order Correction form"
```

---

### Task 3: Documentation

**Files:**
- Modify: `docs/STATUS.md`, `docs/ARCHITECTURE.md`, `docs/PRD.md`

- [ ] **Step 1: Update STATUS.md**

In `docs/STATUS.md`, add a dated changelog entry near the most recent ones (matching the existing Chinese changelog style), e.g. immediately before the line beginning `- 2026-07-13：**产品图自动镜像到 Cloudinary**`:

```markdown
- 2026-07-13：**订单修正支持改店铺**（修复下错店铺的订单）。修正表单（仅管理员）新增 Store 下拉（列活跃店铺，默认选中订单当前店铺/活跃店铺）；`save_sale_order_correction` 改为「显式所选店铺优先」（编辑也生效），保存时把订单及其重建的全部明细行 `Sale.store` 一并改到所选店铺（`SaleOrderPayment` 无店铺字段不动、不涉及 AR）；`snapshot_sale_order` 快照加 `store_id/store_name`，审计日志记录 旧→新 店铺。库存全店共享不受影响；当日汇总由既有 Sale 信号按日期重算。新增测试（服务：选店铺移动订单+明细、不传保持原店铺、快照含店铺；视图：表单显示下拉、POST 改店铺移动订单），全测试通过。文档同步 PRD F2.9.x。
```

- [ ] **Step 2: Update ARCHITECTURE.md**

In `docs/ARCHITECTURE.md`, section 5.5 (历史订单修正与审计, `services/order_corrections.py`), append a sentence noting the store change:

```markdown
- 订单修正保存时可改归属店铺：`save_sale_order_correction(store=...)` 显式所选店铺优先，盖到 `SaleOrder` 与重建的每条 `Sale`；`snapshot_sale_order` 记录 `store_id/store_name` 供审计。仅活跃店铺可选，缺失/非法回退原店铺。
```

- [ ] **Step 3: Update PRD.md**

In `docs/PRD.md`, under the order-correction feature (F2.9.x), add:

```markdown
- **F2.9.x 修正时改店铺**：管理员在订单修正表单可选择本单归属店铺（仅活跃店铺），用于修复下错店铺的订单；保存后订单与其全部明细行改到所选店铺，审计日志记录 旧→新 店铺。库存不受影响，不涉及应收(AR)。
```

- [ ] **Step 4: Commit**

```bash
git add docs/STATUS.md docs/ARCHITECTURE.md docs/PRD.md
git commit -m "docs: document order-correction store change"
```

---

## Self-Review

**Spec coverage:**
- Selected store wins on save (edit + create) → Task 1 Step 4. ✓
- Store propagates to order + rebuilt Sale lines → existing service logic (verified by Task 1/2 tests). ✓
- SaleOrderPayment/AR untouched → no code touches them; constraint stated. ✓
- Admin-only (no new gating) → unchanged; page already `@admin_required`. ✓
- Active stores only, default current/active, no-null fallback → Task 2 Steps 4-6. ✓
- Audit snapshot records store → Task 1 Step 3. ✓
- Tests (service move, no-store keeps current, snapshot store, view shows selector, view POST moves) → Tasks 1-2. ✓
- Docs (STATUS/ARCHITECTURE/PRD) → Task 3. ✓

**Placeholder scan:** none — all steps carry concrete code/commands.

**Type consistency:** `save_sale_order_correction(..., store=<Store>)`, `snapshot_sale_order` keys `store_id`/`store_name`, `available_stores()` (list of active `Store`), and context keys `available_stores`/`selected_store_id` are used consistently across tasks. Template compares `s.id == selected_store_id` (both ints).
