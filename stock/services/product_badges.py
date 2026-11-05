"""The notes a customer list prints beside a product.

Only for what can be bought: praising something sold out only disappoints.
"""
import math
from datetime import timedelta

from django.db.models import Min, Sum
from django.utils import timezone

from ..models import Purchase, Sale
from .availability import offerable_ids

NEW = 'Novidade'
BEST_SELLER = 'Mais vendido'

NEW_DAYS = 14           # since the product's first delivery, not its latest
SALES_DAYS = 30
BEST_SHARE = 0.10       # of the products on the list: rarer means more
BEST_MIN_UNITS = 5      # so a quiet month does not crown one sale


def product_badges(products):
    """``{product_id: label}`` for ``products``, ranked against each other."""
    ids = [p.pk for p in products]
    if not ids:
        return {}
    now = timezone.now()
    offerable = offerable_ids(ids)

    first_delivery = dict(Purchase.objects.filter(product_id__in=ids).values_list('product')
                          .annotate(first=Min('date')).order_by())
    new_since = now - timedelta(days=NEW_DAYS)
    badges = {pk: NEW for pk, first in first_delivery.items() if first >= new_since and pk in offerable}

    sold = (Sale.objects.filter(product_id__in=ids, date__gte=now - timedelta(days=SALES_DAYS))
            .values_list('product').annotate(units=Sum('quantity')).order_by('-units', 'product'))
    ranked = [pk for pk, units in sold if units >= BEST_MIN_UNITS and pk in offerable]
    for pk in ranked[:math.ceil(len(ids) * BEST_SHARE)]:
        badges.setdefault(pk, BEST_SELLER)
    return badges
