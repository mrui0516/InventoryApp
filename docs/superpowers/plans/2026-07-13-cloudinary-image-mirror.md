# Cloudinary Image Mirror Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically mirror a product's primary image to Cloudinary (create / replace / delete) so Cloudinary is the canonical, brand-organized image host, without changing local storage's primary role and without auto-pushing to Shopify.

**Architecture:** A thin Cloudinary SDK wrapper (`cloudinary_client`) plus a sync service (`cloudinary_sync`) that mirrors `product.images.first()` to `public_id=<barcode>` in `asset_folder=product_images/<brand>`. Django `post_save`/`post_delete` signals on `ProductImage` fire the sync after commit, gated by `CLOUDINARY_AUTO_SYNC`, never raising. New local uploads are also foldered by brand via an `upload_to` callable. A management command does batch/dry-run syncs.

**Tech Stack:** Django 5.2, `cloudinary` official Python SDK, SQLite, Django `TestCase` (run via `manage.py test`).

## Global Constraints

- Cloudinary account is in **dynamic-folders mode**: `public_id` does NOT include the folder; `asset_folder` is separate metadata. Delivery URL = `https://res.cloudinary.com/<cloud>/image/upload/<public_id>.<ext>`.
- Upload params (verbatim): `public_id=<barcode>`, `asset_folder=product_images/<brand>` (fallback `product_images`), `resource_type='image'`, `unique_filename=False`, `overwrite=True`, `invalidate=True`.
- Mirror the product's **primary image only** (`product.images.first()`).
- Feature is **off by default** (`CLOUDINARY_AUTO_SYNC`), runs on `transaction.on_commit`, and is wrapped in `try/except` that only logs — a Cloudinary failure must never break the local save/delete.
- **New uploads only** — no retro-migration of existing files or assets.
- Default cloud name: `bulvpmzg`. Env vars override all settings.
- Tests are fully mocked — no network, no real Cloudinary calls.
- Run tests with system Python: `C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe manage.py test ...` (the `.venv` lacks Django).
- Keep the existing Shopify `ProductImage` signal untouched and independent.

---

### Task 1: Brand-foldered local upload path + model field change

**Files:**
- Modify: `stock/models/catalog.py` (add callable near top of file; change `ProductImage.image` at line ~119)
- Create: `stock/migrations/0027_alter_productimage_image.py` (generated)
- Test: `stock/tests.py` (append `UploadPathTests`)

**Interfaces:**
- Produces: `stock.models.catalog.product_image_upload_to(instance, filename) -> str` returning `product_images/<brand>/<filename>` or `product_images/<filename>` when brand is blank; brand sanitized of `\ / : * ? " < > |`.

- [ ] **Step 1: Write the failing test**

Append to `stock/tests.py`:

```python
from types import SimpleNamespace
from django.test import SimpleTestCase


class UploadPathTests(SimpleTestCase):
    def _obj(self, brand):
        return SimpleNamespace(product=SimpleNamespace(brand=brand))

    def test_brand_subfolder(self):
        from stock.models.catalog import product_image_upload_to
        self.assertEqual(
            product_image_upload_to(self._obj("Lattafa"), "a.jpg"),
            "product_images/Lattafa/a.jpg",
        )

    def test_blank_brand_falls_back(self):
        from stock.models.catalog import product_image_upload_to
        self.assertEqual(
            product_image_upload_to(self._obj(""), "a.jpg"),
            "product_images/a.jpg",
        )

    def test_unsafe_chars_stripped(self):
        from stock.models.catalog import product_image_upload_to
        self.assertEqual(
            product_image_upload_to(self._obj("A/B:C"), "a.jpg"),
            "product_images/ABC/a.jpg",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test stock.tests.UploadPathTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'product_image_upload_to'`.

- [ ] **Step 3: Implement the callable and change the field**

In `stock/models/catalog.py`, add near the top (after existing imports; add `import re` if not already imported):

```python
import re


def product_image_upload_to(instance, filename):
    """Store product photos under product_images/<brand>/<filename>.

    Brand is sanitized for filesystem/URL safety; a blank brand falls back to
    product_images/<filename>.
    """
    brand = (getattr(getattr(instance, "product", None), "brand", "") or "").strip()
    brand = re.sub(r'[\\/:*?"<>|]+', "", brand).strip()
    return f"product_images/{brand}/{filename}" if brand else f"product_images/{filename}"
```

