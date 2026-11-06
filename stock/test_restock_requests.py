"""Asking for a sold-out product to come back, and seeing which ones are worth it."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from stock.models import Customer, Product, Purchase, RestockRequest, Sale, SaleOrder
from stock.services.restock_requests import demand_rows, request_restock
from stock.test_wholesale_catalog import CatalogMixin


class ServiceTests(CatalogMixin):
    def setUp(self):
        super().setUp()
        self.gone = self._product(name="Gone", units=0)
        self.other = User.objects.create_user("other", password="pw")

    def _demand(self):
        return {row["product"].pk: row for row in demand_rows()}

    def test_a_request_is_recorded(self):
        self.assertTrue(request_restock(self.gone, self.boss))
        row = self._demand()[self.gone.pk]
        self.assertEqual(row["requests"], 1)
        self.assertIsNotNone(row["last_request"])

    def test_the_same_person_counts_once(self):
        request_restock(self.gone, self.boss)
        self.assertFalse(request_restock(self.gone, self.boss))
        self.assertEqual(self._demand()[self.gone.pk]["requests"], 1)

    def test_two_people_count_twice(self):
        request_restock(self.gone, self.boss)
        request_restock(self.gone, self.other)
        self.assertEqual(self._demand()[self.gone.pk]["requests"], 2)

    def test_two_customers_of_one_person_count_twice(self):
        request_restock(self.gone, self.boss, Customer.objects.create(name="A"))
        request_restock(self.gone, self.boss, Customer.objects.create(name="B"))
        self.assertEqual(self._demand()[self.gone.pk]["requests"], 2)

    def test_a_product_on_sale_cannot_be_requested(self):
        have = self._product(name="Have", units=9)
        with self.assertRaises(ValueError):
            request_restock(have, self.boss)
        self.assertEqual(RestockRequest.objects.count(), 0)

    def test_a_delivery_closes_the_requests(self):
        request_restock(self.gone, self.boss)
        RestockRequest.objects.update(created_at=self.now - timedelta(days=2))
        self._batch(self.gone, 0, 1, remaining=0)
        self.assertEqual(self._demand(), {})

    def test_asking_again_after_a_delivery_counts_again(self):
        request_restock(self.gone, self.boss)
        RestockRequest.objects.update(created_at=self.now - timedelta(days=2))
        self._batch(self.gone, 0, 1, remaining=0)
        self.assertTrue(request_restock(self.gone, self.boss))
        self.assertEqual(self._demand()[self.gone.pk]["requests"], 1)

    def test_the_most_wanted_comes_first_then_the_best_selling(self):
        wanted = self._product(name="A", units=0)
        selling = self._product(name="B", units=0)
        quiet = self._product(name="C", units=0)
        request_restock(wanted, self.boss)
        request_restock(wanted, self.other)
        request_restock(selling, self.boss)
        request_restock(quiet, self.boss)
        self._sold(selling, 7)
        self.assertEqual([r["product"].pk for r in demand_rows()], [wanted.pk, selling.pk, quiet.pk])

    def test_only_the_last_90_days_of_sales_count(self):
        request_restock(self.gone, self.boss)
        self._sold(self.gone, 3, days_ago=10)
        self._sold(self.gone, 50, days_ago=100)
        self.assertEqual(self._demand()[self.gone.pk]["sold_90"], 3)

    def test_an_archived_product_leaves_the_list(self):
        request_restock(self.gone, self.boss)
        Product.objects.filter(pk=self.gone.pk).update(archived_at=timezone.now())
        self.assertEqual(demand_rows(), [])


class CatalogueTests(CatalogMixin):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.boss)
        self.have = self._product(name="Have", units=9)
        self.gone = self._product(name="Gone", units=0)

    def _get(self, **params):
        return self.client.get(reverse("catalog"), params)

    def test_browsing_shows_what_is_on_sale(self):
        self.assertEqual([r["pk"] for r in self._get().context["page_obj"]], [self.have.pk])

    def test_a_search_finds_a_sold_out_product(self):
        self.assertEqual([r["pk"] for r in self._get(q="gone").context["page_obj"]], [self.gone.pk])

    def test_a_search_still_puts_what_is_on_sale_first(self):
        rows = self._get(q="line").context["page_obj"]
        self.assertEqual([r["pk"] for r in rows], [self.have.pk, self.gone.pk])

    def test_a_search_can_be_limited_to_what_is_on_sale(self):
        self.assertEqual(len(self._get(q="gone", stock="in").context["page_obj"]), 0)

    def test_a_new_search_starts_over_from_the_toggle(self):
        html = self._get(q="gone", stock="in").content.decode()
        self.assertNotIn('name="stock"', html[html.index('class="fbar"'):html.index("</form>", html.index('class="fbar"'))])

    def test_a_sold_out_row_offers_the_request(self):
        html = self._get(stock="all").content.decode()
        self.assertEqual(html.count("Pedir reposição"), 2)      # the row and its panel

    def test_a_product_on_sale_does_not_offer_it(self):
        self.assertNotIn("Pedir reposição", self._get().content.decode())

    def test_asking_records_it_and_comes_back(self):
        back = reverse("catalog") + "?q=gone"
        response = self.client.post(reverse("restock_request", args=[self.gone.pk]), {"next": back})
        self.assertRedirects(response, back)
        self.assertEqual(RestockRequest.objects.get().requested_by, self.boss)
        html = self._get(q="gone").content.decode()
        self.assertIn("Reposição pedida", html)
        self.assertNotIn("Pedir reposição", html)

    def test_asking_for_a_product_on_sale_changes_nothing(self):
        response = self.client.post(reverse("restock_request", args=[self.have.pk]), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RestockRequest.objects.count(), 0)

    def test_a_link_cannot_ask(self):
        self.assertEqual(self.client.get(reverse("restock_request", args=[self.gone.pk])).status_code, 405)

    def test_a_foreign_next_is_ignored(self):
        response = self.client.post(reverse("restock_request", args=[self.gone.pk]), {"next": "https://evil.example/"})
        self.assertEqual(response["Location"], reverse("catalog"))

    def test_the_page_never_says_how_many_asked(self):
        other = User.objects.create_user("other", password="pw")
        request_restock(self.gone, other)
        self.assertNotIn("requests", self._get(stock="all").context["page_obj"][1])


class DemandPageTests(CatalogMixin):
    def setUp(self):
        super().setUp()
        self.gone = self._product(name="Gone", units=0)

    def test_only_managers_get_in(self):
        self.client.force_login(User.objects.create_user("staff", password="pw"))
        self.assertEqual(self.client.get(reverse("restock_requests")).status_code, 302)

    def test_the_page_lists_the_demand(self):
        request_restock(self.gone, self.boss)
        self._sold(self.gone, 4)
        self.client.force_login(self.boss)
        response = self.client.get(reverse("restock_requests"))
        self.assertContains(response, self.gone.list_name)
        self.assertContains(response, reverse("product_detail", args=[self.gone.pk]))

    def test_a_product_the_shop_stopped_buying_says_so(self):
        Product.objects.filter(pk=self.gone.pk).update(restock=False)
        request_restock(Product.objects.get(pk=self.gone.pk), self.boss)
        self.client.force_login(self.boss)
        self.assertContains(self.client.get(reverse("restock_requests")), "Not restocking")

    def test_an_empty_list_says_so(self):
        self.client.force_login(self.boss)
        self.assertContains(self.client.get(reverse("restock_requests")), "No requests")
