"""Supplier lead time: how long an inbound order takes from placed to received.

Measured from ``InboundOrder.placed_at`` (the order was put to the supplier)
to ``received_at`` (the goods were confirmed in). Both are stamped by the
model itself, so every order from now on is measured - a view that forgets is
a number lost for good.

Orders that predate ``placed_at`` are not measured at all. There is no honest
way to invent when they were ordered, and guessing drags a supplier's average
towards zero. That means the figures start thin and get sharper as orders come
in, which is why the sample size is shown next to every average.
"""
from collections import defaultdict

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


def measurable_orders():
    """Received orders that carry both timestamps, so the wait is a fact."""
    return InboundOrder.objects.filter(
        status='received',
        placed_at__isnull=False,
        received_at__isnull=False,
    )


def _rows(supplier=None):
    qs = measurable_orders().values_list('supplier_id', 'placed_at', 'received_at')
    if supplier is not None:
        qs = qs.filter(supplier=supplier)
    for supplier_id, placed, received in qs:
        # Clamped at zero: a clock adjustment must not produce a negative wait.
        yield supplier_id, max((received - placed).total_seconds() / 86400.0, 0.0)


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
    for order in (measurable_orders()
                  .filter(supplier=supplier)
                  .order_by('-received_at')[:limit]):
        days = order.lead_time_days or 0.0
        rows.append({'order': order, 'days': days, 'label': humanise(days)})
    return rows
