# Design: Auto-mirror product images to Cloudinary (brand-organized)

Date: 2026-07-13
Status: Approved — ready for implementation plan

## Goal

When a `ProductImage` is **created, replaced, or deleted** in the local Django
app, automatically mirror the product's **primary image** to Cloudinary (cloud
`bulvpmzg`), which acts as the canonical image host / CDN. Local `media/`
storage stays primary and unchanged in role. **No automatic Shopify push** — the
Cloudinary → Shopify step stays separate/on-demand and matches by barcode.

## Confirmed decisions

- **Mirror model (not replace):** local storage remains primary; Cloudinary gets
  a mirrored copy. (chosen over a Cloudinary-backed `ImageField`)
- **Cloudinary only:** the auto-flow stops at Cloudinary; Shopify attach is a
  separate/later step.
- **Triggers:** create + replace/update + delete.
- **Naming (account is in Cloudinary *dynamic-folders* mode):**
  - `public_id = <barcode>` (no folder prefix — in dynamic-folder mode the
    folder is metadata, not part of `public_id` or the delivery URL).
  - `asset_folder = product_images/<brand>` (fallback `product_images` when brand
    is blank) — organizational only, does **not** affect the URL or matching.
  - `unique_filename = false`, `overwrite = true`.
  - Deterministic delivery URL: `https://res.cloudinary.com/bulvpmzg/image/upload/<barcode>.<ext>`.
- **One Cloudinary asset per product** = the product's primary image
  (`product.images.first()`). Multiple images per product → only the primary is
  mirrored (Shopify uses one; barcode naming is one-to-one).
- **Local images also organized by brand:** `ProductImage.image.upload_to` becomes
  `product_images/<brand>/<filename>` (fallback `product_images/<filename>`).
- **Scope: new uploads only.** Existing local files and existing ~400 Cloudinary
  assets stay where they are (flat). No retro-migration.
- Include a `manage.py sync_cloudinary_images` batch command (dry-run default).

## Why these choices

- **Dynamic-folder mode** was confirmed from the existing assets
  (`folder: null`, `asset_folder: "product_images"`, and delivery URLs with no
  folder segment). Keying `public_id` on the **barcode** makes future
  Cloudinary → Shopify matching a direct lookup and avoids the random-suffix
  problem seen during the manual backfill.
- **Mirror over storage-backend swap** keeps the app working on LAN/offline and
  is the same low-risk pattern as the existing Shopify sync signal.

## Configuration — `inventory_system/settings.py`

New block, env-first, mirroring the existing Shopify block:

| Setting | Default | Notes |
|---|---|---|
| `CLOUDINARY_CLOUD_NAME` | `bulvpmzg` | |
| `CLOUDINARY_API_KEY` | `''` | required to sync |
| `CLOUDINARY_API_SECRET` | `''` | required to sync |
| `CLOUDINARY_AUTO_SYNC` | off | master switch, off by default |
| `CLOUDINARY_FOLDER` | `product_images` | base asset folder |

## Dependency

- Add `cloudinary` (official Python SDK) to `requirements.txt`. Imported lazily
  so the app runs without it when the feature is off.

## Components

### 1. `stock/services/cloudinary_client.py` — thin SDK wrapper
- `CloudinaryError` exception.
- `is_configured()` → bool (cloud name + key + secret all present).
- internal `_configure()` → `cloudinary.config(...)` from settings (idempotent).
- `upload_image(filepath, public_id, asset_folder)` → `secure_url`; uploads with
  `resource_type='image', unique_filename=False, overwrite=True, invalidate=True`.
- `delete_image(public_id)` → destroy (ignore "not found").

### 2. `stock/services/cloudinary_sync.py`
- Result codes (stable, tests rely on them): `UPLOADED`, `DELETED`,
  `SKIP_NO_BARCODE`, `SKIP_NO_IMAGE`, `ERROR`.
- `_sanitize_brand(brand)` → filesystem/URL-safe segment (trim; strip path
  separators and control chars; blank → '').
