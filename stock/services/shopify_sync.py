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
from decimal import Decimal

from django.utils.html import linebreaks

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

# Standard Product Taxonomy node for "Eaux de Parfum"
# (Health & Beauty > Personal Care > Cosmetics > Perfumes & Colognes > Eaux de Parfum).
EAU_DE_PARFUM_TAXONOMY_GID = 'gid://shopify/TaxonomyCategory/hb-3-2-8-3'

# Price/inventory push result codes.
INV_NO_BARCODE = 'inv_no_barcode'
INV_NOT_IN_SHOPIFY = 'inv_not_in_shopify'
INV_UNCHANGED = 'inv_unchanged'
INV_WOULD_UPDATE = 'inv_would_update'
INV_UPDATED = 'inv_updated'
INV_ERROR = 'inv_error'


def _price_str(value):
    return f'{Decimal(value):.2f}' if value is not None else '0.00'


# Decant rules. Shopify variant SKUs: 100ml = <barcode>, 10ml = <barcode>-10ML,
# 5ml = <barcode>-5ML. When a product has decant variants, the last few full
# bottles are reserved for decanting (100ml hidden) while the 10ml/5ml stay
# available as long as any bottle exists.
DECANT_RESERVE = 2          # full bottles kept back; 100ml shows on-hand minus this
DECANT_AVAILABLE = 99       # 10ml/5ml available quantity while any bottle exists
DECANT_SUFFIXES = ('-10ML', '-5ML')


def _variant_label(barcode, sku):
    return '100ml' if sku == barcode else sku[len(barcode):].lstrip('-').lower()


def _inventory_targets(barcode, on_hand, present_skus):
    """``{sku: target_available}`` for a product's variants given ``on_hand`` full
    bottles. The last ``DECANT_RESERVE`` bottles are the shop samples: full-bottle
    (100ml) sales only start beyond them, but decants can be made from a sample.
    So: 100ml = max(on_hand - reserve, 0); 10ml/5ml = DECANT_AVAILABLE while any
    stock exists (on_hand >= 1, i.e. a sample is still there) else 0 — a fully
    empty product (0) can't be decanted and shows out of stock. Without decants:
    100ml = on_hand. Only includes SKUs that exist in ``present_skus``."""
    has_decant = any((barcode + s) in present_skus for s in DECANT_SUFFIXES)
    targets = {}
    if has_decant:
        if barcode in present_skus:
            targets[barcode] = max(on_hand - DECANT_RESERVE, 0)
        for suffix in DECANT_SUFFIXES:
            sku = barcode + suffix
            if sku in present_skus:
                targets[sku] = DECANT_AVAILABLE if on_hand >= 1 else 0
    elif barcode in present_skus:
        targets[barcode] = on_hand
    return targets


def sync_product_price_inventory(product, client=None, *, do_price=True, do_inventory=True,
                                 dry_run=False, shop_variants=None, location_id=None):
    """Push the app's price and/or on-hand to Shopify (app authoritative).

    Price goes to the 100ml variant (sku == barcode). Inventory is decant-aware:
    100ml, 10ml and 5ml variants are set per the reserve rules above. Returns
    ``(code, detail)``.

    A bulk caller may pass ``shop_variants`` ({sku: rec} for this product's
    variants, from ``all_variants_by_sku``) and ``location_id`` to avoid per-
    product lookups; the real-time signal passes neither, so the variants are
    looked up on the fly."""
    client = client or ShopifyClient()
    barcode = (product.barcode or '').strip()
    if not barcode:
        return INV_NO_BARCODE, 'product has no barcode'
    try:
        if shop_variants is None:
            shop_variants = {}
            for sku in (barcode, barcode + '-10ML', barcode + '-5ML'):
                rec = client.find_variant_by_sku(sku)
                if rec:
                    shop_variants[sku] = rec
        if not shop_variants:
            return INV_NOT_IN_SHOPIFY, f'no Shopify variant with sku {barcode}'

        parts, writes = [], []

        if do_price:
            main = shop_variants.get(barcode)
            if main is not None and main.get('price') is not None:
                app_price = _price_str(product.default_price)
                if Decimal(main['price']) != Decimal(app_price):
                    parts.append(f"price {main.get('price')}->{app_price}")
                    writes.append(('price', main['product_id'], main['variant_id'], app_price))

        if do_inventory:
            try:
                on_hand = int(product.total_stock() or 0)
            except Exception:
                on_hand = 0
            targets = _inventory_targets(barcode, on_hand, set(shop_variants))
            for sku, target in targets.items():
                rec = shop_variants.get(sku)
                if rec is None:
                    continue
                # A variant must track inventory and refuse overselling, or its
                # quantity is ignored and it stays buyable (the decant bug).
                needs_policy = (rec.get('tracked') is False) or (rec.get('policy') not in (None, 'DENY'))
                needs_qty = rec.get('available') != target
                if not (needs_policy or needs_qty):
                    continue
                bits = (['track+deny'] if needs_policy else []) + \
                       ([f"qty {rec.get('available')}->{target}"] if needs_qty else [])
                parts.append(f"{_variant_label(barcode, sku)} {' '.join(bits)}")
                writes.append(('inv', rec['product_id'], rec['variant_id'],
                               rec['inventory_item_id'], target, needs_policy, needs_qty))

        if not writes:
            return INV_UNCHANGED, ''
        detail = ', '.join(parts)
        if dry_run:
            return INV_WOULD_UPDATE, detail

        loc = None
        for w in writes:
            if w[0] == 'price':
                client.update_variant_price(w[1], w[2], w[3])
            else:
                _, product_id, variant_id, inv_item_id, target, needs_policy, needs_qty = w
                if needs_policy:
                    client.set_variant_stocked(product_id, variant_id)
                if needs_qty:
                    loc = loc or location_id or client.get_location_id()
                    client.set_inventory_available(inv_item_id, loc, target)
        return INV_UPDATED, detail
    except ShopifyError as exc:
        return INV_ERROR, str(exc)



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


def _shopify_description_html(product):
    """The product description as HTML, preserving the saved formatting (blank
    lines -> paragraphs, single newlines -> <br>) instead of collapsing it into
    one run of text. Content is escaped by ``linebreaks``."""
    text = (product.description or '').strip()
    return linebreaks(text) if text else ''


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
        description_html = _shopify_description_html(product) or title
        product_type = (getattr(product.category, 'name', '') if product.category_id else '') or 'Perfume'
        product_input = {
            'title': title,
            'descriptionHtml': description_html,
            'vendor': (product.brand or '').strip(),
            'productType': product_type,
            'tags': _shopify_tags(product),
            'status': status,
            'seo': {'title': _truncate(title, 70), 'description': _truncate(description, 320)},
            'productOptions': [{'name': 'Title', 'values': [{'name': 'Default Title'}]}],
            'variants': [variant],
        }

        # Perfumes get the "Eaux de Parfum" standard category.
        if product.category_id and 'perfum' in (getattr(product.category, 'name', '') or '').lower():
            product_input['category'] = EAU_DE_PARFUM_TAXONOMY_GID

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
            if not dry_run:
                desc = _shopify_description_html(product)
                if desc:
                    client.update_product_description(match['id'], desc)
            return _attach_image(product, client, match, overwrite=overwrite_image, dry_run=dry_run)
        except ShopifyError as exc:
            return ERROR, str(exc)
    if not create_missing:
        return SKIP_NOT_IN_SHOPIFY, f'no Shopify product with sku {barcode}'
    return create_product_in_shopify(product, client, status=status, dry_run=dry_run)
