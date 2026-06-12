import csv
import json
from io import StringIO
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import ProductForm
from .models import ARInvoice, ARPayment, AttendanceRecord, Brand, Category, Customer, DailySalesSummary, InboundOrder, Product, ProductSeries, Purchase, Sale, SaleOrder, SaleOrderChangeLog, Supplier


class SummaryRebuildTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Perfume")
        cls.customer = Customer.objects.create(nif="123456789", name="Alice")
        cls.product = Product.objects.create(
            name="Rose",
            barcode="1234567890123",
            brand="Maison",
            category=cls.category,
            default_price=Decimal("15.00"),
        )

    def create_purchase(self, quantity=10, cost_price=Decimal("10.00")):
        return Purchase.objects.create(
            product=self.product,
            supplier=None,
            quantity=quantity,
            cost_price=cost_price,
            remaining=quantity,
        )

    def create_sale(self, quantity=2, unit_price=Decimal("15.00")):
        order = SaleOrder.objects.create(customer=self.customer)
        with self.captureOnCommitCallbacks(execute=True):
            sale = Sale.objects.create(
                order=order,
                product=self.product,
                customer=self.customer,
                quantity=quantity,
                unit_price=unit_price,
                payment_method="cash",
            )
        sale.refresh_from_db()
        return sale

    def test_sale_create_rebuilds_daily_summary(self):
        self.create_purchase()
        sale = self.create_sale()

        summary = DailySalesSummary.objects.get(date=sale.date.date())
        self.assertEqual(summary.total_sales, Decimal("30.00"))
        self.assertEqual(summary.total_profit, Decimal("10.00"))
        self.assertEqual(summary.total_items_sold, 2)

    def test_moving_sale_to_another_day_rebuilds_both_days(self):
        purchase = self.create_purchase()
        sale = self.create_sale()
        original_day = sale.date.date()

        moved_sale_dt = sale.date - timedelta(days=1)
        Purchase.objects.filter(pk=purchase.pk).update(date=moved_sale_dt - timedelta(hours=1))

        sale.date = moved_sale_dt
        with self.captureOnCommitCallbacks(execute=True):
            sale.save(update_fields=["date"])

        self.assertFalse(DailySalesSummary.objects.filter(date=original_day).exists())
        moved_summary = DailySalesSummary.objects.get(date=moved_sale_dt.date())
        self.assertEqual(moved_summary.total_sales, Decimal("30.00"))
        self.assertEqual(moved_summary.total_items_sold, 2)

    def test_deleting_last_sale_removes_summary_row(self):
        self.create_purchase()
        sale = self.create_sale()
        day = sale.date.date()

        with self.captureOnCommitCallbacks(execute=True):
            sale.delete()

        self.assertFalse(DailySalesSummary.objects.filter(date=day).exists())


class WorkflowRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(username="cashier", password="pw123456")
        cls.staff_user = user_model.objects.create_user(username="manager", password="pw123456", is_staff=True)

        cls.category = Category.objects.create(name="Attar")
        cls.customer = Customer.objects.create(nif="987654321", name="Bob")
        cls.product = Product.objects.create(
            name="Amber",
            barcode="9876543210123",
            brand="Maison",
            category=cls.category,
            default_price=Decimal("18.00"),
        )
        cls.purchase = Purchase.objects.create(
            product=cls.product,
            supplier=None,
            quantity=5,
            cost_price=Decimal("10.00"),
            remaining=5,
        )

    def setUp(self):
        cache.clear()

    def test_outbound_summary_matches_sales_exactly(self):
        self.client.login(username="cashier", password="pw123456")

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("outbound"),
                {
                    "customer_id": str(self.customer.id),
                    "items_json": json.dumps(
                        [
                            {
                                "barcode": self.product.barcode,
                                "qty": 2,
                                "price": "15.00",
                                "payment": "cash",
                            }
                        ]
                    ),
                },
            )

        self.assertEqual(response.status_code, 200)
        sale = Sale.objects.get()
        summary = DailySalesSummary.objects.get(date=sale.date.date())
        self.assertEqual(summary.total_sales, Decimal("30.00"))
        self.assertEqual(summary.total_profit, Decimal("10.00"))
        self.assertEqual(summary.total_items_sold, 2)

    def test_customer_lookup_requires_login(self):
        response = self.client.get(reverse("check_customer"), {"q": "Bob"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_add_customer_requires_csrf_but_still_works_with_token(self):
        client = Client(enforce_csrf_checks=True)
        client.login(username="cashier", password="pw123456")

        blocked = client.post(reverse("add_customer_ajax"), {"nif": "111222333", "name": "Carol"})
        self.assertEqual(blocked.status_code, 403)

        page = client.get(reverse("customer_search"))
        csrf_token = page.cookies["csrftoken"].value
        allowed = client.post(
            reverse("add_customer_ajax"),
            {"nif": "111222333", "name": "Carol"},
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(allowed.json()["success"])

    def test_only_staff_can_adjust_purchase_stock(self):
        denied_client = Client()
        denied_client.login(username="cashier", password="pw123456")
        denied = denied_client.post(
            reverse("api_adjust_purchase_stock"),
            data=json.dumps({"purchase_id": self.purchase.id, "new_remaining": 4}),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 403)

        staff_client = Client()
        staff_client.login(username="manager", password="pw123456")
        allowed = staff_client.post(
            reverse("api_adjust_purchase_stock"),
            data=json.dumps({"purchase_id": self.purchase.id, "new_remaining": 4}),
            content_type="application/json",
        )

        self.assertEqual(allowed.status_code, 200)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.remaining, 4)


class InventoryAdjustmentSyncTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.staff_user = user_model.objects.create_user(username="stock_manager", password="pw123456", is_staff=True)
        cls.admin = user_model.objects.create_superuser(username="stock_admin", password="pw123456")
        cls.category = Category.objects.create(name="Perfumes")
        cls.product = Product.objects.create(
            name="Sync Test",
            barcode="4445556667778",
            brand="Lattafa",
            category=cls.category,
            default_price=Decimal("32.00"),
        )
        cls.purchase = Purchase.objects.create(
            product=cls.product,
            supplier=None,
            quantity=10,
            cost_price=Decimal("9.50"),
            remaining=10,
        )

    def test_decreasing_purchase_remaining_returns_recomputed_inventory_snapshot(self):
        self.client.login(username="stock_manager", password="pw123456")

        response = self.client.post(
            reverse("api_adjust_purchase_stock"),
            data=json.dumps({"purchase_id": self.purchase.id, "new_remaining": 4}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["inventory_snapshot"]["product_total_stock"], 4)
        self.assertEqual(payload["inventory_snapshot"]["perfume_stock_units"], 4)
        self.assertEqual(payload["inventory_snapshot"]["perfume_stock_cost"], "38.00")

        self.client.login(username="stock_admin", password="pw123456")
        dashboard_response = self.client.get(reverse("dashboard"), {"month": timezone.now().strftime("%Y-%m")})
        self.assertEqual(dashboard_response.context["perfume_stock_units"], 4)
        self.assertEqual(dashboard_response.context["perfume_stock_cost"], Decimal("38.00"))

    def test_decreasing_total_stock_preserves_purchase_row_and_recomputes_snapshot(self):
        self.client.login(username="stock_manager", password="pw123456")

        response = self.client.post(
            reverse("api_adjust_total_stock"),
            data=json.dumps({"product_id": self.product.id, "new_total_stock": 0}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["inventory_snapshot"]["product_total_stock"], 0)
        self.assertTrue(Purchase.objects.filter(pk=self.purchase.pk).exists())
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.remaining, 0)


class SalesRecordsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(username="records_user", password="pw123456")
        cls.admin = user_model.objects.create_superuser(username="records_admin", password="pw123456")

        cls.category = Category.objects.create(name="Record Test")
        cls.customer = Customer.objects.create(nif="222333444", name="Record Customer")
        cls.product = Product.objects.create(
            name="Musk",
            barcode="5556667778881",
            brand="Maison",
            category=cls.category,
            default_price=Decimal("18.00"),
        )

        cls.sale_order = SaleOrder.objects.create(customer=cls.customer)
        cls.sale = Sale.objects.create(
            order=cls.sale_order,
            product=cls.product,
            customer=cls.customer,
            quantity=2,
            unit_price=Decimal("15.00"),
            payment_method="cash",
        )

        cls.inbound_order = InboundOrder.objects.create(invoice_no="INV-1001")
        cls.purchase = Purchase.objects.create(
            inbound_order=cls.inbound_order,
            product=cls.product,
            supplier=None,
            quantity=3,
            cost_price=Decimal("4.00"),
            remaining=3,
        )
        cls.standalone_purchase = Purchase.objects.create(
            inbound_order=None,
            product=cls.product,
            supplier=None,
            quantity=2,
            cost_price=Decimal("5.00"),
            remaining=2,
        )

    def test_sales_records_page_renders_sales_and_purchase_totals(self):
        self.client.login(username="records_admin", password="pw123456")
        today = timezone.localdate()

        response = self.client.get(
            reverse("sales_records"),
            {"start_date": today.isoformat(), "end_date": today.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_sales_amount"], Decimal("30.00"))
        self.assertEqual(response.context["total_sales_orders"], 1)
        self.assertEqual(response.context["total_purchase_amount"], Decimal("22.00"))
        self.assertEqual(response.context["total_purchase_orders"], 2)
        self.assertContains(response, "Recent Searches")
        self.assertContains(response, "Sales Orders")
        self.assertContains(response, "No supplier linked")
        self.assertContains(response, "Purchases")
        self.assertContains(response, reverse("customer_detail", args=[self.customer.id]))
        self.assertTrue(response.context["show_profit"])
        self.assertContains(response, "Net Profit")

    def test_sales_records_shows_profit_for_admin_only(self):
        self.client.login(username="records_admin", password="pw123456")
        today = timezone.localdate()

        response = self.client.get(
            reverse("sales_records"),
            {"start_date": today.isoformat(), "end_date": today.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["show_profit"])
        self.assertEqual(response.context["total_sales_profit"], Decimal("30.00"))
        self.assertContains(response, "Net Profit")

    def test_sales_records_hides_purchase_data_for_regular_user(self):
        self.client.login(username="records_user", password="pw123456")
        today = timezone.localdate()

        response = self.client.get(
            reverse("sales_records"),
            {"start_date": today.isoformat(), "end_date": today.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["show_purchases"])
        self.assertFalse(response.context["show_sales_sensitive"])
        self.assertTrue(response.context["show_order_financials"])
        self.assertFalse(response.context["show_profit"])
        self.assertNotContains(response, "Purchase Cost")
        self.assertNotContains(response, "Inbound Purchases")
        self.assertContains(response, "Sales Amount")
        self.assertContains(response, "Payment Split")

    def test_sales_records_groups_corrected_order_by_order_datetime(self):
        self.client.login(username="records_admin", password="pw123456")

        drift_order = SaleOrder.objects.create(customer=self.customer)
        order_dt = timezone.now() - timedelta(days=1)
        SaleOrder.objects.filter(pk=drift_order.pk).update(created_at=order_dt)
        drift_sale = Sale.objects.create(
            order=drift_order,
            product=self.product,
            customer=self.customer,
            quantity=1,
            unit_price=Decimal("16.00"),
            payment_method="card",
        )
        Sale.objects.filter(pk=drift_sale.pk).update(date=timezone.now())

        response = self.client.get(
            reverse("sales_records"),
            {
                "start_date": timezone.localdate(order_dt).isoformat(),
                "end_date": timezone.localdate().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        day_map = {
            block["date"]: [row["order_id"] for row in block["orders"]]
            for block in response.context["day_blocks"]
        }
        self.assertIn(drift_order.id, day_map[timezone.localdate(order_dt)])
        self.assertNotIn(drift_order.id, day_map.get(timezone.localdate(), []))


class CustomerDetailViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(username="customer_timeline_user", password="pw123456")
        cls.admin = user_model.objects.create_superuser(username="customer_timeline_admin", password="pw123456")

        cls.category = Category.objects.create(name="Timeline Test")
        cls.customer = Customer.objects.create(nif="333444555", name="Timeline Customer")
        cls.product = Product.objects.create(
            name="Oud",
            barcode="4445556667778",
            brand="Atelier",
            category=cls.category,
            default_price=Decimal("20.00"),
        )

        cls.older_order = SaleOrder.objects.create(customer=cls.customer)
        cls.older_sale = Sale.objects.create(
            order=cls.older_order,
            product=cls.product,
            customer=cls.customer,
            quantity=1,
            unit_price=Decimal("20.00"),
            payment_method="cash",
        )
        older_dt = timezone.now() - timedelta(days=40)
        SaleOrder.objects.filter(pk=cls.older_order.pk).update(created_at=older_dt)
        Sale.objects.filter(pk=cls.older_sale.pk).update(date=older_dt)

        cls.newer_order = SaleOrder.objects.create(customer=cls.customer)
        cls.newer_sale = Sale.objects.create(
            order=cls.newer_order,
            product=cls.product,
            customer=cls.customer,
            quantity=2,
            unit_price=Decimal("22.00"),
            payment_method="card",
        )
        newer_dt = timezone.now() - timedelta(days=2)
        SaleOrder.objects.filter(pk=cls.newer_order.pk).update(created_at=newer_dt)
        Sale.objects.filter(pk=cls.newer_sale.pk).update(date=newer_dt)

    def test_customer_detail_groups_orders_by_month_day_and_time(self):
        self.client.login(username="customer_timeline_admin", password="pw123456")

        response = self.client.get(reverse("customer_detail", args=[self.customer.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_orders"], 2)
        self.assertEqual(len(response.context["month_blocks"]), 2)
        self.assertContains(response, "Order Timeline")
        self.assertContains(response, "Timeline Customer")
        self.assertContains(response, "Order #")
        self.assertTrue(response.context["show_profit"])
        self.assertContains(response, "Net Profit")

    def test_customer_detail_shows_profit_for_admin_only(self):
        self.client.login(username="customer_timeline_admin", password="pw123456")

        response = self.client.get(reverse("customer_detail", args=[self.customer.id]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["show_profit"])
        self.assertEqual(response.context["total_profit"], Decimal("64.00"))
        self.assertContains(response, "Net Profit")

    def test_customer_detail_hides_sensitive_sections_for_regular_user(self):
        self.client.login(username="customer_timeline_user", password="pw123456")

        response = self.client.get(reverse("customer_detail", args=[self.customer.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Timeline Customer")
        self.assertNotContains(response, "NIF")
        self.assertNotContains(response, "Phone")
        self.assertNotContains(response, "Email")
        self.assertNotContains(response, "New IOU")
        self.assertNotContains(response, "Confirm Deletion")
        self.assertNotContains(response, "IOU / AR Overview")


class DashboardViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(username="dashboard_user", password="pw123456")
        cls.admin = user_model.objects.create_superuser(username="dashboard_admin", password="pw123456")

        cls.category = Category.objects.create(name="Dashboard Test")
        cls.customer = Customer.objects.create(nif="777888999", name="Dashboard Customer")
        cls.product = Product.objects.create(
            name="Saffron",
            barcode="1112223334445",
            brand="Maison",
            category=cls.category,
            default_price=Decimal("15.00"),
        )
        cls.slow_product = Product.objects.create(
            name="Oud Reserve",
            barcode="1112223334446",
            brand="Maison",
            category=cls.category,
            default_price=Decimal("32.00"),
        )

        current_dt = timezone.now()
        previous_dt = current_dt - timedelta(days=40)

        current_purchase = Purchase.objects.create(
            product=cls.product,
            supplier=None,
            quantity=5,
            cost_price=Decimal("8.00"),
            remaining=5,
        )
        Purchase.objects.filter(pk=current_purchase.pk).update(date=current_dt - timedelta(hours=1))

        previous_purchase = Purchase.objects.create(
            product=cls.product,
            supplier=None,
            quantity=3,
            cost_price=Decimal("7.00"),
            remaining=3,
        )
        Purchase.objects.filter(pk=previous_purchase.pk).update(date=previous_dt - timedelta(hours=1))

        slow_purchase = Purchase.objects.create(
            product=cls.slow_product,
            supplier=None,
            quantity=9,
            cost_price=Decimal("12.00"),
            remaining=9,
        )
        Purchase.objects.filter(pk=slow_purchase.pk).update(date=previous_dt - timedelta(days=15))

        cls.current_order = SaleOrder.objects.create(customer=cls.customer)
        cls.current_sale = Sale.objects.create(
            order=cls.current_order,
            product=cls.product,
            customer=cls.customer,
            quantity=2,
            unit_price=Decimal("15.00"),
            payment_method="cash",
        )
        SaleOrder.objects.filter(pk=cls.current_order.pk).update(created_at=current_dt)
        Sale.objects.filter(pk=cls.current_sale.pk).update(date=current_dt)

        cls.previous_order = SaleOrder.objects.create(customer=cls.customer)
        cls.previous_sale = Sale.objects.create(
            order=cls.previous_order,
            product=cls.product,
            customer=cls.customer,
            quantity=1,
            unit_price=Decimal("14.00"),
            payment_method="card",
        )
        SaleOrder.objects.filter(pk=cls.previous_order.pk).update(created_at=previous_dt)
        Sale.objects.filter(pk=cls.previous_sale.pk).update(date=previous_dt)

        cls.invoice = ARInvoice.objects.create(customer=cls.customer, total_amount=Decimal("50.00"), amount_paid=Decimal("10.00"))
        ARInvoice.objects.filter(pk=cls.invoice.pk).update(date=current_dt.date(), created_at=current_dt)
        cls.payment = ARPayment.objects.create(invoice=cls.invoice, amount=Decimal("10.00"), method="cash")
        ARPayment.objects.filter(pk=cls.payment.pk).update(created_at=current_dt)

        cls.current_month = current_dt.strftime("%Y-%m")

    def test_dashboard_hides_sensitive_sections_for_regular_user(self):
        self.client.login(username="dashboard_user", password="pw123456")

        response = self.client.get(reverse("dashboard"), {"month": self.current_month})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["month_sales_amount"], Decimal("30.00"))
        self.assertEqual(response.context["month_order_count"], 1)
        self.assertEqual(response.context["month_receivables_added"], Decimal("50.00"))
        self.assertEqual(response.context["month_receipts_collected"], Decimal("10.00"))
        self.assertFalse(response.context["show_profit"])
        self.assertNotContains(response, "Employee view")
        self.assertNotContains(response, "Today only")
        self.assertNotContains(response, "Monthly overview")
        self.assertNotContains(response, "Top customers")
        self.assertNotContains(response, "Gross Profit")
        self.assertContains(response, "Sales Today")
        self.assertContains(response, "EUR 30.00")
        self.assertContains(response, "Amount")
        self.assertContains(response, "Unit")
        self.assertContains(response, "Operations")
        self.assertContains(response, "Sales & Clients")
        self.assertContains(response, reverse("sales_records"))
        self.assertContains(response, reverse("customer_search"))

    def test_dashboard_shows_admin_only_profit_and_purchase_metrics(self):
        self.client.login(username="dashboard_admin", password="pw123456")

        response = self.client.get(reverse("dashboard"), {"month": self.current_month})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["show_profit"])
        self.assertTrue(response.context["show_purchase_metrics"])
        self.assertEqual(response.context["month_sales_profit"], Decimal("16.00"))
        self.assertEqual(response.context["month_purchase_amount"], Decimal("40.00"))
        self.assertNotContains(response, "Monthly overview")
        self.assertContains(response, "Sales trend")
        self.assertContains(response, "Gross Profit")
        self.assertContains(response, "Purchase Spend")
        self.assertContains(response, "Capital lock and slow movers")
        self.assertContains(response, "Most capital locked stock")
        self.assertContains(response, "Slow movers")
        self.assertContains(response, "Operator insights")
        self.assertContains(response, "Oud Reserve")

    def test_regular_user_can_open_pages_but_not_sensitive_views(self):
        self.client.login(username="dashboard_user", password="pw123456")

        self.assertEqual(self.client.get(reverse("sales_records")).status_code, 200)
        self.assertEqual(self.client.get(reverse("customer_search")).status_code, 200)
        self.assertEqual(self.client.get(reverse("product_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("ar_list")).status_code, 200)

    def test_regular_user_ar_pages_hide_financial_details(self):
        self.client.login(username="dashboard_user", password="pw123456")

        list_response = self.client.get(reverse("ar_list"))
        detail_response = self.client.get(reverse("ar_detail", args=[self.invoice.id]))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)
        self.assertNotContains(list_response, "Total Balance")
        self.assertNotContains(list_response, "New IOU")
        self.assertContains(list_response, "Customer IOUs")
        self.assertContains(detail_response, "Invoice")
        self.assertNotContains(detail_response, "Record a Payment")

    def test_regular_user_can_open_any_order_detail_without_customer_contacts(self):
        self.client.login(username="dashboard_user", password="pw123456")

        today_response = self.client.get(reverse("sale_order_detail", args=[self.current_order.id]))
        old_response = self.client.get(reverse("sale_order_detail", args=[self.previous_order.id]))

        self.assertEqual(today_response.status_code, 200)
        self.assertEqual(old_response.status_code, 200)
        self.assertNotContains(old_response, "NIF:")
        self.assertNotContains(old_response, "Email:")
        self.assertNotContains(old_response, "Order amounts, prices, and payment details are hidden for employee accounts.")
        self.assertContains(old_response, "Grand Total")
        self.assertContains(old_response, "EUR 14.00")
        self.assertContains(old_response, "Card")

    def test_regular_user_can_open_printable_sale_order(self):
        self.client.login(username="dashboard_user", password="pw123456")

        detail_response = self.client.get(reverse("sale_order_detail", args=[self.previous_order.id]))
        print_response = self.client.get(
            reverse("sale_order_detail", args=[self.previous_order.id]),
            {"print": "1", "layout": "a4"},
        )

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(print_response.status_code, 200)
        self.assertContains(detail_response, "Print A4")
        self.assertContains(detail_response, "Print POS (48mm)")
        self.assertContains(detail_response, "Grand Total")
        self.assertContains(print_response, "Grand Total")
        self.assertContains(print_response, "EUR 14.00")

    def test_dashboard_uses_order_datetime_for_today_bucket(self):
        drift_order = SaleOrder.objects.create(customer=self.customer)
        order_dt = timezone.now() - timedelta(days=1)
        SaleOrder.objects.filter(pk=drift_order.pk).update(created_at=order_dt)
        drift_sale = Sale.objects.create(
            order=drift_order,
            product=self.product,
            customer=self.customer,
            quantity=2,
            unit_price=Decimal("15.00"),
            payment_method="cash",
        )
        Sale.objects.filter(pk=drift_sale.pk).update(date=timezone.now())

        self.client.login(username="dashboard_admin", password="pw123456")
        response = self.client.get(reverse("dashboard"), {"month": self.current_month})

        self.assertEqual(response.status_code, 200)
        today_order_ids = [order.id for order in response.context["sale_orders_today"]]
        self.assertNotIn(drift_order.id, today_order_ids)


class ProductArchitectureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_superuser(username="product_admin", password="pw123456")
        cls.employee = user_model.objects.create_user(username="product_employee", password="pw123456")
        cls.category = Category.objects.create(name="Structured Product")
        cls.other_category = Category.objects.create(name="Phone Accessory")

    def test_product_form_can_create_brand_series_variant_and_prices(self):
        form = ProductForm(data={
            "barcode": "3216549870123",
            "category": self.category.id,
            "new_brand_name": "Baseus",
            "new_series_name": "iPhone 15 Pro",
            "name": "Clear Case",
            "spec": "MagSafe",
            "color": "Black",
            "default_price": "19.90",
            "wholesale_price": "11.40",
            "description": "Transparent case",
        })

        self.assertTrue(form.is_valid(), form.errors)
        product = form.save()

        self.assertEqual(product.brand, "Baseus")
        self.assertEqual(product.model, "iPhone 15 Pro")
        self.assertEqual(product.display_name, "Baseus - iPhone 15 Pro - Clear Case MagSafe Black")
        self.assertEqual(product.default_price, Decimal("19.90"))
        self.assertEqual(product.wholesale_price, Decimal("11.40"))
        self.assertEqual(product.brand_master.name, "Baseus")
        self.assertEqual(product.series_master.name, "iPhone 15 Pro")
        self.assertTrue(Brand.objects.filter(name="Baseus").exists())
        self.assertTrue(ProductSeries.objects.filter(name="iPhone 15 Pro", brand__name="Baseus").exists())
        self.assertTrue(product.brand_master.categories.filter(id=self.category.id).exists())

    def test_product_form_filters_brands_by_selected_category(self):
        perfume_brand = Brand.objects.create(name="Lattafa")
        perfume_brand.categories.add(self.category)
        accessory_brand = Brand.objects.create(name="Ugreen")
        accessory_brand.categories.add(self.other_category)

        form = ProductForm(data={"category": self.category.id})

        self.assertIn(perfume_brand, form.fields["brand_master"].queryset)
        self.assertNotIn(accessory_brand, form.fields["brand_master"].queryset)

    def test_existing_brand_can_be_linked_to_another_category_by_saving_product(self):
        brand = Brand.objects.create(name="Baseus")
        brand.categories.add(self.other_category)

        form = ProductForm(data={
            "barcode": "1231231231234",
            "category": self.category.id,
            "brand_master": brand.id,
            "name": "Car Charger",
            "default_price": "14.50",
        })

        self.assertTrue(form.is_valid(), form.errors)
        product = form.save()

        self.assertEqual(product.brand_master, brand)
        self.assertTrue(brand.categories.filter(id=self.category.id).exists())

    def test_add_product_view_accepts_new_brand_when_brand_select_is_blank(self):
        self.client.login(username="product_admin", password="pw123456")

        response = self.client.post(reverse("add_product"), data={
            "barcode": "5556667778881",
            "category": self.category.id,
            "brand_master": "",
            "new_brand_name": "Afnan",
            "series_master": "",
            "new_series_name": "9PM",
            "name": "EDP",
            "spec": "100ml",
            "color": "",
            "default_price": "39.90",
            "wholesale_price": "26.50",
            "description": "Evening fragrance",
        })

        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(barcode="5556667778881")
        self.assertEqual(product.brand_master.name, "Afnan")
        self.assertEqual(product.series_master.name, "9PM")
        self.assertEqual(product.brand, "Afnan")
        self.assertEqual(product.model, "9PM")

    def test_products_autocomplete_supports_product_id_lookup(self):
        brand = Brand.objects.create(name="Lattafa")
        series = ProductSeries.objects.create(brand=brand, name="Asad")
        product = Product.objects.create(
            name="Elixir",
            barcode="1122334455667",
            brand="Lattafa",
            brand_master=brand,
            series_master=series,
            model="Asad",
            category=self.category,
            default_price=Decimal("39.90"),
        )

        self.client.login(username="product_employee", password="pw123456")
        response = self.client.get(reverse("products_autocomplete"), {"product_id": product.id})

        self.assertEqual(response.status_code, 200)
        payload = response.json()["results"]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["id"], product.id)
        self.assertEqual(payload[0]["display_name"], product.display_name)

    def test_product_detail_shows_structured_product_fields_for_manager(self):
        brand = Brand.objects.create(name="Dior")
        series = ProductSeries.objects.create(brand=brand, name="Sauvage")
        product = Product.objects.create(
            name="EDP",
            barcode="8887776665554",
            brand="Dior",
            brand_master=brand,
            series_master=series,
            model="Sauvage",
            category=self.category,
            spec="100ml",
            color="Blue",
            default_price=Decimal("99.00"),
            wholesale_price=Decimal("72.00"),
        )

        self.client.login(username="product_admin", password="pw123456")
        response = self.client.get(reverse("product_detail", args=[product.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dior - Sauvage - EDP 100ml Blue")
        self.assertContains(response, "Wholesale Price")
        self.assertContains(response, "Spec: 100ml")
        self.assertContains(response, "Color: Blue")

    def test_product_detail_shows_prices_but_hides_sales_history_for_regular_user(self):
        brand = Brand.objects.create(name="Anker")
        series = ProductSeries.objects.create(brand=brand, name="USB-C")
        product = Product.objects.create(
            name="Cable",
            barcode="9998887776665",
            brand="Anker",
            brand_master=brand,
            series_master=series,
            model="USB-C",
            category=self.category,
            default_price=Decimal("12.50"),
            wholesale_price=Decimal("8.00"),
        )

        self.client.login(username="product_employee", password="pw123456")
        response = self.client.get(reverse("product_detail", args=[product.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Anker - USB-C - Cable")
        self.assertContains(response, "Retail Price")
        self.assertContains(response, "Wholesale Price")
        self.assertNotContains(response, "Sales history is hidden for employee accounts.")

    def test_product_list_can_filter_by_brand(self):
        perfume_brand = Brand.objects.create(name="Lattafa")
        perfume_brand.categories.add(self.category)
        other_brand = Brand.objects.create(name="Baseus")
        other_brand.categories.add(self.category)

        perfume_product = Product.objects.create(
            name="Asad",
            barcode="1234509876501",
            brand="Lattafa",
            brand_master=perfume_brand,
            category=self.category,
            default_price=Decimal("39.90"),
        )
        Product.objects.create(
            name="Charger",
            barcode="1234509876502",
            brand="Baseus",
            brand_master=other_brand,
            category=self.category,
            default_price=Decimal("19.90"),
        )

        self.client.login(username="product_employee", password="pw123456")
        response = self.client.get(reverse("product_list"), {
            "category": self.category.id,
            "brand": perfume_brand.id,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, perfume_product.display_name)
        self.assertNotContains(response, "Baseus - Charger")

    def test_product_list_can_sort_by_sales_quantity(self):
        best_seller = Product.objects.create(
            name="Winner",
            barcode="1234509876503",
            brand="Maison",
            category=self.category,
            default_price=Decimal("25.00"),
        )
        slower = Product.objects.create(
            name="Slower",
            barcode="1234509876504",
            brand="Maison",
            category=self.category,
            default_price=Decimal("20.00"),
        )
        order = SaleOrder.objects.create()
        Sale.objects.create(order=order, product=slower, quantity=1, unit_price=Decimal("20.00"), payment_method="cash")
        Sale.objects.create(order=order, product=best_seller, quantity=5, unit_price=Decimal("25.00"), payment_method="cash")

        self.client.login(username="product_admin", password="pw123456")
        response = self.client.get(reverse("product_list"), {"sort": "sales_desc"})

        self.assertEqual(response.status_code, 200)
        product_ids = [product.id for product in response.context["page_obj"].object_list[:2]]
        self.assertEqual(product_ids[0], best_seller.id)

    def test_product_list_hides_sales_metrics_but_keeps_prices_for_regular_user(self):
        product = Product.objects.create(
            name="Visible Price",
            barcode="1234509876510",
            brand="Maison",
            category=self.category,
            default_price=Decimal("29.00"),
            wholesale_price=Decimal("17.00"),
        )
        order = SaleOrder.objects.create()
        Sale.objects.create(order=order, product=product, quantity=4, unit_price=Decimal("29.00"), payment_method="cash")

        self.client.login(username="product_employee", password="pw123456")
        response = self.client.get(reverse("product_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Retail / Wholesale")
        self.assertContains(response, "EUR 29.00")
        self.assertContains(response, "EUR 17.00")
        self.assertNotContains(response, "Sales activity is hidden for employee accounts.")
        self.assertNotContains(response, "Best selling")

    def test_product_list_shows_shopify_export_for_manager(self):
        self.client.login(username="product_admin", password="pw123456")

        response = self.client.get(reverse("product_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Export For Shopify")
        self.assertContains(response, reverse("export_shopify_inventory_csv"))
        self.assertContains(response, "Export Shopify Product CSV")
        self.assertContains(response, "Only in stock")

    def test_shopify_export_matches_product_template_and_uses_current_stock(self):
        shopify_category = Category.objects.create(name="Perfumes")
        brand = Brand.objects.create(name="Lattafa")
        brand.categories.add(shopify_category)
        series = ProductSeries.objects.create(brand=brand, name="Asad")
        first = Product.objects.create(
            name="EDP",
            barcode="1234509876511",
            brand="Lattafa",
            brand_master=brand,
            series_master=series,
            model="Asad",
            category=shopify_category,
            spec="100ml",
            color="Blue",
            description="Evening fragrance",
            default_price=Decimal("39.90"),
        )
        second = Product.objects.create(
            name="EDP",
            barcode="1234509876512",
            brand="Lattafa",
            brand_master=brand,
            series_master=series,
            model="Asad",
            category=shopify_category,
            spec="50ml",
            color="Blue",
            default_price=Decimal("29.90"),
        )
        Purchase.objects.create(product=first, quantity=8, remaining=6, cost_price=Decimal("12.34"))
        Purchase.objects.create(product=second, quantity=3, remaining=0, cost_price=Decimal("11.11"))

        self.client.login(username="product_admin", password="pw123456")
        response = self.client.get(reverse("export_shopify_inventory_csv"), {
            "brand": brand.id,
            "sort": "name_asc",
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn("Shopify_Product_Inventory_", response["Content-Disposition"])
        reader = csv.DictReader(StringIO(response.content.decode("utf-8-sig")))
        self.assertEqual(reader.fieldnames, [
            "Title",
            "URL handle",
            "Description",
            "Vendor",
            "Product category",
            "Type",
            "Tags",
            "Published on online store",
            "Status",
            "SKU",
            "Barcode",
            "Option1 name",
            "Option1 value",
            "Option1 Linked To",
            "Option2 name",
            "Option2 value",
            "Option2 Linked To",
            "Option3 name",
            "Option3 value",
            "Option3 Linked To",
            "Price",
            "Compare-at price",
            "Cost per item",
            "Charge tax",
            "Tax code",
            "Unit price total measure",
            "Unit price total measure unit",
            "Unit price base measure",
            "Unit price base measure unit",
            "Inventory tracker",
            "Inventory quantity",
            "Continue selling when out of stock",
            "Weight value (grams)",
            "Weight unit for display",
            "Requires shipping",
            "Fulfillment service",
            "Product image URL",
            "Image position",
            "Image alt text",
            "Variant image URL",
            "Gift card",
            "SEO title",
            "SEO description",
            "Color (product.metafields.shopify.color-pattern)",
            "Google Shopping / Google product category",
            "Google Shopping / Gender",
            "Google Shopping / Age group",
            "Google Shopping / Manufacturer part number (MPN)",
            "Google Shopping / Ad group name",
            "Google Shopping / Ads labels",
            "Google Shopping / Condition",
            "Google Shopping / Custom product",
            "Google Shopping / Custom label 0",
            "Google Shopping / Custom label 1",
            "Google Shopping / Custom label 2",
            "Google Shopping / Custom label 3",
            "Google Shopping / Custom label 4",
        ])
        rows = list(reader)
        self.assertEqual(len(rows), 2)

        self.assertEqual(rows[0]["Title"], "Lattafa - Asad - EDP")
        self.assertEqual(rows[0]["URL handle"], "lattafa-asad-edp")
        self.assertEqual(rows[0]["Description"], "Evening fragrance")
        self.assertEqual(rows[0]["Vendor"], "Lattafa")
        self.assertEqual(rows[0]["Product category"], "Perfumes")
        self.assertEqual(rows[0]["Type"], "Perfumes")
        self.assertEqual(rows[0]["Published on online store"], "TRUE")
        self.assertEqual(rows[0]["Status"], "Active")
        self.assertEqual(rows[0]["SKU"], first.barcode)
        self.assertEqual(rows[0]["Barcode"], first.barcode)
        self.assertEqual(rows[0]["Option1 name"], "Spec")
        self.assertEqual(rows[0]["Option1 value"], "100ml")
        self.assertEqual(rows[0]["Option2 name"], "Color")
        self.assertEqual(rows[0]["Option2 value"], "Blue")
        self.assertEqual(rows[0]["Option2 Linked To"], "product.metafields.shopify.color-pattern")
        self.assertEqual(rows[0]["Price"], "39.90")
        self.assertEqual(rows[0]["Cost per item"], "12.34")
        self.assertEqual(rows[0]["Charge tax"], "TRUE")
        self.assertEqual(rows[0]["Inventory tracker"], "shopify")
        self.assertEqual(rows[0]["Inventory quantity"], "6")
        self.assertEqual(rows[0]["Continue selling when out of stock"], "DENY")
        self.assertEqual(rows[0]["Requires shipping"], "TRUE")
        self.assertEqual(rows[0]["Fulfillment service"], "manual")
        self.assertEqual(rows[0]["Gift card"], "FALSE")
        self.assertEqual(rows[0]["SEO title"], "Lattafa - Asad - EDP")
        self.assertEqual(rows[0]["Color (product.metafields.shopify.color-pattern)"], "Blue")
        self.assertEqual(rows[0]["Google Shopping / Condition"], "New")
        self.assertEqual(rows[0]["Google Shopping / Custom product"], "FALSE")

        self.assertEqual(rows[1]["Title"], "")
        self.assertEqual(rows[1]["URL handle"], rows[0]["URL handle"])
        self.assertEqual(rows[1]["SKU"], second.barcode)
        self.assertEqual(rows[1]["Barcode"], second.barcode)
        self.assertEqual(rows[1]["Option1 value"], "50ml")
        self.assertEqual(rows[1]["Price"], "29.90")
        self.assertEqual(rows[1]["Cost per item"], "11.11")
        self.assertEqual(rows[1]["Inventory quantity"], "0")


class InboundOutboundPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(username="ops_employee", password="pw123456")

    def test_outbound_page_renders_review_guardrails(self):
        self.client.login(username="ops_employee", password="pw123456")

        response = self.client.get(reverse("outbound"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Review and Confirm Sale")
        self.assertContains(response, "Final Sale Review")
        self.assertContains(response, "Guardrails")

    def test_inbound_page_renders_review_guardrails(self):
        self.client.login(username="ops_employee", password="pw123456")

        response = self.client.get(reverse("inbound"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Review and Confirm Inbound")
        self.assertContains(response, "Final Inbound Review")
        self.assertContains(response, "Guardrails")


class SaleOrderCorrectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin = user_model.objects.create_superuser(username="order_fix_admin", password="pw123456")
        cls.employee = user_model.objects.create_user(username="order_fix_employee", password="pw123456")

        cls.category = Category.objects.create(name="Correction Perfume")
        cls.customer = Customer.objects.create(nif="987654321", name="Correction Customer")
        cls.product = Product.objects.create(
            name="Asad",
            barcode="1119991119991",
            brand="Lattafa",
            category=cls.category,
            default_price=Decimal("25.00"),
        )

    def test_admin_can_open_order_correction_center(self):
        self.client.login(username="order_fix_admin", password="pw123456")

        response = self.client.get(reverse("sale_order_correction_center"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Order Correction Center")
        self.assertContains(response, "Add missing historical order")

    def test_admin_can_open_order_correction_form_pages(self):
        purchase = Purchase.objects.create(
            product=self.product,
            supplier=None,
            quantity=5,
            cost_price=Decimal("10.00"),
            remaining=5,
        )
        purchase_dt = timezone.now() - timedelta(days=1)
        Purchase.objects.filter(pk=purchase.pk).update(date=purchase_dt)

        order = SaleOrder.objects.create(customer=self.customer, note="Editable order")
        SaleOrder.objects.filter(pk=order.pk).update(created_at=purchase_dt + timedelta(hours=1))
        sale = Sale.objects.create(
            order=order,
            product=self.product,
            customer=self.customer,
            quantity=1,
            unit_price=Decimal("25.00"),
            payment_method="cash",
        )
        Sale.objects.filter(pk=sale.pk).update(date=purchase_dt + timedelta(hours=1))

        self.client.login(username="order_fix_admin", password="pw123456")

        create_response = self.client.get(reverse("sale_order_correction_create"))
        edit_response = self.client.get(reverse("sale_order_correction_edit", args=[order.id]))

        self.assertEqual(create_response.status_code, 200)
        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(create_response, "Add Missing Historical Order")
        self.assertContains(create_response, "Search product by barcode / brand / model / name")
        self.assertContains(create_response, 'data-product-picker')
        self.assertContains(edit_response, "Correct Order")
        self.assertContains(edit_response, "Delete entire order")

    def test_non_admin_is_redirected_from_order_correction_center(self):
        self.client.login(username="order_fix_employee", password="pw123456")

        response = self.client.get(reverse("sale_order_correction_center"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("dashboard"))

    def test_admin_can_create_backdated_order_and_rebuild_stock(self):
        purchase = Purchase.objects.create(
            product=self.product,
            supplier=None,
            quantity=10,
            cost_price=Decimal("10.00"),
            remaining=10,
        )
        purchase_dt = timezone.now() - timedelta(days=4)
        Purchase.objects.filter(pk=purchase.pk).update(date=purchase_dt)
        order_dt = timezone.localtime(purchase_dt + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")

        self.client.login(username="order_fix_admin", password="pw123456")
        response = self.client.post(reverse("sale_order_correction_create"), data={
            "customer": self.customer.id,
            "order_datetime": order_dt,
            "note": "Missed by employee",
            "reason": "Employee forgot to input the sale on time",
            "lines-TOTAL_FORMS": "1",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-product": self.product.id,
            "lines-0-quantity": "3",
            "lines-0-unit_price": "25.00",
            "lines-0-payment_method": "cash",
        })

        self.assertEqual(response.status_code, 302)
        order = SaleOrder.objects.get(note="Missed by employee")
        purchase.refresh_from_db()

        self.assertEqual(order.items.count(), 1)
        self.assertEqual(purchase.remaining, 7)
        self.assertTrue(SaleOrderChangeLog.objects.filter(order_id_snapshot=order.id, action="create").exists())

    def test_admin_can_create_order_for_earlier_date_without_full_fifo_rebuild(self):
        purchase = Purchase.objects.create(
            product=self.product,
            supplier=None,
            quantity=10,
            cost_price=Decimal("10.00"),
            remaining=10,
        )
        purchase_dt = timezone.now() - timedelta(days=1)
        Purchase.objects.filter(pk=purchase.pk).update(date=purchase_dt)
        order_dt = timezone.localtime(purchase_dt - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M")

        self.client.login(username="order_fix_admin", password="pw123456")
        response = self.client.post(reverse("sale_order_correction_create"), data={
            "customer": self.customer.id,
            "order_datetime": order_dt,
            "note": "Very old missed order",
            "reason": "Entered late after stock count",
            "lines-TOTAL_FORMS": "1",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-product": self.product.id,
            "lines-0-quantity": "4",
            "lines-0-unit_price": "25.00",
            "lines-0-payment_method": "cash",
        })

        self.assertEqual(response.status_code, 302)
        purchase.refresh_from_db()
        order = SaleOrder.objects.get(note="Very old missed order")

        self.assertEqual(order.items.count(), 1)
        self.assertEqual(purchase.remaining, 6)
        self.assertTrue(SaleOrderChangeLog.objects.filter(order_id_snapshot=order.id, action="create").exists())

    def test_admin_can_update_order_and_rebuild_stock(self):
        purchase = Purchase.objects.create(
            product=self.product,
            supplier=None,
            quantity=10,
            cost_price=Decimal("10.00"),
            remaining=8,
        )
        purchase_dt = timezone.now() - timedelta(days=3)
        Purchase.objects.filter(pk=purchase.pk).update(date=purchase_dt)

        order = SaleOrder.objects.create(customer=self.customer, note="Original order")
        SaleOrder.objects.filter(pk=order.pk).update(created_at=purchase_dt + timedelta(hours=1))
        sale = Sale.objects.create(
            order=order,
            product=self.product,
            customer=self.customer,
            quantity=2,
            unit_price=Decimal("25.00"),
            payment_method="cash",
        )
        Sale.objects.filter(pk=sale.pk).update(date=purchase_dt + timedelta(hours=1))

        self.client.login(username="order_fix_admin", password="pw123456")
        response = self.client.post(reverse("sale_order_correction_edit", args=[order.id]), data={
            "customer": self.customer.id,
            "order_datetime": timezone.localtime(purchase_dt + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"),
            "note": "Corrected order",
            "reason": "Price and quantity were entered incorrectly",
            "lines-TOTAL_FORMS": "2",
            "lines-INITIAL_FORMS": "1",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-product": self.product.id,
            "lines-0-quantity": "4",
            "lines-0-unit_price": "27.50",
            "lines-0-payment_method": "card",
            "lines-1-product": "",
            "lines-1-quantity": "",
            "lines-1-unit_price": "",
            "lines-1-payment_method": "",
        })

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        purchase.refresh_from_db()
        sale = order.items.get()

        self.assertEqual(order.note, "Corrected order")
        self.assertEqual(sale.quantity, 4)
        self.assertEqual(sale.unit_price, Decimal("27.50"))
        self.assertEqual(sale.payment_method, "card")
        self.assertEqual(purchase.remaining, 6)
        self.assertTrue(SaleOrderChangeLog.objects.filter(order_id_snapshot=order.id, action="update").exists())

    def test_backdated_order_update_keeps_sales_out_of_today_dashboard(self):
        extra_product = Product.objects.create(
            name="Reserve Line",
            barcode="1119991119992",
            brand="Lattafa",
            category=self.category,
            default_price=Decimal("35.00"),
        )
        first_purchase = Purchase.objects.create(
            product=self.product,
            supplier=None,
            quantity=10,
            cost_price=Decimal("10.00"),
            remaining=9,
        )
        extra_purchase = Purchase.objects.create(
            product=extra_product,
            supplier=None,
            quantity=10,
            cost_price=Decimal("12.00"),
            remaining=10,
        )
        purchase_dt = timezone.now() - timedelta(days=1)
        Purchase.objects.filter(pk=first_purchase.pk).update(date=purchase_dt)
        Purchase.objects.filter(pk=extra_purchase.pk).update(date=purchase_dt)

        order = SaleOrder.objects.create(customer=self.customer, note="Yesterday order")
        order_dt = purchase_dt + timedelta(hours=1)
        SaleOrder.objects.filter(pk=order.pk).update(created_at=order_dt)
        sale = Sale.objects.create(
            order=order,
            product=self.product,
            customer=self.customer,
            quantity=1,
            unit_price=Decimal("25.00"),
            payment_method="cash",
        )
        Sale.objects.filter(pk=sale.pk).update(date=order_dt)

        self.client.login(username="order_fix_admin", password="pw123456")
        response = self.client.post(reverse("sale_order_correction_edit", args=[order.id]), data={
            "customer": self.customer.id,
            "order_datetime": timezone.localtime(order_dt).strftime("%Y-%m-%dT%H:%M"),
            "note": "Yesterday order fixed",
            "reason": "Missing products were added later",
            "lines-TOTAL_FORMS": "3",
            "lines-INITIAL_FORMS": "1",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-product": self.product.id,
            "lines-0-quantity": "1",
            "lines-0-unit_price": "25.00",
            "lines-0-payment_method": "cash",
            "lines-1-product": extra_product.id,
            "lines-1-quantity": "3",
            "lines-1-unit_price": "35.00",
            "lines-1-payment_method": "card",
            "lines-2-product": "",
            "lines-2-quantity": "",
            "lines-2-unit_price": "",
            "lines-2-payment_method": "",
        })

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        sale_dates = list(order.items.order_by('id').values_list('date', flat=True))
        self.assertEqual(len(sale_dates), 2)
        self.assertTrue(all(timezone.localtime(sale_date).date() == timezone.localtime(order_dt).date() for sale_date in sale_dates))

        dashboard_response = self.client.get(reverse("dashboard"), {"month": timezone.localdate().strftime("%Y-%m")})
        self.assertEqual(dashboard_response.status_code, 200)
        today_order_ids = [item.id for item in dashboard_response.context["sale_orders_today"]]
        self.assertNotIn(order.id, today_order_ids)

    def test_admin_can_delete_order_and_restore_stock(self):
        purchase = Purchase.objects.create(
            product=self.product,
            supplier=None,
            quantity=10,
            cost_price=Decimal("10.00"),
            remaining=7,
        )
        purchase_dt = timezone.now() - timedelta(days=2)
        Purchase.objects.filter(pk=purchase.pk).update(date=purchase_dt)

        order = SaleOrder.objects.create(customer=self.customer, note="Delete me")
        SaleOrder.objects.filter(pk=order.pk).update(created_at=purchase_dt + timedelta(hours=1))
        sale = Sale.objects.create(
            order=order,
            product=self.product,
            customer=self.customer,
            quantity=3,
            unit_price=Decimal("25.00"),
            payment_method="cash",
        )
        Sale.objects.filter(pk=sale.pk).update(date=purchase_dt + timedelta(hours=1))

        self.client.login(username="order_fix_admin", password="pw123456")
        response = self.client.post(reverse("sale_order_correction_edit", args=[order.id]), data={
            "customer": self.customer.id,
            "order_datetime": timezone.localtime(purchase_dt + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            "note": "Delete me",
            "reason": "Duplicate order entered by mistake",
            "delete_order": "1",
            "lines-TOTAL_FORMS": "1",
            "lines-INITIAL_FORMS": "1",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-product": self.product.id,
            "lines-0-quantity": "3",
            "lines-0-unit_price": "25.00",
            "lines-0-payment_method": "cash",
        })

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SaleOrder.objects.filter(pk=order.id).exists())
        purchase.refresh_from_db()
        self.assertEqual(purchase.remaining, 10)
        self.assertTrue(SaleOrderChangeLog.objects.filter(order_id_snapshot=order.id, action="delete").exists())


class SupplierManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="supplier_manager",
            password="pw123456",
            is_staff=True,
        )
        cls.employee = user_model.objects.create_user(
            username="supplier_employee",
            password="pw123456",
        )
        cls.category = Category.objects.create(name="Supplier Category")

    def test_manager_can_create_supplier_from_frontend(self):
        self.client.login(username="supplier_manager", password="pw123456")

        response = self.client.post(reverse("supplier_create"), data={
            "name": "Dubai Wholesale",
            "phone": "+351900000000",
            "country": "UAE",
            "address": "Main supplier street",
            "product_types": [self.category.id],
        })

        self.assertEqual(response.status_code, 302)
        supplier = Supplier.objects.get(name="Dubai Wholesale")
        self.assertEqual(supplier.country, "UAE")
        self.assertEqual(list(supplier.product_types.values_list("id", flat=True)), [self.category.id])

    def test_regular_employee_cannot_open_supplier_management(self):
        self.client.login(username="supplier_employee", password="pw123456")

        response = self.client.get(reverse("supplier_list"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("dashboard"))

    def test_manager_can_open_supplier_detail_history(self):
        supplier = Supplier.objects.create(name="History Supplier", phone="+351911111111")
        product = Product.objects.create(
            name="Amber Musk",
            barcode="4445556667778",
            brand="Maison",
            category=self.category,
            default_price=Decimal("22.00"),
        )
        inbound_order = InboundOrder.objects.create(supplier=supplier, invoice_no="INV-1001", total_amount=Decimal("60.00"))
        purchase = Purchase.objects.create(
            inbound_order=inbound_order,
            product=product,
            supplier=supplier,
            quantity=5,
            cost_price=Decimal("12.00"),
            remaining=5,
        )
        Purchase.objects.filter(pk=purchase.pk).update(date=timezone.now() - timedelta(days=1))

        self.client.login(username="supplier_manager", password="pw123456")
        response = self.client.get(reverse("supplier_detail", args=[supplier.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "History Supplier")
        self.assertContains(response, "INV-1001")
        self.assertContains(response, "Amber Musk")


class EmployeeManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin = user_model.objects.create_superuser(username="team_admin", password="pw123456")
        cls.employee = user_model.objects.create_user(username="team_employee", password="pw123456")

    def test_admin_can_create_manager_account(self):
        self.client.login(username="team_admin", password="pw123456")

        response = self.client.post(reverse("employee_create"), data={
            "username": "new_manager",
            "first_name": "New",
            "last_name": "Manager",
            "email": "manager@example.com",
            "role": "manager",
            "is_active": "on",
            "password1": "pw12345678",
            "password2": "pw12345678",
        })

        self.assertEqual(response.status_code, 302)
        user_model = get_user_model()
        new_manager = user_model.objects.get(username="new_manager")
        self.assertTrue(new_manager.is_staff)
        self.assertTrue(new_manager.groups.filter(name="Managers").exists())

    def test_non_admin_cannot_open_team_management(self):
        self.client.login(username="team_employee", password="pw123456")

        response = self.client.get(reverse("employee_list"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("dashboard"))


class AttendanceManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.employee = user_model.objects.create_user(username="attendance_employee", password="pw123456")
        cls.manager = user_model.objects.create_user(username="attendance_manager", password="pw123456", is_staff=True)

    def test_employee_can_check_in_and_check_out(self):
        self.client.login(username="attendance_employee", password="pw123456")

        check_in_response = self.client.post(reverse("attendance"), data={
            "action": "check_in",
            "note": "Morning shift",
        })
        self.assertEqual(check_in_response.status_code, 302)

        record = AttendanceRecord.objects.get(user__username="attendance_employee")
        self.assertIsNone(record.clock_out_at)
        self.assertIn("Morning shift", record.note)

        check_out_response = self.client.post(reverse("attendance"), data={
            "action": "check_out",
            "note": "Closing counter",
        })
        self.assertEqual(check_out_response.status_code, 302)

        record.refresh_from_db()
        self.assertIsNotNone(record.clock_out_at)
        self.assertIn("Closing counter", record.note)

    def test_manager_can_see_team_attendance_section(self):
        AttendanceRecord.objects.create(user=self.employee, clock_in_at=timezone.now() - timedelta(hours=2))
        self.client.login(username="attendance_manager", password="pw123456")

        response = self.client.get(reverse("attendance"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Team attendance overview")
        self.assertContains(response, "attendance_employee")
