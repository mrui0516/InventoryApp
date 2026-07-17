# Design: Employee interface restriction & auto-attendance

Date: 2026-07-16
Status: Approved — ready for implementation plan

> **Revision 2026-07-16b (approved):** see the "## Revision" section at the end.
> It **reopens product view/add to employees** (superseding the "block Products"
> parts of §1-§2 below), and adds **order-number search + order detail on Sales**
> and a **customer-orders reconciliation view**. The auto-attendance design
> (§5) is unchanged. Where this revision and §1-§2 disagree about Products or
> `customer_detail`, the revision wins.

## Goal

Tighten what a non-manager **employee** account can see and do, for information
isolation:

- Employees keep only **4 pages**: Dashboard, Outbound (POS), a restricted
  Sales view, and a restricted Customers view.
- The Sales page, for employees, only lets them pick a single date and see that
  day's orders — no charts/visualizations, order info only.
- The Customers page, for employees, only lets them **search** and **add** — no
  full customer list, no customer detail/history.
- Attendance becomes **automatic** from login/logout time; the attendance page
  is hidden from employees.

## Context (current behavior)

- Role check: `permissions.has_manager_access(user)` → superuser OR `is_staff`
  OR in group "Managers". An **employee** is any authenticated user who is not a
  manager. `manager_required` decorator redirects non-managers to `dashboard`
  with a message. Template filter `access_tags.is_manager_user` gates nav/fields.
- Today an employee's sidebar (`base.html`) shows: Dashboard, Today, Inbound,
  Outbound, Attendance, Products, Catalog View, Sales, Customers, IOU/AR.
  (Suppliers and the Admin group are already manager-only.)
- `record_view` (Sales) and `customer_search_view`/`customer_detail_view` are
  `@login_required` only, so employees see the **full** pages (charts, full
  customer list with spend/balance, customer history).
- `attendance_view` is `@login_required` self-service clock in/out
  (`AttendanceRecord.filter(user=request.user, …)`), plus a manager team section.
- `AttendanceRecord` fields: `user`, `clock_in_at` (default now), `clock_out_at`
  (nullable), `note`. No schema change needed for this feature.
- `SaleOrder` has no "created_by/cashier" field, so per-employee order filtering
  is not available; employee order scope is by store (existing multi-store
  active-store filtering locks an employee to their home store).

## Confirmed decisions

- **Enforcement approach:** per-view gate + nav gating + lean employee templates,
  consistent with the existing `manager_required` / `is_manager_user` pattern
  (not a central deny-middleware). During implementation, audit every nav entry
  and URL name so nothing employee-forbidden is reachable.
- **Employee nav = Dashboard, Outbound, Sales, Customers.** Everything else is
  hidden from the sidebar and blocked at the view level.
- **Attendance is auto-tracked from login/logout for employees; the page is
  hidden from them.** `attendance_view` becomes manager-only (team view).
- **Managers are unchanged** in every respect (full nav, full pages, not
  auto-clocked).
- **Dashboard is kept as-is** for employees (profit already gated to superusers,
  sensitive money already gated to managers).

## Changes

### 1. Navigation — `stock/templates/stock/base.html`

Wrap these employee-forbidden links in `{% if user|is_manager_user %}` (same
pattern Suppliers/Admin already use): **Today** (`daily_summary`), **Inbound**,
**Products**, **Catalog View**, **IOU / AR**, **Attendance**. The whole
**Catalog** group header becomes manager-only (all its items are). Employees see
only: Dashboard, Outbound, Sales, Customers.

### 2. Block employee-forbidden views — add `@manager_required`

Add `manager_required` (redirect to dashboard) to the views employees must not
reach:

- `daily_summary_view` (Today)
- `inbound_view`, `inbound_receive_view`
- `product_list_view`, `product_detail_view`, `add_product_view`, `edit_product_view`
- `catalog_view`
- `ar_list_view`, `ar_detail_view`, `ar_new_view`, `ar_add_payment_view`, `ar_add_items_view`
- `customer_detail_view`
- `attendance_view` (now the manager team view)

> This **overrides** the earlier reviewed decision that `ar_list_view` /
> `ar_detail_view` stay `@login_required`. Employees are now fully blocked from
> AR, which matches the isolation goal. Update the note in ARCHITECTURE §5.4.

