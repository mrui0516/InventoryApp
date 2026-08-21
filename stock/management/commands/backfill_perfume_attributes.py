"""Seed the perfume attribute tables and fill them from existing product text.

Volume, strength, scent family and "inspired by" only ever existed as free text
— in the spec field, in the product name, or buried in the Portuguese
description. This reads that text once so the shop starts with populated
attributes instead of 238 blank forms, and can then correct them by hand.

Dry-run by default; pass --apply to write.
"""
import re

from django.core.management.base import BaseCommand
from django.db import transaction

from stock.models import Concentration, FragranceFamily, Inspiration, Product

CONCENTRATIONS = [
    # name, short, shopify tag (PT storefront), sort
    ('Eau de Toilette', 'EDT', 'Eau de Toilette', 10),
    ('Eau de Parfum', 'EDP', 'Eau de Parfum', 20),
    ('Extrait de Parfum', 'Extrait', 'Extrait de Parfum', 30),
    ('Eau de Cologne', 'EDC', 'Eau de Cologne', 40),
    ('Attar / Oil', 'Attar', '', 50),
    ('Body Mist', 'Mist', '', 60),
]

FAMILIES = [
    # name, shopify tag (PT), sort, keywords found in the description
    ('Floral', 'Floral', 10, r'floral|rosa\b|rose\b|jasmi|flor\b|peonia|pe[oó]nia|lily|l[ií]rio'),
    ('Oud', 'Oud', 20, r'\boud|oudh|agarwood|madeira de agar'),
    ('Woody', 'Amadeirado', 30, r'amadeirad|woody|sandal|s[aâ]ndalo|cedro|cedar|vetiver|patchouli'),
    ('Sweet / Gourmand', 'Doce', 40, r'gourmand|baunilha|vanilla|caramelo|caramel|chocolate|mel\b|honey|doce'),
    ('Fruity', 'Frutado', 50, r'frutad|fruity|anan[aá]s|pineapple|manga|mango|p[eê]ssego|peach|berry|framboesa|morango'),
    ('Fresh / Citrus', 'Fresco', 60, r'c[ií]tric|citrus|frescor|fresco|bergamota|bergamot|lim[aã]o|lemon|marinho|aqu[aá]tic'),
    ('Spicy', 'Especiado', 70, r'especiad|spicy|pimenta|pepper|canela|cinnamon|cardamom|a[çc]afr[aã]o|saffron'),
    ('Amber / Oriental', 'Oriental', 80, r'oriental|[aâ]mbar|amber|incenso|incense|resina'),
    ('Musk', 'Almiscarado', 90, r'almiscar|musk'),
    ('Leather / Tobacco', 'Couro', 100, r'couro|leather|tabaco|tobacco'),
]

# Descriptions are marketing prose and "inspirado" is used metaphorically far more
# often than for a real reference ("inspirado no jogo de luz e sombra"). Matching
# free text produced mostly garbage, so a reference is only accepted when a known
# house is named; anything else is left for manual entry.
KNOWN_HOUSES = [
    'Baccarat Rouge', 'Maison Francis Kurkdjian', 'Francis Kurkdjian', 'Tom Ford',
    'Yves Saint Laurent', 'Jean Paul Gaultier', 'Giorgio Armani', 'Dolce & Gabbana',
    'Paco Rabanne', 'Viktor & Rolf', 'Thierry Mugler', 'Carolina Herrera', 'Le Labo',
    'Parfums de Marly', 'Initio', 'Xerjoff', 'Amouage', 'Creed', 'Byredo', 'Diptyque',
    'Nishane', 'Mancera', 'Montale', 'Givenchy', 'Chanel', 'Dior', 'Guerlain',
    'Versace', 'Prada', 'Gucci', 'Valentino', 'Bvlgari', 'Burberry', 'Cartier',
    'Kilian', 'YSL', 'MFK', 'Azzaro', 'Trussardi', 'Roja',
]
_HOUSE_RE = '|'.join(sorted((re.escape(h) for h in KNOWN_HOUSES), key=len, reverse=True))
INSPIRED_RE = re.compile(
    r"inspirad[oa]s?\b[^.]{0,60}?\b(" + _HOUSE_RE + r")\b"
    r"((?:\s+[A-Z0-9][\w'&.-]*){0,4})",
    re.IGNORECASE)

VOLUME_RE = re.compile(r'(\d{2,4})\s*ml\b', re.IGNORECASE)
# strength written in the name/model/description
CONC_PATTERNS = [
    ('Extrait de Parfum', r'extrait'),
    ('Eau de Toilette', r'\bEDT\b|eau de toilette'),
    ('Eau de Cologne', r'\bEDC\b|eau de cologne'),
    ('Eau de Parfum', r'\bEDP\b|eau de parfum'),
]


