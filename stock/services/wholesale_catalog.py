"""What a wholesale customer may see of the catalogue.

Written for the customer portal (docs/superpowers/specs, wholesale portal
architecture, sections 6-7) and used by the internal sample until that exists.
A row is a whitelist: no unit counts, no cost, nothing a page could leak by
accident. Nothing here may import stock.views.
"""
from collections import Counter

from django.db.models import Sum

from ..models import Product, ProductType, Sale
from .availability import LOW_STOCK_BELOW
from .names import customer_catalog_case
from .product_contents import content_label
from .product_search import product_search_q
from .store_stock import active_stores, full_price_units, stock_by_store

FIRST_BRAND = 'lattafa'
PAGE_SIZE = 24
STATE_ORDER = {'in-stock': 0, 'low-stock': 1, 'out-stock': 2}


def brand_sort_key(name, count):
    """Lattafa, the shop's main brand, then the biggest brands first."""
    name = (name or '').strip().lower()
    return (name != FIRST_BRAND, -count, name)


def units_sold(product_ids):
    """``{product_id: units}`` sold so far. Decides an order, never printed."""
    return dict(Sale.objects.filter(product_id__in=product_ids).values_list('product')
                .annotate(units=Sum('quantity')).order_by())


def sellable_units(product_ids):
    """``{product_id: units}`` from active shops, each keeping its own sample,
    plus stock not yet in any shop. Never shown: it decides the state."""
    shops = active_stores()
    units = {}
    for pk, by_shop in stock_by_store(product_ids).items():
        units[pk] = by_shop.get(None, 0) + sum(full_price_units(shop, by_shop.get(shop.pk, 0)) for shop in shops)
    return units


def availability(units):
    """``(state, label)``: the only stock fact a customer ever gets."""
    if units >= LOW_STOCK_BELOW:
        return 'in-stock', 'Dispon\u00edvel'
    if units > 0:
        return 'low-stock', 'Stock reduzido'
    return 'out-stock', 'Indispon\u00edvel'


def _image_url(product):
    for image in product.images.all():
        if image.image:
            try:
                return image.image.url
            except ValueError:
                continue
    return ''


def _row(product, units):
    kind = product.type_info
    state, state_label = availability(units)
    price = product.wholesale_price
    contents = product.contents.all() if product.lists_contents else ()
    return {
        'pk': product.pk,
        'ean': product.barcode,
        'brand': customer_catalog_case(product.brand),
        'logo_brand': product.brand_master,
        'title': customer_catalog_case(product.title_parts(include_brand=False)) or product.barcode,
        'kind': kind.customer_label or kind.name,
        'gender': Product.GENDER_SHOPIFY_TAGS.get(product.gender, ''),
        'price': price if price and price > 0 else None,
        'state': state,
        'state_label': state_label,
        'image_url': _image_url(product),
        'contents': [content_label(row) for row in contents],
        'description': product.composed_description(include_inspiration=False),
    }


def _line_key(product):
    return (product.brand or '').lower(), product.product_type_id, (product.model or '').strip().lower()


def catalog_rows(query='', brand='', kind='', gender='', show_all=False):
    """The list, in order: available first, then by brand and type, then each
    type's best-selling lines first."""
    products = (Product.objects.select_related('brand_master')
                .prefetch_related('images', 'contents__linked', 'fragrance_families'))
    if query:
        products = products.filter(product_search_q(query))
    if brand:
        products = products.filter(brand__iexact=brand)
    if kind:
        products = products.filter(product_type=kind)
    if gender:
        products = products.filter(gender=gender)
    products = list(products)

    units = sellable_units([p.pk for p in products])
    rows = [(p, _row(p, units.get(p.pk, 0))) for p in products]
    if not show_all:
        rows = [(p, row) for p, row in rows if row['state'] != 'out-stock']

    sizes = Counter((row['brand'] or '').lower() for _, row in rows)
    sold = units_sold([p.pk for p, _ in rows])
    lines = Counter()
    for product, row in rows:
        lines[_line_key(product)] += sold.get(product.pk, 0)

    def order(pair):
        product, row = pair
        return (STATE_ORDER[row['state']], brand_sort_key(row['brand'], sizes[(row['brand'] or '').lower()]),
                product.type_info.sort_order, not (product.model or '').strip(), -lines[_line_key(product)],
                (product.model or '').lower(), row['title'].lower())

    return [row for _, row in sorted(rows, key=order)]


def catalog_options():
    """What the filters can offer: only what the catalogue really has."""
    products = Product.objects.values_list('brand', 'product_type', 'gender').distinct()
    brands, codes, genders = set(), set(), set()
    for name, code, gender in products:
        if (name or '').strip():
            brands.add(customer_catalog_case(name))
        codes.add(code)
        if gender:
            genders.add(gender)
    types = [{'value': t.code, 'label': t.customer_label or t.name}
             for t in ProductType.all_cached() if t.code in codes]
    gender_rows = [{'value': value, 'label': label} for value, label in Product.GENDER_SHOPIFY_TAGS.items()
                   if value in genders]
    return {'brands': sorted(brands, key=str.lower), 'types': types, 'genders': gender_rows}
