"""What a customer can be offered."""
from django.db.models import Sum

from ..models import InboundPendingItem, Purchase

# The last one on the shelf is the display sample, not something to sell, so
# it never counts towards what a customer can have. A list that offers the
# sample either loses the display or disappoints whoever ordered it.
SAMPLE_RESERVE = 1

# Fewer than three sellable is "low stock": at one or two, a customer ordering
# a couple can empty the shelf, so it is worth saying so rather than promising
# availability we may not have by the time they arrive.
LOW_STOCK_BELOW = 3


def product_ids_on_order():
    """Products sitting on an inbound order that has not landed yet.

    Nothing new is recorded for this - an order placed with a supplier and not
    yet received *is* the definition of in transit, and the app already tracks
    it. So "in stock soon" stays true on its own, with nobody maintaining a
    second flag that would drift out of date.
    """
    return set(InboundPendingItem.objects
               .filter(inbound_order__status='pending_receipt')
               .values_list('product_id', flat=True))


def sample_reserve():
    """Settings -> Rules: the bottles each shop keeps open as samples."""
    from .rules import rules
    return rules().samples_per_shop


def low_stock_below():
    """Settings -> Rules: below this a customer reads "Stock reduzido"."""
    from .rules import rules
    return rules().catalog_low_stock_below


def offerable_ids(ids):
    """Those of ``ids`` a customer can have: more than the sample, or on the way."""
    stock = dict(Purchase.objects.filter(product_id__in=ids).values_list('product')
                 .annotate(n=Sum('remaining')).order_by())
    on_order = product_ids_on_order()
    reserve = sample_reserve()
    return {pk for pk in ids if (stock.get(pk) or 0) > reserve or pk in on_order}
