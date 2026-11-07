"""Paying is decided when the inbound order is made, and Finance follows it."""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from stock.models import InboundOrder, Supplier, SupplierPayment
from stock.services import finance, store_stock
from stock.test_finance import FinanceMixin


class NewOrderMixin(FinanceMixin):
    def setUp(self):
        super().setUp()
        self.supplier = Supplier.objects.create(name="Naypes")

    def create(self, **pay):
        items = [{"barcode": self.product.barcode, "qty": 4, "cost_price": "25", "shipping": "0"}]
        return self.client.post(reverse("inbound"), {"items_json": json.dumps(items), "supplier": self.supplier.pk,
                                                     "invoice_date": self.today.isoformat(), "shipping": "0", **pay})

    def manager(self):
        user = get_user_model().objects.create_user("mgr", password="pw")
        user.groups.add(Group.objects.get_or_create(name="Managers")[0])
        self.client.force_login(user)


class ChosenOnCreateTests(NewOrderMixin, TestCase):
    def test_the_form_asks(self):
        page = self.client.get(reverse("inbound"))
        self.assertContains(page, 'name="pay_mode" value="paid" required')

    def test_without_a_choice_nothing_is_made(self):
        self.create()
        self.assertFalse(InboundOrder.objects.exists())

    def test_paid_goes_straight_to_finance(self):
        self.create(pay_mode="paid", pay_store=self.scentory.pk, pay_method="bank", pay_on=self.today.isoformat())
        order = InboundOrder.objects.get()
        payment = SupplierPayment.objects.get()
        self.assertEqual((payment.inbound_order, payment.amount, payment.store), (order, Decimal("100.00"), self.scentory))
        self.assertEqual(finance.payables(), [])

    def test_pay_later_keeps_the_day(self):
        day = self.today + timedelta(days=10)
        self.create(pay_mode="later", pay_first_due=day.isoformat())
        self.assertEqual(InboundOrder.objects.get().pay_first_due, day)
        self.assertEqual(finance.payables()[0]["orders"][0]["next_due"], day)

    def test_instalments(self):
        self.create(pay_mode="instalments", pay_instalments="4")
        self.assertEqual(InboundOrder.objects.get().pay_instalments, 4)

    def test_a_manager_is_not_asked(self):
        self.manager()
        self.assertNotContains(self.client.get(reverse("inbound")), '<fieldset class="rcv-pay"')
        self.create()
        order = InboundOrder.objects.get()
        self.assertEqual((order.pay_instalments, order.pay_first_due), (1, None))

    def test_the_receive_dialog_opens_on_the_choice(self):
        self.create(pay_mode="instalments", pay_instalments="4")
        self.assertContains(self.client.get(reverse("inbound")), 'value="instalments" checked')


class FinanceDoesNotChooseTests(NewOrderMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.order = InboundOrder.objects.create(supplier=self.supplier, status="received", invoice_date=self.today)
        store_stock.receive_batches(product=self.product, quantity=10, cost_price=Decimal("100"),
                                    allocation={self.khan.id: 10}, supplier=self.supplier, inbound_order=self.order)

    def test_finance_shows_the_terms_and_where_to_change_them(self):
        page = self.client.get(reverse("finance_payables"))
        self.assertNotContains(page, "Save terms")
        self.assertContains(page, reverse("inbound_order_edit", args=[self.order.pk]))

    def test_the_record_page_changes_them(self):
        page = self.client.get(reverse("inbound_order_edit", args=[self.order.pk]))
        self.assertContains(page, 'name="pay_mode"')
        day = self.today + timedelta(days=60)
        self.client.post(reverse("inbound_order_terms", args=[self.order.pk]),
                         {"pay_mode": "instalments", "pay_instalments": "3", "pay_first_due": day.isoformat()})
        self.order.refresh_from_db()
        self.assertEqual((self.order.pay_instalments, self.order.pay_first_due), (3, day))

    def test_paid_from_the_record_page(self):
        self.client.post(reverse("inbound_order_terms", args=[self.order.pk]),
                         {"pay_mode": "paid", "pay_store": self.scentory.pk, "pay_method": "cash",
                          "pay_on": self.today.isoformat()})
        self.assertEqual(SupplierPayment.objects.get().amount, Decimal("1000.00"))

    def test_the_30_days_stay_automatic(self):
        self.client.post(reverse("inbound_order_terms", args=[self.order.pk]),
                         {"pay_mode": "later", "pay_first_due": (self.today + timedelta(days=30)).isoformat()})
        self.order.refresh_from_db()
        self.assertIsNone(self.order.pay_first_due)

    def test_a_manager_cannot(self):
        self.manager()
        self.client.post(reverse("inbound_order_terms", args=[self.order.pk]), {"pay_mode": "instalments",
                                                                                "pay_instalments": "3"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.pay_instalments, 1)
