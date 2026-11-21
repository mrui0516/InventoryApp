"""Perfumes on sale that still miss their scent info (2026-11-21): a hint on the
product list, never a required field.

Only perfumes with stock count. A description is the product's own; notes and
families count when the perfume it is a version of has them (the scent_*
properties). Notes are missing only when all three layers are empty.
"""
from django.db.models import Exists, OuterRef, Q

from ..models import Product, Purchase


def incomplete_q():
    families = Product.fragrance_families.through
    no_description = Q(description__isnull=True) | Q(description__regex=r'^\s*$')
    no_notes = (Q(notes_top='', notes_heart='', notes_base='')
                & (Q(scent_source__isnull=True)
                   | Q(scent_source__notes_top='', scent_source__notes_heart='', scent_source__notes_base='')))
    no_families = (~Exists(families.objects.filter(product_id=OuterRef('pk')))
                   & ~Exists(families.objects.filter(product_id=OuterRef('scent_source_id'))))
    in_stock = Exists(Purchase.objects.filter(product_id=OuterRef('pk'), remaining__gt=0))
    return Q(product_type_id=Product.PERFUME) & in_stock & (no_description | no_notes | no_families)


def incomplete_count():
    return Product.objects.filter(incomplete_q()).count()


def missing_parts(product):
    """What one product lacks, in the order the form asks for it."""
    parts = []
    if not (product.description or '').strip():
        parts.append('description')
    if not (product.scent_top or product.scent_heart or product.scent_base):
        parts.append('notes')
    if not product.scent_families():
        parts.append('families')
    return parts
