import time

from django.core.management.base import BaseCommand, CommandError

from stock.services.shopify_incoming import sync_incoming


class Command(BaseCommand):
    help = 'Refresh homepage incoming products from pending first-ever inbound orders.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--watch', action='store_true')
        parser.add_argument('--interval', type=int, default=60)

    def handle(self, *args, **options):
        if options['watch'] and not options['apply']:
            raise CommandError('--watch requires --apply.')
        while True:
            try:
                self.stdout.write(str(sync_incoming(dry_run=not options['apply'])))
                self.stdout.flush()
            except Exception as exc:
                if not options['watch']:
                    raise CommandError(str(exc)) from exc
                self.stderr.write(f'Incoming feed retry pending: {exc}')
            if not options['watch']:
                return
            time.sleep(max(15, options['interval']))
