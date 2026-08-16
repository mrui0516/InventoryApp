"""Re-key decant variant SKUs to match their 100ml.

Barcodes were corrected on the 100ml variant, but the 10ml/5ml decant SKUs still
carry the old barcode base (e.g. 100ml sku=6290360590745 but decant
sku=1213454656777-10ML). The inventory sync then can't find those decants, so
they never get tracked/DENY and stay buyable at quantity 0.

For each product this finds the full-bottle variant (the SKU without a -10ML /
-5ML suffix) and rewrites each decant's SKU to ``<full sku><suffix>``, and makes
it tracked + DENY. Dry-run by default.

  python manage.py fix_decant_skus            # preview
  python manage.py fix_decant_skus --apply    # write
"""
from django.core.management.base import BaseCommand

from stock.services.shopify_client import ShopifyClient

SUFFIXES = ('-10ML', '-5ML')


class Command(BaseCommand):
    help = "Re-key decant variant SKUs to <100ml sku>-10ML/-5ML (+ tracked/DENY)."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually write (default is a dry run).')

    def handle(self, *args, **opts):
        client = ShopifyClient()
        if not client.is_configured():
            self.stderr.write(self.style.ERROR(
                'Shopify not configured. Set SHOPIFY_STORE_DOMAIN and SHOPIFY_ADMIN_TOKEN.'))
            return

        dry_run = not opts['apply']
        mode = 'DRY RUN (no changes)' if dry_run else 'APPLYING'
        self.stdout.write(self.style.WARNING(f'{mode} — re-keying mismatched decant SKUs.'))

        fixed = errors = skipped = 0
        for prod in client.all_products_full_variants():
            variants = prod['variants']
            full = [v for v in variants
                    if v['sku'] and not v['sku'].endswith(SUFFIXES)]
            if len(full) != 1:
                skipped += 1  # can't determine the base SKU unambiguously
                continue
            base = full[0]['sku']
            for v in variants:
                sku = v['sku']
                for suffix in SUFFIXES:
                    if sku.endswith(suffix):
                        expected = base + suffix
                        if sku != expected:
                            self.stdout.write(self.style.SUCCESS(
                                f"  {prod['title'][:40]}: {sku} -> {expected}"))
                            if not dry_run:
                                try:
                                    client.fix_variant_sku(prod['product_id'], v['id'], expected)
                                except Exception as exc:
                                    errors += 1
                                    self.stdout.write(self.style.ERROR(f'    failed: {exc}'))
                                    break
                            fixed += 1
                        break

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Summary:'))
        self.stdout.write(f'  decant SKUs re-keyed: {fixed}')
        self.stdout.write(f'  products skipped (ambiguous base): {skipped}')
        if errors:
            self.stdout.write(self.style.ERROR(f'  errors: {errors}'))
        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\nDry run only. Re-run with --apply, then run sync_shopify_inventory.'))
