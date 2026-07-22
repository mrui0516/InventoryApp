"""Auto-pricing for Perfumes: derive selling prices from the current FIFO cost.

wholesale = ceil(current FIFO cost + 10); retail = wholesale + 12. Perfumes only,
skipped when the product is price-locked or has no positive cost. Idempotent:
writes via Product.objects.update() only when a price actually changes.
"""
import math
from decimal import Decimal

PERFUME_CATEGORY_NAME = 'Perfumes'
WHOLESALE_MARKUP = Decimal('10')
RETAIL_MARKUP = Decimal('12')


def is_perfume(product):
    cat = getattr(product, 'category', None)
    return bool(cat and (cat.name or '').strip().lower() == PERFUME_CATEGORY_NAME.lower())


def sync_perfume_price(product):
    """Recompute a perfume's prices from its current FIFO cost. Returns True if written."""
    from ..models import Product
    if product is None or getattr(product, 'price_locked', False) or not is_perfume(product):
        return False
    cost = product.current_fifo_cost_price() or Decimal('0.00')
    if cost <= 0:
        return False
    wholesale = Decimal(math.ceil(cost + WHOLESALE_MARKUP))
    retail = wholesale + RETAIL_MARKUP
    if product.wholesale_price == wholesale and product.default_price == retail:
        return False
    Product.objects.filter(pk=product.pk).update(
        wholesale_price=wholesale, default_price=retail
    )
    product.wholesale_price = wholesale
    product.default_price = retail
    return True
