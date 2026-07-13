"""Create missing products in Shopify from the app (variant / price / SKU /
barcode / cost / inventory / SEO / image), matching by barcode = SKU.

Products that already exist in Shopify get their image attached (if missing);
products that don't exist are created. Dry-run by default; --apply to write.

Examples:
  python manage.py sync_shopify_products --brand Lattafa                 # preview
  python manage.py sync_shopify_products --brand Lattafa --apply         # create (DRAFT)
  python manage.py sync_shopify_products --brand Lattafa --status active --apply
"""
from django.core.management.base import BaseCommand
from django.db.models import Sum

from stock.models import Product
from stock.services.shopify_client import ShopifyClient
from stock.services import shopify_sync

_LABEL = {
    shopify_sync.CREATED: 'created',
    shopify_sync.WOULD_CREATE: 'would create',
    shopify_sync.UPLOADED: 'image added',
    shopify_sync.WOULD_UPLOAD: 'would add image',
    shopify_sync.SKIP_HAS_IMAGE: 'skip (already complete)',
    shopify_sync.SKIP_NO_IMAGE: 'created/updated without image (no local file)',
    shopify_sync.SKIP_NO_BARCODE: 'skip (no barcode)',
    shopify_sync.ERROR: 'ERROR',
}


class Command(BaseCommand):
    help = 'Create missing products in Shopify from the app (by barcode = SKU).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually write (default is a dry run).')
        parser.add_argument('--status', choices=['active', 'draft'], default='draft',
                            help='Status for newly created products (default draft).')
        parser.add_argument('--overwrite-image', action='store_true',
                            help='Replace images on products that already exist.')
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
        status = opts['status'].upper()
        qs = Product.objects.all().order_by('brand', 'model', 'name')
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
            f'{mode} — {qs.count()} product(s); missing ones created as {status}, matching by barcode=SKU.'))

        counts = {}
        for product in qs:
            code, detail = shopify_sync.sync_product(
                product, client,
                create_missing=True,
                overwrite_image=opts['overwrite_image'],
                dry_run=dry_run,
                status=status,
            )
            counts[code] = counts.get(code, 0) + 1
            if code in (shopify_sync.CREATED, shopify_sync.WOULD_CREATE, shopify_sync.ERROR):
                style = self.style.SUCCESS if code != shopify_sync.ERROR else self.style.ERROR
                self.stdout.write(style(f'  [{_LABEL[code]}] {product.barcode} {product.display_name} — {detail}'))

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Summary:'))
        for code, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f'  {_LABEL.get(code, code)}: {n}')
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDry run only. Re-run with --apply to write.'))
