"""What Settings -> Catalog changes, apart from the screens."""
from django.db import transaction
from django.utils.text import slugify

from ..models import Brand, Product, ProductSeries, ProductType
from .names import title_case


class NameTaken(ValueError):
    """The name belongs to another row: the way forward is a merge."""

    def __init__(self, existing):
        super().__init__(f'{existing.name} already exists.')
        self.existing = existing


def new_type_code(name):
    base = slugify(name).replace('-', '_')[:16] or 'type'
    code, n = base, 1
    while ProductType.objects.filter(code=code).exists():
        n += 1
        tail = f'_{n}'
        code = base[:16 - len(tail)] + tail
    return code


def delete_type(row, move_to=None):
    """Delete a type, moving its products to ``move_to``. Returns how many moved.

    Archived products count: their rows still point at the type.
    """
    if row.code == Product.PERFUME:
        raise ValueError('Perfume is the default type: rename it instead.')
    products = Product.all_objects.filter(product_type=row)
    count = products.count()
    if count and (move_to is None or move_to.pk == row.pk):
        raise ValueError(f'{row.name} has {count} product{"s" if count != 1 else ""}: choose a type to move them to.')
    with transaction.atomic():
        if count:
            products.update(product_type=move_to)
        row.delete()
    return count


# Products keep the brand and line as text too (``Product.brand``, ``model``):
# names and reports read that, so each change here rewrites it, archived rows
# included.
def rename_brand(brand, name):
    name = title_case((name or '').strip())
    if not name:
        raise ValueError('Give the brand a name.')
    existing = Brand.objects.filter(name__iexact=name).exclude(pk=brand.pk).first()
    if existing:
        raise NameTaken(existing)
    with transaction.atomic():
        Brand.objects.filter(pk=brand.pk).update(name=name)
        Product.all_objects.filter(brand_master=brand).update(brand=name)
    brand.name = name


def rename_line(line, name):
    name = title_case((name or '').strip())
    if not name:
        raise ValueError('Give the line a name.')
    existing = ProductSeries.objects.filter(brand=line.brand_id, name__iexact=name).exclude(pk=line.pk).first()
    if existing:
        raise NameTaken(existing)
    with transaction.atomic():
        ProductSeries.objects.filter(pk=line.pk).update(name=name)
        Product.all_objects.filter(series_master=line).update(model=name)
    line.name = name


def merge_lines(keep, other):
    """Move ``other``'s products to ``keep`` and delete it. Returns how many moved."""
    if keep.pk == other.pk or keep.brand_id != other.brand_id:
        raise ValueError('Choose another line of the same brand.')
    with transaction.atomic():
        count = Product.all_objects.filter(series_master=other).update(series_master=keep, model=keep.name)
        other.delete()
    return count


def merge_brands(keep, other):
    """Move ``other``'s products and lines to ``keep`` and delete it. Returns how
    many products moved. A line with the same name becomes one line."""
    if keep.pk == other.pk:
        raise ValueError('Choose another brand.')
    with transaction.atomic():
        count = Product.all_objects.filter(brand_master=other).update(brand_master=keep, brand=keep.name)
        for line in list(other.series.all()):
            same = keep.series.filter(name__iexact=line.name).first()
            if same:
                Product.all_objects.filter(series_master=line).update(series_master=same, model=same.name)
                line.delete()
            else:
                line.brand = keep
                line.save(update_fields=['brand'])
        keep.categories.add(*other.categories.all())
        if other.logo and not keep.logo:
            keep.logo = other.logo.name
            keep.save(update_fields=['logo'])
        other.delete()
    return count
