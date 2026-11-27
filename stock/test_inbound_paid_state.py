"""Inbound records say what was really paid (2026-11-27).

The date an order was recorded used to be shown as "paid", so an order bought
on credit read as paid on its first day. Now that date is "Ordered", and the
real state comes from the supplier payments: paid (on the day of its last
payment), unpaid with its due day, or overdue. Unpaid rows are tinted, and an
Unpaid filter lists every order still owing, whatever the dates."""
import re
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from stock.models import InboundOrder, Supplier
from stock.services import finance, store_stock
from stock.test_finance import FinanceMixin


class PaidStateMixin(FinanceMixin):
    def setUp(self):
        super().setUp()
        self.supplier = Supplier.objects.create(name="Beauty Trend")

    def order(self, *, ordered_days_ago=0, units=2, cost="50", **fields):
        order = InboundOrder.objects.create(supplier=self.supplier, status="received", **fields)
        store_stock.receive_batches(product=self.product, quantity=units, cost_price=Decimal(cost),
                                    allocation={self.khan.id: units}, supplier=self.supplier, inbound_order=order)
        if ordered_days_ago:
            InboundOrder.objects.filter(pk=order.pk).update(
                created_at=timezone.now() - timedelta(days=ordered_days_ago))
            order.refresh_from_db()
        return order

    def pay(self, order, amount, days_ago=0):
        finance.add_payment(order, store=self.scentory, amount=Decimal(amount),
                            paid_on=self.today - timedelta(days=days_ago), method="bank", note="", user=self.admin)

    def page(self, **params):
        start = params.pop("start", self.today)
        query = {"view": "purchases", "start_date": start.isoformat(), "end_date": self.today.isoformat(), **params}
        return self.client.get(reverse("sales_records"), query)

    def group(self, response, order):
        return next(g for g in response.context["purchases"] if g["key"] == f"in-{order.pk}")


class PaidStateTests(PaidStateMixin, TestCase):
    def test_a_pay_later_order_is_not_called_paid(self):
        later = self.order(ordered_days_ago=4)
        response = self.page()
        html = response.content.decode()
        day = self.today - timedelta(days=4)
        ordered = f"{day.day} {day.strftime('%b')}"           # Django's "j M"
        self.assertNotIn(f"paid {ordered}", html)
        self.assertIn(f"ordered {ordered}", html)
        self.assertIn("<dt>Ordered</dt>", html)
        self.assertEqual(self.group(response, later)["pay_state"], "unpaid")

    def test_unpaid_rows_are_tinted_and_tagged(self):
        self.order()
        html = self.page().content.decode()
        self.assertEqual(html.count('class="op-row is-unpaid"'), 1)
        self.assertRegex(html, r'<span class="ch-tag pay-unpaid">Unpaid · due \d+ \w+</span>')

    def test_a_paid_order_says_the_day_of_its_last_payment(self):
        paid = self.order(cost="50")
        self.pay(paid, "60", days_ago=1)
        self.pay(paid, "40")
        response = self.page()
        group = self.group(response, paid)
        self.assertEqual((group["pay_state"], group["last_paid_on"]), ("paid", self.today))
        self.assertIn(f'<span class="ch-tag pay-paid">Paid {self.today.day} {self.today.strftime("%b")}</span>',
                      response.content.decode())
        self.assertNotIn('class="op-row is-unpaid"', response.content.decode())

    def test_overdue_says_so(self):
        late = self.order(invoice_date=self.today - timedelta(days=40), ordered_days_ago=0)
        html = self.page().content.decode()
        self.assertIn('<span class="ch-tag pay-unpaid">Overdue', html)
        self.assertEqual(self.group(self.page(), late)["pay_state"], "unpaid")

    def test_an_order_from_before_finance_has_no_payment_state(self):
        old = self.order(ordered_days_ago=40)
        response = self.page(start=self.today - timedelta(days=60), pdate="paid")
        self.assertIsNone(self.group(response, old)["pay_state"])
        self.assertNotIn('class="op-row is-unpaid"', response.content.decode())

    def test_the_date_toggle_says_ordered(self):
        labels = [d["label"] for d in self.page().context["pdate_urls"]]
        self.assertEqual(labels, ["Received", "Ordered"])


