"""Renaming and merging brands and lines keeps the text on every product in step."""
from django.test import TestCase
from django.utils import timezone

from stock.models import Brand, Category, Product, ProductSeries
from stock.services.catalog_admin import (NameTaken, merge_brands, merge_lines, rename_brand, rename_line)


class BrandsMixin(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Perfumes", form_kind="perfume")

    def setUp(self):
        self.lattafa = Brand.objects.create(name="Lattafa")
        self.armaf = Brand.objects.create(name="Armaf")
        self.khamrah = ProductSeries.objects.create(brand=self.lattafa, name="Khamrah")
        self.yara = ProductSeries.objects.create(brand=self.lattafa, name="Yara")
        self.cdn = ProductSeries.objects.create(brand=self.armaf, name="Club De Nuit")

    def _product(self, barcode, brand, line=None, **fields):
        fields.setdefault("name", "X")
        return Product.objects.create(barcode=barcode, brand=brand.name, brand_master=brand, series_master=line,
                                      model=line.name if line else "", category=self.category, **fields)

    def _text(self, product):
        row = Product.all_objects.get(pk=product.pk)
        return row.brand, row.model


class RenameTests(BrandsMixin):
    def test_renaming_a_brand_updates_live_and_archived_text(self):
        live = self._product("6290000075001", self.lattafa, self.khamrah)
        gone = self._product("6290000075002", self.lattafa, self.khamrah, archived_at=timezone.now())
        other = self._product("6290000075003", self.armaf, self.cdn)
        rename_brand(self.lattafa, "lattafa perfumes")
        self.assertEqual(Brand.objects.get(pk=self.lattafa.pk).name, "Lattafa Perfumes")
        self.assertEqual(self._text(live)[0], "Lattafa Perfumes")
        self.assertEqual(self._text(gone)[0], "Lattafa Perfumes")
        self.assertEqual(self._text(other)[0], "Armaf")

    def test_changing_only_the_case_is_allowed(self):
        brand = Brand.objects.create(name="Rasasi Old")
        rename_brand(brand, "rasasi old")
        self.assertEqual(Brand.objects.get(pk=brand.pk).name, "Rasasi Old")

    def test_an_empty_name_is_refused(self):
        with self.assertRaises(ValueError):
            rename_brand(self.lattafa, "   ")
        with self.assertRaises(ValueError):
            rename_line(self.khamrah, "")

    def test_renaming_a_line_updates_model_only_for_its_products(self):
        same_name = ProductSeries.objects.create(brand=self.armaf, name="Khamrah")
        mine = self._product("6290000075001", self.lattafa, self.khamrah)
        theirs = self._product("6290000075002", self.armaf, same_name)
        rename_line(self.khamrah, "Khamrah Qahwa")
        self.assertEqual(self._text(mine)[1], "Khamrah Qahwa")
        self.assertEqual(self._text(theirs)[1], "Khamrah")

    def test_a_name_in_use_is_refused_even_by_case(self):
        with self.assertRaises(NameTaken) as caught:
            rename_brand(self.armaf, "LATTAFA")
        self.assertEqual(caught.exception.existing, self.lattafa)
        self.assertEqual(Brand.objects.get(pk=self.armaf.pk).name, "Armaf")

    def test_a_line_name_in_use_within_the_brand_is_refused(self):
        with self.assertRaises(NameTaken) as caught:
            rename_line(self.yara, "KHAMRAH")
        self.assertEqual(caught.exception.existing, self.khamrah)

    def test_the_same_line_name_under_another_brand_is_fine(self):
        rename_line(self.cdn, "Khamrah")
        self.assertEqual(ProductSeries.objects.get(pk=self.cdn.pk).name, "Khamrah")


class MergeTests(BrandsMixin):
    def test_merging_moves_products_and_joins_same_named_lines(self):
        twin = ProductSeries.objects.create(brand=self.armaf, name="KHAMRAH")
        moved = self._product("6290000075001", self.armaf, twin)
        kept_line = self._product("6290000075002", self.armaf, self.cdn)
        count = merge_brands(self.lattafa, self.armaf)
        self.assertEqual(count, 2)
        self.assertFalse(Brand.objects.filter(pk=self.armaf.pk).exists())
        self.assertFalse(ProductSeries.objects.filter(pk=twin.pk).exists())
        moved.refresh_from_db()
        self.assertEqual((moved.brand_master, moved.series_master, moved.brand, moved.model),
                         (self.lattafa, self.khamrah, "Lattafa", "Khamrah"))
        self.cdn.refresh_from_db()
        self.assertEqual(self.cdn.brand, self.lattafa)
        kept_line.refresh_from_db()
        self.assertEqual(kept_line.series_master, self.cdn)

    def test_the_merge_rewrites_the_text_on_archived_products(self):
        self._product("6290000075002", self.armaf, self.cdn)        # keeps the brand alive
        gone = self._product("6290000075001", self.armaf, self.cdn, archived_at=timezone.now())
        merge_brands(self.lattafa, self.armaf)
        self.assertEqual(self._text(gone), ("Lattafa", "Club De Nuit"))

    def test_merging_a_brand_into_itself_is_refused(self):
        with self.assertRaises(ValueError):
            merge_brands(self.lattafa, self.lattafa)
        self.assertTrue(Brand.objects.filter(pk=self.lattafa.pk).exists())

    def test_merging_lines_moves_products_and_text(self):
        product = self._product("6290000075001", self.lattafa, self.yara)
        self.assertEqual(merge_lines(self.khamrah, self.yara), 1)
        self.assertFalse(ProductSeries.objects.filter(pk=self.yara.pk).exists())
        self.assertEqual(self._text(product)[1], "Khamrah")

    def test_merging_lines_of_two_brands_is_refused(self):
        with self.assertRaises(ValueError):
            merge_lines(self.khamrah, self.cdn)

    def test_the_merge_does_not_leave_a_category_behind(self):
        self.armaf.categories.add(self.category)
        merge_brands(self.lattafa, self.armaf)
        self.assertIn(self.category, self.lattafa.categories.all())
