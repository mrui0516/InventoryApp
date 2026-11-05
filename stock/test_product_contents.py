"""Mix, coffret and single-scent products link to the products they relate to."""
import importlib
from decimal import Decimal

from django.apps import apps
from django.test import TestCase
from django.utils import timezone

from stock.models import Brand, Category, Product, ProductContent, ProductSeries, Purchase
from stock.services.product_contents import (also_in, content_label, contents_title, parse_pasted, save_contents)


class ContentsMixin(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Perfumes", form_kind="perfume")
        cls.lattafa = Brand.objects.create(name="Lattafa")
        cls.mix_line = ProductSeries.objects.create(brand=cls.lattafa, name="Mix")
        cls.khamrah_line = ProductSeries.objects.create(brand=cls.lattafa, name="Khamrah")

    def setUp(self):
        self.khamrah = self._product("6290000080001", model="Khamrah", name="", series_master=self.khamrah_line)
        self.yara = self._product("6290000080002", model="Yara", name="")
        self.asad = self._product("6290000080003", model="Asad", name="Elixir")
        self.mix = self._product("6290000080004", model="Mix", name="Body Spray", series_master=self.mix_line,
                                 product_type_id="body_spray", is_mix=True)
        self.gift = self._product("6290000080005", model="Yara", name="Gift", product_type_id="coffret")

    def _product(self, barcode, **fields):
        return Product.objects.create(barcode=barcode, brand="Lattafa", brand_master=self.lattafa,
                                      category=self.category, **fields)


class ScentNameTests(ContentsMixin):
    def test_line_and_name_without_brand_or_size(self):
        self.assertEqual(self.khamrah.scent_name, "Khamrah")
        self.assertEqual(self.asad.scent_name, "Asad Elixir")

    def test_a_line_equal_to_the_name_is_said_once(self):
        product = self._product("6290000080006", model="Yara", name="yara", volume_ml=100)
        self.assertEqual(product.scent_name, "Yara")


class ListsContentsTests(ContentsMixin):
    def test_a_ticked_mix_lists(self):
        self.assertTrue(self.mix.lists_contents)

    def test_a_type_with_contents_lists_without_a_tick(self):
        self.assertTrue(self.gift.lists_contents)
        self.assertTrue(self._product("6290000080006", product_type_id="body_mist").lists_contents)

    def test_a_plain_perfume_does_not(self):
        self.assertFalse(self.khamrah.lists_contents)

    def test_the_title_follows_the_type(self):
        self.assertEqual(contents_title(self.gift), "Contains")
        self.assertEqual(contents_title(self.mix), "Scents")


class LabelTests(ContentsMixin):
    def test_a_linked_scent_follows_the_perfume_name(self):
        save_contents(self.mix, [{"name": "Old", "linked_id": self.khamrah.id}])
        Product.objects.filter(pk=self.khamrah.pk).update(model="Khamrah Qahwa")
        row = ProductContent.objects.select_related("linked").get(owner=self.mix)
        self.assertEqual(content_label(row), "Khamrah Qahwa")

    def test_saving_writes_the_linked_name_back(self):
        save_contents(self.mix, [{"name": "Old", "linked_id": self.khamrah.id}])
        self.assertEqual(ProductContent.objects.get(owner=self.mix).name, "Khamrah")

    def test_an_archived_perfume_leaves_the_stored_name(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.khamrah.id}])
        Product.objects.filter(pk=self.khamrah.pk).update(archived_at=timezone.now(), model="Gone")
        self.assertEqual(content_label(ProductContent.objects.select_related("linked").get(owner=self.mix)), "Khamrah")

    def test_a_deleted_perfume_leaves_the_stored_name(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.khamrah.id}])
        Product.all_objects.filter(pk=self.khamrah.pk).delete()
        row = ProductContent.objects.get(owner=self.mix)
        self.assertIsNone(row.linked)
        self.assertEqual(content_label(row), "Khamrah")

    def test_a_name_only_row(self):
        save_contents(self.mix, [{"name": "Oud Novo", "linked_id": None}])
        self.assertEqual(content_label(ProductContent.objects.get(owner=self.mix)), "Oud Novo")


