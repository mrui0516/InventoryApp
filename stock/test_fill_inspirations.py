"""Inspired by: filled from the researched list, shown under the description
(2026-11-21)."""
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from stock.models import Category, Product


class FillInspirationsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Perfumes", form_kind="perfume")

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.list = Path(folder.name) / "inspirations.csv"
        self.list.write_text(
            "brand,line,name,inspired_by\n"
            "Lattafa,Asad,,Dior Sauvage Elixir\n"
            "lattafa,KHAMRAH,,Kilian Angels' Share\n"
            "Armaf,Club De Nuit,Intense Man,Creed Aventus\n"
            "Ghost,Brand,,Nothing\n", encoding="utf-8")

    def _product(self, barcode, model, name="", brand="Lattafa", **fields):
        return Product.objects.create(barcode=barcode, brand=brand, model=model, name=name,
                                      category=self.category, **fields)

    def _run(self, *args):
        out = StringIO()
        call_command("fill_inspirations", "--file", str(self.list), *args, stdout=out)
        return out.getvalue()

    def test_a_dry_run_writes_nothing(self):
        asad = self._product("6290000096001", "Asad")
        out = self._run()
        asad.refresh_from_db()
        self.assertEqual(asad.inspired_by_text, "")
        self.assertIn("Fill 1 product(s): 1 from the list", out)
        self.assertIn("Dry run", out)

    def test_every_size_of_the_scent_is_filled_case_blind(self):
        small = self._product("6290000096002", "Khamrah", volume_ml=30)
        big = self._product("6290000096003", "Khamrah", volume_ml=100)
        club = self._product("6290000096004", "Club de Nuit", "Intense Man", brand="Armaf")
        self._run("--apply")
        for product in (small, big, club):
            product.refresh_from_db()
        self.assertEqual({small.inspired_by_text, big.inspired_by_text}, {"Kilian Angels' Share"})
        self.assertEqual(club.inspired_by_text, "Creed Aventus")

    def test_a_filled_field_is_never_touched_and_lends_its_value_to_other_sizes(self):
        mine = self._product("6290000096005", "Asad", volume_ml=100, inspired_by_text="Typed By Hand")
        other = self._product("6290000096006", "Asad", volume_ml=30)
        out = self._run("--apply")
        mine.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(mine.inspired_by_text, "Typed By Hand")
        self.assertEqual(other.inspired_by_text, "Typed By Hand")
        self.assertIn("1 from another size", out)

    def test_versions_archived_and_other_types_are_left_alone(self):
        parent = self._product("6290000096007", "Asad")
        version = self._product("6290000096008", "Asad", volume_ml=30)
        Product.objects.filter(pk=version.pk).update(scent_source=parent)
        gone = self._product("6290000096009", "Khamrah", archived_at=timezone.now())
        mist = self._product("6290000096010", "Khamrah", product_type_id="body_mist")
        self._run("--apply")
        for product in (version, gone, mist):
            product.refresh_from_db()
            self.assertEqual(product.inspired_by_text, "")

    def test_the_report_lists_what_is_left(self):
        self._product("6290000096011", "Yara")
        out = self._run()
        self.assertIn("Still empty, not on the list: 1", out)
        self.assertIn("ghost | brand |", out)

    def test_a_list_without_the_columns_stops(self):
        self.list.write_text("brand,model\nLattafa,Asad\n", encoding="utf-8")
        with self.assertRaises(CommandError):
            self._run()


class ShownInTheDescriptionTests(TestCase):
    def test_the_product_page_says_it_under_the_description(self):
        category = Category.objects.create(name="Perfumes", form_kind="perfume")
        product = Product.objects.create(barcode="6290000096020", brand="Lattafa", model="Asad", category=category,
                                         description="Especiado.", inspired_by_text="Dior Sauvage Elixir")
        clerk = User.objects.create_user("clerk5", password="pw")
        self.client.force_login(clerk)
        html = self.client.get(reverse("product_detail", args=[product.pk])).content.decode()
        self.assertIn('<div class="pdp-insp">Inspirado em: Dior Sauvage Elixir</div>', html)
        self.assertNotIn("<dt>Inspired by</dt>", html)
