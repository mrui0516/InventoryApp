"""A sale recorded is reported as recorded, and "database is locked" never
reaches the till or the order page as a crash (2026-11-22).

The bug: each sale line scheduled a daily-summary recalculation to run after
the commit; when one hit SQLite's write lock the error surfaced as "Unexpected
error: database is locked" on Sell, or a 500 on Orders -> new, for sales that
were in fact in the books."""
import json
import logging
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import OperationalError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from stock import system_log
from stock.models import Category, ErrorLog, Product, Purchase, Sale, SaleOrder, Store
from stock.services.summaries import schedule_summary_recalc

LOCKED = OperationalError("database is locked")


class SaleFixture:
    def make_fixture(self, username):
        from stock.stores import ACTIVE_STORE_SESSION_KEY
        self.admin = get_user_model().objects.create_superuser(username, password="pw123456")
        self.client.force_login(self.admin)
        Store.objects.update(is_active=False)
        self.store = Store.objects.create(name="Lock Store", code="LCK")
        session = self.client.session
        session[ACTIVE_STORE_SESSION_KEY] = self.store.id
        session.save()
        category = Category.objects.create(name="Lock Cat")
        self.product = Product.objects.create(name="Lock", brand="LK", barcode="9880000000077",
                                              category=category, default_price=Decimal("10"))
        Purchase.objects.create(product=self.product, quantity=10, remaining=10, cost_price=Decimal("4"),
                                store=self.store)

    def sell(self, qty=1, token="0123456789abcdef0123456789abcd77"):
        return self.client.post(reverse("outbound"), {
            "items_json": json.dumps([{"barcode": self.product.barcode, "qty": qty, "price": "10.00"}]),
            "payments_json": json.dumps([{"method": "cash", "amount": f"{10 * qty}.00"}]),
            "submit_token": token})

    def make_order(self):
        when = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
        return self.client.post(reverse("sale_order_correction_create"), {
            "order_datetime": when, "note": "Lock test", "reason": "Entered by hand",
            "store": self.store.id, "affects_stock": "on",
            "items_json": json.dumps([{"product_id": self.product.id, "qty": 2, "price": "10.00", "payment": "cash"}]),
            "payments_json": json.dumps([{"method": "cash", "amount": "20.00"}])})


class AfterTheCommitTests(SaleFixture, TransactionTestCase):
    """Real commits, so the after-commit callbacks really run."""
    serialized_rollback = True

    def setUp(self):
        self.make_fixture("lock_admin")

    def test_sell_reports_a_recorded_sale_as_recorded(self):
        with mock.patch("stock.services.summaries.recalc_summary_for_date", side_effect=LOCKED):
            response = self.sell()
        self.assertRedirects(response, reverse("outbound"), fetch_redirect_response=False)
        self.assertEqual((SaleOrder.objects.count(), Sale.objects.count()), (1, 1))
        page = self.client.get(reverse("outbound")).content.decode()
        self.assertIn("Sale completed", page)
        self.assertNotIn("Unexpected error", page)

    def test_a_hand_made_order_is_saved_and_shown_not_a_500(self):
        with mock.patch("stock.services.summaries.recalc_summary_for_date", side_effect=LOCKED):
            response = self.make_order()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SaleOrder.objects.filter(note="Lock test").count(), 1)

    def test_a_failure_after_the_commit_is_still_told_apart(self):
        # Any other after-commit step failing: the claimed token says the sale is in.
        def fail_after_commit(*args, **kwargs):
            transaction.on_commit(lambda: (_ for _ in ()).throw(RuntimeError("late step")))
            return None
        with mock.patch("stock.views.card_fees.record", side_effect=fail_after_commit):
            response = self.sell(token="0123456789abcdef0123456789abcd78")
        self.assertRedirects(response, reverse("outbound"), fetch_redirect_response=False)
        self.assertEqual(SaleOrder.objects.count(), 1)
        page = self.client.get(reverse("outbound")).content.decode()
        self.assertIn("The sale was recorded, but a step after it failed", page)


class InsideTheTransactionTests(SaleFixture, TestCase):
    def setUp(self):
        self.make_fixture("lock_admin2")

    def test_sell_says_plainly_that_nothing_was_recorded(self):
        with mock.patch("stock.views.consume_for_sale", side_effect=LOCKED):
            response = self.sell()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The database was busy, so this sale was NOT recorded")
        self.assertEqual(SaleOrder.objects.count(), 0)
        self.assertEqual(Purchase.objects.get(product=self.product).remaining, 10)

    def test_the_order_page_says_it_was_not_saved(self):
        with mock.patch("stock.views.save_sale_order_correction", side_effect=LOCKED):
            response = self.make_order()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The database was busy, so the order was NOT saved")

    def test_one_summary_per_day_however_many_lines(self):
        other = Product.objects.create(name="Lock 2", brand="LK", barcode="9880000000078",
                                       category=self.product.category, default_price=Decimal("10"))
        Purchase.objects.create(product=other, quantity=5, remaining=5, cost_price=Decimal("4"), store=self.store)
        items = [{"barcode": self.product.barcode, "qty": 1, "price": "10.00"},
                 {"barcode": other.barcode, "qty": 2, "price": "10.00"}]
        with mock.patch("stock.services.summaries.recalc_summary_for_date") as recalc:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("outbound"), {
                    "items_json": json.dumps(items),
                    "payments_json": json.dumps([{"method": "cash", "amount": "30.00"}]),
                    "submit_token": "0123456789abcdef0123456789abcd79"})
        self.assertEqual(Sale.objects.count(), 2)
        self.assertEqual(recalc.call_count, 1)

    def test_a_summary_that_fails_is_logged_not_raised(self):
        with mock.patch("stock.services.summaries.recalc_summary_for_date", side_effect=LOCKED):
            with self.assertLogs("stock.services.summaries", level="ERROR"):
                with self.captureOnCommitCallbacks(execute=True):
                    schedule_summary_recalc(timezone.localdate())


class SettingsTests(TestCase):
    def test_writers_queue_instead_of_failing_at_once(self):
        options = connection.settings_dict["OPTIONS"]
        self.assertEqual(options["transaction_mode"], "IMMEDIATE")
        self.assertGreaterEqual(options["timeout"], 20)


class RepeatedWarningTests(TestCase):
    def setUp(self):
        system_log._recent.clear()
        self.addCleanup(system_log._recent.clear)
        self.log = logging.getLogger("stock.services.shopify_catalog_worker")

    def test_a_repeated_warning_is_counted_not_written_each_time(self):
        with mock.patch("stock.system_log.time.monotonic", return_value=1000.0):
            for _ in range(3):
                self.log.warning("Shopify catalogue job %s remains queued: %s", 476, "duplicate SKU")
        row = ErrorLog.objects.get()
        self.assertEqual(row.count, 1)
        with mock.patch("stock.system_log.time.monotonic", return_value=1000.0 + system_log.REPEAT_SECONDS + 1):
            self.log.warning("Shopify catalogue job %s remains queued: %s", 476, "duplicate SKU")
        row.refresh_from_db()
        self.assertEqual(row.count, 4)

    def test_errors_and_failed_logins_are_always_written(self):
        auth = logging.getLogger("stock.auth")
        with mock.patch("stock.system_log.time.monotonic", return_value=1000.0):
            auth.warning("Failed login: someone")
            auth.warning("Failed login: someone")
            self.log.error("Broken twice")
            self.log.error("Broken twice")
        self.assertEqual(ErrorLog.objects.get(title="Failed login: someone").count, 2)
        self.assertEqual(ErrorLog.objects.get(title="Broken twice").count, 2)
