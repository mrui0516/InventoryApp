"""Sell-through: how fast a delivery sold down to low stock.

For each product that had a delivery in the last ``days`` days, take its
latest batch, work out the stock right after it arrived (stock now + sold
since - received since), then walk the sales that followed until the stock
fell below the low-stock line. The products that got there in the fewest days
are the ones to reorder sooner and in larger quantities.

Manual stock corrections are not replayed; a product that has not reached low
stock yet is not listed (it is still selling through).
"""
from collections import defaultdict
from datetime import timedelta

from django.db.models import Sum
from django.db.models.functions import Coalesce

from .low_stock import LOW_STOCK_BELOW


def sell_through(period_end, category_ids=(), days=90, limit=8):
    from ..models import Purchase, Sale
    since = period_end - timedelta(days=days)
    batches = (Purchase.objects
               .filter(date__date__gte=since, date__date__lte=period_end, product__archived_at__isnull=True)
               .select_related('product')
               .prefetch_related('product__images')
               .order_by('date', 'id'))
    if category_ids:
        batches = batches.filter(product__category_id__in=category_ids)
    latest = {}
    for batch in batches:
        latest[batch.product_id] = batch            # the last one wins
    if not latest:
        return []
    ids = list(latest)

    on_hand = dict(Purchase.objects.filter(product_id__in=ids, remaining__gt=0)
                   .values_list('product_id').annotate(s=Sum('remaining')).order_by())
    later_deliveries = defaultdict(int)
    for product_id, qty, at, batch_id in (Purchase.objects.filter(product_id__in=ids)
                                          .values_list('product_id', 'quantity', 'date', 'id')):
        batch = latest[product_id]
        if batch_id != batch.id and at > batch.date:
            later_deliveries[product_id] += qty
    sales = defaultdict(list)
    for product_id, at, qty in (Sale.objects.filter(product_id__in=ids)
                                .annotate(at=Coalesce('order__created_at', 'date'))
                                .order_by('at').values_list('product_id', 'at', 'quantity')):
        sales[product_id].append((at, qty))

    from ..views import get_product_image_url
    rows = []
    for product_id, batch in latest.items():
        after = [(at, qty) for at, qty in sales[product_id] if at >= batch.date]
        level = on_hand.get(product_id, 0) + sum(q for _, q in after) - later_deliveries[product_id]
        if level < LOW_STOCK_BELOW:
            continue                     # it arrived already low: nothing to measure
        start, sold = level, 0
        for at, qty in after:
            level -= qty
            sold += qty
            if level < LOW_STOCK_BELOW:
                rows.append({
                    'product_id': product_id,
                    'brand': batch.product.brand,
                    'name': batch.product.list_name,
                    'image_url': get_product_image_url(batch.product),
                    'arrived_at': batch.date,
                    'arrived_qty': batch.quantity,
                    'stock_after_arrival': start,
                    'sold': sold,
                    'days': max((at - batch.date).total_seconds() / 86400.0, 0.0),
                })
                break
    rows.sort(key=lambda r: (r['days'], -r['sold']))
    return rows[:limit]
