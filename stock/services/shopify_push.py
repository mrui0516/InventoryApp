"""Push stock to Shopify when it changes here, without making the till wait.

The app is the authority on stock. Anything that moves it - a sale, a receipt,
a correction, a manual adjustment - should reach Shopify on its own, so nobody
has to remember to go and fix the online quantity by hand.

Two things this has to get right.

**The till must not wait.** ``transaction.on_commit`` callbacks run inline in
the request thread, so pushing directly from a signal would make a five-line
sale wait on fifteen Shopify API calls before the page came back. Products are
collected during the transaction, deduplicated, and handed to a background
thread once it commits.

**Nothing is read back from Shopify.** This is one-way by design: sales made
on the storefront are entered into the app by hand. A two-way sync would need
to decide which side wins when both change, and getting that wrong quietly
loses stock.

Pushes set absolute quantities, so firing twice for the same product is
harmless. If a worker is recycled before a thread finishes, the push is simply
lost - which is what ``sync_shopify_inventory`` exists to reconcile, and why
it is worth running nightly.
"""
import logging
import threading

from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

_pending = threading.local()


def enabled():
    return bool(getattr(settings, 'SHOPIFY_INVENTORY_SYNC', False))


def _in_background():
    """Threaded by default; tests and management commands run inline."""
    return bool(getattr(settings, 'SHOPIFY_PUSH_BACKGROUND', True))


def queue_inventory_push(product):
    """Note that ``product``'s stock moved; push once this transaction commits.

    Deduplicated per transaction: an order with three lines of the same product
    is one push, not three.
    """
    if not enabled():
        return
    product_id = getattr(product, 'pk', None)
    if not product_id:
        return

    pending = getattr(_pending, 'ids', None)
    if pending is None:
        pending = _pending.ids = set()
    was_empty = not pending
    pending.add(product_id)
    if was_empty:
        transaction.on_commit(flush)


def flush():
    """Send everything collected in this transaction."""
    product_ids = sorted(getattr(_pending, 'ids', ()) or ())
    _pending.ids = set()
    if not product_ids:
        return
    if _in_background():
        threading.Thread(target=push_products, args=(product_ids,),
                         daemon=True, name='shopify-stock-push').start()
    else:
        push_products(product_ids)


def push_products(product_ids):
    """Set each product's Shopify quantity to what the app now holds.

    Never raises: a Shopify outage must not turn into a failed sale, and the
    quantity here is absolute, so the next push repairs whatever was missed.
    """
    from ..models import Product
    from . import shopify_sync

    try:
        products = list(shopify_sync.shopify_syncable(Product.objects)
                        .filter(pk__in=product_ids)
                        .exclude(barcode='')
                        .exclude(barcode__isnull=True))
    except Exception:
        logger.exception('Shopify push could not load products %s', product_ids)
        return

    for product in products:
        try:
            code, detail = shopify_sync.sync_product_price_inventory(
                product, do_price=False, do_inventory=True)
            logger.info('Shopify stock %s for %s (%s)', code, product.barcode, detail)
        except Exception:
            logger.exception('Shopify stock push failed for %s', product.barcode)
