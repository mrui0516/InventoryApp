# Cloudinary Image URLs in the Shopify CSV Export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Shopify product CSV export emit public Cloudinary image URLs (built from the product barcode) instead of unreachable LAN media URLs.

**Architecture:** A new pure-string service, `stock/services/cloudinary_urls.py`, reads the naming contract that `stock/services/cloudinary_sync.py` already writes (one Cloudinary asset per product, `public_id == Product.barcode`) and turns it into a delivery URL. The only production caller is `_build_shopify_image_url` in `stock/views.py`, which both Shopify CSV image columns already flow through. No network calls, no credentials, no new model fields, no migration.

**Tech Stack:** Django 5.2.4, SQLite, `django.test.TestCase` + `override_settings`, `csv.DictReader`.

Spec: `docs/superpowers/specs/2026-07-15-shopify-csv-cloudinary-images-design.md`

## Global Constraints

- Transformation string is exactly `c_pad,b_white,w_1600,h_1600,q_auto` — a 1:1 white-padded square, matching the normalization already applied to the live Shopify images.
- URL shape is exactly `https://res.cloudinary.com/<cloud>/image/upload/<transformation>/<barcode>.jpg`. The `.jpg` extension is mandatory: the stored originals are WebP and Shopify must receive JPEG.
- The export performs **no** network calls and reads **no** API credentials. `product_image_cdn_url` is pure string building.
- Scope is the Shopify CSV export only. Do not touch `export_product_list_excel`, `export_catalog_excel`, or any template.
- Do not add a `Product` field or a migration.
- **Tests must not depend on the developer's `.env`.** `inventory_system/settings.py` loads `F:\APP\InventoryApp\.env`, which sets `CLOUDINARY_AUTO_SYNC=1` and real credentials on this machine. Every new test that asserts on a Cloudinary URL MUST pin the cloud name with `@override_settings(CLOUDINARY_CLOUD_NAME="testcloud")`, and every new test that creates a `ProductImage` MUST also set `CLOUDINARY_AUTO_SYNC=False` in that same decorator. (`stock/signals.py` mirrors images via `transaction.on_commit`, which `TestCase` never fires because it rolls back — but pin the setting anyway so the intent is explicit and a future `TransactionTestCase` cannot start uploading from a test run.)
- Run tests with the system Python, not the portable one:
  `C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe manage.py test stock`
- Existing test count is 136 and must not regress.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `stock/services/cloudinary_urls.py` (create) | Read half of the Cloudinary naming contract: product → public delivery URL. Pure functions. |
| `stock/views.py` (modify, `_build_shopify_image_url` at lines 329-333) | Prefer the Cloudinary URL; fall back to today's absolute media URL. |
| `stock/tests.py` (modify, append) | `CloudinaryImageUrlTests` (unit) and `ShopifyCsvCloudinaryImageTests` (export). |
| `docs/STATUS.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md` (modify) | Record the feature. |

---

### Task 1: Cloudinary delivery-URL service

**Files:**
- Create: `stock/services/cloudinary_urls.py`
- Test: `stock/tests.py` (append a new class at end of file)

**Interfaces:**
- Consumes: `django.conf.settings.CLOUDINARY_CLOUD_NAME`; `Product.barcode`; `Product.images` (related manager).
- Produces:
  - `SHOPIFY_IMAGE_TRANSFORMATION: str = 'c_pad,b_white,w_1600,h_1600,q_auto'`
  - `product_image_cdn_url(product, transformation=SHOPIFY_IMAGE_TRANSFORMATION) -> str` — returns the delivery URL, or `''` when the product is falsy / has no barcode / has no image / no cloud name is configured. Task 2 calls this.

- [ ] **Step 1: Write the failing tests**

Append to the end of `stock/tests.py`. Everything the code below needs is already in scope at module level — **add no imports**:
- `_make_product` (line 2529) and `_tiny_png` (line 2534) are existing module-level helpers. Reuse them; do not redefine them.
- `override_settings` is imported at **line 2493**, not at the top of the file. It is still in scope for classes appended at the end. Do not re-import it.
- `TestCase` (line 11), `csv` (line 1), `StringIO` (line 4), `Decimal` (line 6), `reverse` (line 12), `get_user_model` (line 8), and the models incl. `Brand`/`Category`/`ProductSeries` (line 16) are imported at the top.

