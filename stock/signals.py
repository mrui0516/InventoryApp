import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import ProductImage, Sale
from .services import schedule_summary_recalc

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Sale)
def capture_previous_summary_day(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_summary_day = None
        return

    previous_date = Sale.objects.filter(pk=instance.pk).values_list("date", flat=True).first()
    instance._previous_summary_day = previous_date.date() if previous_date else None


@receiver(post_save, sender=Sale)
def update_summary_on_save(sender, instance, **kwargs):
    current_day = instance.date.date()
    previous_day = getattr(instance, "_previous_summary_day", None)

    schedule_summary_recalc(current_day)
    if previous_day and previous_day != current_day:
        schedule_summary_recalc(previous_day)


@receiver(post_delete, sender=Sale)
def update_summary_on_delete(sender, instance, **kwargs):
    schedule_summary_recalc(instance.date.date())


@receiver(post_save, sender=ProductImage)
def push_product_image_to_shopify(sender, instance, created, **kwargs):
    """When a product photo is uploaded in the app, sync the product to Shopify:
    attach the image to the matching product, or (if ``SHOPIFY_AUTO_CREATE``)
    create the product there — variant/price/inventory/SEO/image.

    Off unless ``SHOPIFY_AUTO_SYNC`` is enabled, runs after commit, and never
    raises — a Shopify hiccup must not break saving the product image.
    """
    if not created or not getattr(settings, 'SHOPIFY_AUTO_SYNC', False):
        return

    product = instance.product
    if not product:
        return

    def _push():
        try:
            from .services import shopify_sync
            code, detail = shopify_sync.sync_product(
                product,
                create_missing=getattr(settings, 'SHOPIFY_AUTO_CREATE', False),
                status=getattr(settings, 'SHOPIFY_NEW_PRODUCT_STATUS', 'DRAFT'),
            )
            if code in (shopify_sync.UPLOADED, shopify_sync.CREATED):
                logger.info('Shopify sync %s for %s', code, product.barcode)
            elif code == shopify_sync.ERROR:
                logger.warning('Shopify sync error for %s: %s', product.barcode, detail)
            else:
                logger.info('Shopify sync for %s: %s (%s)', product.barcode, code, detail)
        except Exception:  # never let a sync problem break the upload
            logger.exception('Shopify sync crashed for %s', getattr(product, 'barcode', '?'))

    transaction.on_commit(_push)
