"""Push corrected EANs (barcodes) from the app to Shopify.

Barcodes were re-keyed in the app, so Shopify's SKU/barcode no longer match by
barcode. This matches each app product to its Shopify product by **title** (the
stable key — titles didn't change) and, where the Shopify SKU/barcode differs,
updates the Shopify variant's SKU + barcode to the app's current barcode.

All Shopify products are fetched once (paginated) and matched locally, so the
dry run makes only a few API calls; only actual updates (--apply) write per
product. Dry-run by default.

  python manage.py sync_shopify_barcodes                  # preview every change
  python manage.py sync_shopify_barcodes --apply          # write to Shopify
  python manage.py sync_shopify_barcodes --brand Lattafa  # scope by brand
"""
from django.core.management.base import BaseCommand

from stock.models import Product
from stock.services.shopify_client import ShopifyClient, ShopifyError
from stock.services.shopify_sync import _shopify_title
from stock.services.shopify_sync import shopify_syncable


class Command(BaseCommand):
    help = "Push corrected barcodes (EANs) to Shopify, matching by product title."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually write to Shopify (default is a dry run).')
        parser.add_argument('--brand', default='',
                            help='Only products whose brand contains this text.')
        parser.add_argument('--limit', type=int, default=0,
                            help='Cap the number of products processed.')

    def handle(self, *args, **opts):
        client = ShopifyClient()
        if not client.is_configured():
            self.stderr.write(self.style.ERROR(
                'Shopify not configured. Set SHOPIFY_STORE_DOMAIN and SHOPIFY_ADMIN_TOKEN.'))
            return

        dry_run = not opts['apply']
        qs = (shopify_syncable(Product.objects)
              .exclude(barcode='').exclude(barcode__isnull=True)
              .order_by('brand', 'name'))
        if opts['brand']:
            qs = qs.filter(brand__icontains=opts['brand'])
        if opts['limit'] and opts['limit'] > 0:
            qs = qs[:opts['limit']]

        mode = 'DRY RUN (no changes)' if dry_run else 'APPLYING'
        self.stdout.write(self.style.WARNING(
            f'{mode} — matching {qs.count()} product(s) to Shopify by title.'))

        by_title = client.all_products_by_title()

        changed = unchanged = not_found = errors = 0
        for product in qs:
            barcode = (product.barcode or '').strip()
            title = _shopify_title(product)
            match = by_title.get(title)
            if not match:  # missing OR ambiguous (duplicate title) -> skip
                not_found += 1
                continue
            if match['sku'] == barcode and match['barcode'] == barcode:
                unchanged += 1
                continue
            changed += 1
            self.stdout.write(self.style.SUCCESS(
                f'  {title}: sku={match["sku"]} barcode={match["barcode"]} -> {barcode}'))
            if not dry_run:
                try:
                    client.update_variant_barcode_sku(
                        match['product_id'], match['variant_id'], barcode, barcode)
                except ShopifyError as exc:
                    errors += 1
                    self.stdout.write(self.style.ERROR(f'    write failed: {exc}'))

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Summary:'))
        self.stdout.write(f'  to update: {changed}')
        self.stdout.write(f'  already correct: {unchanged}')
        self.stdout.write(f'  not on Shopify / ambiguous (skipped): {not_found}')
        if errors:
            self.stdout.write(self.style.ERROR(f'  errors: {errors}'))
        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\nDry run only. Re-run with --apply to write to Shopify.'))