```python
@override_settings(CLOUDINARY_CLOUD_NAME="testcloud", CLOUDINARY_AUTO_SYNC=False)
class CloudinaryImageUrlTests(TestCase):
    def test_url_for_product_with_barcode_and_image(self):
        from stock.models import ProductImage
        from stock.services.cloudinary_urls import product_image_cdn_url
        p = _make_product(barcode="6290362349730")
        ProductImage.objects.create(product=p, image=_tiny_png())
        self.assertEqual(
            product_image_cdn_url(p),
            "https://res.cloudinary.com/testcloud/image/upload/"
            "c_pad,b_white,w_1600,h_1600,q_auto/6290362349730.jpg",
        )

    def test_blank_without_image(self):
        from stock.services.cloudinary_urls import product_image_cdn_url
        p = _make_product(barcode="6290362349730")
        self.assertEqual(product_image_cdn_url(p), "")

    def test_blank_without_barcode(self):
        from stock.models import ProductImage
        from stock.services.cloudinary_urls import product_image_cdn_url
        p = _make_product(barcode="")
        ProductImage.objects.create(product=p, image=_tiny_png())
        self.assertEqual(product_image_cdn_url(p), "")

    def test_blank_for_falsy_product(self):
        from stock.services.cloudinary_urls import product_image_cdn_url
        self.assertEqual(product_image_cdn_url(None), "")

    @override_settings(CLOUDINARY_CLOUD_NAME="")
    def test_blank_when_cloud_name_missing(self):
        from stock.models import ProductImage
        from stock.services.cloudinary_urls import product_image_cdn_url
        p = _make_product(barcode="6290362349730")
        ProductImage.objects.create(product=p, image=_tiny_png())
        self.assertEqual(product_image_cdn_url(p), "")

    def test_custom_transformation_is_used(self):
        from stock.models import ProductImage
        from stock.services.cloudinary_urls import product_image_cdn_url
        p = _make_product(barcode="6290362349730")
        ProductImage.objects.create(product=p, image=_tiny_png())
        self.assertEqual(
            product_image_cdn_url(p, transformation="w_400"),
            "https://res.cloudinary.com/testcloud/image/upload/w_400/6290362349730.jpg",
        )

    def test_barcode_is_stripped(self):
        # cloudinary_sync strips the barcode when choosing public_id; the URL
        # must use the same key or it would point at a non-existent asset.
        # Barcode is max_length=13, so keep the padded value within that.
        from stock.models import ProductImage
        from stock.services.cloudinary_urls import product_image_cdn_url
        p = _make_product(barcode="  62903623  ")
        ProductImage.objects.create(product=p, image=_tiny_png())
        self.assertIn("/62903623.jpg", product_image_cdn_url(p))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe manage.py test stock.tests.CloudinaryImageUrlTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock.services.cloudinary_urls'`

- [ ] **Step 3: Write the implementation**

Create `stock/services/cloudinary_urls.py`:

```python
"""Build public Cloudinary delivery URLs for mirrored product images.

The read half of the naming contract that ``cloudinary_sync`` writes: one asset
per product, ``public_id == Product.barcode``. Pure string building — this
module never calls Cloudinary and never reads API credentials.
"""
from django.conf import settings

# 1:1 white-padded square JPEG. Matches the normalization already applied to the
# live Shopify images, so CSV-imported products display at a uniform size. The
# .jpg extension is required: the stored originals are WebP.
SHOPIFY_IMAGE_TRANSFORMATION = 'c_pad,b_white,w_1600,h_1600,q_auto'


def _has_primary_image(product):
    images = getattr(product, 'images', None)
    if images is None:
        return False
    return images.first() is not None


def product_image_cdn_url(product, transformation=SHOPIFY_IMAGE_TRANSFORMATION):
    """Public URL for the product's mirrored image, or '' if there isn't one."""
    cloud_name = getattr(settings, 'CLOUDINARY_CLOUD_NAME', '')
    if not product or not cloud_name:
        return ''
    barcode = (getattr(product, 'barcode', '') or '').strip()
    if not barcode or not _has_primary_image(product):
        return ''
    return (
        f'https://res.cloudinary.com/{cloud_name}/image/upload/'
        f'{transformation}/{barcode}.jpg'
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe manage.py test stock.tests.CloudinaryImageUrlTests -v 2`
Expected: PASS — 7 tests, OK

