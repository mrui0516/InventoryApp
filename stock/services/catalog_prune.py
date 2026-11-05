"""A brand or line with no product left goes away by itself.

Archived products do not count: they are gone from the catalogue, cannot come
back from the app, and keep their brand and line as text (``Product.brand`` and
``Product.model``), which is what sales history and reports read. Deleting the
row only clears their link to it.

A live product that carries the name as text but has no link still counts, so
nothing is removed from under an old row.

A brand with a logo stays until someone deletes it: the logo took work, and the
brand often comes back with the next delivery.
"""
from django.db.models import Exists, OuterRef

from ..models import Brand, Product, ProductSeries


def empty_series(ids=None):
    rows = ProductSeries.objects.all() if ids is None else ProductSeries.objects.filter(pk__in=ids)
    linked = Product.objects.filter(series_master=OuterRef('pk'))
    by_text = Product.objects.filter(series_master=None, model__iexact=OuterRef('name'),
                                     brand__iexact=OuterRef('brand__name'))
    return rows.filter(~Exists(linked), ~Exists(by_text))


def empty_brands(ids=None, keep_logos=True):
    rows = Brand.objects.all() if ids is None else Brand.objects.filter(pk__in=ids)
    if keep_logos:
        rows = rows.filter(logo='')
    linked = Product.objects.filter(brand_master=OuterRef('pk'))
    by_series = Product.objects.filter(series_master__brand=OuterRef('pk'))
    by_text = Product.objects.filter(brand_master=None, brand__iexact=OuterRef('name'))
    return rows.filter(~Exists(linked), ~Exists(by_series), ~Exists(by_text))


def prune(brand_ids=None, series_ids=None):
    """Delete the empty ones among these ids, or all of them when both are None.

    Returns ``(brands, lines)`` deleted; a brand takes its leftover lines with it.
    """
    everything = brand_ids is None and series_ids is None
    lines = list(empty_series(None if everything else (series_ids or ())).values_list('pk', flat=True))
    ProductSeries.objects.filter(pk__in=lines).delete()
    brands = list(empty_brands(None if everything else (brand_ids or ())).values_list('pk', flat=True))
    gone_with_brand = ProductSeries.objects.filter(brand_id__in=brands).count()
    Brand.objects.filter(pk__in=brands).delete()
    return len(brands), len(lines) + gone_with_brand
