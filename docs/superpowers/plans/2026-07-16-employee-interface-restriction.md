# Employee Interface Restriction & Auto-Attendance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict non-manager employees to Dashboard, Outbound (POS), a single-day order-only Sales view, and a search+add-only Customers view; block every other page; make attendance automatic from login/logout.

**Architecture:** Reuse the existing `manager_required` decorator + `is_manager_user` template filter. Blocked pages get `@manager_required` (redirect to dashboard). The two kept-but-restricted pages branch at the top of their view to a dedicated employee helper that renders a lean template — the manager code paths and templates are untouched. Attendance is driven by `user_logged_in` / `user_logged_out` auth signals; no model or migration change.

**Tech Stack:** Django 5.2.4, SQLite, `django.test.TestCase` + `self.client`.

Spec: `docs/superpowers/specs/2026-07-16-employee-interface-restriction-design.md`

## Global Constraints

- **Employee = authenticated user where `permissions.has_manager_access(user)` is False.** Managers (superuser / `is_staff` / group "Managers") are unchanged everywhere.
- Employee page set is exactly: **Dashboard, Outbound (+ its POST/AJAX/autocomplete endpoints), Sales (restricted), Customers (restricted), `add_customer`, `sale_order_detail`, store switch, login/logout.** Everything else is blocked.
- Blocked view → `manager_required` redirects to `dashboard` (302), never a 403.
- Restricted Sales: single day only (GET `date`, default today), € amounts shown, **no** charts / yearly overview / purchases; scope = active store.
- Restricted Customers: **no rows until a query is entered**; results show name / phone / email only (no spend / balance / history / chart); "Add customer" kept.
- Auto-attendance applies to **employees only**; managers are never auto-clocked. No schema/migration change (`AttendanceRecord(user, clock_in_at, clock_out_at, note)` already fits).
- Do not modify manager-facing templates (`sales_records.html`, `customer_search.html`) or the manager branches of `record_view` / `customer_search_view`.
- Run tests with the system Python: `C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe manage.py test stock`. Django prints results to stderr — capture full output, don't tail.
- Baseline before this plan: **147 tests pass.** Several existing tests encode the OLD policy (employees may view these pages) and MUST be updated to the new policy — each task below lists the known ones; **the full-suite run is the source of truth**: update every failure that stems from this intended change.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `stock/templates/stock/base.html` (modify) | Sidebar: hide employee-forbidden links behind `is_manager_user`. |
| `stock/views.py` (modify) | `@manager_required` on blocked views; employee branches + helpers in `record_view` / `customer_search_view`; `@manager_required` on `attendance_view`. |
| `stock/templates/stock/sales_records_employee.html` (create) | Lean single-day order list for employees. |
| `stock/templates/stock/customer_search_employee.html` (create) | Lean search + add for employees. |
| `stock/signals.py` (modify) | `user_logged_in` / `user_logged_out` receivers for auto-attendance. |
| `stock/tests.py` (modify) | Update OLD-policy tests; add new employee-restriction + auto-attendance tests. |
| `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/STATUS.md` (modify) | Record the new employee policy and auto-attendance. |

---

## Test helpers (already in `stock/tests.py`)

`get_user_model`, `reverse`, `timezone`, `timedelta`, `AttendanceRecord`, `Customer`, `SaleOrder`, `Sale` are imported at the top. Create an employee with `create_user(username=..., password=...)` (no `is_staff`), a manager with `create_user(..., is_staff=True)` or `create_superuser(...)`.

Uniform assertion for "employee is blocked from view X":

```python
resp = self.client.get(reverse("X"))
self.assertEqual(resp.status_code, 302)
self.assertIn(reverse("dashboard"), resp.headers["Location"])
```

---

### Task 1: Sidebar — hide employee-forbidden links

**Files:**
- Modify: `stock/templates/stock/base.html` (nav block, lines 59-87)
- Test: `stock/tests.py` (append a class)

**Interfaces:**
- Consumes: `access_tags.is_manager_user` (existing template filter, already loaded in base.html).
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the failing test**

Append to `stock/tests.py`:

```python
class EmployeeNavTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.employee = user_model.objects.create_user(username="nav_emp", password="pw123456")
        cls.manager = user_model.objects.create_user(username="nav_mgr", password="pw123456", is_staff=True)

    def _nav(self, username):
        self.client.login(username=username, password="pw123456")
        return self.client.get(reverse("dashboard"))

    def test_employee_sidebar_shows_only_allowed_links(self):
        resp = self._nav("nav_emp")
        for name in ["dashboard", "outbound", "sales_records", "customer_search"]:
            self.assertContains(resp, 'href="%s"' % reverse(name))
        for name in ["daily_summary", "inbound", "attendance", "product_list", "catalog", "ar_list"]:
            self.assertNotContains(resp, 'href="%s"' % reverse(name))

    def test_manager_sidebar_shows_everything(self):
        resp = self._nav("nav_mgr")
        for name in ["daily_summary", "inbound", "attendance", "product_list", "catalog", "ar_list", "sales_records", "customer_search"]:
            self.assertContains(resp, 'href="%s"' % reverse(name))
```

- [ ] **Step 2: Run to verify it fails**

Run: `...python.exe manage.py test stock.tests.EmployeeNavTests -v 2`
Expected: `test_employee_sidebar_shows_only_allowed_links` FAILS (forbidden links still present).

- [ ] **Step 3: Edit the nav block**

Replace `stock/templates/stock/base.html` lines 59-87 (the `<nav class="erp-nav">…</nav>`) with:

