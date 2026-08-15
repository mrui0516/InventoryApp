"""Shopify sync for every perfume, matched by barcode = SKU. Dry-run by default.

Fast by default: for products already on Shopify it only pushes changed price +
decant-aware inventory and adds them to their brand's manual collection — it uses
the GID from one bulk fetch, so there's no per-product lookup. Opt-ins:
  --create   also create perfumes missing on Shopify (with category/description/image)
  --full     also re-push descriptions for existing products (slower)

"Perfume" = a product whose category name contains "perfum". The product-list
"Sync all perfumes to Shopify" button runs this (fast, no --create/--full).

  python manage.py sync_shopify_perfumes                    # preview
  python manage.py sync_shopify_perfumes --apply            # inventory + collections
  python manage.py sync_shopify_perfumes --apply --create   # + create missing
  python manage.py sync_shopify_perfumes --apply --full     # + re-push descriptions
"""
from django.core.management.base import BaseCommand

from stock.models import Product
from stock.services import shopify_sync
from stock.services.shopify_client import ShopifyClient


class Command(BaseCommand):
    help = 'Full Shopify sync for all perfumes (create/description/image + price + decant inventory).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually write to Shopify (default is a dry run).')
        parser.add_argument('--limit', type=int, default=0, help='Cap the number of products.')
        parser.add_argument('--create', action='store_true',
                            help='Also create perfumes that are missing on Shopify.')
        parser.add_argument('--full', action='store_true',
                            help='Also re-push descriptions for existing products (slower).')

    def handle(self, *args, **opts):
        client = ShopifyClient()
        if not client.is_configured():
            self.stderr.write(self.style.ERROR(
                'Shopify not configured. Set SHOPIFY_STORE_DOMAIN and SHOPIFY_ADMIN_TOKEN.'))
            return

        dry_run = not opts['apply']
        qs = (Product.objects
              .filter(category__name__icontains='perfum')
              .exclude(barcode='').exclude(barcode__isnull=True)
              .order_by('brand', 'name'))
        if opts['limit'] and opts['limit'] > 0:
            qs = qs[:opts['limit']]

        mode = 'DRY RUN (no changes)' if dry_run else 'APPLYING'
        total = qs.count()
        self.stdout.write(self.style.WARNING(f'{mode} — {total} perfume(s), by barcode=SKU.'))

        by_sku = client.all_variants_by_sku()
        location_id = client.get_location_id() if not dry_run else None

        do_create = opts['create']
        do_full = opts['full']
        created = updated = unchanged = missing = descr = errors = 0
        for i, product in enumerate(qs, 1):
            barcode = (product.barcode or '').strip()
            shop_variants = {sku: by_sku[sku]
                             for sku in (barcode, barcode + '-10ML', barcode + '-5ML')
                             if sku in by_sku}

            if not shop_variants:
                # not on Shopify: create only when asked (avoids listing decants/mixes)
                if not do_create:
                    missing += 1
                    continue
                code, detail = shopify_sync.sync_product(
                    product, client, create_missing=True, status='ACTIVE', dry_run=dry_run)
                if code == shopify_sync.CREATED:
                    created += 1
                    self.stdout.write(self.style.SUCCESS(f'  [{i}/{total}] created {barcode}'))
                elif code == shopify_sync.ERROR:
                    errors += 1
                    self.stdout.write(self.style.ERROR(f'  [{i}/{total}] {barcode} create: {detail}'))
                continue

            # exists: we already have its GID from by_sku — no per-product lookup.
            if do_full and not dry_run:
                main = shop_variants.get(barcode)
                desc = shopify_sync._shopify_description_html(product)
                if main and desc:
                    try:
                        client.update_product_description(main['product_id'], desc)
                        descr += 1
                    except Exception as exc:
                        errors += 1
                        self.stdout.write(self.style.ERROR(f'  [{i}/{total}] {barcode} desc: {exc}'))

            icode, idetail = shopify_sync.sync_product_price_inventory(
                product, client, do_price=True, do_inventory=True,
                dry_run=dry_run, shop_variants=shop_variants, location_id=location_id)
            if icode in (shopify_sync.INV_UPDATED, shopify_sync.INV_WOULD_UPDATE):
                updated += 1
                self.stdout.write(self.style.SUCCESS(f'  [{i}/{total}] {barcode}: {idetail}'))
            elif icode == shopify_sync.INV_UNCHANGED:
                unchanged += 1
            elif icode == shopify_sync.INV_ERROR:
                errors += 1
                self.stdout.write(self.style.ERROR(f'  [{i}/{total}] {barcode} inv: {idetail}'))

        # --- add each perfume to its brand's manual collection --------------
        # (smart brand collections auto-join by vendor; only manual ones need it)
        manual_by_brand = {}
        for col in client.all_collections():
            if col['smart']:
                continue
            prefix = col['title'].split(' - ')[0].strip().lower()
            manual_by_brand.setdefault(prefix, col['id'])
        coll_groups = {}
        for product in qs:
            rec = by_sku.get((product.barcode or '').strip())
            if not rec:
                continue
            coll_id = manual_by_brand.get((product.brand or '').strip().lower())
            if coll_id:
                coll_groups.setdefault(coll_id, []).append(rec['product_id'])
        collection_adds = 0
        for coll_id, gids in coll_groups.items():
            gids = list(dict.fromkeys(gids))
            if not dry_run:
                try:
                    client.collection_add_products(coll_id, gids)
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f'  collection add failed: {exc}'))
                    continue
            collection_adds += len(gids)

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Summary:'))
        self.stdout.write(f'  price/inventory changed: {updated}')
        self.stdout.write(f'  already correct: {unchanged}')
        self.stdout.write(f'  brand-collection memberships: {collection_adds}')
        if do_full:
            self.stdout.write(f'  descriptions re-pushed: {descr}')
        if do_create:
            self.stdout.write(f'  created: {created}')
        else:
            self.stdout.write(f'  not on Shopify (use --create to add): {missing}')
        if errors:
            self.stdout.write(self.style.ERROR(f'  errors: {errors}'))
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDry run only. Re-run with --apply to write.'))
