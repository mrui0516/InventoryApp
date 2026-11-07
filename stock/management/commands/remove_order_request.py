"""Remove one wholesale order request, with its waiting Pedido draft.

Dry run unless --apply. A sold order is refused: its sale moved stock, and is
undone from Orders, not here.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from stock.models import OrderRequest, PedidoDraft


class Command(BaseCommand):
    help = 'Remove a wholesale order request (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('order_id', type=int)
        parser.add_argument('--apply', action='store_true')

    def handle(self, order_id, apply=False, **options):
        order = OrderRequest.objects.select_related('customer', 'pedido').filter(pk=order_id).first()
        if order is None:
            raise CommandError(f'No order request #{order_id}.')
        who = order.customer.name if order.customer else order.contact_name
        self.stdout.write(f'Order request #{order.id} - {who} - {order.get_status_display()} - '
                          f'{order.units} units - EUR {order.total:.2f}')
        if order.status == OrderRequest.COMPLETED:
            self.stdout.write('It was sold: undo the sale in Orders first. Nothing removed.')
            return
        draft = order.pedido if order.pedido and order.pedido.status == PedidoDraft.OPEN else None
        if draft:
            self.stdout.write(f'  and its Pedido draft #{draft.id}')
        if not apply:
            self.stdout.write('Dry run - nothing removed. Add --apply to remove it.')
            return
        with transaction.atomic():
            if draft:
                draft.delete()
            order.delete()
        self.stdout.write(f'Order request #{order_id} removed.')
