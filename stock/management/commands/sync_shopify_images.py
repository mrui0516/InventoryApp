"""Push local product photos to matching Shopify products (by barcode == SKU).

Dry-run by default — prints what it *would* do and writes nothing. Add --apply
to actually upload. Only touches Shopify products that currently have no image
(use --overwrite to replace existing images).

Examples:
  python manage.py sync_shopify_images --brand Lattafa            # preview
  python manage.py sync_shopify_images --brand Lattafa --apply    # do it
  python manage.py sync_shopify_images --barcode 6290362349730 --apply
"""
from django.core.management.base import BaseCommand
from django.db.models import Sum

from stock.models import Product
from stock.services.shopify_client import ShopifyClient
from stock.services import shopify_sync

_LABEL = {
    shopify_sync.UPLOADED: 'uploaded',
    shopify_sync.WOULD_UPLOAD: 'would upload',
    shopify_sync.SKIP_HAS_IMAGE: 'skip (already has image)',
    shopify_sync.SKIP_NOT_IN_SHOPIFY: 'skip (not in Shopify)',
    shopify_sync.SKIP_NO_IMAGE: 'skip (no local image)',
    shopify_sync.SKIP_NO_BARCODE: 'skip (no barcode)',
    shopify_sync.ERROR: 'ERROR',
}


class Command(BaseCommand):
    help = 'Upload local product photos to matching Shopify products (by barcode = SKU).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually upload (default is a dry run).')
        parser.add_argument('--overwrite', action='store_true',
                            help='Replace images even if the Shopify product already has one.')
        parser.add_argument('--brand', default='', help='Only products whose brand contains this text.')
        parser.add_argument('--barcode', default='', help='Only the product with this exact barcode.')
        parser.add_argument('--in-stock', action='store_true', help='Only products with stock > 0.')
        parser.add_argument('--limit', type=int, default=0, help='Cap the number of products processed.')

    def handle(self, *args, **opts):
        client = ShopifyClient()
        if not client.is_configured():
            self.stderr.write(self.style.ERROR(
                'Shopify not configured. Set SHOPIFY_STORE_DOMAIN and SHOPIFY_ADMIN_TOKEN '
                'in the environment (see docs/SHOPIFY_SYNC.md).'))
            return

        dry_run = not opts['apply']
        qs = Product.objects.filter(images__isnull=False).distinct().order_by('brand', 'model', 'name')
        if opts['brand']:
            qs = qs.filter(brand__icontains=opts['brand'])
        if opts['barcode']:
            qs = qs.filter(barcode=opts['barcode'].strip())
        if opts['in_stock']:
            qs = qs.annotate(_stock=Sum('purchase__remaining')).filter(_stock__gt=0)
        if opts['limit'] and opts['limit'] > 0:
            qs = qs[:opts['limit']]

        mode = 'DRY RUN (no changes)' if dry_run else 'APPLYING'
        self.stdout.write(self.style.WARNING(
            f'{mode} — {qs.count()} local product(s) with images, matching by barcode=SKU'
            f'{" (overwrite)" if opts["overwrite"] else ""}.'))

        counts = {}
        for product in qs:
            code, detail = shopify_sync.sync_product_image(
                product, client, overwrite=opts['overwrite'], dry_run=dry_run,
            )
            counts[code] = counts.get(code, 0) + 1
            if code in (shopify_sync.UPLOADED, shopify_sync.WOULD_UPLOAD, shopify_sync.ERROR):
                style = self.style.SUCCESS if code != shopify_sync.ERROR else self.style.ERROR
                self.stdout.write(style(f'  [{_LABEL[code]}] {product.barcode} {product.display_name} — {detail}'))

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Summary:'))
        for code, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f'  {_LABEL.get(code, code)}: {n}')
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDry run only. Re-run with --apply to upload.'))
