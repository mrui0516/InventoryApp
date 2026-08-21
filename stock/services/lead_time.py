"""Supplier lead time: how long an inbound order takes from placed to received.

An order is placed when the pending inbound is created (``created_at``) and
lands when it is confirmed received (``received_at``). Only orders that actually
waited count: migration 0023 backfilled ``received_at = created_at`` on every
pre-existing order, and a direct inbound is received the moment it is entered,
so those rows carry no waiting time. Averaging them in drags every supplier
towards zero and makes the comparison useless, hence the ``received_at >
created_at`` filter.
"""
from collections import defaultdict

from django.db.models import F

from ..models import InboundOrder


def humanise(days):
    """Days as a short label. Many receipts land the same day, and '0.0d' hides
    whether that was ten minutes or ten hours."""
    if days is None:
        return '—'
    if days >= 1:
        return f'{days:.1f}d'
    hours = days * 24
    if hours >= 1:
        return f'{hours:.0f}h'
    return f'{max(hours * 60, 1):.0f}m'


def _stats(days):
    """Summarise a list of lead times (in days) for one supplier."""
    if not days:
        return None
    ordered = sorted(days)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    avg = sum(ordered) / len(ordered)
    return {
        'sample': len(ordered),
        'avg_days': avg,
        'median_days': median,
        'min_days': ordered[0],
        'max_days': ordered[-1],
        'avg_label': humanise(avg),
        'median_label': humanise(median),
        'min_label': humanise(ordered[0]),
        'max_label': humanise(ordered[-1]),
    }


def _rows(supplier=None):
    qs = (
        InboundOrder.objects
        .filter(status='received', received_at__isnull=False, received_at__gt=F('created_at'))
        .values_list('supplier_id', 'created_at', 'received_at')
    )
    if supplier is not None:
        qs = qs.filter(supplier=supplier)
    for supplier_id, created, received in qs:
        yield supplier_id, (received - created).total_seconds() / 86400.0


def supplier_lead_stats(supplier):
    """Lead-time summary for one supplier, or ``None`` when it has no timed orders."""
    return _stats([days for _sid, days in _rows(supplier)])


def lead_stats_by_supplier():
    """``{supplier_id: stats}`` for every supplier that has timed orders.

    One query for the whole list page, so the supplier table can rank them.
    """
    grouped = defaultdict(list)
    for supplier_id, days in _rows():
        if supplier_id is not None:
            grouped[supplier_id].append(days)
    return {sid: _stats(days) for sid, days in grouped.items()}


def recent_lead_times(supplier, limit=8):
    """The supplier's most recent timed orders, newest first, for the detail page."""
    rows = []
    for order in (InboundOrder.objects
                  .filter(supplier=supplier, status='received',
                          received_at__isnull=False, received_at__gt=F('created_at'))
                  .order_by('-received_at')[:limit]):
        days = (order.received_at - order.created_at).total_seconds() / 86400.0
        rows.append({'order': order, 'days': days, 'label': humanise(days)})
    return rows