```html
      <nav class="erp-nav">
        <a class="erp-nav-link {% if current_url == 'dashboard' %}active{% endif %}" href="{% url 'dashboard' %}">Dashboard</a>
        {% if user|is_manager_user %}
        <a class="erp-nav-link {% if current_url == 'daily_summary' %}active{% endif %}" href="{% url 'daily_summary' %}">Today</a>
        {% endif %}

        <div class="erp-nav-group">Operations</div>
        {% if user|is_manager_user %}
        <a class="erp-nav-link {% if current_url == 'inbound' %}active{% endif %}" href="{% url 'inbound' %}">Inbound</a>
        {% endif %}
        <a class="erp-nav-link {% if current_url == 'outbound' %}active{% endif %}" href="{% url 'outbound' %}">Outbound</a>
        {% if user|is_manager_user %}
        <a class="erp-nav-link {% if current_url == 'attendance' %}active{% endif %}" href="{% url 'attendance' %}">Attendance</a>
        {% endif %}

        {% if user|is_manager_user %}
        <div class="erp-nav-group">Catalog</div>
        <a class="erp-nav-link {% if current_url in 'product_list,product_detail,edit_product,add_product' %}active{% endif %}" href="{% url 'product_list' %}">Products</a>
        <a class="erp-nav-link {% if current_url == 'catalog' %}active{% endif %}" href="{% url 'catalog' %}">Catalog View</a>
        <a class="erp-nav-link {% if current_url in 'supplier_list,supplier_create,supplier_edit,supplier_detail' %}active{% endif %}" href="{% url 'supplier_list' %}">Suppliers</a>
        {% endif %}

        <div class="erp-nav-group">Sales &amp; Clients</div>
        <a class="erp-nav-link {% if current_url == 'sales_records' %}active{% endif %}" href="{% url 'sales_records' %}">Sales</a>
        <a class="erp-nav-link {% if current_url in 'customer_search,customer_detail,edit_customer' %}active{% endif %}" href="{% url 'customer_search' %}">Customers</a>
        {% if user|is_manager_user %}
        <a class="erp-nav-link {% if current_url in 'ar_list,ar_detail,ar_new,ar_add_payment,ar_add_items' %}active{% endif %}" href="{% url 'ar_list' %}">IOU / AR</a>
        {% endif %}

        {% if user.is_superuser %}
          <div class="erp-nav-group">Admin</div>
          <a class="erp-nav-link {% if current_url in 'store_list,store_create,store_edit' %}active{% endif %}" href="{% url 'store_list' %}">Stores</a>
          <a class="erp-nav-link {% if current_url in 'employee_list,employee_create,employee_edit' %}active{% endif %}" href="{% url 'employee_list' %}">Team</a>
          <a class="erp-nav-link {% if current_url in 'sale_order_correction_center,sale_order_correction_create,sale_order_correction_edit' %}active{% endif %}" href="{% url 'sale_order_correction_center' %}">Order Control</a>
          <a class="erp-nav-link {% if current_url == 'print_profile_edit' %}active{% endif %}" href="{% url 'print_profile_edit' %}">Print Header</a>
        {% endif %}
      </nav>
```

(Note: the `{% load %}` for `access_tags` is already at the top of base.html — the file already uses `is_manager_user` for Suppliers. Do not add a second load.)

- [ ] **Step 4: Run to verify it passes**