Change the field (was `upload_to='product_images/'`):

```python
    image = models.ImageField(upload_to=product_image_upload_to)
```

- [ ] **Step 4: Generate the migration**

Run: `python manage.py makemigrations stock`
Expected: creates `stock/migrations/0027_alter_productimage_image.py` (state-only field change).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python manage.py test stock.tests.UploadPathTests -v 2`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add stock/models/catalog.py stock/migrations/0027_alter_productimage_image.py stock/tests.py
git commit -m "feat: fold local product images into brand subfolders"
```

---

### Task 2: Cloudinary settings + SDK dependency + client wrapper

**Files:**
- Modify: `requirements.txt` (add `cloudinary`)
- Modify: `inventory_system/settings.py` (append Cloudinary block near the Shopify block)
- Create: `stock/services/cloudinary_client.py`
- Test: `stock/tests.py` (append `CloudinaryClientTests`)

**Interfaces:**
- Consumes: settings `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`, `CLOUDINARY_FOLDER`, `CLOUDINARY_AUTO_SYNC`.
- Produces:
  - `stock.services.cloudinary_client.CloudinaryError` (Exception)
  - `CloudinaryClient()` with:
    - `is_configured() -> bool`
    - `upload_image(filepath: str, public_id: str, asset_folder: str) -> str` (returns secure_url; raises `CloudinaryError` on failure)
    - `delete_image(public_id: str) -> None` (raises `CloudinaryError` on failure)

- [ ] **Step 1: Install the SDK and add it to requirements**

Run: `C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe -m pip install cloudinary`
Then append to `requirements.txt`:

```
cloudinary==1.41.0
```

(If pip resolves a newer version, pin that exact version instead.)

- [ ] **Step 2: Add the settings block**

In `inventory_system/settings.py`, directly after the existing `SHOPIFY_*` block, add:

```python
# --- Cloudinary (product image mirror) ---
CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME', 'bulvpmzg')
CLOUDINARY_API_KEY = os.environ.get('CLOUDINARY_API_KEY', '')
CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET', '')
CLOUDINARY_FOLDER = os.environ.get('CLOUDINARY_FOLDER', 'product_images')
CLOUDINARY_AUTO_SYNC = os.environ.get('CLOUDINARY_AUTO_SYNC', '') in {'1', 'true', 'True', 'yes', 'on'}
```

- [ ] **Step 3: Write the failing test**

Append to `stock/tests.py`:

```python
from unittest import mock
from django.test import override_settings


class CloudinaryClientTests(SimpleTestCase):
    @override_settings(CLOUDINARY_CLOUD_NAME="c", CLOUDINARY_API_KEY="k", CLOUDINARY_API_SECRET="s")
    def test_is_configured_true_when_all_present(self):
        from stock.services.cloudinary_client import CloudinaryClient
        self.assertTrue(CloudinaryClient().is_configured())

    @override_settings(CLOUDINARY_CLOUD_NAME="c", CLOUDINARY_API_KEY="k", CLOUDINARY_API_SECRET="")
    def test_is_configured_false_when_secret_missing(self):
        from stock.services.cloudinary_client import CloudinaryClient
        self.assertFalse(CloudinaryClient().is_configured())

    @override_settings(CLOUDINARY_CLOUD_NAME="c", CLOUDINARY_API_KEY="k", CLOUDINARY_API_SECRET="s")
    def test_upload_image_passes_expected_params(self):
        from stock.services.cloudinary_client import CloudinaryClient
        with mock.patch("cloudinary.uploader.upload", return_value={"secure_url": "https://x/y.jpg"}) as up, \
             mock.patch("cloudinary.config"):
            url = CloudinaryClient().upload_image("/tmp/a.jpg", public_id="123", asset_folder="product_images/Lattafa")
        self.assertEqual(url, "https://x/y.jpg")
        _, kwargs = up.call_args
        self.assertEqual(kwargs["public_id"], "123")
        self.assertEqual(kwargs["asset_folder"], "product_images/Lattafa")
        self.assertFalse(kwargs["unique_filename"])
        self.assertTrue(kwargs["overwrite"])

    @override_settings(CLOUDINARY_CLOUD_NAME="c", CLOUDINARY_API_KEY="k", CLOUDINARY_API_SECRET="s")
    def test_upload_error_wrapped(self):
        from stock.services.cloudinary_client import CloudinaryClient, CloudinaryError
        with mock.patch("cloudinary.uploader.upload", side_effect=RuntimeError("boom")), \
             mock.patch("cloudinary.config"):
            with self.assertRaises(CloudinaryError):
                CloudinaryClient().upload_image("/tmp/a.jpg", public_id="123", asset_folder="product_images")
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python manage.py test stock.tests.CloudinaryClientTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock.services.cloudinary_client'`.

