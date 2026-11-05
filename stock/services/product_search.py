"""What a search box looks at: every word must appear in some field, so
"yara body spray" finds it although the type and the line live apart."""
from django.db.models import Q

FIELDS = ('name', 'barcode', 'brand', 'model', 'spec', 'color',
          'product_type__name_word', 'product_type__name')


def product_search_q(text):
    query = Q()
    for word in (text or '').split():
        any_field = Q()
        for field in FIELDS:
            any_field |= Q(**{f'{field}__icontains': word})
        query &= any_field
    return query
