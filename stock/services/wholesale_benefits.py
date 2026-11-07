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


DEFAULT_POLICY = {'minimum': MINIMUM, 'free_shipping': FREE_SHIPPING, 'shipping_fee': SHIPPING_FEE, 'tiers': TIERS}


def tier_for(subtotal, tiers=TIERS):
    for threshold, discount in tiers:
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


def _message(subtotal, zone, policy):
    minimum, free = policy['minimum'], policy['free_shipping']
    (top, top_off), (first, first_off) = policy['tiers']
    if subtotal < minimum:
        return f'Faltam {eur(minimum - subtotal)}\u20ac para o pedido m\u00ednimo de {eur(minimum)}\u20ac.'
    if subtotal < free and zone in ('mainland', 'unknown'):
        return f'Faltam {eur(free - subtotal)}\u20ac para portes gr\u00e1tis em Portugal Continental.'
    if subtotal < first:
        return f'Faltam {eur(first - subtotal)}\u20ac para mais {eur(first_off)}\u20ac de desconto por unidade.'
    if subtotal < top:
        return (f'Faltam {eur(top - subtotal)}\u20ac para {eur(top_off)}\u20ac de desconto por unidade '
                f'(agora {eur(first_off)}\u20ac).')
    return f'Escal\u00e3o m\u00e1ximo: {eur(top_off)}\u20ac extra de desconto por unidade.'


def progress(subtotal, zone, policy=None):
    policy = policy or DEFAULT_POLICY
    (top, top_off), (first, first_off) = policy['tiers']
    end = top or BAR_END
    marks = [(policy['minimum'], 'M\u00edn.')]
    if zone in ('mainland', 'unknown'):
        marks.append((policy['free_shipping'], 'Portes'))
    marks += [(first, f'-{eur(first_off)}\u20ac'), (top, f'-{eur(top_off)}\u20ac')]
    return {
        'message': _message(subtotal, zone, policy),
        'percent': int(min(subtotal / end * 100, Decimal('100'))),
        'marks': [{'amount': int(amount), 'label': label, 'at': int(min(amount / end * 100, Decimal('100'))),
                   'reached': subtotal >= amount} for amount, label in marks],
    }


def _fingerprint(lines):
    payload = [[line['product_id'], line['quantity'], str(line['catalog_price']), line.get('picks') or []]
               for line in lines]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def build_quote(lines, destination, policy=None):
    """``lines``: dicts with product_id, name, barcode, quantity, base_price,
    customer_discount, catalog_price and ok (it can be ordered)."""
    policy = policy or DEFAULT_POLICY
    subtotal = sum((line['catalog_price'] * line['quantity'] for line in lines), Decimal('0'))
    tier = tier_for(subtotal, policy['tiers'])
    out, goods = [], Decimal('0')
    for line in lines:
        final = line['catalog_price'] - tier
        total = final * line['quantity']
        goods += total
        out.append({**line, 'tier_discount': tier, 'final_price': final, 'line_total': total, 'needs_price': final <= 0})

    zone = zone_of(destination)
    free = zone == 'mainland' and subtotal >= policy['free_shipping']
    if zone == 'pickup':
        state, label, fee = 'pickup', 'Levantamento em loja', Decimal('0')
    elif free:
        state, label, fee = 'free', 'Portes gr\u00e1tis', Decimal('0')
    elif zone == 'mainland':
        state, label, fee = 'standard', 'Portes', policy['shipping_fee']
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
        'short_of_minimum': max(policy['minimum'] - subtotal, Decimal('0')),
        'minimum': policy['minimum'],
        'policy': policy,
        'can_submit': bool(out) and subtotal >= policy['minimum'] and all(l['ok'] and not l['needs_price'] for l in out),
        'progress': progress(subtotal, zone, policy),
        'fingerprint': _fingerprint(out),
    }


def policy_snapshot(policy=None):
    policy = policy or DEFAULT_POLICY
    return {'minimum': str(policy['minimum']), 'free_shipping': str(policy['free_shipping']),
            'shipping_fee': eur(policy['shipping_fee']),
            'tiers': [[str(threshold), str(discount)] for threshold, discount in policy['tiers']]}
