"""Mix, coffret and single-scent products link to the products they relate to."""
import importlib
import json
from decimal import Decimal

from django.apps import apps
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from stock.models import Brand, Category, Product, ProductContent, ProductSeries, ProductType, Purchase
from stock.models.product_types import clear_type_cache
from stock.services.product_contents import (also_in, content_label, contents_for_form, contents_title,
                                             parse_pasted, save_contents)


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


class FormTests(ContentsMixin):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from django.contrib.auth.models import User
        cls.boss = User.objects.create_superuser("boss", password="pw")

    def setUp(self):
        super().setUp()
        self.addCleanup(clear_type_cache)
        ProductType.objects.update(default_category=self.category)
        clear_type_cache()
        self.client.force_login(self.boss)

    def _add(self, kind, barcode, **fields):
        data = {"barcode": barcode, "brand_master": self.lattafa.id, "name": "New", "product_type": kind,
                "default_price": "4", "wholesale_price": "2"}
        data.update(fields)
        return self.client.post(f'{reverse("add_product")}?type={kind}', data)

    def _edit(self, product, **fields):
        data = {"barcode": product.barcode, "brand_master": self.lattafa.id, "category": self.category.id,
                "name": product.name, "product_type": product.product_type_id,
                "default_price": "4", "wholesale_price": "2"}
        data.update(fields)
        return self.client.post(reverse("edit_product", args=[product.pk]), data)

    def _rows(self, *rows):
        return json.dumps([{"name": n, "linked_id": linked} for n, linked in rows])

    def test_ticking_mix_saves_the_list(self):
        response = self._add("perfume", "6290000081001", is_mix="on",
                             contents_json=self._rows(("Khamrah", self.khamrah.id), ("Oud Novo", None)))
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(barcode="6290000081001")
        self.assertTrue(product.is_mix)
        self.assertEqual([c.name for c in product.contents.all()], ["Khamrah", "Oud Novo"])
        self.assertEqual(product.contents.first().linked, self.khamrah)

    def test_a_coffret_needs_no_tick(self):
        self._add("coffret", "6290000081002", contents_json=self._rows(("Yara", self.yara.id)))
        product = Product.objects.get(barcode="6290000081002")
        self.assertFalse(product.is_mix)
        self.assertEqual(product.contents.count(), 1)

    def test_a_single_body_spray_links_with_no_tick(self):
        self._add("body_spray", "6290000081003", contents_json=self._rows(("Yara", self.yara.id)))
        self.assertEqual(Product.objects.get(barcode="6290000081003").contents.first().linked, self.yara)

    def test_an_ordinary_perfume_has_no_list(self):
        self._add("perfume", "6290000081004", contents_json=self._rows(("Yara", None)))
        self.assertEqual(Product.objects.get(barcode="6290000081004").contents.count(), 0)

    def test_unticking_mix_keeps_the_rows_hidden(self):
        save_contents(self.mix, [{"name": "Yara", "linked_id": self.yara.id}])
        Product.objects.filter(pk=self.mix.pk).update(product_type_id="perfume")
        self.mix.refresh_from_db()
        self._edit(self.mix, contents_json=self._rows(("Yara", self.yara.id)))
        self.mix.refresh_from_db()
        self.assertFalse(self.mix.is_mix)
        self.assertEqual(self.mix.contents.count(), 1)
        self.assertEqual(also_in(self.yara), [])

    def test_the_form_comes_back_with_the_list(self):
        save_contents(self.mix, [{"name": "Yara", "linked_id": self.yara.id}])
        html = self.client.get(reverse("edit_product", args=[self.mix.pk])).content.decode()
        self.assertIn('name="contents_json"', html)
        self.assertIn(f"&quot;linked_id&quot;: {self.yara.id}", html)

    def test_an_edit_without_the_field_keeps_the_list(self):
        save_contents(self.mix, [{"name": "Yara", "linked_id": None}])
        self._edit(self.mix, is_mix="on")
        self.assertEqual(self.mix.contents.count(), 1)

    def test_broken_json_does_not_lose_the_product(self):
        response = self._add("coffret", "6290000081005", contents_json="{not json")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "could not be read")
        self.assertFalse(Product.objects.filter(barcode="6290000081005").exists())

    def test_json_of_the_wrong_shape_does_not_crash(self):
        for bad in ('{"a": 1}', '[1, 2]', '"x"', '[{"linked_id": "abc", "name": "A"}]'):
            self.assertLess(self._add("coffret", "6290000081006", contents_json=bad).status_code, 500)
            Product.objects.filter(barcode="6290000081006").delete()

    def test_autocomplete_skips_mixes_on_request(self):
        url = reverse("products_autocomplete")
        names = lambda **p: [r["id"] for r in self.client.get(url, {"q": "Mix", "scope": "stock", **p}).json()["results"]]
        self.assertIn(self.mix.id, names())
        self.assertNotIn(self.mix.id, names(exclude_mix="1"))

    def test_autocomplete_returns_the_scent_name(self):
        rows = self.client.get(reverse("products_autocomplete"), {"q": "Khamrah", "scope": "stock"}).json()["results"]
        self.assertEqual(rows[0]["scent_name"], "Khamrah")

    def test_the_paste_endpoint(self):
        response = self.client.post(reverse("parse_contents"), {"text": "Khamrah, Oud Novo", "brand": "Lattafa"})
        rows = response.json()["rows"]
        self.assertEqual([r["name"] for r in rows], ["Khamrah", "Oud Novo"])
        self.assertEqual(rows[0]["linked_id"], self.khamrah.id)

    def test_the_types_tab_saves_the_list_switch(self):
        row = ProductType.objects.get(code="mini")
        self.client.post(reverse("catalog_types"), {"action": "save", "id": row.id, "name": "Mini",
                                                    "default_category": self.category.id, "has_contents": "on"})
        row.refresh_from_db()
        self.assertTrue(row.has_contents)

    def test_the_form_rules_carry_the_list_switches(self):
        html = self.client.get(reverse("add_product"), {"type": "coffret"}).content.decode()
        self.assertIn('"contents": true', html)
        self.assertIn('"gift": true', html)


