# Design: Employee interface restriction & auto-attendance

Date: 2026-07-16
Status: Approved — ready for implementation plan

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
