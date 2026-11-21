"""A version (body mist, mini) links to its perfume and reads the scent from it;
a mix is two or more scents, not a tick (2026-11-16)."""
import importlib
from decimal import Decimal

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from stock.models import Brand, Category, FragranceFamily, Product, ProductContent, ProductType, Purchase
from stock.models.product_types import clear_type_cache
from stock.services.product_contents import also_in, pdf_links, product_page_links, save_contents
from stock.services.wholesale_catalog import catalog_rows


class ScentMixin(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Perfumes", form_kind="perfume")
        cls.brand = Brand.objects.create(name="Lattafa")
        cls.floral = FragranceFamily.objects.create(name="Floral")
        cls.woody = FragranceFamily.objects.create(name="Amadeirado")

    def setUp(self):
        self.perfume = self._product("6290000090001", model="Esta Pura", volume_ml=100, gender="women",
                                     notes_top="Bergamota", notes_heart="Rosa", notes_base="Almíscar",
                                     inspired_by_text="Some Reference")
        self.perfume.fragrance_families.set([self.floral])
        self.mist = self._product("6290000090002", model="Esta Pura", volume_ml=250, product_type_id="body_mist")
        self.mini = self._product("6290000090003", model="Esta Pura", volume_ml=30, product_type_id="mini")

    def _product(self, barcode, **fields):
        return Product.objects.create(barcode=barcode, brand="Lattafa", brand_master=self.brand,
                                      category=self.category, **fields)

    def _link(self, version, parent):
        Product.objects.filter(pk=version.pk).update(scent_source=parent)
        version.refresh_from_db()


class SaveContentsRuleTests(ScentMixin):
    def test_one_linked_scent_becomes_the_source(self):
        save_contents(self.mist, [{"name": "", "linked_id": self.perfume.id}])
        self.mist.refresh_from_db()
        self.assertEqual(self.mist.scent_source, self.perfume)
        self.assertFalse(self.mist.contents.exists())
        self.assertFalse(self.mist.is_mix)

    def test_a_version_of_a_version_points_at_the_perfume(self):
        self._link(self.mini, self.perfume)
        save_contents(self.mist, [{"name": "", "linked_id": self.mini.id}])
        self.mist.refresh_from_db()
        self.assertEqual(self.mist.scent_source, self.perfume)

    def test_two_scents_make_a_mix_and_one_ends_it(self):
        other = self._product("6290000090004", model="Yara")
        save_contents(self.mist, [{"name": "", "linked_id": self.perfume.id}, {"name": "", "linked_id": other.id}])
        self.mist.refresh_from_db()
        self.assertTrue(self.mist.is_mix)
        self.assertIsNone(self.mist.scent_source)
        save_contents(self.mist, [{"name": "Oud Novo", "linked_id": None}])
        self.mist.refresh_from_db()
        self.assertFalse(self.mist.is_mix)

    def test_a_gift_keeps_its_rows_and_is_never_a_mix(self):
        gift = self._product("6290000090005", model="Esta Pura", product_type_id="coffret")
        save_contents(gift, [{"name": "", "linked_id": self.perfume.id}])
        save_contents(gift, [{"name": "", "linked_id": self.perfume.id}, {"name": "Creme", "linked_id": None}])
        gift.refresh_from_db()
        self.assertEqual(gift.contents.count(), 2)
        self.assertFalse(gift.is_mix)
        self.assertIsNone(gift.scent_source)

    def test_a_source_chosen_by_hand_wins_over_a_different_row(self):
        other = self._product("6290000090004", model="Yara")
        self._link(self.mist, self.perfume)
        save_contents(self.mist, [{"name": "", "linked_id": other.id}])
        self.mist.refresh_from_db()
        self.assertEqual(self.mist.scent_source, self.perfume)
        self.assertEqual(self.mist.contents.get().linked, other)

    def test_the_same_link_twice_leaves_no_row(self):
        self._link(self.mist, self.perfume)
        save_contents(self.mist, [{"name": "", "linked_id": self.perfume.id}])
        self.assertFalse(self.mist.contents.exists())


class InheritanceTests(ScentMixin):
    def test_an_empty_version_reads_the_perfume(self):
        self._link(self.mist, self.perfume)
        self.assertEqual((self.mist.scent_top, self.mist.scent_heart, self.mist.scent_base),
                         ("Bergamota", "Rosa", "Almíscar"))
        self.assertEqual(self.mist.scent_gender, "women")
        self.assertEqual(self.mist.scent_gender_label, "Women")
        self.assertEqual(self.mist.scent_families(), [self.floral])
        self.assertEqual(self.mist.inspiration_label, "Some Reference")

    def test_its_own_values_win(self):
        Product.objects.filter(pk=self.mist.pk).update(notes_top="Limão", gender="unisex", inspired_by_text="Other")
        self.mist.fragrance_families.set([self.woody])
        self._link(self.mist, self.perfume)
        self.assertEqual(self.mist.scent_top, "Limão")
        self.assertEqual(self.mist.scent_heart, "Rosa")
        self.assertEqual(self.mist.scent_gender, "unisex")
        self.assertEqual(self.mist.scent_families(), [self.woody])
        self.assertEqual(self.mist.inspiration_label, "Other")

    def test_it_is_live(self):
        self._link(self.mist, self.perfume)
        Product.objects.filter(pk=self.perfume.pk).update(notes_top="Pimenta")
        self.mist.refresh_from_db()
        self.assertEqual(self.mist.scent_top, "Pimenta")

    def test_an_archived_perfume_still_lends_its_scent(self):
        self._link(self.mist, self.perfume)
        Product.objects.filter(pk=self.perfume.pk).update(archived_at=timezone.now())
        self.mist.refresh_from_db()
        self.assertEqual(self.mist.scent_top, "Bergamota")

    def test_the_composed_description_reads_the_perfume(self):
        Product.objects.filter(pk=self.mist.pk).update(description="Leve e fresco.")
        self._link(self.mist, self.perfume)
        text = self.mist.composed_description()
        self.assertIn("Leve e fresco.", text)
        self.assertIn("Família olfativa: Floral", text)
        self.assertIn("Notas de Topo: Bergamota", text)
        self.assertIn("Inspirado em: Some Reference", text)
        self.assertNotIn("Inspirado em", self.mist.composed_description(include_inspiration=False))

    def test_without_a_source_nothing_changes(self):
        self.assertEqual(self.mist.scent_top, "")
        self.assertEqual(self.mist.scent_families(), [])
        self.assertEqual(self.mist.inspiration_label, "")


class GenderFilterTests(ScentMixin):
    def test_a_version_without_a_gender_follows_the_perfume(self):
        self._link(self.mist, self.perfume)
        found = set(Product.objects.filter(Product.gender_q("women")).values_list("pk", flat=True))
        self.assertEqual(found, {self.perfume.pk, self.mist.pk})
        self.assertFalse(Product.objects.filter(Product.gender_q("men"), pk=self.mist.pk).exists())

    def test_its_own_gender_wins_in_the_filter(self):
        Product.objects.filter(pk=self.mist.pk).update(gender="men")
        self._link(self.mist, self.perfume)
        self.assertFalse(Product.objects.filter(Product.gender_q("women"), pk=self.mist.pk).exists())

    def test_the_b2b_catalogue_row_and_filter(self):
        self._link(self.mist, self.perfume)
        rows = catalog_rows(gender="women", show_all=True)
        mist = next(row for row in rows if row["pk"] == self.mist.pk)
        self.assertEqual(mist["gender"], "Mulher")
        self.assertIn("Notas de Topo: Bergamota", mist["description"])


class LinksTests(ScentMixin):
    def test_also_in_lists_the_versions(self):
        self._link(self.mist, self.perfume)
        self._link(self.mini, self.perfume)
        self.assertEqual({p.pk for p in also_in(self.perfume)}, {self.mist.pk, self.mini.pk})

    def test_an_archived_version_is_not_listed(self):
        self._link(self.mist, self.perfume)
        Product.objects.filter(pk=self.mist.pk).update(archived_at=timezone.now())
        self.assertEqual(also_in(self.perfume), [])

    def test_the_page_names_the_kind_and_size(self):
        self._link(self.mist, self.perfume)
        links = product_page_links(self.perfume)
        self.assertEqual([a["label"] for a in links["also_in"]], ["Body mist 250ml"])
        self.assertEqual(product_page_links(self.mist)["scent_source"], self.perfume)

    def test_the_pdf_links_both_ways(self):
        self._link(self.mist, self.perfume)
        Purchase.objects.create(product=self.mist, quantity=9, remaining=9, cost_price=Decimal("2"))
        links = pdf_links([self.perfume, self.mist])
        self.assertEqual(links[self.mist.pk]["contents"], [("Esta Pura", self.perfume.pk)])
        label, target = links[self.perfume.pk]["also_in"][0]
        self.assertIn("250ml", label)
        self.assertEqual(target, self.mist.pk)

    def test_the_pdf_skips_a_version_the_customer_cannot_have(self):
        self._link(self.mist, self.perfume)
        self.assertNotIn(self.perfume.pk, pdf_links([self.perfume, self.mist]))


class PageTests(ScentMixin):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.boss = User.objects.create_superuser("scentboss", password="pw")

    def setUp(self):
        super().setUp()
        self.client.force_login(self.boss)

    def test_a_version_page_links_the_perfume_and_shows_its_notes(self):
        self._link(self.mist, self.perfume)
        html = self.client.get(reverse("product_detail", args=[self.mist.pk])).content.decode()
        links = html[html.index('class="pdp-links"'):]
        self.assertIn("Same scent as", links)
        self.assertIn(f'href="{reverse("product_detail", args=[self.perfume.pk])}"', links)
        self.assertIn("Bergamota", html)
        self.assertIn("<span>Women</span>", html)

    def test_the_perfume_page_lists_its_versions(self):
        self._link(self.mist, self.perfume)
        html = self.client.get(reverse("product_detail", args=[self.perfume.pk])).content.decode()
        self.assertIn("Body mist 250ml", html[html.index('class="pdp-links"'):])

    def test_the_product_list_gender_filter_follows_the_perfume(self):
        self._link(self.mist, self.perfume)
        Purchase.objects.create(product=self.mist, quantity=3, remaining=3, cost_price=Decimal("2"))
        html = self.client.get(reverse("product_list"), {"gender": "women"}).content.decode()
        self.assertIn(self.mist.barcode, html)


class FormTests(ScentMixin):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.boss = User.objects.create_superuser("scentform", password="pw")

    def setUp(self):
        super().setUp()
        self.addCleanup(clear_type_cache)
        ProductType.objects.update(default_category=self.category)
        clear_type_cache()
        self.client.force_login(self.boss)

    def _edit(self, product, **fields):
        data = {"barcode": product.barcode, "brand_master": self.brand.id, "category": self.category.id,
                "name": product.name, "product_type": product.product_type_id,
                "default_price": "4", "wholesale_price": "2", "contents_json": "[]"}
        data.update(fields)
        return self.client.post(reverse("edit_product", args=[product.pk]), data)

    def test_the_card_is_always_on_the_form_and_the_tick_is_gone(self):
        html = self.client.get(reverse("edit_product", args=[self.perfume.pk])).content.decode()
        self.assertIn('id="pfContents"', html)
        self.assertNotIn('id="pfContents" class="pf-card d-none"', html)
        self.assertIn('name="scent_source"', html)
        self.assertNotIn('name="is_mix"', html)

    def test_choosing_a_source(self):
        self.assertEqual(self._edit(self.mist, scent_source=self.perfume.id).status_code, 302)
        self.mist.refresh_from_db()
        self.assertEqual(self.mist.scent_source, self.perfume)

    def test_choosing_a_version_links_its_perfume(self):
        self._link(self.mini, self.perfume)
        self._edit(self.mist, scent_source=self.mini.id)
        self.mist.refresh_from_db()
        self.assertEqual(self.mist.scent_source, self.perfume)

    def test_clearing_the_source(self):
        self._link(self.mist, self.perfume)
        self._edit(self.mist, scent_source="")
        self.mist.refresh_from_db()
        self.assertIsNone(self.mist.scent_source)

    def test_not_itself(self):
        response = self._edit(self.mist, scent_source=self.mist.id)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be a version of itself")

    def test_not_one_of_its_own_versions(self):
        self._link(self.mist, self.perfume)
        response = self._edit(self.perfume, scent_source=self.mist.id)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "is already a version of this product")

    def test_a_perfume_that_becomes_a_version_hands_over_its_versions(self):
        root = self._product("6290000090006", model="Esta Pura Intense", volume_ml=100)
        self._link(self.mist, self.perfume)
        self._edit(self.perfume, scent_source=root.id)
        self.mist.refresh_from_db()
        self.assertEqual(self.mist.scent_source, root)

    def test_an_archived_source_does_not_block_a_save(self):
        self._link(self.mist, self.perfume)
        Product.objects.filter(pk=self.perfume.pk).update(archived_at=timezone.now())
        self.assertEqual(self._edit(self.mist, scent_source=self.perfume.id).status_code, 302)

    def test_after_an_error_the_card_names_the_posted_source(self):
        response = self._edit(self.mist, scent_source=self.perfume.id, barcode="", brand_master="")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        chosen = html[html.index('id="pfScentChosen"'):html.index('id="pfScentSearch"')]
        self.assertNotIn(" hidden", chosen.split(">")[0])
        self.assertIn(self.perfume.display_name, chosen)


