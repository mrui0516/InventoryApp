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
from decimal import ROUND_HALF_UP, Decimal

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
DECANT_AVAILABLE = 10       # 10ml/5ml available quantity while any bottle exists
DECANT_SUFFIXES = ('-10ML', '-5ML')

# Only a full bottle is worth decanting: a 30ml costs little, and taking two of
# them as samples would empty the stock. Matches what is already on the store,
# where every product carrying decants is a 100ml.
DECANTABLE_VOLUME_ML = 100

# Decant prices as a share of the full bottle, rounded to 5 cents. Taken from
# the prices already on the storefront (10ml sits at 17-18%, 5ml at 11-12%) so
# newly created products land beside the existing ones rather than looking
# arbitrary. Shopify keeps whatever is set afterwards - the app only writes the
# full-bottle price - so these are a sensible start, not a policy.
DECANT_PRICE_RATIO = {'-10ML': Decimal('0.175'), '-5ML': Decimal('0.115')}
DECANT_PRICE_STEP = Decimal('0.05')

# The storefront is Portuguese and its size option is called Tamanho; a new
# product using a different option name would not sit with the others.
SIZE_OPTION_NAME = 'Tamanho'
ARABIC_PERFUME_TAG = 'Perfume Árabe'
STORE_TAG = 'Scentory'
SEO_SUFFIX = 'Scentory — envio para Portugal'
GENDER_PHRASE = {'Homem': 'Perfume masculino',
                 'Mulher': 'Perfume feminino',
                 'Unissexo': 'Perfume unissexo'}


def is_perfume(product):
    return bool(product.category_id) and 'perfum' in (
        getattr(product.category, 'name', '') or '').lower()


def volume_label(product):
    """"100ml" from the structured volume, falling back to the old free text."""
    if getattr(product, 'volume_ml', None):
        return f'{product.volume_ml}ml'
    return (getattr(product, 'spec', '') or '').strip()


def is_decantable(product):
    """A full bottle of perfume, which is what decants are poured from."""
    return is_perfume(product) and getattr(product, 'volume_ml', None) == DECANTABLE_VOLUME_ML


def decant_price(full_price, suffix):
    """A starting price for one decant size, rounded to the nearest 5 cents."""
    if full_price is None:
        return Decimal('0.00')
    ratio = DECANT_PRICE_RATIO.get(suffix)
    if ratio is None:
        return Decimal('0.00')
    raw = Decimal(full_price) * ratio
    steps = (raw / DECANT_PRICE_STEP).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return (steps * DECANT_PRICE_STEP).quantize(Decimal('0.01'))


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
                # Switching a variant to tracked makes Shopify (re)initialise its
                # inventory level, which starts at 0 — an untracked variant never
                # had a real level, so its reported quantity means nothing. Always
                # write the quantity after a policy change, even when the reported
                # number already matched, or the variant is left stranded at 0.
                if needs_policy:
                    needs_qty = True
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


def _pretty(text):
    """Title case, keeping the perfume abbreviations upright.

    The app stores brands and series shouting ("RAYHAAN"); the storefront
    writes them as words, and a tag that differs only in case is a second,
    useless tag.
    """
    text = (text or '').strip()
    if not text:
        return ''
    text = text.title()
    return re.sub(r'\b(Edp|Edt|Edc)\b', lambda m: m.group(1).upper(), text)


def _shopify_title(product):
    """A clean storefront title from the app's structured fields.

    Brand, series, name, strength, size - "Lattafa Khamrah Waha EDP 100ml".

    Two things this has to handle. The size lives in ``volume_ml`` now, not in
    the free-text spec, so reading only the spec left every recently entered
    product without its size. And a series that repeats the name produced
    "Rayhaan Pharaoh Pharaoh", which is what the shop noticed.
    """
    brand = (product.brand or '').strip()
    series = (getattr(product, 'model', '') or '').strip()
    name = (product.name or '').strip()
    if series and name and series.lower() == name.lower():
        series = ''                      # "Pharaoh Pharaoh" -> "Pharaoh"

    text = ' '.join(p for p in [brand, series, name] if p).title()
    # Restore perfume tokens that Title() mangles.
    text = re.sub(r'\b(Edp|Edt|Edc)\b', lambda m: m.group(1).upper(), text)

    strength = getattr(getattr(product, 'concentration', None), 'short', '') or ''
    if strength and not re.search(r'\b' + re.escape(strength) + r'\b', text, re.IGNORECASE):
        text = (text + ' ' + strength).strip()

    size = volume_label(product)
    if size and size.lower() not in text.lower():
        text = (text + ' ' + size).strip()
    text = re.sub(r'(\d)\s*Ml\b', r'\1ml', text)  # "100Ml" -> "100ml"
    return text or product.display_name


def _shopify_tags(product):
    """Tags in the shape the storefront already uses.

    An existing product carries its size, series, brand, the Portuguese gender
    phrase, "Perfume Arabe", "Scentory", the plain gender word and its name.
    The collections filter on these, so a product missing them simply never
    turns up in them.
    """
    category = getattr(product.category, 'name', '') if product.category_id else ''
    gender_tag = (getattr(product, 'gender_shopify_tag', '') or '').strip()

    raw = [volume_label(product), _pretty(getattr(product, 'model', '')),
           _pretty(product.brand)]
    if gender_tag:
        raw.append(GENDER_PHRASE.get(gender_tag, ''))
    raw.append(ARABIC_PERFUME_TAG if is_perfume(product) else category)
    raw += [STORE_TAG, gender_tag, _pretty(product.name)]

    seen, tags = set(), []
    for tag in raw:
        tag = (tag or '').strip()
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return tags


