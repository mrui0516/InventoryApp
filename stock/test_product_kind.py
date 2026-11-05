"""2026-11-04: a gift set and a perfume of the same name can be told apart:
everywhere a product is named, and when Quick import matches a line by name."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .models import Category, Product
from .services.quick_import import match_products


class SameNameTests(TestCase):
    def setUp(self):
        cat = Category.objects.create(name="Perfumes", form_kind="perfume")
        common = {"brand": "Lattafa", "model": "Yara", "name": "", "category": cat, "volume_ml": 100,
                  "default_price": Decimal("30")}
        self.bottle = Product.objects.create(barcode="9970000000001", **common)
        self.gift = Product.objects.create(barcode="9970000000002", product_type_id=Product.COFFRET, **common)

    def rows(self, name):
        return match_products([{"ean": "", "name": name, "qty": 1, "unit_price": None, "total": None}])

    def test_the_names_differ(self):
        self.assertEqual(self.bottle.display_name, "Lattafa - Yara 100ml")
        self.assertEqual(self.gift.display_name, "Lattafa - Coffret Yara 100ml")

    def test_a_name_that_says_it_already_is_not_repeated(self):
        self.gift.model = "Yara Coffret"
        self.assertEqual(self.gift.display_name, "Lattafa - Yara Coffret 100ml")

    def test_every_kind_but_a_plain_perfume_is_named(self):
        self.bottle.product_type_id = Product.BODY_SPRAY
        self.assertEqual(self.bottle.display_name, "Lattafa - Body spray Yara 100ml")

    def test_a_plain_line_finds_the_bottle(self):
        self.assertEqual(self.rows("Lattafa Yara 100ml")[0]["product"], self.bottle)

    def test_a_gift_line_finds_the_gift_set(self):
        for name in ("Lattafa Yara Gift Set 100ml", "Lattafa Yara coffret 100ml", "Estojo Lattafa Yara 100ml"):
            self.assertEqual(self.rows(name)[0]["product"], self.gift, name)

    def test_the_guesses_show_which_is_which(self):
        titles = [p.display_name for p in self.rows("Yara")[0]["candidates"]]
        self.assertEqual(len(set(titles)), 2)


class TheTypeLeadsTheNameTests(TestCase):
    def setUp(self):
        cat = Category.objects.create(name="Perfumes", form_kind="perfume")
        self.common = {"brand": "Lattafa", "category": cat}

    def _make(self, barcode="9970000000011", **fields):
        return Product.objects.create(barcode=barcode, **self.common, **fields)

    def test_every_name_starts_with_the_type(self):
        spray = self._make(model="Yara", name="", product_type_id="body_spray", volume_ml=200)
        self.assertEqual(spray.display_name, "Lattafa - Body spray Yara 200ml")
        self.assertEqual(spray.list_name, "Body spray Yara · 200ml")
        self.assertEqual(spray.title_parts(include_brand=False), "Body spray Yara 200ml")
        self.assertEqual(spray.title_parts(), "Lattafa Body spray Yara 200ml")

    def test_a_perfume_stays_as_it_was(self):
        bottle = self._make(model="Yara", name="", volume_ml=100)
        self.assertEqual(bottle.display_name, "Lattafa - Yara 100ml")
        self.assertEqual(bottle.title_parts(include_brand=False), "Yara 100ml")

    def test_a_product_with_no_name_is_called_by_its_type(self):
        blank = self._make(model="", name="", product_type_id="air_freshener")
        self.assertEqual(blank.display_name, "Lattafa - Air freshener")
        self.assertEqual(blank.title_parts(include_brand=False), "Air freshener")
        self.assertEqual(blank.list_name, "Air freshener")

    def test_a_name_that_already_says_it_is_not_repeated(self):
        mix = self._make(model="Mix", name="Body Spray", product_type_id="body_spray")
        self.assertEqual(mix.display_name, "Lattafa - Mix - Body Spray")
        self.assertEqual(mix.title_parts(include_brand=False), "Mix Body Spray")
        self.assertEqual(mix.list_name, "Mix Body Spray")

    def test_a_new_type_works_the_same(self):
        from stock.models import ProductType
        from stock.models.product_types import clear_type_cache
        self.addCleanup(clear_type_cache)
        ProductType.objects.create(code="creme", name="Creme", name_word="Creme")
        cream = self._make(model="Yara", name="", product_type_id="creme")
        self.assertEqual(cream.title_parts(include_brand=False), "Creme Yara")


class SearchByTypeTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        cat = Category.objects.create(name="Perfumes", form_kind="perfume")
        self.client.force_login(User.objects.create_superuser("boss", password="pw"))
        self.spray = Product.objects.create(barcode="9970000000021", brand="Lattafa", model="Yara", name="",
                                            category=cat, product_type_id="body_spray")
        self.bottle = Product.objects.create(barcode="9970000000022", brand="Lattafa", model="Yara", name="", category=cat)

    def _list(self, q):
        return [p.pk for p in self.client.get(reverse("product_list"), {"q": q}).context["page_obj"]]

    def _auto(self, q):
        rows = self.client.get(reverse("products_autocomplete"), {"q": q, "scope": "stock"}).json()["results"]
        return [r["id"] for r in rows]

    def test_the_type_finds_the_product(self):
        self.assertEqual(self._list("body spray"), [self.spray.pk])
        self.assertEqual(self._auto("body spray"), [self.spray.pk])

    def test_words_can_come_from_different_fields(self):
        self.assertEqual(self._list("yara body spray"), [self.spray.pk])
        self.assertEqual(self._auto("lattafa spray yara"), [self.spray.pk])

    def test_a_plain_search_still_finds_both(self):
        self.assertEqual(set(self._list("yara")), {self.spray.pk, self.bottle.pk})

    def test_quick_import_reads_the_type_too(self):
        rows = match_products([{"ean": "", "name": "Lattafa Yara Body Spray", "qty": 1,
                                "unit_price": None, "total": None}])
        self.assertEqual(rows[0]["candidates"][0], self.spray)
