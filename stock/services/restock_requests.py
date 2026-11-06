"""Who asked for a sold-out product, so the shop can see what is worth buying.

A request stays open until the product's next delivery: what counts is the wish
for this gap in stock, not everyone who ever asked. One person, or one customer
once the portal exists, counts once per gap.
"""
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from ..models import Product, Purchase, RestockRequest, Sale
from .wholesale_catalog import availability, sellable_units

SALES_DAYS = 90


def _opened_after(product_ids):
    """``{product_id: date}``: requests older than the last delivery are closed."""
    return dict(Purchase.objects.filter(product_id__in=product_ids).values_list('product')
                .annotate(last=Max('date')).order_by())


def _open_requests(product_ids=None):
    rows = RestockRequest.objects.all()
    if product_ids is not None:
        rows = rows.filter(product_id__in=product_ids)
    rows = list(rows)
    delivered = _opened_after({row.product_id for row in rows})
    return [row for row in rows if row.product_id not in delivered or row.created_at > delivered[row.product_id]]


def _who(row):
    return ('customer', row.customer_id) if row.customer_id else ('user', row.requested_by_id)


def request_restock(product, user, customer=None):
    """Record the wish. True if it counted, False if this person had asked already.
    Refused while the product is on sale: there is nothing to bring back."""
    if sellable_units([product.pk]).get(product.pk, 0) > 0:
        raise ValueError('This product is available.')
    mine = RestockRequest(product=product, requested_by=user, customer=customer)
    with transaction.atomic():
        if any(_who(row) == _who(mine) for row in _open_requests([product.pk])):
            return False
        mine.save()
    return True


def requested_ids(user, product_ids):
    """Those of ``product_ids`` this person has an open request for."""
    return {row.product_id for row in _open_requests(product_ids) if row.requested_by_id == user.pk}


def demand_rows():
    """What was asked for: the most wanted first, then the best selling."""
    grouped = defaultdict(list)
    for row in _open_requests():
        grouped[row.product_id].append(row)
    products = Product.objects.filter(pk__in=grouped)
    since = timezone.now() - timedelta(days=SALES_DAYS)
    sold = dict(Sale.objects.filter(product_id__in=grouped, date__gte=since).values_list('product')
                .annotate(units=Sum('quantity')).order_by())
    units = sellable_units(list(grouped))
    rows = []
    for product in products:
        asks = grouped[product.pk]
        rows.append({
            'product': product,
            'requests': len({_who(row) for row in asks}),
            'sold_90': sold.get(product.pk, 0),
            'last_request': max(row.created_at for row in asks),
            'state_label': availability(units.get(product.pk, 0))[1],
        })
    rows.sort(key=lambda row: (-row['requests'], -row['sold_90'], row['product'].list_name.lower()))
    return rows
