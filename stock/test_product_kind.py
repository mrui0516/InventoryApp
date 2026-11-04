"""2026-11-04: a gift set and a perfume of the same name can be told apart:
everywhere a product is named, and when Quick import matches a line by name."""
from decimal import Decimal

from django.test import TestCase

from .models import Category, Product
from .services.quick_import import match_products


class SameNameTests(TestCase):
    def setUp(self):
        cat = Category.objects.create(name="Perfumes", form_kind="perfume")
        common = {"brand": "Lattafa", "model": "Yara", "name": "", "category": cat, "volume_ml": 100,
                  "default_price": Decimal("30")}
        self.bottle = Product.objects.create(barcode="9970000000001", **common)
        self.gift = Product.objects.create(barcode="9970000000002", perfume_type=Product.COFFRET, **common)

    def rows(self, name):
        return match_products([{"ean": "", "name": name, "qty": 1, "unit_price": None, "total": None}])

    def test_the_names_differ(self):
        self.assertEqual(self.bottle.display_name, "Lattafa - Yara 100ml")
        self.assertEqual(self.gift.display_name, "Lattafa - Yara Coffret 100ml")

    def test_a_name_that_says_it_already_is_not_repeated(self):
        self.gift.model = "Yara Coffret"
        self.assertEqual(self.gift.display_name, "Lattafa - Yara Coffret 100ml")

    def test_every_kind_but_a_plain_perfume_is_named(self):
        self.bottle.perfume_type = Product.BODY_SPRAY
        self.assertEqual(self.bottle.display_name, "Lattafa - Yara Body spray 100ml")

    def test_a_plain_line_finds_the_bottle(self):
        self.assertEqual(self.rows("Lattafa Yara 100ml")[0]["product"], self.bottle)

    def test_a_gift_line_finds_the_gift_set(self):
        for name in ("Lattafa Yara Gift Set 100ml", "Lattafa Yara coffret 100ml", "Estojo Lattafa Yara 100ml"):
            self.assertEqual(self.rows(name)[0]["product"], self.gift, name)

    def test_the_guesses_show_which_is_which(self):
        titles = [p.display_name for p in self.rows("Yara")[0]["candidates"]]
        self.assertEqual(len(set(titles)), 2)
