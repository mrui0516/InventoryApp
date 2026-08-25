"""Show which inbound orders are being measured for lead time, and why not.

Lead time needs two facts: when the order was placed and when it landed. When
a supplier's average looks wrong, the question is always which orders are
behind it, so this prints exactly that instead of leaving it to guesswork.

Read-only.
"""
from django.core.management.base import BaseCommand

from stock.models import InboundOrder, Supplier
from stock.services.lead_time import humanise, supplier_lead_stats


def why_not(order):
    """Why this order contributes nothing, or '' when it does."""
    if order.status != 'received':
        return 'not received yet'
    if order.placed_at is None:
        return 'no placed_at (predates lead-time tracking)'
    if order.received_at is None:
        return 'no received_at (received without stamping)'
    return ''


class Command(BaseCommand):
    help = 'Show which inbound orders count towards supplier lead time.'

    def add_arguments(self, parser):
        parser.add_argument('--supplier', help='filter by supplier name (contains)')
        parser.add_argument('--limit', type=int, default=15,
                            help='how many recent orders to list per supplier')

    def handle(self, *args, **opts):
        suppliers = Supplier.objects.order_by('name')
        if opts['supplier']:
            suppliers = suppliers.filter(name__icontains=opts['supplier'])
        if not suppliers.exists():
            self.stdout.write(self.style.WARNING('No matching supplier.'))
            return

        for supplier in suppliers:
            orders = (InboundOrder.objects
                      .filter(supplier=supplier)
                      .order_by('-created_at')[:opts['limit']])
            if not orders:
                continue

            stats = supplier_lead_stats(supplier)
            summary = (f'avg {stats["avg_label"]} over {stats["sample"]} order(s)'
                       if stats else 'no measurable orders yet')
            self.stdout.write('')
            self.stdout.write(self.style.MIGRATE_HEADING(f'{supplier.name} - {summary}'))

            for order in orders:
                reason = why_not(order)
                if reason:
                    self.stdout.write(
                        f'  #{order.id:<6} {order.created_at:%Y-%m-%d}  '
                        f'{self.style.WARNING("skipped")}  {reason}')
                else:
                    self.stdout.write(
                        f'  #{order.id:<6} {order.placed_at:%Y-%m-%d} -> '
                        f'{order.received_at:%Y-%m-%d}  '
                        f'{self.style.SUCCESS(humanise(order.lead_time_days))}')
