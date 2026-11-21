"""Scent quick import, the description clean-up and the incomplete-info hint
(2026-11-21)."""
import importlib
from decimal import Decimal

from django.apps import apps
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from stock.models import Brand, Category, FragranceFamily, Product, ProductSeries, ProductType, Purchase
from stock.models.product_types import clear_type_cache
from stock.services.scent_info import incomplete_count, missing_parts
from stock.services.scent_text import read_scent_text, tidy

FAMILIES = [(1, 'Floral', 'Floral'), (2, 'Woody', 'Amadeirado'), (3, 'Sweet / Gourmand', 'Doce'),
            (4, 'Fruity', 'Frutado'), (5, 'Amber / Oriental', 'Oriental'), (6, 'Spicy', 'Especiado')]


def read(text, **kw):
    return read_scent_text(text, FAMILIES, **kw)


class NoteLineTests(SimpleTestCase):
    def test_the_usual_labels_move_and_the_prose_stays(self):
        text = ("Um perfume quente.\n\nNotas da fragrância:\nSaída: Gengibre, Canela\n\n"
                "Coração: Praliné, Flores Brancas\n\nBase: Café, Baunilha\n\nInformações do produto:\nFoi lançado em 2023.")
        r = read(text)
        self.assertEqual(r.notes, {"top": "Gengibre, Canela", "heart": "Praliné, Flores Brancas", "base": "Café, Baunilha"})
        self.assertEqual(r.prose, "Um perfume quente.\n\nInformações do produto:\nFoi lançado em 2023.")

    def test_dash_verb_and_english_labels(self):
        r = read("Notas de cabeça - Maçã, Bergamota\nnotas de coração são: Açafrão e Jasmim\nBase notes: Musk")
        self.assertEqual(r.notes, {"top": "Maçã, Bergamota", "heart": "Açafrão e Jasmim", "base": "Musk"})
        self.assertEqual(r.prose, "")

    def test_a_value_on_the_lines_beneath(self):
        r = read("Notas de cabeça (topo):\nPistache fresco\nTons cremosos suaves\n\nFim.")
        self.assertEqual(r.notes["top"], "Pistache fresco, Tons cremosos suaves")
        self.assertEqual(r.prose, "Fim.")

    def test_a_label_without_a_separator_needs_a_list(self):
        self.assertEqual(read("Notas de topo sálvia, alfazema e hortelã").notes["top"], "sálvia, alfazema e hortelã")
        r = read("Notas de base profundas e duradouras")
        self.assertEqual(r.notes, {})
        self.assertEqual(r.prose, "Notas de base profundas e duradouras")

    def test_a_sentence_after_a_label_stays(self):
        text = "Notas de Topo: A abertura é marcada pela bergamota, proporcionando frescura."
        r = read(text)
        self.assertEqual(r.notes, {})
        self.assertEqual(r.prose, text)

    def test_a_comment_after_a_dash_stays(self):
        text = "Notas de saída: Tangerina, cardamomo – uma abertura vibrante e especiada."
        self.assertEqual(read(text).prose, text)

    def test_a_heading_over_its_own_lines_is_not_a_value(self):
        text = ("🌿 Pirâmide Olfativa 🌿\n🍍 Notas de Saída – Explosão Cítrica e Frutada\n"
                "Abacaxi tropical – Doçura suculenta\nBergamota – Sofisticação e frescor")
        r = read(text)
        self.assertEqual(r.notes, {})
        self.assertEqual(r.prose, text)

    def test_emoji_do_not_reach_the_field(self):
        r = read("Notas de fundo:\n🌲 Cedro, 🧡 Âmbar Cinzento, 🌿 Almíscar")
        self.assertEqual(r.notes["base"], "Cedro, Âmbar Cinzento, Almíscar")

    def test_the_first_reading_of_a_layer_wins_and_both_lines_go(self):
        r = read("Topo: Limão\nNotas de topo: Laranja")
        self.assertEqual(r.notes["top"], "Limão")
        self.assertEqual(r.prose, "")

    def test_nothing_recognised_leaves_the_text_exactly(self):
        text = "  Linha um\n\n\n\nLinha dois  "
        self.assertEqual(read(text).prose, text.strip())


class FamilyAndReferenceTests(SimpleTestCase):
    def test_family_words_in_portuguese(self):
        self.assertEqual(read("Família Olfativa: amadeirados, das flores, de âmbar").families, [2, 1, 5])
        self.assertEqual(read("Família olfativa: Gourmand / doce").families, [3])
        self.assertEqual(read("Família: Oriental Especiado.").families, [5, 6])
        self.assertEqual(read("Família Olfativa: de frutas").prose, "")

    def test_a_word_that_names_no_family_keeps_the_line(self):
        r = read("Família: Fougère Marinho")
        self.assertEqual(r.families, [])
        self.assertEqual(r.prose, "Família: Fougère Marinho")

    def test_a_family_inside_a_sentence_only_for_the_quick_import(self):
        text = "Uma fragrância unissexo da família olfativa oriental frutada."
        self.assertEqual(read(text).families, [])
        r = read(text, phrases=True)
        self.assertEqual(r.families, [5, 4])
        self.assertEqual(r.prose, text)

    def test_a_reference_with_a_colon_or_a_house(self):
        self.assertEqual(read("Inspirado em: Baccarat Rouge 540").inspiration, "Baccarat Rouge 540")
        self.assertEqual(read("Inspirado no Sauvage da Dior").inspiration, "Sauvage da Dior")

    def test_a_metaphor_is_not_a_reference(self):
        r = read("Inspirado na natureza")
        self.assertEqual(r.inspiration, "")
        self.assertEqual(r.prose, "Inspirado na natureza")


