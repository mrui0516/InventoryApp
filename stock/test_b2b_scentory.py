"""Migration 0093: B2B is Scentory's, decants filed as Perfume become Amostra; and
the menu keeps its scroll."""
import importlib
from decimal import Decimal as D

from django.apps import apps
from django.urls import reverse

from stock.models import BusinessSettings, Product, ProductType
from stock.models.core import forget_current
from stock.models.product_types import clear_type_cache
from stock.services.wholesale_catalog import pick_mode
from stock.services.wholesale_orders import shop_phone, shop_store
from stock.test_portal import PortalMixin

forward = importlib.import_module("stock.migrations.0093_b2b_is_scentory").forward


class MigrationTests(PortalMixin):
    def run_it(self):
        forward(apps, None)
        clear_type_cache()
        forget_current()

    def test_wholesale_goes_to_scentory_when_left_on_the_default_shop(self):
        from stock.models import PrintProfile
        PrintProfile.objects.update_or_create(store=self.scentory, defaults={"phone": "(+351) 935993566"})
        BusinessSettings.objects.update(wholesale_store=None)
        self.run_it()
        self.assertEqual(shop_store(), self.scentory)
        self.assertEqual(shop_phone(), "(+351) 935993566")

    def test_a_shop_chosen_on_purpose_is_kept(self):
        other = self.khan.__class__.objects.create(name="Third", is_active=True)
        BusinessSettings.objects.update(wholesale_store=other)
        self.run_it()
        self.assertEqual(BusinessSettings.objects.get().wholesale_store, other)

    def test_decants_filed_as_perfume_become_samples(self):
        decant = self._product(name="Vidro 5ml", brand="Amostra", model="Decante", units=3,
                               wholesale_price=D("4"))
        yara = self._product(name="Yara", units=3)
        ProductType.objects.filter(code="amostra").update(picks_perfume=False)
        self.run_it()
        decant = Product.objects.get(pk=decant.pk)
        self.assertEqual(decant.product_type_id, "amostra")
        self.assertTrue(ProductType.objects.get(code="amostra").picks_perfume)
        self.assertEqual(Product.objects.get(pk=yara.pk).product_type_id, "perfume")


class SamplePickerTests(PortalMixin):
    def test_a_decant_offers_the_perfume_search_in_the_catalogue(self):
        decant = self._product(name="Vidro 5ml", brand="Amostra", model="Decante", units=3, wholesale_price=D("4"))
        forward(apps, None)
        clear_type_cache()
        decant = Product.objects.get(pk=decant.pk)
        self.assertEqual(pick_mode(decant), "perfume")
        self.client.force_login(self.boss)
        page = self.client.get(reverse("catalog"), {"q": "Vidro"})
        self.assertContains(page, "data-wc-pick-search")


class MenuScrollTests(PortalMixin):
    def test_the_menu_remembers_its_scroll(self):
        self.client.force_login(self.boss)
        self.assertContains(self.client.get(reverse("product_list")), "navScroll")