- [ ] **Step 5: Implement the client**

Create `stock/services/cloudinary_client.py`:

```python
"""Thin wrapper over the Cloudinary SDK for mirroring product images.

Account is in dynamic-folders mode: ``public_id`` is the full identifier (no
folder prefix); ``asset_folder`` only organizes the Media Library. The delivery
URL is ``.../image/upload/<public_id>.<ext>``.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class CloudinaryError(Exception):
    """Any failure talking to Cloudinary."""


class CloudinaryClient:
    def is_configured(self):
        return bool(
            getattr(settings, 'CLOUDINARY_CLOUD_NAME', '')
            and getattr(settings, 'CLOUDINARY_API_KEY', '')
            and getattr(settings, 'CLOUDINARY_API_SECRET', '')
        )

    def _configure(self):
        import cloudinary
        cloudinary.config(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            secure=True,
        )

    def upload_image(self, filepath, public_id, asset_folder):
        import cloudinary.uploader
        self._configure()
        try:
            result = cloudinary.uploader.upload(
                filepath,
                public_id=public_id,
                asset_folder=asset_folder,
                resource_type='image',
                unique_filename=False,
                overwrite=True,
                invalidate=True,
            )
        except Exception as exc:  # SDK raises various types
            raise CloudinaryError(f'upload failed for {public_id}: {exc}') from exc
        return result.get('secure_url')

    def delete_image(self, public_id):
        import cloudinary.uploader
        self._configure()
        try:
            cloudinary.uploader.destroy(public_id, resource_type='image', invalidate=True)
        except Exception as exc:
            raise CloudinaryError(f'delete failed for {public_id}: {exc}') from exc
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python manage.py test stock.tests.CloudinaryClientTests -v 2`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add requirements.txt inventory_system/settings.py stock/services/cloudinary_client.py stock/tests.py
git commit -m "feat: add Cloudinary client wrapper and settings"
```

---

### Task 3: Cloudinary sync service

**Files:**
- Create: `stock/services/cloudinary_sync.py`
- Test: `stock/tests.py` (append `CloudinarySyncTests`)

**Interfaces:**
- Consumes: `CloudinaryClient` (Task 2), settings `CLOUDINARY_FOLDER`.
- Produces:
  - Result codes: `UPLOADED='uploaded'`, `DELETED='deleted'`, `SKIP_NO_BARCODE='no_barcode'`, `SKIP_NO_IMAGE='no_image_file'`, `ERROR='error'` (note: `SKIP_NO_IMAGE` is reserved for future use; delete path uses `DELETED`).
  - `sync_product_primary_image(product, client=None, *, dry_run=False) -> (code, detail)`.

- [ ] **Step 1: Write the failing test**

Append to `stock/tests.py` (uses helpers to build a product with/without an image):

```python
import tempfile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase


def _make_product(barcode="111", brand="Lattafa"):
    from stock.models import Product
    return Product.objects.create(name="X", brand=brand, barcode=barcode)