class GenderTests(SimpleTestCase):
    def test_only_the_quick_import_reads_the_gender(self):
        self.assertEqual(read("Género: unissexo").gender, "")
        r = read("Género: unissexo", gender=True)
        self.assertEqual((r.gender, r.prose), ("unisex", ""))

    def test_a_gender_line_that_says_more_stays(self):
        text = "Género : Masculino - Família amadeirada oriental"
        r = read(text, gender=True)
        self.assertEqual(r.gender, "men")
        self.assertEqual(r.prose, text)

    def test_the_gender_in_the_prose(self):
        self.assertEqual(read("Perfume Feminino", gender=True).gender, "women")
        self.assertEqual(read("Para mulheres e homens.", gender=True).gender, "unisex")
        self.assertEqual(read("Para homens.", gender=True).gender, "men")

    def test_todos_in_a_sentence_is_not_unisex(self):
        self.assertEqual(read("Perfeito para todos os momentos. Perfume masculino.", gender=True).gender, "men")


class TidyTests(SimpleTestCase):
    def _data(self, **kw):
        data = {"name": "", "model": "", "description": "", "notes_top": "", "notes_heart": "", "notes_base": "",
                "inspired_by_text": "", "family_ids": []}
        data.update(kw)
        return data

    def test_a_name_equal_to_the_model_goes(self):
        self.assertEqual(tidy(self._data(name="Khamrah", model="khamrah "), FAMILIES)["name"], "")
        self.assertEqual(tidy(self._data(name="Qahwa", model="Khamrah"), FAMILIES)["name"], "Qahwa")

    def test_empty_fields_fill_and_filled_fields_win(self):
        out = tidy(self._data(description="Doce.\nNotas de Topo: Limão\nNotas de Base: Âmbar\nFamília: Floral\n"
                                          "Inspirado em: Xerjoff Erba Pura",
                              notes_base="Baunilha", family_ids=[2]), FAMILIES)
        self.assertEqual(out["description"], "Doce.")
        self.assertEqual(out["notes_top"], "Limão")
        self.assertEqual(out["notes_base"], "Baunilha")
        self.assertEqual(out["family_ids"], [2])
        self.assertEqual(out["inspired_by_text"], "Xerjoff Erba Pura")

    def test_nothing_to_do_changes_nothing(self):
        data = self._data(name="Qahwa", model="Khamrah", description=None)
        self.assertEqual(tidy(data, FAMILIES), data)


class ScentFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Perfumes", form_kind="perfume")
        cls.brand = Brand.objects.create(name="Lattafa")
        cls.woody = FragranceFamily.objects.create(name="Woody", shopify_tag="Amadeirado")
        cls.floral = FragranceFamily.objects.create(name="Floral", shopify_tag="Floral")
        cls.boss = User.objects.create_superuser("scentboss3", password="pw")

    def _product(self, barcode, units=0, **fields):
        product = Product.objects.create(barcode=barcode, brand="Lattafa", brand_master=self.brand,
                                         category=self.category, **fields)
        if units:
            Purchase.objects.create(product=product, quantity=units, remaining=units, cost_price=Decimal("5"))
        return product


class FormTidyTests(ScentFixture):
    def setUp(self):
        self.addCleanup(clear_type_cache)
        ProductType.objects.update(default_category=self.category)
        clear_type_cache()
        self.client.force_login(self.boss)
        self.line = ProductSeries.objects.create(brand=self.brand, name="Khamrah")

    def test_saving_moves_the_lines_and_drops_a_repeated_name(self):
        product = self._product("6290000095001", name="Khamrah", series_master=self.line, notes_heart="Tâmaras")
        response = self.client.post(reverse("edit_product", args=[product.pk]), {
            "barcode": product.barcode, "brand_master": self.brand.id, "series_master": self.line.id,
            "category": self.category.id, "name": "Khamrah", "product_type": "perfume",
            "default_price": "40", "wholesale_price": "20", "notes_heart": "Tâmaras",
            "description": "Doce e quente.\n\nNotas de topo: Canela, Noz-moscada\nNotas de coração: Praliné\n"
                           "Família olfativa: Amadeirado"})
        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.name, "")
        self.assertEqual(product.description, "Doce e quente.")
        self.assertEqual(product.notes_top, "Canela, Noz-moscada")
        self.assertEqual(product.notes_heart, "Tâmaras")
        self.assertEqual(list(product.fragrance_families.all()), [self.woody])


