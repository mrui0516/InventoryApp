"""Daily Shopify storefront automation (run from a PA Scheduled Task):

1. Hide sold-out perfumes: on-hand N == 0 -> product DRAFT; N >= 1 -> ACTIVE.
2. "Novidades": set the collection to the 20 newest perfumes (newest first).
3. "O Mais Vendido do Mes": top 5 perfumes by units sold this calendar month.

Matches by barcode = SKU. Dry-run by default; --apply to write.

  python manage.py sync_shopify_storefront            # preview
  python manage.py sync_shopify_storefront --apply    # write to Shopify
"""
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from stock.models import Product, Sale
from stock.services.shopify_client import ShopifyClient

NOVIDADES_TITLE = 'Novidades'
TOP_MONTH_TITLE = 'O Mais Vendido do Mês'
NOVIDADES_COUNT = 20
TOP_COUNT = 5


class Command(BaseCommand):
    help = 'Daily Shopify storefront sync: hide sold-out, Novidades (newest 20), Top 5 this month.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually write to Shopify (default is a dry run).')

    def handle(self, *args, **opts):
        client = ShopifyClient()
        if not client.is_configured():
            self.stderr.write(self.style.ERROR(
                'Shopify not configured. Set SHOPIFY_STORE_DOMAIN and SHOPIFY_ADMIN_TOKEN.'))
            return

        dry_run = not opts['apply']
        mode = 'DRY RUN (no changes)' if dry_run else 'APPLYING'
        self.stdout.write(self.style.WARNING(f'{mode} — Shopify storefront sync.'))

        by_sku = client.all_variants_by_sku()
        perfumes = (Product.objects
                    .filter(category__name__icontains='perfum')
                    .exclude(barcode='').exclude(barcode__isnull=True))

        def gid(product):
            rec = by_sku.get((product.barcode or '').strip())
            return rec['product_id'] if rec else None

        # --- 1) hide sold-out / show restocked ------------------------------
        drafted = activated = 0
        for product in perfumes:
            rec = by_sku.get((product.barcode or '').strip())
            if not rec:
                continue
            try:
                on_hand = int(product.total_stock() or 0)
            except Exception:
                on_hand = 0
            desired = 'DRAFT' if on_hand == 0 else 'ACTIVE'
            if rec.get('status') and rec['status'] != desired:
                if not dry_run:
                    client.set_product_status(rec['product_id'], desired)
                if desired == 'DRAFT':
                    drafted += 1
                    self.stdout.write(self.style.WARNING(f'  hide (sold out): {product.barcode} {product.display_name}'))
                else:
                    activated += 1
                    self.stdout.write(self.style.SUCCESS(f'  show (restocked): {product.barcode} {product.display_name}'))

        # --- 2) Novidades: newest 20 perfumes on Shopify --------------------
        nov_gids = []
        for product in perfumes.order_by('-created_at', '-id'):
            g = gid(product)
            if g and g not in nov_gids:
                nov_gids.append(g)
            if len(nov_gids) >= NOVIDADES_COUNT:
                break
        nov_id = client.find_collection_by_title(NOVIDADES_TITLE)
        if not nov_id:
            self.stdout.write(self.style.ERROR(f"  collection '{NOVIDADES_TITLE}' not found — skipped."))
        else:
            self.stdout.write(f'  Novidades -> {len(nov_gids)} newest perfume(s).')
            if not dry_run:
                client.set_collection_products(nov_id, nov_gids)

        # --- 3) Top 5 best sellers this calendar month ----------------------
        now = timezone.localtime()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        top_rows = (Sale.objects
                    .filter(product__category__name__icontains='perfum')
                    .annotate(business_at=Coalesce('order__created_at', 'date'))
                    .filter(business_at__gte=month_start)
                    .values('product')
                    .annotate(sold=Sum('quantity'))
                    .order_by('-sold')[:TOP_COUNT * 2])
        top_gids = []
        for row in top_rows:
            product = Product.objects.filter(pk=row['product']).first()
            if not product:
                continue
            g = gid(product)
            if g and g not in top_gids:
                top_gids.append(g)
            if len(top_gids) >= TOP_COUNT:
                break
        top_id = client.find_collection_by_title(TOP_MONTH_TITLE)
        if not top_id:
            if dry_run:
                self.stdout.write(f"  '{TOP_MONTH_TITLE}' would be created.")
            else:
                top_id = client.create_collection(TOP_MONTH_TITLE)
                self.stdout.write(self.style.SUCCESS(f"  created collection '{TOP_MONTH_TITLE}'."))
        self.stdout.write(f'  Top 5 this month -> {len(top_gids)} product(s).')
        if top_id and not dry_run:
            client.set_collection_products(top_id, top_gids)

        # --- summary --------------------------------------------------------
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Summary:'))
        self.stdout.write(f'  hidden (sold out): {drafted}')
        self.stdout.write(f'  shown (restocked): {activated}')
        self.stdout.write(f'  Novidades: {len(nov_gids)} | Top month: {len(top_gids)}')
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDry run only. Re-run with --apply to write.'))