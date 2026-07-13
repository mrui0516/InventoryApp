"""Mirror a product's primary image to Cloudinary.

One Cloudinary asset per product: public_id == Product.barcode, placed in
asset_folder product_images/<brand>. The asset always reflects the product's
current primary image (product.images.first()); when the product has no image
left, the asset is deleted.
"""
import logging
import os
import re

from django.conf import settings

from .cloudinary_client import CloudinaryClient, CloudinaryError

logger = logging.getLogger(__name__)

UPLOADED = 'uploaded'
DELETED = 'deleted'
SKIP_NO_BARCODE = 'no_barcode'
ERROR = 'error'


def _sanitize_brand(brand):
    brand = (brand or '').strip()
    return re.sub(r'[\\/:*?"<>|]+', '', brand).strip()


def _brand_folder(product):
    base = getattr(settings, 'CLOUDINARY_FOLDER', 'product_images')
    brand = _sanitize_brand(getattr(product, 'brand', ''))
    return f'{base}/{brand}' if brand else base


def _primary_image_path(product):
    image = product.images.first()
    if not image or not getattr(image, 'image', None):
        return None
    try:
        path = image.image.path
    except (ValueError, NotImplementedError):
        return None
    return path if (path and os.path.exists(path)) else None


def sync_product_primary_image(product, client=None, *, dry_run=False):
    """Make the Cloudinary asset match the product's current primary image."""
    client = client or CloudinaryClient()
    barcode = (product.barcode or '').strip()
    if not barcode:
        return SKIP_NO_BARCODE, 'product has no barcode'

    path = _primary_image_path(product)
    try:
        if path:
            if not dry_run:
                client.upload_image(path, public_id=barcode, asset_folder=_brand_folder(product))
            return UPLOADED, barcode
        if not dry_run:
            client.delete_image(barcode)
        return DELETED, barcode
    except CloudinaryError as exc:
        return ERROR, str(exc)