class MigrationTests(ScentMixin):
    def _forward(self):
        importlib.import_module("stock.migrations.0095_scent_source").forward(apps, None)

    def test_one_linked_row_becomes_the_source(self):
        ProductContent.objects.create(owner=self.mist, name="Esta Pura", linked=self.perfume)
        self._forward()
        self.mist.refresh_from_db()
        self.assertEqual(self.mist.scent_source, self.perfume)
        self.assertFalse(self.mist.contents.exists())

    def test_two_rows_are_a_mix_and_a_tick_alone_is_not(self):
        other = self._product("6290000090004", model="Yara")
        ProductContent.objects.create(owner=self.mist, name="A", linked=self.perfume)
        ProductContent.objects.create(owner=self.mist, name="B", linked=other, sort_order=1)
        Product.objects.filter(pk=other.pk).update(is_mix=True)
        self._forward()
        self.mist.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(self.mist.is_mix)
        self.assertFalse(other.is_mix)

    def test_a_gift_keeps_its_row(self):
        gift = self._product("6290000090005", model="Esta Pura", product_type_id="coffret")
        ProductContent.objects.create(owner=gift, name="Esta Pura", linked=self.perfume)
        self._forward()
        gift.refresh_from_db()
        self.assertIsNone(gift.scent_source)
        self.assertEqual(gift.contents.count(), 1)

    def test_a_chain_ends_at_the_root(self):
        ProductContent.objects.create(owner=self.mist, name="Mini", linked=self.mini)
        ProductContent.objects.create(owner=self.mini, name="Esta Pura", linked=self.perfume)
        self._forward()
        self.mist.refresh_from_db()
        self.mini.refresh_from_db()
        self.assertEqual(self.mini.scent_source, self.perfume)
        self.assertEqual(self.mist.scent_source, self.perfume)

    def test_two_products_naming_each_other_do_not_loop(self):
        ProductContent.objects.create(owner=self.mist, name="Mini", linked=self.mini)
        ProductContent.objects.create(owner=self.mini, name="Mist", linked=self.mist)
        self._forward()
        self.mist.refresh_from_db()
        self.mini.refresh_from_db()
        self.assertFalse(self.mist.scent_source_id and self.mini.scent_source_id)