Exact function names are pinned during planning by reading `urls.py`; the
principle is: any nav entry not in the employee set is blocked.

### 3. Restricted Sales — `record_view`

Branch on manager vs employee:

- **Manager:** unchanged (yearly overview, charts, ranges, purchases, profit).
- **Employee:** force a **single day** (a date picker; default today, no range,
  no charts, no yearly overview, no purchases). Render a dedicated lean template
  `stock/sales_records_employee.html` listing that day's orders — time, customer,
  item count, payment method, **€ order total**, and the **day total**. Profit is
  hidden. Scope is the employee's store (existing active-store filter). Invalid/
  absent date → default to today.

### 4. Restricted Customers — `customer_search_view`

Branch on manager vs employee:

- **Manager:** unchanged (full annotated list, activity chart, spend/balance).
- **Employee:** render a dedicated lean template
  `stock/customer_search_employee.html`. **No list is shown until a query is
  entered.** With a query, results show minimal fields only — name / phone /
  email — with **no** spend / balance / last-order / chart. Keep the "Add
  customer" action (reusing the existing `add_customer` POST endpoint).
  `customer_detail_view` is blocked (§2), so results are not links to history.

### 5. Auto-attendance — `stock/signals.py` (Django auth signals)

Register receivers on `django.contrib.auth.signals`:

- **`user_logged_in`:** if the user is an employee (`not has_manager_access`):
  - If the user already has an open shift (`clock_out_at IS NULL`) **from today**,
    reuse it (do nothing — avoids duplicate open shifts across devices/re-login).
  - If the open shift is **from a previous day** (never logged out), close it at
    the end of its `clock_in_at` day (`note='auto-closed: no logout'`), then open
    a fresh shift for today.
  - Otherwise open a new shift: `AttendanceRecord(user, clock_in_at=now,
    note='auto: login')`.
- **`user_logged_out`:** if the user is an employee and has an open shift, close
  it (`clock_out_at=now`).
- Managers are excluded (not auto-clocked).

No model or migration change (`AttendanceRecord` already fits).

### 6. Tests — `stock/tests.py`

- **Nav:** employee sees only Dashboard/Outbound/Sales/Customers links; not
  Today/Inbound/Products/Catalog/AR/Attendance. Manager sees all.
- **Blocked views:** employee GET of `daily_summary`, `inbound`, `product_list`,
  `catalog`, `ar_list`, `customer_detail`, `attendance` → 302 to `dashboard`.
  Manager → 200.
- **Restricted Sales:** employee with no date → today's orders, with € amounts,
  no chart/yearly-overview markers in HTML; only the active store's orders.
  Manager path still renders the full page.
- **Restricted Customers:** employee with no query → no customer rows; with a
  query → matching rows, name/phone only, no spend/balance; `add_customer` works
  for an employee.
- **POS unaffected:** employee can load Outbound and use customer search / add.
- **Auto-attendance:** employee login opens exactly one open shift; a second
  login while open does not duplicate; logout closes it; a stale previous-day
  open shift is closed on next login; a manager login creates no record.

### 7. Docs

Update `docs/PRD.md` (employee access matrix / new auto-attendance),
`docs/ARCHITECTURE.md` (§5.4 permissions — employee page set, blocked views,
override of the AR note; auto-attendance via signals), `docs/STATUS.md`.

## Data flow

```
Employee logs in
  → user_logged_in signal → open AttendanceRecord (reuse if open; close stale)
  → nav shows only Dashboard / Outbound / Sales / Customers
Employee opens Sales  → record_view (employee branch) → today's orders + € totals
Employee opens Customers → customer_search_view (employee branch)
  → empty until a search; results = name/phone/email; can add
Employee types a forbidden URL (e.g. /inbound/) → manager_required → redirect dashboard
Employee logs out
  → user_logged_out signal → close the open AttendanceRecord (clock_out=now)
Manager opens Attendance → team view populated from the auto records
```

## Error handling / edge cases

- **No explicit logout** (browser close / session expiry): Django does not fire
  `user_logged_out`, so the shift stays open until the next explicit logout or a
  manager edit. Mitigation: the next login auto-closes a stale previous-day open
  shift (capped at that day's end). Precise work-hour tracking (heartbeat/idle
  timeout) is out of scope.