class Command(BaseCommand):
    help = 'Seed perfume attribute tables and backfill them from existing product text.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='write changes (default: dry run)')

    def handle(self, *args, **opts):
        apply = opts['apply']
        with transaction.atomic():
            conc_by_name, fam_by_name = self._seed(apply)
            stats = self._backfill(apply, conc_by_name, fam_by_name)
            if not apply:
                transaction.set_rollback(True)

        self.stdout.write('')
        for key, value in stats.items():
            self.stdout.write(f'  {key:<28} {value}')
        self.stdout.write(self.style.SUCCESS('Applied.') if apply
                          else self.style.WARNING('Dry run — nothing written. Use --apply.'))

    # -- seeding -----------------------------------------------------------
    def _seed(self, apply):
        conc, fam = {}, {}
        for name, short, tag, order in CONCENTRATIONS:
            obj = Concentration.objects.filter(name=name).first()
            if obj is None and apply:
                obj = Concentration.objects.create(name=name, short=short, shopify_tag=tag, sort_order=order)
            conc[name] = obj
        for name, tag, order, _kw in FAMILIES:
            obj = FragranceFamily.objects.filter(name=name).first()
            if obj is None and apply:
                obj = FragranceFamily.objects.create(name=name, shopify_tag=tag, sort_order=order)
            fam[name] = obj
        return conc, fam

    # -- backfill ----------------------------------------------------------
    def _backfill(self, apply, conc_by_name, fam_by_name):
        stats = dict.fromkeys(
            ['perfumes scanned', 'volume set', 'strength set', 'families set',
             'inspiration set', 'name cleaned'], 0)
        perfumes = (Product.objects
                    .filter(category__name__icontains='perfum')
                    .prefetch_related('fragrance_families'))

        for product in perfumes:
            stats['perfumes scanned'] += 1
            text = ' '.join(filter(None, [product.name, product.model, product.description]))
            fields = []

            volume = self._volume(product) if product.volume_ml is None else None
            if volume:
                stats['volume set'] += 1
                if apply:
                    product.volume_ml = volume
                    fields.append('volume_ml')

            # Counted on detection, not on the lookup row existing, so a dry run
            # reports what --apply would actually do.
            strength = self._concentration_name(text) if product.concentration_id is None else None
            if strength:
                stats['strength set'] += 1
                if apply and conc_by_name.get(strength) is not None:
                    product.concentration = conc_by_name[strength]
                    fields.append('concentration')

            # a product literally named "EDP" carries its strength in the name
            if (product.name or '').strip().upper() in {'EDP', 'EDT', 'EDC', 'EXTRAIT'}:
                fallback = (product.model or '').strip()
                if fallback:
                    stats['name cleaned'] += 1
                    if apply:
                        product.name, product.model = fallback, ''
                        fields += ['name', 'model']

            inspiration = self._inspiration_phrase(product.description or '')                 if product.inspired_by_id is None else None
            if inspiration:
                stats['inspiration set'] += 1
                if apply:
                    house, name = inspiration
                    product.inspired_by = Inspiration.objects.get_or_create(house=house, name=name)[0]
                    fields.append('inspired_by')

            if apply and fields:
                product.save(update_fields=sorted(set(fields)))

            if not product.fragrance_families.exists():
                names = self._families(text)
                if names:
                    stats['families set'] += 1
                    if apply:
                        product.fragrance_families.set(
                            [fam_by_name[n] for n in names if fam_by_name.get(n) is not None])
        return stats

    def _volume(self, product):
        for source in (product.spec, product.name, product.model, product.description):
            match = VOLUME_RE.search(source or '')
            if match:
                value = int(match.group(1))
                if 1 <= value <= 1000:
                    return value
        return None

    def _concentration_name(self, text):
        for name, pattern in CONC_PATTERNS:      # most specific first
            if re.search(pattern, text, re.IGNORECASE):
                return name
        return None

    def _families(self, text):
        return [name for name, _tag, _o, keywords in FAMILIES
                if re.search(keywords, text, re.IGNORECASE)]

    def _inspiration_phrase(self, description):
        """``(house, name)`` of the fragrance this one is inspired by, or None.

        Only fires when a known house is named - see KNOWN_HOUSES.
        """
        match = INSPIRED_RE.search(description)
        if not match:
            return None
        house = ' '.join(match.group(1).split())
        stop = {"e", "de", "da", "do", "esta", "este", "com", "que", "uma", "um", "na", "no"}
        words = []
        for word in match.group(2).split():
            if word.lower() in stop:
                break
            words.append(word.rstrip(".,;:"))
        return house, ' '.join(words)

