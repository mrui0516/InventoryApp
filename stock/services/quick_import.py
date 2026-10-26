"""Quick import: turn a pasted invoice / order text (or a text PDF) into lines.

Rules only - no AI, no outside service. What makes that workable is the
arithmetic every invoice line carries: quantity x unit price = line total. A
line whose numbers do not add up is flagged, so a misread never slips through
silently; the person confirms every line before it reaches a form.

The reading, in order:

1. Flatten the text (PDF copies break lines anywhere, and some turn spaces
   into "?").
2. Cut it into items: by the invoice's own line numbers when it has them
   (1, 2, 3 ...), else at each EAN, else one item per line.
3. In each item: the EAN, the quantity (the whole number just before the first
   price), the prices, and the name (everything before the quantity).
4. Match a product: by EAN first, then by the words of the name.
"""
import io
from difflib import get_close_matches
import re
from decimal import Decimal, InvalidOperation

EAN_RE = re.compile(r'(?<!\d)(\d{13}|\d{12}|\d{8})(?!\d)')
# 1.234,56 / 1234,56 / 12.75 - a price always has two decimals
MONEY_RE = re.compile(r'(?<![\d.,])(\d{1,3}(?:[.  ]\d{3})+,\d{2}|\d+,\d{2}|\d+\.\d{2})(?![\d])')
INT_RE = re.compile(r'(?<![\d.,])(\d{1,4})(?![\d.,])')
QTY_X_RE = re.compile(r'(?:\bx\s*(\d{1,4})\b|\b(\d{1,4})\s*(?:x|un|uni|unid|pcs|pz)\b)', re.I)
CENT = Decimal('0.01')
MAX_PDF_BYTES = 5 * 1024 * 1024

STOPWORDS = {
    'eau', 'de', 'du', 'parfum', 'perfume', 'edp', 'edt', 'edc', 'ml', 'the', 'and', 'by', 'for',
    'spray', 'vaporisateur', 'natural', 'unisex', 'men', 'women', 'homme', 'femme', 'pour', 'oz',
}


class QuickImportError(ValueError):
    """A message for the person importing."""


# ---------------------------------------------------------------- reading
def pdf_text(uploaded):
    """The text of a PDF that has a text layer (what invoicing software makes)."""
    from pypdf import PdfReader
    data = uploaded.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise QuickImportError('The PDF is larger than 5 MB.')
    try:
        reader = PdfReader(io.BytesIO(data))
        text = '\n'.join((page.extract_text() or '') for page in reader.pages)
    except Exception:
        raise QuickImportError('This file could not be read as a PDF.')
    if len(re.sub(r'\s', '', text)) < 20:
        raise QuickImportError('No text found in this PDF - it is probably a scan. '
                               'Open it, copy the product lines and paste them instead.')
    return text


def _money(raw):
    raw = raw.replace(' ', '').replace(' ', '')
    if ',' in raw:
        raw = raw.replace('.', '').replace(',', '.')
    try:
        return Decimal(raw).quantize(CENT)
    except InvalidOperation:
        return None