class DetailPageTests(ContentsMixin):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from django.contrib.auth.models import User
        cls.boss = User.objects.create_superuser("boss2", password="pw")

    def setUp(self):
        super().setUp()
        self.client.force_login(self.boss)

    def _page(self, product):
        return self.client.get(reverse("product_detail", args=[product.pk])).content.decode()

    def test_a_mix_page_lists_scents_and_links_the_linked_ones(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.yara.id}, {"name": "Oud Novo", "linked_id": None}])
        html = self._page(self.mix)
        section = html[html.index('class="pdp-links"'):]
        self.assertIn("Scents", section)
        self.assertIn(f'href="{reverse("product_detail", args=[self.yara.pk])}"', section)
        self.assertIn("Oud Novo", section)

    def test_a_coffret_says_contains(self):
        save_contents(self.gift, [{"name": "", "linked_id": self.yara.id}])
        self.assertIn("Contains", self._page(self.gift))

    def test_a_perfume_page_says_also_in_with_stock(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.yara.id}])
        Purchase.objects.create(product=self.mix, quantity=7, remaining=7, cost_price=Decimal("2"))
        html = self._page(self.yara)
        section = html[html.index('class="pdp-links"'):]
        self.assertIn("Also in", section)
        self.assertIn("Body spray (Mix)", section)
        self.assertIn(f'href="{reverse("product_detail", args=[self.mix.pk])}"', section)
        self.assertIn(">7<", section)

    def test_an_archived_mix_is_not_mentioned(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.yara.id}])
        Product.objects.filter(pk=self.mix.pk).update(archived_at=timezone.now())
        self.assertNotIn('class="pdp-links"', self._page(self.yara))

    def test_an_unticked_mix_shows_no_list(self):
        save_contents(self.mix, [{"name": "Oud Novo", "linked_id": None}])
        Product.objects.filter(pk=self.mix.pk).update(is_mix=False, product_type_id="perfume")
        self.assertNotIn("Oud Novo", self._page(self.mix))

    def test_a_product_with_neither_shows_no_section(self):
        self.assertNotIn('class="pdp-links"', self._page(self.yara))


class PasteChoosesTheRightProductTests(ContentsMixin):
    def setUp(self):
        super().setUp()
        self.small = self._product("6290000080011", model="Oud", name="", volume_ml=50)
        self.big = self._product("6290000080012", model="Oud", name="", volume_ml=100)
        self.mini = self._product("6290000080013", model="Oud", name="", product_type_id="mini", volume_ml=10)
        self.gift2 = self._product("6290000080014", model="Oud", name="", product_type_id="coffret")
        self.spray = self._product("6290000080015", model="Oud", name="", product_type_id="body_spray")
        self.mist = self._product("6290000080016", model="Oud", name="", product_type_id="body_mist")

    def test_the_perfume_wins_over_every_other_type(self):
        self.assertEqual(parse_pasted("Lattafa", "oud")[0]["linked_id"], self.big.id)

    def test_with_no_perfume_a_mini_is_next(self):
        Product.objects.filter(pk__in=[self.small.pk, self.big.pk]).delete()
        self.assertEqual(parse_pasted("Lattafa", "oud")[0]["linked_id"], self.mini.id)

    def test_a_product_with_its_own_list_is_never_matched(self):
        Product.objects.filter(pk__in=[self.small.pk, self.big.pk, self.mini.pk]).delete()
        row = parse_pasted("Lattafa", "oud")[0]
        self.assertIsNone(row["linked_id"])
        self.assertEqual(row["name"], "Oud")

    def test_the_row_says_which_product_it_linked(self):
        row = parse_pasted("Lattafa", "oud")[0]
        self.assertIn("100ml", row["linked_label"])

    def test_the_form_list_says_which_product_it_linked(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.big.id}])
        self.assertIn("100ml", contents_for_form(self.mix)[0]["linked_label"])
