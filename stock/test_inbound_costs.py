"""2026-11-05: Inbound with IVA at 23% on top of the supplier's price, shipping
allocated to each line, and the invoice kept with the order."""
import json
import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (BusinessSettings, Category, InboundOrder, InboundPendingItem, Product, Purchase, Store,
                     Supplier)
from .services import finance, store_stock
from .services.inbound_costs import unit_cost
from .services.inbound_rollback import roll_back_receipt

UPLOADS = tempfile.mkdtemp(prefix='invoices-test-')


@override_settings(PRIVATE_MEDIA_ROOT=UPLOADS)
class InboundCostsMixin(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(UPLOADS, ignore_errors=True)

    def setUp(self):
        cache.clear()
        Store.objects.update(is_default=False, is_active=False)
        self.khan = Store.objects.create(name="Khan Perfume", code="KP", is_default=True)
        self.scentory = Store.objects.create(name="Scentory", code="SC")
        self.admin = get_user_model().objects.create_superuser("ic_admin", password="pw123456")
        self.client.force_login(self.admin)
        self.supplier = Supplier.objects.create(name="Lisboa Lda", adds_iva=True)
        cat = Category.objects.create(name="Perfumes", form_kind="perfume")
        self.a = Product.objects.create(name="A", brand="B", barcode="9980000000001", category=cat)
        self.b = Product.objects.create(name="B", brand="B", barcode="9980000000002", category=cat)

    def stock_in(self, lines, **extra):
        data = {"supplier": self.supplier.id, "invoice_no": "F1", "invoice_date": "2026-11-05",
                "items_json": json.dumps(lines), **extra}
        return self.client.post(reverse("inbound"), data)

    def order(self, *lines, iva="0", shipping="0"):
        order = InboundOrder.objects.create(supplier=self.supplier, status="pending_receipt",
                                            iva_rate=Decimal(iva), shipping=Decimal(shipping))
        for product, qty, cost, share in lines:
            InboundPendingItem.objects.create(inbound_order=order, product=product, quantity=qty,
                                              cost_price=Decimal(cost), shipping_share=Decimal(share),
                                              allocation={str(self.khan.id): qty})
        return order

    def receive(self, order, **changes):
        lines = list(order.pending_items.all())
        data = {"action": "receive", "supplier": self.supplier.id, "invoice_no": order.invoice_no or "",
                "invoice_date": "2026-11-05", "note": "", "shipping": str(order.shipping),
                "lines-TOTAL_FORMS": len(lines), "lines-INITIAL_FORMS": len(lines),
                "lines-MIN_NUM_FORMS": 0, "lines-MAX_NUM_FORMS": 1000}
        if order.iva_rate:
            data["add_iva"] = "1"
        for n, item in enumerate(lines):
            data.update({f"lines-{n}-id": item.id, f"lines-{n}-inbound_order": order.id,
                         f"lines-{n}-quantity": item.quantity, f"lines-{n}-cost_price": str(item.cost_price),
                         f"lines-{n}-shipping_share": str(item.shipping_share), f"lines-{n}-DELETE": "",
                         f"alloc-{item.id}-{self.khan.id}": item.quantity})
        data.update(changes)
        return self.client.post(reverse("inbound_receive", args=[order.id]), data)


class UnitCostTests(TestCase):
    def test_iva_is_added_to_the_price(self):
        self.assertEqual(unit_cost(Decimal("10.00"), Decimal("23.00"), Decimal("0"), 1), Decimal("12.30"))

    def test_shipping_is_shared_over_the_units_after_iva(self):
        self.assertEqual(unit_cost(Decimal("10.00"), Decimal("23.00"), Decimal("5.00"), 2), Decimal("14.80"))

    def test_no_iva_no_shipping_is_the_price(self):
        self.assertEqual(unit_cost(Decimal("10.00"), Decimal("0"), Decimal("0"), 3), Decimal("10.00"))


class StockInTests(InboundCostsMixin):
    def line(self, product=None, qty=2, cost="10", shipping=0):
        return {"barcode": (product or self.a).barcode, "qty": qty, "cost_price": cost, "shipping": shipping}

    def test_iva_on_keeps_the_price_typed_and_totals_with_iva(self):
        self.stock_in([self.line()], add_iva="1")
        order = InboundOrder.objects.get()
        item = order.pending_items.get()
        self.assertEqual((order.iva_rate, item.cost_price, item.unit_cost, order.total_amount),
                         (Decimal("23.00"), Decimal("10.00"), Decimal("12.30"), Decimal("24.60")))

    def test_iva_off_is_the_price(self):
        self.stock_in([self.line()])
        self.assertEqual(InboundOrder.objects.get().iva_rate, Decimal("0"))

    def test_shipping_must_be_allocated_in_full(self):
        response = self.stock_in([self.line(shipping=3), self.line(self.b, shipping=3)], shipping="10")
        self.assertContains(response, "Shipping")
        self.assertFalse(InboundOrder.objects.exists())

    def test_shipping_allocated_goes_into_each_line(self):
        self.stock_in([self.line(shipping=6), self.line(self.b, qty=4, shipping=4)], shipping="10")
        order = InboundOrder.objects.get()
        self.assertEqual(order.shipping, Decimal("10.00"))
        self.assertEqual(order.pending_items.get(product=self.a).unit_cost, Decimal("13.00"))
        self.assertEqual(order.total_amount, Decimal("70.00"))

    def test_the_invoice_is_kept_and_opens_for_managers_only(self):
        upload = SimpleUploadedFile("invoice.pdf", b"%PDF-1.4 x", content_type="application/pdf")
        self.stock_in([self.line()], invoice_file=upload)
        order = InboundOrder.objects.get()
        self.assertTrue(order.invoice_file)
        url = reverse("inbound_invoice", args=[order.id])
        opened = self.client.get(url)
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(b"".join(opened.streaming_content), b"%PDF-1.4 x")
        self.client.logout()
        self.assertNotEqual(self.client.get(url).status_code, 200)
        employee = get_user_model().objects.create_user("ic_emp", password="pw123456")
        self.client.force_login(employee)
        self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_an_order_with_no_invoice_says_not_found(self):
        order = self.order((self.a, 1, "10", "0"))
        self.assertEqual(self.client.get(reverse("inbound_invoice", args=[order.id])).status_code, 404)

    def test_only_a_pdf_or_photo_is_taken(self):
        upload = SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream")
        response = self.stock_in([self.line()], invoice_file=upload)
        self.assertContains(response, "PDF or a photo")
        self.assertFalse(InboundOrder.objects.exists())

    def test_a_large_file_is_refused(self):
        upload = SimpleUploadedFile("big.pdf", b"x" * (10 * 1024 * 1024 + 1), content_type="application/pdf")
        response = self.stock_in([self.line()], invoice_file=upload)
        self.assertContains(response, "10 MB")
        self.assertFalse(InboundOrder.objects.exists())

    def test_the_supplier_search_says_who_adds_iva(self):
        found = self.client.get("/api/suppliers_autocomplete/", {"q": "Lisboa"}).json()["results"]
        self.assertEqual(found[0]["adds_iva"], True)

    def test_the_supplier_form_sets_it(self):
        html = self.client.get(reverse("supplier_edit", args=[self.supplier.id])).content.decode()
        self.assertIn('name="adds_iva"', html)


class ReceiveTests(InboundCostsMixin):
    def test_the_batch_costs_iva_and_shipping_and_is_marked(self):
        order = self.order((self.a, 2, "10", "5"), iva="23", shipping="5")
        self.receive(order)
        batch = Purchase.objects.get()
        self.assertEqual((batch.cost_price, batch.net_cost, batch.iva_rate),
                         (Decimal("14.80"), Decimal("10.00"), Decimal("23.00")))

    def test_iva_can_be_turned_on_when_receiving(self):
        order = self.order((self.a, 1, "10", "0"))
        self.receive(order, add_iva="1")
        self.assertEqual(Purchase.objects.get().cost_price, Decimal("12.30"))

    def test_iva_can_be_turned_off_when_receiving(self):
        order = self.order((self.a, 1, "10", "0"), iva="23")
        lines = self.receive(order)
        self.assertEqual(lines.status_code, 302)
        order2 = self.order((self.b, 1, "10", "0"), iva="23")
        data = {"add_iva": ""}
        self.receive(order2, **data)
        self.assertEqual(Purchase.objects.get(product=self.b).cost_price, Decimal("10.00"))

    def test_shipping_that_does_not_add_up_stops_the_receipt_but_is_saved(self):
        order = self.order((self.a, 2, "10", "3"), shipping="5")
        self.receive(order)
        order.refresh_from_db()
        self.assertEqual(order.status, "pending_receipt")
        self.assertFalse(Purchase.objects.exists())

    def test_a_split_keeps_the_mark(self):
        order = self.order((self.a, 4, "10", "0"), iva="23")
        self.receive(order)
        store_stock.move_units(self.a, 1, self.khan, self.scentory)
        moved = Purchase.objects.get(store=self.scentory)
        self.assertEqual((moved.iva_rate, moved.net_cost, moved.cost_price),
                         (Decimal("23.00"), Decimal("10.00"), Decimal("12.30")))

    def test_rolling_back_gives_the_price_and_shipping_again(self):
        order = self.order((self.a, 3, "10", "1"), (self.b, 7, "9.99", "2"), iva="23", shipping="3")
        self.receive(order)
        order.refresh_from_db()
        self.assertEqual(order.status, "received")
        ok, _message = roll_back_receipt(order, self.admin)
        self.assertTrue(ok)
        items = {i.product_id: i for i in order.pending_items.all()}
        self.assertEqual(items[self.a.id].cost_price, Decimal("10.00"))
        self.assertEqual(sum(i.shipping_share for i in items.values()), Decimal("3.00"))

    def test_the_next_stock_in_starts_at_the_price_without_iva(self):
        order = self.order((self.a, 1, "10", "0"), iva="23")
        self.receive(order)
        found = self.client.get(reverse("check_barcode"), {"barcode": self.a.barcode}).json()
        self.assertEqual((found["last_cost"], found["last_net_cost"]), (12.3, 10.0))


class FinanceAndRecordsTests(InboundCostsMixin):
    def setUp(self):
        super().setUp()
        settings = BusinessSettings.get()
        settings.payables_from = None
        settings.save()
        self.client.post  # the client is logged in already

    def test_a_pending_order_is_owed_with_iva_and_shipping(self):
        order = self.order((self.a, 2, "10", "5"), iva="23", shipping="5")
        self.assertEqual(finance.order_due(order), Decimal("29.60"))

    def test_a_received_order_owes_the_same(self):
        order = self.order((self.a, 2, "10", "5"), iva="23", shipping="5")
        self.receive(order)
        order.refresh_from_db()
        self.assertEqual(finance.order_due(order), Decimal("29.60"))

    def test_cents_of_rounding_are_not_a_debt(self):
        BusinessSettings.objects.update(payables_from="2020-01-01")
        order = self.order((self.a, 3, "1.11", "0"), iva="23")
        finance.add_payment(order, store=self.scentory, amount=finance.order_due(order) - Decimal("0.40"),
                            paid_on="2026-11-05", method="bank", note="", user=self.admin)
        self.assertEqual(finance.payables(), [])

    def test_purchases_shows_iva_shipping_and_the_invoice(self):
        order = self.order((self.a, 2, "10", "5"), iva="23", shipping="5")
        self.receive(order)
        InboundOrder.objects.filter(pk=order.pk).update(invoice_file="invoices/2026/x.pdf")
        session = self.client.session
        session["active_store_id"] = "all"
        session.save()
        html = self.client.get(reverse("sales_records"), {"view": "purchases"}).content.decode()
        self.assertIn("IVA 23%", html)
        self.assertIn("<dt>Shipping</dt><dd>EUR 5.00</dd>", html)
        self.assertIn(reverse("inbound_invoice", args=[order.id]), html)

    def test_the_inbound_page_offers_all_three(self):
        html = self.client.get(reverse("inbound")).content.decode()
        for name in ('name="add_iva"', 'name="shipping"', 'name="invoice_file"', 'enctype="multipart/form-data"'):
            self.assertIn(name, html)

    def test_the_order_edit_page_takes_an_invoice_later(self):
        order = self.order((self.a, 1, "10", "0"))
        self.receive(order)
        page = self.client.get(reverse("inbound_order_edit", args=[order.id]))
        self.assertContains(page, 'name="invoice_file"')


class PendingListTests(InboundCostsMixin):
    def test_awaiting_receipt_totals_every_pending_order(self):
        first = self.order((self.a, 2, "10", "0"))
        second = self.order((self.b, 1, "5.50", "0"))
        received = self.order((self.b, 1, "99", "0"))
        InboundOrder.objects.filter(pk=first.pk).update(total_amount=Decimal("100.10"))
        InboundOrder.objects.filter(pk=second.pk).update(total_amount=Decimal("23.40"))
        InboundOrder.objects.filter(pk=received.pk).update(total_amount=Decimal("99"), status="received")
        response = self.client.get(reverse("inbound"))
        self.assertEqual(response.context["pending_total"], Decimal("123.50"))
        self.assertContains(response, "Total &euro;123.50")

    def test_the_total_is_there_after_a_refused_post_too(self):
        self.order((self.a, 1, "10", "0"))
        InboundOrder.objects.update(total_amount=Decimal("10"))
        response = self.client.post(reverse("inbound"), {"items_json": "[]"})
        self.assertEqual(response.context["pending_total"], Decimal("10"))

    def test_no_pending_orders_no_list(self):
        self.assertNotContains(self.client.get(reverse("inbound")), "Pending inbound orders")


class WithIvaColumnTests(InboundCostsMixin):
    def test_the_dialog_has_the_column_and_opens_it_for_an_iva_order(self):
        order = self.order((self.a, 1, "10", "0"), iva="23")
        html = self.client.get(reverse("inbound")).content.decode()
        table = html[html.index(f'id="receive-modal-{order.id}"'):]
        self.assertIn("With IVA", table)
        self.assertIn("rcv-table has-iva", table)
        self.assertIn("data-rcv-with-iva", table)

    def test_the_dialog_keeps_it_closed_without_iva(self):
        order = self.order((self.a, 1, "10", "0"))
        html = self.client.get(reverse("inbound")).content.decode()
        table = html[html.index(f'id="receive-modal-{order.id}"'):]
        self.assertNotIn("rcv-table has-iva", table)

    def test_the_cart_has_the_column(self):
        html = self.client.get(reverse("inbound")).content.decode()
        self.assertIn('class="iva-col"', html)
        self.assertIn("with-iva", html)

