"""Give a shop its opening stock: N units of every product from the default shop.

When stock started being counted per shop (2026-11-01) every batch was put in
the default shop. Scentory's shelves hold two of each product, so:

    python manage.py split_opening_stock --to Scentory --units 2          # preview
    python manage.py split_opening_stock --to Scentory --units 2 --apply

A product with N or fewer units moves entirely. The units move as an opening
transfer, which owes nothing; corrections afterwards are ordinary transfers.
Runs once: a second opening split is refused.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from stock.models import Product, Purchase, StockTransfer, Store
from stock.services.store_stock import move_units


class Command(BaseCommand):
    help = "Move N units of every product from the default shop to another (opening stock)."

    def add_arguments(self, parser):
        parser.add_argument('--to', required=True, help='Name of the shop receiving the stock.')
        parser.add_argument('--units', type=int, default=2)
        parser.add_argument('--apply', action='store_true', help='Write the changes (default: preview).')

    def handle(self, *args, to, units, apply, **options):
        target = Store.objects.filter(name__iexact=to).first()
        source = Store.get_default()
        if target is None:
            raise CommandError(f'No shop called "{to}". Shops: ' + ', '.join(Store.objects.values_list('name', flat=True)))
        if source is None or source.pk == target.pk:
            raise CommandError('The receiving shop must not be the default shop.')
        if units < 1:
            raise CommandError('--units must be at least 1.')
        if StockTransfer.objects.filter(reason=StockTransfer.OPENING).exists():
            raise CommandError('The opening split was already made. Use transfers to correct it.')

        held = defaultdict(dict)
        for row in (Purchase.objects.filter(remaining__gt=0, store__in=[source, target])
                    .values('product_id', 'store_id').annotate(u=Sum('remaining')).order_by()):
            held[row['product_id']][row['store_id']] = row['u']
        plan = []
        for product_id, by_store in held.items():
            if by_store.get(target.pk):
                continue                         # already has stock there
            have = by_store.get(source.pk, 0)
            if have:
                plan.append((product_id, min(units, have), have))
        # deleted (archived) products are left where they are
        products = Product.objects.in_bulk([p for p, _, _ in plan])
        plan = [row for row in plan if row[0] in products]

        out = self.stdout.write
        out(f'{source.name} -> {target.name}: {len(plan)} products, '
            f'{sum(q for _, q, _ in plan)} units ({units} each; fewer where {source.name} has fewer)')
        for product_id, qty, have in sorted(plan, key=lambda r: products[r[0]].display_name)[:40]:
            out(f'  {qty} x {products[product_id].display_name}  ({have} in {source.name})')
        if len(plan) > 40:
            out(f'  ... and {len(plan) - 40} more')
        if not apply:
            out(self.style.WARNING('Preview only. Back up the database, then run again with --apply.'))
            return
        with transaction.atomic():
            transfer = StockTransfer.objects.create(from_store=source, to_store=target,
                                                    reason=StockTransfer.OPENING)
            for product_id, qty, _have in plan:
                move_units(products[product_id], qty, source, target, transfer=transfer)
        out(self.style.SUCCESS(f'Done: transfer #{transfer.id}, {len(plan)} products moved.'))
