"""Oil perfume and body mist are types; only perfume and mini have a concentration."""
import importlib
from decimal import Decimal

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from stock.models import Brand, Category, Concentration, Product
from stock.views import discount_applies


class ProductTypesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Perfumes", form_kind="perfume")
        cls.brand = Brand.objects.create(name="Lattafa")
        cls.edp = Concentration.objects.create(name="Eau de Parfum", short="EDP")
        cls.boss = User.objects.create_superuser("boss", password="pw")

    def _product(self, **kw):
        kw.setdefault("name", "Yara")
        kw.setdefault("barcode", "6290000009001")
        return Product.objects.create(brand="Lattafa", brand_master=self.brand, category=self.category, **kw)

    def _post(self, **over):
        self.client.force_login(self.boss)
        data = {"barcode": "6290000009002", "brand_master": self.brand.id, "category": self.category.id,
                "name": "Yara", "perfume_type": "perfume", "concentration": self.edp.id, "volume_ml": "100",
                "default_price": "40", "wholesale_price": "20"}
        data.update(over)
        return self.client.post(reverse("add_product"), data)

    def test_names_say_the_new_types(self):
        oil = self._product(perfume_type="oil_perfume", volume_ml=12)
        mist = self._product(perfume_type="body_mist", volume_ml=250, barcode="6290000009003")
        self.assertEqual(oil.type_label, "Oil perfume")
        self.assertEqual(mist.type_label, "Body mist")
        self.assertIn("Oil perfume", oil.list_name)
        self.assertIn("Body mist", mist.list_name)

    def test_a_perfume_and_a_mini_keep_their_concentration(self):
        for n, kind in enumerate(("perfume", "mini")):
            self._post(perfume_type=kind, barcode=f"629000000910{n}")
            self.assertEqual(Product.objects.get(barcode=f"629000000910{n}").concentration, self.edp)

    def test_every_other_type_drops_the_concentration(self):
        for n, kind in enumerate(("oil_perfume", "body_mist", "body_spray", "air_freshener", "coffret")):
            self._post(perfume_type=kind, barcode=f"629000000920{n}")
            product = Product.objects.get(barcode=f"629000000920{n}")
            self.assertEqual(product.perfume_type, kind)
            self.assertIsNone(product.concentration)

    def test_the_list_offers_a_tab_per_new_type(self):
        self._product(perfume_type="oil_perfume")
        self._product(perfume_type="body_mist", barcode="6290000009003")
        self.client.force_login(self.boss)
        html = self.client.get(reverse("product_list")).content.decode()
        self.assertIn("Oil perfume", html)
        self.assertIn("Body mist / splash", html)
        rows = self.client.get(reverse("product_list"), {"type": "body_mist"}).context["page_obj"]
        self.assertEqual([p.perfume_type for p in rows], ["body_mist"])

    def test_a_body_mist_gets_no_wholesale_discount_at_any_size(self):
        mist = self._product(perfume_type="body_mist", volume_ml=100, wholesale_price=Decimal("9"))
        oil = self._product(perfume_type="oil_perfume", volume_ml=100, wholesale_price=Decimal("9"),
                            barcode="6290000009003")
        self.assertFalse(discount_applies(mist))
        self.assertTrue(discount_applies(oil))

    def test_the_migration_moves_the_two_old_concentrations(self):
        attar = Concentration.objects.create(name="Attar / Oil", short="Attar")
        mist = Concentration.objects.create(name="Body Mist", short="Mist")
        oil = self._product(concentration=attar)
        body = self._product(concentration=mist, barcode="6290000009003", perfume_type="mini")
        gift = self._product(concentration=mist, barcode="6290000009004", perfume_type="coffret")
        spray = self._product(concentration=self.edp, barcode="6290000009005", perfume_type="body_spray")
        kept = self._product(concentration=self.edp, barcode="6290000009006")
        migration = importlib.import_module("stock.migrations.0074_oil_perfume_body_mist_types")
        migration.forward(apps, None)
        for product in (oil, body, gift, spray, kept):
            product.refresh_from_db()
        self.assertEqual((oil.perfume_type, oil.concentration), ("oil_perfume", None))
        self.assertEqual((body.perfume_type, body.concentration), ("body_mist", None))
        self.assertEqual((gift.perfume_type, gift.concentration), ("coffret", None))
        self.assertEqual((spray.perfume_type, spray.concentration), ("body_spray", None))
        self.assertEqual((kept.perfume_type, kept.concentration), ("perfume", self.edp))
        self.assertEqual(list(Concentration.objects.values_list("short", flat=True)), ["EDP"])

        migration.backward(apps, None)
        oil.refresh_from_db()
        self.assertEqual((oil.perfume_type, oil.concentration.short), ("perfume", "Attar"))
