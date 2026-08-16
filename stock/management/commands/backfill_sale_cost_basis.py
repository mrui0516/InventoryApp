"""Backfill Sale.cost_basis for existing stock-affecting sales.

New sales record their true FIFO cost at sale time. Old sales don't, so profit
falls back to a from-scratch reconstruction that drifts when stock was changed
without a trace (batch-quantity edits, deletes). This assigns each sale a cost
basis by matching the actual per-batch consumption (quantity − remaining) to the
sales **newest-first**, so recent sales anchor to the batch they really came from
(the newest partly-consumed one) even if older counts don't reconcile.

Only fills sales whose cost_basis is null and that affect stock. Dry-run by
default.

  python manage.py backfill_sale_cost_basis            # preview
  python manage.py backfill_sale_cost_basis --apply    # write
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from stock.models import Product, Purchase, Sale


class Command(BaseCommand):
    help = "Backfill Sale.cost_basis via newest-first FIFO matching against actual consumption."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually write (default is a dry run).')

    def handle(self, *args, **opts):
        dry_run = not opts['apply']
        product_ids = (Sale.objects.filter(cost_basis__isnull=True)
                       .values_list('product_id', flat=True).distinct())

        filled = 0
        for product_id in product_ids:
            # consumed segments, oldest batch first: (cost, count)
            segments = []
            for pu in (Purchase.objects.filter(product_id=product_id)
                       .order_by('date', 'id')
                       .only('quantity', 'remaining', 'cost_price')):
                consumed = pu.quantity - pu.remaining
                if consumed > 0:
                    segments.append([pu.cost_price or Decimal('0.00'), consumed])
            # newest-consumed batch first, so the latest sales match the newest batch
            segments.reverse()

            # stock-affecting sales, newest first
            sales = list(Sale.objects.filter(product_id=product_id)
                         .exclude(order__affects_stock=False)
                         .order_by('-date', '-id')
                         .only('id', 'quantity', 'cost_basis'))

            seg_i = 0
            for sale in sales:
                units, cost = sale.quantity, Decimal('0.00')
                while units > 0 and seg_i < len(segments):
                    seg_cost, seg_count = segments[seg_i]
                    take = min(units, seg_count)
                    cost += Decimal(take) * seg_cost
                    seg_count -= take
                    units -= take
                    if seg_count == 0:
                        seg_i += 1
                    else:
                        segments[seg_i][1] = seg_count
                # only fill rows that don't already have a cost basis
                if sale.cost_basis is None and units == 0:
                    if not dry_run:
                        Sale.objects.filter(pk=sale.pk).update(cost_basis=cost)
                    filled += 1

        self.stdout.write(self.style.WARNING(
            f"{'DRY RUN' if dry_run else 'APPLIED'} — cost_basis backfilled for {filled} sale(s)."))
        if dry_run:
            self.stdout.write(self.style.WARNING('Re-run with --apply to write.'))
