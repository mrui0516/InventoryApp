from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from restock_alerts.messaging import client, SCOPES_QUERY


class Command(BaseCommand):
    help = 'Read-only check of the server Shopify token and Messaging prerequisites. Never sends mail.'

    def handle(self, *args, **options):
        api = client()
        if not api.is_configured():
            raise CommandError('Configure SHOPIFY_STORE_DOMAIN and SHOPIFY_ADMIN_TOKEN on this server.')
        try:
            data = api.graphql(SCOPES_QUERY)
        except Exception as exc:
            raise CommandError(f'Shopify connection failed ({type(exc).__name__}); no email triggered.') from None
        scopes = {s['handle'] for s in data.get('currentAppInstallation', {}).get('accessScopes', [])}
        missing = []
        if 'write_customers' not in scopes:
            missing.append('write_customers')
        if not scopes.intersection({'read_products','write_products'}):
            missing.append('read_products')
        if missing:
            raise CommandError('Missing Shopify permissions: '+', '.join(missing)+'. Update the inventory App installation/token.')
        self.stdout.write(self.style.SUCCESS('Shopify connection and required permissions verified. No email triggered.'))
        self.stdout.write(f'Provider: {settings.RESTOCK_PROVIDER}; API: {api.api_version}')
        self.stdout.write(f'Notifications enabled: {settings.RESTOCK_NOTIFICATIONS_ENABLED}; Flow ready: {settings.RESTOCK_MESSAGING_FLOW_READY}')
        self.stdout.write('Flow configuration and inbox delivery must be verified separately in Shopify.')