class QuickImportTests(ScentFixture):
    def test_the_endpoint_reads_a_paragraph(self):
        self.client.force_login(self.boss)
        response = self.client.post(reverse("parse_scent"), {"text": (
            "Uma fragrância amadeirada para homens.\nNotas de topo: Bergamota\nFamília: Floral\nInspirado em: Dior Sauvage")})
        data = response.json()
        self.assertEqual(data["notes"], {"top": "Bergamota"})
        self.assertEqual(data["families"], [self.floral.pk])
        self.assertEqual(data["inspiration"], "Dior Sauvage")
        self.assertEqual(data["gender"], "men")
        self.assertEqual(data["description"], "Uma fragrância amadeirada para homens.")

    def test_staff_without_manager_rights_cannot_use_it(self):
        clerk = User.objects.create_user("clerk3", password="pw")
        self.client.force_login(clerk)
        self.assertNotEqual(self.client.post(reverse("parse_scent"), {"text": "x"}).status_code, 200)

    def test_the_form_carries_the_box(self):
        self.client.force_login(self.boss)
        html = self.client.get(reverse("add_product"), {"type": "perfume"}).content.decode()
        self.assertIn('id="pfScentPaste"', html)
        self.assertIn(reverse("parse_scent"), html)


class MigrationTests(ScentFixture):
    def test_the_migration_tidies_the_catalogue(self):
        product = self._product("6290000095002", name="Yara", model="Yara",
                                description="Floral e doce.\nNotas de base: Almíscar\nFamília: Floral, Amadeirado")
        kept = self._product("6290000095003", name="Tous", model="Yara", description="Só prosa.")
        importlib.import_module("stock.migrations.0096_tidy_scent_text").forward(apps, None)
        product.refresh_from_db()
        kept.refresh_from_db()
        self.assertEqual((product.name, product.description, product.notes_base), ("", "Floral e doce.", "Almíscar"))
        self.assertEqual(set(product.fragrance_families.all()), {self.floral, self.woody})
        self.assertEqual((kept.name, kept.description), ("Tous", "Só prosa."))

    def test_families_already_set_are_not_touched(self):
        product = self._product("6290000095004", description="Família: Floral")
        product.fragrance_families.set([self.woody])
        importlib.import_module("stock.migrations.0096_tidy_scent_text").forward(apps, None)
        product.refresh_from_db()
        self.assertEqual(list(product.fragrance_families.all()), [self.woody])
        self.assertEqual(product.description, "")


class IncompleteInfoTests(ScentFixture):
    def _complete(self, barcode, **fields):
        product = self._product(barcode, units=3, description="Texto.", notes_top="Limão", **fields)
        product.fragrance_families.set([self.floral])
        return product

    def test_only_perfumes_in_stock_missing_something(self):
        self._complete("6290000095010")
        self._product("6290000095011", units=3, description="Texto.", notes_top="Limão")          # no family
        self._product("6290000095012", units=0)                                                   # no stock
        self._product("6290000095013", units=3, product_type_id="body_mist")                      # not a perfume
        self.assertEqual(incomplete_count(), 1)

    def test_one_layer_of_notes_is_enough(self):
        self._complete("6290000095014", notes_base="")
        self.assertEqual(incomplete_count(), 0)

    def test_a_version_reads_the_perfume(self):
        parent = self._complete("6290000095015")
        version = self._product("6290000095016", units=2, description="Versão.", volume_ml=30)
        Product.objects.filter(pk=version.pk).update(scent_source=parent)
        self.assertEqual(incomplete_count(), 0)
        Product.objects.filter(pk=version.pk).update(description="")
        version.refresh_from_db()
        self.assertEqual(incomplete_count(), 1)
        self.assertEqual(missing_parts(version), ["description"])

    def test_the_list_warns_and_filters(self):
        self._complete("6290000095017")
        gap = self._product("6290000095018", units=3, model="Gap")
        self.client.force_login(self.boss)
        html = self.client.get(reverse("product_list")).content.decode()
        self.assertIn("1 perfume in stock missing description, notes or families", html)
        html = self.client.get(reverse("product_list"), {"info": "incomplete"}).content.decode()
        self.assertIn(gap.barcode, html)
        self.assertNotIn("6290000095017", html)
        self.assertIn("Missing description, notes, families", html)
        self.assertIn("Missing scent info", html)

    def test_no_warning_when_all_is_complete_or_for_staff(self):
        self._complete("6290000095019")
        self.client.force_login(self.boss)
        self.assertNotIn("in stock missing", self.client.get(reverse("product_list")).content.decode())
        self._product("6290000095020", units=3)
        clerk = User.objects.create_user("clerk4", password="pw")
        self.client.force_login(clerk)
        self.assertNotIn("in stock missing", self.client.get(reverse("product_list")).content.decode())
