"""How far below wholesale a customer buys.

Nothing is stored: the figure is read from the customer's last sale, so a
corrected or deleted order changes it on its own (as ``last_prices`` does).
It is what the export and Pedido start from, and both can change it.
"""
from collections import Counter
from decimal import Decimal

from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone

# A wholesale discount is margin off a normal bottle of perfume. A 50ml is
# already cheap, so the same few euros off would take most of its margin; a
# 200ml is a body spray, priced by the litre rather than by the fragrance.
# Both ends of the range matter - this is not "big bottles", it is "the size a
# bottle of perfume comes in".
DISCOUNT_MIN_VOLUME_ML = 60
DISCOUNT_MAX_VOLUME_ML = 120


def discount_applies(product):
    """Whether an export discount may come off this product's wholesale price.

    Normal bottles of perfume only - 60ml to 120ml. A product with no volume
    recorded is left out on purpose: quoting a discount we did not mean costs
    the shop margin quietly, while missing one is visible and easily
    corrected.
    """
    category = getattr(product.category, 'name', '') if product.category_id else ''
    if 'perfum' not in (category or '').lower():
        return False
    volume = getattr(product, 'volume_ml', None)
    if volume is None or not (DISCOUNT_MIN_VOLUME_ML <= volume <= DISCOUNT_MAX_VOLUME_ML):
        return False
    # Size already excludes the 200ml sprays; this catches a body mist that
    # happens to be bottled at 100ml.
    strength = getattr(getattr(product, 'concentration', None), 'name', '') or ''
    return 'mist' not in strength.lower()


def last_discount(customer_id):
    """``{'discount', 'date', 'order_id'}`` from the customer's last sale that
    had a bottle the discount applies to, or None if they never bought one.

    ``discount`` is that sale's gap to the wholesale price: the commonest one
    by units when its lines differ (the lower on a tie, as a quote is easier
    to raise than to take back), and 0 when they paid wholesale or more.
    Samples do not count. The wholesale price is today's: a sale does not keep
    the one it was made against.
    """
    from ..models import Sale
    if not customer_id:
        return None
    lines = (Sale.objects
             .filter(Q(order__customer_id=customer_id) | Q(order__isnull=True, customer_id=customer_id),
                     is_sample=False)
             .annotate(at=Coalesce('order__created_at', 'date'))
             .select_related('product__category', 'product__concentration')
             .order_by('-at', '-id'))
    found, gaps = None, Counter()
    for line in lines:
        key = line.order_id or f'line-{line.id}'
        if found is not None and key != found['key']:
            break
        product = line.product
        if product.wholesale_price is None or not discount_applies(product):
            continue
        if found is None:
            found = {'key': key, 'order_id': line.order_id, 'date': line.at}
        gaps[max(product.wholesale_price - line.unit_price, Decimal('0.00'))] += line.quantity
    if found is None:
        return None
    best = max(gaps.values())
    return {'discount': min(gap for gap, units in gaps.items() if units == best),
            'order_id': found['order_id'],
            'date': timezone.localtime(found['date']).strftime('%d/%m/%Y')}
