"""What a wholesale customer may see of the catalogue.

Written for the customer portal (docs/superpowers/specs, wholesale portal
architecture, sections 6-7) and used by the internal sample until that exists.
A row is a whitelist: no unit counts, no cost, nothing a page could leak by
accident. Nothing here may import stock.views.
"""
from collections import Counter
from decimal import Decimal

from django.db.models import Sum

from ..models import CartLine, Product, ProductType, Sale
from .availability import low_stock_below, product_ids_on_order
from .names import customer_catalog_case
from .product_contents import content_label
from .product_search import product_search_q
from .store_stock import active_stores, full_price_units, stock_by_store
from .wholesale_discount import discount_applies

FIRST_BRAND = 'lattafa'
PAGE_SIZE = 24
STATE_ORDER = {'in-stock': 0, 'low-stock': 1, 'incoming': 2, 'out-stock': 3}


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


def availability(units, on_way=False):
    """``(state, label)``: the only stock fact a customer ever gets. Only
    'out-stock' cannot be ordered: what is on the way can."""
    if units >= low_stock_below():
        return 'in-stock', 'Dispon\u00edvel'
    if units > 0:
        return 'low-stock', 'Mais a caminho' if on_way else 'Stock reduzido'
    if on_way:
        return 'incoming', 'Brevemente em stock'
    return 'out-stock', 'Indispon\u00edvel'


def customer_price(product, customer=None):
    """``(price, discount)``: the wholesale price less the discount the shop set for
    this customer, where the discount applies. None for a price that needs a
    person: no wholesale price, or a discount that would leave nothing."""
    base = product.wholesale_price
    if not base or base <= 0:
        return None, Decimal('0')
    discount = Decimal('0')
    # portal_discount: worked out once per request (wholesale_views._customer).
    agreed = getattr(customer, 'portal_discount', customer.wholesale_discount) if customer else None
    if agreed and agreed > 0 and discount_applies(product):
        discount = agreed
    price = base - discount
    return (price if price > 0 else None), discount


def display_title(product):
    return customer_catalog_case(product.title_parts(include_brand=False)) or product.barcode


def image_url(product):
    for image in product.images.all():
        if image.image:
            try:
                return image.image.url
            except ValueError:
                continue
    return ''


def made_to_order(product):
    return product.type_info.picks_perfume


def pick_mode(product):
    """'perfume': a sample, from any perfume in stock. 'list': a mix, from its own
    names. '': nothing to choose, and a coffret is what it is."""
    if made_to_order(product):
        return 'perfume'
    if product.is_mix and len(product.contents.all()) > 1:
        return 'list'
    return ''


def pick_options(product, picks=()):
    """The rows of the choosing panel, holding what is already chosen."""
    if pick_mode(product) != 'list':
        return [{'id': pick['id'], 'name': pick['name'], 'quantity': pick['quantity']} for pick in picks]
    chosen = {pick['id']: pick['quantity'] for pick in picks}
    return [{'id': row.pk, 'name': content_label(row), 'quantity': chosen.get(row.pk, 0)}
            for row in product.contents.all()]


def _row(product, units, requested=False, customer=None, in_cart=0, on_way=False):
    kind = product.type_info
    state, state_label = availability(low_stock_below() if made_to_order(product) else units, on_way)
    price = customer_price(product, customer)[0]
    contents = product.contents.all() if product.lists_contents else ()
    return {
        'pk': product.pk,
        'ean': product.barcode,
        'brand': customer_catalog_case(product.brand),
        'logo_brand': product.brand_master,
        'title': display_title(product),
        'kind': kind.customer_label or kind.name,
        'type_code': product.product_type_id,
        'volume_ml': product.volume_ml,
        'strength': product.strength_short if hasattr(product, 'strength_short') else (
            getattr(product.concentration, 'short', '') if product.concentration_id else ''),
        'gender': Product.GENDER_SHOPIFY_TAGS.get(product.gender, ''),
        'price': price,
        'base_price': product.wholesale_price,
        'in_cart': in_cart,
        'state': state,
        'state_label': state_label,
        'image_url': image_url(product),
        'contents': [content_label(row) for row in contents],
        'description': product.composed_description(include_inspiration=False),
        'requested': requested,
        'pick_mode': pick_mode(product),
        'pick_options': pick_options(product),
    }


def _line_key(product):
    return (product.brand or '').lower(), product.product_type_id, (product.model or '').strip().lower()


def catalog_rows(query='', brand='', kind='', gender='', show_all=False, viewer=None, customer=None):
    """The list, in order: sold out last, then by brand and type, each type's
    best-selling lines first. A brand stays together whatever its stock levels."""
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
    # Local import: restock_requests reads availability() from this module.
    from .restock_requests import requested_ids
    asked = requested_ids(viewer, [p.pk for p in products]) if viewer else set()
    carted = dict(CartLine.objects.filter(user=viewer).values_list('product', 'quantity')) if viewer else {}
    coming = product_ids_on_order()
    rows = [(p, _row(p, units.get(p.pk, 0), p.pk in asked, customer, carted.get(p.pk, 0), p.pk in coming))
            for p in products]
    if not show_all:
        rows = [(p, row) for p, row in rows if row['state'] != 'out-stock']

    sizes = Counter((row['brand'] or '').lower() for _, row in rows)
    sold = units_sold([p.pk for p, _ in rows])
    lines = Counter()
    for product, row in rows:
        lines[_line_key(product)] += sold.get(product.pk, 0)

    def order(pair):
        product, row = pair
        return (row['state'] == 'out-stock', brand_sort_key(row['brand'], sizes[(row['brand'] or '').lower()]),
                product.type_info.sort_order, not (product.model or '').strip(), -lines[_line_key(product)],
                (product.model or '').lower(), STATE_ORDER[row['state']], row['title'].lower())

    return [row for _, row in sorted(rows, key=order)]
