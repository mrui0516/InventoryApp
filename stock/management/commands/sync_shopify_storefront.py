"""Reconcile storefront collections without drafting sold-out products."""
from django.core.management.base import BaseCommand, CommandError

from stock.services.shopify_client import ShopifyError
from stock.services.shopify_storefront import sync_storefront
from stock.services.shopify_catalog_worker import process_catalog


class Command(BaseCommand):
    help = 'Refresh Novidades, Restock (30 days), and monthly retail rankings; preserve product status.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        try:
            result = process_catalog() if options['apply'] else sync_storefront(dry_run=True)
        except ShopifyError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(('APPLIED' if options['apply'] else 'DRY RUN') + f' {result}')