def _tiny_png():
    # 1x1 transparent PNG
    import base64
    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    return SimpleUploadedFile("p.png", data, content_type="image/png")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CloudinarySyncTests(TestCase):
    def test_upload_when_primary_exists(self):
        from stock.models import ProductImage
        from stock.services import cloudinary_sync
        p = _make_product(barcode="222", brand="Lattafa")
        ProductImage.objects.create(product=p, image=_tiny_png())
        client = mock.Mock()
        code, _ = cloudinary_sync.sync_product_primary_image(p, client=client)
        self.assertEqual(code, cloudinary_sync.UPLOADED)
        _, kwargs = client.upload_image.call_args
        self.assertEqual(kwargs["public_id"], "222")
        self.assertEqual(kwargs["asset_folder"], "product_images/Lattafa")

    def test_delete_when_no_image(self):
        from stock.services import cloudinary_sync
        p = _make_product(barcode="333")
        client = mock.Mock()
        code, _ = cloudinary_sync.sync_product_primary_image(p, client=client)
        self.assertEqual(code, cloudinary_sync.DELETED)
        client.delete_image.assert_called_once_with("333")

    def test_skip_no_barcode(self):
        from stock.services import cloudinary_sync
        p = _make_product(barcode="")
        client = mock.Mock()
        code, _ = cloudinary_sync.sync_product_primary_image(p, client=client)
        self.assertEqual(code, cloudinary_sync.SKIP_NO_BARCODE)
        client.upload_image.assert_not_called()
        client.delete_image.assert_not_called()

    def test_error_wrapped(self):
        from stock.models import ProductImage
        from stock.services import cloudinary_sync
        from stock.services.cloudinary_client import CloudinaryError
        p = _make_product(barcode="444")
        ProductImage.objects.create(product=p, image=_tiny_png())
        client = mock.Mock()
        client.upload_image.side_effect = CloudinaryError("nope")
        code, _ = cloudinary_sync.sync_product_primary_image(p, client=client)
        self.assertEqual(code, cloudinary_sync.ERROR)

    def test_dry_run_makes_no_calls(self):
        from stock.models import ProductImage
        from stock.services import cloudinary_sync
        p = _make_product(barcode="555")
        ProductImage.objects.create(product=p, image=_tiny_png())
        client = mock.Mock()
        code, _ = cloudinary_sync.sync_product_primary_image(p, client=client, dry_run=True)
        self.assertEqual(code, cloudinary_sync.UPLOADED)
        client.upload_image.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test stock.tests.CloudinarySyncTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock.services.cloudinary_sync'`.

- [ ] **Step 3: Implement the sync service**

Create `stock/services/cloudinary_sync.py`:

```python
"""Mirror a product's primary image to Cloudinary.

One Cloudinary asset per product: public_id == Product.barcode, placed in
asset_folder product_images/<brand>. The asset always reflects the product's
current primary image (product.images.first()); when the product has no image
left, the asset is deleted.
"""
import logging
import os
import re

from django.conf import settings

from .cloudinary_client import CloudinaryClient, CloudinaryError

logger = logging.getLogger(__name__)

UPLOADED = 'uploaded'
DELETED = 'deleted'
SKIP_NO_BARCODE = 'no_barcode'
SKIP_NO_IMAGE = 'no_image_file'
ERROR = 'error'


def _sanitize_brand(brand):
    brand = (brand or '').strip()
    return re.sub(r'[\\/:*?"<>|]+', '', brand).strip()


def _brand_folder(product):
    base = getattr(settings, 'CLOUDINARY_FOLDER', 'product_images')
    brand = _sanitize_brand(getattr(product, 'brand', ''))
    return f'{base}/{brand}' if brand else base


def _primary_image_path(product):
    image = product.images.first()
    if not image or not getattr(image, 'image', None):
        return None
    try:
        path = image.image.path
    except (ValueError, NotImplementedError):
        return None
    return path if (path and os.path.exists(path)) else None


def sync_product_primary_image(product, client=None, *, dry_run=False):
    """Make the Cloudinary asset match the product's current primary image."""
    client = client or CloudinaryClient()
    barcode = (product.barcode or '').strip()
    if not barcode:
        return SKIP_NO_BARCODE, 'product has no barcode'

    path = _primary_image_path(product)
    try:
        if path:
            if not dry_run:
                client.upload_image(path, public_id=barcode, asset_folder=_brand_folder(product))
            return UPLOADED, barcode
        if not dry_run:
            client.delete_image(barcode)
        return DELETED, barcode
    except CloudinaryError as exc:
        return ERROR, str(exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test stock.tests.CloudinarySyncTests -v 2`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add stock/services/cloudinary_sync.py stock/tests.py
