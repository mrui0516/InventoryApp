import time

from django.core.management.base import BaseCommand,CommandError
from django.db import close_old_connections
from restock_alerts.services import enabled,process_notifications


class Command(BaseCommand):
    help = 'Process capacity-specific restock alerts using the configured provider and reconcile Flow receipts.'

    def add_arguments(self,parser):
        parser.add_argument('--limit',type=int,default=100)
        parser.add_argument('--watch',action='store_true',help='Keep polling in a supervised/always-on task.')
        parser.add_argument('--interval',type=int,default=60,help='Polling seconds in watch mode (minimum 15).')

    def handle(self,*args,**options):
        if not enabled():
            raise CommandError('Restock alerts disabled or provider not ready. Check RESTOCK_NOTIFICATIONS_ENABLED and SMTP / Messaging configuration.')
        try:
            while True:
                close_old_connections()
                self.stdout.write(str(process_notifications(limit=max(1,min(options['limit'],1000)))))
                if not options['watch']:
                    break
                time.sleep(max(15,options['interval']))
        except KeyboardInterrupt:
            self.stdout.write('Worker stopped.')
