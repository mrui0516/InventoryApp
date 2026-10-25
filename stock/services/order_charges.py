"""Shipping and fees on an order, and the last price a customer paid.

Charges (``SaleOrderCharge``) are money taken with an order that is not a
product. They never enter ``Sale``, so sales, profit and targets stay
goods-only; they do enter the payments, so the money collected matches the
till. Sell and Correct Order post them the same way, as ``charges_json``:

    [{"label": "Transporte", "amount": "6.50", "payment": "card"}, ...]
"""
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.db.models.functions import Coalesce

ZERO = Decimal('0.00')
METHODS = ('cash', 'card', 'mbway')
MAX_CHARGES = 20
LABEL_MAX = 60


def parse_charges(raw):
    """``charges_json`` -> ``[{'label', 'amount', 'payment_method'}]``.

    Raises ``ValueError`` with a message for the person at the till. A row
    with neither a label nor an amount is a blank spare row and is skipped.
    """
    try:
        rows = json.loads(raw or '[]')
    except (TypeError, ValueError):
        raise ValueError('Could not read the shipping and fees.')
    if not isinstance(rows, list):
        raise ValueError('Could not read the shipping and fees.')
    charges = []
    for row in rows[:MAX_CHARGES]:
        if not isinstance(row, dict):
            continue
        label = str(row.get('label') or '').strip()[:LABEL_MAX]
        raw_amount = str(row.get('amount') or '').strip().replace(',', '.')
        if not label and not raw_amount:
            continue
        try:
            amount = Decimal(raw_amount).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            raise ValueError(f'{label or "Fee"}: the amount must be a number.')
        if amount <= 0:
            raise ValueError(f'{label or "Fee"}: the amount must be above 0.')
        method = str(row.get('payment') or '').strip()
        if method not in METHODS:
            raise ValueError(f'{label or "Fee"}: choose how it was paid.')
        charges.append({'label': label or 'Fee', 'amount': amount, 'payment_method': method})
    return charges


def charges_total(charges):
    """Sum of parsed charges or ``SaleOrderCharge`` rows."""
    total = ZERO
    for charge in charges:
        amount = charge['amount'] if isinstance(charge, dict) else charge.amount
        total += amount or ZERO
    return total


def order_charges(order):
    """The order's charges; reads a prefetch when the caller made one."""
    if order is None or not getattr(order, 'pk', None):
        return []
    return list(order.charges.all())


def charge_payments(charges):
    """Per-method totals of the charges, to add to the goods' payments."""
    out = defaultdict(Decimal)
    for charge in charges:
        method = charge['payment_method'] if isinstance(charge, dict) else charge.payment_method
        amount = charge['amount'] if isinstance(charge, dict) else charge.amount
        out[method] += amount or ZERO
    return out


def save_charges(order, charges):
    """Replace the order's charges with ``charges`` (parsed dicts)."""
    from ..models import SaleOrderCharge
    order.charges.all().delete()
    SaleOrderCharge.objects.bulk_create([
        SaleOrderCharge(order=order, label=c['label'], amount=c['amount'],
                        payment_method=c['payment_method'])
        for c in charges
    ])


def last_prices(customer_id):
    """``{product_id: {'price', 'date'}}``: what this customer last paid.

    Read from the sales themselves, so there is nothing extra to keep in step:
    a corrected or deleted order changes the answer on its own. The price is
    the unit price after any discount - what they actually paid. Quotes do not
    count; only things that were sold.
    """
    from ..models import Sale
    if not customer_id:
        return {}
    rows = (Sale.objects
            .filter(Q(order__customer_id=customer_id)
                    | Q(order__isnull=True, customer_id=customer_id))
            .annotate(at=Coalesce('order__created_at', 'date'))
            .order_by('-at', '-id')
            .values_list('product_id', 'unit_price', 'at'))
    found = {}
    for product_id, price, at in rows:
        if product_id not in found:
            found[product_id] = {'price': price, 'date': at}
    return found
