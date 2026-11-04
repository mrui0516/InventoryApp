"""A brand or line with no product left deletes itself."""
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from stock.models import Brand, Category, Product, ProductSeries
from stock.services.catalog_prune import empty_brands, empty_series, prune


class CatalogPruneMixin(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Perfumes", form_kind="perfume")
        cls.boss = User.objects.create_superuser("boss", password="pw")

    def setUp(self):
        self.brand = Brand.objects.create(name="Lattafa")
        self.line = ProductSeries.objects.create(brand=self.brand, name="Khamrah")
        self.other_line = ProductSeries.objects.create(brand=self.brand, name="Yara")
        self.other_brand = Brand.objects.create(name="Armaf")
        self.other_brand_line = ProductSeries.objects.create(brand=self.other_brand, name="Club De Nuit")
        self.product = self._product("6290000008001", self.brand, self.line)
        self.keeper = self._product("6290000008002", self.other_brand, self.other_brand_line)
        self.neighbour = self._product("6290000008003", self.brand, self.other_line)

    def _product(self, barcode, brand, line):
        return Product.objects.create(name="X", barcode=barcode, brand=brand.name, brand_master=brand,
                                      model=line.name if line else "", series_master=line, category=self.category)

    def _exists(self, row):
        return type(row).objects.filter(pk=row.pk).exists()


class AutomaticPruneTests(CatalogPruneMixin):
    def test_changing_the_line_removes_the_empty_one(self):
        self.product.series_master = self.other_line
        self.product.save()
        self.assertFalse(self._exists(self.line))
        self.assertTrue(self._exists(self.other_line))
        self.assertTrue(self._exists(self.brand))

    def test_changing_the_brand_removes_the_empty_brand_and_its_lines(self):
        self.neighbour.delete()
        self.product.brand_master = self.other_brand
        self.product.series_master = self.other_brand_line
        self.product.save()
        self.assertFalse(self._exists(self.brand))
        self.assertFalse(self._exists(self.line))
        self.assertFalse(self._exists(self.other_line))

    def test_a_brand_with_another_product_stays(self):
        self.product.delete()
        self.assertTrue(self._exists(self.brand))
        self.assertFalse(self._exists(self.line))

    def test_deleting_the_last_product_removes_brand_and_line(self):
        self.keeper.delete()
        self.assertFalse(self._exists(self.other_brand))
        self.assertFalse(self._exists(self.other_brand_line))

    def test_the_delete_button_archives_and_prunes_but_keeps_the_text(self):
        self.client.force_login(self.boss)
        response = self.client.post(reverse("delete_product", args=[self.keeper.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self._exists(self.other_brand))
        archived = Product.all_objects.get(pk=self.keeper.pk)
        self.assertIsNotNone(archived.archived_at)
        self.assertIsNone(archived.brand_master)
        self.assertIsNone(archived.series_master)
        self.assertEqual((archived.brand, archived.model), ("Armaf", "Club De Nuit"))

    def test_a_save_that_changes_nothing_touches_nothing(self):
        empty = Brand.objects.create(name="Waiting")
        self.product.default_price = 40
        self.product.save()
        self.product.save(update_fields=["default_price"])
        self.assertTrue(self._exists(empty))
        self.assertTrue(self._exists(self.line))

    def test_a_new_brand_typed_in_the_form_survives_its_first_product(self):
        self.client.force_login(self.boss)
        response = self.client.post(reverse("add_product"), {
            "category": self.category.id, "barcode": "6290000008009", "new_brand_name": "Rasasi",
            "new_series_name": "Hawas", "name": "", "perfume_type": "perfume",
            "default_price": "40", "wholesale_price": "28"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Brand.objects.filter(name="Rasasi").exists())
        self.assertTrue(ProductSeries.objects.filter(name="Hawas").exists())


class WhatCountsTests(CatalogPruneMixin):
    def test_a_live_product_held_only_as_text_keeps_brand_and_line(self):
        Product.objects.filter(pk__in=[self.keeper.pk]).update(brand_master=None, series_master=None)
        self.assertFalse(empty_brands().filter(pk=self.other_brand.pk).exists())
        self.assertFalse(empty_series().filter(pk=self.other_brand_line.pk).exists())

    def test_an_archived_product_does_not_count(self):
        Product.objects.filter(pk=self.keeper.pk).update(archived_at="2026-11-01T10:00:00Z")
        self.assertTrue(empty_brands().filter(pk=self.other_brand.pk).exists())
        self.assertTrue(empty_series().filter(pk=self.other_brand_line.pk).exists())

    def test_a_brand_whose_line_holds_a_product_of_another_brand_stays(self):
        Product.objects.filter(pk=self.keeper.pk).update(brand_master=self.brand)
        self.assertFalse(empty_brands().filter(pk=self.other_brand.pk).exists())

    def test_prune_by_ids_leaves_the_rest_alone(self):
        empty = Brand.objects.create(name="Old")
        self.assertEqual(prune({self.brand.pk}, set()), (0, 0))
        self.assertTrue(self._exists(empty))
        self.assertEqual(prune(set(), set()), (0, 0))
        self.assertTrue(self._exists(empty))


class CommandTests(CatalogPruneMixin):
    def setUp(self):
        super().setUp()
        self.old = Brand.objects.create(name="China")
        ProductSeries.objects.create(brand=self.old, name="CR2016")
        ProductSeries.objects.create(brand=self.old, name="CR2025")
        self.spare_line = ProductSeries.objects.create(brand=self.brand, name="Unused")

    def test_the_dry_run_lists_and_deletes_nothing(self):
        out = StringIO()
        call_command("prune_empty_brands", stdout=out)
        text = out.getvalue()
        self.assertIn("China  (2 lines)", text)
        self.assertIn("Lattafa: Unused", text)
        self.assertIn("Dry run", text)
        self.assertTrue(self._exists(self.old))
        self.assertTrue(self._exists(self.spare_line))

    def test_apply_deletes_only_the_empty_ones(self):
        out = StringIO()
        call_command("prune_empty_brands", "--apply", stdout=out)
        self.assertIn("Deleted 1 brands and 3 lines.", out.getvalue())
        self.assertFalse(self._exists(self.old))
        self.assertFalse(self._exists(self.spare_line))
        self.assertTrue(self._exists(self.brand))
        self.assertTrue(self._exists(self.line))
        self.assertEqual(ProductSeries.objects.filter(brand=self.old).count(), 0)
