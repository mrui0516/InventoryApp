# Shopify image sync

Push local product photos to the matching products in the Shopify store
(**Scentory**, `www.scentory.pt`). Products are matched by **barcode = Shopify
variant SKU**.

Entry points, all driven by the same service:

- **`python manage.py sync_shopify_images`** — attach photos to products that
  already exist in Shopify (bulk backfill).
- **`python manage.py sync_shopify_products`** — **create** missing products in
  Shopify (variant / price / SKU / barcode / cost / inventory / SEO / image),
  and attach images to ones that already exist.
- **Auto-sync signal** — when a product photo is uploaded in the app, it is
  synced to Shopify automatically (opt-in via `SHOPIFY_AUTO_SYNC`; with
  `SHOPIFY_AUTO_CREATE` it also *creates* the product if it isn't there yet).

Only the authenticated GraphQL calls use the Admin token; image bytes go to
Shopify's own pre-signed upload URL. No third-party image host (Cloudinary etc.).

---

## 1. One-time setup — create a custom app token

1. Shopify admin → **Settings → Apps and sales channels → Develop apps → Create an app**.
2. Name it e.g. `Inventory Image Sync`.
3. **Configuration → Admin API integration → Configure**, grant scopes:
   - `read_products`, `write_products`
   - `read_inventory`, `write_inventory`  (needed to create products with stock)
   - `read_locations`
4. **Install app**, then **API credentials → Admin API access token → Reveal** and copy it (`shpat_…`). You only see it once.

## 2. Configure the environment (never commit the token)

Set these before running the app / command:

| Variable | Value | Required |
|---|---|---|
| `SHOPIFY_STORE_DOMAIN` | `66tcd5-su.myshopify.com` | default already set |
| `SHOPIFY_ADMIN_TOKEN` | `shpat_…` (from step 1) | **yes** |
| `SHOPIFY_API_VERSION` | e.g. `2025-01` | optional (default `2025-01`) |
| `SHOPIFY_AUTO_SYNC` | `1` to auto-sync on upload | optional (default off) |
| `SHOPIFY_AUTO_CREATE` | `1` to also *create* missing products on upload | optional (default off) |
| `SHOPIFY_NEW_PRODUCT_STATUS` | `DRAFT` or `ACTIVE` for created products | optional (default `DRAFT`) |

PowerShell (current session):
```powershell
$env:SHOPIFY_ADMIN_TOKEN = "shpat_xxxxxxxxxxxxxxxx"
```

## 3. Bulk backfill

Always preview first (dry run — writes nothing):
```powershell
python manage.py sync_shopify_images --brand Lattafa
```
Then apply:
```powershell
python manage.py sync_shopify_images --brand Lattafa --apply
```

Options:

| Flag | Effect |
|---|---|
| `--apply` | Actually upload (default is a dry run) |
| `--overwrite` | Replace images even if the Shopify product already has one |
| `--brand <text>` | Only products whose brand contains `<text>` |
| `--barcode <code>` | Only the one product with this exact barcode |
| `--in-stock` | Only products with stock > 0 |
| `--limit <n>` | Process at most `n` products |

The command prints a per-product line for uploads/errors and a summary. Result
categories: `uploaded`, `already has image` (skipped), `not in Shopify`
(skipped), `no local image`, `no barcode`, `ERROR`.

## 4. Create missing products

`sync_shopify_products` creates products that aren't in Shopify yet (and images
existing ones). **Always test one first**, then a brand, then all:

```powershell
python manage.py sync_shopify_products --barcode 6290362349730          # preview one
python manage.py sync_shopify_products --barcode 6290362349730 --apply  # create one
python manage.py sync_shopify_products --brand Lattafa --apply          # then the brand
```

Each created product gets: clean title (from brand/model/name/size), vendor =
brand, product type = category, tags, description, **SEO title + meta
description**, a single variant with **price / SKU / barcode / unit cost /
tracked inventory** set to current stock at your location, and the **product
photo**. New products are created as **DRAFT** by default (review before
publishing) — pass `--status active` to publish immediately.

Extra flags on top of the shared ones: `--status active|draft`,
`--overwrite-image`.

