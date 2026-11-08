"""Collection membership from Shopify publications and App stock/sales rules."""
from datetime import timedelta

from django.conf import settings
from django.db.models import F, IntegerField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from stock.models import Product, Sale, ShopifyCatalogState
from .shopify_catalog_client import variant_availability_tier
from .shopify_client import ShopifyError
from .shopify_sync import shopify_syncable

NEW_TITLE = 'Novidades'
RESTOCK_TITLE = 'Restock'
TOP_TITLE = 'O Mais Vendido do Mês'
GENDER_COLLECTIONS = {
    'men': 'Homem - Perfumes Árabes by Scentory',
    'women': 'Mulher - Perfumes Árabes by Scentory',
    'unisex': 'Unissexo - Perfumes Árabes by Scentory',
}


def product_availability_tiers(variants):
    tiers = {}
    for sku, variant in variants.items():
        gid = variant['product_id']
        tier = variant.get('availability_tier', variant_availability_tier(sku, variant))
        tiers[gid] = min(tiers.get(gid, 2), tier)
    return tiers


def collection_targets(variants, *, online_product_ids=(), availability_tiers=None, now=None):
    now = now or timezone.now()
    products = list(shopify_syncable(Product.objects).exclude(barcode='').annotate(
        on_hand=Coalesce(Sum('purchase__remaining'), Value(0), output_field=IntegerField())))
    matched = [(p, variants[p.barcode.strip()]) for p in products
               if p.barcode and p.barcode.strip() in variants
               and variants[p.barcode.strip()].get('status') == 'ACTIVE']

    def ids(pairs):
        return list(dict.fromkeys(rec['product_id'] for _, rec in pairs))

    online = set(online_product_ids)
    tiers = availability_tiers if availability_tiers is not None else product_availability_tiers(variants)
    newest = sorted((pair for pair in matched if pair[1]['product_id'] in online
                     and pair[0].on_hand > 0 and tiers.get(pair[1]['product_id'], 2) < 2),
                    key=lambda pair: (-pair[0].created_at.timestamp(), -pair[0].pk))
    cutoff = now - timedelta(days=max(1, getattr(settings, 'SHOPIFY_RESTOCK_DAYS', 30)))
    dates = dict(ShopifyCatalogState.objects.filter(last_restocked_at__gte=cutoff)
                 .values_list('product_id', 'last_restocked_at'))
    restocked = sorted((pair for pair in matched if pair[0].pk in dates and pair[0].on_hand > 0),
                       key=lambda pair: (-dates[pair[0].pk].timestamp(), -pair[0].pk))
    local = timezone.localtime(now)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year+1, month=1) if start.month == 12 else start.replace(month=start.month+1)
    sales = (Sale.objects.filter(product_id__in=[p.pk for p, _ in matched])
             .annotate(at=Coalesce('order__created_at', 'date'),
                       kind=Coalesce('order__customer__kind', 'customer__kind', Value('retail')))
             .filter(at__gte=start, at__lt=end).exclude(kind='revenda')
             .values('product_id').annotate(units=Sum('quantity')))
    units = {row['product_id']: row['units'] for row in sales}
    ranked = sorted(matched, key=lambda pair: (tiers.get(pair[1]['product_id'], 2),
                                              -units.get(pair[0].pk, 0), pair[0].pk))
    targets = {NEW_TITLE: ids(newest)[:20], RESTOCK_TITLE: ids(restocked),
               TOP_TITLE: ids(pair for pair in ranked if units.get(pair[0].pk, 0) > 0)[:5]}
    for gender, title in GENDER_COLLECTIONS.items():
        targets[title] = ids(pair for pair in ranked
                            if pair[0].gender == gender and pair[0].product_type_id == 'perfume')
    return targets


def sync_storefront(client=None, *, dry_run=False):
    if client is None:
        from .shopify_catalog_client import CatalogClient
        client = CatalogClient()
    client.require_configured()
    variants = client.all_variants_by_sku()
    online = client.online_product_ids()
    tiers = product_availability_tiers(variants)
    catalog_tiers = getattr(client, 'availability_tiers', {})
    if isinstance(catalog_tiers, dict):
        tiers.update(catalog_tiers)
    targets = collection_targets(variants, online_product_ids=online, availability_tiers=tiers)
    collections = {c['title']: c for c in client.all_collections()}
    all_collection = next((c for c in collections.values() if c.get('handle') == 'all'), None)
    all_members = sorted(sorted(online), key=lambda gid: tiers.get(gid, 2))
    reorders = []
    if not dry_run:
        for title, collection in collections.items():
            if title in (NEW_TITLE, RESTOCK_TITLE, TOP_TITLE) or collection.get('managed_all'):
                continue
            current = client.collection_product_ids(collection['id'])
            ranked = [gid for gid in targets.get(title, []) if gid in current]
            ranked.extend(gid for gid in current if gid not in ranked)
            desired = sorted(ranked, key=lambda gid: tiers.get(gid, 2))
            reorders.append((collection['id'], desired, current))
    for title in (NEW_TITLE, RESTOCK_TITLE, TOP_TITLE):
        c = collections.get(title)
        if c and c.get('smart'):
            raise ShopifyError(f'{title} must be a manual collection.')
        if not dry_run:
            gid = c['id'] if c else client.create_collection(title)
            client.publish_online(gid)
            client.set_collection_products(gid, targets[title])
    if not dry_run:
        if all_collection and all_collection.get('managed_all'):
            client.set_collection_products(all_collection['id'], all_members)
        for gid, desired, current in reorders:
            client.reorder_collection_products(gid, desired, current=current)
    return {title: len(ids) for title, ids in targets.items()}


def backfill_restocks(*, now=None):
    """Only infer history when the existing journal reconciles without adjustments."""
    from .stock_ledger import build_stock_ledger
    now = now or timezone.now()
    cutoff = now - timedelta(days=max(1, getattr(settings, 'SHOPIFY_RESTOCK_DAYS', 30)))
    count = 0
    for p in shopify_syncable(Product.objects).exclude(barcode=''):
        if p.total_stock() <= 0 or p.stock_adjustment_logs.exists():
            continue
        journal = build_stock_ledger(p)
        events = list(reversed(journal['events']))
        if journal['has_discrepancy'] or any(e['balance'] < 0 for e in events):
            continue
        before, seen, last = 0, False, None
        for e in events:
            if e['kind'] == 'purchase' and e['delta'] > 0:
                if seen and before == 0:
                    last = e['at']
                seen = True
            before = e['balance']
        if last and last >= cutoff:
            state, _ = ShopifyCatalogState.objects.get_or_create(product=p)
            if state.last_restocked_at is None or state.last_restocked_at < last:
                ShopifyCatalogState.objects.filter(pk=state.pk).update(last_restocked_at=last)
                count += 1
    return count
