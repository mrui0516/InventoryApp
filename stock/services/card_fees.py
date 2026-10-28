"""Card fees: what the terminal keeps, taken off profit.

The fee is worked out when an order is made, from the card part of its
payments and the rate set in Settings, and stored on the order. Profit then
spreads it over the order's lines by their value (``fee_by_sale``), so every
figure built on ``sale_profit_map_for_sale_ids`` - Dashboard, Orders, Today,
customer pages, reports - is net of it. MB WAY and cash carry no fee.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal('0.01')
# Card fees count from this day on; older orders keep their profit as it was.
import datetime as _dt
FEES_FROM = _dt.date(2026, 10, 28)


def rate():
    from ..models import BusinessSettings
    return BusinessSettings.get().card_fee_pct


def fee_for(payment_totals):
    """The fee on a ``{method: amount}`` payment split."""
    card = Decimal(str((payment_totals or {}).get('card') or 0))
    if card <= 0:
        return Decimal('0.00')
    return (card * rate() / Decimal('100')).quantize(CENT, rounding=ROUND_HALF_UP)


def fee_by_sale(sales):
    """``{sale_id: share of its order's card fee}`` for the given Sale rows
    (each with ``order_id``, ``quantity`` and ``unit_price``)."""
    from ..models import Sale, SaleOrder
    order_ids = {s.order_id for s in sales if s.order_id}
    if not order_ids:
        return {}
    fees = dict(SaleOrder.objects.filter(id__in=order_ids, card_fee__gt=0).values_list('id', 'card_fee'))
    if not fees:
        return {}
    totals = defaultdict(Decimal)
    for line in Sale.objects.filter(order_id__in=fees).only('order_id', 'quantity', 'unit_price'):
        totals[line.order_id] += (line.unit_price or Decimal('0')) * line.quantity
    shares = {}
    for s in sales:
        fee = fees.get(s.order_id)
        if not fee or not totals.get(s.order_id):
            continue
        value = (s.unit_price or Decimal('0')) * s.quantity
        shares[s.id] = (fee * value / totals[s.order_id]).quantize(CENT, rounding=ROUND_HALF_UP)
    return shares


def record(order, payment_totals):
    """Store the fee on a new order (Sell)."""
    from ..models import SaleOrder
    fee = fee_for(payment_totals)
    SaleOrder.objects.filter(pk=order.pk).update(card_fee=fee)
    order.card_fee = fee


def refresh_after_correction(order, payment_totals):
    """A corrected order's payments may have changed. Orders from before card
    fees were recorded keep 0 - the fee applies from 2026-10-28 on."""
    from ..models import SaleOrder
    if order.created_at.date() < FEES_FROM:
        return
    record(order, payment_totals)
