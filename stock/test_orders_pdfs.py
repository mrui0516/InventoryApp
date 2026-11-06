"""Orders > Export PDF, one document for each part, and one for each record."""
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from stock.models import InboundOrder, InboundPendingItem, Purchase, Sale, SaleOrder, Supplier
from stock.services import finance, store_stock
from stock.test_finance import FinanceMixin


class PdfMixin(FinanceMixin):
    def setUp(self):
        super().setUp()
        self.supplier = Supplier.objects.create(name="Naypes")
        self.order = InboundOrder.objects.create(supplier=self.supplier, status="received", invoice_no="F-12",
                                                 invoice_date=self.today)
        store_stock.receive_batches(product=self.product, quantity=10, cost_price=Decimal("100"),
                                    allocation={self.khan.id: 6, self.scentory.id: 4},
                                    supplier=self.supplier, inbound_order=self.order)
        self.sale = SaleOrder.objects.create(store=self.khan)
        Sale.objects.create(order=self.sale, product=self.product, quantity=1, store=self.khan,
                            unit_price=Decimal("50"), payment_method="cash")

    def export(self, **params):
        return self.client.get(reverse("export_sales_purchases_pdf"), params)

    def manager(self):
        user = get_user_model().objects.create_user("mgr", password="pw")
        user.groups.add(Group.objects.get_or_create(name="Managers")[0])
        self.client.force_login(user)


class EachPartHasItsOwnReportTests(PdfMixin, TestCase):
    def assertPdf(self, response, name):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn(f'filename="{name}', response["Content-Disposition"])

    def test_sales_stay_the_sales_report(self):
        self.assertPdf(self.export(), "Sales_")

    def test_purchases_get_the_inbound_report(self):
        self.assertPdf(self.export(view="purchases"), "Inbound_")

    def test_analysis_gets_its_own(self):
        self.assertPdf(self.export(view="analysis"), "Analysis_")

    def test_an_empty_range_still_prints(self):
        far = "2020-01-01"
        for view in ("purchases", "analysis"):
            self.assertPdf(self.export(view=view, start_date=far, end_date=far),
                           "Inbound_" if view == "purchases" else "Analysis_")

    def test_the_inbound_report_follows_the_page(self):
        with mock.patch("stock.services.inbound_report_pdf.build_inbound_report_pdf", return_value=b"%PDF") as build:
            self.export(view="purchases", q="nothing like it")
        self.assertEqual(build.call_args.kwargs["groups"], [])
        self.assertIn("nothing like it", build.call_args.kwargs["filter_label"])

    def test_it_says_what_each_shop_received(self):
        with mock.patch("stock.services.inbound_report_pdf.build_inbound_report_pdf", return_value=b"%PDF") as build:
            self.export(view="purchases")
        self.assertEqual({s["label"]: s["cost"] for s in build.call_args.kwargs["shop_totals"]},
                         {"Khan": Decimal("600.00"), "Scentory": Decimal("400.00")})

    def test_money_owed_is_for_the_admin(self):
        with mock.patch("stock.services.inbound_report_pdf.build_inbound_report_pdf", return_value=b"%PDF") as build:
            self.export(view="purchases")
            self.assertTrue(build.call_args.kwargs["with_payments"])
            self.manager()
            self.export(view="purchases")
        self.assertFalse(build.call_args.kwargs["with_payments"])
        self.assertIsNone(build.call_args.kwargs["groups"][0]["order_owing"])

    def test_profit_is_for_the_admin(self):
        with mock.patch("stock.services.analysis_report_pdf.build_analysis_report_pdf", return_value=b"%PDF") as build:
            self.manager()
            self.export(view="analysis")
        self.assertFalse(build.call_args.kwargs["show_profit"])

    def test_the_button_exports_the_part_on_screen(self):
        for view in ("purchases", "analysis"):
            page = self.client.get(reverse("sales_records"), {"view": view})
            self.assertIn(f"view={view}", page.context["export_url"])
            self.assertContains(page, "Export PDF")
        self.assertNotIn("view=", self.client.get(reverse("sales_records")).context["export_url"])

    def test_the_date_choice_goes_with_it(self):
        page = self.client.get(reverse("sales_records"), {"view": "purchases", "pdate": "paid"})
        self.assertIn("pdate=paid", page.context["export_url"])


class EachRecordHasItsOwnTests(PdfMixin, TestCase):
    def build(self, url):
        with mock.patch("stock.services.inbound_report_pdf.build_inbound_doc_pdf", return_value=b"%PDF") as build:
            response = self.client.get(url)
        return response, build.call_args.kwargs if build.called else None

    def test_a_received_order(self):
        response = self.client.get(reverse("inbound_order_pdf", args=[self.order.pk]))
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn(f'Inbound_{self.order.pk}.pdf', response["Content-Disposition"])

    def test_it_lists_the_goods_and_the_shops(self):
        _response, kwargs = self.build(reverse("inbound_order_pdf", args=[self.order.pk]))
        self.assertEqual([i.quantity for i in kwargs["items"]], [10])
        self.assertEqual([label for _id, label in kwargs["shops"]], ["Khan", "Scentory"])

    def test_an_order_on_the_way(self):
        order = InboundOrder.objects.create(supplier=self.supplier, status="pending_receipt")
        InboundPendingItem.objects.create(inbound_order=order, product=self.product, quantity=3, cost_price=Decimal("7"))
        self.assertTrue(self.client.get(reverse("inbound_order_pdf", args=[order.pk])).content.startswith(b"%PDF"))

    def test_the_admin_sees_the_payment(self):
        _response, kwargs = self.build(reverse("inbound_order_pdf", args=[self.order.pk]))
        self.assertEqual(kwargs["payment"]["owing"], Decimal("1000.00"))

    def test_a_manager_does_not(self):
        self.manager()
        _response, kwargs = self.build(reverse("inbound_order_pdf", args=[self.order.pk]))
        self.assertIsNone(kwargs["payment"])

    def test_an_employee_cannot(self):
        self.client.force_login(get_user_model().objects.create_user("emp", password="pw"))
        self.assertNotEqual(self.client.get(reverse("inbound_order_pdf", args=[self.order.pk])).status_code, 200)

    def test_a_stock_in_without_a_supplier(self):
        batch = Purchase.objects.create(product=self.product, quantity=2, remaining=2, cost_price=Decimal("5"))
        response = self.client.get(reverse("direct_purchase_pdf"), {"ids": str(batch.pk)})
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_a_stock_in_refuses_an_orders_batch(self):
        batch = self.order.items.first()
        self.assertEqual(self.client.get(reverse("direct_purchase_pdf"), {"ids": str(batch.pk)}).status_code, 404)

    def test_the_purchases_panel_offers_it(self):
        page = self.client.get(reverse("sales_records"), {"view": "purchases"})
        self.assertContains(page, reverse("inbound_order_pdf", args=[self.order.pk]))

    def test_the_sales_panel_offers_it(self):
        self.assertContains(self.client.get(reverse("sales_records")),
                            reverse("sale_order_receipt_pdf", args=[self.sale.pk]))

    def test_a_real_document_with_a_payment_plan(self):
        InboundOrder.objects.filter(pk=self.order.pk).update(pay_instalments=3)
        finance.add_payment(self.order, store=self.scentory, amount=Decimal("400"), paid_on=self.today,
                            method="bank", note="", user=self.admin)
        self.assertTrue(self.client.get(reverse("inbound_order_pdf", args=[self.order.pk])).content.startswith(b"%PDF"))
