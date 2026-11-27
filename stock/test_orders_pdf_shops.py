"""The Orders PDFs follow the shop chosen in the header: one shop's part, or every shop's."""
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from stock.models import InboundOrder, InboundPendingItem
from stock.services import finance
from stock.test_orders_pdfs import PdfMixin

DOC = "stock.services.inbound_report_pdf.build_inbound_doc_pdf"
REPORT = "stock.services.inbound_report_pdf.build_inbound_report_pdf"


class ShopMixin(PdfMixin):
    def choose(self, store):
        session = self.client.session
        session["active_store_id"] = store.id if store else "all"
        session.save()

    def doc(self, order=None):
        with mock.patch(DOC, return_value=b"%PDF") as build:
            response = self.client.get(reverse("inbound_order_pdf", args=[(order or self.order).pk]))
        return response, build.call_args.kwargs if build.called else None

    def report(self, **params):
        with mock.patch(REPORT, return_value=b"%PDF") as build:
            self.export(view="purchases", **params)
        return build.call_args.kwargs


class OneDeliveryTests(ShopMixin, TestCase):
    def test_all_shops_show_every_shop(self):
        self.choose(None)
        _response, kwargs = self.doc()
        self.assertEqual([i.quantity for i in kwargs["items"]], [10])
        self.assertEqual([label for _id, label in kwargs["shops"]], ["Khan", "Scentory"])

    def test_a_shop_sees_only_its_part(self):
        self.choose(self.scentory)
        _response, kwargs = self.doc()
        item = kwargs["items"][0]
        self.assertEqual((item.quantity, item.line_total), (4, Decimal("400")))
        self.assertEqual(kwargs["shops"], [])
        self.assertIn("Scentory", kwargs["right_lines"])

    def test_a_shop_sees_its_part_of_the_payment(self):
        # 1000 owed: Khan received 600 of it, so a 400 payment covers 240 of Khan's
        finance.add_payment(self.order, store=self.scentory, amount=Decimal("400"), paid_on=self.today,
                            method="bank", note="", user=self.admin)
        self.choose(self.khan)
        payment = self.doc()[1]["payment"]
        self.assertEqual((payment["paid"], payment["owing"]), (Decimal("240.00"), Decimal("360.00")))
        self.assertEqual(sum(part["amount"] for part in payment["schedule"]), Decimal("600"))

    def test_an_order_the_shop_got_nothing_of(self):
        order = InboundOrder.objects.create(supplier=self.supplier, status="pending_receipt")
        InboundPendingItem.objects.create(inbound_order=order, product=self.product, quantity=3,
                                          cost_price=Decimal("7"), allocation={str(self.khan.id): 3})
        self.choose(self.scentory)
        self.assertEqual(self.doc(order)[0].status_code, 404)
        self.choose(self.khan)
        self.assertEqual([i.quantity for i in self.doc(order)[1]["items"]], [3])

    def test_a_real_document_for_one_shop(self):
        self.choose(self.khan)
        self.assertTrue(self.client.get(reverse("inbound_order_pdf", args=[self.order.pk])).content.startswith(b"%PDF"))


class TheReportTests(ShopMixin, TestCase):
    def test_all_shops_have_a_column_each(self):
        self.choose(None)
        self.assertEqual([label for _id, label in self.report()["shop_columns"]], ["Khan", "Scentory"])

    def test_one_shop_has_its_own_figures(self):
        self.choose(self.khan)
        kwargs = self.report()
        self.assertEqual(kwargs["shop_columns"], [])
        self.assertEqual(kwargs["groups"][0]["cost"], Decimal("600"))
        self.assertEqual(kwargs["store_label"], "Khan Perfume")
        self.assertTrue(kwargs["one_shop"])

    def test_one_shop_has_its_part_of_the_supplier_debt(self):
        self.choose(self.khan)
        kwargs = self.report()
        self.assertTrue(kwargs["with_payments"])
        self.assertEqual(kwargs["groups"][0]["order_owing"], Decimal("600.00"))

    def test_the_ordered_date_is_not_called_paid(self):
        self.choose(None)
        self.assertEqual(self.report(pdate="paid")["date_label"], "By day ordered")

    def test_the_unpaid_report_is_not_a_date_range(self):
        self.choose(self.khan)
        kwargs = self.report(pay="unpaid")
        self.assertEqual(kwargs["range_label"], "All dates")
        self.assertIn("Unpaid", kwargs["date_label"])
        self.assertEqual([g["order_owing"] for g in kwargs["groups"]], [Decimal("600.00")])

    def test_a_real_unpaid_report(self):
        for store in (None, self.khan):
            self.choose(store)
            response = self.export(view="purchases", pay="unpaid")
            self.assertTrue(response.content.startswith(b"%PDF"), store)
            self.assertIn("Inbound_unpaid", response["Content-Disposition"])

    def test_the_lines_carry_their_prices(self):
        self.choose(None)
        item = self.report()["groups"][0]["items"][0]
        self.assertEqual((item.quantity, item.cost_price, item.line_total), (10, Decimal("100"), Decimal("1000")))

    def test_a_real_report_for_each_choice(self):
        for store in (None, self.khan, self.scentory):
            self.choose(store)
            response = self.export(view="purchases")
            self.assertTrue(response.content.startswith(b"%PDF"), store)