## 5. Auto-sync on upload

Set `SHOPIFY_AUTO_SYNC=1`. Then whenever a product photo is saved in the app, the
product is synced to Shopify **after the DB commit**, in the background of the
request (failures logged, never block the save):

- product already in Shopify → its image is attached;
- product not in Shopify **and** `SHOPIFY_AUTO_CREATE=1` → it is created
  (as `SHOPIFY_NEW_PRODUCT_STATUS`, default DRAFT) with variant/inventory/SEO/image;
- otherwise → logged and skipped.

---

## Re-align barcodes after fixing EANs (`sync_shopify_barcodes`)

If you corrected barcodes **in the app**, Shopify's SKU/barcode no longer match
(the match key changed). This command re-aligns them by matching each product to
its Shopify product by **title** (the stable key — titles didn't change) and
pushing the app's current barcode into the Shopify variant's SKU + barcode.

```
python manage.py sync_shopify_barcodes                  # preview every change (dry run)
python manage.py sync_shopify_barcodes --brand Lattafa  # scope by brand
python manage.py sync_shopify_barcodes --apply          # write to Shopify
```

All Shopify products are fetched once and matched locally, so the dry run is a
few API calls; only real changes write. **Always dry-run and review first.**
Titles that occur on more than one Shopify product are treated as ambiguous and
skipped. Run the barcode re-align **before** the image/product sync so those keep
matching by SKU. After this, re-run `sync_cloudinary_images --apply` so Cloudinary
assets live under the corrected barcodes too.

## Price + inventory (`sync_shopify_inventory`) + real-time push

Push the app's **price** and **on-hand** to Shopify — the app is the source of
truth — matched by barcode = SKU:

```
python manage.py sync_shopify_inventory                    # preview (dry run)
python manage.py sync_shopify_inventory --apply            # write price + inventory
python manage.py sync_shopify_inventory --inventory-only --apply
python manage.py sync_shopify_inventory --price-only --brand Lattafa --apply
```

All Shopify variants are fetched once; only real changes write. Price sets the
100ml variant to `Product.default_price`.

**Decant-aware inventory.** Shopify variant SKUs are `<barcode>` (100ml),
`<barcode>-10ML`, `<barcode>-5ML`. Given `N = Σ purchase.remaining` full bottles:

| Variant | Available set to |
|---|---|
| 100ml | `max(N − 2, 0)` — the last 2 bottles are reserved for decanting (so ≤2 → out of stock) |
| 10ml / 5ml | `99` while `N ≥ 1`, else `0` |

Products without decant variants just get 100ml = `N`. The reserve (2) and decant
availability (99) are `DECANT_RESERVE` / `DECANT_AVAILABLE` in `shopify_sync.py`.

**Real-time:** set `SHOPIFY_INVENTORY_SYNC=1` in the environment. Then, after the
DB commit:

- a **sale / purchase / stock adjustment** pushes that product's **on-hand** to Shopify;
- changing a product's **price** in the app pushes the new **price** to Shopify.

Idempotent (absolute set), never breaks the local save, ~1–2 Shopify API calls per
event, off by default. Run the bulk `sync_shopify_inventory --apply` once first to
align everything, then turn the flag on so it stays in sync.

## Behaviour & limitations

- **Match key is barcode = SKU** (except `sync_shopify_barcodes`, which matches by
  title to *repair* the barcode). A product whose barcode doesn't match any
  Shopify variant SKU is skipped (`not in Shopify`). Placeholder/fake barcodes
  (e.g. `7777777777777`) won't match — fix the SKU in Shopify or the barcode in
  the app, then re-run.
- **Only fills missing images** by default (products with no image on Shopify).
  Use `--overwrite` / `--overwrite-image` to replace.
- **Single variant.** Created products get one default variant (matches this
  catalog: one barcode = one variant). Multi-variant products aren't modelled.
- **Created as DRAFT** by default so a mistake never goes live instantly —
  publish from Shopify or use `--status active`. **Test with one product first**
  (`--barcode … --apply`) before a bulk run.
- **Idempotent:** re-running skips products that already exist with an image, so
  it's safe to run repeatedly.
