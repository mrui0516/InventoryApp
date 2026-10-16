"""Delete customer rows that never bought anything.

The list filled up with names typed at the till that never turned into a sale -
a mis-keyed NIF, somebody who asked for a price and left, a test row. They
answer every search and make the real customers harder to find.

What it refuses to delete, and why:

* Anyone with a sale order or a sale line. Those are the books. (``SaleOrder``
  and ``Sale`` point at the customer with SET_NULL, so deleting such a row
  would not raise - it would quietly blank the customer on historical orders.
  That is exactly the damage this guard exists to prevent.)
* Anyone with an AR invoice. ``ARInvoice.customer`` is PROTECT, so Django would
  refuse anyway; they are listed as kept rather than left to raise mid-run.

Dry-run by default, and it prints every row it would delete - these are real
names on a live shop's database, and the list is meant to be read before
``--apply``, not after. ``--exclude-nif`` spares specific rows (placeholders
like a walk-in "Cliente" are worth keeping even with no orders).
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from stock.models import Customer

# Names that read like a counter placeholder rather than a person. Only used to
# add a warning to the dry run - nothing is skipped on a guess.
PLACEHOLDER_HINTS = ('cliente', 'tmp', 'temp', 'teste', 'test', 'balcao',
                     'balcão', 'generic')


class Command(BaseCommand):
    help = 'Delete customers with no sale order and no sale line (dry run by default).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='write changes (default: dry run)')
        parser.add_argument('--exclude-nif', action='append', default=[],
                            metavar='NIF',
                            help='keep this customer regardless (repeatable)')

    def handle(self, *args, **opts):
        spared = {n.strip() for n in opts['exclude_nif'] if n.strip()}

        rows = (Customer.objects
                .annotate(orders=Count('saleorder', distinct=True),
                          sale_lines=Count('sale', distinct=True),
                          invoices=Count('ar_invoices', distinct=True))
                .order_by('name'))

        deletable, blocked, kept_excluded, has_history = [], [], [], 0
        for customer in rows:
            if customer.orders or customer.sale_lines:
                has_history += 1
                continue
            if customer.nif in spared:
                kept_excluded.append(customer)
            elif customer.invoices:
                blocked.append(customer)
            else:
                deletable.append(customer)

        self.stdout.write(f'  customers                 {rows.count()}')
        self.stdout.write(f'  kept (has order history)  {has_history}')
        self.stdout.write(f'  kept (has AR invoice)     {len(blocked)}')
        self.stdout.write(f'  kept (--exclude-nif)      {len(kept_excluded)}')
        self.stdout.write(f'  would be DELETED          {len(deletable)}')
        self.stdout.write('')

        for customer in deletable:
            hint = ''
            if any(word in (customer.name or '').lower() for word in PLACEHOLDER_HINTS):
                hint = '   <- looks like a placeholder, keep it with --exclude-nif?'
            self.stdout.write(f'    {customer.nif}  {(customer.name or "")[:34]:<34}{hint}')
        for customer in blocked:
            self.stdout.write(self.style.WARNING(
                f'    {customer.nif}  {(customer.name or "")[:34]:<34}   kept: '
                f'{customer.invoices} AR invoice(s)'))

        if not opts['apply']:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'Dry run - nothing deleted. Read the list above, then use --apply.'))
            return

        with transaction.atomic():
            for customer in deletable:
                customer.delete()

        self.stdout.write(self.style.SUCCESS(f'Deleted {len(deletable)} customers'))
