"""The customer PDF names what a mix or coffret holds, and jumps both ways."""
import io

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pypdf import PdfReader

from stock.models import Category, Product
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

    def _product(self, barcode, name, **fields):
        fields.setdefault("model", "")
        product = Product.objects.create(barcode=barcode, brand="Lattafa", name=name, category=self.category, **fields)
        return product

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
