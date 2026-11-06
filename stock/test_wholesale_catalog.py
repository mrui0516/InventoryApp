"""The wholesale catalogue: what a customer may see, as an internal sample for now."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from stock.models import Category, Product, Purchase, Sale, SaleOrder, Store
from stock.services.product_contents import save_contents
from stock.services.wholesale_catalog import availability, catalog_rows, sellable_units


class CatalogMixin(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Perfumes", form_kind="perfume")
        cls.boss = User.objects.create_superuser("boss", password="pw")

    def setUp(self):
        Store.objects.update(is_default=False, is_active=False)
        self.khan = Store.objects.create(name="Khan", code="KP", is_default=True, keeps_samples=True)
        self.scentory = Store.objects.create(name="Scentory", code="SC", keeps_samples=True)
        self.next = 0
        self.now = timezone.now()

    def _product(self, name="Yara", brand="Lattafa", units=0, days_ago=200, **fields):
        self.next += 1
        fields.setdefault("model", "Line")
        fields.setdefault("wholesale_price", Decimal("20"))
        fields.setdefault("price_locked", True)           # a batch would reprice it
        product = Product.objects.create(barcode=f"62900000{self.next:05d}", brand=brand, name=name,
                                         category=self.category, **fields)
        self._batch(product, units or 1, days_ago, remaining=units)
        return product

    def _batch(self, product, units, days_ago, remaining=None, store=None):
        batch = Purchase.objects.create(product=product, quantity=units, cost_price=Decimal("17.77"),
                                        remaining=units if remaining is None else remaining, store=store or self.khan)
        Purchase.objects.filter(pk=batch.pk).update(date=self.now - timedelta(days=days_ago))
        return batch

    def _sold(self, product, units, days_ago=3):
        order = SaleOrder.objects.create()
        sale = Sale.objects.create(order=order, product=product, quantity=units, unit_price=Decimal("20"),
                                   payment_method="cash")
        Sale.objects.filter(pk=sale.pk).update(date=self.now - timedelta(days=days_ago))


class SellableTests(CatalogMixin):
    def _units(self, product):
        return sellable_units([product.pk])[product.pk]

    def test_each_shop_holds_back_its_own_sample(self):
        product = self._product(units=2)
        self._batch(product, 2, 200, store=self.scentory)
        self.assertEqual(self._units(product), 2)

    def test_a_shop_that_keeps_no_sample_sells_everything(self):
        Store.objects.filter(pk=self.khan.pk).update(keeps_samples=False)
        self.assertEqual(self._units(self._product(units=2)), 2)

    def test_a_closed_shop_does_not_count(self):
        product = self._product(units=5)
        Store.objects.filter(pk=self.khan.pk).update(is_active=False)
        self.assertEqual(self._units(product), 0)

    def test_stock_not_yet_in_a_shop_counts_in_full(self):
        product = self._product(units=0)
        batch = Purchase.objects.create(product=product, quantity=3, remaining=3, cost_price=Decimal("1"))
        Purchase.objects.filter(pk=batch.pk).update(store=None)
        self.assertEqual(self._units(product), 3)

    def test_a_product_with_nothing_is_zero(self):
        self.assertEqual(sellable_units([999999]).get(999999, 0), 0)

    def test_the_three_states(self):
        self.assertEqual([availability(n)[1] for n in (4, 3, 2, 1, 0)],
                         ["Disponível", "Disponível", "Stock reduzido", "Stock reduzido", "Indisponível"])


class RowsTests(CatalogMixin):
    def _rows(self, **filters):
        return catalog_rows(**filters)

    def test_a_row_carries_only_what_a_customer_may_see(self):
        self._product(units=9, gender="women")
        row = self._rows()[0]
        self.assertEqual(set(row), {"pk", "ean", "brand", "logo_brand", "title", "kind", "gender", "price", "state",
                                    "state_label", "image_url", "contents", "description", "requested", "base_price", "in_cart"})

    def test_out_of_stock_is_hidden_until_asked_for(self):
        self._product(name="Have", units=9)
        self._product(name="Gone", units=0)
        self.assertEqual([r["title"] for r in self._rows()], ["Line Have"])
        self.assertEqual(len(self._rows(show_all=True)), 2)

    def test_available_comes_first_then_low_then_out(self):
        self._product(name="Gone", units=0)
        self._product(name="Low", units=2)
        self._product(name="Have", units=9)
        self.assertEqual([r["state"] for r in self._rows(show_all=True)], ["in-stock", "low-stock", "out-stock"])

    def test_lattafa_then_the_biggest_brand(self):
        self._product(name="A", brand="Armaf", units=9)
        for n in range(2):
            self._product(name=f"R{n}", brand="Rasasi", units=9)
        self._product(name="L", units=9)
        self.assertEqual([r["brand"] for r in self._rows()], ["Lattafa", "Rasasi", "Rasasi", "Armaf"])

    def test_inside_a_brand_the_type_comes_first(self):
        self._product(name="S", units=9, product_type_id="body_spray")
        self._product(name="P", units=9)
        self.assertEqual([r["kind"] for r in self._rows()], ["Perfume", "Body spray"])

    def test_filters(self):
        self._product(name="Spray", units=9, product_type_id="body_spray", gender="men")
        other = self._product(name="Other", brand="Armaf", units=9, gender="women")
        self.assertEqual(len(self._rows(kind="body_spray")), 1)
        self.assertEqual(len(self._rows(gender="women")), 1)
        self.assertEqual(self._rows(brand="armaf")[0]["pk"], other.pk)
        self.assertEqual(self._rows(query="armaf other")[0]["pk"], other.pk)

    def test_an_archived_product_never_shows(self):
        product = self._product(units=9)
        Product.objects.filter(pk=product.pk).update(archived_at=self.now)
        self.assertEqual(self._rows(show_all=True), [])

    def test_a_product_without_a_photo_is_still_listed(self):
        self._product(units=9)
        self.assertEqual(self._rows()[0]["image_url"], "")

    def test_no_wholesale_price_is_no_price(self):
        self._product(units=9, wholesale_price=None)
        self._product(name="Zero", units=9, wholesale_price=Decimal("0"))
        self.assertEqual([r["price"] for r in self._rows()], [None, None])

    def test_the_list_says_what_a_mix_holds(self):
        yara = self._product(units=9)
        mix = self._product(name="Spray", units=9, is_mix=True, product_type_id="body_spray")
        save_contents(mix, [{"name": "", "linked_id": yara.pk}, {"name": "Oud", "linked_id": None}])
        row = next(r for r in self._rows() if r["pk"] == mix.pk)
        self.assertEqual(row["contents"], ["Line Yara", "Oud"])

    def test_inside_a_type_the_best_selling_line_comes_first(self):
        quiet = self._product(name="A", units=9, model="Alfa")
        loud = self._product(name="B", units=9, model="Beta")
        self._sold(loud, 8)
        self._sold(quiet, 2)
        self.assertEqual([r["pk"] for r in self._rows()], [loud.pk, quiet.pk])

    def test_a_line_counts_all_its_products(self):
        a = self._product(name="A", units=9, model="Alfa")
        b = self._product(name="B", units=9, model="Alfa")
        c = self._product(name="C", units=9, model="Beta")
        self._sold(a, 3)
        self._sold(b, 3)
        self._sold(c, 5)
        self.assertEqual([r["pk"] for r in self._rows()][-1], c.pk)

    def test_old_sales_count_too(self):
        old = self._product(name="A", units=9, model="Alfa")
        new = self._product(name="B", units=9, model="Beta")
        self._sold(old, 9, days_ago=400)
        self._sold(new, 2)
        self.assertEqual(self._rows()[0]["pk"], old.pk)

    def test_the_type_still_comes_before_the_sales(self):
        spray = self._product(name="S", units=9, model="Alfa", product_type_id="body_spray")
        bottle = self._product(name="P", units=9, model="Beta")
        self._sold(spray, 50)
        self.assertEqual([r["pk"] for r in self._rows()], [bottle.pk, spray.pk])

    def test_no_line_goes_last(self):
        blank = self._product(name="X", units=9, model="")
        lined = self._product(name="Y", units=9, model="Alfa")
        self._sold(blank, 50)
        self.assertEqual([r["pk"] for r in self._rows()], [lined.pk, blank.pk])


class PageTests(CatalogMixin):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.boss)

    def _get(self, **params):
        return self.client.get(reverse("catalog"), params)

    def test_the_page_is_a_list_with_the_wholesale_price(self):
        self._product(units=9, wholesale_price=Decimal("25.50"), default_price=Decimal("44"))
        response = self._get()
        self.assertContains(response, "Preço grossista")
        self.assertContains(response, "25.50")
        self.assertNotContains(response, "44.00")

    def test_no_units_and_no_cost_reach_the_page(self):
        self._product(units=137, default_price=Decimal("44"))
        html = self._get().content.decode()
        self.assertNotIn("137", html)
        self.assertNotIn("17.77", html)

    def test_a_missing_price_says_so(self):
        self._product(units=9, wholesale_price=None)
        self.assertContains(self._get(), "Sob consulta")

    def test_the_page_shows_24_a_page(self):
        for n in range(25):
            self._product(name=f"P{n}", units=9)
        self.assertEqual(len(self._get().context["page_obj"]), 24)
        self.assertEqual(len(self._get(page=2).context["page_obj"]), 1)

    def test_show_all_keeps_going_across_pages(self):
        self._product(units=0)
        html = self._get(stock="all").content.decode()
        self.assertIn("Indisponível", html)

    def test_an_empty_result_says_so(self):
        self.assertContains(self._get(q="nothing here"), "Sem resultados")

    def test_the_filters_offer_only_what_exists(self):
        self._product(units=9, product_type_id="coffret", gender="men")
        context = self._get().context
        self.assertEqual([t["value"] for t in context["types"]], ["coffret"])
        self.assertEqual(context["brands"], ["Lattafa"])

    def test_the_panel_uses_the_shared_side_panel_parts(self):
        product = self._product(units=9, description="Doce", gender="men")
        html = self._get().content.decode()
        panel = html[html.index(f'<template id="wc-{product.pk}">'):]
        panel = panel[:panel.index("</template>")]
        self.assertIn("sp-kpis", panel)
        self.assertIn('class="sp-kv"', panel)
        self.assertIn("sp-sec", panel)
        self.assertEqual(panel.count("<dt>"), 4)      # brand, EAN, type, gender: price and stock head the panel

    def test_the_panel_lists_what_a_mix_holds(self):
        mix = self._product(name="Spray", units=9, is_mix=True, product_type_id="body_spray")
        save_contents(mix, [{"name": "Oud", "linked_id": None}])
        html = self._get().content.decode()
        panel = html[html.index(f'<template id="wc-{mix.pk}">'):]
        self.assertIn("Fragrâncias", panel[:panel.index("</template>")])

    def test_there_are_no_notes(self):
        product = self._product(units=9, days_ago=1)
        self._sold(product, 50)
        html = self._get().content.decode()
        self.assertNotIn("Novidade", html)
        self.assertNotIn("Mais vendido", html)

    def test_the_description_leaves_out_what_inspired_it(self):
        self._product(units=9, description="Doce", inspired_by_text="Baccarat")
        html = self._get().content.decode()
        self.assertIn("Doce", html)
        self.assertNotIn("Baccarat", html)
