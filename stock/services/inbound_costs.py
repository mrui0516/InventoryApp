"""What a delivered unit costs: the supplier's price, IVA, its part of the shipping.

Portuguese suppliers quote without IVA, so a price is typed as quoted and 23% goes
on top when the order says so. Shipping comes off one bill and is allocated line
by line by hand. ``InboundPendingItem.cost_price`` stays the price as quoted, so
IVA can be switched while the order waits; the batch made on receipt holds the
full cost, with the quoted price beside it.
"""
from decimal import ROUND_HALF_UP, Decimal

IVA_RATE = Decimal('23.00')
CENT = Decimal('0.01')
INVOICE_MAX_BYTES = 10 * 1024 * 1024
INVOICE_EXTENSIONS = ('.pdf', '.jpg', '.jpeg', '.png', '.webp', '.heic')


def unit_cost(net, iva_rate, shipping_share, quantity):
    """One unit of a line: price, plus IVA, plus its share of the line's shipping."""
    cost = (net or Decimal('0')) * (1 + (iva_rate or Decimal('0')) / 100)
    if shipping_share and quantity:
        cost += shipping_share / quantity
    return cost.quantize(CENT, rounding=ROUND_HALF_UP)


def check_shipping(total, shares):
    """The lines must carry the whole shipping. Raises ``ValueError``."""
    allocated = sum(shares, Decimal('0.00'))
    if allocated != (total or Decimal('0.00')):
        raise ValueError(f'Shipping: €{allocated:.2f} of €{total:.2f} allocated to the lines.')


def check_invoice(upload):
    """A PDF or a photo of a sensible size. Raises ``ValueError``."""
    if upload is None:
        return
    if not (upload.name or '').lower().endswith(INVOICE_EXTENSIONS):
        raise ValueError('The invoice must be a PDF or a photo.')
    if upload.size > INVOICE_MAX_BYTES:
        raise ValueError('The invoice is over 10 MB.')
