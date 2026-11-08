"""What arrived lately: the catalogue's Novidades and Reposição.

A product whose first delivery from a supplier came in the last 30 days is new.
One delivered before that, with another delivery in the last 30 days, is back
in stock. Moves between the two shops are not deliveries.
"""
from datetime import timedelta

from django.db.models import Max, Min
from django.utils import timezone

from ..models import Purchase

DAYS = 30
NEW, RESTOCK = 'new', 'restock'
LABELS = {NEW: 'Novidades', RESTOCK: 'Reposição'}


def arrivals(product_ids, now=None):
    """``{product id: (NEW or RESTOCK, when)}`` for what arrived in the last 30 days."""
    cutoff = (now or timezone.now()) - timedelta(days=DAYS)
    rows = (Purchase.objects.filter(product_id__in=list(product_ids), transfer_line__isnull=True)
            .values('product').annotate(first=Min('date'), last=Max('date')).order_by())
    out = {}
    for row in rows:
        if row['first'] >= cutoff:
            out[row['product']] = (NEW, row['first'])
        elif row['last'] >= cutoff:
            out[row['product']] = (RESTOCK, row['last'])
    return out
