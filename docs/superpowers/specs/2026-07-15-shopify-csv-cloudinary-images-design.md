# Design: Cloudinary image URLs in the Shopify CSV export

Date: 2026-07-15
Status: Approved — ready for implementation plan

## Goal

Make the Shopify product CSV export emit public Cloudinary URLs for product
images instead of local absolute URLs, so Shopify can actually fetch the images
on import.

## Context (current behavior)

- `export_shopify_inventory_csv` (`stock/views.py`) fills two image columns:
  `Product image URL` (via `_first_shopify_image_url`) and `Variant image URL`
  (via `_build_shopify_image_url`). Both flow through `_build_shopify_image_url`.
- `_build_shopify_image_url` returns `request.build_absolute_uri(image_url)`,
  which resolves to a LAN address (e.g. `http://192.168.x.x:8000/media/...`).
  Shopify's importer fetches image URLs from its own servers and cannot reach a
  private address, so both columns are effectively dead today.
- `cloudinary_sync.sync_product_primary_image` already mirrors each product's
  primary image to Cloudinary with `public_id == Product.barcode` (asset_folder
  `product_images/<brand>` is Media Library organization only). All 222 products
  that have an image are mirrored, and `CLOUDINARY_AUTO_SYNC` is now on.

Data as of this design (392 products):

| Group | Count |
| --- | --- |
| Products total (all have a barcode) | 392 |
| With an image → has a Cloudinary asset | 222 |
| Without an image → image columns already blank | 170 |
| With an image but no barcode → would lose a URL | **0** |

Because no product has an image without a barcode, every row that emits an image
URL today will emit a Cloudinary URL instead. There is no regression surface.

## Confirmed decisions

- **URL form:** 1:1 white-padded square JPEG —
  `c_pad,b_white,w_1600,h_1600,q_auto/<barcode>.jpg`. This is the same
  normalization already applied to the 112 Lattafa images in Shopify, so
  CSV-imported products display uniformly without a later fix-up pass. The
  `.jpg` extension is required: the stored originals are WebP.
- **No verification:** the URL is built from the barcode without calling
  Cloudinary. The export stays offline, instant, and independent of API
  credentials. Drift is prevented by `CLOUDINARY_AUTO_SYNC` plus the existing
  `sync_cloudinary_images --apply` command.
- **Scope:** the Shopify CSV export only. Excel exports, the catalog export, and
  the product list templates keep using local media URLs.

## Changes

### 1. New service — `stock/services/cloudinary_urls.py`

The read half of the naming contract that `cloudinary_sync` writes. Pure string
building; no network, no credentials.

```python
SHOPIFY_IMAGE_TRANSFORMATION = 'c_pad,b_white,w_1600,h_1600,q_auto'


def product_image_cdn_url(product, transformation=SHOPIFY_IMAGE_TRANSFORMATION):
    """Public URL for the product's mirrored image, or '' if there isn't one."""
```

Returns `''` when the product is falsy, has no barcode, has no primary image, or
`CLOUDINARY_CLOUD_NAME` is empty. "Has a primary image" is `product.images.first()`
being truthy — the same first-image rule `cloudinary_sync._primary_image_path`
uses to decide what to upload, so the URL and the asset agree. Otherwise returns:

```
https://res.cloudinary.com/<cloud>/image/upload/<transformation>/<barcode>.jpg
```

`transformation` is a parameter so a caller can request a different rendition
without the module owning every use case; the Shopify default lives in the
module constant.

### 2. View — `_build_shopify_image_url` (`stock/views.py`)

```python
def _build_shopify_image_url(request, product):
    image_url = get_product_image_url(product)
    if not image_url:
        return ''
    return product_image_cdn_url(product) or request.build_absolute_uri(image_url)
```

`get_product_image_url` stays the gate for "does this product have an image", so
the set of rows carrying a URL is unchanged. `_first_shopify_image_url` calls
this function and needs no edit.

### 3. Tests — `stock/tests.py`

`product_image_cdn_url`:
- product with barcode + image → the padded `.jpg` Cloudinary URL
- product with no barcode → `''`
- product with no image → `''`
- `CLOUDINARY_CLOUD_NAME` empty (`override_settings`) → `''`

CSV export:
- a product with an image → both image columns contain the `res.cloudinary.com`
  URL and no local/media host
- a product with no image → both image columns blank
- `CLOUDINARY_CLOUD_NAME` empty → the old absolute media URL is still emitted
  (fallback is not a regression)

### 4. Docs

Update `docs/STATUS.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md`.

## Data flow

```
Export Shopify CSV
  → for each product: get_product_image_url(product)
      → ''            → column blank (unchanged)
      → has an image  → product_image_cdn_url(product)
          → 'https://res.cloudinary.com/bulvpmzg/image/upload/
             c_pad,b_white,w_1600,h_1600,q_auto/<barcode>.jpg'
          → '' (no cloud configured) → request.build_absolute_uri(image_url)
  → Shopify import fetches the Cloudinary URL and stores its own CDN copy
```

## Error handling / edge cases

- No network call, so there is nothing to time out or retry.
- A product whose Cloudinary asset is missing (sync never ran for it) yields a
  URL that 404s. Shopify reports an image error for that row and still imports
  the product; re-running `sync_cloudinary_images --apply` fixes it. Accepted per
  the "no verification" decision.
- The transformation contains commas; `csv.DictWriter` quotes the field, which is
  standard CSV and Shopify parses it correctly.
- Cloudinary renders the `.jpg` derivation on first request and caches it.

## Out of scope

- Verifying asset existence at export time (rejected: adds a network dependency).
- Uploading missing images during export (rejected: couples export to sync).
- Cloudinary URLs in the Excel/catalog exports or in the app's own templates.
- Making the transformation user-configurable in the UI (it is a code constant).
