"""The business rules the owner sets in Settings -> Rules, read from one place.

Each rule is a field on ``BusinessSettings``. ``rules()`` reads the row once per
request (``BusinessSettings.current``), so a change applies on the next page and
a loop over products costs no extra query. An order keeps the rules it was
placed under (``OrderRequest.policy``); changing a rule never rewrites it.
"""
from decimal import Decimal

from ..models.core import BusinessSettings

MONEY, WHOLE = 'money', 'whole'

# (section, [(field, label, unit, kind, low, high)])
SECTIONS = (
    ('Wholesale catalogue', (
        ('wholesale_minimum', 'Minimum order', '€', MONEY, 0, 100000),
        ('wholesale_free_shipping', 'Free shipping from', '€', MONEY, 0, 100000),
        ('wholesale_shipping_fee', 'Shipping fee below it', '€', MONEY, 0, 1000),
        ('wholesale_tier1_from', 'Volume discount from', '€', MONEY, 0, 1000000),
        ('wholesale_tier1_off', 'Off each unit', '€', MONEY, 0, 1000),
        ('wholesale_tier2_from', 'Bigger discount from', '€', MONEY, 0, 1000000),
        ('wholesale_tier2_off', 'Off each unit', '€', MONEY, 0, 1000),
        ('discount_min_ml', "Customer's discount from", 'ml', WHOLE, 1, 5000),
        ('discount_max_ml', 'up to', 'ml', WHOLE, 1, 5000),
    )),
    ('Payments and quotes', (
        ('supplier_term_days', 'Supplier invoices due after', 'days', WHOLE, 0, 365),
        ('iva_rate', 'IVA', '%', MONEY, 0, 100),
        ('pedido_valid_days', 'A Pedido is valid for', 'days', WHOLE, 0, 365),
    )),
    ('Stock', (
        ('catalog_low_stock_below', '"Stock reduzido" below', 'units', WHOLE, 1, 1000),
        ('low_stock_alert_below', 'Low stock alert below', 'units', WHOLE, 1, 1000),
        ('samples_per_shop', 'Samples kept per shop', 'units', WHOLE, 0, 20),
    )),
    ('Auto pricing (perfumes)', (
        ('wholesale_markup', 'Wholesale = cost +', '€', MONEY, 0, 10000),
        ('retail_markup', 'Retail = wholesale +', '€', MONEY, 0, 10000),
        ('pvp_uplift', 'PVP = retail +', '€', MONEY, 0, 10000),
    )),
)
FIELDS = [field for _section, rows in SECTIONS for field in rows]


def rules():
    return BusinessSettings.current()


def clean(post):
    """``(values, errors)`` from the Rules form. Nothing is saved with an error."""
    values, errors = {}, []
    for name, label, unit, kind, low, high in FIELDS:
        raw = (post.get(name) or '').strip().replace(',', '.').replace('€', '')
        try:
            value = Decimal(raw)
            if kind == WHOLE:
                if value != value.to_integral_value():
                    raise ValueError
                value = int(value)
            if not low <= value <= high:
                raise ValueError
        except (ArithmeticError, ValueError):
            errors.append(f'{label}: enter a number from {low} to {high} {unit}.')
            continue
        values[name] = value.quantize(Decimal('0.01')) if kind == MONEY else value
    if not errors:
        if values['wholesale_tier2_from'] <= values['wholesale_tier1_from']:
            errors.append('The bigger discount has to start above the first one.')
        if values['discount_min_ml'] > values['discount_max_ml']:
            errors.append("The customer's discount: the smallest size cannot be above the largest.")
    return values, errors


def save(values):
    row = BusinessSettings.get()
    for name, value in values.items():
        setattr(row, name, value)
    row.save()
    from .low_stock import forget
    forget()        # the alert list is cached with the old threshold
    return row


def wholesale_policy():
    """The catalogue's order rules, in the shape wholesale_benefits works with."""
    r = rules()
    return {'minimum': r.wholesale_minimum, 'free_shipping': r.wholesale_free_shipping,
            'shipping_fee': r.wholesale_shipping_fee,
            'tiers': ((r.wholesale_tier2_from, r.wholesale_tier2_off), (r.wholesale_tier1_from, r.wholesale_tier1_off))}
