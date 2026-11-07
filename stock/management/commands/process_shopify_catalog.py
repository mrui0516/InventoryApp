"""Durable product publication and collection worker."""
import time

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F
from django.utils import timezone

from stock.models import Product, ShopifyCatalogState
from stock.services.shopify_catalog_client import CatalogClient
from stock.services.shopify_catalog_queue import enabled, enqueue
from stock.services.shopify_catalog_worker import process_catalog
from stock.services.shopify_storefront import backfill_restocks
from stock.services.shopify_sync import shopify_syncable


class Command(BaseCommand):
    help = 'Publish ready new products and refresh Novidades/Restock, with durable retries.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--all', action='store_true', help='Queue existing eligible products once.')
        parser.add_argument('--backfill-restocks', action='store_true', help='Infer only reconciled, unadjusted historical restocks.')
        parser.add_argument('--watch', action='store_true')
        parser.add_argument('--interval', type=int, default=60)
        parser.add_argument('--limit', type=int, default=25)

    def handle(self, *args, **options):
        if not options['apply']:
            count = ShopifyCatalogState.objects.filter(requested_revision__gt=F('synced_revision')).count()
            self.stdout.write(f'DRY RUN: {count} pending product jobs; no writes or API calls.')
            return
        if not enabled():
            raise CommandError('Set SHOPIFY_CATALOG_AUTOMATION=1 before running the worker.')
        if options['watch'] and (options['all'] or options['backfill_restocks']):
            raise CommandError('Run --all/--backfill-restocks once, separately from --watch.')
        if options['backfill_restocks']:
            self.stdout.write(f'Historical restocks recorded: {backfill_restocks()}')
        if options['all']:
            for product in shopify_syncable(Product.objects).exclude(barcode='').iterator():
                enqueue(product, wake_worker=False)
        client = CatalogClient()
        client.require_configured()
        while True:
            try:
                result = process_catalog(client=client, limit=options['limit'])
                self.stdout.write(str(result))
                self.stdout.flush()
            except Exception as exc:
                if not options['watch']:
                    raise CommandError(str(exc)) from exc
                self.stderr.write(f'Catalogue retry pending: {exc}')
            if not options['watch']:
                if options['all'] and not result.get('busy') and ShopifyCatalogState.objects.filter(
                        requested_revision__gt=F('synced_revision'), next_attempt_at__lte=timezone.now()).exists():
                    continue
                return
            time.sleep(max(10, options['interval']))