def _flatten(text):
    text = (text or '').replace(' ', ' ').replace('?', ' ').replace('\t', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def _split_items(text):
    """Items as strings, without their leading line numbers."""
    flat = _flatten(text)
    # 1. the invoice's own line numbers. A number starts an item when it opens
    #    the text (or is 1, after a header), or comes right after a price - the
    #    end of the line before. Numbers only need to rise, so a copied part of
    #    an invoice (lines 6-8) works, while "100 ml" or "9 PM" inside a name,
    #    never preceded by a price, cannot be taken for one.
    starts, last_no = [], 0
    for m in re.finditer(r'(?:^|(?<=\s))(\d{1,3})\s+(?=\d{8,14}|[A-Za-zÀ-ÿ])', flat):
        number = int(m.group(1))
        before = flat[:m.start()].rstrip()
        after_price = re.search(r'(\d,\d{2}|\d\.\d{2})\s*(€|eur)?$', before, re.I)
        opens = not starts and (not before or number == 1)
        if number > last_no and (opens or (starts and after_price)):
            starts.append((m.start(), m.end()))
            last_no = number
    if len(starts) >= 2:
        items = []
        for i, (start, body) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else len(flat)
            items.append(flat[body:end].strip())
        return items
    # 2. one item per EAN
    eans = list(EAN_RE.finditer(flat))
    if len(eans) >= 2:
        items = []
        for i, m in enumerate(eans):
            end = eans[i + 1].start() if i + 1 < len(eans) else len(flat)
            items.append(flat[m.start():end].strip())
        return items
    # 3. one per line
    return [_flatten(line) for line in (text or '').splitlines() if _flatten(line)]


SUMMARY_RE = re.compile(r'\b(sub-?total|total|iva|vat|imposto|taxa|base\s+tribut|portes|shipping)\b', re.I)


def _parse_item(item):
    # The invoice's own summary ("Total 334,50 €", "IVA 23% ...") follows the
    # last product line in the flattened text; it is not part of that line.
    # Only cut after a price, so a product named "Total Black" survives.
    cut = SUMMARY_RE.search(item)
    if cut and MONEY_RE.search(item[:cut.start()]):
        item = item[:cut.start()].strip()
    ean = None
    m = EAN_RE.search(item)
    if m and m.start() <= 3:            # an EAN leads the item, glued to the name or not
        ean = m.group(1)
        item = (item[:m.start()] + ' ' + item[m.end():]).strip()
    elif m and not MONEY_RE.search(item[:m.start()]):
        ean = m.group(1)
        item = (item[:m.start()] + ' ' + item[m.end():]).strip()

    monies = list(MONEY_RE.finditer(item))
    qty, name_end = None, len(item)
    if monies:
        before = item[:monies[0].start()]
        ints = list(INT_RE.finditer(before))
        if ints:
            last = ints[-1]
            tail = before[last.end():].strip().lower()
            if not tail.startswith('ml'):
                qty = int(last.group(1))
                name_end = last.start()
        if qty is None:
            name_end = monies[0].start()
    else:
        mx = QTY_X_RE.search(item)
        if mx:
            qty = int(mx.group(1) or mx.group(2))
            name_end = mx.start()
        else:
            ints = [i for i in INT_RE.finditer(item)
                    if not item[i.end():].strip().lower().startswith('ml')]
            if ints:
                qty = int(ints[-1].group(1))
                name_end = ints[-1].start()

    prices = [p for p in (_money(mm.group(1)) for mm in monies) if p is not None]
    unit = total = None
    check = None
    if prices:
        if len(prices) == 1:
            unit = prices[0]
        else:
            total = prices[-1]
            for p in reversed(prices[:-1]):           # the net unit price comes last
                if qty and abs(p * qty - total) <= CENT * 2:
                    unit = p
                    break
            if unit is None:
                unit = prices[-2]
                check = False
            else:
                check = True
    name = re.sub(r'[\s/|,;:-]+$', '', item[:name_end]).strip()
    name = re.sub(r'\s*/\s*', ' ', name)
    return {'ean': ean, 'name': name, 'qty': qty or 1, 'qty_read': qty is not None,
            'unit_price': unit, 'total': total, 'check': check}


def parse_text(text):
    rows = []
    for item in _split_items(text):
        row = _parse_item(item)
        if row['ean'] or len(row['name']) >= 3:
            rows.append(row)
    return rows[:200]


# --------------------------------------------------------------- matching
def _words(text):
    return [w for w in re.split(r'[^0-9a-zà-ÿ]+', (text or '').lower()) if w]


def _volume(text):
    m = re.search(r'(\d{2,4})\s*ml\b', (text or '').lower())
    return int(m.group(1)) if m else None


def match_products(rows):
    """Adds ``product`` (or None), ``match`` ('ean' / 'name' / None) and
    ``candidates`` (best guesses by name) to each row."""
    from ..models import Product
    eans = {r['ean'] for r in rows if r['ean']}
    variants = set()
    for e in eans:
        variants |= {e, e.lstrip('0'), e.zfill(13)}
    by_barcode = {p.barcode: p for p in Product.objects.filter(barcode__in=variants)}

    need_names = [r for r in rows if not (r['ean'] and _find_barcode(by_barcode, r['ean']))]
    catalogue = []
    if need_names:
        for p in Product.objects.only('id', 'brand', 'model', 'name', 'barcode', 'volume_ml', 'spec'):
            words = set(_words(' '.join(filter(None, [p.brand, p.model, p.name, p.spec]))))
            # Initials too, as people write them: "cdn" for CLUB DE NUIT.
            for part in (p.brand, p.model, p.name):
                parts = _words(part)
                if len(parts) >= 2:
                    words.add(''.join(w[0] for w in parts))
            catalogue.append((p, words))

    for row in rows:
        product = _find_barcode(by_barcode, row['ean']) if row['ean'] else None
        row['candidates'] = []
        if product:
            row['product'], row['match'] = product, 'ean'
            continue
        volume = _volume(row['name'])
        # Numbers stay ("Rouge 540"), except the volume, which is compared on
        # its own against the product's millilitres.
        wanted = [w for w in _words(row['name'])
                  if w not in STOPWORDS and not (w.isdigit() and volume and int(w) == volume)
                  and not re.fullmatch(r'\d+ml', w)]
        scored = []
        for p, words in catalogue:
            hits = 0.0
            for w in wanted:
                if w in words:
                    hits += 1
                elif len(w) >= 4 and get_close_matches(w, words, n=1, cutoff=0.8):
                    hits += 0.8          # a typo: "sabal" for SABAH
            if not hits:
                continue
            score = hits / max(len(wanted), 1)
            # the closer product wins over one with many words besides
            score += 0.1 * min(hits / max(len(words), 1), 1)
            # A volume that agrees helps; one that does not only slightly hurts -
            # people round (CDN Urban Man Elixir is 105 ml, written "100ml").
            if volume and p.volume_ml:
                score += 0.15 if p.volume_ml == volume else -0.1
            scored.append((score, hits, p))
        # ties go to the product that shares more of the words
        scored.sort(key=lambda s: (-s[0], -s[1]))
        row['candidates'] = [p for _, _, p in scored[:3]]
        best = (scored[0][0], scored[0][2]) if scored else None
        runner = scored[1][0] if len(scored) > 1 else -1
        if best and best[0] >= 0.75 and best[0] - runner >= 0.2:
            row['product'], row['match'] = best[1], 'name'
        else:
            row['product'], row['match'] = None, None
    return rows


def _find_barcode(by_barcode, ean):
    for key in (ean, ean.lstrip('0'), ean.zfill(13)):
        if key in by_barcode:
            return by_barcode[key]
    return None
