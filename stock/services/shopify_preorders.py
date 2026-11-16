"""Restrict existing STOQ offers to new products on pending inbound orders.

This guard can close an offer's inventory policy, never open an offer, change
quantities, or create a selling plan. STOQ remains responsible for checkout.
"""
from .shopify_client import ShopifyError


READ_OFFER_VARIANTS = '''query ScentoryPreorderEligibility($ids: [ID!]!) {
  nodes(ids: $ids) { ... on ProductVariant {
    id sku inventoryPolicy inventoryItem { id tracked }
    product { id variants(first: 50) { nodes { sku } pageInfo { hasNextPage } } }
  } }
}'''


def incoming_capacity(product, skus):
    from .shopify_incoming import incoming_products
    from .shopify_sync import DECANT_RESERVE, DECANT_SUFFIXES, is_non_decant_type
    if not getattr(product, 'pk', None):
        return 0
    units = incoming_products().filter(pk=product.pk).values_list('_incoming_units', flat=True).first()
    has_decants = any(product.barcode.strip() + suffix in skus for suffix in DECANT_SUFFIXES)
    reserve = DECANT_RESERVE if has_decants and not is_non_decant_type(product.product_type_id) else 0
    return max(0, int(units or 0) - reserve)


def remembers_preorder(product, variant_id, offered_ids, *, dry_run=False):
    """Keep reservation protection after an offer is disabled or deleted."""
    from stock.models import ShopifyCatalogState
    offered = variant_id in offered_ids
    if not getattr(product, 'pk', None):
        return offered
    if offered and not dry_run:
        state, _ = ShopifyCatalogState.objects.get_or_create(product=product)
        if state.preorder_variant_gid != variant_id:
            ShopifyCatalogState.objects.filter(pk=state.pk).update(preorder_variant_gid=variant_id)
        return True
    return offered or ShopifyCatalogState.objects.filter(
        product=product, preorder_variant_gid=variant_id).exists()


def reconcile_preorder_policies(client):
    """Close ineligible CONTINUE policies during each existing incoming refresh."""
    from .shopify_incoming import incoming_products
    from stock.models import Product, ShopifyCatalogState
    members, historical = client.active_preorder_variant_ids(), client.preorder_variant_ids()
    if not isinstance(members, (set, frozenset)) or not isinstance(historical, (set, frozenset)):
        raise ShopifyError('Invalid STOQ preorder membership response.')
    result = {'denied': []}
    remembered = set(ShopifyCatalogState.objects.exclude(preorder_variant_gid='')
                     .values_list('preorder_variant_gid', flat=True))
    candidates = historical | remembered
    if not candidates:
        return result
    eligible = {p.barcode.strip(): p for p in incoming_products()}
    ids, location_id = sorted(candidates), None
    for start in range(0, len(ids), 50):
        rows = client.graphql(READ_OFFER_VARIANTS, {'ids': ids[start:start + 50]})['nodes']
        known = {p.barcode.strip(): p for p in Product.objects.filter(barcode__in=[r['sku'] for r in rows if r])}
        for row in rows:
            if not row:
                continue
            if row.get('sku') in known:
                remembers_preorder(known[row['sku']], row['id'], historical)
            if row.get('inventoryPolicy') != 'CONTINUE':
                continue
            product = eligible.get(row.get('sku', ''))
            item = row.get('inventoryItem') or {}
            siblings = row['product']['variants']
            if (siblings.get('pageInfo') or {}).get('hasNextPage'):
                raise ShopifyError('Preorder product has more than 50 variants; review its capacity manually.')
            capacity = incoming_capacity(product, {v['sku'] for v in siblings['nodes']}) if product else 0
            allow = False
            if row['id'] in members and capacity > 0 and item.get('tracked') is True:
                location_id = location_id or client.get_location_id()
                allow = capacity > client.committed_quantity(item['id'], location_id)
            if not allow:
                client.set_variant_stocked(row['product']['id'], row['id'])
                result['denied'].append(row['id'])
    return result
