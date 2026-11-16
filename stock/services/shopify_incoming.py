"""Public incoming-product teasers, derived only from the live inbound ledger."""
import json
import logging
import threading
import uuid
from datetime import timedelta
from urllib.parse import urljoin, urlsplit

from django.conf import settings
from django.db import close_old_connections
from django.db.models import Exists, OuterRef, Subquery, Sum
from django.utils import timezone

from stock.models import InboundPendingItem, Product, Purchase, Sale, ShopifyCatalogLease, ShopifyCatalogState
from .shopify_catalog_client import CatalogClient
from .shopify_client import ShopifyError
from .shopify_sync import shopify_syncable

logger = logging.getLogger(__name__)
_running = threading.Lock()
NAMESPACE, KEY = 'scentory', 'incoming_products'
MAX_PRODUCTS = 20

READ_FEED = '''query IncomingFeed {
  shop { id incoming: metafield(namespace: "scentory", key: "incoming_products") { compareDigest } }
  metafieldDefinitions(first: 5, ownerType: SHOP, namespace: "scentory", key: "incoming_products") {
    nodes { id type { name } access { storefront } }
  }
}'''
CREATE_DEFINITION = '''mutation IncomingDefinition($definition: MetafieldDefinitionInput!) {
  metafieldDefinitionCreate(definition: $definition) { createdDefinition { id } userErrors { field message } }
}'''
READ_PRODUCTS = '''query IncomingCards($ids: [ID!]!) {
  nodes(ids: $ids) { ... on Product {
    id title vendor status onlineStoreUrl featuredMedia { ... on MediaImage { image { url altText } } }
    variants(first: 50) { nodes { id title sku inventoryPolicy } }
  } }
}'''
WRITE_FEED = '''mutation IncomingWrite($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) { metafields { id } userErrors { field message code } }
}'''


def incoming_products():
    pending = InboundPendingItem.objects.filter(
        product_id=OuterRef('pk'), quantity__gt=0, inbound_order__status='pending_receipt')
    return (shopify_syncable(Product.objects).exclude(barcode='')
            .annotate(_pending=Exists(pending),
                      _bought=Exists(Purchase.objects.filter(product_id=OuterRef('pk'))),
                      _sold=Exists(Sale.objects.filter(product_id=OuterRef('pk'))),
                      _incoming_units=Subquery(pending.order_by().values('product_id').annotate(total=Sum('quantity')).values('total')[:1]),
                      _incoming_at=Subquery(pending.order_by('-inbound_order__created_at')
                                           .values('inbound_order__created_at')[:1]))
            .filter(_pending=True, _bought=False, _sold=False)
            .select_related('category', 'product_type', 'concentration')
            .prefetch_related('images').order_by('-_incoming_at', '-created_at', '-pk'))


def _https(value):
    value = str(value or '')
    parsed = urlsplit(value)
    return value if parsed.scheme == 'https' and parsed.netloc and not parsed.username and not parsed.password else ''


def _local_image(product):
    first = next(iter(product.images.all()), None)
    if not first or not first.image:
        return ''
    try:
        return _https(urljoin(settings.SHOPIFY_INCOMING_MEDIA_BASE_URL, first.image.url))
    except (ValueError, AttributeError):
        return ''


