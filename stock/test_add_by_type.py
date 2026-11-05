"""Adding a product starts from its type; the form then asks only what that type needs."""
from django.urls import reverse

from stock.models import Brand, Category, Product, ProductType
from stock.models.product_types import clear_type_cache
from stock.test_catalog_settings import CatalogMixin


class AddByTypeMixin(CatalogMixin):
    def setUp(self):
        super().setUp()
        self.brand = Brand.objects.create(name="Lattafa")
        self.brand.categories.add(self.perfumes)

    def _get(self, **params):
        return self.client.get(reverse("add_product"), params)

    def _save(self, type, barcode, **fields):
        data = {"barcode": barcode, "brand_master": self.brand.id, "name": "Yara", "product_type": type,
                "default_price": "40", "wholesale_price": "28"}
        data.update(fields)
        return self.client.post(f'{reverse("add_product")}?type={type}', data)


class AddByTypeTests(AddByTypeMixin):
    def test_the_page_opens_on_the_types(self):
        html = self._get().content.decode()
        self.assertIn('href="/add-product/?type=oil_perfume"', html)
        self.assertNotIn('name="barcode"', html)

    def test_an_unknown_type_goes_back_to_the_cards(self):
        self.assertNotIn('name="barcode"', self._get(type="nope").content.decode())

    def test_the_form_says_the_type_and_can_go_back(self):
        html = self._get(type="body_spray").content.decode()
        self.assertIn("Add Body spray", html)
        self.assertIn('name="barcode"', html)
        self.assertIn("Change type", html)

    def test_the_type_is_fixed_on_the_form(self):
        html = self._get(type="body_spray").content.decode()
        self.assertRegex(html, r'<input type="hidden" name="product_type" value="body_spray"')

    def test_a_failed_save_stays_on_the_type(self):
        response = self.client.post(reverse("add_product") + "?type=body_spray", {"barcode": ""})
        self.assertContains(response, "Add Body spray")

    def test_the_category_comes_from_the_type(self):
        other = Category.objects.create(name="Body care")
        ProductType.objects.filter(code="body_spray").update(default_category=other)
        clear_type_cache()
        self.assertEqual(self._save("body_spray", "6290000073001").status_code, 302)
        self.assertEqual(Product.objects.get(barcode="6290000073001").category, other)

    def test_a_type_in_a_new_category_still_offers_every_brand(self):
        other = Category.objects.create(name="Body care")
        ProductType.objects.filter(code="body_spray").update(default_category=other)
        clear_type_cache()
        form = self._get(type="body_spray").context["form"]
        self.assertIn(self.brand, form.fields["brand_master"].queryset)

    def test_the_edit_page_keeps_the_type_list(self):
        product = Product.objects.create(barcode="6290000073002", brand="Lattafa", category=self.perfumes)
        html = self.client.get(reverse("edit_product", args=[product.pk])).content.decode()
        self.assertIn('<select name="product_type"', html)


class WhatTheFormAsksTests(AddByTypeMixin):
    def test_description_sits_outside_the_scent_section(self):
        html = self._get(type="air_freshener").content.decode()
        scent = html[html.index('id="pfScent"'):]
        self.assertNotIn('name="description"', scent[:scent.index("</section>")])
        self.assertIn('name="description"', html)

    def test_hidden_scent_details_stay_in_the_form_so_they_are_kept(self):
        product = Product.objects.create(barcode="6290000073003", brand="Lattafa", category=self.perfumes,
                                         product_type_id="air_freshener", notes_top="Rosa")
        html = self.client.get(reverse("edit_product", args=[product.pk])).content.decode()
        self.assertIn('value="Rosa"', html)

    def test_the_browser_gets_the_rules_for_every_type(self):
        html = self._get(type="coffret").content.decode()
        self.assertIn('id="pfTypeRules"', html)
        self.assertIn('"scent": false', html)
        self.assertIn('id="pfVolumeField"', html)