- [ ] **Step 5: Commit**

```bash
git add stock/services/cloudinary_urls.py stock/tests.py
git commit -m "feat: build Cloudinary delivery URLs from a product barcode"
```

---

### Task 2: Use the Cloudinary URL in the Shopify CSV export

**Files:**
- Modify: `stock/views.py:329-333` (`_build_shopify_image_url`)
- Test: `stock/tests.py` (append a new class at end of file)

**Interfaces:**
- Consumes: `product_image_cdn_url` from Task 1.
- Produces: nothing for later tasks. `_first_shopify_image_url` (`stock/views.py:336-341`) already delegates to `_build_shopify_image_url`, so the `Product image URL` column is covered without editing it.

- [ ] **Step 1: Write the failing tests**

Append to the end of `stock/tests.py`. `Brand`, `Category`, `Product`, `ProductImage`, `ProductSeries`, `get_user_model`, `reverse`, `csv`, `StringIO`, `Decimal`, and `override_settings` are already imported at the top of this file.

```python
@override_settings(CLOUDINARY_CLOUD_NAME="testcloud", CLOUDINARY_AUTO_SYNC=False)
class ShopifyCsvCloudinaryImageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_superuser(username="csv_admin", password="pw123456")
        cls.category = Category.objects.create(name="Perfumes")
        cls.brand = Brand.objects.create(name="Lattafa")
        cls.brand.categories.add(cls.category)
        cls.series = ProductSeries.objects.create(brand=cls.brand, name="Asad")

    def _make_csv_product(self, barcode, spec):
        from stock.models import Product
        return Product.objects.create(
            name="EDP",
            barcode=barcode,
            brand="Lattafa",
            brand_master=self.brand,
            series_master=self.series,
            model="Asad",
            category=self.category,
            spec=spec,
            default_price=Decimal("39.90"),
        )

    def _export_rows(self):
        self.client.login(username="csv_admin", password="pw123456")
        response = self.client.get(reverse("export_shopify_inventory_csv"))
        self.assertEqual(response.status_code, 200)
        return list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))

    def test_export_uses_cloudinary_url_for_both_image_columns(self):
        from stock.models import ProductImage
        product = self._make_csv_product("1234509876511", "100ml")
        ProductImage.objects.create(product=product, image=_tiny_png())

        rows = self._export_rows()

        expected = (
            "https://res.cloudinary.com/testcloud/image/upload/"
            "c_pad,b_white,w_1600,h_1600,q_auto/1234509876511.jpg"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Product image URL"], expected)
        self.assertEqual(rows[0]["Variant image URL"], expected)
        self.assertNotIn("/media/", rows[0]["Product image URL"])

    def test_export_leaves_image_columns_blank_without_image(self):
        self._make_csv_product("1234509876512", "50ml")

        rows = self._export_rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Product image URL"], "")
        self.assertEqual(rows[0]["Variant image URL"], "")

    @override_settings(CLOUDINARY_CLOUD_NAME="")
    def test_export_falls_back_to_absolute_media_url_without_cloud_name(self):
        from stock.models import ProductImage
        product = self._make_csv_product("1234509876513", "30ml")
        ProductImage.objects.create(product=product, image=_tiny_png())

        rows = self._export_rows()

        self.assertEqual(len(rows), 1)
        self.assertIn("/media/", rows[0]["Product image URL"])
        self.assertTrue(rows[0]["Product image URL"].startswith("http://"))
        self.assertNotIn("res.cloudinary.com", rows[0]["Product image URL"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe manage.py test stock.tests.ShopifyCsvCloudinaryImageTests -v 2`
