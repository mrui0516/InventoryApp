"""The order rules: minimum, free shipping, volume discount, and the amounts.

Nothing here reads the database or trusts the browser: give it the lines and the
destination, it gives back every figure a page or a request needs.

The tiers read ``subtotal``, the goods at the customer's own price before the
volume discount, so unlocking a discount never takes the order back below it.
"""
import hashlib
import json
import re
from decimal import Decimal

MINIMUM = Decimal('150')
FREE_SHIPPING = Decimal('300')
SHIPPING_FEE = Decimal('5')     # Portugal Continental, below FREE_SHIPPING
TIERS = ((Decimal('1000'), Decimal('2')), (Decimal('500'), Decimal('1')))   # highest first: it replaces the lower
MAX_QUANTITY = 999
BAR_END = Decimal('1000')

POSTAL_PT = re.compile(r'^(\d{4})-\d{3}$')


def eur(amount):
    amount = Decimal(amount).quantize(Decimal('0.01'))
    return str(int(amount)) if amount == int(amount) else str(amount)


def tier_for(subtotal):
    for threshold, discount in TIERS:
        if subtotal >= threshold:
            return discount
    return Decimal('0')


def zone_of(destination):
    """pickup, mainland, islands, eu or unknown. An empty postal code in Portugal
    reads as mainland while the form is being filled; one we cannot read is unknown,
    and shipping to it stays to be confirmed."""
    if destination.get('delivery') == 'pickup':
        return 'pickup'
    country = (destination.get('country') or '').strip().upper()
    if not country:
        return 'unknown'
    if country != 'PT':
        return 'eu'
    postal = (destination.get('postal_code') or '').strip()
    if not postal:
        return 'mainland'
    match = POSTAL_PT.match(postal)
    if not match:
        return 'unknown'
    code = int(match.group(1))
    if 1000 <= code <= 8999:
        return 'mainland'
    if 9000 <= code <= 9399 or 9500 <= code <= 9999:
        return 'islands'
    return 'unknown'


def _message(subtotal, zone):
    if subtotal < MINIMUM:
        return f'Faltam {eur(MINIMUM - subtotal)}\u20ac para o pedido m\u00ednimo de {eur(MINIMUM)}\u20ac.'
    if subtotal < FREE_SHIPPING and zone in ('mainland', 'unknown'):
        return f'Faltam {eur(FREE_SHIPPING - subtotal)}\u20ac para portes gr\u00e1tis em Portugal Continental.'
    if subtotal < TIERS[1][0]:
        return f'Faltam {eur(TIERS[1][0] - subtotal)}\u20ac para mais {eur(TIERS[1][1])}\u20ac de desconto por unidade.'
    if subtotal < TIERS[0][0]:
        return (f'Faltam {eur(TIERS[0][0] - subtotal)}\u20ac para {eur(TIERS[0][1])}\u20ac de desconto por unidade '
                f'(agora {eur(TIERS[1][1])}\u20ac).')
    return f'Escal\u00e3o m\u00e1ximo: {eur(TIERS[0][1])}\u20ac extra de desconto por unidade.'


def progress(subtotal, zone):
    marks = [(MINIMUM, 'M\u00edn.')]
    if zone in ('mainland', 'unknown'):
        marks.append((FREE_SHIPPING, 'Portes'))
    marks += [(TIERS[1][0], f'-{eur(TIERS[1][1])}\u20ac'), (TIERS[0][0], f'-{eur(TIERS[0][1])}\u20ac')]
    return {
        'message': _message(subtotal, zone),
        'percent': int(min(subtotal / BAR_END * 100, Decimal('100'))),
        'marks': [{'amount': int(amount), 'label': label, 'at': int(amount / BAR_END * 100), 'reached': subtotal >= amount}
                  for amount, label in marks],
    }


def _fingerprint(lines):
    payload = [[line['product_id'], line['quantity'], str(line['catalog_price']), line.get('picks') or []]
               for line in lines]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def build_quote(lines, destination):
    """``lines``: dicts with product_id, name, barcode, quantity, base_price,
    customer_discount, catalog_price and ok (it can be ordered)."""
    subtotal = sum((line['catalog_price'] * line['quantity'] for line in lines), Decimal('0'))
    tier = tier_for(subtotal)
    out, goods = [], Decimal('0')
    for line in lines:
        final = line['catalog_price'] - tier
        total = final * line['quantity']
        goods += total
        out.append({**line, 'tier_discount': tier, 'final_price': final, 'line_total': total, 'needs_price': final <= 0})

    zone = zone_of(destination)
    free = zone == 'mainland' and subtotal >= FREE_SHIPPING
    if zone == 'pickup':
        state, label, fee = 'pickup', 'Levantamento em loja', Decimal('0')
    elif free:
        state, label, fee = 'free', 'Portes gr\u00e1tis', Decimal('0')
    elif zone == 'mainland':
        state, label, fee = 'standard', 'Portes', SHIPPING_FEE
    else:
        state, label, fee = 'pending', 'Portes por confirmar', None

    return {
        'lines': out,
        'units': sum(line['quantity'] for line in out),
        'subtotal': subtotal,
        'tier_discount': tier,
        'tier_total': subtotal - goods,
        'goods_total': goods,
        'zone': zone,
        'free_shipping': free,
        'shipping_state': state,
        'shipping_label': label,
        'shipping_fee': fee,
        'total': goods + (fee or Decimal('0')),
        'short_of_minimum': max(MINIMUM - subtotal, Decimal('0')),
        'can_submit': bool(out) and subtotal >= MINIMUM and all(l['ok'] and not l['needs_price'] for l in out),
        'progress': progress(subtotal, zone),
        'fingerprint': _fingerprint(out),
    }


def policy_snapshot():
    return {'minimum': str(MINIMUM), 'free_shipping': str(FREE_SHIPPING), 'shipping_fee': eur(SHIPPING_FEE),
            'tiers': [[str(threshold), str(discount)] for threshold, discount in TIERS]}
