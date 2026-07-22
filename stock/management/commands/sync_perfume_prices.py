from django.core.management.base import BaseCommand

from stock.models import Product
from stock.services.pricing import PERFUME_CATEGORY_NAME, sync_perfume_price


class Command(BaseCommand):
    help = "Recompute wholesale/retail for all Perfumes from their current FIFO cost."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="Report without writing.")

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        qs = Product.objects.filter(category__name__iexact=PERFUME_CATEGORY_NAME).select_related('category')
        updated = skipped = 0
        for product in qs:
            if dry_run:
                # Peek without writing: replicate the skip conditions loosely.
                self.stdout.write(f"[dry-run] would sync {product.barcode} {product.name}")
                continue
            if sync_perfume_price(product):
                updated += 1
            else:
                skipped += 1
        self.stdout.write(self.style.SUCCESS(
            f"Perfume pricing: {updated} updated, {skipped} unchanged/locked/no-cost"
            + (" (dry-run: nothing written)" if dry_run else "")
        ))
