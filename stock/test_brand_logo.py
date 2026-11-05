"""A brand's logo: an uploaded picture, checked and shrunk, available everywhere."""
import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import Context, Template
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from stock.models import Brand, Product
from stock.services.brand_logo import clean_logo
from stock.services.catalog_admin import merge_brands
from stock.services.catalog_prune import empty_brands, prune
from stock.test_catalog_settings import CatalogMixin

UPLOADS = tempfile.mkdtemp()


def picture(name="logo.png", size=(800, 400), fmt="PNG", mode="RGBA"):
    data = io.BytesIO()
    Image.new(mode, size, (200, 0, 0, 128) if mode == "RGBA" else (200, 0, 0)).save(data, fmt)
    return SimpleUploadedFile(name, data.getvalue(), content_type=f"image/{fmt.lower()}")


class CleanLogoTests(TestCase):
    def test_a_big_photo_is_shrunk(self):
        result = Image.open(clean_logo(picture()))
        self.assertEqual(max(result.size), 256)

    def test_a_small_logo_keeps_its_size(self):
        self.assertEqual(Image.open(clean_logo(picture(size=(64, 64)))).size, (64, 64))

    def test_transparency_is_kept(self):
        self.assertEqual(Image.open(clean_logo(picture())).mode, "RGBA")

    def test_jpg_and_webp_are_accepted(self):
        self.assertTrue(clean_logo(picture("a.jpg", fmt="JPEG", mode="RGB")))
        self.assertTrue(clean_logo(picture("a.webp", fmt="WEBP", mode="RGB")))

    def test_a_text_file_named_png_is_refused(self):
        with self.assertRaisesMessage(ValueError, "must be a JPG, PNG or WebP"):
            clean_logo(SimpleUploadedFile("x.png", b"not an image", content_type="image/png"))

    def test_svg_is_refused(self):
        with self.assertRaisesMessage(ValueError, "must be a JPG, PNG or WebP"):
            clean_logo(SimpleUploadedFile("x.svg", b"<svg onload='alert(1)'/>", content_type="image/svg+xml"))

    def test_gif_is_refused(self):
        with self.assertRaisesMessage(ValueError, "must be a JPG, PNG or WebP"):
            clean_logo(picture("x.gif", fmt="GIF", mode="RGB"))

    def test_over_two_megabytes_is_refused(self):
        with self.assertRaisesMessage(ValueError, "over 2 MB"):
            clean_logo(SimpleUploadedFile("x.png", b"0" * (2 * 1024 * 1024 + 1), content_type="image/png"))


class LogoTagTests(TestCase):
    def _render(self, **context):
        template = Template('{% load brands %}{% brand_logo brand size=32 fallback=text %}')
        return template.render(Context(context))

    def test_the_tag_falls_back_to_the_first_letter(self):
        html = self._render(brand=None, text="lattafa")
        self.assertIn(">L<", html)
        self.assertNotIn("<img", html)

    def test_a_brand_without_a_logo_uses_its_own_letter(self):
        self.assertIn(">A<", self._render(brand=Brand(name="Armaf"), text=""))

    def test_a_logo_is_an_image_with_the_brand_as_its_alt(self):
        html = self._render(brand=Brand(name="Armaf", logo="brand_logos/a.png"), text="")
        self.assertIn('<img src="/media/brand_logos/a.png"', html)
        self.assertIn('alt="Armaf"', html)

    def test_nothing_at_all_still_renders(self):
        self.assertIn(">?<", self._render(brand=None, text=""))


@override_settings(MEDIA_ROOT=UPLOADS)
class LogoPagesTests(CatalogMixin):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(UPLOADS, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.brand = Brand.objects.create(name="Lattafa")
        self.product = Product.objects.create(barcode="6290000079001", brand="Lattafa", brand_master=self.brand,
                                              name="Yara", category=self.perfumes)

    def _upload(self, upload):
        return self.client.post(reverse("catalog_brands"), {"action": "logo", "id": self.brand.id, "logo": upload},
                                follow=True)

    def test_uploading_saves_a_small_png(self):
        self._upload(picture())
        self.brand.refresh_from_db()
        self.assertTrue(self.brand.logo.name.startswith("brand_logos/"))
        self.assertEqual(max(Image.open(self.brand.logo.path).size), 256)

    def test_a_bad_upload_saves_nothing_and_shows_the_message(self):
        response = self._upload(SimpleUploadedFile("x.png", b"nope", content_type="image/png"))
        self.assertContains(response, "must be a JPG, PNG or WebP")
        self.brand.refresh_from_db()
        self.assertFalse(self.brand.logo)

    def test_no_file_is_not_a_crash(self):
        response = self.client.post(reverse("catalog_brands"), {"action": "logo", "id": self.brand.id}, follow=True)
        self.assertEqual(response.status_code, 200)

    def test_removing_the_logo(self):
        self._upload(picture())
        self.client.post(reverse("catalog_brands"), {"action": "remove_logo", "id": self.brand.id})
        self.brand.refresh_from_db()
        self.assertFalse(self.brand.logo)

    def test_the_product_page_shows_it(self):
        self._upload(picture())
        self.brand.refresh_from_db()
        html = self.client.get(reverse("product_detail", args=[self.product.pk])).content.decode()
        self.assertIn(self.brand.logo.url, html)

    def test_the_filtered_list_shows_it_on_the_chip(self):
        self._upload(picture())
        self.brand.refresh_from_db()
        html = self.client.get(reverse("product_list"), {"brand": self.brand.id}).content.decode()
        self.assertIn(self.brand.logo.url, html)

    def test_a_brand_with_a_logo_is_not_pruned(self):
        self._upload(picture())
        self.product.delete()
        self.assertTrue(Brand.objects.filter(pk=self.brand.pk).exists())

    def test_a_logo_brand_left_empty_can_still_be_deleted_by_hand(self):
        self._upload(picture())
        self.product.delete()
        self.client.post(reverse("catalog_brands"), {"action": "delete", "id": self.brand.id})
        self.assertFalse(Brand.objects.filter(pk=self.brand.pk).exists())

    def test_the_command_list_leaves_logo_brands_out(self):
        self._upload(picture())
        self.product.delete()
        self.assertFalse(empty_brands().filter(pk=self.brand.pk).exists())
        self.assertEqual(prune()[0], 0)

    def test_merging_passes_the_logo_on(self):
        other = Brand.objects.create(name="Armaf")
        self._upload(picture())
        self.brand.refresh_from_db()
        name = self.brand.logo.name
        merge_brands(other, self.brand)
        other.refresh_from_db()
        self.assertEqual(other.logo.name, name)

    def test_the_pdf_prints_with_and_without_a_logo(self):
        from stock.services.catalog_pdf import build_catalog_pdf
        self._upload(picture())
        self.product.export_title = "Yara"
        self.product.export_model = "Other"
        self.product.export_availability = "Em stock"
        with_logo = build_catalog_pdf({"Lattafa": [self.product]}, include_images=False).getvalue()
        without = build_catalog_pdf({"No Such Brand": [self.product]}, include_images=False).getvalue()
        self.assertIn(b"/Subtype /Image", with_logo)
        self.assertNotIn(b"/Subtype /Image", without)