- `_brand_folder(product)` → `f"{CLOUDINARY_FOLDER}/{brand}"` or `CLOUDINARY_FOLDER`.
- `_primary_image_path(product)` → abs path of `images.first()` or None.
- `sync_product_primary_image(product, client=None, *, dry_run=False)`:
  - no barcode → `SKIP_NO_BARCODE`.
  - primary image on disk → `upload_image(path, public_id=barcode,
    asset_folder=_brand_folder)` → `UPLOADED`.
  - no image left → `delete_image(barcode)` → `DELETED`.
  - `CloudinaryError` → `ERROR` (caught, returned).

### 3. `stock/models/catalog.py`
- Module-level `def product_image_upload_to(instance, filename)` returning
  `product_images/<sanitized-brand>/<filename>` (blank brand → `product_images/<filename>`).
- `ProductImage.image = models.ImageField(upload_to=product_image_upload_to)`.
- Migration: state-only field change (no schema/data change).

### 4. `stock/signals.py` — extend (Shopify signal untouched)
- `ProductImage` `post_save` (created **or** updated) → `transaction.on_commit`
  → `cloudinary_sync.sync_product_primary_image(product)`.
- `ProductImage` `post_delete` → `transaction.on_commit` → same call (recomputes
  the current primary from remaining images: uploads the new primary, or deletes
  the Cloudinary asset when none remain).
- Both gated by `CLOUDINARY_AUTO_SYNC`; wrapped in `try/except` that only logs —
  a Cloudinary problem must never break saving/deleting the local image.

### 5. `stock/management/commands/sync_cloudinary_images.py`
- Dry-run default; `--apply`, `--brand <text>`, `--barcode <code>`, `--limit <n>`.
- Iterates products that have a barcode and at least one image; calls
  `sync_product_primary_image`; prints a per-product line and a summary
  (uploaded / deleted / skipped / errors). Useful for rebuilds and to push the
  current catalog into brand folders on demand.

### 6. Tests — `stock/tests.py` `CloudinarySyncTests` (fully mocked, no network)
- create → `upload_image` called with `public_id=barcode`,
  `asset_folder=product_images/<brand>`.
- replace/update → upload (overwrite) called.
- delete last image → `delete_image(barcode)` called.
- delete non-last image → remaining primary re-uploaded.
- disabled by default → signal is a no-op (no client calls).
- no barcode → `SKIP_NO_BARCODE`, no client calls.
- `product_image_upload_to` returns `product_images/<brand>/<file>` and falls
  back to `product_images/<file>` for blank brand.
- command dry-run → no client calls.

### 7. Docs
- New `docs/CLOUDINARY_SYNC.md`: credential setup, env vars, command usage,
  behavior, dynamic-folder note, limitations (primary-image-only, new-uploads-only).
- Update `docs/STATUS.md` and `docs/ARCHITECTURE.md`.

## Data flow

```
Upload image in app
  → ProductImage.save()  (local file → media/product_images/<brand>/<filename>)
  → post_save signal → transaction.on_commit
     → if CLOUDINARY_AUTO_SYNC:
         sync_product_primary_image(product)
           → cloudinary_client.upload_image(
                 local primary path,
                 public_id = <barcode>,
                 asset_folder = product_images/<brand>)
  → Cloudinary asset at folder product_images/<brand>,
    URL https://res.cloudinary.com/bulvpmzg/image/upload/<barcode>.<ext>
```

## Error handling

All Cloudinary interaction is wrapped; failures are logged
(`logger.warning` / `logger.exception`) and never propagate into the request.
The whole feature is gated off by default via `CLOUDINARY_AUTO_SYNC`.

## Edge cases

- Blank brand → `product_images` folder; brand sanitized for path safety.
- Blank barcode → skip (cannot key the asset).
- Primary file missing on disk → skip.
- Brand changed later → new uploads land in the new folder; the existing asset is
  not moved (documented limitation).
- Multiple images per product → only the primary is mirrored (by design).
- Delete a non-primary image → primary re-uploaded (idempotent/consistent).

## Out of scope

- Retro-migrating existing local files or existing Cloudinary assets into brand
  folders.
- Automatic Shopify attach on upload (kept separate per the B2 decision).
- Mirroring non-primary images.
