"""Push price + inventory from the app to Shopify (the app is authoritative).

Matches by barcode = SKU. For each matched product, sets the Shopify variant's
price to the app's price and its available inventory to the app's on-hand
(Σ purchase.remaining). All Shopify variants are fetched once, so the dry run is
a few API calls; only real changes write. Dry-run by default.

  python manage.py sync_shopify_inventory                  # preview
  python manage.py sync_shopify_inventory --apply          # write price + inventory
  python manage.py sync_shopify_inventory --inventory-only --apply
  python manage.py sync_shopify_inventory --price-only --brand Lattafa --apply
"""
from django.core.management.base import BaseCommand

from stock.models import Product
from stock.services import shopify_sync
from stock.services.shopify_client import ShopifyClient

_LABEL = {
    shopify_sync.INV_WOULD_UPDATE: 'would update',
    shopify_sync.INV_UPDATED: 'updated',
    shopify_sync.INV_UNCHANGED: 'already correct',
    shopify_sync.INV_NOT_IN_SHOPIFY: 'not on Shopify',
    shopify_sync.INV_NO_BARCODE: 'no barcode',
    shopify_sync.INV_ERROR: 'ERROR',
}


class Command(BaseCommand):
    help = 'Push price + inventory to Shopify (app authoritative), matching by barcode=SKU.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually write to Shopify (default is a dry run).')
        parser.add_argument('--brand', default='', help='Only products whose brand contains this text.')
        parser.add_argument('--limit', type=int, default=0, help='Cap the number of products processed.')
        parser.add_argument('--price-only', action='store_true', help='Sync price, not inventory.')
        parser.add_argument('--inventory-only', action='store_true', help='Sync inventory, not price.')

    def handle(self, *args, **opts):
        client = ShopifyClient()
        if not client.is_configured():
            self.stderr.write(self.style.ERROR(
                'Shopify not configured. Set SHOPIFY_STORE_DOMAIN and SHOPIFY_ADMIN_TOKEN.'))
            return

        do_price = not opts['inventory_only']
        do_inv = not opts['price_only']
        dry_run = not opts['apply']

        qs = (Product.objects
              .exclude(barcode='').exclude(barcode__isnull=True)
              .order_by('brand', 'name'))
        if opts['brand']:
            qs = qs.filter(brand__icontains=opts['brand'])
        if opts['limit'] and opts['limit'] > 0:
            qs = qs[:opts['limit']]

        mode = 'DRY RUN (no changes)' if dry_run else 'APPLYING'
        self.stdout.write(self.style.WARNING(
            f'{mode} — {qs.count()} product(s); price={do_price} inventory={do_inv}, by barcode=SKU.'))

        by_sku = client.all_variants_by_sku()
        location_id = client.get_location_id() if (do_inv and not dry_run) else None

        counts = {}
        for product in qs:
            barcode = (product.barcode or '').strip()
            shop_variants = {sku: by_sku[sku]
                             for sku in (barcode, barcode + '-10ML', barcode + '-5ML')
                             if sku in by_sku}
            if not shop_variants:
                counts[shopify_sync.INV_NOT_IN_SHOPIFY] = counts.get(shopify_sync.INV_NOT_IN_SHOPIFY, 0) + 1
                continue
            code, detail = shopify_sync.sync_product_price_inventory(
                product, client, do_price=do_price, do_inventory=do_inv,
                dry_run=dry_run, shop_variants=shop_variants, location_id=location_id)
            counts[code] = counts.get(code, 0) + 1
            if code in (shopify_sync.INV_WOULD_UPDATE, shopify_sync.INV_UPDATED, shopify_sync.INV_ERROR):
                style = self.style.ERROR if code == shopify_sync.INV_ERROR else self.style.SUCCESS
                self.stdout.write(style(f'  {product.barcode} {product.display_name}: {detail}'))

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Summary:'))
        for code, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f'  {_LABEL.get(code, code)}: {n}')
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDry run only. Re-run with --apply to write to Shopify.'))
