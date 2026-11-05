"""Product types are rows the shop edits, and the rules are switches on them."""
from django.test import TestCase

from stock.models import Category, Product, ProductType
from stock.models.product_types import clear_type_cache


def make_product(**fields):
    category, _ = Category.objects.get_or_create(name="Perfumes", defaults={"form_kind": "perfume"})
    fields.setdefault("barcode", "6290000077001")
    fields.setdefault("name", "Yara")
    fields.setdefault("brand", "Lattafa")
    return Product.objects.create(category=category, **fields)


class ProductTypeTableTests(TestCase):
    def test_the_seven_types_are_seeded_in_order(self):
        self.assertEqual([t.code for t in ProductType.objects.all()],
                         ["perfume", "mini", "oil_perfume", "body_mist", "body_spray", "air_freshener", "coffret"])

    def test_the_seeded_switches(self):
        rows = {t.code: t for t in ProductType.objects.all()}
        self.assertEqual({c for c, t in rows.items() if t.takes_concentration}, {"perfume", "mini"})
        self.assertEqual({c for c, t in rows.items() if t.wholesale_discount}, {"perfume"})
        self.assertEqual({c for c, t in rows.items() if t.is_gift}, {"coffret"})
        no_scent = {"body_mist", "body_spray", "air_freshener", "coffret"}
        self.assertEqual({c for c, t in rows.items() if t.has_contents}, no_scent)
        self.assertEqual({c for c, t in rows.items() if not t.shows_scent_details}, no_scent)
        self.assertTrue(all(t.takes_volume for t in rows.values()))
        self.assertEqual(rows["perfume"].name_word, "")
        self.assertEqual(rows["body_mist"].name_word, "Body mist")
        self.assertEqual(rows["air_freshener"].customer_label, "Ambientador")

    def test_names_read_the_table(self):
        self.addCleanup(clear_type_cache)
        product = make_product(product_type_id="coffret", name="Yara")
        self.assertEqual(product.type_label, "Coffret")
        self.assertIn("Coffret", product.display_name)
        ProductType.objects.filter(code="coffret").update(name_word="Gift set")
        clear_type_cache()
        product.refresh_from_db()
        self.assertEqual(product.type_label, "Gift set")
        self.assertIn("Gift set", product.display_name)

    def test_a_perfume_still_says_its_concentration(self):
        from stock.models import Concentration
        product = make_product(concentration=Concentration.objects.create(name="Eau de Parfum", short="EDP"))
        self.assertEqual(product.type_label, "EDP")

    def test_a_page_of_names_costs_one_query(self):
        self.addCleanup(clear_type_cache)
        for n in range(5):
            make_product(barcode=f"629000000770{n}", product_type_id="body_spray")
        clear_type_cache()
        products = list(Product.objects.all())
        with self.assertNumQueries(1):
            [p.display_name for p in products]

    def test_saving_a_type_refreshes_the_names_at_once(self):
        self.addCleanup(clear_type_cache)
        row = ProductType.objects.get(code="mini")
        ProductType.for_code("mini")
        row.name_word = "Miniature"
        row.save()
        self.assertEqual(ProductType.for_code("mini").name_word, "Miniature")

    def test_all_cached_keeps_the_order(self):
        self.addCleanup(clear_type_cache)
        clear_type_cache()
        self.assertEqual([t.code for t in ProductType.all_cached()][:3], ["perfume", "mini", "oil_perfume"])

    def test_the_form_and_list_filter_use_the_code(self):
        from django.contrib.auth.models import User
        from django.urls import reverse
        make_product(product_type_id="body_mist", barcode="6290000077002")
        make_product(barcode="6290000077003")
        self.client.force_login(User.objects.create_superuser("boss", password="pw"))
        rows = self.client.get(reverse("product_list"), {"type": "body_mist"}).context["page_obj"]
        self.assertEqual([p.product_type_id for p in rows], ["body_mist"])


class DiscountSwitchTests(TestCase):
    def test_only_perfume(self):
        from stock.services.wholesale_discount import discount_applies
        cases = (("perfume", True), ("coffret", False), ("mini", False), ("oil_perfume", False),
                 ("body_mist", False), ("body_spray", False), ("air_freshener", False))
        for n, (code, expected) in enumerate(cases):
            product = make_product(barcode=f"629000007000{n}", product_type_id=code, volume_ml=100)
            self.assertEqual(discount_applies(product), expected, code)

    def test_a_perfume_still_needs_the_size(self):
        from stock.services.wholesale_discount import discount_applies
        for volume, expected in ((50, False), (60, True), (120, True), (150, False), (None, False)):
            product = make_product(barcode=f"6290000071{volume or 0:03d}", volume_ml=volume)
            self.assertEqual(discount_applies(product), expected, volume)

    def test_the_switch_decides_for_a_new_type(self):
        from stock.services.wholesale_discount import discount_applies
        self.addCleanup(clear_type_cache)
        ProductType.objects.create(code="creme", name="Creme", wholesale_discount=True)
        self.assertTrue(discount_applies(make_product(product_type_id="creme", volume_ml=100)))
