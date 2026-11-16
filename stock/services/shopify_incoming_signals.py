"""Wake only after inbound edits and receipts have committed."""
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from stock.models import InboundOrder, InboundPendingItem, Product, ProductImage, Purchase, Sale


@receiver(post_save, sender=InboundOrder)
@receiver(post_delete, sender=InboundOrder)
@receiver(post_save, sender=InboundPendingItem)
@receiver(post_delete, sender=InboundPendingItem)
@receiver(post_save, sender=Product)
@receiver(post_delete, sender=Product)
@receiver(post_save, sender=ProductImage)
@receiver(post_delete, sender=ProductImage)
@receiver(post_save, sender=Purchase)
@receiver(post_delete, sender=Purchase)
@receiver(post_save, sender=Sale)
@receiver(post_delete, sender=Sale)
def refresh_incoming(sender, instance, raw=False, **kwargs):
    if raw or not getattr(settings, 'SHOPIFY_INCOMING_SYNC', False):
        return
    from . import shopify_incoming
    transaction.on_commit(lambda: shopify_incoming.wake_incoming())