def _cards(products, client):
    known = dict(ShopifyCatalogState.objects.filter(product_id__in=[p.pk for p in products])
                 .values_list('product_id', 'product_gid'))
    matches = {}
    for p in products:
        gid = known.get(p.pk)
        if not gid:
            match = client.find_product_by_sku(p.barcode.strip())
            gid = match['id'] if match else None
        if gid:
            matches[p.pk] = gid
    gids = list(dict.fromkeys(matches.values()))
    nodes = client.graphql(READ_PRODUCTS, {'ids': gids})['nodes'] if gids else []
    catalogue = {n['id']: n for n in nodes if n}
    cards = []
    for p in products:
        node = catalogue.get(matches.get(p.pk)) or {}
        if node.get('status') == 'ARCHIVED':
            continue
        image = (node.get('featuredMedia') or {}).get('image') or {}
        public = _https(node.get('onlineStoreUrl')) if node.get('status') == 'ACTIVE' else ''
        public_path = urlsplit(public).path if public else ''
        if '/products/' not in public_path:
            public_path = ''
        variants = (node.get('variants') or {}).get('nodes', [])
        full = next((v for v in variants if v.get('sku') == p.barcode.strip()), {})
        has_decants = any(v.get('sku') in {p.barcode.strip() + '-5ML', p.barcode.strip() + '-10ML'} for v in variants)
        from .shopify_sync import DECANT_RESERVE, is_non_decant_type
        reserve = DECANT_RESERVE if has_decants and not is_non_decant_type(p.product_type_id) else 0
        cards.append({
            'title': node.get('title') or p.display_name,
            'brand': node.get('vendor') or p.brand or '',
            'image_url': _https(image.get('url')) or _local_image(p),
            'image_alt': image.get('altText') or node.get('title') or p.display_name,
            'url': public_path,
            'product_id': str(node.get('id') or '').rsplit('/', 1)[-1],
            'variant_id': str(full.get('id') or '').rsplit('/', 1)[-1],
            'variant_title': full.get('title') or '',
            'variant_sku': full.get('sku') or '',
            'inventory_policy': full.get('inventoryPolicy') or 'DENY',
            'preorder_capacity': max(0, int(p._incoming_units or 0) - reserve),
        })
    return cards


def sync_incoming(client=None, *, dry_run=False):
    if dry_run:
        return {'dry_run': True, 'products': incoming_products()[:MAX_PRODUCTS].count()}
    client = client or CatalogClient()
    client.require_configured()
    token, now = uuid.uuid4().hex, timezone.now()
    ShopifyCatalogLease.objects.get_or_create(name='incoming', defaults={'expires_at': now})
    if not ShopifyCatalogLease.objects.filter(name='incoming', expires_at__lte=now).update(
            token=token, expires_at=now + timedelta(minutes=10)):
        return {'busy': True}
    try:
        from .shopify_preorders import reconcile_preorder_policies
        preorder_guard = reconcile_preorder_policies(client)
        # Read the digest before the database: an older in-flight snapshot must
        # never overwrite a later receipt/cancellation written by another worker.
        current = client.graphql(READ_FEED)
        definitions = current['metafieldDefinitions']['nodes']
        if not definitions:
            result = client.graphql(CREATE_DEFINITION, {'definition': {
                'name': 'Novidades a caminho — InventoryApp', 'namespace': NAMESPACE,
                'key': KEY, 'ownerType': 'SHOP', 'type': 'json',
                'access': {'storefront': 'PUBLIC_READ'},
            }})['metafieldDefinitionCreate']
            if result.get('userErrors'):
                raise ShopifyError(str(result['userErrors']))
        elif definitions[0]['type']['name'] != 'json' or definitions[0]['access']['storefront'] != 'PUBLIC_READ':
            raise ShopifyError('Incoming feed definition must be JSON with PUBLIC_READ storefront access.')
        products = list(incoming_products()[:MAX_PRODUCTS])
        payload = {'schema_version': 1, 'updated_at': timezone.now().isoformat(),
                   'products': _cards(products, client)}
        field = current['shop'].get('incoming') or {}
        result = client.graphql(WRITE_FEED, {'metafields': [{
            'ownerId': current['shop']['id'], 'namespace': NAMESPACE, 'key': KEY, 'type': 'json',
            'compareDigest': field.get('compareDigest'),
            'value': json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        }]})['metafieldsSet']
        if result.get('userErrors'):
            raise ShopifyError('Incoming feed not written: ' + str(result['userErrors']))
        return {'busy': False, 'products': len(payload['products']), 'preorders': preorder_guard}
    finally:
        ShopifyCatalogLease.objects.filter(name='incoming', token=token).update(
            token='', expires_at=timezone.now())


def wake_incoming():
    if not getattr(settings, 'SHOPIFY_INCOMING_SYNC', False) or not getattr(settings, 'SHOPIFY_PUSH_BACKGROUND', True):
        return
    if not _running.acquire(blocking=False):
        return

    def run():
        close_old_connections()
        try:
            sync_incoming()
        except Exception:
            logger.exception('Incoming homepage refresh failed; the periodic worker will retry.')
        finally:
            close_old_connections()
            _running.release()

    try:
        threading.Thread(target=run, daemon=True, name='shopify-incoming').start()
    except Exception:
        _running.release()
        logger.exception('Could not start incoming homepage refresh.')