git commit -m "feat: add Cloudinary sync service for product primary image"
```

---

### Task 4: Signal wiring (create / replace / delete, gated, after-commit)

**Files:**
- Modify: `stock/signals.py` (add two receivers; add `post_delete` import already present)
- Test: `stock/tests.py` (append `CloudinarySignalTests`)

**Interfaces:**
- Consumes: `cloudinary_sync.sync_product_primary_image` (Task 3), setting `CLOUDINARY_AUTO_SYNC`.
- Produces: side-effect only — on `ProductImage` save/delete, calls `sync_product_primary_image(product)` inside `transaction.on_commit` when enabled.

- [ ] **Step 1: Write the failing test**

Append to `stock/tests.py`:

```python
@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CloudinarySignalTests(TestCase):
    @override_settings(CLOUDINARY_AUTO_SYNC=True)
    def test_save_triggers_sync_when_enabled(self):
        from stock.models import ProductImage
        p = _make_product(barcode="666")
        with mock.patch("stock.services.cloudinary_sync.sync_product_primary_image") as sync:
            with self.captureOnCommitCallbacks(execute=True):
                ProductImage.objects.create(product=p, image=_tiny_png())
        sync.assert_called()

    @override_settings(CLOUDINARY_AUTO_SYNC=True)
    def test_delete_triggers_sync_when_enabled(self):
        from stock.models import ProductImage
        p = _make_product(barcode="777")
        img = ProductImage.objects.create(product=p, image=_tiny_png())
        with mock.patch("stock.services.cloudinary_sync.sync_product_primary_image") as sync:
            with self.captureOnCommitCallbacks(execute=True):
                img.delete()
        sync.assert_called()

    def test_disabled_by_default_no_sync(self):
        from stock.models import ProductImage
        p = _make_product(barcode="888")
        with mock.patch("stock.services.cloudinary_sync.sync_product_primary_image") as sync:
            with self.captureOnCommitCallbacks(execute=True):
                ProductImage.objects.create(product=p, image=_tiny_png())
        sync.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test stock.tests.CloudinarySignalTests -v 2`
Expected: FAIL — `test_save_triggers_sync_when_enabled` and `test_delete...` fail (sync not called), `test_disabled...` passes.

- [ ] **Step 3: Implement the receivers**

In `stock/signals.py`, append (imports `post_save`, `post_delete`, `transaction`, `settings`, `logger`, `ProductImage` are already present from the existing Shopify receiver):

```python
def _mirror_to_cloudinary(product):
    if not product:
        return
    if not getattr(settings, 'CLOUDINARY_AUTO_SYNC', False):
        return

    def _sync():
        try:
            from .services import cloudinary_sync
            code, detail = cloudinary_sync.sync_product_primary_image(product)
            logger.info('Cloudinary mirror %s for %s (%s)', code, getattr(product, 'barcode', '?'), detail)
        except Exception:  # never let a mirror problem break the local save/delete
            logger.exception('Cloudinary mirror crashed for %s', getattr(product, 'barcode', '?'))

    transaction.on_commit(_sync)


@receiver(post_save, sender=ProductImage)
def mirror_product_image_on_save(sender, instance, **kwargs):
    """Mirror the product's primary image to Cloudinary on create or replace."""
    _mirror_to_cloudinary(instance.product)


