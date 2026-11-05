"""The list on a mix, a coffret or a single-scent product: names, some linked to
a product. Display only: nothing here touches stock."""
import re

from django.db import transaction
from django.db.models import Sum

from ..models import Product, ProductContent, Purchase
from .names import title_case


def content_label(content):
    """The linked product's name while it is on sale, otherwise the stored one."""
    linked = content.linked
    if linked is not None and linked.archived_at is None:
        return linked.scent_name or content.name
    return content.name


def contents_title(owner):
    return 'Contains' if owner.type_info.is_gift else 'Scents'


def _clean_link(owner, linked_id):
    if not linked_id:
        return None
    return Product.objects.filter(pk=linked_id, is_mix=False).exclude(pk=owner.pk).first()


def contents_for_form(owner):
    rows = owner.contents.select_related('linked')
    return [{'name': content_label(row),
             'linked_id': row.linked_id if row.linked and row.linked.archived_at is None else None}
            for row in rows]


def _link_id(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def save_contents(owner, rows):
    """Replace the list. ``rows`` is ``[{'name': str, 'linked_id': int | None}]``."""
    seen, fresh = set(), []
    for row in rows:
        linked = _clean_link(owner, _link_id(row.get('linked_id')))
        name = (linked.scent_name if linked else '') or str(row.get('name') or '').strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        fresh.append(ProductContent(owner=owner, name=name[:120], linked=linked, sort_order=len(fresh)))
    with transaction.atomic():
        ProductContent.objects.filter(owner=owner).delete()
        ProductContent.objects.bulk_create(fresh)


def parse_pasted(brand_name, text):
    """Names one per line or comma separated, linked where a product of this brand
    has exactly that name. Takes the brand's name since a new product has no id."""
    catalogue = {p.scent_name.lower(): p for p in Product.objects.filter(brand__iexact=brand_name, is_mix=False)}
    seen, rows = set(), []
    for part in re.split(r'[\n,]', text or ''):
        name = part.strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        match = catalogue.get(name.lower())
        rows.append({'name': match.scent_name if match else title_case(name),
                     'linked_id': match.pk if match else None})
    return rows


def also_in(product):
    """The products on sale whose list names this one."""
    owners = Product.objects.filter(contents__linked=product).distinct().order_by('model', 'name')
    return [owner for owner in owners if owner.lists_contents]


def product_page_links(product):
    """What the product page says: its own list, and where it appears."""
    contents = []
    if product.lists_contents:
        for row in product.contents.select_related('linked'):
            live = row.linked if row.linked and row.linked.archived_at is None else None
            contents.append({'label': content_label(row), 'product': live})
    owners = also_in(product)
    stock = dict(Purchase.objects.filter(product__in=owners).values_list('product')
                 .annotate(n=Sum('remaining')).order_by())
    also = [{'product': owner,
             'label': (owner.type_info.name_word or owner.type_info.name) + (' (Mix)' if owner.is_mix else ''),
             'stock': stock.get(owner.pk) or 0}
            for owner in owners]
    return {'contents': contents, 'contents_title': contents_title(product), 'also_in': also}