Run: `...python.exe manage.py test stock.tests.EmployeeNavTests -v 2`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add stock/templates/stock/base.html stock/tests.py
git commit -m "feat: hide employee-forbidden nav links behind is_manager_user"
```

---

### Task 2: Block employee-forbidden views

**Files:**
- Modify: `stock/views.py` (add `@manager_required` to the listed views)
- Test: `stock/tests.py` (new class + update OLD-policy tests)

**Interfaces:**
- Consumes: `manager_required` (already imported in views.py at line ~58).
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the failing test**

Append to `stock/tests.py`:

```python
class EmployeeBlockedViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.employee = user_model.objects.create_user(username="blk_emp", password="pw123456")
        cls.manager = user_model.objects.create_user(username="blk_mgr", password="pw123456", is_staff=True)
        cls.customer = Customer.objects.create(nif="123456789", name="Blk Cust")

    def _assert_blocked(self, url):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302, url)
        self.assertIn(reverse("dashboard"), resp.headers["Location"], url)

    def test_employee_blocked_from_restricted_pages(self):
        self.client.login(username="blk_emp", password="pw123456")
        for name in ["daily_summary", "inbound", "product_list", "catalog", "ar_list"]:
            self._assert_blocked(reverse(name))
        self._assert_blocked(reverse("customer_detail", args=[self.customer.id]))

    def test_manager_can_open_restricted_pages(self):
        self.client.login(username="blk_mgr", password="pw123456")
        for name in ["daily_summary", "inbound", "product_list", "catalog", "ar_list"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
```

- [ ] **Step 2: Run to verify it fails**

Run: `...python.exe manage.py test stock.tests.EmployeeBlockedViewsTests -v 2`
Expected: `test_employee_blocked_from_restricted_pages` FAILS (employee still gets 200).

- [ ] **Step 3: Add `@manager_required` to the blocked views**

In `stock/views.py`, for each function below, add a line `@manager_required` immediately **after** its existing `@login_required` line (keep `@login_required` first so auth is checked before role):

- `inbound_view` (~476)
- `inbound_receive_view` (~619)
- `product_list_view` (~849)
- `add_product_view` (~1396)
- `product_detail_view` (~1414)
- `edit_product_view` (~1470) — already has `@manager_required`? Verify; if present, leave it.
- `daily_summary_view` (~3611)
- `customer_detail_view` (~4396)
- `catalog_view` (~4782)
- `ar_new_view` (~5113)
- `ar_list_view` (~5176)
- `ar_detail_view` (~5208)
- `ar_add_payment_view` (~5223)
- `ar_add_items_view` (~5251)

Do NOT add it to `attendance_view` (handled in Task 5), nor to `record_view` / `customer_search_view` (restricted, Tasks 3-4), nor to `outbound_view` / `sale_order_detail` / `add_customer` (employees keep these).

- [ ] **Step 4: Update existing OLD-policy tests**

These existing tests assert employees may open now-blocked pages and MUST be updated to assert the redirect. Read each and change its expectation:

- `WorkflowRegressionTests.test_regular_user_can_open_pages_but_not_sensitive_views` (~852): keep `sales_records` and `customer_search` at 200; change `product_list` and `ar_list` to the blocked pattern (302 → dashboard).
- `WorkflowRegressionTests.test_regular_user_ar_pages_hide_financial_details` (~860): the employee can no longer open AR at all — replace the body with the blocked pattern for `ar_list` and `ar_detail` (302 → dashboard). (Manager-side AR money hiding is unrelated and stays.)
- `CustomerDetailViewTests.test_customer_detail_hides_sensitive_sections_for_regular_user` (~672): replace with the blocked pattern for `customer_detail` (302 → dashboard).
- `ProductArchitectureTests.test_product_detail_shows_prices_but_hides_sales_history_for_regular_user` (~1068): replace with the blocked pattern for `product_detail` (302 → dashboard).
- `ProductArchitectureTests.test_product_list_hides_sales_metrics_but_keeps_prices_for_regular_user` (~1151): replace with the blocked pattern for `product_list` (302 → dashboard).

- [ ] **Step 5: Run the full suite; update any remaining OLD-policy failures**

Run: `...python.exe manage.py test stock`
Expected: all pass. If any other test fails because an **employee** (a `create_user` without `is_staff`) GETs one of the blocked views (candidates: `InboundOutboundPageTests` around line 1331 with `ops_employee`; `MultiStoreTests` with `store_emp` ~2024; other `product_employee` tests ~1033/1115), update it to the blocked pattern (302 → dashboard) or, if the test's whole premise was "employee views this page", delete it. Do not weaken manager assertions.

- [ ] **Step 6: Commit**

```bash
git add stock/views.py stock/tests.py
git commit -m "feat: block employees from inbound/products/catalog/AR/daily-summary/customer-detail"
```

---

### Task 3: Restricted employee Sales view

**Files:**
- Modify: `stock/views.py` (`record_view` top branch + new helper `_employee_sales_day_view`)
- Create: `stock/templates/stock/sales_records_employee.html`
- Test: `stock/tests.py` (new class + update one OLD test)

**Interfaces:**
- Consumes: `has_manager_access`, `resolve_active_store`, `scope_sales_by_store`, `timezone`, `datetime`, `Decimal`, `SaleOrder` (all already imported in views.py).
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the failing test**

Append to `stock/tests.py`:

```python
class EmployeeSalesViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.employee = user_model.objects.create_user(username="sales_emp", password="pw123456")

    def test_employee_sales_shows_todays_orders_with_amounts_no_charts(self):
        order = SaleOrder.objects.create()
        product = Product.objects.create(name="P", barcode="7000000000001", brand="B")
        Sale.objects.create(order=order, product=product, quantity=2,
                            unit_price=Decimal("10.00"), payment_method="cash")

        self.client.login(username="sales_emp", password="pw123456")
        resp = self.client.get(reverse("sales_records"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "EUR 20.00")       # order/day total shown
        self.assertContains(resp, 'name="date"')      # single-day date picker
        self.assertNotContains(resp, "<canvas")       # no charts at all
```

`Product` is imported at the top of `stock/tests.py`. Asserting the absence of
`<canvas` is a robust "no charts" check (Chart.js renders into `<canvas>`), and
does not depend on the manager template's exact heading text.

- [ ] **Step 2: Run to verify it fails**

Run: `...python.exe manage.py test stock.tests.EmployeeSalesViewTests -v 2`
Expected: FAIL (employee currently gets the full manager page with charts).

- [ ] **Step 3: Add the employee branch + helper in `record_view`**

In `stock/views.py`, make the **first statement** of `record_view` (right after `def record_view(request):`, before `start_str = ...`):

```python
    if not has_manager_access(request.user):
        return _employee_sales_day_view(request)
```

Add this helper immediately **above** `record_view`:

```python
def _employee_sales_day_view(request):
    """Employee Sales: a single day's orders, amounts only, no charts."""
    day = timezone.localdate()
    day_str = (request.GET.get('date') or '').strip()
    if day_str:
        try:
            day = datetime.strptime(day_str, '%Y-%m-%d').date()
        except ValueError:
            day = timezone.localdate()

    active_store, store_is_all = resolve_active_store(request)
    orders_qs = (
        SaleOrder.objects
        .filter(created_at__date=day)
        .select_related('customer')
        .prefetch_related('items', 'payments')
        .order_by('-created_at', '-id')
    )
    # Reuse the shared store scoping (unfiltered when store is None / "all stores").
    orders_qs = scope_sales_by_store(orders_qs, active_store, store_is_all)

    payment_labels = {'cash': 'Cash', 'card': 'Card', 'mbway': 'MBWay'}
    orders = []
    day_total = Decimal('0.00')
    for order in orders_qs:
        items = list(order.items.all())
        total = sum((i.quantity * (i.unit_price or Decimal('0.00')) for i in items), Decimal('0.00'))
        qty = sum(i.quantity for i in items)
        methods = [payment_labels.get(p.method, (p.method or '').title()) for p in order.payments.all()]
        orders.append({
            'created_hhmm': timezone.localtime(order.created_at).strftime('%H:%M'),
            'customer_name': order.customer.name if order.customer_id else 'Walk-in / No customer',
            'item_count': qty,
            'payment_label': ', '.join(dict.fromkeys(methods)) or '-',
            'total_amount': total,
        })
        day_total += total

    return render(request, 'stock/sales_records_employee.html', {
        'day': day,
        'date_value': day.strftime('%Y-%m-%d'),
        'orders': orders,
        'order_count': len(orders),
        'day_total': day_total,
    })
```

- [ ] **Step 4: Create the lean template**

Create `stock/templates/stock/sales_records_employee.html`:

```html
{% extends 'stock/base.html' %}
{% block content %}
<div class="page-card">
  <h1 class="pos-title">Sales</h1>

  <form method="get" class="row g-2 align-items-end mb-3">
    <div class="col-auto">
      <label class="form-label fw-bold" for="date">Date</label>
      <input type="date" id="date" name="date" value="{{ date_value }}" class="form-control">
    </div>
    <div class="col-auto">
      <button type="submit" class="btn btn-primary">View</button>
    </div>
  </form>

  <div class="table-responsive">
    <table class="table align-middle">
      <thead>
        <tr>
          <th>Time</th><th>Customer</th><th>Items</th><th>Payment</th><th class="text-end">Total</th>
        </tr>
      </thead>
      <tbody>
        {% for o in orders %}
        <tr>
          <td class="num">{{ o.created_hhmm }}</td>
          <td>{{ o.customer_name }}</td>
          <td class="num">{{ o.item_count }}</td>
          <td>{{ o.payment_label }}</td>
          <td class="num text-end">EUR {{ o.total_amount|floatformat:2 }}</td>
        </tr>
        {% empty %}
        <tr><td colspan="5" class="text-muted">No orders for {{ date_value }}.</td></tr>
        {% endfor %}
      </tbody>
      {% if orders %}
      <tfoot>
        <tr class="fw-bold">
          <td colspan="4" class="text-end">Day total ({{ order_count }} orders)</td>
          <td class="num text-end">EUR {{ day_total|floatformat:2 }}</td>
        </tr>
      </tfoot>
      {% endif %}
    </table>
  </div>
</div>
{% endblock %}
```

(`base.html` defines `{% block content %}` at line 122 — confirmed; this template targets it. No intro/explanatory paragraph — title + controls + data only.)

- [ ] **Step 5: Update the OLD test + run**

`SalesRecordsViewTests.test_sales_records_hides_purchase_data_for_regular_user` (~506) now hits the lean employee view. Read it and update its assertions to the lean view: still `status_code == 200`, still no purchase data, but it no longer contains the manager purchase/chart markup. Change any `assertContains` that referenced manager-only sales markup to reflect the lean order table (or assert the day total / order rows). Keep the "no purchase data" intent.

Run: `...python.exe manage.py test stock.tests.EmployeeSalesViewTests stock.tests.SalesRecordsViewTests -v 2`
Expected: PASS.

- [ ] **Step 6: Full suite + commit**

Run: `...python.exe manage.py test stock` → all pass.

```bash
git add stock/views.py stock/templates/stock/sales_records_employee.html stock/tests.py
git commit -m "feat: employee Sales view = single day, orders + amounts, no charts"
```

---

### Task 4: Restricted employee Customers view

**Files:**
- Modify: `stock/views.py` (`customer_search_view` top branch + helper `_employee_customer_search_view`)
- Create: `stock/templates/stock/customer_search_employee.html`
- Test: `stock/tests.py` (new class)

**Interfaces:**
- Consumes: `has_manager_access`, `Customer`, `Q` (all imported in views.py); the existing `add_customer` JSON endpoint (`{% url 'add_customer' %}`, POST fields `nif`,`name`,`phone`,`email`; nif = 9 digits).
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the failing test**

Append to `stock/tests.py`:

```python
class EmployeeCustomerViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.employee = user_model.objects.create_user(username="cust_emp", password="pw123456")
        cls.alice = Customer.objects.create(nif="111111111", name="Alice Stone", phone="912000001")
        cls.bob = Customer.objects.create(nif="222222222", name="Bob Rivers", phone="912000002")

    def test_no_query_shows_no_customer_rows(self):
        self.client.login(username="cust_emp", password="pw123456")
        resp = self.client.get(reverse("customer_search"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Alice Stone")
        self.assertNotContains(resp, "Bob Rivers")

    def test_query_shows_only_matching_minimal_fields(self):
        self.client.login(username="cust_emp", password="pw123456")
        resp = self.client.get(reverse("customer_search"), {"q": "Alice"})
        self.assertContains(resp, "Alice Stone")
        self.assertContains(resp, "912000001")
        self.assertNotContains(resp, "Bob Rivers")
        self.assertNotContains(resp, "Total spent")   # no spend/balance columns

    def test_employee_can_add_customer(self):
        self.client.login(username="cust_emp", password="pw123456")
        resp = self.client.post(reverse("add_customer"), {
            "nif": "999999999", "name": "New Walkin", "phone": "912999999",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Customer.objects.filter(nif="999999999").exists())
```

- [ ] **Step 2: Run to verify it fails**

Run: `...python.exe manage.py test stock.tests.EmployeeCustomerViewTests -v 2`
Expected: `test_no_query_shows_no_customer_rows` FAILS (full list shown today).

- [ ] **Step 3: Add the employee branch + helper**

In `stock/views.py`, make the **first statement** of `customer_search_view` (right after `def customer_search_view(request):`):

```python
    if not has_manager_access(request.user):
        return _employee_customer_search_view(request)
```

Add this helper immediately **above** `customer_search_view`:

```python
def _employee_customer_search_view(request):
    """Employee Customers: search + add only; no full list, no history."""
    query = (request.GET.get('q') or '').strip()
    customers = []
    if query:
        customers = list(
            Customer.objects.filter(
                Q(name__icontains=query)
                | Q(phone__icontains=query)
                | Q(email__icontains=query)
                | Q(nif__icontains=query)
            ).order_by('name')[:50]
        )
    return render(request, 'stock/customer_search_employee.html', {
        'query': query,
        'customers': customers,
    })
```

- [ ] **Step 4: Create the lean template**

Create `stock/templates/stock/customer_search_employee.html`:

```html
{% extends 'stock/base.html' %}
{% block content %}
<div class="page-card">
  <h1 class="pos-title">Customers</h1>

  <div class="d-flex gap-2 mb-3">
    <form method="get" class="d-flex gap-2 flex-grow-1">
      <input type="text" name="q" value="{{ query }}" class="form-control" placeholder="Search name / phone / email / NIF">
      <button type="submit" class="btn btn-primary">Search</button>
    </form>
    <button type="button" class="btn btn-outline-primary" data-bs-toggle="modal" data-bs-target="#addCustomerModal">Add customer</button>
  </div>

  {% if query %}
  <div class="table-responsive">
    <table class="table align-middle">
      <thead><tr><th>Name</th><th>Phone</th><th>Email</th></tr></thead>
      <tbody>
        {% for c in customers %}
        <tr><td>{{ c.name }}</td><td>{{ c.phone|default:"-" }}</td><td>{{ c.email|default:"-" }}</td></tr>
        {% empty %}
        <tr><td colspan="3" class="text-muted">No customers match "{{ query }}".</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
</div>

<div class="modal fade" id="addCustomerModal" tabindex="-1" aria-hidden="true">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">Add customer</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
      </div>
      <div class="modal-body">
        <div id="addCustomerError" class="text-danger mb-2" role="alert"></div>
        <div class="mb-2"><label class="form-label" for="ac_nif">NIF (9 digits)</label><input id="ac_nif" class="form-control"></div>
        <div class="mb-2"><label class="form-label" for="ac_name">Name</label><input id="ac_name" class="form-control"></div>
        <div class="mb-2"><label class="form-label" for="ac_phone">Phone</label><input id="ac_phone" class="form-control"></div>
        <div class="mb-2"><label class="form-label" for="ac_email">Email</label><input id="ac_email" class="form-control"></div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-primary" id="ac_save">Save</button>
      </div>
    </div>
  </div>
</div>

<script>
document.getElementById('ac_save').addEventListener('click', function () {
  const body = new FormData();
  body.append('nif', document.getElementById('ac_nif').value);
  body.append('name', document.getElementById('ac_name').value);
  body.append('phone', document.getElementById('ac_phone').value);
  body.append('email', document.getElementById('ac_email').value);
  fetch('{% url "add_customer" %}', {
    method: 'POST',
    headers: {'X-CSRFToken': '{{ csrf_token }}'},
    body: body,
  }).then(r => r.json()).then(d => {
    if (d.success) { window.location.reload(); }
    else { document.getElementById('addCustomerError').textContent = d.error || 'Could not add customer.'; }
  });
});
</script>
{% endblock %}
```

(`base.html` loads `bootstrap.min.js` at line 133 — confirmed, so the `data-bs-toggle="modal"` modal works. No explanatory paragraph.)

- [ ] **Step 5: Run to verify it passes**

Run: `...python.exe manage.py test stock.tests.EmployeeCustomerViewTests -v 2`
Expected: PASS (3 tests).

- [ ] **Step 6: Full suite + commit**

Run: `...python.exe manage.py test stock` → all pass.

```bash
git add stock/views.py stock/templates/stock/customer_search_employee.html stock/tests.py
git commit -m "feat: employee Customers view = search + add only, no list or history"
```

---

### Task 5: Auto-attendance from login/logout + attendance page manager-only

**Files:**
- Modify: `stock/signals.py` (auth-signal receivers)
- Modify: `stock/views.py` (`attendance_view` → `@manager_required`)
- Test: `stock/tests.py` (rewrite `AttendanceManagementTests`)

**Interfaces:**
- Consumes: `AttendanceRecord`, `has_manager_access`, `timezone`.
- Produces: attendance auto-records for employees (login opens, logout closes).

- [ ] **Step 1: Write the failing test**

Replace the body of `AttendanceManagementTests` (~1897) with:

```python
class AttendanceManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.employee = user_model.objects.create_user(username="attendance_employee", password="pw123456")
        cls.manager = user_model.objects.create_user(username="attendance_manager", password="pw123456", is_staff=True)

    def test_login_opens_shift_logout_closes_it(self):
        self.client.login(username="attendance_employee", password="pw123456")
        rec = AttendanceRecord.objects.get(user=self.employee)
        self.assertIsNone(rec.clock_out_at)
        self.client.logout()
        rec.refresh_from_db()
        self.assertIsNotNone(rec.clock_out_at)

    def test_second_login_while_open_does_not_duplicate(self):
        self.client.login(username="attendance_employee", password="pw123456")
        self.client.login(username="attendance_employee", password="pw123456")
        self.assertEqual(AttendanceRecord.objects.filter(user=self.employee).count(), 1)

    def test_stale_previous_day_shift_closed_on_next_login(self):
        stale = AttendanceRecord.objects.create(
            user=self.employee,
            clock_in_at=timezone.now() - timedelta(days=1),
        )
        self.client.login(username="attendance_employee", password="pw123456")
        stale.refresh_from_db()
        self.assertIsNotNone(stale.clock_out_at)                 # stale closed
        self.assertEqual(AttendanceRecord.objects.filter(user=self.employee, clock_out_at__isnull=True).count(), 1)  # one fresh open

    def test_manager_login_creates_no_record(self):
        self.client.login(username="attendance_manager", password="pw123456")
        self.assertEqual(AttendanceRecord.objects.filter(user=self.manager).count(), 0)

    def test_employee_blocked_from_attendance_page(self):
        self.client.login(username="attendance_employee", password="pw123456")
        resp = self.client.get(reverse("attendance"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("dashboard"), resp.headers["Location"])

    def test_manager_can_see_team_attendance_section(self):
        AttendanceRecord.objects.create(user=self.employee, clock_in_at=timezone.now() - timedelta(hours=2))
        self.client.login(username="attendance_manager", password="pw123456")
        resp = self.client.get(reverse("attendance"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Team attendance overview")
        self.assertContains(resp, "attendance_employee")
```

- [ ] **Step 2: Run to verify it fails**

Run: `...python.exe manage.py test stock.tests.AttendanceManagementTests -v 2`
Expected: FAILS (no signals yet; attendance page not gated).

- [ ] **Step 3: Add the auth-signal receivers**

In `stock/signals.py`, extend the imports:

```python
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.utils import timezone

from .models import AttendanceRecord, ProductImage, Sale
from .permissions import has_manager_access
```

Append these receivers at the end of the file:

```python
@receiver(user_logged_in)
def open_attendance_on_login(sender, request, user, **kwargs):
    """Employees: opening a shift on login. Reuse today's open shift; close a
    stale previous-day open shift first."""
    if has_manager_access(user):
        return
    today = timezone.localdate()
    open_shift = (
        AttendanceRecord.objects
        .filter(user=user, clock_out_at__isnull=True)
        .order_by('-clock_in_at', '-id')
        .first()
    )
    if open_shift:
        if timezone.localtime(open_shift.clock_in_at).date() == today:
            return  # already an open shift today
        end_of_day = timezone.localtime(open_shift.clock_in_at).replace(
            hour=23, minute=59, second=59, microsecond=0)
        open_shift.clock_out_at = end_of_day
        open_shift.note = (open_shift.note + ' auto-closed: no logout').strip()
        open_shift.save(update_fields=['clock_out_at', 'note'])
    AttendanceRecord.objects.create(user=user, clock_in_at=timezone.now(), note='auto: login')


@receiver(user_logged_out)
def close_attendance_on_logout(sender, request, user, **kwargs):
    """Employees: closing the open shift on logout."""
    if user is None or has_manager_access(user):
        return
    open_shift = (
        AttendanceRecord.objects
        .filter(user=user, clock_out_at__isnull=True)
        .order_by('-clock_in_at', '-id')
        .first()
    )
    if open_shift:
        open_shift.clock_out_at = timezone.now()
        open_shift.note = (open_shift.note + ' auto: logout').strip()
        open_shift.save(update_fields=['clock_out_at', 'note'])
```

- [ ] **Step 4: Gate the attendance page**

In `stock/views.py`, add `@manager_required` immediately after the `@login_required` on `attendance_view` (~3072):

```python
@login_required
@manager_required
def attendance_view(request):
```

- [ ] **Step 5: Run to verify it passes**

Run: `...python.exe manage.py test stock.tests.AttendanceManagementTests -v 2`
Expected: PASS.

- [ ] **Step 6: Full suite + commit**

Run: `...python.exe manage.py test stock` → all pass. (Employee `client.login` now creates attendance records suite-wide — this is expected and harmless; only `AttendanceManagementTests` asserts on record counts.)

```bash
git add stock/signals.py stock/views.py stock/tests.py
git commit -m "feat: auto-attendance from login/logout; attendance page manager-only"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/STATUS.md`

- [ ] **Step 1: Update `docs/ARCHITECTURE.md` §5.4 (permissions)**

Record: the employee page set (Dashboard, Outbound, restricted Sales, restricted Customers) and that all other pages are `manager_required`; that `record_view` / `customer_search_view` branch to a lean employee template; that attendance is auto-tracked via `user_logged_in`/`user_logged_out` signals and `attendance_view` is now manager-only. **Update the existing note** that said `ar_list_view`/`ar_detail_view` stay `@login_required` — they are now `manager_required` (employees blocked); the money-hiding rationale is superseded by full blocking.

- [ ] **Step 2: Update `docs/PRD.md`**

Add an employee-access description (the 4-page employee experience + auto-attendance) under the permissions/roles section (§1.3) or a new feature entry, following neighbouring formatting.

- [ ] **Step 3: Update `docs/STATUS.md`**

Add a dated 2026-07-16 changelog line: employee interface restricted to 4 pages + auto-attendance from login/logout.

- [ ] **Step 4: Commit**

```bash
git add docs/PRD.md docs/ARCHITECTURE.md docs/STATUS.md
git commit -m "docs: record employee interface restriction and auto-attendance"
```

---

## Manual verification (after Task 6)

Not a task — a quick smoke check after restarting the server:

1. Log in as an employee account → sidebar shows only Dashboard, Outbound, Sales, Customers.
2. Employee opens Sales → date picker + that day's orders with € totals, no charts. Type `/inbound/` in the URL → redirected to dashboard.
3. Employee opens Customers → empty until search; search finds a customer (name/phone only); "Add customer" works; no way to open a customer's history.
4. Log out, log back in → attendance record opened; check the manager Attendance page shows the employee's shift.

---

## REVISION 2026-07-16b — additional tasks (7-9)

Spec revision section governs. Tasks 1-4 are committed. Execute remaining tasks in this order: **Task 5 (attendance) → Task 7 → Task 8 → Task 9 → Task 6 (docs last, covering the final state)**. Same rules: SYSTEM python, full-suite green is the source of truth, no UTF-8 BOM, do not weaken manager assertions.

Post-revision employee page set: Dashboard, Outbound, **Products (view/search/add)**, Sales (day list + order-number search + order detail), Customers (search + add + per-customer orders). Still blocked: Inbound, edit product, Catalog View, AR, Today, Attendance page, Suppliers, Admin.

---

### Task 7: Reopen Products to employees (view / search / add)

**Files:**
- Modify: `stock/views.py` (remove `@manager_required` from 3 views)
- Modify: `stock/templates/stock/base.html` (Products link visible to all)
- Test: `stock/tests.py` (revert product-block tests; add new)

**Interfaces:** consumes `is_manager_user` (template), `has_manager_access`.

- [ ] **Step 1: Write the new test class**

```python
class EmployeeProductAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.employee = user_model.objects.create_user(username="prod_emp", password="pw123456")
        cls.category = Category.objects.create(name="PCat")
        cls.product = Product.objects.create(name="PP", barcode="7100000000001", brand="B",
                                             default_price=Decimal("9.90"))

    def test_employee_can_view_products_and_detail(self):
        self.client.login(username="prod_emp", password="pw123456")
        self.assertEqual(self.client.get(reverse("product_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("product_detail", args=[self.product.pk])).status_code, 200)

    def test_employee_can_add_product(self):
        self.client.login(username="prod_emp", password="pw123456")
        resp = self.client.post(reverse("add_product"), {
            "barcode": "7100000000002", "category": self.category.id,
            "new_brand_name": "NB", "name": "New Item", "default_price": "12.00",
        })
        self.assertIn(resp.status_code, (200, 302))
        self.assertTrue(Product.objects.filter(barcode="7100000000002").exists())

    def test_employee_still_blocked_from_inbound_and_edit(self):
        self.client.login(username="prod_emp", password="pw123456")
        for url in [reverse("inbound"), reverse("edit_product", args=[self.product.pk])]:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302)
            self.assertIn(reverse("dashboard"), resp.headers["Location"])
```

Verify the `add_product` and `edit_product` URL names via `stock/urls.py` — if they differ, use the actual names. `Product`, `Category` are imported at the top of tests.py.

- [ ] **Step 2: Run to verify it fails**

Run: `...python.exe manage.py test stock.tests.EmployeeProductAccessTests -v 2`
Expected: the view/detail/add tests FAIL (employee currently 302'd by Task 2).

- [ ] **Step 3: Remove `@manager_required` from the 3 product views**

In `stock/views.py`, remove the `@manager_required` line (added in Task 2) from `product_list_view`, `product_detail_view`, `add_product_view`. Keep `@login_required`. Do NOT touch `edit_product_view`, `inbound_view`, `inbound_receive_view`.

- [ ] **Step 4: Restore the Products nav link for employees**

In `stock/templates/stock/base.html`, the Catalog block is currently fully manager-gated. Replace it so **Products is visible to all**, Catalog View + Suppliers stay manager-only:

```html
        <div class="erp-nav-group">Catalog</div>
        <a class="erp-nav-link {% if current_url in 'product_list,product_detail,edit_product,add_product' %}active{% endif %}" href="{% url 'product_list' %}">Products</a>
        {% if user|is_manager_user %}
        <a class="erp-nav-link {% if current_url == 'catalog' %}active{% endif %}" href="{% url 'catalog' %}">Catalog View</a>
        <a class="erp-nav-link {% if current_url in 'supplier_list,supplier_create,supplier_edit,supplier_detail' %}active{% endif %}" href="{% url 'supplier_list' %}">Suppliers</a>
        {% endif %}
```

- [ ] **Step 5: Revert the product-block tests changed in Task 2**

Task 2 changed these to assert 302; revert to assert employee SEES the page (200), sensitive hidden:
- `test_product_list_hides_sales_metrics_but_keeps_prices_for_regular_user` — employee GET `product_list` → 200, a price string present, a sales-metric string absent.
- `test_product_detail_shows_prices_but_hides_sales_history_for_regular_user` — employee GET `product_detail` → 200, prices present, sales history / "Suppliers &amp; cost" absent.
- In `test_regular_user_can_open_pages_but_not_sensitive_views`, change the `product_list` assertion back to 200 (leave `ar_list` at 302 — AR stays blocked).

Recover the original bodies with `git show cd2db89:stock/tests.py` if helpful.

- [ ] **Step 6: Full suite + commit**

Run: `...python.exe manage.py test stock` → all pass.
```
git add stock/views.py stock/templates/stock/base.html stock/tests.py
git commit -m "feat: reopen product view/search/add to employees (inbound/edit stay manager-only)"
```

---

### Task 8: Sales order-number search + order-detail links

**Files:**
- Modify: `stock/views.py` (`_employee_sales_day_view`)
- Modify: `stock/templates/stock/sales_records_employee.html`
- Test: `stock/tests.py` (extend `EmployeeSalesViewTests`)

**Interfaces:** consumes `redirect`, `messages`, `scope_sales_by_store`, `resolve_active_store`, `SaleOrder` (all imported in views.py). URL `sale_order_detail` takes `order_id`.

- [ ] **Step 1: Write the failing tests** (add to `EmployeeSalesViewTests`)

```python
    def test_order_number_search_redirects_to_detail(self):
        order = SaleOrder.objects.create()
        product = Product.objects.create(name="Q", barcode="7200000000001", brand="B")
        Sale.objects.create(order=order, product=product, quantity=1,
                            unit_price=Decimal("5.00"), payment_method="cash")
        self.client.login(username="sales_emp", password="pw123456")
        resp = self.client.get(reverse("sales_records"), {"order": str(order.id)})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("sale_order_detail", args=[order.id]), resp.headers["Location"])

    def test_order_rows_link_to_detail(self):
        order = SaleOrder.objects.create()
        product = Product.objects.create(name="Q2", barcode="7200000000002", brand="B")
        Sale.objects.create(order=order, product=product, quantity=1,
                            unit_price=Decimal("5.00"), payment_method="cash")
        self.client.login(username="sales_emp", password="pw123456")
        resp = self.client.get(reverse("sales_records"))
        self.assertContains(resp, reverse("sale_order_detail", args=[order.id]))
```

- [ ] **Step 2: Run to verify it fails**

Run: `...python.exe manage.py test stock.tests.EmployeeSalesViewTests -v 2` → both new tests FAIL.

- [ ] **Step 3: Add order lookup + order_id in the helper**

In `_employee_sales_day_view`, after `active_store, store_is_all = resolve_active_store(request)`, add:

```python
    order_str = (request.GET.get('order') or '').strip()
    if order_str:
        oq = SaleOrder.objects.filter(id=order_str) if order_str.isdigit() else SaleOrder.objects.none()
        found = scope_sales_by_store(oq, active_store, store_is_all).first()
        if found:
            return redirect('sale_order_detail', order_id=found.id)
        messages.warning(request, f'Order #{order_str} not found.')
```

And add `'order_id': order.id,` to each appended row dict.

- [ ] **Step 4: Update the template** (`sales_records_employee.html`)

Add an order-number search form after the date form:

```html
  <form method="get" class="row g-2 align-items-end mb-3">
    <div class="col-auto">
      <label class="form-label fw-bold" for="order">Order #</label>
      <input type="text" id="order" name="order" class="form-control" placeholder="Find by order number">
    </div>
    <div class="col-auto"><button type="submit" class="btn btn-outline-primary">Find</button></div>
  </form>
```

And change the time cell to a detail link:

```html
          <td class="num"><a href="{% url 'sale_order_detail' o.order_id %}">{{ o.created_hhmm }}</a></td>
```

- [ ] **Step 5: Full suite + commit**

Run: `...python.exe manage.py test stock` → all pass.
```
git add stock/views.py stock/templates/stock/sales_records_employee.html stock/tests.py
git commit -m "feat: employee Sales order-number search + order-detail links"
```

---

### Task 9: Per-customer orders for reconciliation

**Files:**
- Modify: `stock/views.py` (`customer_detail_view` branch + helper `_employee_customer_orders_view`)
- Modify: `stock/templates/stock/customer_search_employee.html` (link results to the orders view)
- Create: `stock/templates/stock/customer_orders_employee.html`
- Test: `stock/tests.py` (new class)

**Interfaces:** consumes `has_manager_access`, `get_object_or_404`, `Customer`, `SaleOrder`, `resolve_active_store`, `scope_sales_by_store`, `timezone`, `Decimal`, `render` (all imported). `sale_order_detail` takes `order_id`.

- [ ] **Step 1: Write the failing test**

```python
class EmployeeCustomerOrdersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.employee = user_model.objects.create_user(username="recon_emp", password="pw123456")
        cls.customer = Customer.objects.create(nif="333333333", name="Recon Cust")
        cls.order = SaleOrder.objects.create(customer=cls.customer)
        product = Product.objects.create(name="R", barcode="7300000000001", brand="B")
        Sale.objects.create(order=cls.order, product=product, quantity=2,
                            unit_price=Decimal("7.50"), payment_method="cash")

    def test_employee_sees_customer_orders_only(self):
        self.client.login(username="recon_emp", password="pw123456")
        resp = self.client.get(reverse("customer_detail", args=[self.customer.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Recon Cust")
        self.assertContains(resp, "EUR 15.00")
        self.assertContains(resp, reverse("sale_order_detail", args=[self.order.id]))
        self.assertNotContains(resp, "<canvas")

    def test_manager_still_sees_full_customer_detail(self):
        user_model = get_user_model()
        user_model.objects.create_user(username="recon_mgr", password="pw123456", is_staff=True)
        self.client.login(username="recon_mgr", password="pw123456")
        resp = self.client.get(reverse("customer_detail", args=[self.customer.id]))
        self.assertEqual(resp.status_code, 200)
```

- [ ] **Step 2: Run to verify it fails**

Run: `...python.exe manage.py test stock.tests.EmployeeCustomerOrdersTests -v 2` → `test_employee_sees_customer_orders_only` FAILS (employee 302'd by Task 2's `@manager_required`).

- [ ] **Step 3: Branch `customer_detail_view` + add helper**

Remove the `@manager_required` decorator from `customer_detail_view`; make its FIRST statement:

```python
    if not has_manager_access(request.user):
        return _employee_customer_orders_view(request, customer_id)
```

Add this helper immediately above `customer_detail_view`:

```python
def _employee_customer_orders_view(request, customer_id):
    """Employee: a customer's orders only (for reconciliation) - no analytics."""
    customer = get_object_or_404(Customer, id=customer_id)
    active_store, store_is_all = resolve_active_store(request)
    orders_qs = (
        SaleOrder.objects.filter(customer=customer)
        .prefetch_related('items', 'payments')
        .order_by('-created_at', '-id')
    )
    orders_qs = scope_sales_by_store(orders_qs, active_store, store_is_all)
    payment_labels = {'cash': 'Cash', 'card': 'Card', 'mbway': 'MBWay'}
    orders = []
    for order in orders_qs:
        items = list(order.items.all())
        total = sum((i.quantity * (i.unit_price or Decimal('0.00')) for i in items), Decimal('0.00'))
        methods = [payment_labels.get(p.method, (p.method or '').title()) for p in order.payments.all()]
        orders.append({
            'order_id': order.id,
            'created_at': timezone.localtime(order.created_at).strftime('%Y-%m-%d %H:%M'),
            'item_count': sum(i.quantity for i in items),
            'payment_label': ', '.join(dict.fromkeys(methods)) or '-',
            'total_amount': total,
        })
    return render(request, 'stock/customer_orders_employee.html', {
        'customer': customer,
        'orders': orders,
    })
```

- [ ] **Step 4: Create the lean orders template** (`stock/templates/stock/customer_orders_employee.html`)

```html
{% extends 'stock/base.html' %}
{% block content %}
<div class="page-card">
  <h1 class="pos-title">{{ customer.name }}</h1>
  <p class="num">{{ customer.phone|default:"-" }}{% if customer.email %} - {{ customer.email }}{% endif %}</p>
  <div class="table-responsive">
    <table class="table align-middle">
      <thead><tr><th>Date</th><th>Order #</th><th>Items</th><th>Payment</th><th class="text-end">Total</th></tr></thead>
      <tbody>
        {% for o in orders %}
        <tr>
          <td>{{ o.created_at }}</td>
          <td><a href="{% url 'sale_order_detail' o.order_id %}">#{{ o.order_id }}</a></td>
          <td class="num">{{ o.item_count }}</td>
          <td>{{ o.payment_label }}</td>
          <td class="num text-end">EUR {{ o.total_amount|floatformat:2 }}</td>
        </tr>
        {% empty %}
        <tr><td colspan="5" class="text-muted">No orders for this customer.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 5: Link customer-search results to the orders view**

In `stock/templates/stock/customer_search_employee.html`, change the result name cell to a link:

```html
        <tr><td><a href="{% url 'customer_detail' c.id %}">{{ c.name }}</a></td><td>{{ c.phone|default:"-" }}</td><td>{{ c.email|default:"-" }}</td></tr>
```

- [ ] **Step 6: Full suite + commit**

Run: `...python.exe manage.py test stock` → all pass.
```
git add stock/views.py stock/templates/stock/customer_orders_employee.html stock/templates/stock/customer_search_employee.html stock/tests.py
git commit -m "feat: employee per-customer orders view for reconciliation"
```

---

### Task 6 (revised): Documentation — final state

Update `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/STATUS.md` to describe the FINAL employee experience (Dashboard, Outbound, Products view/search/add with sensitive info hidden and inbound/edit manager-only, single-day Sales with order-number search + order detail, Customers search+add + per-customer orders for reconciliation, auto-attendance from login/logout). Note the AR `@login_required` note is superseded (employees blocked from AR).
