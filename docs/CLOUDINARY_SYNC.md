# Cloudinary image mirror

Mirror each product's **primary image** to Cloudinary (cloud `bulvpmzg`), which
is the canonical image host. Local `media/` storage stays primary. The matching
key for later Shopify use is the **barcode**.

## What it does

- On product-image **create / replace / delete** in the app, the product's
  current primary image (`product.images.first()`) is mirrored to Cloudinary:
  `public_id = <barcode>`, `asset_folder = product_images/<brand>`
  (`overwrite=true`). When the last image is removed, the asset is deleted.
- The account is in **dynamic-folders** mode, so the folder is organizational
  only; the delivery URL is
  `https://res.cloudinary.com/bulvpmzg/image/upload/<barcode>.<ext>`.
- New local uploads are stored under `media/product_images/<brand>/`.

Only the primary image is mirrored (one Cloudinary asset per product), matching
how Shopify uses a single image and keeping the barcode → asset mapping
one-to-one.

## 1. One-time setup — Cloudinary credentials

1. Cloudinary dashboard → **Settings → API Keys**: copy your **API Key** and
   **API Secret**.
2. Set the environment variables (never commit the secret):

| Variable | Value | Required |
|---|---|---|
| `CLOUDINARY_CLOUD_NAME` | `bulvpmzg` | default already set |
| `CLOUDINARY_API_KEY` | from dashboard | **yes** |
| `CLOUDINARY_API_SECRET` | from dashboard | **yes** |
| `CLOUDINARY_FOLDER` | `product_images` | optional (default) |
| `CLOUDINARY_AUTO_SYNC` | `1` to auto-mirror on upload | optional (default off) |

PowerShell (current session):
```powershell
$env:CLOUDINARY_API_KEY = "..."
$env:CLOUDINARY_API_SECRET = "..."
$env:CLOUDINARY_AUTO_SYNC = "1"
```

## 2. Auto-mirror on upload

Set `CLOUDINARY_AUTO_SYNC=1`. Then, whenever a product image is saved or deleted
in the app, the product's primary image is synced to Cloudinary **after the DB
commit**, in the background of the request (failures logged, never block the
save):

- image added / replaced → primary uploaded to `product_images/<brand>` as
  `public_id=<barcode>` (overwrite);
- last image removed → the `<barcode>` asset is deleted;
- product without a barcode → skipped.

## 3. Batch command

Always preview first (dry run — writes nothing):
```powershell
python manage.py sync_cloudinary_images --brand Lattafa
```
Then apply:
```powershell
python manage.py sync_cloudinary_images --brand Lattafa --apply
```

Options:

| Flag | Effect |
|---|---|
| `--apply` | Actually upload (default is a dry run) |
| `--brand <text>` | Only products whose brand contains `<text>` |
| `--barcode <code>` | Only the one product with this exact barcode |
| `--limit <n>` | Process at most `n` products |

The command prints a per-product line and a summary
(`uploaded` / `deleted` / `no_barcode` / `error`).

## Behaviour & limitations

- **Primary image only** — one Cloudinary asset per product
  (`public_id=barcode`). Products with multiple images mirror only the primary.
- **New uploads only** — this feature does not retro-fold existing local files
  or existing Cloudinary assets into brand folders; it acts on saves/deletes
  going forward (use the batch command to push the current catalog on demand).
- **No Shopify push** — Cloudinary → Shopify stays a separate step, matched by
  barcode.
- **Match key is the barcode.** A product without a barcode is skipped.
- Failures are logged and never break saving/deleting the local image; the whole
  feature is gated off by default via `CLOUDINARY_AUTO_SYNC`.
