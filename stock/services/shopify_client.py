"""Minimal Shopify Admin API client for pushing product images.

Auth + store come from Django settings (which read the environment):
``SHOPIFY_STORE_DOMAIN``, ``SHOPIFY_ADMIN_TOKEN``, ``SHOPIFY_API_VERSION``.

The client covers exactly what the image sync needs:
  - resolve a product by variant SKU (== our ``Product.barcode``),
  - stage-upload a local image file to Shopify's storage, and
  - attach the uploaded image to the product.

Image bytes are pushed to Shopify's signed staged-upload URL (no auth needed
there); only the two GraphQL calls use the Admin token.
"""
import mimetypes
import os

import requests
from django.conf import settings


class ShopifyError(RuntimeError):
    """Raised for configuration problems or Shopify API/user errors."""


class ShopifyClient:
    def __init__(self, domain=None, token=None, api_version=None, timeout=30):
        self.domain = (domain or settings.SHOPIFY_STORE_DOMAIN or '').strip()
        self.token = (token or settings.SHOPIFY_ADMIN_TOKEN or '').strip()
        self.api_version = (api_version or settings.SHOPIFY_API_VERSION or '2025-01').strip()
        self.timeout = timeout
        self._location_id = None

    # ------------------------------------------------------------------ config
    def is_configured(self):
        return bool(self.domain and self.token)

    def require_configured(self):
        if not self.is_configured():
            raise ShopifyError(
                'Shopify is not configured. Set SHOPIFY_STORE_DOMAIN and '
                'SHOPIFY_ADMIN_TOKEN in the environment.'
            )

    @property
    def _graphql_url(self):
        return f'https://{self.domain}/admin/api/{self.api_version}/graphql.json'

    # ----------------------------------------------------------------- graphql
    def graphql(self, query, variables=None):
        self.require_configured()
        resp = requests.post(
            self._graphql_url,
            json={'query': query, 'variables': variables or {}},
            headers={
                'X-Shopify-Access-Token': self.token,
                'Content-Type': 'application/json',
            },
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise ShopifyError(f'Shopify HTTP {resp.status_code}: {resp.text[:300]}')
        payload = resp.json()
        if payload.get('errors'):
            raise ShopifyError(f'GraphQL errors: {payload["errors"]}')
        return payload.get('data') or {}

    # ------------------------------------------------------------ product lookup
    def find_product_by_sku(self, sku):
        """Return ``{'id', 'title', 'has_image'}`` for the product whose variant
        SKU matches ``sku``, or ``None`` if there is no exact match."""
        sku = (sku or '').strip()
        if not sku:
            return None
        data = self.graphql(
            """
            query($q: String!) {
              products(first: 10, query: $q) {
                edges { node {
                  id title featuredMedia { id }
                  variants(first: 25) { edges { node { sku } } }
                } }
              }
            }
            """,
            {'q': f'sku:{sku}'},
        )
        for edge in data.get('products', {}).get('edges', []):
            node = edge['node']
            skus = {v['node'].get('sku') for v in node.get('variants', {}).get('edges', [])}
            if sku in skus:
                return {
                    'id': node['id'],
                    'title': node.get('title', ''),
                    'has_image': node.get('featuredMedia') is not None,
                }
        return None

    def all_products_by_title(self):
        """Map exact product ``title`` -> ``{product_id, variant_id, sku, barcode}``
        for the whole store, paginated. Used to re-align products whose barcode/SKU
        changed in the app (title is the stable key). A title that occurs on more
        than one product maps to ``None`` so the caller skips ambiguous matches."""
        query = """
        query($cursor: String) {
          products(first: 100, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            edges { node {
              id title
              variants(first: 1) { edges { node { id sku barcode } } }
            } }
          }
        }
        """
        out, dup, cursor = {}, set(), None
        while True:
            data = self.graphql(query, {'cursor': cursor}).get('products', {})
            for edge in data.get('edges', []):
                node = edge['node']
                title = node.get('title', '')
                variants = node.get('variants', {}).get('edges', [])
                if not variants:
                    continue
                v = variants[0]['node']
                if title in out:
                    dup.add(title)
                out[title] = {
                    'product_id': node['id'],
                    'variant_id': v['id'],
                    'sku': v.get('sku'),
                    'barcode': v.get('barcode'),
                }
            page = data.get('pageInfo', {})
            if not page.get('hasNextPage'):
                break
            cursor = page.get('endCursor')
        for title in dup:
            out[title] = None  # ambiguous — don't touch
        return out

    def all_variants_by_sku(self):
        """Map variant ``sku`` -> ``{product_id, variant_id, inventory_item_id,
        price, available}`` for the whole store, paginated. For pushing price and
        inventory to Shopify by barcode = SKU."""
        query = """
        query($cursor: String) {
          products(first: 100, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            edges { node {
              id status
              variants(first: 10) { edges { node {
                id sku price inventoryQuantity inventoryPolicy
                inventoryItem { id tracked }
              } } }
            } }
          }
        }
        """
        out, cursor = {}, None
        while True:
            data = self.graphql(query, {'cursor': cursor}).get('products', {})
            for edge in data.get('edges', []):
                node = edge['node']
                for ve in node.get('variants', {}).get('edges', []):
                    v = ve['node']
                    sku = (v.get('sku') or '').strip()
                    if not sku:
                        continue
                    inv = v.get('inventoryItem') or {}
                    out[sku] = {
                        'product_id': node['id'],
                        'status': node.get('status'),
                        'variant_id': v['id'],
                        'inventory_item_id': inv.get('id'),
                        'tracked': inv.get('tracked'),
                        'policy': v.get('inventoryPolicy'),
                        'price': v.get('price'),
                        'available': v.get('inventoryQuantity'),
                    }
            page = data.get('pageInfo', {})
            if not page.get('hasNextPage'):
                break
            cursor = page.get('endCursor')
        return out

    def find_variant_by_sku(self, sku):
        """Single-product form of ``all_variants_by_sku``: returns
        ``{product_id, variant_id, inventory_item_id, price, available}`` for the
        variant whose SKU matches, or ``None``. Used by the real-time push."""
        sku = (sku or '').strip()
        if not sku:
            return None
        data = self.graphql(
            """
            query($q: String!) {
              products(first: 10, query: $q) {
                edges { node {
                  id
                  variants(first: 25) { edges { node {
                    id sku price inventoryQuantity inventoryPolicy inventoryItem { id tracked }
                  } } }
                } }
              }
            }
            """,
            {'q': f'sku:{sku}'},
        )
        for edge in data.get('products', {}).get('edges', []):
            node = edge['node']
            for ve in node.get('variants', {}).get('edges', []):
                v = ve['node']
                if (v.get('sku') or '').strip() == sku:
                    inv = v.get('inventoryItem') or {}
                    return {
                        'product_id': node['id'],
                        'variant_id': v['id'],
                        'inventory_item_id': inv.get('id'),
                        'tracked': inv.get('tracked'),
                        'policy': v.get('inventoryPolicy'),
                        'price': v.get('price'),
                        'available': v.get('inventoryQuantity'),
                    }
        return None

    def set_variant_stocked(self, product_id, variant_id):
        """Make a variant track inventory and refuse overselling (tracked + DENY),
        so its quantity actually controls whether it can be bought. Without this a
        variant with tracked=False / policy=CONTINUE stays buyable at quantity 0."""
        data = self.graphql(
            """
            mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
              productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                productVariants { id }
                userErrors { field message }
              }
            }
            """,
            {'productId': product_id, 'variants': [{
                'id': variant_id,
                'inventoryPolicy': 'DENY',
                'inventoryItem': {'tracked': True},
            }]},
        )
        result = data.get('productVariantsBulkUpdate', {})
        if result.get('userErrors'):
            raise ShopifyError(f'productVariantsBulkUpdate(policy): {result["userErrors"]}')
        return (result.get('productVariants') or [{}])[0].get('id')

    def update_variant_price(self, product_id, variant_id, price):
        """Set a variant's price (a string like '12.00')."""
        data = self.graphql(
            """
            mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
              productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                productVariants { id price }
                userErrors { field message }
              }
            }
            """,
            {'productId': product_id, 'variants': [{'id': variant_id, 'price': price}]},
        )
        result = data.get('productVariantsBulkUpdate', {})
        if result.get('userErrors'):
            raise ShopifyError(f'productVariantsBulkUpdate(price): {result["userErrors"]}')
        variants = result.get('productVariants') or []
        return variants[0]['id'] if variants else None

    def set_inventory_available(self, inventory_item_id, location_id, quantity):
        """Set the 'available' quantity for an inventory item at a location."""
        data = self.graphql(
            """
            mutation($input: InventorySetQuantitiesInput!) {
              inventorySetQuantities(input: $input) {
                inventoryAdjustmentGroup { createdAt }
                userErrors { field message }
              }
            }
            """,
            {'input': {
                'name': 'available',
                'reason': 'correction',
                'ignoreCompareQuantity': True,
                'quantities': [{
                    'inventoryItemId': inventory_item_id,
                    'locationId': location_id,
                    'quantity': int(quantity),
                }],
            }},
        )
        result = data.get('inventorySetQuantities', {})
        if result.get('userErrors'):
            raise ShopifyError(f'inventorySetQuantities: {result["userErrors"]}')
        return True

    def update_variant_barcode_sku(self, product_id, variant_id, sku, barcode):
        """Set a variant's SKU + barcode (pushes a corrected EAN to Shopify)."""
        data = self.graphql(
            """
            mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
              productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                productVariants { id barcode inventoryItem { sku } }
                userErrors { field message }
              }
            }
            """,
            {'productId': product_id, 'variants': [{
                'id': variant_id,
                'barcode': barcode,
                'inventoryItem': {'sku': sku},
            }]},
        )
        result = data.get('productVariantsBulkUpdate', {})
        if result.get('userErrors'):
            raise ShopifyError(f'productVariantsBulkUpdate: {result["userErrors"]}')
        variants = result.get('productVariants') or []
        return variants[0]['id'] if variants else None

    # --------------------------------------------------------------- image upload
    def stage_and_upload_image(self, filepath, filename=None):
        """Upload a local image file to Shopify's staged storage; return the
        ``resourceUrl`` to hand to ``attach_image``."""
        if not os.path.exists(filepath):
            raise ShopifyError(f'Image file not found: {filepath}')
        filename = filename or os.path.basename(filepath)
        mime = mimetypes.guess_type(filename)[0] or 'image/jpeg'

        data = self.graphql(
            """
            mutation($input: [StagedUploadInput!]!) {
              stagedUploadsCreate(input: $input) {
                stagedTargets { url resourceUrl parameters { name value } }
                userErrors { field message }
              }
            }
            """,
            {'input': [{
                'filename': filename,
                'mimeType': mime,
                'resource': 'IMAGE',
                'httpMethod': 'POST',
            }]},
        )
        result = data.get('stagedUploadsCreate', {})
        if result.get('userErrors'):
            raise ShopifyError(f'stagedUploadsCreate: {result["userErrors"]}')
        targets = result.get('stagedTargets') or []
        if not targets:
            raise ShopifyError('stagedUploadsCreate returned no target.')
        target = targets[0]

        # POST the bytes to the pre-signed URL (fields first, file last).
        form = [(p['name'], (None, p['value'])) for p in target['parameters']]
        with open(filepath, 'rb') as fh:
            form.append(('file', (filename, fh, mime)))
            upload = requests.post(target['url'], files=form, timeout=self.timeout)
        if upload.status_code not in (200, 201, 204):
            raise ShopifyError(f'Staged upload failed HTTP {upload.status_code}: {upload.text[:300]}')
        return target['resourceUrl']

    def get_location_id(self):
        """The location GID to set inventory at (cached). Prefers the configured
        ``SHOPIFY_LOCATION_ID`` (so a multi-location store targets the right one);
        otherwise falls back to the store's first location."""
        if self._location_id:
            return self._location_id
        configured = (getattr(settings, 'SHOPIFY_LOCATION_ID', '') or '').strip()
        if configured:
            self._location_id = configured
            return self._location_id
        data = self.graphql('{ locations(first: 1) { edges { node { id } } } }')
        edges = data.get('locations', {}).get('edges', [])
        if not edges:
            raise ShopifyError('No Shopify location found for inventory.')
        self._location_id = edges[0]['node']['id']
        return self._location_id

    def product_set(self, product_input):
        """Create/update a product in one call (variant, inventory, SEO, image).

        Returns the product GID. Raises ShopifyError on user errors.
        """
        data = self.graphql(
            """
            mutation($input: ProductSetInput!) {
              productSet(input: $input, synchronous: true) {
                product { id }
                userErrors { field message }
              }
            }
            """,
            {'input': product_input},
        )
        result = data.get('productSet', {})
        if result.get('userErrors'):
            raise ShopifyError(f'productSet: {result["userErrors"]}')
        product = result.get('product')
        return product['id'] if product else None

    def set_product_status(self, product_gid, status):
        """Set a product's status to 'ACTIVE' or 'DRAFT' (used to hide/show it)."""
        data = self.graphql(
            """
            mutation($product: ProductUpdateInput!) {
              productUpdate(product: $product) {
                product { id status }
                userErrors { field message }
              }
            }
            """,
            {'product': {'id': product_gid, 'status': status}},
        )
        result = data.get('productUpdate', {})
        if result.get('userErrors'):
            raise ShopifyError(f'productUpdate(status): {result["userErrors"]}')
        return (result.get('product') or {}).get('id')

    def find_collection_by_title(self, title):
        """The GID of the manual collection with this exact title, or None."""
        esc = (title or '').replace('\\', '\\\\').replace('"', '\\"')
        data = self.graphql(
            'query($q: String!) { collections(first: 10, query: $q) { edges { node { id title } } } }',
            {'q': f'title:"{esc}"'},
        )
        for edge in data.get('collections', {}).get('edges', []):
            if edge['node'].get('title') == title:
                return edge['node']['id']
        return None

    def create_collection(self, title):
        """Create a manual collection (MANUAL sort so we can order it), return GID."""
        data = self.graphql(
            """
            mutation($input: CollectionInput!) {
              collectionCreate(input: $input) {
                collection { id }
                userErrors { field message }
              }
            }
            """,
            {'input': {'title': title, 'sortOrder': 'MANUAL'}},
        )
        result = data.get('collectionCreate', {})
        if result.get('userErrors'):
            raise ShopifyError(f'collectionCreate: {result["userErrors"]}')
        return (result.get('collection') or {}).get('id')

    def all_products_full_variants(self):
        """[{product_id, title, variants: [{id, sku}]}] for every product."""
        out, cursor = [], None
        query = """
        query($cursor: String) {
          products(first: 100, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            edges { node { id title variants(first: 10) { edges { node { id sku } } } } }
          }
        }
        """
        while True:
            data = self.graphql(query, {'cursor': cursor}).get('products', {})
            for edge in data.get('edges', []):
                node = edge['node']
                variants = [{'id': ve['node']['id'], 'sku': (ve['node'].get('sku') or '').strip()}
                            for ve in node.get('variants', {}).get('edges', [])]
                out.append({'product_id': node['id'], 'title': node.get('title', ''),
                            'variants': variants})
            page = data.get('pageInfo', {})
            if not page.get('hasNextPage'):
                break
            cursor = page.get('endCursor')
        return out

    def fix_variant_sku(self, product_id, variant_id, sku):
        """Set a variant's SKU and make it tracked + DENY. Used to re-key decant
        variants whose SKU still carries an old barcode base."""
        data = self.graphql(
            """
            mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
              productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                productVariants { id }
                userErrors { field message }
              }
            }
            """,
            {'productId': product_id, 'variants': [{
                'id': variant_id,
                'inventoryPolicy': 'DENY',
                'inventoryItem': {'sku': sku, 'tracked': True},
            }]},
        )
        result = data.get('productVariantsBulkUpdate', {})
        if result.get('userErrors'):
            raise ShopifyError(f'productVariantsBulkUpdate(sku): {result["userErrors"]}')
        return (result.get('productVariants') or [{}])[0].get('id')

    def delete_product(self, product_gid):
        """Delete a product from Shopify. Returns the deleted product's GID."""
        data = self.graphql(
            """
            mutation($input: ProductDeleteInput!) {
              productDelete(input: $input) {
                deletedProductId
                userErrors { field message }
              }
            }
            """,
            {'input': {'id': product_gid}},
        )
        result = data.get('productDelete', {})
        if result.get('userErrors'):
            raise ShopifyError(f'productDelete: {result["userErrors"]}')
        return result.get('deletedProductId')

    def all_collections(self):
        """[{id, title, smart}] for every collection (smart = rule-based)."""
        out, cursor = [], None
        query = """
        query($cursor: String) {
          collections(first: 100, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            edges { node { id title ruleSet { appliedDisjunctively } } }
          }
        }
        """
        while True:
            data = self.graphql(query, {'cursor': cursor}).get('collections', {})
            for edge in data.get('edges', []):
                node = edge['node']
                out.append({'id': node['id'], 'title': node.get('title', ''),
                            'smart': node.get('ruleSet') is not None})
            page = data.get('pageInfo', {})
            if not page.get('hasNextPage'):
                break
            cursor = page.get('endCursor')
        return out

    def collection_add_products(self, collection_gid, product_gids):
        """Add products to a manual collection (a no-op for ones already in it).
        Fails on smart collections — the caller should only pass manual ones."""
        ids = list(dict.fromkeys(product_gids))
        if not ids:
            return
        data = self.graphql(
            'mutation($id: ID!, $ids: [ID!]!) { collectionAddProducts(id: $id, productIds: $ids) '
            '{ userErrors { field message } } }',
            {'id': collection_gid, 'ids': ids})
        errs = data.get('collectionAddProducts', {}).get('userErrors')
        if errs:
            raise ShopifyError(f'collectionAddProducts: {errs}')

    def collection_product_ids(self, collection_gid):
        """All product GIDs currently in the collection (paginated)."""
        ids, cursor = [], None
        query = """
        query($id: ID!, $cursor: String) {
          collection(id: $id) {
            products(first: 100, after: $cursor) {
              pageInfo { hasNextPage endCursor }
              edges { node { id } }
            }
          }
        }
        """
        while True:
            coll = self.graphql(query, {'id': collection_gid, 'cursor': cursor}).get('collection') or {}
            prods = coll.get('products', {})
            for edge in prods.get('edges', []):
                ids.append(edge['node']['id'])
            page = prods.get('pageInfo', {})
            if not page.get('hasNextPage'):
                break
            cursor = page.get('endCursor')
        return ids

    def set_collection_products(self, collection_gid, ordered_gids):
        """Make the manual collection contain exactly ``ordered_gids`` in that order
        (add missing, remove extras, then reorder). Returns {added, removed}."""
        desired = list(dict.fromkeys(ordered_gids))  # de-dup, keep order
        current = self.collection_product_ids(collection_gid)
        to_add = [g for g in desired if g not in current]
        to_remove = [g for g in current if g not in desired]
        if to_add:
            data = self.graphql(
                'mutation($id: ID!, $ids: [ID!]!) { collectionAddProducts(id: $id, productIds: $ids) '
                '{ userErrors { field message } } }',
                {'id': collection_gid, 'ids': to_add})
            errs = data.get('collectionAddProducts', {}).get('userErrors')
            if errs:
                raise ShopifyError(f'collectionAddProducts: {errs}')
        if to_remove:
            data = self.graphql(
                'mutation($id: ID!, $ids: [ID!]!) { collectionRemoveProducts(id: $id, productIds: $ids) '
                '{ job { id } userErrors { field message } } }',
                {'id': collection_gid, 'ids': to_remove})
            errs = data.get('collectionRemoveProducts', {}).get('userErrors')
            if errs:
                raise ShopifyError(f'collectionRemoveProducts: {errs}')
        if desired:
            moves = [{'id': g, 'newPosition': str(i)} for i, g in enumerate(desired)]
            data = self.graphql(
                'mutation($id: ID!, $moves: [MoveInput!]!) { collectionReorderProducts(id: $id, moves: $moves) '
                '{ job { id } userErrors { field message } } }',
                {'id': collection_gid, 'moves': moves})
            errs = data.get('collectionReorderProducts', {}).get('userErrors')
            if errs:
                raise ShopifyError(f'collectionReorderProducts: {errs}')
        return {'added': len(to_add), 'removed': len(to_remove)}

    def update_product_description(self, product_gid, description_html):
        """Set a product's description (HTML), preserving the app's formatting."""
        data = self.graphql(
            """
            mutation($product: ProductUpdateInput!) {
              productUpdate(product: $product) {
                product { id }
                userErrors { field message }
              }
            }
            """,
            {'product': {'id': product_gid, 'descriptionHtml': description_html}},
        )
        result = data.get('productUpdate', {})
        if result.get('userErrors'):
            raise ShopifyError(f'productUpdate(description): {result["userErrors"]}')
        return (result.get('product') or {}).get('id')

    def attach_image(self, product_gid, resource_url, alt=''):
        """Attach a staged (or public) image URL to a product as media."""
        data = self.graphql(
            """
            mutation($productId: ID!, $media: [CreateMediaInput!]!) {
              productCreateMedia(productId: $productId, media: $media) {
                media { ... on MediaImage { id } status }
                mediaUserErrors { field message }
              }
            }
            """,
            {'productId': product_gid, 'media': [{
                'originalSource': resource_url,
                'mediaContentType': 'IMAGE',
                'alt': alt or '',
            }]},
        )
        result = data.get('productCreateMedia', {})
        if result.get('mediaUserErrors'):
            raise ShopifyError(f'productCreateMedia: {result["mediaUserErrors"]}')
        media = result.get('media') or []
        return media[0]['id'] if media else None
