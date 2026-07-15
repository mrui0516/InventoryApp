"""Build public Cloudinary delivery URLs for mirrored product images.

The read half of the naming contract that ``cloudinary_sync`` writes: one asset
per product, ``public_id == Product.barcode``. Pure string building — this
module never calls Cloudinary and never reads API credentials.
"""
from django.conf import settings

# 1:1 white-padded square JPEG. Matches the normalization already applied to the
# live Shopify images, so CSV-imported products display at a uniform size. The
# .jpg extension is required: the stored originals are WebP.
SHOPIFY_IMAGE_TRANSFORMATION = 'c_pad,b_white,w_1600,h_1600,q_auto'


def _has_primary_image(product):
    images = getattr(product, 'images', None)
    if images is None:
        return False
    return images.first() is not None


def product_image_cdn_url(product, transformation=SHOPIFY_IMAGE_TRANSFORMATION):
    """Public URL for the product's mirrored image, or '' if there isn't one."""
    cloud_name = getattr(settings, 'CLOUDINARY_CLOUD_NAME', '')
    if not product or not cloud_name:
        return ''
    barcode = (getattr(product, 'barcode', '') or '').strip()
    if not barcode or not _has_primary_image(product):
        return ''
    return (
        f'https://res.cloudinary.com/{cloud_name}/image/upload/'
        f'{transformation}/{barcode}.jpg'
    )
