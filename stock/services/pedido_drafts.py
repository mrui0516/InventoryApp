"""Pedido drafts: keeping a quote after its PDF, and turning it into a sale.

A draft stores the form as it was filled in. Turning it into a sale has one
real decision in it, :func:`sell_payload`: a sale is product lines plus
charges, with no order discount, so the quote's discount is spread over the
unit prices and its transport and fees become charges.
"""
from decimal import Decimal, ROUND_HALF_UP

from . import pedido as pedido_math

ZERO = Decimal('0.00')
CENT = Decimal('0.01')


def draft_lines_for_form(draft, products):
    """The draft's lines with what the Pedido form shows for each product.

    A product deleted since the quote is dropped rather than kept as a line
    with no name.
    """
    out = []
    for line in draft.lines or []:
        product = products.get(line.get('product_id'))
        if product is None:
            continue
        out.append({'product': product, 'quantity': int(line.get('quantity') or 0),
                    'unit_price': pedido_math.money(line.get('unit_price'))})
    return out


def spread_discount(lines, discount):
    """Per-unit discounts that take ``discount`` off ``lines`` in proportion.

    ``lines`` carry ``quantity`` and ``unit_price``. Returns one Decimal per
    line. A unit discount has to be whole cents, so a line of three pieces
    can only move in steps of three cents; the cents that are left go on a
    single-piece line when there is one, and otherwise stay as a difference
    of a few cents from the quote.
    """
    discount = pedido_math.money(discount)
    subtotal = sum((pedido_math.line_total(l['quantity'], l['unit_price']) for l in lines), ZERO)
    if discount <= 0 or subtotal <= 0:
        return [ZERO for _ in lines]
    share = discount / subtotal
    per_unit = [(l['unit_price'] * share).quantize(CENT, rounding=ROUND_HALF_UP) for l in lines]
    taken = sum((d * l['quantity'] for d, l in zip(per_unit, lines)), ZERO)
    rest = discount - taken
    if rest:
        for index, line in enumerate(lines):
            candidate = per_unit[index] + rest
            if line['quantity'] == 1 and ZERO <= candidate < line['unit_price']:
                per_unit[index] = candidate
                break
    return per_unit


def sell_payload(draft, products):
    """What the Sell page needs to open a draft as a cart.

    ``lines``   - product, quantity, the quoted unit price and the per-unit
                  discount that carries the quote's order discount.
    ``charges`` - transport and every positive fee, labelled as on the quote.
                  A negative fee is a credit; it joins the discount instead.
    """
    lines = draft_lines_for_form(draft, products)
    subtotal = sum((pedido_math.line_total(l['quantity'], l['unit_price']) for l in lines), ZERO)
    totals = pedido_math.compute_totals(
        lines, shipping=draft.shipping, extras=draft.extras or [],
        discount_value=draft.discount_value, discount_kind=draft.discount_kind)

    charges = []
    credit = ZERO
    if totals['shipping'] > 0:
        charges.append({'label': 'Transporte', 'amount': totals['shipping']})
    for extra in totals['extras']:
        if extra['amount'] > 0:
            charges.append({'label': extra['label'], 'amount': extra['amount']})
        elif extra['amount'] < 0:
            credit += -extra['amount']

    discount = min(totals['discount'] + credit, subtotal)
    for line, per_unit in zip(lines, spread_discount(lines, discount)):
        line['discount'] = per_unit
    return {'lines': lines, 'charges': charges}
