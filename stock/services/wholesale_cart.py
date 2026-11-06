"""The cart and the order request. Prices are worked out here every time: the page
sends a product and a quantity, never an amount."""
from django.db import transaction

from ..models import CartLine, OrderRequest, OrderRequestLine
from .availability import product_ids_on_order
from .wholesale_benefits import MAX_QUANTITY, MINIMUM, build_quote, eur, policy_snapshot
from .wholesale_catalog import (customer_price, display_title, image_url, made_to_order, pick_mode, pick_options,
                                sellable_units)
from .wholesale_orders import save_to_pedido
from .wholesale_picks import clean_picks, merge_picks, sold_out_pick


def _clean_quantity(quantity):
    if not isinstance(quantity, int) or quantity < 1 or quantity > MAX_QUANTITY:
        raise ValueError(f'Indique uma quantidade entre 1 e {MAX_QUANTITY}.')
    return quantity


def add_to_cart(user, product, quantity, customer=None, picks=None, replace=False):
    """Any quantity is taken: stock is confirmed by hand, and a limit here would
    tell a customer how many are on the shelf. Where the customer chooses, the
    choice is the quantity, whatever else was sent."""
    chosen = clean_picks(product, picks) if pick_mode(product) else []
    if chosen:
        quantity = sum(pick['quantity'] for pick in chosen)
    _clean_quantity(quantity)
    if customer_price(product, customer)[0] is None:
        raise ValueError('Este produto precisa de confirma\u00e7\u00e3o de pre\u00e7o.')
    if not (made_to_order(product) or sellable_units([product.pk]).get(product.pk, 0) > 0
            or product.pk in product_ids_on_order()):
        raise ValueError('Este produto est\u00e1 indispon\u00edvel.')
    line = CartLine.objects.filter(user=user, product=product).first()
    if line and not replace:
        quantity += line.quantity
        chosen = merge_picks(line.picks, chosen)
    _clean_quantity(quantity)
    CartLine.objects.update_or_create(user=user, product=product, defaults={'quantity': quantity, 'picks': chosen})


def set_quantity(user, product_id, quantity):
    if quantity <= 0:
        CartLine.objects.filter(user=user, product_id=product_id).delete()
        return
    _clean_quantity(quantity)
    if CartLine.objects.filter(user=user, product_id=product_id).exclude(picks=[]).exists():
        raise ValueError('Altere as fragr\u00e2ncias escolhidas.')
    CartLine.objects.filter(user=user, product_id=product_id).update(quantity=quantity)


def cart_units(user):
    return sum(CartLine.objects.filter(user=user, product__archived_at__isnull=True).values_list('quantity', flat=True))


def _lines(user, customer):
    rows = list(CartLine.objects.filter(user=user, product__archived_at__isnull=True)
                .select_related('product').prefetch_related('product__images', 'product__contents__linked')
                .order_by('added_at', 'id'))
    units = sellable_units([row.product_id for row in rows])
    coming = product_ids_on_order()
    lines = []
    for row in rows:
        product = row.product
        price, discount = customer_price(product, customer)
        available = made_to_order(product) or units.get(product.pk, 0) > 0 or product.pk in coming
        mode = pick_mode(product)
        problem = '' if available and price is not None else ('Indispon\u00edvel' if not available else 'Sob consulta')
        if not problem and mode:
            gone = sold_out_pick(mode, row.picks)
            problem = 'Escolha as fragr\u00e2ncias' if not row.picks else (f'Sem stock: {gone}' if gone else '')
        lines.append({
            'product_id': product.pk, 'name': f'{product.brand} {display_title(product)}'.strip(),
            'brand': product.brand, 'title': display_title(product), 'barcode': product.barcode,
            'image_url': image_url(product), 'quantity': row.quantity,
            'base_price': product.wholesale_price or price or 0, 'customer_discount': discount,
            'catalog_price': price if price is not None else 0,
            'ok': not problem, 'problem': problem,
            'picks': row.picks, 'pick_mode': mode, 'pick_options': pick_options(product, row.picks),
        })
    return lines


def cart_quote(user, customer, destination):
    return build_quote(_lines(user, customer), destination)


def submit_request(user, customer, data, fingerprint, editing=None):
    """Turn the cart into a request, or raise ValueError saying why it cannot be.
    The prices are worked out again here, whatever the page was showing.
    ``editing``: the public id of the order this cart is changing."""
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
        fields = dict(
            customer=customer,
            delivery=data.get('delivery') or 'delivery', business_name=data.get('business_name') or '',
            contact_name=data['contact_name'], phone=data['phone'], email=data.get('email') or '',
            address=data.get('address') or '', postal_code=data.get('postal_code') or '',
            city=data.get('city') or '', country=data.get('country') or '', note=data.get('note') or '',
            subtotal=quote['subtotal'], tier_discount=quote['tier_discount'], tier_total=quote['tier_total'],
            goods_total=quote['goods_total'], shipping_state=quote['shipping_state'],
            shipping_fee=quote['shipping_fee'], policy=policy_snapshot())
        if editing:
            order = OrderRequest.objects.select_for_update().filter(
                public_id=editing, status=OrderRequest.SUBMITTED).first()
            if order is None:
                raise ValueError('Este pedido j\u00e1 foi confirmado e n\u00e3o pode ser alterado.')
            for name, value in fields.items():
                setattr(order, name, value)
            order.save()
            order.lines.all().delete()
        else:
            order = OrderRequest.objects.create(submitted_by=user, **fields)
        OrderRequestLine.objects.bulk_create([OrderRequestLine(
            request=order, product_id=line['product_id'], name=line['name'][:250], barcode=line['barcode'],
            quantity=line['quantity'], picks=line['picks'], base_price=line['base_price'], customer_discount=line['customer_discount'],
            catalog_price=line['catalog_price'], tier_discount=line['tier_discount'],
            final_price=line['final_price'], line_total=line['line_total']) for line in quote['lines']])
        CartLine.objects.filter(user=user).delete()
        save_to_pedido(order)
    return order
