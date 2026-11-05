"""The wholesale catalogue: what a customer may see, as an internal sample for now."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from stock.models import Category, Product, Purchase, Sale, SaleOrder, Store
from stock.services.product_badges import BEST_SELLER, NEW, product_badges
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


class NewProductTests(CatalogMixin):
    def test_a_first_delivery_in_the_last_14_days_is_new(self):
        fresh = self._product(units=5, days_ago=13)
        self.assertEqual(product_badges([fresh]), {fresh.pk: NEW})

    def test_a_first_delivery_older_than_14_days_is_not(self):
        old = self._product(units=5, days_ago=15)
        self.assertEqual(product_badges([old]), {})

    def test_a_new_delivery_of_an_old_product_is_not_new(self):
        old = self._product(units=5, days_ago=200)
        self._batch(old, 5, 1)
        self.assertEqual(product_badges([old]), {})

    def test_a_new_product_sold_out_is_not_offered_as_new(self):
        fresh = self._product(units=0, days_ago=2)
        self.assertEqual(product_badges([fresh]), {})


class BestSellerTests(CatalogMixin):
    def _list(self, count):
        return [self._product(name=f"P{n}", units=5) for n in range(count)]

    def test_the_top_tenth_of_the_list_with_five_sold(self):
        rows = self._list(20)
        for n, units in enumerate((9, 8, 7)):
            self._sold(rows[n], units)
        self.assertEqual(product_badges(rows), {rows[0].pk: BEST_SELLER, rows[1].pk: BEST_SELLER})

    def test_a_short_list_still_has_its_best(self):
        rows = self._list(3)
        self._sold(rows[1], 6)
        self.assertEqual(product_badges(rows), {rows[1].pk: BEST_SELLER})

    def test_fewer_than_five_sold_is_not_a_best_seller(self):
        rows = self._list(3)
        self._sold(rows[0], 4)
        self.assertEqual(product_badges(rows), {})

    def test_only_the_last_30_days_count(self):
        rows = self._list(3)
        self._sold(rows[0], 50, days_ago=31)
        self.assertEqual(product_badges(rows), {})

    def test_several_sales_add_up(self):
        rows = self._list(3)
        self._sold(rows[0], 3)
        self._sold(rows[0], 3, days_ago=10)
        self.assertEqual(product_badges(rows), {rows[0].pk: BEST_SELLER})

    def test_a_sold_out_best_seller_is_not_shown(self):
        gone = self._product(units=0)
        self._sold(gone, 20)
        self.assertEqual(product_badges([gone]), {})

    def test_new_wins_when_it_is_both(self):
        fresh = self._product(units=5, days_ago=2)
        self._sold(fresh, 20)
        self.assertEqual(product_badges([fresh]), {fresh.pk: NEW})

    def test_nothing_to_rank(self):
        self.assertEqual(product_badges([]), {})


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
                                    "state_label", "note", "image_url", "contents", "description"})

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

    def test_the_note_comes_with_the_row(self):
        self._product(units=9, days_ago=1)
        self.assertEqual(self._rows()[0]["note"], NEW)

    def test_a_filter_does_not_change_the_note(self):
        rows = [self._product(name=f"P{n}", units=5) for n in range(20)]
        self._sold(rows[0], 9)
        self._sold(rows[1], 8)
        self._sold(rows[2], 7)
        hit = next(r for r in self._rows(query="P0") if r["pk"] == rows[0].pk)
        self.assertEqual(hit["note"], BEST_SELLER)
        third = next(r for r in self._rows(query="P2") if r["pk"] == rows[2].pk)
        self.assertEqual(third["note"], "")


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

    def test_the_description_leaves_out_what_inspired_it(self):
        self._product(units=9, description="Doce", inspired_by_text="Baccarat")
        html = self._get().content.decode()
        self.assertIn("Doce", html)
        self.assertNotIn("Baccarat", html)
