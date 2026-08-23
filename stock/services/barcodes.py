"""Internal barcodes for goods that arrive without one.

Phone cases, screen protectors and cables mostly ship with no EAN at all, but
``Product.barcode`` is the identity key for the till, for Cloudinary asset
names and for the Shopify variant SKU. Rather than make it nullable and teach
a hundred call sites to cope, we mint a barcode ourselves.

GS1 reserves prefixes 02 and 20-29 for restricted circulation inside a single
shop, so an internal code is a *real, valid* EAN-13: it fits the existing
13-character column, it scans, and it can be printed on a shelf label. Nothing
downstream needs to know the difference - ``barcode_is_internal`` records it
only so the UI can say where the number came from.
"""
from django.db import IntegrityError, transaction

PREFIX = '29'
BODY_DIGITS = 10          # PREFIX + body + check digit == 13


def ean13_check_digit(twelve_digits):
    """The 13th digit of an EAN-13, per GS1: weights alternate 1 and 3."""
    if len(twelve_digits) != 12 or not twelve_digits.isdigit():
        raise ValueError('EAN-13 check digit needs exactly 12 digits')
    total = sum(int(digit) * (3 if index % 2 else 1)
                for index, digit in enumerate(twelve_digits))
    return str((10 - total % 10) % 10)


def build_internal_barcode(sequence):
    body = str(sequence).rjust(BODY_DIGITS, '0')
    if len(body) > BODY_DIGITS:
        raise ValueError('internal barcode sequence exhausted')
    return (PREFIX + body) + ean13_check_digit(PREFIX + body)


def _next_sequence(model):
    last = (model.all_objects
            .filter(barcode_is_internal=True, barcode__startswith=PREFIX)
            .order_by('-barcode')
            .values_list('barcode', flat=True)
            .first())
    if not last:
        return 1
    return int(last[len(PREFIX):-1]) + 1


def assign_internal_barcode(product, attempts=25):
    """Give ``product`` a fresh internal barcode and save it.

    ``select_for_update`` is a no-op on SQLite, so two tills creating a product
    at the same instant can pick the same sequence. The unique index is the
    real guard: on collision we simply take the next number.
    """
    model = type(product)
    sequence = _next_sequence(model)
    for offset in range(attempts):
        candidate = build_internal_barcode(sequence + offset)
        if model.all_objects.filter(barcode=candidate).exists():
            continue
        product.barcode = candidate
        product.barcode_is_internal = True
        try:
            with transaction.atomic():
                product.save()
            return candidate
        except IntegrityError:
            continue                      # lost the race, try the next number
    raise RuntimeError('could not allocate an internal barcode')
