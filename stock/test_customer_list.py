"""Customers: new customers show and can be found, customers who bought come first."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from stock.models import Customer, SaleOrder, Store


class CustomerListTests(TestCase):
    def setUp(self):
        Store.objects.update(is_default=False, is_active=False)
        self.khan = Store.objects.create(name="Khan", code="KP", is_default=True)
        self.scentory = Store.objects.create(name="Scentory", code="SC")
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="pw"))
        self.buyer = Customer.objects.create(name="Bought Here")
        SaleOrder.objects.create(customer=self.buyer, store=self.khan)
        self.elsewhere = Customer.objects.create(name="Bought Elsewhere")
        SaleOrder.objects.create(customer=self.elsewhere, store=self.scentory)
        self.older_new = Customer.objects.create(name="Older New")
        self.newest = Customer.objects.create(name="Newest New")

    def choose(self, store):
        session = self.client.session
        session["active_store_id"] = store.id if store else "all"
        session.save()

    def names(self, **params):
        return [c.name for c in self.client.get(reverse("customer_search"), params).context["customers"]]

    def test_new_customers_show_after_the_ones_who_bought(self):
        self.choose(None)
        self.assertEqual(self.names()[-2:], ["Newest New", "Older New"])
        self.assertEqual(set(self.names()[:2]), {"Bought Here", "Bought Elsewhere"})

    def test_a_shop_shows_its_buyers_and_every_new_customer(self):
        self.choose(self.khan)
        self.assertEqual(self.names(), ["Bought Here", "Newest New", "Older New"])

    def test_a_search_finds_a_new_customer(self):
        self.choose(self.khan)
        self.assertEqual(self.names(q="Newest"), ["Newest New"])

    def test_a_search_finds_the_other_shops_customer(self):
        self.choose(self.khan)
        self.assertEqual(self.names(q="Elsewhere"), ["Bought Elsewhere"])

    def test_the_new_filter_lists_them(self):
        self.choose(self.khan)
        self.assertEqual(self.names(status="new"), ["Newest New", "Older New"])
