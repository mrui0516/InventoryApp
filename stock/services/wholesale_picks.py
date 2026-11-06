"""What a mix or a sample is made of, as the customer chose it.

A pick is ``{'id', 'name', 'barcode', 'quantity'}``. ``id`` is a ProductContent for
a mix and a Product for a sample. Name and barcode are copied, so an order still
reads the same after a product changes. The stock never moves by a pick.
"""
from ..models import Product
from .names import customer_catalog_case
from .product_contents import content_label
from .product_search import product_search_q
from .wholesale_benefits import MAX_QUANTITY
from .wholesale_catalog import display_title, pick_mode, sellable_units, units_sold

NOTHING_CHOSEN = 'Escolha pelo menos uma fragrância.'
SEARCH_FROM = 2
SEARCH_LIMIT = 12


def perfume_name(product):
    return f'{customer_catalog_case(product.brand)} {display_title(product)}'.strip()


def _perfumes(**filters):
    return Product.objects.filter(product_type_id='perfume', is_mix=False, **filters)


def perfume_choices(query):
    """Perfumes a sample can be made from, best sellers first. The result holds no
    price and no stock: it goes straight to the page."""
    query = (query or '').strip()
    if len(query) < SEARCH_FROM:
        return []
    products = list(_perfumes().filter(product_search_q(query)))
    ids = [product.pk for product in products]
    units, sold = sellable_units(ids), units_sold(ids)
    products = [product for product in products if units.get(product.pk, 0) > 0]
    products.sort(key=lambda product: (-sold.get(product.pk, 0), perfume_name(product).lower()))
    return [{'id': product.pk, 'name': perfume_name(product), 'ean': product.barcode}
            for product in products[:SEARCH_LIMIT]]


def _wanted(raw):
    """``{id: quantity}`` from what the page sent, adding up repeated ids."""
    if not isinstance(raw, list):
        raise ValueError(NOTHING_CHOSEN)
    wanted = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(NOTHING_CHOSEN)
        pk, quantity = item.get('id'), item.get('quantity')
        if not isinstance(pk, int) or not isinstance(quantity, int) or quantity < 0 or quantity > MAX_QUANTITY:
            raise ValueError(NOTHING_CHOSEN)
        if quantity:
            wanted[pk] = wanted.get(pk, 0) + quantity
    if not wanted:
        raise ValueError(NOTHING_CHOSEN)
    return wanted


def _from_list(product, wanted):
    options = {content.pk: content for content in product.contents.select_related('linked')}
    if set(wanted) - set(options):
        raise ValueError('Essa fragrância não faz parte deste produto.')
    return [{'id': pk, 'name': content_label(options[pk]),
             'barcode': options[pk].linked.barcode if options[pk].linked else '', 'quantity': quantity}
            for pk, quantity in wanted.items()]


def _from_perfumes(wanted):
    perfumes = {product.pk: product for product in _perfumes(pk__in=wanted)}
    if set(wanted) - set(perfumes):
        raise ValueError('Escolha um perfume do catálogo.')
    units = sellable_units(list(wanted))
    for pk, product in perfumes.items():
        if units.get(pk, 0) <= 0:
            raise ValueError(f'Sem stock: {perfume_name(product)}')
    return [{'id': pk, 'name': perfume_name(perfumes[pk]), 'barcode': perfumes[pk].barcode, 'quantity': quantity}
            for pk, quantity in wanted.items()]


def clean_picks(product, raw):
    """The customer's choice as stored, or ValueError in the customer's words."""
    wanted = _wanted(raw)
    return _from_perfumes(wanted) if pick_mode(product) == 'perfume' else _from_list(product, wanted)


def merge_picks(current, new):
    """The same scent added twice is one row with both quantities."""
    merged = {pick['id']: dict(pick) for pick in current}
    for pick in new:
        if pick['id'] in merged:
            merged[pick['id']]['quantity'] += pick['quantity']
        else:
            merged[pick['id']] = dict(pick)
    return list(merged.values())


def sold_out_pick(mode, picks):
    """The name of a chosen perfume that has run out since, or ''. A mix is not
    checked: the shop confirms its scents by hand."""
    if mode != 'perfume':
        return ''
    units = sellable_units([pick['id'] for pick in picks])
    return next((pick['name'] for pick in picks if units.get(pick['id'], 0) <= 0), '')
