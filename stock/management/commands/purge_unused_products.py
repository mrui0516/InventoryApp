"""Delete products that were only ever typed in and never used.

The shop's electronics catalogue was entered long ago and never traded: no
purchases, no sales, no stock, no images. Those rows are being rebuilt properly,
so they are removed rather than archived - archiving would leave them cluttering
every lookup forever.

Refuses to touch anything that carries business data. Dry-run by default.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from stock.models import Product


class Command(BaseCommand):
    help = 'Delete never-used products (no purchase, sale, stock, image or pending item).'

    def add_arguments(self, parser):
        parser.add_argument('--category', default='',
                            help='only this category (substring match), e.g. Accessories')
        parser.add_argument('--exclude-category', default='',
                            help='skip this category (substring match)')
        parser.add_argument('--apply', action='store_true', help='write changes (default: dry run)')

    def handle(self, *args, **opts):
        qs = Product.all_objects.all()
        if opts['category']:
            qs = qs.filter(category__name__icontains=opts['category'])
        if opts['exclude_category']:
            qs = qs.exclude(category__name__icontains=opts['exclude_category'])

        deletable, kept = [], 0
        for product in qs.prefetch_related('images'):
            # every relation that would make this row part of the books
            used = (product.purchase_set.exists()
                    or product.sale_set.exists()
                    or product.images.exists()
                    or product.stock_adjustment_logs.exists()
                    or product.inboundpendingitem_set.exists())
            if used:
                kept += 1
            else:
                deletable.append(product)

        self.stdout.write(f'  candidates            {qs.count()}')
        self.stdout.write(f'  kept (has history)    {kept}')
        self.stdout.write(f'  deletable             {len(deletable)}')
        for product in deletable[:10]:
            self.stdout.write(f'    - {product.barcode}  {product.display_name[:52]}')
        if len(deletable) > 10:
            self.stdout.write(f'    ... and {len(deletable) - 10} more')

        if not opts['apply']:
            self.stdout.write(self.style.WARNING('Dry run - nothing deleted. Use --apply.'))
            return

        with transaction.atomic():
            count = 0
            for product in deletable:
                product.delete()
                count += 1
        self.stdout.write(self.style.SUCCESS(f'Deleted {count} unused product(s).'))
