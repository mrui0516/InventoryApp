"""Settings -> Audit: one page per kind of record, so the owner can see what
changed, who changed it and what went wrong without opening Django admin.

* Orders     - ``SaleOrderChangeLog``: orders added, edited or deleted through
               Correct orders, with who, why and each line before/after;
* Stock      - ``StockAdjustmentLog``: stock counts set by hand;
* Deliveries - ``InboundOrder`` received, and how long each took to arrive;
* Admin edits - Django admin's own history (``LogEntry``) of everything
               added, changed or deleted in /admin/;
* Accounts   - every login, its role, store and last sign-in, plus failed logins;
* Errors     - ``ErrorLog``: crashes and warnings (``stock/system_log.py``).

Sales and stock-ins made the normal way are the business itself and live in
Orders and Inbound.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone

PAGES = (
    ('audit_orders', 'Orders'),
    ('audit_stock', 'Stock'),
    ('audit_deliveries', 'Deliveries'),
    ('audit_admin', 'Admin edits'),
    ('audit_accounts', 'Accounts'),
    ('audit_errors', 'Errors'),
)
PERIODS = (('7', '7 days'), ('30', '30 days'), ('90', '90 days'), ('all', 'All'))
DASH = '—'
ACTION_LABELS = {'create': 'Added', 'update': 'Edited', 'delete': 'Deleted'}
FAILED_LOGIN_SOURCE = 'stock.auth'


def since_for(period):
    if period == 'all':
        return None
    days = int(period) if str(period).isdigit() else 30
    return timezone.now() - timedelta(days=days)


def _money(raw):
    try:
        return Decimal(str(raw or 0)).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return Decimal('0.00')


def _who(user):
    if not user:
        return ''
    return user.get_full_name() or user.username


def _line_changes(before, after):
    """What happened to each product between two order snapshots, e.g.
    ``Yara 100ml: qty 2 -> 3``, ``+ Afeef x1 @ 19.50``, ``- Khamrah x2``."""
    def by_product(snapshot):
        lines = {}
        for item in (snapshot or {}).get('items') or []:
            key = item.get('product_id') or item.get('product_name')
            row = lines.setdefault(key, {'name': item.get('product_name') or '?', 'qty': 0,
                                         'price': _money(item.get('unit_price'))})
            row['qty'] += int(item.get('quantity') or 0)
        return lines

    old, new = by_product(before), by_product(after)
    changes = []
    for key, row in new.items():
        was = old.get(key)
        if not was:
            changes.append(f"+ {row['name']} ×{row['qty']} @ {row['price']}")
            continue
        parts = []
        if was['qty'] != row['qty']:
            parts.append(f"qty {was['qty']} → {row['qty']}")
        if was['price'] != row['price']:
            parts.append(f"price {was['price']} → {row['price']}")
        if parts:
            changes.append(f"{row['name']}: " + ', '.join(parts))
    for key, row in old.items():
        if key not in new:
            changes.append(f"− {row['name']} ×{row['qty']}")
    return changes


def order_changes(since, query):
    from ..models import SaleOrder, SaleOrderChangeLog
    logs = SaleOrderChangeLog.objects.select_related('changed_by').order_by('-created_at', '-id')
    if since:
        logs = logs.filter(created_at__gte=since)
    if query:
        match = Q(reason__icontains=query) | Q(changed_by__username__icontains=query)
        if query.lstrip('#').isdigit():
            match |= Q(order_id_snapshot=int(query.lstrip('#')))
        logs = logs.filter(match)
    logs = list(logs)
    alive = set(SaleOrder.objects.filter(id__in={l.order_id_snapshot for l in logs}).values_list('id', flat=True))
    entries = []
    for log in logs:
        before, after = log.before_data or {}, log.after_data or {}
        shown = after or before
        if log.action == 'update':
            detail = f"€{_money(before.get('total_amount'))} → €{_money(after.get('total_amount'))}"
            changes = _line_changes(before, after)
            for field, label in (('customer_name', 'customer'), ('store_name', 'store')):
                was, now = before.get(field) or DASH, after.get(field) or DASH
                if was != now:
                    changes.append(f"{label}: {was} → {now}")
        else:
            detail = f"€{_money(shown.get('total_amount'))} · {shown.get('total_qty') or 0} units"
            changes = [f"{i.get('product_name')} ×{i.get('quantity')} @ {_money(i.get('unit_price'))}"
                       for i in shown.get('items') or []]
        extra = ' · '.join(x for x in (shown.get('customer_name'), shown.get('store_name')) if x)
        entries.append({
            'when': log.created_at,
            'tone': 'danger' if log.action == 'delete' else ('warn' if log.action == 'update' else 'info'),
            'tag': ACTION_LABELS.get(log.action, log.action),
            'title': f"Order #{log.order_id_snapshot}",
            'detail': detail + (f" · {extra}" if extra else ''),
            'reason': log.reason,
            'changes': changes,
            'who': _who(log.changed_by),
            'link': reverse('sale_order_detail', args=[log.order_id_snapshot])
            if log.order_id_snapshot in alive else '',
        })
    return entries


def stock_counts(since, query):
    from ..models import StockAdjustmentLog
    logs = StockAdjustmentLog.objects.select_related('user', 'product').order_by('-created_at', '-id')
    if since:
        logs = logs.filter(created_at__gte=since)
    if query:
        logs = logs.filter(Q(product__name__icontains=query) | Q(product__brand__icontains=query)
                           | Q(product__barcode__icontains=query) | Q(user__username__icontains=query))
    return [{
        'when': log.created_at,
        'product': log.product.display_name,
        'barcode': log.product.barcode or '',
        'link': reverse('product_detail', args=[log.product_id]),
        'old': log.old_value,
        'new': log.new_value,
        'diff': log.new_value - log.old_value,
        'scope': 'One batch' if log.adjustment_type == 'purchase_remaining' else 'Whole product',
        'who': _who(log.user),
    } for log in logs]


def deliveries(since, query):
    from ..models import InboundOrder
    orders = (InboundOrder.objects.filter(status='received', received_at__isnull=False)
              .select_related('supplier')
              .annotate(units=Sum('items__quantity'))
              .order_by('-received_at', '-id'))
    if since:
        orders = orders.filter(received_at__gte=since)
    if query:
        match = Q(supplier__name__icontains=query) | Q(invoice_no__icontains=query)
        if query.lstrip('#').isdigit():
            match |= Q(id=int(query.lstrip('#')))
        orders = orders.filter(match)
    rows = []
    for order in orders:
        took = None
        if order.placed_at and order.received_at and order.received_at > order.placed_at:
            took = (order.received_at - order.placed_at).days
        rows.append({
            'when': order.received_at,
            'id': order.id,
            'supplier': order.supplier.name if order.supplier else '',
            'link': reverse('supplier_detail', args=[order.supplier_id]) if order.supplier_id else '',
            'invoice': order.invoice_no or '',
            'units': order.units or 0,
            'amount': _money(order.total_amount),
            'took': took,
        })
    return rows


def admin_edits(since, query):
    """Django admin's history of /admin/: who added, changed or deleted what."""
    from django.contrib.admin.models import ADDITION, DELETION, LogEntry
    entries = LogEntry.objects.select_related('user', 'content_type').order_by('-action_time', '-id')
    if since:
        entries = entries.filter(action_time__gte=since)
    if query:
        entries = entries.filter(Q(object_repr__icontains=query) | Q(user__username__icontains=query)
                                 | Q(content_type__model__icontains=query))
    rows = []
    for e in entries:
        kind = e.content_type.name if e.content_type_id else ''
        deleted = e.action_flag == DELETION
        message = '' if deleted else e.get_change_message()
        rows.append({
            'when': e.action_time,
            'tone': 'info' if e.action_flag == ADDITION else ('danger' if deleted else 'warn'),
            'tag': 'Added' if e.action_flag == ADDITION else ('Deleted' if deleted else 'Changed'),
            'title': e.object_repr,
            'detail': kind[:1].upper() + kind[1:],
            'reason': '',
            'changes': [message] if message else [],
            'who': _who(e.user),
            'link': '' if deleted else (e.get_admin_url() or ''),
        })
    return rows