def _shopify_seo(product, title):
    """Search title and description, following the storefront's own pattern.

    The title says which range it belongs to; the description leads with the
    product's own words and closes with the shop and where it ships, which is
    what somebody in Portugal is actually searching for.
    """
    suffix = ARABIC_PERFUME_TAG if is_perfume(product) else STORE_TAG
    seo_title = _truncate(title + ' | ' + suffix, 70)

    body = ' '.join((product.description or '').split())
    if not body:
        # No description yet: say what it is rather than leaving SEO blank.
        body = ' '.join(x for x in [title, volume_label(product)] if x)
    tail = ' | ' + SEO_SUFFIX
    return {'title': seo_title,
            'description': _truncate(body, 320 - len(tail)) + tail}


def _truncate(text, limit):
    text = (text or '').strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _shopify_description_html(product):
    """The composed description as HTML, formatting preserved.

    Composed, not raw: the family, the three note layers and the reference are
    already fields on the product, so they are written under the shop's own
    paragraph rather than copied into it by hand - a copy that goes stale the
    moment one of those fields changes.
    """
    text = (product.composed_description(include_inspiration=False) or '').strip()
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
    """Create a missing product in Shopify, in the shape the storefront uses.

    A full bottle of perfume is created with its three sizes - 100ml, 10ml and
    5ml - under the Tamanho option, because that is how every decanted product
    already on the store is built, and a product created with a single
    "Default Title" variant cannot be given decants later without rebuilding
    it. Quantities follow the same reserve rule as the update path: two bottles
    are held back as samples, and the decants show as available while any
    bottle remains.
    """
    client = client or ShopifyClient()
    barcode = (product.barcode or '').strip()
    if not barcode:
        return SKIP_NO_BARCODE, 'product has no barcode'

    title = _shopify_title(product)
    if dry_run:
        return WOULD_CREATE, title

    try:
        location_id = client.get_location_id()
        full_price = product.default_price
        try:
            on_hand = int(product.total_stock() or 0)
        except Exception:
            on_hand = 0
        try:
            cost = product.current_fifo_cost_price()
        except Exception:
            cost = None

        decanted = is_decantable(product)
        size_label = volume_label(product) or 'Default Title'
        skus = [barcode] + ([barcode + s for s in DECANT_SUFFIXES] if decanted else [])
        targets = _inventory_targets(barcode, on_hand, set(skus))

        variants = []
        for sku in skus:
            suffix = sku[len(barcode):]
            label = size_label if not suffix else suffix.lstrip('-').lower()
            price = (_price_str(full_price) if not suffix
                     else _price_str(decant_price(full_price, suffix)))
            inventory_item = {'tracked': True, 'sku': sku}
            # Cost is what a full bottle cost us; a decant is a share of one,
            # so claiming the same cost would make every decant look like a loss.
            if cost is not None and not suffix:
                inventory_item['cost'] = f'{cost:.2f}'
            variants.append({
                'optionValues': [{'optionName': SIZE_OPTION_NAME, 'name': label}],
                'price': price,
                'sku': sku,
                'barcode': barcode,
                'inventoryItem': inventory_item,
                'inventoryQuantities': [{'locationId': location_id, 'name': 'available',
                                         'quantity': int(targets.get(sku, 0))}],
            })

        description_html = _shopify_description_html(product) or title
        # Match what is already on the store: every listing there is vendor
        # "Lattafa" and productType "Perfume". The app stores brands shouting
        # and its category is plural, and a vendor that differs only in case
        # drops the new product out of that brand's collection.
        product_type = 'Perfume' if is_perfume(product) else (
            (getattr(product.category, 'name', '') if product.category_id else '')
            or 'Perfume')
        product_input = {
            'title': title,
            'descriptionHtml': description_html,
            'vendor': _pretty(product.brand),
            'productType': product_type,
            'tags': _shopify_tags(product),
            'status': status,
            'seo': _shopify_seo(product, title),
            'productOptions': [{
                'name': SIZE_OPTION_NAME,
                'values': [{'name': v['optionValues'][0]['name']} for v in variants],
            }],
            'variants': variants,
        }

        # Perfumes get the "Eaux de Parfum" standard category.
        if is_perfume(product):
            product_input['category'] = EAU_DE_PARFUM_TAXONOMY_GID

        image_path = _local_image_path(product)
        if image_path:
            resource_url = client.stage_and_upload_image(image_path)
            product_input['files'] = [{'originalSource': resource_url, 'contentType': 'IMAGE', 'alt': title}]

        gid = client.product_set(product_input)
        shape = f'{len(variants)} variant(s)'
        return CREATED, f'{title} ({shape}, {gid})'
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


def shopify_syncable(queryset):
    """Narrow a Product queryset to what belongs on the storefront.

    Which categories go online is a shop decision, not a code decision:
    Category.sync_to_shopify carries it. Products with no category at all are
    included, so nothing silently stops syncing because a category was cleared.
    """
    from django.db.models import Q
    return queryset.filter(Q(category__isnull=True) | Q(category__sync_to_shopify=True))
