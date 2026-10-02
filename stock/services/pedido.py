"""The arithmetic behind a quote, kept away from the form and the PDF.

A quote is a throwaway document - nothing is stored - so the only thing that
has to be exactly right is the money. Keeping it here means it can be tested
without a request, a template or a PDF reader.

The order of operations is the part worth stating: the discount comes off the
goods, and transport and fees are added after it. A percentage discount applies
to the products only - nobody means "10% off the shipping too" when they knock
10% off a wholesale order, and applying it to the fees would quietly give away
money on every line the shop is merely passing through.
"""
from decimal import Decimal, ROUND_HALF_UP

CENTS = Decimal('0.01')

AMOUNT = 'amount'
PERCENT = 'percent'
DISCOUNT_KINDS = (AMOUNT, PERCENT)


def money(value):
    """A Decimal rounded to cents. Accepts anything Decimal() accepts."""
    if value in (None, ''):
        return Decimal('0.00')
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def line_total(quantity, unit_price):
    return money(money(unit_price) * int(quantity or 0))


def compute_totals(lines, *, shipping=0, extras=(), discount_value=0,
                   discount_kind=AMOUNT):
    """Add a quote up.

    ``lines``    - dicts carrying at least ``quantity`` and ``unit_price``.
    ``extras``   - dicts carrying ``label`` and ``amount``; blank labels and
                   zero amounts are dropped so an empty spare row on the form
                   never reaches the paper.
    Returns a dict of Decimals plus the cleaned ``extras`` list, which is what
    the PDF prints - so the document can never show a different set of fees
    from the ones that were added up.
    """
    subtotal = sum((line_total(l.get('quantity'), l.get('unit_price'))
                    for l in lines), Decimal('0.00'))
    subtotal = money(subtotal)

    if discount_kind == PERCENT:
        percent = money(discount_value)
        # A percentage over 100 would turn the discount into a surcharge.
        percent = min(max(percent, Decimal('0.00')), Decimal('100.00'))
        discount = money(subtotal * percent / Decimal('100'))
    else:
        discount = max(money(discount_value), Decimal('0.00'))
    # Never discount more than the goods are worth.
    discount = min(discount, subtotal)

    clean_extras = []
    for extra in extras:
        amount = money(extra.get('amount'))
        label = (extra.get('label') or '').strip()
        if not label and not amount:
            continue
        clean_extras.append({'label': label or 'Outros', 'amount': amount})
    extras_total = money(sum((e['amount'] for e in clean_extras), Decimal('0.00')))

    shipping = money(shipping)
    total = money(subtotal - discount + shipping + extras_total)
    # Fees can be negative (a credit), so the total still needs a floor.
    total = max(total, Decimal('0.00'))

    return {
        'subtotal': subtotal,
        'discount': discount,
        'shipping': shipping,
        'extras': clean_extras,
        'extras_total': extras_total,
        'total': total,
    }