def accounts():
    """Every login with its role, home store and last sign-in, admins first."""
    from django.contrib.auth import get_user_model
    from ..permissions import has_manager_access
    users = get_user_model().objects.select_related('store_profile__store').prefetch_related('groups')
    rows = []
    for user in users:
        role = 'Admin' if user.is_superuser else ('Manager' if has_manager_access(user) else 'Employee')
        profile = getattr(user, 'store_profile', None)
        rows.append({
            'name': user.get_full_name() or user.username,
            'username': user.username,
            'role': role,
            'store': profile.store.name if profile and profile.store else '',
            'active': user.is_active,
            'last_login': user.last_login,
            'joined': user.date_joined,
        })
    order = {'Admin': 0, 'Manager': 1, 'Employee': 2}
    rows.sort(key=lambda r: (not r['active'], order[r['role']], r['name'].lower()))
    return rows


def failed_logins(since):
    from ..models import ErrorLog
    rows = ErrorLog.objects.filter(source=FAILED_LOGIN_SOURCE)
    if since:
        rows = rows.filter(last_seen__gte=since)
    return rows


def errors(since, query, status):
    """``ErrorLog`` rows; ``status`` is open, resolved or all."""
    from ..models import ErrorLog
    rows = ErrorLog.objects.exclude(source=FAILED_LOGIN_SOURCE).select_related('user')
    if since:
        rows = rows.filter(last_seen__gte=since)
    if status == 'open':
        rows = rows.filter(resolved_at__isnull=True)
    elif status == 'resolved':
        rows = rows.filter(resolved_at__isnull=False)
    if query:
        rows = rows.filter(Q(title__icontains=query) | Q(path__icontains=query) | Q(source__icontains=query))
    return rows


def open_error_count():
    from ..models import ErrorLog
    return (ErrorLog.objects.exclude(source=FAILED_LOGIN_SOURCE)
            .filter(resolved_at__isnull=True, level='ERROR').count())
