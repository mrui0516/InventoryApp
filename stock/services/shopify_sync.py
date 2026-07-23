"""Sync a product from the app to its Shopify product.

Match key: our ``Product.barcode`` == the Shopify variant ``sku``.

- ``sync_product_image`` — attach the local photo to an *existing* Shopify product.
- ``create_product_in_shopify`` — create a missing product (variant / price /
  SKU / barcode / cost / inventory / SEO / image) in one ``productSet`` call.
- ``sync_product`` — find-or-(optionally)-create, then ensure the image.

Used by the ``sync_shopify_images`` / ``sync_shopify_products`` commands and the
ProductImage signal.
"""
import logging
import os
import re

from .shopify_client import ShopifyClient, ShopifyError

logger = logging.getLogger(__name__)

# Result codes (stable — tests/CLI rely on them).
SKIP_NO_BARCODE = 'no_barcode'
SKIP_NO_IMAGE = 'no_image_file'
SKIP_NOT_IN_SHOPIFY = 'not_in_shopify'
SKIP_HAS_IMAGE = 'already_has_image'
WOULD_UPLOAD = 'would_upload'
WOULD_CREATE = 'would_create'
UPLOADED = 'uploaded'
CREATED = 'created'
ERROR = 'error'



def _local_image_path(product):
    """Absolute path of the product's first image file, or None."""
    image = product.images.first()
    if not image or not getattr(image, 'image', None):
        return None
    try:
        path = image.image.path
    except (ValueError, NotImplementedError):
        return None
    return path if (path and os.path.exists(path)) else None


def _shopify_title(product):
    """A clean storefront title from the app's structured fields."""
    parts = [product.brand, getattr(product, 'model', ''), product.name, getattr(product, 'spec', '')]
    text = ' '.join(str(p).strip() for p in parts if p and str(p).strip())
    text = text.title()
    # Restore perfume tokens that Title() mangles.
    text = re.sub(r'\b(Edp|Edt|Edc)\b', lambda m: m.group(1).upper(), text)
    text = re.sub(r'(\d)\s*Ml\b', r'\1ml', text)  # "100Ml" -> "100ml"
    return text or product.display_name


def _shopify_tags(product):
    category = getattr(product.category, 'name', '') if product.category_id else ''
    gender_tag = getattr(product, 'gender_shopify_tag', '')
    raw = [product.brand, getattr(product, 'model', ''), category, getattr(product, 'spec', ''), gender_tag, 'Scentory']
    seen, tags = set(), []
    for tag in raw:
        tag = (tag or '').strip()
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return tags


def _truncate(text, limit):
    text = (text or '').strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _attach_image(product, client, match, *, overwrite, dry_run):
    """Attach the product's photo to an already-found Shopify product."""
    image_path = _local_image_path(product)
    if not image_path:
        return SKIP_NO_IMAGE, 'no local image file'
    if match['has_image'] and not overwrite:
        return SKIP_HAS_IMAGE, match['title']
    if dry_run:
        return WOULD_UPLOAD, match['title']
    resource_url = client.stage_and_upload_image(image_path)
    client.attach_image(match['id'], resource_url, alt=product.display_name)
    return UPLOADED, match['title']


def sync_product_image(product, client=None, *, overwrite=False, dry_run=False):
    """Push ``product``'s photo to its matching *existing* Shopify product."""
    client = client or ShopifyClient()
    barcode = (product.barcode or '').strip()
    if not barcode:
        return SKIP_NO_BARCODE, 'product has no barcode'
    try:
        match = client.find_product_by_sku(barcode)
        if not match:
            return SKIP_NOT_IN_SHOPIFY, f'no Shopify product with sku {barcode}'
        return _attach_image(product, client, match, overwrite=overwrite, dry_run=dry_run)
    except ShopifyError as exc:
        return ERROR, str(exc)


def create_product_in_shopify(product, client=None, *, status='DRAFT', dry_run=False):
    """Create a missing product in Shopify (variant / inventory / SEO / image)."""
    client = client or ShopifyClient()
    barcode = (product.barcode or '').strip()
    if not barcode:
        return SKIP_NO_BARCODE, 'product has no barcode'

    title = _shopify_title(product)
    if dry_run:
        return WOULD_CREATE, title

    try:
        location_id = client.get_location_id()
        price = f'{product.default_price:.2f}' if product.default_price is not None else '0.00'
        try:
            qty = int(product.total_stock() or 0)
        except Exception:
            qty = 0
        cost = None
        try:
            cost = product.current_fifo_cost_price()
        except Exception:
            cost = None

        inventory_item = {'tracked': True, 'sku': barcode}
        if cost is not None:
            inventory_item['cost'] = f'{cost:.2f}'
        variant = {
            'optionValues': [{'optionName': 'Title', 'name': 'Default Title'}],
            'price': price,
            'sku': barcode,
            'barcode': barcode,
            'inventoryItem': inventory_item,
            'inventoryQuantities': [{'locationId': location_id, 'name': 'available', 'quantity': qty}],
        }

        description = (product.description or '').strip() or title
        product_type = (getattr(product.category, 'name', '') if product.category_id else '') or 'Perfume'
        product_input = {
            'title': title,
            'descriptionHtml': description,
            'vendor': (product.brand or '').strip(),
            'productType': product_type,
            'tags': _shopify_tags(product),
            'status': status,
            'seo': {'title': _truncate(title, 70), 'description': _truncate(description, 320)},
            'productOptions': [{'name': 'Title', 'values': [{'name': 'Default Title'}]}],
            'variants': [variant],
        }

        image_path = _local_image_path(product)
        if image_path:
            resource_url = client.stage_and_upload_image(image_path)
            product_input['files'] = [{'originalSource': resource_url, 'contentType': 'IMAGE', 'alt': title}]

        gid = client.product_set(product_input)
        return CREATED, f'{title} ({gid})'
    except ShopifyError as exc:
        return ERROR, str(exc)


def sync_product(product, client=None, *, create_missing=False, overwrite_image=False,
                 dry_run=False, status='DRAFT'):
    """Find the Shopify product by SKU; attach its image, or create it if missing
    (when ``create_missing``)."""
    client = client or ShopifyClient()
    barcode = (product.barcode or '').strip()
    if not barcode:
        return SKIP_NO_BARCODE, 'product has no barcode'
    try:
        match = client.find_product_by_sku(barcode)
    except ShopifyError as exc:
        return ERROR, str(exc)

    if match:
        try:
            return _attach_image(product, client, match, overwrite=overwrite_image, dry_run=dry_run)
        except ShopifyError as exc:
            return ERROR, str(exc)
    if not create_missing:
        return SKIP_NOT_IN_SHOPIFY, f'no Shopify product with sku {barcode}'
    return create_product_in_shopify(product, client, status=status, dry_run=dry_run)