Expected: `test_export_uses_cloudinary_url_for_both_image_columns` FAILS — the columns still hold `http://testserver/media/...` instead of the `res.cloudinary.com` URL. The other two tests pass already (they assert today's behavior, which must not change).

- [ ] **Step 3: Write the implementation**

In `stock/views.py`, add the import to the `.services` import block near the top. That block is alphabetical (`dashboard`, `inventory`, `order_corrections`, `profit`, `stock_ops`), so this line goes **first in the block — immediately before `from .services.dashboard import (` at line 69**:

```python
from .services.cloudinary_urls import product_image_cdn_url
```

Then replace `_build_shopify_image_url` (currently lines 329-333):

```python
def _build_shopify_image_url(request, product):
    image_url = get_product_image_url(product)
    if not image_url:
        return ''
    # Shopify fetches this URL from its own servers, so the local absolute URL
    # (a LAN address) is only a fallback for when Cloudinary is not configured.
    return product_image_cdn_url(product) or request.build_absolute_uri(image_url)
```

Do not change `_first_shopify_image_url`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe manage.py test stock.tests.ShopifyCsvCloudinaryImageTests -v 2`
Expected: PASS — 3 tests, OK

- [ ] **Step 5: Run the whole suite for regressions**

Run: `C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe manage.py test stock`
Expected: OK, 146 tests (136 existing + 7 from Task 1 + 3 from Task 2), 0 failures.

If `test_shopify_export_matches_product_template_and_uses_current_stock` (`stock/tests.py:1184`) fails, read its assertions before changing anything: its products have no `ProductImage`, so both image columns should still be `''` and it must pass untouched. A failure there means the fallback logic is wrong, not that the test needs updating.

- [ ] **Step 6: Commit**

```bash
git add stock/views.py stock/tests.py
git commit -m "feat: emit Cloudinary image URLs in the Shopify CSV export"
```

---

### Task 3: Documentation

**Files:**
- Modify: `docs/STATUS.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md`

**Interfaces:**
- Consumes: the finished behavior from Tasks 1-2.
- Produces: nothing.

- [ ] **Step 1: Read the three docs to match their existing structure**

Read `docs/PRD.md` and find the highest existing `F2.9.x` feature id (the affects-inventory toggle is `F2.9.6`). Read `docs/ARCHITECTURE.md` and find the section that lists `stock/services/` modules. Read `docs/STATUS.md` and find the list of recently completed work.

- [ ] **Step 2: Update `docs/PRD.md`**

Add a feature entry using the next free id after `F2.9.6`, following the wording and formatting of its neighbors. Content to convey:

> Shopify CSV export writes public Cloudinary image URLs (`c_pad,b_white,w_1600,h_1600,q_auto/<barcode>.jpg`) for the `Product image URL` and `Variant image URL` columns, so Shopify can fetch the images on import and every product lands as a uniform 1:1 white-background image. Products without an image leave both columns blank. If Cloudinary is not configured, the export falls back to the previous local absolute URL.

- [ ] **Step 3: Update `docs/ARCHITECTURE.md`**

In the `stock/services/` module list, add a line for `cloudinary_urls.py` alongside the existing `cloudinary_client.py` / `cloudinary_sync.py` entries:

> `cloudinary_urls.py` — builds public Cloudinary delivery URLs from `Product.barcode` (the read half of the contract `cloudinary_sync.py` writes). Pure string building; no network calls, no credentials. Used by the Shopify CSV export.

- [ ] **Step 4: Update `docs/STATUS.md`**

Add an entry dated 2026-07-15 recording that the Shopify CSV export now emits Cloudinary image URLs, noting that the previous LAN-address URLs were unreachable by Shopify.

- [ ] **Step 5: Verify nothing else in the docs contradicts the change**

Run: `git grep -n "build_absolute_uri" -- docs/`
Expected: no hit that describes the Shopify CSV image columns as local URLs. If one exists, correct it.

- [ ] **Step 6: Commit**

```bash
git add docs/STATUS.md docs/PRD.md docs/ARCHITECTURE.md
git commit -m "docs: record Cloudinary image URLs in the Shopify CSV export"
```

---

## Manual verification (after Task 3)

Not a task — a smoke check to run once at the end, since the automated tests deliberately never touch the network.

1. Confirm a real URL resolves (this is the exact string the export now emits):

```bash
curl.exe -sI "https://res.cloudinary.com/bulvpmzg/image/upload/c_pad,b_white,w_1600,h_1600,q_auto/6290362349730.jpg" | head -3
```

Expected: `HTTP/1.1 200` and `content-type: image/jpeg`.

2. Restart the server (`start.bat`), export the Shopify CSV from the product list, and open it. The `Product image URL` column should hold `res.cloudinary.com` links for products that have images, and be blank for the 170 that do not.
