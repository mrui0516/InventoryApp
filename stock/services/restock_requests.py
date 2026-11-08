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

from ..models import Purchase, RestockRequest, Sale
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


TELL_DAYS = 60


def _requester(row):
    """Who asked, for the shop to read: the customer, how, and who typed it."""
    customer = row.customer
    account = getattr(row.requested_by, 'customer_account', None) if row.requested_by_id else None
    if customer is None and account is not None:
        customer = account.customer
    if account is not None:
        via = 'B2B'
    elif row.requested_by_id:
        via = f'Staff · {row.requested_by.username}'
    else:
        via = ''
    return {'request': row, 'customer': customer, 'via': via,
            'name': customer.name if customer else (row.requested_by.username if row.requested_by_id else '?'),
            'phone': (customer.phone or '') if customer else ''}


def _requests(**filters):
    return list(RestockRequest.objects.filter(product__archived_at__isnull=True, **filters)
                .select_related('product', 'customer', 'requested_by__customer_account__customer'))


def _split():
    """``(still_missing, back)``: requests a delivery or stock on hand has answered
    go to ``back`` until the customer is told; the rest are still missing."""
    rows = _requests(notified_at__isnull=True, created_at__gte=timezone.now() - timedelta(days=TELL_DAYS))
    delivered = _opened_after({row.product_id for row in rows})
    stock = sellable_units(list({row.product_id for row in rows}))
    missing, back = [], []
    for row in rows:
        answered = (row.product_id in delivered and delivered[row.product_id] > row.created_at) \
            or stock.get(row.product_id, 0) > 0
        (back if answered else missing).append(row)
    return missing, back


def _by_product(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.product_id].append(_requester(row))
    return grouped


def arrived_rows():
    """Back in stock, by product: ``[{product, asks: [requester]}]``, to tell each customer."""
    _missing, back = _split()
    out = [{'product': asks[0]['request'].product, 'asks': asks} for asks in _by_product(back).values()]
    out.sort(key=lambda r: r['product'].list_name.lower())
    return out


def tell_message(request_row, catalog_url):
    customer = _requester(request_row)['customer']
    name = customer.name.split()[0] if customer and customer.name else ''
    product = f'{request_row.product.brand} {request_row.product.list_name}'.strip()
    return (f'Olá {name}! O {product} que pediu já chegou. '
            f'Pode encomendar no nosso catálogo: {catalog_url}')


def demand_rows():
    """Still missing: the most wanted first, then the best selling, each with who asked."""
    missing, _back = _split()
    grouped = _by_product(missing)
    since = timezone.now() - timedelta(days=SALES_DAYS)
    sold = dict(Sale.objects.filter(product_id__in=grouped, date__gte=since).values_list('product')
                .annotate(units=Sum('quantity')).order_by())
    units = sellable_units(list(grouped))
    rows = []
    for asks in grouped.values():
        product = asks[0]['request'].product
        rows.append({
            'product': product, 'asks': asks,
            'requests': len({_who(a['request']) for a in asks}),
            'sold_90': sold.get(product.pk, 0),
            'last_request': max(a['request'].created_at for a in asks),
            'state_label': availability(units.get(product.pk, 0))[1],
        })
    rows.sort(key=lambda row: (-row['requests'], -row['sold_90'], row['product'].list_name.lower()))
    return rows
