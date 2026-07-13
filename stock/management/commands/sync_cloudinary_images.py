"""Mirror product primary images to Cloudinary (dry-run by default)."""
from django.core.management.base import BaseCommand

from stock.models import Product
from stock.services import cloudinary_sync
from stock.services.cloudinary_client import CloudinaryClient


class Command(BaseCommand):
    help = "Mirror each product's primary image to Cloudinary (public_id=barcode)."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually upload (default: dry run).')
        parser.add_argument('--brand', default=None, help='Only products whose brand contains this text.')
        parser.add_argument('--barcode', default=None, help='Only the product with this exact barcode.')
        parser.add_argument('--limit', type=int, default=None, help='Process at most N products.')

    def handle(self, *args, **options):
        dry_run = not options['apply']
        qs = Product.objects.exclude(barcode='').filter(images__isnull=False).distinct()
        if options['brand']:
            qs = qs.filter(brand__icontains=options['brand'])
        if options['barcode']:
            qs = qs.filter(barcode=options['barcode'])
        if options['limit']:
            qs = qs[:options['limit']]

        client = CloudinaryClient()
        if not dry_run and not client.is_configured():
            self.stderr.write('Cloudinary is not configured (set CLOUDINARY_API_KEY / _SECRET).')
            return

        counts = {}
        for product in qs:
            code, detail = cloudinary_sync.sync_product_primary_image(
                product, client=client, dry_run=dry_run
            )
            counts[code] = counts.get(code, 0) + 1
            self.stdout.write(f'{product.barcode}: {code} {detail}')

        mode = 'DRY-RUN' if dry_run else 'APPLIED'
        self.stdout.write(f'Summary ({mode}): {counts}')
