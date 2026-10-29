"""Write every brand, series and product name one way (each word capitalised,
stock/services/names.py) and set the type of body sprays, air fresheners,
coffrets and minis that the names already say.

Dry run by default - prints every change. ``--apply`` writes them.

Brands that end up with the same name ("LATTAFA" and "Lattafa") are merged
into the one with the most products: its products, series and categories
move over. Series of one brand are merged the same way. Products are written
with queryset updates, so no Shopify or Cloudinary signal fires per product;
the storefront titles follow on the next sync.
"""
import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from stock.models import Brand, Product, ProductSeries
from stock.services.names import title_case

TYPE_HINTS = [
    (Product.AIR_FRESHENER, re.compile(r'air\s*fresh', re.I)),
    (Product.BODY_SPRAY, re.compile(r'body\s*spray|\bdeo(dorant)?\b', re.I)),
    (Product.COFFRET, re.compile(r'gift\s*(pack|set|box)|coffret|\bpack\b|\bset\b', re.I)),
    (Product.MINI, re.compile(r'\bmini\b', re.I)),
]


def guess_type(product):
    text = ' '.join(filter(None, [product.model, product.name, product.series_master.name
                                  if product.series_master_id else None]))
    for kind, pattern in TYPE_HINTS:
        if pattern.search(text):
            return kind
    return None


class Command(BaseCommand):
    help = 'Capitalise brand/series/product names one way and set obvious product types (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write the changes.')

    def handle(self, *args, apply=False, **options):
        out = self.stdout.write
        with transaction.atomic():
            counts = self._brands(out) | {}
            counts.update(self._series(out))
            counts.update(self._products(out))
            out('')
            out('Summary: ' + ', '.join(f'{k} {v}' for k, v in counts.items()))
            if not apply:
                transaction.set_rollback(True)
                out(self.style.WARNING('Dry run - nothing written. Run again with --apply to write it.'))
            else:
                out(self.style.SUCCESS('Written.'))

    # -- brands ---------------------------------------------------------------
    def _brands(self, out):
        groups = defaultdict(list)
        for brand in Brand.objects.annotate(n=Count('products')).order_by('-n', 'id'):
            groups[title_case(brand.name)].append(brand)
        merged = renamed = 0
        out('== Brands')
        for name, brands in groups.items():
            keeper, others = brands[0], brands[1:]
            for other in others:
                out(f'  merge "{other.name}" ({other.n} products) into "{keeper.name}" -> "{name}"')
                Product.all_objects.filter(brand_master=other).update(brand_master=keeper)
                for series in list(other.series.all()):
                    same = keeper.series.filter(name__iexact=series.name).first()
                    if same:
                        Product.all_objects.filter(series_master=series).update(series_master=same)
                        series.delete()
                    else:
                        series.brand = keeper
                        series.save(update_fields=['brand'])
                keeper.categories.add(*other.categories.all())
                other.delete()
                merged += 1
            if keeper.name != name:
                out(f'  "{keeper.name}" -> "{name}"')
                Brand.objects.filter(pk=keeper.pk).update(name=name)
                renamed += 1
        return {'brands renamed': renamed, 'brands merged': merged}

    # -- series --------------------------------------------------------------
    def _series(self, out):
        groups = defaultdict(list)
        for series in ProductSeries.objects.annotate(n=Count('products')).order_by('-n', 'id'):
            groups[(series.brand_id, title_case(series.name))].append(series)
        merged = renamed = 0
        out('== Series')
        for (_brand_id, name), items in groups.items():
            keeper, others = items[0], items[1:]
            for other in others:
                out(f'  merge series "{other.name}" into "{keeper.name}" -> "{name}"')
                Product.all_objects.filter(series_master=other).update(series_master=keeper)
                other.delete()
                merged += 1
            if keeper.name != name:
                out(f'  "{keeper.name}" -> "{name}"')
                ProductSeries.objects.filter(pk=keeper.pk).update(name=name)
                renamed += 1
        return {'series renamed': renamed, 'series merged': merged}

    # -- products ------------------------------------------------------------
    def _products(self, out):
        renamed = typed = 0
        out('== Products')
        for product in Product.all_objects.select_related('brand_master', 'series_master').order_by('id'):
            changes = {}
            brand = product.brand_master.name if product.brand_master_id else title_case(product.brand)
            model = product.series_master.name if product.series_master_id else title_case(product.model)
            name = title_case(product.name) if product.name else product.name
            for field, value in (('brand', brand), ('model', model or None), ('name', name)):
                if (getattr(product, field) or None) != (value or None):
                    changes[field] = value if field != 'model' else (value or None)
            if changes:
                before = product.display_name
                for field, value in changes.items():
                    setattr(product, field, value)
                out(f'  #{product.id}: {before}  ->  {product.display_name}')
                renamed += 1
            if product.perfume_type == Product.PERFUME:
                kind = guess_type(product)
                if kind:
                    changes['perfume_type'] = kind
                    out(f'  #{product.id}: type -> {dict(Product.PERFUME_TYPES)[kind]}  ({product.display_name})')
                    typed += 1
            if changes:
                Product.all_objects.filter(pk=product.pk).update(**changes)
        return {'products renamed': renamed, 'types set': typed}
