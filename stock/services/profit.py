from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.utils.timezone import make_naive

from ..models import Purchase, Sale

BACKFILL_MARGIN = Decimal("0.50")  # no-stock backfill orders book a flat 50% profit margin


def sale_profit_map_for_sale_ids(sale_ids):
    relevant_sale_ids = {int(sale_id) for sale_id in sale_ids if sale_id}
    if not relevant_sale_ids:
        return {}

    relevant_sales = list(
        Sale.objects.filter(id__in=relevant_sale_ids)
        .only("id", "date", "quantity", "unit_price", "cost_basis")
    )
    if not relevant_sales:
        return {}

    # Sales that captured their true FIFO cost at sale time use it directly — no
    # history replay needed. Only the rest fall back to the reconstruction below,
    # so once cost_basis is backfilled this function does no heavy work.
    profit_map = {}
    reconstruct_ids = set()
    for sale in relevant_sales:
        if sale.cost_basis is not None:
            revenue = (sale.unit_price or Decimal("0.00")) * sale.quantity
            profit_map[sale.id] = {
                "revenue": revenue, "cost": sale.cost_basis, "profit": revenue - sale.cost_basis,
            }
        else:
            reconstruct_ids.add(sale.id)
    if not reconstruct_ids:
        return profit_map  # fast path

    relevant_sale_ids = reconstruct_ids
    end_day = max(make_naive(s.date).date() for s in relevant_sales if s.id in reconstruct_ids)

    no_stock_sale_ids = set(
        Sale.objects.filter(order__affects_stock=False, date__date__lte=end_day)
        .values_list("id", flat=True)
    )

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

    for event_type, event_dt, product_id, quantity, amount, event_id in events:
        if event_type == "in":
            stock_batches[product_id].append([quantity, amount or Decimal("0.00")])
            continue

        if event_id in no_stock_sale_ids:
            # Backfill order: does not consume FIFO batches; flat margin (unless a
            # real cost basis was captured).
            if event_id in relevant_sale_ids:
                revenue = (amount or Decimal("0.00")) * quantity
                cost = (revenue * (Decimal("1") - BACKFILL_MARGIN)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                profit_map[event_id] = {"revenue": revenue, "cost": cost, "profit": revenue - cost}
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
