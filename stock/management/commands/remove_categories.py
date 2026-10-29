"""Delete top-level categories the shop no longer uses, with everything beneath them.

    manage.py remove_categories --name Accessories --name Shisha          # dry run
    manage.py remove_categories --name Accessories --name Shisha --apply

Dry run by default: lists the categories, their active products with stock
and sales, and every row that points at them. Refuses to delete while a
category still holds an active product - unless ``--archive-products`` is
given, which archives them first (as "Delete" does in the app: gone from the
catalogue, search and selling; sales, purchases and profit kept). Archived
products are left without a category; attributes defined for the category go
with it; stores, suppliers, brands and shelf styles simply lose the link.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from stock.models import Category, Product


def with_descendants(category):
    found, frontier = [category], [category.id]
    while frontier:
        children = list(Category.objects.filter(parent_id__in=frontier))
        found.extend(children)
        frontier = [c.id for c in children]
    return found


class Command(BaseCommand):
    help = 'Delete unused top-level categories and their subcategories (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--name', action='append', required=True, help='Top-level category name (repeatable).')
        parser.add_argument('--apply', action='store_true', help='Delete them.')
        parser.add_argument('--archive-products', action='store_true',
                            help='Archive their active products first (history kept).')

    def handle(self, *args, name, apply=False, archive_products=False, **options):
        out = self.stdout.write
        doomed = []
        for wanted in name:
            top = Category.objects.filter(name__iexact=wanted.strip(), parent__isnull=True).first()
            if not top:
                out(self.style.WARNING(f'No top-level category called "{wanted}" - skipped.'))
                continue
            doomed.extend(with_descendants(top))
        if not doomed:
            out('Nothing to delete.')
            return
        ids = [c.id for c in doomed]
        active = Product.objects.filter(category_id__in=ids)
        archived = Product.all_objects.filter(category_id__in=ids, archived_at__isnull=False)
        out('Categories: ' + ', '.join(c.name for c in doomed))
        from django.db.models import Sum
        from stock.models import Purchase, Sale
        stock = Purchase.objects.filter(product__in=active, remaining__gt=0)
        units = stock.aggregate(u=Sum('remaining'))['u'] or 0
        with_stock = stock.values('product_id').distinct().count()
        sold = Sale.objects.filter(product__in=active)
        out(f'  active products   {active.count()}  ({with_stock} with stock, {units} units on the shelf; '
            f'{sold.values("product_id").distinct().count()} ever sold, {sold.aggregate(q=Sum("quantity"))["q"] or 0} units)')
        for p in active[:50]:
            out(f'    #{p.id} {p.display_name}')
        if active.count() > 50:
            out(f'    ... and {active.count() - 50} more')
        out(f'  archived products {archived.count()}  (kept, left without a category)')
        out(f'  attributes        {sum(c.attributes.count() for c in doomed if hasattr(c, "attributes"))}  (deleted with them)')
        out(f'  stores selling    {sum(c.stores.count() for c in doomed)}  (link removed)')
        out(f'  suppliers linked  {sum(c.supplier_set.count() for c in doomed)}  (link removed)')
        out(f'  brands linked     {sum(c.brands.count() for c in doomed)}  (link removed)')
        out(f'  shelf styles      {sum(c.shelf_styles.count() for c in doomed)}  (link removed)')
        if active.exists() and not archive_products:
            raise CommandError('Some of these categories still hold active products - archive or move them '
                               'first, or add --archive-products.')
        if not apply:
            out(self.style.WARNING('Dry run - nothing changed. Run again with --apply to do it.'))
            return
        with transaction.atomic():
            if archive_products:
                from django.utils import timezone
                count = active.update(archived_at=timezone.now())
                out(f'Archived {count} products.')
            for category in reversed(doomed):          # children before parents
                category.delete()
        out(self.style.SUCCESS(f'Deleted {len(doomed)} categories.'))
