"""A login for trying the wholesale catalogue, and removing it with what it made.

    manage.py test_account create teste      # asks for the password
    manage.py test_account remove teste      # lists what would go
    manage.py test_account remove teste --apply

The account is a manager, so it can open the Catálogo. Removing it takes its
cart, its order requests with their waiting Pedido drafts, and its restock
requests. Only accounts made here are removed, and a sold order stops it: the
sale moved stock and is undone from Orders.
"""
import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from stock.models import CartLine, OrderRequest, PedidoDraft, RestockRequest

MARK = 'Test account'      # in last_name: what tells a test account from a real one


class Command(BaseCommand):
    help = 'Create or remove a test account for the wholesale catalogue.'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=('create', 'remove'))
        parser.add_argument('username')
        parser.add_argument('--apply', action='store_true', help='remove for real')

    def handle(self, action, username, apply=False, **options):
        return self.create(username) if action == 'create' else self.remove(username, apply)

    def create(self, username):
        users = get_user_model()
        if users.objects.filter(username=username).exists():
            raise CommandError(f'{username} exists already.')
        password = getpass.getpass('Password: ')
        if len(password) < 8 or password != getpass.getpass('Again: '):
            raise CommandError('The passwords differ or are shorter than 8 characters.')
        user = users.objects.create_user(username, password=password, last_name=MARK)
        user.groups.add(Group.objects.get_or_create(name='Managers')[0])
        self.stdout.write(f'{username} created. Remove it with: manage.py test_account remove {username}')

    def remove(self, username, apply):
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f'No account {username}.')
        if user.last_name != MARK or user.is_superuser:
            self.stdout.write(f'{username} is not a test account. Nothing removed.')
            return
        orders = OrderRequest.objects.filter(submitted_by=user)
        if orders.filter(status=OrderRequest.COMPLETED).exists():
            self.stdout.write('One of its orders was sold: undo that sale in Orders first. Nothing removed.')
            return
        drafts = PedidoDraft.objects.filter(order_request__in=orders, status=PedidoDraft.OPEN)
        carts = CartLine.objects.filter(user=user)
        asks = RestockRequest.objects.filter(requested_by=user)
        counts = [(orders.count(), 'order request'), (drafts.count(), 'Pedido draft'),
                  (carts.count(), 'cart line'), (asks.count(), 'restock request')]
        for number, name in counts:
            self.stdout.write(f'  {number} {name}{"" if number == 1 else "s"}')
        if not apply:
            self.stdout.write('Dry run - nothing removed. Add --apply to remove it all.')
            return
        with transaction.atomic():
            drafts.delete()
            orders.delete()
            carts.delete()
            asks.delete()
            user.delete()
        self.stdout.write(f'{username} and everything it made removed.')
