"""One way to write a brand, a series and a product name: each word capitalised.

``LATTAFA`` -> ``Lattafa``, ``CLUB DE NUIT`` -> ``Club De Nuit``,
``CREAM/BODY MIST`` -> ``Cream/Body Mist``, ``L'HOMME`` -> ``L'Homme``.
Kept as written: abbreviations the trade writes in capitals (EDP, EDT, II ...)
anything with a digit in it (B6108, 9AM, 24K) - those are codes - except a
volume, which is always lowercase (``100ML`` -> ``100ml``) - and words already
written in mixed case (iPhone, McLaren).
"""
import re

KEEP_UPPER = {'EDP', 'EDT', 'EDC', 'XL', 'XXL', 'UAE', 'USA', 'UK', 'EU', 'VIP', 'LED',
              'II', 'III', 'IV', 'VII', 'VIII', 'IX', 'XI', 'XII', 'XIII', 'XIV', 'XV', 'XX'}
VOLUME = re.compile(r'^(\d+(?:[.,]\d+)?)(ml|cl|l|g|kg|oz)$', re.IGNORECASE)
SEPARATORS = re.compile(r"([\s/\-+&(),.]+)")


def _word(token):
    upper = token.upper()
    if upper in KEEP_UPPER:
        return upper
    volume = VOLUME.match(token)
    if volume:
        return volume.group(1) + volume.group(2).lower()
    if any(ch.isdigit() for ch in token):
        return token
    # Written in mixed case on purpose - iPhone, McLaren, eBay - keep it.
    letters = [ch for ch in token if ch.isalpha()]
    if any(ch.isupper() for ch in letters[1:]) and any(ch.islower() for ch in letters):
        return token
    # L'HOMME -> L'Homme, D'ORO -> D'Oro
    return "'".join(p[:1].upper() + p[1:] for p in token.lower().split("'"))


def title_case(text):
    """``text`` with each word capitalised by the rules above; ``None`` stays ``None``."""
    if text is None:
        return None
    text = re.sub(r'\s+', ' ', str(text)).strip()
    return ''.join(part if not part or SEPARATORS.fullmatch(part) else _word(part)
                   for part in SEPARATORS.split(text))
