from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils.timezone import make_naive

from ..models import DailySalesSummary, Purchase, Sale


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


def schedule_summary_recalc(day: date):
    transaction.on_commit(lambda summary_day=day: recalc_summary_for_date(summary_day))
