"""The cart and the order request. Prices are worked out here every time: the page
sends a product and a quantity, never an amount."""
from django.db import transaction

from ..models import CartLine, OrderRequest, OrderRequestLine, Product
from .wholesale_benefits import MAX_QUANTITY, MINIMUM, build_quote, eur, policy_snapshot
from .wholesale_catalog import customer_price, display_title, image_url, sellable_units


def _clean_quantity(quantity):
    if not isinstance(quantity, int) or quantity < 1 or quantity > MAX_QUANTITY:
        raise ValueError(f'Indique uma quantidade entre 1 e {MAX_QUANTITY}.')
    return quantity


def add_to_cart(user, product, quantity, customer=None):
    """Any quantity is taken: stock is confirmed by hand, and a limit here would
    tell a customer how many are on the shelf."""
    _clean_quantity(quantity)
    if customer_price(product, customer)[0] is None:
        raise ValueError('Este produto precisa de confirma\u00e7\u00e3o de pre\u00e7o.')
    if sellable_units([product.pk]).get(product.pk, 0) <= 0:
        raise ValueError('Este produto est\u00e1 indispon\u00edvel.')
    line = CartLine.objects.filter(user=user, product=product).first()
    total = quantity + (line.quantity if line else 0)
    _clean_quantity(total)
    CartLine.objects.update_or_create(user=user, product=product, defaults={'quantity': total})


def set_quantity(user, product_id, quantity):
    if quantity <= 0:
        CartLine.objects.filter(user=user, product_id=product_id).delete()
        return
    _clean_quantity(quantity)
    CartLine.objects.filter(user=user, product_id=product_id).update(quantity=quantity)


def cart_units(user):
    return sum(CartLine.objects.filter(user=user, product__archived_at__isnull=True).values_list('quantity', flat=True))


def _lines(user, customer):
    rows = list(CartLine.objects.filter(user=user, product__archived_at__isnull=True)
                .select_related('product').prefetch_related('product__images').order_by('added_at', 'id'))
    units = sellable_units([row.product_id for row in rows])
    lines = []
    for row in rows:
        product = row.product
        price, discount = customer_price(product, customer)
        available = units.get(product.pk, 0) > 0
        lines.append({
            'product_id': product.pk, 'name': f'{product.brand} {display_title(product)}'.strip(),
            'brand': product.brand, 'title': display_title(product), 'barcode': product.barcode,
            'image_url': image_url(product), 'quantity': row.quantity,
            'base_price': product.wholesale_price or price or 0, 'customer_discount': discount,
            'catalog_price': price if price is not None else 0,
            'ok': available and price is not None,
            'problem': '' if available and price is not None else ('Indispon\u00edvel' if not available else 'Sob consulta'),
        })
    return lines


def cart_quote(user, customer, destination):
    return build_quote(_lines(user, customer), destination)


def submit_request(user, customer, data, fingerprint):
    """Turn the cart into a request, or raise ValueError saying why it cannot be.
    The prices are worked out again here, whatever the page was showing."""
    with transaction.atomic():
        quote = cart_quote(user, customer, data)
        if not quote['lines']:
            raise ValueError('O carrinho est\u00e1 vazio.')
        if quote['fingerprint'] != fingerprint:
            raise ValueError('Os pre\u00e7os ou as quantidades mudaram. Confirme o carrinho de novo.')
        if quote['short_of_minimum']:
            raise ValueError(f'O pedido m\u00ednimo \u00e9 de {eur(MINIMUM)}\u20ac.')
        if not quote['can_submit']:
            raise ValueError('Retire do carrinho os produtos assinalados.')
        order = OrderRequest.objects.create(
            customer=customer, submitted_by=user,
            delivery=data.get('delivery') or 'delivery', business_name=data.get('business_name') or '',
            contact_name=data['contact_name'], phone=data['phone'], email=data.get('email') or '',
            address=data.get('address') or '', postal_code=data.get('postal_code') or '',
            city=data.get('city') or '', country=data.get('country') or '', note=data.get('note') or '',
            subtotal=quote['subtotal'], tier_discount=quote['tier_discount'], tier_total=quote['tier_total'],
            goods_total=quote['goods_total'], shipping_state=quote['shipping_state'], policy=policy_snapshot())
        OrderRequestLine.objects.bulk_create([OrderRequestLine(
            request=order, product_id=line['product_id'], name=line['name'][:250], barcode=line['barcode'],
            quantity=line['quantity'], base_price=line['base_price'], customer_discount=line['customer_discount'],
            catalog_price=line['catalog_price'], tier_discount=line['tier_discount'],
            final_price=line['final_price'], line_total=line['line_total']) for line in quote['lines']])
        CartLine.objects.filter(user=user).delete()
    return order
