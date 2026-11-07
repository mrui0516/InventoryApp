"""When a supplier gets paid: terms, instalments, what is coming, and each shop's goods."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from stock.models import InboundOrder, InboundPendingItem, Supplier, SupplierPayment
from stock.services import finance, store_stock
from stock.test_finance import FinanceMixin


class PaymentTermsTests(FinanceMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.supplier = Supplier.objects.create(name="Naypes")
        self.order = InboundOrder.objects.create(supplier=self.supplier, status="received", invoice_date=self.today)
        store_stock.receive_batches(product=self.product, quantity=10, cost_price=Decimal("100"),
                                    allocation={self.khan.id: 6, self.scentory.id: 4},
                                    supplier=self.supplier, inbound_order=self.order)

    def row(self):
        return finance.payables()[0]["orders"][0]

    def set(self, **fields):
        InboundOrder.objects.filter(pk=self.order.pk).update(**fields)

    def pay(self, amount):
        finance.add_payment(self.order, store=self.scentory, amount=Decimal(amount), paid_on=self.today,
                            method="bank", note="", user=self.admin)

    def test_unpaid_is_due_30_days_after_the_invoice(self):
        self.assertEqual(self.row()["next_due"], self.today + timedelta(days=30))
        self.assertFalse(self.row()["overdue"])

    def test_with_no_invoice_date_it_counts_from_the_receipt(self):
        self.set(invoice_date=None)
        self.assertEqual(self.row()["next_due"], self.today + timedelta(days=30))

    def test_a_past_date_is_overdue(self):
        self.set(invoice_date=self.today - timedelta(days=31))
        self.assertTrue(self.row()["overdue"])

    def test_a_date_set_by_hand_wins(self):
        day = self.today + timedelta(days=5)
        self.set(pay_first_due=day)
        self.assertEqual(self.row()["next_due"], day)

    def test_instalments_are_equal_and_add_up(self):
        self.set(pay_instalments=3, pay_first_due=date(2026, 1, 31))
        rows = self.row()["schedule"]
        self.assertEqual([r["amount"] for r in rows], [Decimal("333.33"), Decimal("333.33"), Decimal("333.34")])
        self.assertEqual([r["on"] for r in rows], [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)])

    def test_a_payment_covers_the_earliest_first(self):
        self.set(pay_instalments=3, pay_first_due=self.today)
        self.pay("400")
        rows = self.row()["schedule"]
        self.assertEqual([r["left"] for r in rows], [Decimal("0.00"), Decimal("266.66"), Decimal("333.34")])
        self.assertEqual(self.row()["next_due"], rows[1]["on"])

    def test_the_amounts_follow_the_order(self):
        self.set(pay_instalments=2)
        self.order.items.update(cost_price=Decimal("50"))
        self.assertEqual([r["amount"] for r in self.row()["schedule"]], [Decimal("250.00"), Decimal("250.00")])

    def test_each_shops_goods_are_on_the_row(self):
        self.assertEqual({s["label"]: s["cost"] for s in self.row()["shops"]},
                         {"Khan": Decimal("600.00"), "Scentory": Decimal("400.00")})

    def test_the_forecast_groups_by_month(self):
        self.set(pay_instalments=2, pay_first_due=date(2026, 12, 10))
        rows = finance.supplier_forecast(date(2026, 11, 6))
        self.assertEqual([(r["label"], r["amount"]) for r in rows],
                         [("Dec 2026", Decimal("500.00")), ("Jan 2027", Decimal("500.00"))])

    def test_the_forecast_puts_late_money_first(self):
        self.set(pay_first_due=date(2026, 10, 1))
        rows = finance.supplier_forecast(date(2026, 11, 6))
        self.assertEqual([(r["label"], r["late"]) for r in rows], [("Overdue", True)])

    def test_the_forecast_ends_in_later(self):
        self.set(pay_first_due=date(2027, 12, 1))
        self.assertEqual(finance.supplier_forecast(date(2026, 11, 6))[0]["label"], "Later")

    def test_a_paid_order_is_out_of_the_forecast(self):
        self.pay("1000")
        self.assertEqual(finance.supplier_forecast(self.today), [])

    def test_the_goods_each_shop_received_in_the_month(self):
        rows = finance.received_by_shop(self.month, self.today + timedelta(days=1))
        self.assertEqual({r["label"]: (r["units"], r["cost"]) for r in rows},
                         {"Khan": (6, Decimal("600.00")), "Scentory": (4, Decimal("400.00"))})

    def test_the_pages_show_it(self):
        page = self.client.get(reverse("finance_payables"))
        self.assertContains(page, "change on the inbound record")
        self.assertContains(page, "Khan")
        overview = self.client.get(reverse("finance"))
        self.assertContains(overview, "Supplier payments due")
        self.assertContains(overview, "Goods received")

    def test_terms_can_be_changed_later(self):
        day = self.today + timedelta(days=60)
        self.client.post(reverse("inbound_order_terms", args=[self.order.pk]),
                         {"pay_mode": "instalments", "pay_first_due": day.isoformat(), "pay_instalments": "4"})
        self.order.refresh_from_db()
        self.assertEqual((self.order.pay_first_due, self.order.pay_instalments), (day, 4))


class ReceivePaymentTests(FinanceMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.supplier = Supplier.objects.create(name="Naypes")
        self.order = InboundOrder.objects.create(supplier=self.supplier, status="pending_receipt",
                                                 invoice_date=self.today)
        self.line = InboundPendingItem.objects.create(
            inbound_order=self.order, product=self.product, quantity=10, cost_price=Decimal("10"),
            allocation={str(self.khan.id): 6, str(self.scentory.id): 4})
        self.paying = dict(pay_mode="paid", pay_store=self.scentory.pk, pay_method="bank",
                           pay_on=self.today.isoformat())

    def _post(self, action="receive", **fields):
        return self.client.post(reverse("inbound_receive", args=[self.order.pk]), {
            "action": action, "supplier": self.supplier.pk, "shipping": "0", "invoice_date": self.today.isoformat(),
            "lines-TOTAL_FORMS": 1, "lines-INITIAL_FORMS": 1, "lines-MIN_NUM_FORMS": 0, "lines-MAX_NUM_FORMS": 1000,
            "lines-0-id": self.line.pk, "lines-0-quantity": 10, "lines-0-cost_price": "10",
            "lines-0-shipping_share": "0", f"alloc-{self.line.pk}-{self.khan.pk}": 6,
            f"alloc-{self.line.pk}-{self.scentory.pk}": 4, **fields})

    def _page(self):
        return self.client.get(reverse("inbound"))

    def _manager(self):
        user = get_user_model().objects.create_user("mgr", password="pw")
        user.groups.add(Group.objects.get_or_create(name="Managers")[0])
        self.client.force_login(user)

    def test_the_admin_sees_the_payment_choice(self):
        self.assertContains(self._page(), '<fieldset class="rcv-pay"')

    def test_a_manager_does_not(self):
        self._manager()
        response = self._page()
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<fieldset class="rcv-pay"')

    def test_a_manager_cannot_post_it_either(self):
        self._manager()
        self._post(**self.paying)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "received")
        self.assertFalse(SupplierPayment.objects.exists())

    def test_stock_comes_in_whatever_the_payment(self):
        self._post(pay_mode="later")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "received")
        self.assertEqual(finance.payables()[0]["owed"], Decimal("100.00"))

    def test_paid_records_the_payment(self):
        self._post(**self.paying)
        payment = SupplierPayment.objects.get()
        self.assertEqual((payment.amount, payment.store, payment.method), (Decimal("100.00"), self.scentory, "bank"))
        self.assertEqual(finance.payables(), [])

    def test_saving_twice_pays_once(self):
        self._post("save", **self.paying)
        self._post("save", **self.paying)
        self.assertEqual(SupplierPayment.objects.count(), 1)

    def test_paid_without_a_shop_stops_before_anything_moves(self):
        self._post(**{**self.paying, "pay_store": ""})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending_receipt")
        self.assertFalse(SupplierPayment.objects.exists())

    def test_pay_later_keeps_a_chosen_day(self):
        day = self.today + timedelta(days=45)
        self._post(pay_mode="later", pay_first_due=day.isoformat())
        self.order.refresh_from_db()
        self.assertEqual(self.order.pay_first_due, day)

    def test_instalments_are_kept(self):
        self._post(pay_mode="instalments", pay_instalments="3")
        self.order.refresh_from_db()
        self.assertEqual(self.order.pay_instalments, 3)

    def test_instalments_need_two(self):
        self._post(pay_mode="instalments", pay_instalments="1")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending_receipt")

    def test_the_dialog_reopens_on_what_was_chosen(self):
        self._post("save", pay_mode="instalments", pay_instalments="3")
        self.assertContains(self._page(), 'value="instalments" checked')

    def test_the_dialog_shows_each_shops_goods(self):
        self.assertContains(self._page(), "data-rcv-shop")
