"""Customer phone numbers: comparing them, and opening them in WhatsApp.

Phones are typed by hand ("912 345 678", "+351912345678", "00351 912..."),
so two spellings of the same number have to be recognised as one, and a
WhatsApp link needs the number with its country code and nothing else.
"""
import re

# Numbers typed without a country code are Portuguese: the shops are in Portugal.
DEFAULT_COUNTRY_CODE = '351'


def digits(phone):
    return re.sub(r'\D', '', phone or '')


def international(phone):
    """Digits with the country code, or '' when there is no usable number."""
    raw = (phone or '').strip()
    number = digits(raw)
    if not number:
        return ''
    if raw.startswith('+'):
        return number
    if number.startswith('00'):
        return number[2:]
    if len(number) == 9:
        return DEFAULT_COUNTRY_CODE + number
    return number


def same_number_key(phone):
    """What two spellings of one number share: its last nine digits."""
    number = digits(phone)
    return number[-9:] if len(number) >= 9 else number


def whatsapp_url(phone):
    number = international(phone)
    return f'https://wa.me/{number}' if len(number) >= 8 else ''


def customer_with_phone(phone, exclude_id=None):
    """The customer already using this number, if any."""
    from ..models import Customer
    key = same_number_key(phone)
    if len(key) < 6:
        return None
    # Compared in Python: a stored "912 345 678" has no run of digits a
    # database LIKE could match against "912345678".
    candidates = Customer.objects.exclude(phone__isnull=True).exclude(phone='')
    if exclude_id:
        candidates = candidates.exclude(id=exclude_id)
    for customer_id, stored in candidates.values_list('id', 'phone'):
        if same_number_key(stored) == key:
            return Customer.objects.get(id=customer_id)
    return None
