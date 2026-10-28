"""Settings -> Audit: one dated feed of what changed orders and stock after the fact.

Three sources, merged newest first:

* ``SaleOrderChangeLog`` - orders entered, edited or deleted through Correct
  orders, with who, why and a line-by-line before/after;
* ``StockAdjustmentLog`` - stock counts set by hand on a product;
* ``InboundOrder`` received - deliveries that put stock on the shelf.

Sales and stock-ins made the normal way are the business itself, not
corrections, and live in Orders and Inbound.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone

KINDS = (
    ('orders', 'Orders'),
    ('stock', 'Stock'),
    ('deliveries', 'Deliveries'),
)
PERIODS = (('7', '7 days'), ('30', '30 days'), ('90', '90 days'), ('all', 'All'))
DASH = '—'
ACTION_LABELS = {'create': 'Added', 'update': 'Edited', 'delete': 'Deleted'}


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
    ``Yara 100ml 2 -> 3``, ``+ Afeef x1 @ 19.50``, ``- Khamrah x2``."""
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


def _order_entries(since, query):
    from ..models import SaleOrder, SaleOrderChangeLog
    logs = SaleOrderChangeLog.objects.select_related('changed_by')
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
            'kind': 'orders',
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


def _stock_entries(since, query):
    from ..models import StockAdjustmentLog
    logs = StockAdjustmentLog.objects.select_related('user', 'product')
    if since:
        logs = logs.filter(created_at__gte=since)
    if query:
        logs = logs.filter(Q(product__name__icontains=query) | Q(product__brand__icontains=query)
                           | Q(product__barcode__icontains=query) | Q(user__username__icontains=query))
    entries = []
    for log in logs:
        diff = log.new_value - log.old_value
        entries.append({
            'when': log.created_at,
            'kind': 'stock',
            'tone': 'danger' if diff < 0 else 'info',
            'tag': 'Stock count',
            'title': log.product.display_name,
            'detail': f"{log.old_value} → {log.new_value} ({diff:+d})"
                      + (' · one batch' if log.adjustment_type == 'purchase_remaining' else ''),
            'reason': '',
            'changes': [],
            'who': _who(log.user),
            'link': reverse('product_detail', args=[log.product_id]),
        })
    return entries


def _delivery_entries(since, query):
    from ..models import InboundOrder
    orders = (InboundOrder.objects.filter(status='received', received_at__isnull=False)
              .select_related('supplier')
              .annotate(units=Sum('items__quantity')))
    if since:
        orders = orders.filter(received_at__gte=since)
    if query:
        match = Q(supplier__name__icontains=query) | Q(invoice_no__icontains=query)
        if query.lstrip('#').isdigit():
            match |= Q(id=int(query.lstrip('#')))
        orders = orders.filter(match)
    entries = []
    for order in orders:
        bits = [order.supplier.name if order.supplier else '', order.invoice_no or '',
                f"{order.units or 0} units", f"€{_money(order.total_amount)}"]
        entries.append({
            'when': order.received_at,
            'kind': 'deliveries',
            'tone': 'ok',
            'tag': 'Received',
            'title': f"Inbound #{order.id}",
            'detail': ' · '.join(b for b in bits if b),
            'reason': '',
            'changes': [],
            'who': '',
            'link': reverse('supplier_detail', args=[order.supplier_id]) if order.supplier_id else '',
        })
    return entries


SOURCES = {'orders': _order_entries, 'stock': _stock_entries, 'deliveries': _delivery_entries}


def feed(kind='', period='30', query=''):
    """``(entries newest first, {kind: count})`` for the chosen period and search.
    The counts ignore ``kind`` so every tab shows its own number."""
    since = None
    if period != 'all':
        days = int(period) if period.isdigit() else 30
        since = timezone.now() - timedelta(days=days)
    query = (query or '').strip()
    found = {name: source(since, query) for name, source in SOURCES.items()}
    counts = {name: len(rows) for name, rows in found.items()}
    rows = found[kind] if kind in found else [r for rows in found.values() for r in rows]
    rows.sort(key=lambda r: r['when'], reverse=True)
    return rows, counts
