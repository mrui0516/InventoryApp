"""Oil perfume and body mist are types; only perfume and mini have a concentration."""
from decimal import Decimal

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
                "name": "Yara", "product_type": "perfume", "concentration": self.edp.id, "volume_ml": "100",
                "default_price": "40", "wholesale_price": "20"}
        data.update(over)
        return self.client.post(reverse("add_product"), data)

    def test_names_say_the_new_types(self):
        oil = self._product(product_type_id="oil_perfume", volume_ml=12)
        mist = self._product(product_type_id="body_mist", volume_ml=250, barcode="6290000009003")
        self.assertEqual(oil.type_label, "Oil perfume")
        self.assertEqual(mist.type_label, "Body mist")
        self.assertIn("Oil perfume", oil.list_name)
        self.assertIn("Body mist", mist.list_name)

    def test_a_perfume_and_a_mini_keep_their_concentration(self):
        for n, kind in enumerate(("perfume", "mini")):
            self._post(product_type=kind, barcode=f"629000000910{n}")
            self.assertEqual(Product.objects.get(barcode=f"629000000910{n}").concentration, self.edp)

    def test_every_other_type_drops_the_concentration(self):
        for n, kind in enumerate(("oil_perfume", "body_mist", "body_spray", "air_freshener", "coffret")):
            self._post(product_type=kind, barcode=f"629000000920{n}")
            product = Product.objects.get(barcode=f"629000000920{n}")
            self.assertEqual(product.product_type_id, kind)
            self.assertIsNone(product.concentration)

    def test_the_list_offers_a_tab_per_new_type(self):
        self._product(product_type_id="oil_perfume")
        self._product(product_type_id="body_mist", barcode="6290000009003")
        self.client.force_login(self.boss)
        html = self.client.get(reverse("product_list")).content.decode()
        self.assertIn("Oil perfume", html)
        self.assertIn("Body mist", html)
        rows = self.client.get(reverse("product_list"), {"type": "body_mist"}).context["page_obj"]
        self.assertEqual([p.product_type_id for p in rows], ["body_mist"])

    def test_a_body_mist_gets_no_wholesale_discount_at_any_size(self):
        mist = self._product(product_type_id="body_mist", volume_ml=100, wholesale_price=Decimal("9"))
        oil = self._product(product_type_id="oil_perfume", volume_ml=100, wholesale_price=Decimal("9"),
                            barcode="6290000009003")
        self.assertFalse(discount_applies(mist))
        self.assertFalse(discount_applies(oil))
