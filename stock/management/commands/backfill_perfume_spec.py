"""Set Specification (volume) = '100ml' for perfumes that don't have one.

Perfume = category name contains 'perfum'. Only blank/empty specs are filled;
existing specs (90ml, 105ml, 50ml, ...) are left untouched. Dry-run by default.

  python manage.py backfill_perfume_spec            # preview
  python manage.py backfill_perfume_spec --apply    # write
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from stock.models import Product

DEFAULT_SPEC = '100ml'


class Command(BaseCommand):
    help = "Fill blank Specification with '100ml' for perfumes."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually write (default is a dry run).')

    def handle(self, *args, **opts):
        dry_run = not opts['apply']
        qs = (Product.objects
              .filter(category__name__icontains='perfum')
              .filter(Q(spec__isnull=True) | Q(spec='')))
        total = qs.count()
        self.stdout.write(self.style.WARNING(
            f"{'DRY RUN (no changes)' if dry_run else 'APPLYING'} — "
            f"{total} perfume(s) missing Specification -> '{DEFAULT_SPEC}'."))
        for product in qs.order_by('brand', 'name')[:60]:
            self.stdout.write(f'  {product.barcode} {product.display_name}')
        if total > 60:
            self.stdout.write(f'  ... and {total - 60} more')

        if dry_run:
            self.stdout.write(self.style.WARNING('\nDry run only. Re-run with --apply to write.'))
        else:
            updated = qs.update(spec=DEFAULT_SPEC)
            self.stdout.write(self.style.SUCCESS(f'\nUpdated {updated} product(s).'))
