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
        """The store's first location GID (cached), for setting inventory."""
        if self._location_id:
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
