import logging

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import AttendanceRecord, Product, ProductImage, Purchase, Sale
from .permissions import has_manager_access
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


def _mirror_to_cloudinary(product):
    if not product:
        return
    if not getattr(settings, 'CLOUDINARY_AUTO_SYNC', False):
        return

    def _sync():
        try:
            from .services import cloudinary_sync
            code, detail = cloudinary_sync.sync_product_primary_image(product)
            logger.info('Cloudinary mirror %s for %s (%s)', code, getattr(product, 'barcode', '?'), detail)
        except Exception:  # never let a mirror problem break the local save/delete
            logger.exception('Cloudinary mirror crashed for %s', getattr(product, 'barcode', '?'))

    transaction.on_commit(_sync)


@receiver(post_save, sender=ProductImage)
def mirror_product_image_on_save(sender, instance, **kwargs):
    """Mirror the product's primary image to Cloudinary on create or replace."""
    _mirror_to_cloudinary(instance.product)


@receiver(post_delete, sender=ProductImage)
def mirror_product_image_on_delete(sender, instance, **kwargs):
    """Re-sync Cloudinary after an image is removed (upload new primary, or
    delete the asset when none remain)."""
    _mirror_to_cloudinary(instance.product)


def _push_shopify(product, *, do_price=False, do_inventory=False):
    """Push the product's price and/or on-hand to Shopify after commit. Gated by
    ``SHOPIFY_INVENTORY_SYNC``; idempotent (sets absolute values) so firing more
    than once for the same product is harmless; never raises — a Shopify hiccup
    must not break the local save."""
    if not product:
        return
    if not getattr(settings, 'SHOPIFY_INVENTORY_SYNC', False):
        return

    def _sync():
        try:
            from .services import shopify_sync
            code, detail = shopify_sync.sync_product_price_inventory(
                product, do_price=do_price, do_inventory=do_inventory)
            logger.info('Shopify sync %s for %s (%s)', code, getattr(product, 'barcode', '?'), detail)
        except Exception:  # never let a sync problem break the local save
            logger.exception('Shopify sync crashed for %s', getattr(product, 'barcode', '?'))

    transaction.on_commit(_sync)


@receiver(post_save, sender=Sale)
def push_inventory_on_sale(sender, instance, **kwargs):
    _push_shopify(instance.product, do_inventory=True)


@receiver(post_delete, sender=Sale)
def push_inventory_on_sale_delete(sender, instance, **kwargs):
    _push_shopify(instance.product, do_inventory=True)


@receiver(post_save, sender=Purchase)
def push_inventory_on_purchase(sender, instance, **kwargs):
    _push_shopify(instance.product, do_inventory=True)


@receiver(post_delete, sender=Purchase)
def push_inventory_on_purchase_delete(sender, instance, **kwargs):
    _push_shopify(instance.product, do_inventory=True)


@receiver(pre_save, sender=Product)
def _capture_old_default_price(sender, instance, **kwargs):
    """Remember the pre-save price so post_save can tell if it changed. Only when
    real-time sync is on, to avoid an extra query otherwise."""
    if not getattr(settings, 'SHOPIFY_INVENTORY_SYNC', False):
        return
    if not instance.pk:
        instance._old_default_price = None
        return
    instance._old_default_price = (
        Product.objects.filter(pk=instance.pk).values_list('default_price', flat=True).first())


@receiver(post_save, sender=Product)
def push_price_on_change(sender, instance, created, **kwargs):
    """When a product's price changes in the app, push the new price to Shopify."""
    if created or not hasattr(instance, '_old_default_price'):
        return
    if instance._old_default_price == instance.default_price:
        return
    _push_shopify(instance, do_price=True)


@receiver(user_logged_in)
def open_attendance_on_login(sender, request, user, **kwargs):
    """Employees: opening a shift on login. Reuse today's open shift; close a
    stale previous-day open shift first."""
    if has_manager_access(user):
        return
    today = timezone.localdate()
    open_shift = (
        AttendanceRecord.objects
        .filter(user=user, clock_out_at__isnull=True)
        .order_by('-clock_in_at', '-id')
        .first()
    )
    if open_shift:
        if timezone.localtime(open_shift.clock_in_at).date() == today:
            return  # already an open shift today
        end_of_day = timezone.localtime(open_shift.clock_in_at).replace(
            hour=23, minute=59, second=59, microsecond=0)
        open_shift.clock_out_at = end_of_day
        open_shift.note = (open_shift.note + ' auto-closed: no logout').strip()
        open_shift.save(update_fields=['clock_out_at', 'note'])
    AttendanceRecord.objects.create(user=user, clock_in_at=timezone.now(), note='auto: login')


@receiver(user_logged_out)
def close_attendance_on_logout(sender, request, user, **kwargs):
    """Employees: closing the open shift on logout."""
    if user is None or has_manager_access(user):
        return
    open_shift = (
        AttendanceRecord.objects
        .filter(user=user, clock_out_at__isnull=True)
        .order_by('-clock_in_at', '-id')
        .first()
    )
    if open_shift:
        open_shift.clock_out_at = timezone.now()
        open_shift.note = (open_shift.note + ' auto: logout').strip()
        open_shift.save(update_fields=['clock_out_at', 'note'])


@receiver(post_save, sender=Purchase)
def reprice_perfume_on_purchase(sender, instance, **kwargs):
    """A new/updated purchase batch may change a perfume's current FIFO cost."""
    from .services.pricing import sync_perfume_price
    try:
        sync_perfume_price(instance.product)
    except Exception:  # never let pricing break an inbound
        logger.exception('perfume reprice on purchase failed for %s', getattr(instance, 'product_id', '?'))


@receiver(post_delete, sender=Purchase)
def reprice_perfume_on_purchase_delete(sender, instance, **kwargs):
    """Deleting a batch may change a perfume's current FIFO cost."""
    from .services.pricing import sync_perfume_price
    try:
        sync_perfume_price(instance.product)
    except Exception:
        logger.exception('perfume reprice on purchase delete failed for %s', getattr(instance, 'product_id', '?'))
