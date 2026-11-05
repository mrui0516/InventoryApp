"""What Settings -> Catalog changes, apart from the screens."""
from django.db import transaction
from django.utils.text import slugify

from ..models import Product, ProductType


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
