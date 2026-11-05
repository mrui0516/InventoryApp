"""Settings -> Catalog: what the shop edits without a developer."""
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from stock.models import Category, Product, ProductType
from stock.models.product_types import clear_type_cache


class CatalogMixin(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.boss = User.objects.create_superuser("boss", password="pw")
        cls.manager = User.objects.create_user("manager", password="pw", is_staff=True)
        cls.perfumes = Category.objects.create(name="Perfumes", form_kind="perfume")

    def setUp(self):
        self.addCleanup(clear_type_cache)
        clear_type_cache()
        self.client.force_login(self.boss)

    def _product(self, barcode="6290000072001", **fields):
        fields.setdefault("name", "Yara")
        return Product.objects.create(brand="Lattafa", barcode=barcode, category=self.perfumes, **fields)

    def _post(self, **data):
        return self.client.post(reverse("catalog_types"), data, follow=True)


class TypesTabTests(CatalogMixin):
    def test_only_an_admin_gets_in(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("catalog_types")).status_code, 302)

    def test_the_tab_lists_the_types_with_their_products(self):
        self._product(product_type_id="mini")
        html = self.client.get(reverse("catalog_types")).content.decode()
        self.assertIn("Oil perfume", html)
        self.assertIn("Coffret (gift set)", html)

    def test_settings_opens_on_the_types(self):
        self.assertRedirects(self.client.get(reverse("catalog_settings")), reverse("catalog_types"))

    def test_adding_creme_needs_no_code_and_starts_without_discount(self):
        self._post(action="add", name="Creme", name_word="Creme", default_category=self.perfumes.id)
        row = ProductType.objects.get(name="Creme")
        self.assertEqual(row.code, "creme")
        self.assertFalse(row.wholesale_discount)
        self.assertFalse(row.takes_concentration)
        self.assertTrue(row.takes_volume)
        self.assertGreater(row.sort_order, ProductType.objects.get(code="coffret").sort_order)
        self.assertIn('value="creme"', self.client.get(reverse("add_product")).content.decode())

    def test_a_name_that_makes_the_same_code_still_gets_its_own(self):
        self._post(action="add", name="Mini!", default_category=self.perfumes.id)
        self.assertEqual(ProductType.objects.get(name="Mini!").code, "mini_2")

    def test_a_new_type_needs_a_category(self):
        response = self._post(action="add", name="Creme", name_word="Creme")
        self.assertFalse(ProductType.objects.filter(name="Creme").exists())
        self.assertContains(response, "Choose a category")

    def test_renaming_keeps_the_code_and_the_switches_save(self):
        row = ProductType.objects.get(code="body_spray")
        self._post(action="save", id=row.id, name="Deo", name_word="Deo", default_category=self.perfumes.id,
                   sort_order="5", takes_volume="on", wholesale_discount="on")
        row.refresh_from_db()
        self.assertEqual((row.code, row.name, row.sort_order), ("body_spray", "Deo", 5))
        self.assertTrue(row.wholesale_discount)
        self.assertFalse(row.shows_scent_details)

    def test_perfume_cannot_be_deleted(self):
        row = ProductType.objects.get(code="perfume")
        self._post(action="delete", id=row.id)
        self.assertTrue(ProductType.objects.filter(code="perfume").exists())

    def test_an_empty_type_goes_without_a_target(self):
        self._post(action="delete", id=ProductType.objects.get(code="mini").id)
        self.assertFalse(ProductType.objects.filter(code="mini").exists())

    def test_deleting_a_type_in_use_moves_live_and_archived_products(self):
        row = ProductType.objects.get(code="mini")
        live = self._product(barcode="6290000072001", product_type_id="mini")
        gone = self._product(barcode="6290000072002", product_type_id="mini", archived_at=timezone.now())
        self._post(action="delete", id=row.id, move_to=ProductType.objects.get(code="perfume").id)
        self.assertFalse(ProductType.objects.filter(code="mini").exists())
        self.assertEqual(Product.all_objects.get(pk=gone.pk).product_type_id, "perfume")
        self.assertEqual(Product.objects.get(pk=live.pk).product_type_id, "perfume")

    def test_a_type_in_use_cannot_go_without_a_target_or_to_itself(self):
        row = ProductType.objects.get(code="mini")
        product = self._product(product_type_id="mini")
        for target in ("", row.id, 999999):
            response = self._post(action="delete", id=row.id, move_to=target)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(ProductType.objects.filter(code="mini").exists())
            product.refresh_from_db()
            self.assertEqual(product.product_type_id, "mini")

    def test_a_row_deleted_meanwhile_is_not_a_crash(self):
        self.assertEqual(self._post(action="save", id=999999, name="X").status_code, 200)

    def test_the_shop_catalog_page_does_not_light_up_the_settings_link(self):
        html = self.client.get(reverse("catalog")).content.decode()
        link = html[:html.index(f'href="{reverse("catalog_settings")}"')].rsplit("<a ", 1)[1]
        self.assertNotIn("active", link)

    def test_the_settings_link_lights_up_on_its_own_tabs(self):
        html = self.client.get(reverse("catalog_types")).content.decode()
        link = html[:html.index(f'href="{reverse("catalog_settings")}"')].rsplit("<a ", 1)[1]
        self.assertIn("active", link)
