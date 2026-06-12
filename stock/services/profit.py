from collections import defaultdict
from decimal import Decimal

from django.utils.timezone import make_naive

from ..models import Purchase, Sale


def sale_profit_map_for_sale_ids(sale_ids):
    relevant_sale_ids = {int(sale_id) for sale_id in sale_ids if sale_id}
    if not relevant_sale_ids:
        return {}

    relevant_sales = list(
        Sale.objects.filter(id__in=relevant_sale_ids).only("id", "date")
    )
    if not relevant_sales:
        return {}

    end_day = max(make_naive(sale.date).date() for sale in relevant_sales)

    purchase_events = [
        ("in", make_naive(p.date), p.product_id, p.quantity, p.cost_price, p.id)
        for p in Purchase.objects.filter(date__date__lte=end_day).only(
            "id", "date", "product_id", "quantity", "cost_price"
        )
    ]
    sale_events = [
        ("out", make_naive(s.date), s.product_id, s.quantity, s.unit_price, s.id)
        for s in Sale.objects.filter(date__date__lte=end_day).only(
            "id", "date", "product_id", "quantity", "unit_price"
        )
    ]

    events = purchase_events + sale_events
    events.sort(key=lambda event: (event[1], 0 if event[0] == "in" else 1, event[5]))

    stock_batches = defaultdict(list)
    profit_map = {}

    for event_type, event_dt, product_id, quantity, amount, event_id in events:
        if event_type == "in":
            stock_batches[product_id].append([quantity, amount or Decimal("0.00")])
            continue

        remaining = quantity
        line_cost = Decimal("0.00")

        while remaining > 0 and stock_batches[product_id]:
            batch_quantity, batch_cost = stock_batches[product_id][0]
            used = min(batch_quantity, remaining)
            line_cost += Decimal(used) * (batch_cost or Decimal("0.00"))
            batch_quantity -= used
            remaining -= used

            if batch_quantity == 0:
                stock_batches[product_id].pop(0)
            else:
                stock_batches[product_id][0][0] = batch_quantity

        if event_id in relevant_sale_ids:
            revenue = (amount or Decimal("0.00")) * quantity
            profit_map[event_id] = {
                "revenue": revenue,
                "cost": line_cost,
                "profit": revenue - line_cost,
            }

    return profit_map