@receiver(post_delete, sender=ProductImage)
def mirror_product_image_on_delete(sender, instance, **kwargs):
    """Re-sync Cloudinary after an image is removed (upload new primary, or
    delete the asset when none remain)."""
    _mirror_to_cloudinary(instance.product)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test stock.tests.CloudinarySignalTests -v 2`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full stock test module (regression)**

Run: `python manage.py test stock.tests -v 1`
Expected: PASS (existing Shopify/other tests unaffected).

- [ ] **Step 6: Commit**

```bash
git add stock/signals.py stock/tests.py
git commit -m "feat: mirror product images to Cloudinary on save/delete"
```

---

### Task 5: Batch management command

**Files:**
- Create: `stock/management/commands/sync_cloudinary_images.py`
- Test: `stock/tests.py` (append `CloudinaryCommandTests`)

**Interfaces:**
- Consumes: `cloudinary_sync.sync_product_primary_image`, `CloudinaryClient` (for `is_configured` gate).
- Produces: `manage.py sync_cloudinary_images [--apply] [--brand X] [--barcode C] [--limit N]`.

- [ ] **Step 1: Write the failing test**

Append to `stock/tests.py`:

```python
from django.core.management import call_command
from io import StringIO


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CloudinaryCommandTests(TestCase):
    def test_dry_run_makes_no_client_calls(self):
        from stock.models import ProductImage
        p = _make_product(barcode="999")
        ProductImage.objects.create(product=p, image=_tiny_png())
        with mock.patch("stock.services.cloudinary_sync.sync_product_primary_image",
                        return_value=("uploaded", "999")) as sync:
            out = StringIO()
            call_command("sync_cloudinary_images", stdout=out)
        # dry-run passes dry_run=True through
        _, kwargs = sync.call_args
        self.assertTrue(kwargs.get("dry_run"))
        self.assertIn("999", out.getvalue())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test stock.tests.CloudinaryCommandTests -v 2`
Expected: FAIL — `CommandError: Unknown command: 'sync_cloudinary_images'`.

- [ ] **Step 3: Implement the command**

Create `stock/management/commands/sync_cloudinary_images.py`:

```python
"""Mirror product primary images to Cloudinary (dry-run by default)."""
from django.core.management.base import BaseCommand

from stock.models import Product
from stock.services import cloudinary_sync
from stock.services.cloudinary_client import CloudinaryClient


class Command(BaseCommand):
    help = "Mirror each product's primary image to Cloudinary (public_id=barcode)."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually upload (default: dry run).')
        parser.add_argument('--brand', default=None, help='Only products whose brand contains this text.')
        parser.add_argument('--barcode', default=None, help='Only the product with this exact barcode.')
        parser.add_argument('--limit', type=int, default=None, help='Process at most N products.')

    def handle(self, *args, **options):
        dry_run = not options['apply']
        qs = Product.objects.exclude(barcode='').filter(images__isnull=False).distinct()
        if options['brand']:
            qs = qs.filter(brand__icontains=options['brand'])
        if options['barcode']:
            qs = qs.filter(barcode=options['barcode'])
        if options['limit']:
            qs = qs[:options['limit']]

        client = CloudinaryClient()
        if not dry_run and not client.is_configured():
            self.stderr.write('Cloudinary is not configured (set CLOUDINARY_API_KEY / _SECRET).')
            return

        counts = {}
        for product in qs:
            code, detail = cloudinary_sync.sync_product_primary_image(
                product, client=client, dry_run=dry_run
            )
            counts[code] = counts.get(code, 0) + 1
            self.stdout.write(f'{product.barcode}: {code} {detail}')

        mode = 'DRY-RUN' if dry_run else 'APPLIED'
        self.stdout.write(f'Summary ({mode}): {counts}')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test stock.tests.CloudinaryCommandTests -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add stock/management/commands/sync_cloudinary_images.py stock/tests.py
git commit -m "feat: add sync_cloudinary_images management command"
```

---

### Task 6: Documentation

**Files:**
- Create: `docs/CLOUDINARY_SYNC.md`
- Modify: `docs/STATUS.md`, `docs/ARCHITECTURE.md`

- [ ] **Step 1: Write `docs/CLOUDINARY_SYNC.md`**

Create `docs/CLOUDINARY_SYNC.md`:

```markdown
# Cloudinary image mirror

Mirror each product's **primary image** to Cloudinary (cloud `bulvpmzg`), which
is the canonical image host. Local `media/` storage stays primary. Matching key
for later Shopify use is **barcode**.

## What it does

- On product-image **create / replace / delete** in the app, the product's
  current primary image (`product.images.first()`) is mirrored to Cloudinary:
  `public_id = <barcode>`, `asset_folder = product_images/<brand>`
  (`overwrite=true`). When the last image is removed, the asset is deleted.
