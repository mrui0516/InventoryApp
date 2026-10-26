from django.core.management.base import BaseCommand,CommandError
from restock_alerts.services import enabled,process_notifications


class Command(BaseCommand):
    help = 'Send confirmed, capacity-specific back-in-stock emails and retry queued messages.'

    def add_arguments(self,parser):
        parser.add_argument('--limit',type=int,default=100)

    def handle(self,*args,**options):
        if not enabled():
            raise CommandError('Restock emails disabled or SMTP host missing. Configure SMTP and RESTOCK_NOTIFICATIONS_ENABLED=1.')
        self.stdout.write(str(process_notifications(limit=max(1,min(options['limit'],1000)))))
