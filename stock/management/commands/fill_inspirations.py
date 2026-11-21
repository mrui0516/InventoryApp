"""Fill "Inspired by" on perfumes from a researched list (2026-11-21).

The list is ``stock/data/inspirations.csv``: ``brand,line,name,inspired_by``,
one row per scent. A row fills every active perfume of that brand, line and
name (any size) whose own field is empty; a field already filled is never
touched, and a version with "Same scent as" reads its perfume instead.

A size left empty while another size of the same scent has a value takes that
value too, so nothing needs looking up twice.

Dry-run by default; pass --apply to write (after ``backup_db``).

    python manage.py fill_inspirations            # what it would do
    python manage.py fill_inspirations --apply    # write it
"""
import csv
from collections import Counter, defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from stock.models import Product

DEFAULT_FILE = Path(__file__).resolve().parents[2] / 'data' / 'inspirations.csv'
MAX_LENGTH = Product._meta.get_field('inspired_by_text').max_length


def scent_key(brand, line, name):
    return tuple((part or '').strip().lower() for part in (brand, line, name))


def read_list(path):
    with open(path, encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    missing = {'brand', 'line', 'name', 'inspired_by'} - set(rows[0].keys() if rows else [])
    if missing:
        raise CommandError(f'{path} lacks the column(s): {", ".join(sorted(missing))}')
    found = {}
    for row in rows:
        value = (row['inspired_by'] or '').strip()
        if value:
            found[scent_key(row['brand'], row['line'], row['name'])] = value[:MAX_LENGTH]
    return found


class Command(BaseCommand):
    help = 'Fill empty "Inspired by" fields on perfumes from stock/data/inspirations.csv.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='write changes (default: dry run)')
        parser.add_argument('--file', default=str(DEFAULT_FILE), help='the researched list (CSV)')
        parser.add_argument('--show', type=int, default=20, help='how many products to list per section')

    def handle(self, *args, **opts):
        listed = read_list(opts['file'])
        groups = defaultdict(list)
        for product in Product.objects.filter(product_type_id=Product.PERFUME, scent_source__isnull=True):
            groups[scent_key(product.brand, product.model, product.name)].append(product)

        planned, from_list, from_size, unmatched = [], 0, 0, []
        for key, products in groups.items():
            empty = [p for p in products if not (p.inspired_by_text or '').strip()]
            if not empty:
                continue
            sizes = Counter(p.inspired_by_text.strip() for p in products if (p.inspired_by_text or '').strip())
            value = sizes.most_common(1)[0][0] if sizes else listed.get(key)
            if not value:
                unmatched.append(empty[0])
                continue
            if sizes:
                from_size += len(empty)
            else:
                from_list += len(empty)
            planned += [(p, value) for p in empty]

        unused = sorted(set(listed) - set(groups))
        if opts['apply']:
            with transaction.atomic():
                for product, value in planned:
                    Product.all_objects.filter(pk=product.pk).update(inspired_by_text=value)

        show = opts['show']
        self.stdout.write(f'Fill {len(planned)} product(s): {from_list} from the list, {from_size} from another size.')
        for product, value in planned[:show]:
            self.stdout.write(f'  {product.barcode}  {product.display_name}  ->  {value}')
        self.stdout.write(f'Still empty, not on the list: {len(unmatched)}')
        for product in unmatched[:show]:
            self.stdout.write(f'  {product.barcode}  {product.display_name}')
        self.stdout.write(f'List rows matching no product: {len(unused)}')
        for key in unused[:show]:
            self.stdout.write(f'  {" | ".join(key)}')
        self.stdout.write(self.style.SUCCESS('Applied.') if opts['apply']
                          else self.style.WARNING('Dry run - nothing written. Use --apply.'))