- Account is in **dynamic-folders** mode, so the folder is organizational only;
  the delivery URL is `https://res.cloudinary.com/bulvpmzg/image/upload/<barcode>.<ext>`.
- New local uploads are stored under `media/product_images/<brand>/`.

## Setup

1. Cloudinary dashboard → **Settings → API Keys**: copy **API Key** + **API Secret**.
2. Set env vars (never commit the secret):

| Variable | Value |
|---|---|
| `CLOUDINARY_CLOUD_NAME` | `bulvpmzg` (default) |
| `CLOUDINARY_API_KEY` | from dashboard |
| `CLOUDINARY_API_SECRET` | from dashboard |
| `CLOUDINARY_FOLDER` | `product_images` (default) |
| `CLOUDINARY_AUTO_SYNC` | `1` to enable auto-mirror (default off) |

PowerShell (current session):
```powershell
$env:CLOUDINARY_API_KEY = "..."
$env:CLOUDINARY_API_SECRET = "..."
$env:CLOUDINARY_AUTO_SYNC = "1"
```

## Batch command

```powershell
python manage.py sync_cloudinary_images --brand Lattafa            # preview
python manage.py sync_cloudinary_images --brand Lattafa --apply    # mirror
```
Flags: `--apply`, `--brand <text>`, `--barcode <code>`, `--limit <n>`.

## Behaviour & limitations

- **Primary image only** — one Cloudinary asset per product (`public_id=barcode`).
- **New uploads only** — existing local files and Cloudinary assets are not
  retro-foldered by this feature.
- **No Shopify push** — Cloudinary → Shopify stays a separate step (match by barcode).
- Failures are logged and never break saving/deleting the local image.
```

- [ ] **Step 2: Update STATUS and ARCHITECTURE**

In `docs/STATUS.md`, add a bullet under the current work/features section:

```markdown
- Cloudinary image mirror: product primary image auto-synced to Cloudinary
  (brand-foldered, public_id=barcode) on create/replace/delete; opt-in via
  `CLOUDINARY_AUTO_SYNC`. See docs/CLOUDINARY_SYNC.md.
```

In `docs/ARCHITECTURE.md`, add under the services/integrations section:

```markdown
- `stock/services/cloudinary_client.py` + `cloudinary_sync.py`: mirror a
  product's primary image to Cloudinary (dynamic-folders mode, asset_folder
  `product_images/<brand>`, public_id `<barcode>`). Driven by `ProductImage`
  post_save/post_delete signals (gated by `CLOUDINARY_AUTO_SYNC`) and the
  `sync_cloudinary_images` command.
```

- [ ] **Step 3: Commit**

```bash
git add docs/CLOUDINARY_SYNC.md docs/STATUS.md docs/ARCHITECTURE.md
git commit -m "docs: document Cloudinary image mirror"
```

---

## Self-Review

**Spec coverage:**
- Mirror model / local primary → Tasks 1, 3, 4. ✓
- Cloudinary-only (no Shopify) → no Shopify calls anywhere. ✓
- Triggers create/replace/delete → Task 4 (post_save + post_delete). ✓
- Naming public_id=barcode, asset_folder=product_images/<brand>, dynamic mode, overwrite → Tasks 2 (client params) + 3 (sync). ✓
- Primary image only → Task 3 `_primary_image_path`. ✓
- Local brand foldering + migration → Task 1. ✓
- New uploads only (no retro-migration) → nothing migrates existing data. ✓
- Config block + env → Task 2. ✓
- `cloudinary` dependency → Task 2. ✓
- Batch command dry-run default → Task 5. ✓
- Mocked tests → Tasks 1–5. ✓
- Docs (CLOUDINARY_SYNC/STATUS/ARCHITECTURE) → Task 6. ✓

**Placeholder scan:** none — all steps carry real code/commands.

**Type consistency:** `sync_product_primary_image(product, client=None, *, dry_run=False)` and client methods `upload_image(filepath, public_id, asset_folder)` / `delete_image(public_id)` / `is_configured()` are used identically across Tasks 2–5. Result codes (`UPLOADED`/`DELETED`/`SKIP_NO_BARCODE`/`ERROR`) are consistent.
