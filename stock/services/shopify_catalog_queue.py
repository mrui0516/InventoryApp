"""Enqueue within the stock transaction; perform no network I/O here."""
import logging
import threading

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)
_running = threading.Lock()


def enabled():
    return bool(getattr(settings, 'SHOPIFY_CATALOG_AUTOMATION', False))


def enqueue(product, *, wake_worker=True):
    if not enabled() or not getattr(product, 'pk', None):
        return
    from stock.models import ShopifyCatalogState
    state, _ = ShopifyCatalogState.objects.get_or_create(product_id=product.pk)
    ShopifyCatalogState.objects.filter(pk=state.pk).update(
        requested_revision=F('requested_revision') + 1,
        next_attempt_at=timezone.now(), updated_at=timezone.now())
    if wake_worker:
        transaction.on_commit(wake)


def receipt_recorded(product, *, before_stock, had_history):
    if not enabled():
        return
    from stock.models import ShopifyCatalogState
    if had_history and before_stock <= 0 and product.total_stock() > 0:
        state, _ = ShopifyCatalogState.objects.get_or_create(product_id=product.pk)
        ShopifyCatalogState.objects.filter(pk=state.pk).update(last_restocked_at=timezone.now())
    enqueue(product)


def wake():
    """Best-effort immediate wake. The watcher owns durable retry/recovery."""
    if not enabled() or not getattr(settings, 'SHOPIFY_PUSH_BACKGROUND', True):
        return
    if not _running.acquire(blocking=False):
        return

    def run():
        close_old_connections()
        try:
            from .shopify_catalog_worker import process_catalog
            process_catalog()
        except Exception:
            logger.exception('Shopify catalogue wake failed; durable jobs remain queued')
        finally:
            close_old_connections()
            _running.release()

    try:
        threading.Thread(target=run, daemon=True, name='shopify-catalogue').start()
    except Exception:
        _running.release()
        logger.exception('Could not wake Shopify catalogue worker')
