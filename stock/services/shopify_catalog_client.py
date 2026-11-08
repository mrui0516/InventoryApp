"""2026-10 catalogue operations, isolated from the legacy inventory client."""
import logging
import time
import uuid

from django.conf import settings

from .shopify_client import ShopifyClient, ShopifyError
from .shopify_sync import is_non_decant_type

logger = logging.getLogger(__name__)
MANAGED_ALL_DESCRIPTION = '<!-- Inventory App: managed catalogue -->'


def variant_availability_tier(sku, variant):
    """Full bottles first, then decants, then variants Shopify cannot sell."""
    decant = sku.upper().endswith(('-5ML', '-10ML'))
    if decant and is_non_decant_type(variant.get('product_type')):
        return 2
    buyable = (variant.get('tracked', True) is False or variant.get('policy') == 'CONTINUE'
               or (variant.get('available') or 0) > 0)
    return (1 if decant else 0) if buyable else 2


class CatalogClient(ShopifyClient):
    def __init__(self, **kwargs):
        kwargs.setdefault('api_version', getattr(settings, 'SHOPIFY_CATALOG_API_VERSION', '2026-10'))
        super().__init__(**kwargs)
        self._publication = None
        self.lease_heartbeat = None
        self.availability_tiers = {}

    def graphql(self, query, variables=None):
        if self.lease_heartbeat is not None:
            self.lease_heartbeat()
        return super().graphql(query, variables)

    def product_set(self, product_input):
        data = self.graphql('''mutation CatalogUpsert($input: ProductSetInput!, $identifier: ProductSetIdentifiers) {
          productSet(input: $input, identifier: $identifier, synchronous: true) {
            product { id } userErrors { field message }
          }
        }''', {'input': product_input,
              'identifier': {'handle': product_input['handle']} if product_input.get('handle') else None})
        result = data.get('productSet') or {}
        if result.get('userErrors') or not result.get('product'):
            raise ShopifyError(f'productSet: {result.get("userErrors")}')
        return result['product']['id']

    def find_product_by_handle(self, handle):
        node = self.graphql('''query CatalogHandle($handle: String!) {
          productByIdentifier(identifier: {handle: $handle}) {
            id title status productType vendor featuredMedia { id status mediaContentType }
            variants(first: 50) { nodes { id sku price inventoryQuantity inventoryPolicy inventoryItem { id tracked } } pageInfo { hasNextPage endCursor } }
          }
        }''', {'handle': handle}).get('productByIdentifier')
        return self._complete_product(node)

    def _complete_product(self, node):
        if not node:
            return None
        rows, page = list(node['variants']['nodes']), node['variants']['pageInfo']
        while page['hasNextPage']:
            more = self.graphql('''query CatalogInventoryVariants($id: ID!, $after: String) {
              product(id: $id) { variants(first: 50, after: $after) {
                nodes { id sku price inventoryQuantity inventoryPolicy inventoryItem { id tracked } }
                pageInfo { hasNextPage endCursor }
              } }
            }''', {'id': node['id'], 'after': page['endCursor']})['product']['variants']
            rows.extend(more['nodes'])
            page = more['pageInfo']
        variants = {}
        for v in rows:
            if not v.get('sku'):
                continue
            if v['sku'] in variants:
                raise ShopifyError(f'Duplicate SKU {v["sku"]}; resolve it before syncing.')
            variants[v['sku']] = {'product_id': node['id'], 'variant_id': v['id'],
                'inventory_item_id': v['inventoryItem']['id'], 'tracked': v['inventoryItem']['tracked'],
                'policy': v['inventoryPolicy'], 'price': v['price'], 'available': v['inventoryQuantity']}
        media = node.get('featuredMedia') or {}
        return {'id': node['id'], 'title': node['title'], 'status': node['status'], 'variants': variants,
                'product_type': node['productType'], 'vendor': node['vendor'],
                'has_image': bool(media), 'image_ready': media.get('status') == 'READY'
                and media.get('mediaContentType') == 'IMAGE', 'image_failed': media.get('status') == 'FAILED'}

    def find_product_by_sku(self, sku):
        found, cursor = set(), None
        while True:
            data = self.graphql('''query CatalogSKU($q: String!, $after: String) {
              productVariants(first: 50, query: $q, after: $after) {
                nodes { sku product { id } } pageInfo { hasNextPage endCursor }
              }
            }''', {'q': 'sku:"' + sku.replace('\\', '\\\\').replace('"', '\\"') + '"', 'after': cursor})['productVariants']
            found.update(v['product']['id'] for v in data['nodes'] if v.get('sku') == sku)
            if len(found) > 1:
                raise ShopifyError(f'Multiple Shopify products use SKU {sku}; resolve the duplicate first.')
            if not data['pageInfo']['hasNextPage']:
                break
            cursor = data['pageInfo']['endCursor']
        if not found:
            return None
        node = self.graphql('''query CatalogProduct($id: ID!) {
          product(id: $id) {
            id title status productType vendor featuredMedia { id status mediaContentType }
            variants(first: 50) { nodes { id sku price inventoryQuantity inventoryPolicy inventoryItem { id tracked } }
              pageInfo { hasNextPage endCursor } }
          }
        }''', {'id': next(iter(found))}).get('product')
        if not node:
            raise ShopifyError('The matched product is temporarily unreadable; retry before creating a listing.')
        return self._complete_product(node)

    def update_catalog_identity(self, gid, kind, vendor):
        data = self.graphql('''mutation CatalogIdentity($product: ProductUpdateInput!) {
          productUpdate(product: $product) { userErrors { field message } }
        }''', {'product': {'id': gid, 'productType': kind, 'vendor': vendor}})['productUpdate']
        if data.get('userErrors'):
            raise ShopifyError(str(data['userErrors']))

    def set_inventory_available(self, inventory_item_id, location_id, quantity):
        """App-authoritative absolute stock using the 2026-04+ input contract."""
        data = self.graphql('''mutation CatalogInventory(
          $input: InventorySetQuantitiesInput!, $idempotencyKey: String!
        ) {
          inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
            userErrors { field message }
          }
        }''', {'input': {
            'name': 'available', 'reason': 'correction',
            'quantities': [{'inventoryItemId': inventory_item_id, 'locationId': location_id,
                            'quantity': int(quantity), 'changeFromQuantity': None}],
        }, 'idempotencyKey': str(uuid.uuid4())})
        result = data.get('inventorySetQuantities') or {}
        if result.get('userErrors'):
            raise ShopifyError(f'inventorySetQuantities: {result["userErrors"]}')
        return True

    def create_collection(self, title, *, handle=None):
        collection = {'title': title, 'sortOrder': 'MANUAL', 'sources': [
            {'source': {'title': 'Inventory App', 'inclusion': {'selections': []}}}]}
        if handle:
            collection['handle'] = handle
        if handle == 'all':
            collection['descriptionHtml'] = MANAGED_ALL_DESCRIPTION
        data = self.graphql('''mutation CatalogCreateCollection($collection: CollectionCreateInput!) {
          collectionCreate(collection: $collection) { collection { id } userErrors { field message } }
        }''', {'collection': collection})['collectionCreate']
        if data.get('userErrors') or not data.get('collection'):
            raise ShopifyError(str(data.get('userErrors')))
        return data['collection']['id']

    def all_variants_by_sku(self):
        out, ambiguous, cursor = {}, set(), None
        self.availability_tiers = {}
        while True:
            data = self.graphql('''query CatalogProducts($cursor: String) {
              products(first: 40, after: $cursor) {
                nodes { id status productType variants(first: 10) {
                  nodes { id sku inventoryQuantity inventoryPolicy inventoryItem { id tracked } }
                  pageInfo { hasNextPage endCursor }
                } }
                pageInfo { hasNextPage endCursor }
              }
            }''', {'cursor': cursor})['products']
            for p in data['nodes']:
                variants, page = list(p['variants']['nodes']), p['variants']['pageInfo']
                while page['hasNextPage']:
                    more = self.graphql('''query CatalogMoreVariants($id: ID!, $after: String) {
                      product(id: $id) { variants(first: 50, after: $after) {
                        nodes { id sku inventoryQuantity inventoryPolicy inventoryItem { id tracked } }
                        pageInfo { hasNextPage endCursor }
                      } }
                    }''', {'id': p['id'], 'after': page['endCursor']})['product']['variants']
                    variants.extend(more['nodes'])
                    page = more['pageInfo']
                records = [(v.get('sku') or '', {'product_id': p['id'], 'variant_id': v['id'],
                            'status': p['status'], 'product_type': p.get('productType', ''),
                            'available': v['inventoryQuantity'],
                            'policy': v['inventoryPolicy'], 'tracked': v['inventoryItem']['tracked']})
                           for v in variants]
                tier = min((variant_availability_tier(sku, rec) for sku, rec in records), default=2)
                self.availability_tiers[p['id']] = tier
                for sku, rec in records:
                    rec['availability_tier'] = tier
                    if sku:
                        if sku in ambiguous:
                            continue
                        if sku in out and out[sku]['product_id'] != p['id']:
                            logger.warning('Exclude ambiguous SKU %s from catalogue collections', sku)
                            ambiguous.add(sku)
                            out.pop(sku)
                            continue
                        out[sku] = rec
            if not data['pageInfo']['hasNextPage']:
                return out
            cursor = data['pageInfo']['endCursor']

    def all_collections(self):
        out, cursor = [], None
        while True:
            data = self.graphql('''query CatalogCollections($after: String) {
              collections(first: 50, after: $after) {
                nodes { id title handle descriptionHtml
                  sources { ... on CollectionConditionsSource { inclusion { conditions { id } } } }
                }
                pageInfo { hasNextPage endCursor }
              }
            }''', {'after': cursor})['collections']
            out.extend({'id': c['id'], 'title': c['title'], 'handle': c['handle'],
                        'managed_all': c['handle'] == 'all' and c['descriptionHtml'] == MANAGED_ALL_DESCRIPTION,
                        'smart': any('inclusion' not in s or s['inclusion']['conditions'] for s in c['sources'])}
                       for c in data['nodes'])
            if not data['pageInfo']['hasNextPage']:
                return out
            cursor = data['pageInfo']['endCursor']

    def collection_product_ids(self, gid):
        out, cursor = [], None
        while True:
            data = self.graphql('''query CatalogMembers($id: ID!, $after: String) {
              collection(id: $id) { products(first: 50, after: $after, sortKey: COLLECTION_DEFAULT) {
                nodes { id } pageInfo { hasNextPage endCursor }
              } }
            }''', {'id': gid, 'after': cursor})['collection']['products']
            out.extend(p['id'] for p in data['nodes'])
            if not data['pageInfo']['hasNextPage']:
                return out
            cursor = data['pageInfo']['endCursor']

    def online_store_publication_id(self):
        if self._publication is None:
            self._publication = getattr(settings, 'SHOPIFY_ONLINE_STORE_PUBLICATION_ID', '')
            if not self._publication:
                data = self.graphql('''query CatalogPublications {
                  publications(first: 20) { nodes { id channels(first: 10) { nodes { handle name } pageInfo { hasNextPage endCursor } } } pageInfo { hasNextPage endCursor } }
                }''')
                matches = [p['id'] for p in data['publications']['nodes']
                           if any(c['handle'].replace('-', '_') in ('online_store', 'online_store_channel')
                                  or c['name'].lower() == 'online store'
                                  for c in (p.get('channels') or {}).get('nodes', []))]
                if len(matches) != 1:
                    raise ShopifyError('Set SHOPIFY_ONLINE_STORE_PUBLICATION_ID to the Online Store publication.')
                self._publication = matches[0]
        return self._publication

    def online_product_ids(self):
        """All active Online Store listings, used to gate App collection selection."""
        publication_id = self.online_store_publication_id()
        out, seen, cursors, cursor = set(), set(), set(), None
        while True:
            data = self.graphql('''query CatalogOnlineProducts($query: String!, $publicationId: ID!, $after: String) {
              products(first: 50, query: $query, after: $after) {
                nodes { id publishedOnPublication(publicationId: $publicationId) }
                pageInfo { hasNextPage endCursor }
              }
            }''', {'query': 'status:active AND published_status:published',
                  'publicationId': publication_id, 'after': cursor})
            products = data.get('products') if isinstance(data, dict) else None
            if not isinstance(products, dict) or not isinstance(products.get('nodes'), list):
                raise ShopifyError('Invalid Online Store products response.')
            page = products.get('pageInfo')
            if not isinstance(page, dict) or type(page.get('hasNextPage')) is not bool:
                raise ShopifyError('Invalid Online Store products pagination.')
            for product in products['nodes']:
                if (not isinstance(product, dict) or not isinstance(product.get('id'), str)
                        or not product['id'] or product['id'] in seen
                        or type(product.get('publishedOnPublication')) is not bool):
                    raise ShopifyError('Invalid Online Store product publication.')
                seen.add(product['id'])
                if product['publishedOnPublication']:
                    out.add(product['id'])
            if page['hasNextPage']:
                cursor = page.get('endCursor')
                if not products['nodes'] or not isinstance(cursor, str) or not cursor or cursor in cursors:
                    raise ShopifyError('Invalid Online Store products cursor.')
                cursors.add(cursor)
            if not page['hasNextPage']:
                return out

    def publish_online(self, gid):
        publication_id = self.online_store_publication_id()
        data = self.graphql('''mutation CatalogPublish($id: ID!, $input: [PublicationInput!]!) {
          publishablePublish(id: $id, input: $input) { userErrors { field message } }
        }''', {'id': gid, 'input': [{'publicationId': publication_id}]})
        if data['publishablePublish'].get('userErrors'):
            raise ShopifyError(str(data['publishablePublish']['userErrors']))

    def wait_job(self, job):
        if not job or not job.get('id'):
            return
        until = time.monotonic() + 30
        while time.monotonic() < until:
            data = self.graphql('query CatalogJob($id: ID!) { job(id: $id) { done } }', {'id': job['id']})
            if (data.get('job') or {}).get('done'):
                return
            time.sleep(0.5)
        raise ShopifyError('Shopify collection update is still running; the worker will reconcile it.')

    def set_collection_products(self, gid, product_gids):
        desired = list(dict.fromkeys(product_gids))
        current = self.collection_product_ids(gid)
        if current == desired:
            return
        if set(current) != set(desired):
            collection = self.graphql('''query CatalogSources($id: ID!) {
              collection(id: $id) { sources { id ... on CollectionConditionsSource { inclusion { conditions { id } } } } }
            }''', {'id': gid})['collection']
            if any('inclusion' not in s or s['inclusion']['conditions'] for s in collection['sources']):
                raise ShopifyError('Refusing to replace a smart collection with manual selections.')
            chunks = [desired[i:i+250] for i in range(0, len(desired), 250)] or [[]]
            sources = [{'source': {'title': 'Inventory App', 'inclusion': {
                'selections': [{'productId': p} for p in chunk]}}} for chunk in chunks]
            self._collection_update({'id': gid, 'sortOrder': 'MANUAL',
                                     'sourcesToDelete': [s['id'] for s in collection['sources']],
                                     'sourcesToCreate': sources})
        self.reorder_collection_products(gid, desired)

    def _collection_update(self, collection):
        data = self.graphql('''mutation CatalogCollection($collection: CollectionUpdateInput!) {
          collectionUpdate(collection: $collection) { job { id } userErrors { field message } }
        }''', {'collection': collection})['collectionUpdate']
        if data.get('userErrors'):
            raise ShopifyError(str(data['userErrors']))
        self.wait_job(data.get('job'))

    def reorder_collection_products(self, gid, desired, *, current=None):
        current = list(current) if current is not None else self.collection_product_ids(gid)
        wanted = [p for p in dict.fromkeys(desired) if p in current]
        wanted.extend(p for p in current if p not in wanted)
        if wanted == current:
            return
        self._collection_update({'id': gid, 'sortOrder': 'MANUAL'})
        # Switching sort mode can reveal a different saved manual sequence.
        current = self.collection_product_ids(gid)
        wanted = [p for p in dict.fromkeys(desired) if p in current]
        wanted.extend(p for p in current if p not in wanted)
        moves = []
        for position, pid in enumerate(wanted):
            if current[position] != pid:
                current.remove(pid)
                current.insert(position, pid)
                moves.append({'id': pid, 'newPosition': str(position)})
        for offset in range(0, len(moves), 250):
            data = self.graphql('''mutation CatalogReorder($id: ID!, $moves: [MoveInput!]!) {
              collectionReorderProducts(id: $id, moves: $moves) { job { id } userErrors { field message } }
            }''', {'id': gid, 'moves': moves[offset:offset+250]})['collectionReorderProducts']
            if data.get('userErrors'):
                raise ShopifyError(str(data['userErrors']))
            self.wait_job(data.get('job'))
