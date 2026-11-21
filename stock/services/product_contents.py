"""The list on a mix or a coffret, and the perfume a product is a version of.
Display only: nothing here touches stock.

Since 2026-11-16 a mix is not ticked: two or more scents on anything but a gift
is a mix (the customer picks one). One linked scent on anything but a gift is
the perfume it is a version of, kept as ``Product.scent_source``."""
import re

from django.db import transaction
from django.db.models import Q, Sum

from ..models import Product, ProductContent, Purchase
from .availability import offerable_ids
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
    result = []
    for row in rows:
        live = row.linked if row.linked and row.linked.archived_at is None else None
        result.append({'name': content_label(row), 'linked_id': live.pk if live else None,
                       'linked_label': live.list_name if live else ''})
    return result


def _link_id(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def scent_root(product):
    """A version of a version is a version of the perfume itself."""
    return product.scent_source if product is not None and product.scent_source_id else product


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
    gift = owner.type_info.is_gift
    if not gift and len(fresh) == 1 and fresh[0].linked is not None:
        root = scent_root(fresh[0].linked)
        # A different source chosen by hand wins; the row then stays a row.
        if root.pk != owner.pk and owner.scent_source_id in (None, root.pk):
            owner.scent_source = root
            fresh = []
    with transaction.atomic():
        ProductContent.objects.filter(owner=owner).delete()
        ProductContent.objects.bulk_create(fresh)
        owner.is_mix = not gift and len(fresh) >= 2
        Product.all_objects.filter(pk=owner.pk).update(is_mix=owner.is_mix, scent_source=owner.scent_source_id)


def adopt_versions(product):
    """A perfume that becomes a version hands its own versions to its perfume,
    so every link points at the root."""
    if product.scent_source_id:
        Product.all_objects.filter(scent_source=product).update(scent_source=product.scent_source_id)


def parse_pasted(brand_name, text):
    """Names one per line or comma separated, linked where a product of this brand
    has exactly that name. Takes the brand's name since a new product has no id.

    One name often means several products. The list links to the scent itself, so
    the perfume wins, the biggest bottle first; a product with a list of its own
    (a coffret, a body spray) is never the scent."""
    catalogue = {}
    products = Product.objects.filter(brand__iexact=brand_name, is_mix=False)
    candidates = [p for p in products if not p.type_info.has_contents]
    candidates.sort(key=lambda p: (p.product_type_id != Product.PERFUME, -(p.volume_ml or 0), p.pk))
    for product in candidates:
        catalogue.setdefault(product.scent_name.lower(), product)
    seen, rows = set(), []
    for part in re.split(r'[\n,]', text or ''):
        name = part.strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        match = catalogue.get(name.lower())
        rows.append({'name': match.scent_name if match else title_case(name),
                     'linked_id': match.pk if match else None,
                     'linked_label': match.list_name if match else ''})
    return rows


def also_in(product):
    """The products on sale whose list names this one, and its versions."""
    owners = Product.objects.filter(contents__linked=product).distinct().order_by('model', 'name')
    listed = [owner for owner in owners if owner.lists_contents]
    versions = [v for v in product.scent_versions.order_by('product_type__sort_order', '-volume_ml', 'pk')
                if v.pk != product.pk and v not in listed]
    return listed + versions


def version_label(product):
    """'Body mist 250ml', 'Perfume 30ml': what kind of version it is."""
    kind = product.type_info.name_word or product.type_info.name
    size = f' {product.volume_ml}ml' if product.volume_ml else ''
    return f'{kind}{size}'


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
             'label': (version_label(owner) if owner.scent_source_id == product.pk
                       else (owner.type_info.name_word or owner.type_info.name) + (' (Mix)' if owner.is_mix else '')),
             'stock': stock.get(owner.pk) or 0}
            for owner in owners]
    return {'contents': contents, 'contents_title': contents_title(product), 'also_in': also,
            'scent_source': product.scent_parent}


def pdf_links(products):
    """For a PDF of these products: ``{id: {'contents': [...], 'also_in': [...]}}``,
    each entry ``(label, target_id)``. ``target_id`` is None unless the target is
    in this PDF: a link to a row that is not there stops reportlab cold. A perfume
    only points at what the customer can have."""
    ids = {p.pk for p in products}
    links = {}
    rows = list(ProductContent.objects.filter(Q(owner_id__in=ids) | Q(linked_id__in=ids))
                .select_related('owner', 'linked').order_by('sort_order', 'id'))
    # A version and its perfume read like a one-scent list: the version names the
    # perfume, the perfume lists the versions a customer can have.
    pairs = list(Product.objects.filter(Q(pk__in=ids, scent_source__isnull=False) | Q(scent_source_id__in=ids))
                 .select_related('scent_source', 'scent_source__concentration'))
    # One stock lookup for both kinds, so the query count never depends on the data.
    offerable = offerable_ids({row.owner_id for row in rows if row.linked_id in ids}
                              | {p.pk for p in pairs if p.scent_source_id in ids})
    for row in rows:
        owner = row.owner
        if owner.archived_at is not None or not owner.lists_contents:
            continue
        if owner.pk in ids:
            live = row.linked_id in ids and row.linked.archived_at is None
            entry = links.setdefault(owner.pk, {'contents': [], 'also_in': []})
            entry['contents'].append((content_label(row), row.linked_id if live else None))
        if row.linked_id in ids and owner.pk in offerable:
            kind = owner.type_info
            entry = links.setdefault(row.linked_id, {'contents': [], 'also_in': []})
            entry['also_in'].append((kind.customer_label or kind.name, owner.pk if owner.pk in ids else None))
    for version in pairs:
        parent = version.scent_source
        if version.archived_at is not None or parent is None:
            continue
        if version.pk in ids:
            # An archived perfume is still named, as plain text.
            live = parent.pk in ids and parent.archived_at is None
            entry = links.setdefault(version.pk, {'contents': [], 'also_in': []})
            entry['contents'].append((parent.scent_name or parent.list_name, parent.pk if live else None))
        if parent.pk in ids and version.pk in offerable:
            kind = version.type_info
            label = f'{kind.customer_label or kind.name}' + (f' {version.volume_ml}ml' if version.volume_ml else '')
            entry = links.setdefault(parent.pk, {'contents': [], 'also_in': []})
            entry['also_in'].append((label, version.pk if version.pk in ids else None))
    return links

