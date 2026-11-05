"""Settings -> Catalog: what the shop edits without a developer."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from stock.models import (Brand, Category, Concentration, FragranceFamily, Product, ProductSeries,
                          ProductType)
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
        self.assertIn('?type=creme"', self.client.get(reverse("add_product")).content.decode())

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

    def test_a_new_type_gets_links_and_a_description_not_the_scent_section(self):
        self._post(action="add", name="Creme", default_category=self.perfumes.id)
        row = ProductType.objects.get(name="Creme")
        self.assertFalse(row.shows_scent_details)
        self.assertTrue(row.has_contents)

    def test_save_stays_on_screen_in_the_panel_foot(self):
        html = self.client.get(reverse("catalog_types")).content.decode()
        row = ProductType.objects.get(code="mini")
        foot = html[html.index(f'id="type-{row.id}"'):]
        foot = foot[foot.index('data-part="foot"'):foot.index("</template>")]
        self.assertIn(f'form="type-form-{row.id}"', foot)
        self.assertIn("<details", foot)


class BrandsMixin(CatalogMixin):
    def setUp(self):
        super().setUp()
        self.lattafa = Brand.objects.create(name="Lattafa")
        self.armaf = Brand.objects.create(name="Armaf")
        self.khamrah = ProductSeries.objects.create(brand=self.lattafa, name="Khamrah")
        self.yara = ProductSeries.objects.create(brand=self.lattafa, name="Yara")
        self.cdn = ProductSeries.objects.create(brand=self.armaf, name="Club De Nuit")
        self.product = self._branded("6290000076001", self.lattafa, self.khamrah)
        self.armaf_product = self._branded("6290000076002", self.armaf, self.cdn)

    def _branded(self, barcode, brand, line, **fields):
        return self._product(barcode, brand_master=brand, series_master=line, model=line.name, **{"brand": brand.name, **fields})

    def _product(self, barcode="6290000072001", **fields):
        fields.setdefault("name", "Yara")
        fields.setdefault("brand", "Lattafa")
        return Product.objects.create(barcode=barcode, category=self.perfumes, **fields)

    def _brands(self, **data):
        return self.client.post(reverse("catalog_brands"), data, follow=True)

    def _lines(self, **data):
        return self.client.post(reverse("catalog_lines"), data, follow=True)


class BrandsTabTests(BrandsMixin):
    def test_only_an_admin_gets_in(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("catalog_brands")).status_code, 302)

    def test_the_counts_ignore_archived_products(self):
        self._branded("6290000076003", self.lattafa, self.khamrah, archived_at=timezone.now())
        rows = {b.name: b for b in self.client.get(reverse("catalog_brands")).context["brands"]}
        self.assertEqual((rows["Lattafa"].live_count, rows["Lattafa"].line_count), (1, 2))

    def test_adding_a_brand(self):
        self._brands(action="add", name="rasasi")
        self.assertTrue(Brand.objects.filter(name="Rasasi").exists())

    def test_adding_a_brand_that_exists_is_refused(self):
        response = self._brands(action="add", name="LATTAFA")
        self.assertEqual(Brand.objects.filter(name__iexact="lattafa").count(), 1)
        self.assertContains(response, "already exists")

    def test_renaming_rewrites_the_products(self):
        self._brands(action="rename", id=self.lattafa.id, name="Lattafa Perfumes")
        self.product.refresh_from_db()
        self.assertEqual(self.product.brand, "Lattafa Perfumes")

    def test_a_taken_name_offers_the_merge(self):
        response = self._brands(action="rename", id=self.armaf.id, name="lattafa")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Merge")
        self.assertEqual(Brand.objects.get(pk=self.armaf.id).name, "Armaf")

    def test_merge_says_how_many_products_moved(self):
        response = self._brands(action="merge", id=self.armaf.id, into=self.lattafa.id)
        self.assertContains(response, "1 product moved to Lattafa")
        self.assertFalse(Brand.objects.filter(pk=self.armaf.id).exists())

    def test_merge_into_itself_or_nothing_changes_nothing(self):
        for target in (self.armaf.id, "", 999999):
            self.assertEqual(self._brands(action="merge", id=self.armaf.id, into=target).status_code, 200)
            self.assertTrue(Brand.objects.filter(pk=self.armaf.id).exists())

    def test_a_brand_with_products_cannot_be_deleted(self):
        response = self._brands(action="delete", id=self.lattafa.id)
        self.assertTrue(Brand.objects.filter(pk=self.lattafa.id).exists())
        self.assertContains(response, "still has products")

    def test_an_empty_brand_can_be_deleted(self):
        empty = Brand.objects.create(name="Old")
        self._brands(action="delete", id=empty.id)
        self.assertFalse(Brand.objects.filter(pk=empty.id).exists())

    def test_a_row_deleted_meanwhile_is_not_a_crash(self):
        self.assertEqual(self._brands(action="rename", id=999999, name="X").status_code, 200)


class LinesTabTests(BrandsMixin):
    def test_lines_filter_by_brand(self):
        rows = self.client.get(reverse("catalog_lines"), {"brand": self.armaf.id}).context["lines"]
        self.assertEqual([line.name for line in rows], ["Club De Nuit"])

    def test_every_line_shows_without_a_filter(self):
        rows = self.client.get(reverse("catalog_lines")).context["lines"]
        self.assertEqual(len(rows), 3)

    def test_adding_a_line_needs_a_brand(self):
        self._lines(action="add", name="Asad")
        self.assertFalse(ProductSeries.objects.filter(name="Asad").exists())
        self._lines(action="add", name="Asad", brand=self.lattafa.id)
        self.assertTrue(ProductSeries.objects.filter(name="Asad", brand=self.lattafa).exists())

    def test_renaming_and_a_taken_name(self):
        self._lines(action="rename", id=self.khamrah.id, name="Khamrah Qahwa")
        self.product.refresh_from_db()
        self.assertEqual(self.product.model, "Khamrah Qahwa")
        response = self._lines(action="rename", id=self.khamrah.id, name="yara")
        self.assertContains(response, "Merge")

    def test_merging_keeps_to_the_brand(self):
        self._lines(action="merge", id=self.khamrah.id, into=self.cdn.id)
        self.assertTrue(ProductSeries.objects.filter(pk=self.khamrah.id).exists())
        self._lines(action="merge", id=self.khamrah.id, into=self.yara.id)
        self.assertFalse(ProductSeries.objects.filter(pk=self.khamrah.id).exists())

    def test_a_line_with_products_cannot_be_deleted(self):
        response = self._lines(action="delete", id=self.khamrah.id)
        self.assertTrue(ProductSeries.objects.filter(pk=self.khamrah.id).exists())
        self.assertContains(response, "still has products")

    def test_an_empty_line_can_be_deleted(self):
        self._lines(action="delete", id=self.yara.id)
        self.assertFalse(ProductSeries.objects.filter(pk=self.yara.id).exists())

    def test_the_filter_survives_a_save(self):
        response = self.client.post(reverse("catalog_lines") + f"?brand={self.armaf.id}",
                                    {"action": "rename", "id": self.cdn.id, "name": "CDN"})
        self.assertIn(f"brand={self.armaf.id}", response["Location"])


class CategoriesTabTests(BrandsMixin):
    def _post_to(self, **data):
        return self.client.post(reverse("catalog_categories"), data, follow=True)

    def test_adding_a_category(self):
        self._post_to(action="add", name="Body care")
        row = Category.objects.get(name="Body care")
        self.assertEqual(row.form_kind, "perfume")
        self.assertFalse(row.sync_to_shopify)

    def test_a_name_taken_is_refused(self):
        response = self._post_to(action="add", name="PERFUMES")
        self.assertEqual(Category.objects.filter(name__iexact="perfumes").count(), 1)
        self.assertContains(response, "already exists")

    def test_renaming_and_the_shopify_switch_save(self):
        row = Category.objects.create(name="Body care", form_kind="perfume")
        self._post_to(action="save", id=row.id, name="Skin care", sync_to_shopify="on")
        row.refresh_from_db()
        self.assertEqual((row.name, row.sync_to_shopify), ("Skin care", True))
        self._post_to(action="save", id=row.id, name="Skin care")
        row.refresh_from_db()
        self.assertFalse(row.sync_to_shopify)

    def test_a_category_in_use_stays(self):
        response = self._post_to(action="delete", id=self.perfumes.id)
        self.assertTrue(Category.objects.filter(pk=self.perfumes.id).exists())
        self.assertContains(response, "still has products")

    def test_a_category_with_a_subcategory_stays(self):
        parent = Category.objects.create(name="Parent")
        Category.objects.create(name="Child", parent=parent)
        response = self._post_to(action="delete", id=parent.id)
        self.assertTrue(Category.objects.filter(pk=parent.id).exists())
        self.assertContains(response, "has subcategories")

    def test_the_default_of_a_type_stays(self):
        row = Category.objects.create(name="Body care")
        ProductType.objects.filter(code="mini").update(default_category=row)
        response = self._post_to(action="delete", id=row.id)
        self.assertTrue(Category.objects.filter(pk=row.id).exists())
        self.assertContains(response, "is the category of Mini")

    def test_an_unused_category_goes(self):
        row = Category.objects.create(name="Old")
        self._post_to(action="delete", id=row.id)
        self.assertFalse(Category.objects.filter(pk=row.id).exists())

    def test_the_counts_ignore_archived_products(self):
        row = Category.objects.create(name="Old")
        Product.objects.create(barcode="6290000076010", brand="L", category=row, archived_at=timezone.now())
        rows = {c.name: c for c in self.client.get(reverse("catalog_categories")).context["categories"]}
        self.assertEqual(rows["Old"].live_count, 0)
        self.assertEqual(rows["Perfumes"].live_count, 2)


class StrengthsTabTests(BrandsMixin):
    def _post_to(self, **data):
        return self.client.post(reverse("catalog_strengths"), data, follow=True)

    def test_new_rows_appear_in_the_product_form(self):
        self._post_to(action="add", name="Parfum", short="PAR", shopify_tag="Parfum", sort_order="15")
        row = Concentration.objects.get(name="Parfum")
        self.assertEqual((row.short, row.shopify_tag, row.sort_order), ("PAR", "Parfum", 15))
        self.assertContains(self.client.get(reverse("add_product"), {"type": "perfume"}), "PAR")

    def test_saving_changes_every_field(self):
        row = Concentration.objects.create(name="Eau de Parfum", short="EDP")
        self._post_to(action="save", id=row.id, name="EdP", short="E", shopify_tag="T", sort_order="9")
        row.refresh_from_db()
        self.assertEqual((row.name, row.short, row.shopify_tag, row.sort_order), ("EdP", "E", "T", 9))

    def test_a_strength_in_use_stays_and_says_how_many(self):
        row = Concentration.objects.create(name="Eau de Parfum", short="EDP")
        Product.objects.filter(pk=self.product.pk).update(concentration=row)
        response = self._post_to(action="delete", id=row.id)
        self.assertTrue(Concentration.objects.filter(pk=row.id).exists())
        self.assertContains(response, "1 product uses it")

    def test_an_unused_strength_goes(self):
        row = Concentration.objects.create(name="Old")
        self._post_to(action="delete", id=row.id)
        self.assertFalse(Concentration.objects.filter(pk=row.id).exists())

    def test_a_name_taken_is_refused(self):
        Concentration.objects.create(name="Eau de Parfum")
        response = self._post_to(action="add", name="eau de parfum")
        self.assertEqual(Concentration.objects.count(), 1)
        self.assertContains(response, "already exists")


class FamiliesTabTests(BrandsMixin):
    def _post_to(self, **data):
        return self.client.post(reverse("catalog_families"), data, follow=True)

    def test_adding_and_saving(self):
        self._post_to(action="add", name="Oud", shopify_tag="Oriental", sort_order="5")
        row = FragranceFamily.objects.get(name="Oud")
        self.assertEqual((row.shopify_tag, row.sort_order), ("Oriental", 5))
        self._post_to(action="save", id=row.id, name="Oud Rich", shopify_tag="", sort_order="6")
        row.refresh_from_db()
        self.assertEqual((row.name, row.shopify_tag, row.sort_order), ("Oud Rich", "", 6))

    def test_a_family_in_use_stays(self):
        row = FragranceFamily.objects.create(name="Oud")
        self.product.fragrance_families.add(row)
        response = self._post_to(action="delete", id=row.id)
        self.assertTrue(FragranceFamily.objects.filter(pk=row.id).exists())
        self.assertContains(response, "1 product uses it")

    def test_an_unused_family_goes(self):
        row = FragranceFamily.objects.create(name="Old")
        self._post_to(action="delete", id=row.id)
        self.assertFalse(FragranceFamily.objects.filter(pk=row.id).exists())
