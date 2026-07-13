"""Move all sales tied to a marker "customer" into a real store.

Historically a dummy customer (e.g. SHOP2) was used to tag a second shop's
sales before the multi-store feature existed. This command re-attributes those
orders/sales to a real Store, and can optionally clear the marker customer to
walk-in and delete it.

Dry-run by default — pass --commit to actually apply.

    python manage.py move_sales_to_store --customer SHOP2 --store Scentory \
        --clear-customer --delete-customer --commit
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from stock.models import Customer, Sale, SaleOrder, Store


class Command(BaseCommand):
    help = "Re-attribute a marker customer's sales to a real store."

    def add_arguments(self, parser):
        parser.add_argument('--customer', required=True, help='Customer id, NIF, or exact name')
        parser.add_argument('--store', required=True, help='Store id or code')
        parser.add_argument('--clear-customer', action='store_true',
                            help='Clear the customer link to walk-in on moved records')
        parser.add_argument('--delete-customer', action='store_true',
                            help='Delete the marker customer afterwards (implies --clear-customer)')
        parser.add_argument('--commit', action='store_true',
                            help='Actually apply changes (default: dry-run)')

    def _resolve_customer(self, value):
        if value.isdigit():
            customer = Customer.objects.filter(id=int(value)).first()
            if customer:
                return customer
        return (
            Customer.objects.filter(nif=value).first()
            or Customer.objects.filter(name__iexact=value).first()
        )

    def _resolve_store(self, value):
        if value.isdigit():
            store = Store.objects.filter(id=int(value)).first()
            if store:
                return store
        return Store.objects.filter(code__iexact=value).first() or Store.objects.filter(name__iexact=value).first()

    def handle(self, *args, **opts):
        customer = self._resolve_customer(opts['customer'])
        if not customer:
            raise CommandError(f"No customer matched {opts['customer']!r}.")
        store = self._resolve_store(opts['store'])
        if not store:
            raise CommandError(f"No store matched {opts['store']!r}.")

        delete_customer = opts['delete_customer']
        clear_customer = opts['clear_customer'] or delete_customer
        commit = opts['commit']

        order_ids = list(SaleOrder.objects.filter(customer=customer).values_list('id', flat=True))
        order_sales = Sale.objects.filter(order_id__in=order_ids)
        legacy_sales = Sale.objects.filter(order__isnull=True, customer=customer)

        self.stdout.write(f"Customer : #{customer.id} {customer.name!r} (NIF {customer.nif})")
        self.stdout.write(f"Store    : #{store.id} {store.name!r} (code {store.code})")
        self.stdout.write(f"Orders to move        : {len(order_ids)}")
        self.stdout.write(f"Sales under orders    : {order_sales.count()}")
        self.stdout.write(f"Legacy (order-less)   : {legacy_sales.count()}")
        self.stdout.write(f"Clear customer link   : {clear_customer}")
        self.stdout.write(f"Delete marker customer: {delete_customer}")

        if not commit:
            self.stdout.write(self.style.WARNING("\nDRY-RUN — nothing changed. Re-run with --commit to apply."))
            return

        clear = {'customer': None} if clear_customer else {}
        with transaction.atomic():
            moved_order_sales = Sale.objects.filter(order_id__in=order_ids).update(store=store, **clear)
            moved_legacy = Sale.objects.filter(order__isnull=True, customer=customer).update(store=store, **clear)
            moved_catchall = Sale.objects.filter(customer=customer).update(store=store, **clear)
            moved_orders = SaleOrder.objects.filter(id__in=order_ids).update(store=store, **clear)
            deleted = 0
            if delete_customer:
                deleted, _ = Customer.objects.filter(id=customer.id).delete()

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. orders={moved_orders}, order_sales={moved_order_sales}, "
            f"legacy_sales={moved_legacy}, catchall_sales={moved_catchall}, "
            f"customer_deleted={'yes' if deleted else 'no'}."
        ))
