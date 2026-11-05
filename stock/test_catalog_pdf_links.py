"""The customer PDF names what a mix or coffret holds, and jumps both ways."""
import io
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader

from stock.models import Category, InboundOrder, InboundPendingItem, Product, Purchase, Supplier
from stock.services.catalog_pdf import build_catalog_pdf
from stock.services.product_contents import pdf_links, save_contents


class PdfLinkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Perfumes", form_kind="perfume")

    def setUp(self):
        self.yara = self._product("6290000082001", "Yara")
        self.asad = self._product("6290000082002", "Asad")
        self.mix = self._product("6290000082003", "Body Spray", model="Mix", product_type_id="body_spray", is_mix=True)
        self.gift = self._product("6290000082004", "Yara Gift", product_type_id="coffret")
        self.air = self._product("6290000082005", "Air", model="Mix", product_type_id="air_freshener", is_mix=True)
        for owner in (self.mix, self.gift, self.air):
            self._stock(owner, 5)

    def _product(self, barcode, name, **fields):
        fields.setdefault("model", "")
        product = Product.objects.create(barcode=barcode, brand="Lattafa", name=name, category=self.category, **fields)
        return product

    def _stock(self, product, units):
        Purchase.objects.create(product=product, quantity=units, remaining=units, cost_price=Decimal("2"))

    def _reader(self, *products):
        for product in products:
            product.export_title = product.name
            product.export_model = product.model or "Other"
            product.export_availability = "Em stock"
            product.export_state = ""
        data = build_catalog_pdf({"Lattafa": list(products)}, include_images=False)
        return PdfReader(io.BytesIO(data.getvalue()))

    def _text(self, *products):
        return " ".join(page.extract_text() for page in self._reader(*products).pages)

    def _links(self, reader):
        found = 0
        for page in reader.pages:
            for note in page.get("/Annots") or []:
                found += note.get_object().get("/Subtype") == "/Link"
        return found

    def test_a_perfume_and_its_mix_link_both_ways(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.yara.id}])
        self.assertEqual(self._links(self._reader(self.yara, self.mix)), 2)

    def test_the_words_are_portuguese(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.yara.id}])
        save_contents(self.gift, [{"name": "", "linked_id": self.asad.id}])
        text = self._text(self.yara, self.asad, self.mix, self.gift)
        self.assertIn("Também em:", text)
        self.assertIn("Fragrâncias:", text)
        self.assertIn("Contém:", text)

    def test_the_customer_label_names_the_type(self):
        save_contents(self.air, [{"name": "", "linked_id": self.yara.id}])
        self.assertIn("Ambientador", self._text(self.yara, self.air))

    def test_a_single_body_spray_says_its_scent(self):
        single = self._product("6290000082006", "Spray", product_type_id="body_spray")
        self._stock(single, 5)
        save_contents(single, [{"name": "", "linked_id": self.yara.id}])
        self.assertIn("Fragrâncias:", self._text(self.yara, single))

    def test_a_target_outside_the_pdf_stays_plain_text(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.yara.id}, {"name": "Oud", "linked_id": None}])
        reader = self._reader(self.mix)
        self.assertEqual(self._links(reader), 0)
        self.assertIn("Yara", reader.pages[0].extract_text())

    def test_the_other_way_outside_the_pdf_stays_plain_text(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.yara.id}])
        reader = self._reader(self.yara)
        self.assertEqual(self._links(reader), 0)
        self.assertIn("Body spray", reader.pages[0].extract_text())

    def test_an_archived_perfume_stays_plain_text(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.yara.id}])
        Product.objects.filter(pk=self.yara.pk).update(archived_at=timezone.now())
        self.yara.refresh_from_db()
        reader = self._reader(self.mix, self.asad)
        self.assertEqual(self._links(reader), 0)
        self.assertIn("Yara", reader.pages[0].extract_text())

    def test_a_name_with_an_ampersand_prints(self):
        save_contents(self.mix, [{"name": "Oud & Rose <b>", "linked_id": None}])
        self.assertIn("Oud & Rose", self._text(self.mix))

    def test_a_long_list_wraps_and_builds(self):
        save_contents(self.mix, [{"name": f"Fragrance Number {n}", "linked_id": None} for n in range(40)])
        text = self._text(self.mix)
        self.assertIn("Fragrance Number 0", text)
        self.assertIn("Number 39", text)

    def test_an_empty_list_adds_no_line(self):
        self.assertNotIn("Fragrâncias", self._text(self.mix))

    def test_an_unticked_mix_shows_nothing(self):
        save_contents(self.mix, [{"name": "Oud", "linked_id": self.yara.id}])
        Product.objects.filter(pk=self.mix.pk).update(is_mix=False, product_type_id="perfume")
        self.mix.refresh_from_db()
        text = self._text(self.yara, self.mix)
        self.assertNotIn("Fragrâncias", text)
        self.assertNotIn("Também em", text)

    def test_links_cost_a_fixed_number_of_queries(self):
        save_contents(self.mix, [{"name": "", "linked_id": self.yara.id}])
        products = [self.yara, self.mix]
        with CaptureQueriesContext(connection) as few:
            pdf_links(products)
        save_contents(self.air, [{"name": "", "linked_id": self.asad.id}])
        save_contents(self.gift, [{"name": "", "linked_id": self.asad.id}])
        with CaptureQueriesContext(connection) as many:
            pdf_links(products + [self.asad, self.air, self.gift])
        self.assertEqual(len(few), len(many))

    def test_an_item_with_no_stock_is_not_offered(self):
        empty = self._product("6290000082007", "Spray", product_type_id="body_spray")
        save_contents(empty, [{"name": "", "linked_id": self.yara.id}])
        text = self._text(self.yara, empty)
        self.assertNotIn("Também em", text)

    def test_the_last_unit_is_the_sample_so_it_counts_as_none(self):
        last = self._product("6290000082007", "Spray", product_type_id="body_spray")
        self._stock(last, 1)
        save_contents(last, [{"name": "", "linked_id": self.yara.id}])
        self.assertNotIn("Também em", self._text(self.yara, last))

    def test_one_in_stock_is_kept_when_the_other_is_gone(self):
        empty = self._product("6290000082007", "Air", product_type_id="air_freshener")
        save_contents(empty, [{"name": "", "linked_id": self.yara.id}])
        save_contents(self.mix, [{"name": "", "linked_id": self.yara.id}])
        text = self._text(self.yara, self.mix)
        self.assertIn("Também em", text)
        self.assertNotIn("Ambientador", text)

    def test_an_item_on_order_is_still_offered(self):
        coming = self._product("6290000082007", "Spray", product_type_id="body_spray")
        order = InboundOrder.objects.create(supplier=Supplier.objects.create(name="S"), status="pending_receipt")
        InboundPendingItem.objects.create(inbound_order=order, product=coming, quantity=3, cost_price=Decimal("2"))
        save_contents(coming, [{"name": "", "linked_id": self.yara.id}])
        self.assertIn("Também em", self._text(self.yara, coming))

    def test_the_owner_keeps_its_own_list_when_it_has_no_stock(self):
        empty = self._product("6290000082007", "Spray", product_type_id="body_spray")
        save_contents(empty, [{"name": "", "linked_id": self.yara.id}])
        self.assertIn("Fragrâncias", self._text(self.yara, empty))


class PdfLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Perfumes", form_kind="perfume")

    def _product(self, barcode, name, brand="Lattafa", **fields):
        fields.setdefault("model", "Line")
        product = Product.objects.create(barcode=barcode, brand=brand, name=name, category=self.category, **fields)
        product.export_title = product.title_parts(include_brand=False)
        product.export_model = product.model or "Other"
        product.export_availability = "Em stock"
        product.export_state = ""
        return product

    def _text(self, groups, **options):
        data = build_catalog_pdf(groups, include_images=False, **options)
        return " ".join(page.extract_text() for page in PdfReader(io.BytesIO(data.getvalue())).pages)

    def test_the_ean_sits_above_the_name_and_has_no_column(self):
        text = self._text({"Lattafa": [self._product("6290000083001", "Yara")]})
        self.assertLess(text.index("6290000083001"), text.index("Yara"))
        self.assertNotIn("EAN", text)

    def test_the_gender_has_a_column_in_portuguese(self):
        rows = [self._product("6290000083001", "A", gender="men"), self._product("6290000083002", "B", gender="women"),
                self._product("6290000083003", "C", gender="unisex"), self._product("6290000083004", "D")]
        text = self._text({"Lattafa": rows})
        for word in ("Género", "Homem", "Mulher", "Unissexo"):
            self.assertIn(word, text)

    def test_lattafa_comes_first_then_the_biggest_brand(self):
        groups = {"Armaf": [self._product("6290000083001", "A", "Armaf")],
                  "Rasasi": [self._product(f"629000008301{n}", f"R{n}", "Rasasi") for n in range(3)],
                  "Lattafa": [self._product("6290000083002", "L")],
                  "Afnan": [self._product(f"629000008302{n}", f"F{n}", "Afnan") for n in range(2)]}
        text = self._text(groups)
        places = [text.index(f"{name} | Lista") for name in ("Lattafa", "Rasasi", "Afnan", "Armaf")]
        self.assertEqual(places, sorted(places))

    def test_inside_a_brand_the_types_come_first_then_the_lines(self):
        rows = [self._product("6290000083001", "S1", model="Zeta", product_type_id="body_spray"),
                self._product("6290000083002", "P1", model="Zeta"),
                self._product("6290000083003", "S2", model="Alfa", product_type_id="body_spray"),
                self._product("6290000083004", "P2", model="Alfa"),
                self._product("6290000083005", "G1", model="Zeta", product_type_id="coffret")]
        text = self._text({"Lattafa": rows})
        places = [text.index(word) for word in ("P1", "P2", "S1", "S2", "G1")]
        self.assertEqual(places, sorted(places))
        self.assertIn("Perfume", text)
        self.assertIn("Body spray", text)
        self.assertIn("Coffret", text)

    def test_a_blank_name_prints_its_type(self):
        text = self._text({"Lattafa": [self._product("6290000083001", "", model="", product_type_id="air_freshener")]})
        self.assertGreaterEqual(text.count("Air freshener"), 1)


class ExportFileNameTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        category = Category.objects.create(name="Perfumes", form_kind="perfume")
        Product.objects.create(barcode="6290000084001", brand="Lattafa", name="Yara", category=category,
                               volume_ml=100, wholesale_price=Decimal("20"), default_price=Decimal("30"))
        self.client.force_login(User.objects.create_superuser("boss", password="pw"))

    def _name(self, **params):
        response = self.client.get(reverse("export_product_list_excel"), params)
        return response["Content-Disposition"]

    def test_a_list_without_a_discount_keeps_its_name(self):
        self.assertIn("Product_List_2", self._name(format="pdf"))
        self.assertIn("Customer_Product_List_2", self._name())

    def test_a_discounted_pdf_carries_a_code_not_the_word(self):
        name = self._name(format="pdf", discount="2,50", price_mode="wholesale")
        self.assertIn("Product_List_D25_", name)
        self.assertNotIn("iscount", name)

    def test_a_discounted_spreadsheet_too(self):
        self.assertIn("Customer_Product_List_D30_", self._name(discount="3", price_mode="wholesale"))

    def test_the_codes_do_not_collide(self):
        from stock.views import discount_code
        self.assertEqual([discount_code(Decimal(v)) for v in ("1.5", "15", "2.5", "3", "0.5", "0")],
                         ["D15_", "D150_", "D25_", "D30_", "D5_", ""])

    def test_a_retail_list_ignores_the_discount(self):
        self.assertIn("Product_List_2", self._name(format="pdf", discount="2", price_mode="retail"))