- **Multiple devices / rapid re-login:** "reuse existing open shift" prevents
  duplicate open shifts.
- **Employee forbidden URL access:** `manager_required` redirects to dashboard
  with the existing message; no 403 page needed.
- **Invalid/absent Sales date:** default to today.
- **POS endpoints:** outbound POST, product/customer autocomplete, `add_customer`,
  and store switch stay accessible to employees — verified in tests.

## Out of scope

- Per-employee (cashier) order attribution — no `created_by` on `SaleOrder`.
- Precise attendance beyond login/logout (heartbeat, idle timeout, geofence).
- Trimming the employee Dashboard further (kept as-is with existing gating).
- Any change to manager-facing pages.

---

## Revision (2026-07-16b) — reopen products, sales/customer reconciliation

Approved amendment after Tasks 1-4 were built. The employee is NOT limited to 4
pages after all: products are reopened (view + add), and Sales/Customers gain
reconciliation affordances. Auto-attendance (§5) is unchanged.

### R1. Products reopened to employees (supersedes the Products parts of §1-§2)

- Employees may **view/search the product list, open product detail, and add a
  new product**. Restore the **Products** nav link for employees.
- Remove `@manager_required` from `product_list_view`, `product_detail_view`,
  `add_product_view` (undo Task 2 for these three).
- **Inbound stays manager-only** (`inbound_view`, `inbound_receive_view` keep
  `@manager_required`) — employees create the catalog entry, managers do the
  stock receipt (which is where cost price lives, on `Purchase`).
- **Editing an existing product stays manager-only** (`edit_product_view` keeps
  `@manager_required`) — employees add, not edit.
- **No pending/approval workflow, no schema change.** The earlier "pending
  product" idea is dropped. `ProductForm` already has no cost field
  (`default_price`/`wholesale_price` are retail/wholesale, not cost), so an
  employee adding a product never touches cost.
- **Sensitive-info isolation is already implemented** and just needs to remain
  in effect: `product_detail.html` gates "Suppliers & cost", batch costs, and
  sales history behind `{% if show_sensitive %}` / `{% if show_sales_sensitive %}`
  (manager-only), and the product list hides sales metrics for non-managers.
  Employees see retail/wholesale prices only.
- **Tests:** revert the product tests that Task 2 changed to assert a 302
  (`test_product_list_hides_sales_metrics_but_keeps_prices_for_regular_user`,
  `test_product_detail_shows_prices_but_hides_sales_history_for_regular_user`,
  and any product-block assertions in `test_regular_user_can_open_pages_but_not_sensitive_views`)
  back to the employee-visible-with-sensitive-hidden behavior. Add a test that an
  employee can POST a new product via `add_product_view` and that
  `inbound`/`edit_product` remain blocked (302) for employees.

### R2. Sales: order-number search + order detail (extends §3 / Task 3)

- The employee Sales view (`_employee_sales_day_view`) gains an **order-number
  search**: entering an order number finds **that order across all dates** (not
  limited to the selected day) and links to its detail. Simplest: a second input
  (`order` / order id) that, when present, looks up the order and shows it (or
  redirects to `sale_order_detail`).
- Each order row (day list and search result) **links to `sale_order_detail`**.
  `sale_order_detail` is already employee-accessible and already hides customer
  contacts (NIF/email) while showing amounts — suitable for reconciliation.
- Store scope still applies to the day list; an order-number lookup returns the
  order only if it is in the employee's store (avoid cross-store leakage).

### R3. Customers: per-customer orders for reconciliation (extends §4 / Task 4)