class SaveTests(ContentsMixin):
    def _names(self):
        return [r.name for r in ProductContent.objects.filter(owner=self.mix).order_by("sort_order")]

    def test_saving_replaces_the_list_and_keeps_the_order(self):
        save_contents(self.mix, [{"name": "B", "linked_id": None}, {"name": "A", "linked_id": None}])
        self.assertEqual(self._names(), ["B", "A"])
        save_contents(self.mix, [{"name": "C", "linked_id": None}])
        self.assertEqual(self._names(), ["C"])

    def test_blank_rows_and_repeats_go(self):
        save_contents(self.mix, [{"name": " A ", "linked_id": None}, {"name": "", "linked_id": None},
                                 {"name": "a", "linked_id": None}])
        self.assertEqual(self._names(), ["A"])

    def test_a_product_cannot_link_to_itself(self):
        save_contents(self.mix, [{"name": "Self", "linked_id": self.mix.id}])
        row = ProductContent.objects.get(owner=self.mix)
        self.assertEqual((row.name, row.linked), ("Self", None))

    def test_a_product_cannot_link_to_a_mix(self):
        other = self._product("6290000080006", model="Mix", name="Air", is_mix=True)
        save_contents(self.gift, [{"name": "Mix", "linked_id": other.id}])
        self.assertIsNone(ProductContent.objects.get(owner=self.gift).linked)

    def test_a_link_to_nothing_keeps_the_name(self):
        save_contents(self.mix, [{"name": "Gone", "linked_id": 999999}])
        self.assertEqual(self._names(), ["Gone"])
        self.assertIsNone(ProductContent.objects.get(owner=self.mix).linked)


class PasteTests(ContentsMixin):
    def test_pasting_cleans_up(self):
        rows = parse_pasted("Lattafa", " Khamrah \n\nYara, Yara\nasad elixir, Oud Novo")
        self.assertEqual([r["name"] for r in rows], ["Khamrah", "Yara", "Asad Elixir", "Oud Novo"])
        self.assertEqual([bool(r["linked_id"]) for r in rows], [True, True, True, False])

    def test_another_brand_is_not_linked(self):
        self.assertIsNone(parse_pasted("Armaf", "Khamrah")[0]["linked_id"])

    def test_a_mix_is_never_a_match(self):
        self.assertIsNone(parse_pasted("Lattafa", "Mix Body Spray")[0]["linked_id"])

    def test_an_archived_product_is_never_a_match(self):
        Product.objects.filter(pk=self.khamrah.pk).update(archived_at=timezone.now())
        self.assertIsNone(parse_pasted("Lattafa", "Khamrah")[0]["linked_id"])

    def test_nothing_pasted(self):
        self.assertEqual(parse_pasted("Lattafa", " \n , "), [])


class AlsoInTests(ContentsMixin):
    def test_also_in_lists_what_holds_it(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.yara.id}])
        save_contents(self.gift, [{"name": "", "linked_id": self.yara.id}])
        self.assertEqual({p.pk for p in also_in(self.yara)}, {self.mix.pk, self.gift.pk})

    def test_also_in_skips_archived_owners(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.yara.id}])
        Product.objects.filter(pk=self.mix.pk).update(archived_at=timezone.now())
        self.assertEqual(also_in(self.yara), [])

    def test_also_in_skips_an_unticked_mix(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.yara.id}])
        Product.objects.filter(pk=self.mix.pk).update(is_mix=False, product_type_id="perfume")
        self.assertEqual(also_in(self.yara), [])

    def test_linking_moves_no_stock(self):
        batch = Purchase.objects.create(product=self.yara, quantity=5, remaining=5, cost_price=Decimal("10"))
        save_contents(self.gift, [{"name": "", "linked_id": self.yara.id}])
        batch.refresh_from_db()
        self.assertEqual(batch.remaining, 5)


class MigrationTests(ContentsMixin):
    def test_the_migration_ticks_products_on_the_mix_line(self):
        upper = self._product("6290000080006", model="MIX", name="Air")
        named = self._product("6290000080007", model="Fakhar", name="Mix Gold")
        importlib.import_module("stock.migrations.0078_product_contents").forward(apps, None)
        upper.refresh_from_db()
        named.refresh_from_db()
        self.assertTrue(upper.is_mix)
        self.assertFalse(named.is_mix)
