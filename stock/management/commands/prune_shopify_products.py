"""Delete Shopify products that no longer exist in the app.

A Shopify product whose main variant SKU is not any app product's barcode is
listed for deletion. **Dangerous** — deletes live products — so it is dry-run by
default and prints every product it would delete. Products with no SKU, or whose
title is ambiguous (shared by more than one Shopify product), are left alone.

  python manage.py prune_shopify_products            # preview what would be deleted
  python manage.py prune_shopify_products --apply    # actually delete
"""
from django.core.management.base import BaseCommand

from stock.models import Product
from stock.services.shopify_client import ShopifyClient


class Command(BaseCommand):
    help = "Delete Shopify products whose SKU is no longer an app product barcode."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually delete (default is a dry run).')

    def handle(self, *args, **opts):
        client = ShopifyClient()
        if not client.is_configured():
            self.stderr.write(self.style.ERROR(
                'Shopify not configured. Set SHOPIFY_STORE_DOMAIN and SHOPIFY_ADMIN_TOKEN.'))
            return

        dry_run = not opts['apply']
        app_barcodes = {
            (b or '').strip()
            for b in Product.objects.exclude(barcode='').exclude(barcode__isnull=True)
                                    .values_list('barcode', flat=True)
            if (b or '').strip()
        }

        to_delete = []
        for title, rec in client.all_products_by_title().items():
            if rec is None:  # ambiguous title (more than one Shopify product) — skip
                continue
            sku = (rec.get('sku') or '').strip()
            if not sku:      # no SKU — can't match safely, leave it
                continue
            if sku not in app_barcodes:
                to_delete.append((rec['product_id'], title, sku))

        mode = 'DRY RUN (no changes)' if dry_run else 'DELETING'
        self.stdout.write(self.style.WARNING(
            f'{mode} — {len(to_delete)} Shopify product(s) not in the app.'))
        errors = 0
        for product_id, title, sku in to_delete:
            self.stdout.write(self.style.ERROR(f'  delete: {sku}  {title}'))
            if not dry_run:
                try:
                    client.delete_product(product_id)
                except Exception as exc:
                    errors += 1
                    self.stdout.write(self.style.ERROR(f'    failed: {exc}'))

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Summary:'))
        self.stdout.write(f'  to delete: {len(to_delete)}')
        if errors:
            self.stdout.write(self.style.ERROR(f'  errors: {errors}'))
        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\nDry run only. Review the list above carefully, then re-run with --apply.'))
