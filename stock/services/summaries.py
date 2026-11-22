import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils.timezone import make_naive

from ..models import DailySalesSummary, Purchase, Sale

logger = logging.getLogger(__name__)


def recalc_summary_for_date(day: date):
    purchase_events = [
        ("in", make_naive(p.date), p.product_id, p.quantity, p.cost_price, p.id)
        for p in Purchase.objects.filter(date__date__lte=day).only("id", "date", "product_id", "quantity", "cost_price")
    ]
    sale_events = [
        ("out", make_naive(s.date), s.product_id, s.quantity, s.unit_price, s.id)
        for s in Sale.objects.filter(date__date__lte=day).only("id", "date", "product_id", "quantity", "unit_price")
    ]

    if not any(event[1].date() == day for event in sale_events):
        DailySalesSummary.objects.filter(date=day).delete()
        return None

    events = purchase_events + sale_events
    events.sort(key=lambda event: (event[1], 0 if event[0] == "in" else 1, event[5]))

    stock_batches = defaultdict(list)
    total_sales = Decimal("0.00")
    total_cost = Decimal("0.00")
    total_items = 0

    for event_type, event_dt, product_id, quantity, amount, _event_id in events:
        if event_type == "in":
            stock_batches[product_id].append([quantity, amount])
            continue

        remaining = quantity
        line_cost = Decimal("0.00")

        while remaining > 0 and stock_batches[product_id]:
            batch_quantity, batch_cost = stock_batches[product_id][0]
            used = min(batch_quantity, remaining)
            line_cost += Decimal(used) * batch_cost
            batch_quantity -= used
            remaining -= used

            if batch_quantity == 0:
                stock_batches[product_id].pop(0)
            else:
                stock_batches[product_id][0][0] = batch_quantity

        if event_dt.date() == day:
            total_sales += (amount or Decimal("0.00")) * quantity
            total_cost += line_cost
            total_items += quantity

    if total_items == 0:
        DailySalesSummary.objects.filter(date=day).delete()
        return None

    summary, _created = DailySalesSummary.objects.update_or_create(
        date=day,
        defaults={
            "total_sales": total_sales,
            "total_profit": total_sales - total_cost,
            "total_items_sold": total_items,
        },
    )
    return summary


def rebuild_all_daily_summaries():
    sale_days = list(Sale.objects.dates("date", "day"))
    DailySalesSummary.objects.exclude(date__in=sale_days).delete()

    for day in sale_days:
        recalc_summary_for_date(day)

    return len(sale_days)


class _SummaryRecalc:
    """One day's recalculation, run once after the commit.

    It runs after the sale has committed, so it must not fail the request: a
    summary that could not be written is logged, and the next sale that day
    (or ``rebuild_dailysummary``) brings it up to date."""
    __qualname__ = 'recalc_summary_for_date'

    def __init__(self, day):
        self.day, self.done = day, False

    def __call__(self):
        self.done = True
        try:
            recalc_summary_for_date(self.day)
        except Exception:
            logger.exception('Daily sales summary for %s was not updated', self.day)


def schedule_summary_recalc(day: date):
    """Recalculate ``day`` once, after the transaction commits (2026-11-22).

    Every sale line used to schedule its own full-history replay, and when one
    of them hit "database is locked" the error reached the till as a failed
    sale - which had in fact been recorded. One pending callback per day, and
    it never raises."""
    connection = transaction.get_connection()
    if connection.in_atomic_block:
        for pending in connection.run_on_commit:
            callback = pending[1]
            if isinstance(callback, _SummaryRecalc) and callback.day == day and not callback.done:
                return
    transaction.on_commit(_SummaryRecalc(day), robust=True)