class UnpaidFilterTests(PaidStateMixin, TestCase):
    def test_every_order_still_owing_whatever_the_dates_soonest_due_first(self):
        older = self.order(ordered_days_ago=20, invoice_date=self.today - timedelta(days=20))
        newer = self.order(invoice_date=self.today)
        part = self.order(invoice_date=self.today - timedelta(days=10))
        self.pay(part, "30")
        done = self.order()
        self.pay(done, "100")
        response = self.page(pay="unpaid")
        keys = [g["key"] for g in response.context["purchases"]]
        self.assertEqual(keys, [f"in-{older.pk}", f"in-{part.pk}", f"in-{newer.pk}"])
        self.assertContains(response, 'class="on" href="')       # the filter shows as chosen

    def test_the_headline_says_what_is_owed_not_a_date_range(self):
        self.order(cost="50")                       # 2 x 50 owed
        part = self.order(cost="50")
        self.pay(part, "30")                        # 70 owed
        response = self.page(pay="unpaid")
        self.assertEqual(response.context["total_owing"], Decimal("170.00"))
        html = response.content.decode()
        self.assertIn('<span class="k">Owing</span>', html)
        self.assertIn("All dates", html)

    def test_paid_orders_cost_the_filter_nothing(self):
        # every order ever made is looked at; a paid one must not have its lines built
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from stock.orders_views import load_purchases

        def queries():
            with CaptureQueriesContext(connection) as ctx:
                groups = load_purchases(self.today, self.today, "", "", with_payments=True, unpaid_only=True)
            return len(groups), len(ctx.captured_queries)

        self.order()
        for _ in range(2):
            self.pay(self.order(), "100")
        before = queries()
        for _ in range(5):
            self.pay(self.order(), "100")
        self.assertEqual(queries(), before)

    def test_more_rows_cost_no_more_queries(self):
        # each row's lines, shops and roll-back check come from what was fetched once
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from stock.orders_views import load_purchases

        def queries(**mode):
            with CaptureQueriesContext(connection) as ctx:
                load_purchases(self.today, self.today, "", "", with_payments=True, **mode)
            return len(ctx.captured_queries)

        self.order()
        before = queries(), queries(unpaid_only=True)
        for _ in range(4):
            self.order()
        self.assertEqual((queries(), queries(unpaid_only=True)), before)

    def test_an_unpaid_row_still_says_when_it_came(self):
        self.order()
        html = self.page(pay="unpaid").content.decode()
        self.assertIn(f"received {self.today.day} {self.today.strftime('%b')}", html)

    def test_the_filter_link_keeps_the_other_choices(self):
        html = self.page(q="oud").content.decode()
        link = re.search(r'<a class="" href="([^"]+)">Unpaid</a>', html).group(1)
        self.assertIn("pay=unpaid", link)
        self.assertIn("q=oud", link)


def choose(client, store):
    session = client.session
    session["active_store_id"] = store.id if store else "all"
    session.save()


class OneShopTests(PaidStateMixin, TestCase):
    """One shop chosen in the header: the order's own state and due day (a
    payment covers every shop's part alike), and that shop's part of the money."""

    def split_order(self, **fields):
        order = InboundOrder.objects.create(supplier=self.supplier, status="received", **fields)
        store_stock.receive_batches(product=self.product, quantity=3, cost_price=Decimal("50"),
                                    allocation={self.khan.id: 2, self.scentory.id: 1},
                                    supplier=self.supplier, inbound_order=order)
        return order                                # 150: Khan 100, Scentory 50

    def test_a_shop_sees_the_state_and_its_part_of_the_money(self):
        order = self.split_order()
        self.pay(order, "60")                       # 40 % of the order
        everyone = self.group(self.page(), order)
        choose(self.client, self.khan)
        response = self.page()
        khan = self.group(response, order)
        self.assertEqual((khan["pay_state"], khan["next_due"], khan["overdue"]),
                         (everyone["pay_state"], everyone["next_due"], everyone["overdue"]))
        self.assertEqual((khan["cost"], khan["order_paid"], khan["order_owing"]),
                         (Decimal("100"), Decimal("40.00"), Decimal("60.00")))
        html = response.content.decode()
        self.assertIn('class="op-row is-unpaid"', html)
        self.assertIn('<span class="ch-tag pay-unpaid">Part paid', html)
        self.assertIn("<dt>Owing</dt><dd>EUR 60.00</dd>", html)

    def test_paid_in_full_is_paid_in_every_shop(self):
        order = self.split_order()
        self.pay(order, "100", days_ago=1)
        self.pay(order, "50")
        for store in (self.khan, self.scentory):
            choose(self.client, store)
            group = self.group(self.page(), order)
            self.assertEqual((group["pay_state"], group["order_owing"], group["last_paid_on"]),
                             ("paid", Decimal("0.00"), self.today))

    def test_the_unpaid_filter_lists_what_the_shop_owes(self):
        split = self.split_order()                  # Khan's part: 100
        mine = self.order(cost="50")                # all Khan's: 100
        self.pay(mine, "25")                        # 75 left
        theirs = InboundOrder.objects.create(supplier=self.supplier, status="received")
        store_stock.receive_batches(product=self.product, quantity=1, cost_price=Decimal("80"),
                                    allocation={self.scentory.id: 1}, supplier=self.supplier, inbound_order=theirs)
        choose(self.client, self.khan)
        response = self.page(pay="unpaid")
        self.assertEqual({g["key"] for g in response.context["purchases"]}, {f"in-{split.pk}", f"in-{mine.pk}"})
        self.assertEqual(response.context["total_owing"], Decimal("175.00"))
        self.assertContains(response, '<span class="k">Owing</span>')


class ManagerTests(PaidStateMixin, TestCase):
    def test_a_manager_sees_no_payment_state(self):
        self.order()
        user = get_user_model().objects.create_user("mgr_ps", password="pw")
        user.groups.add(Group.objects.get_or_create(name="Managers")[0])
        self.client.force_login(user)
        for store in (None, self.khan):
            choose(self.client, store)
            html = self.page().content.decode()
            self.assertNotIn('class="op-row is-unpaid"', html)
            self.assertNotIn(">Unpaid<", html)
            self.assertNotIn('class="ch-tag pay-unpaid"', html)
