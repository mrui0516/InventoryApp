"""Low stock: the products to reorder, shared by the Dashboard and the alerts panel.

Stock is shared by every store, so this is one list for the whole business,
cached for a few minutes - the sold-quantity subquery over every product is
the expensive part.

A product only counts once it has been part of the business: bought in at
least once, or sold. A catalogue entry that was never stocked is "0 in stock"
too, but it is not running out of anything - counting those made Today show
336 low-stock products, which meant nothing.

Two more stay out (2026-10-26): a product whose restock bell is off (the
shop is not buying it again for now) and one already on an inbound order
that has not arrived - it is being restocked. Both are still in the catalogue
and in every analysis; only this list leaves them out.
"""
from collections import defaultdict

from django.core.cache import cache
from django.db.models import Exists, F, IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce

LOW_STOCK_BELOW = 5
CACHE_SECONDS = 300
THUMB = 'c_fill,w_96,h_96,q_auto,f_auto'

OUT, LOW_1_2, LOW_3_4 = 'out', 'low12', 'low34'
BANDS = (
    (OUT, 'Out'),
    (LOW_1_2, '1-2 left'),
    (LOW_3_4, '3-4 left'),
)


def band_for(stock):
    if stock <= 0:
        return OUT
    return LOW_1_2 if stock <= 2 else LOW_3_4


GENERATION_KEY = 'lowstock:gen'


def cache_key(category_ids=()):
    scope = ','.join(str(cid) for cid in sorted(category_ids or ()))
    return f'lowstock:v3:{cache.get(GENERATION_KEY, 0)}:{scope}'


def forget():
    """Drop every cached list - after a bell is switched or an order changes."""
    try:
        cache.incr(GENERATION_KEY)
    except ValueError:
        cache.set(GENERATION_KEY, 1, None)


def in_transit_quantities(product_ids=None):
    """``{product_id: units}`` on inbound orders not received yet."""
    from django.db.models import Sum as _Sum
    from ..models import InboundPendingItem
    rows = InboundPendingItem.objects.filter(inbound_order__status='pending_receipt')
    if product_ids is not None:
        rows = rows.filter(product_id__in=list(product_ids))
    return dict(rows.values('product_id').annotate(q=_Sum('quantity')).values_list('product_id', 'q'))


def low_stock_data(category_ids=()):
    """``{'items', 'blocks', 'count', 'out', 'low12', 'low34', 'focus'}``.

    ``items`` - every low product, most urgent first (out before 1-2 before
    3-4, then the best sellers). ``blocks`` - the same, grouped by brand.
    """
    key = cache_key(category_ids)
    data = cache.get(key)
    if data is not None:
        return data

    from ..models import Product, Purchase, Sale
    from ..views import get_product_image_url
    from .cloudinary_urls import product_image_cdn_url

    stock_subq = (Purchase.objects.filter(product=OuterRef('pk')).values('product')
                  .annotate(s=Sum('remaining')).values('s')[:1])
    sold_subq = (Sale.objects.filter(product=OuterRef('pk')).values('product')
                 .annotate(s=Sum('quantity')).values('s')[:1])
    products = Product.objects.filter(restock=True).exclude(id__in=list(in_transit_quantities()))
    if category_ids:
        products = products.filter(category_id__in=list(category_ids))
    low = (products
           .annotate(stock=Coalesce(Subquery(stock_subq, output_field=IntegerField()), 0),
                     sold=Coalesce(Subquery(sold_subq, output_field=IntegerField()), 0),
                     ever_bought=Exists(Purchase.objects.filter(product=OuterRef('pk'))))
           .filter(stock__lt=LOW_STOCK_BELOW)
           .filter(Q(ever_bought=True) | Q(sold__gt=0))
           .only('id', 'brand', 'model', 'name', 'category')
           .prefetch_related('images'))

    items = []
    for product in low:
        stock = max(int(product.stock or 0), 0)
        items.append({
            'id': product.id,
            'display_name': product.display_name,
            'brand': product.brand or 'Unknown brand',
            'stock': stock,
            'sold': int(product.sold or 0),
            'band': band_for(stock),
            # Small CDN thumbnail, and the photo on this server as the fallback:
            # a product never mirrored to Cloudinary has a CDN address that 404s,
            # which is why some thumbnails came out blank.
            'image_url': product_image_cdn_url(product, THUMB) or get_product_image_url(product),
            'image_fallback': get_product_image_url(product),
        })
    items.sort(key=lambda it: (it['stock'] > 0, it['stock'], -it['sold'], it['display_name'].lower()))

    by_brand = defaultdict(list)
    for item in items:
        by_brand[item['brand']].append(item)
    blocks = [{'brand': brand, 'products': rows, 'count': len(rows),
               'out_count': sum(1 for it in rows if it['band'] == OUT)}
              for brand, rows in by_brand.items()]
    blocks.sort(key=lambda b: (-b['count'], b['brand'].lower()))

    data = {
        'items': items,
        'blocks': blocks,
        'count': len(items),
        OUT: sum(1 for it in items if it['band'] == OUT),
        LOW_1_2: sum(1 for it in items if it['band'] == LOW_1_2),
        LOW_3_4: sum(1 for it in items if it['band'] == LOW_3_4),
        'focus': items[:6],
    }
    cache.set(key, data, CACHE_SECONDS)
    return data
