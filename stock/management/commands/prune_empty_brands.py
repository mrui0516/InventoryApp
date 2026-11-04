"""Delete every brand and line with no product left.

    manage.py prune_empty_brands            # dry run: lists them
    manage.py prune_empty_brands --apply

Since 2026-11-05 this happens by itself when a product changes brand or line,
is deleted or is archived; the command clears what was left before that, such
as the old accessory brands. See services/catalog_prune.py for what counts.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from stock.services.catalog_prune import empty_brands, empty_series, prune


class Command(BaseCommand):
    help = 'Delete brands and lines with no product (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Delete them.')

    def handle(self, *args, apply=False, **options):
        out = self.stdout.write
        lines = list(empty_series().select_related('brand'))
        brands = list(empty_brands())
        by_brand = {}
        for line in lines:
            by_brand.setdefault(line.brand, []).append(line.name)

        out(f'Brands with no product: {len(brands)}')
        for brand in sorted(brands, key=lambda b: b.name.lower()):
            names = by_brand.pop(brand, [])
            out(f'  {brand.name}' + (f'  ({len(names)} lines)' if names else ''))
        out(f'Lines with no product, brand stays: {sum(len(v) for v in by_brand.values())}')
        for brand, names in sorted(by_brand.items(), key=lambda item: item[0].name.lower()):
            out(f'  {brand.name}: {", ".join(sorted(names, key=str.lower))}')

        if not apply:
            out(self.style.WARNING('Dry run - nothing changed. Run again with --apply to do it.'))
            return
        with transaction.atomic():
            brand_count, line_count = prune()
        out(self.style.SUCCESS(f'Deleted {brand_count} brands and {line_count} lines.'))