- After an employee finds a customer, they can open that customer's **orders**
  (date / order # / amount / payment) and drill into `sale_order_detail` — for
  reconciliation (对账). Orders only: **no** spend analytics, charts, timeline,
  balance, or the full `customer_detail` page.
- Implementation: remove `@manager_required` from `customer_detail_view` and
  branch inside it — employee → a lean orders-only template; manager → the
  existing full page unchanged. (This is the same top-of-view branch pattern used
  for `record_view` / `customer_search_view`.) The employee customer-search
  results link each customer to this orders view.
- Store scope applies (only the employee's store's orders for that customer).

### R4. Net effect on the employee page set

Employee-visible pages become: Dashboard, Outbound, **Products (view/search/add)**,
Sales (single-day list + order-number search + order detail), Customers (search +
add + per-customer orders for reconciliation). Still blocked from employees:
Inbound, edit product, Catalog View, AR, Today (daily_summary), Attendance page,
Suppliers, and all Admin pages.

### R5. Execution note

Tasks 1-4 are already committed on `feature/employee-interface-restriction`.
This revision adds new tasks (reopen products; sales order search+detail; customer
orders view) and the still-pending Task 5 (auto-attendance) and Task 6 (docs,
reflecting the final state). The revision partially reverses Task 1 (nav) and
Task 2 (product blocks) — those reversals are explicit new tasks, not edits to
the completed commits.

---

## Revision (2026-07-16c) — employee product edit/export + polished order pages

Approved amendment after the 2026-07-16b tasks (172 tests). Extends the employee
experience further. Auto-attendance and the store-scoping fix are unchanged.

### R6. Employees can edit products

- Remove `@manager_required` from `edit_product_view` — employees can edit
  product fields (name/prices/spec/etc.) and upload new images (the edit view
  already handles image upload on POST).
- `delete_product_view` (whole product) and `delete_product_image` (single
  image) STAY `@manager_required` (no change). In `edit_product.html`, hide the
  whole-product delete block and the per-image delete forms behind
  `{% if user|is_manager_user %}` so employees don't hit those 302 endpoints.
- In `product_list.html`, make the **Add Product** button (line ~12) and the
  per-row **Edit** links (lines ~258, ~290) visible to employees (they are
  currently `{% if can_manage %}`). Employees add + edit; delete stays manager.

### R7. Employees can download the product Excel

- Remove `@manager_required` from `export_product_list_excel`. This export is a
  **customer-facing product list** — columns are Image / Product / Category /
  Retail Price / Wholesale Price / Availability, with **no cost / profit /
  supplier** — so it is safe for employees (they already see retail/wholesale
  prices on the product page).
- In `product_list.html`, the export panel (`{% if can_manage %}` at line ~100)
  wraps BOTH "Export For Client" (Excel) and "Export For Shopify". Split it:
  **"Export For Client" visible to all**; **"Export For Shopify" stays
  `{% if can_manage %}`** (manager-only).

### R8. Employee Sales/Customers pages match the manager design

- The three lean employee templates (`sales_records_employee.html`,
  `customer_search_employee.html`, `customer_orders_employee.html`) currently use
  bare Bootstrap tables. Restyle them with the app's shared design system used by
  the manager pages (`page-card`, section/`strip-head` headers, the standard
  table treatment, stat/summary cards where appropriate) so they look consistent
  with `sales_records.html` / `customer_search.html`. No new intro/explanatory
  paragraphs (house rule) — titles + controls + data only.

### R9. Each order row: a View modal + a Print button

- In the employee **Sales** day list and the **customer orders** page, each order
  row gets two actions instead of the current time/order#-cell link:
  - **View** — a Bootstrap modal (same pattern as `daily_summary.html`'s
    `#order-modal-{{ id }}` "Details" modal) showing the order's line items
    (name / qty / unit price / subtotal), payment method(s), and total — **no
    profit/cost** (isolation). Opens in place, for quick reconciliation.
  - **Print** — a button linking to `sale_order_detail` (the printable
    receipt/detail page; already store-scoped to the employee).
- The two employee helpers (`_employee_sales_day_view`,
  `_employee_customer_orders_view`) must pass each order's **items** (a list of
  {name, qty, unit_price, line_total}) to the template so the modal can render
  them (they currently pass only aggregates). Orders per page are few (one day /
  one customer), so inline per-order modals are fine.

### R10. Net effect

Employee product access becomes: view/search list, **Add Product**, **Edit
Product** (fields + add images; not delete), open product detail, and **Export
For Client (Excel)**. Inbound, Shopify export, edit's delete actions, and whole-
product/image deletion stay manager-only. Employee Sales/Customers pages are
visually consistent with manager pages and expose per-order **View** (modal) +
**Print** (detail) actions.
