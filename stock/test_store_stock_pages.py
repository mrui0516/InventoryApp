"""Stock per shop through the pages: the till, receiving, transfers and the
opening split (2026-11-01). The service itself: test_store_stock.py."""
import json
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from .models import (InboundOrder, InboundPendingItem, Product, Purchase, Sale, SaleOrder,
                     StockTransfer, StoreSettlement, Supplier)
from .services import store_stock
from .services.order_corrections import delete_sale_order_correction
from .test_store_stock import ShopsMixin


class PagesMixin(ShopsMixin):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.admin = get_user_model().objects.create_superuser("ss_admin", password="pw123456")
        self.client.force_login(self.admin)

    def pick(self, store):
        session = self.client.session
        session["active_store_id"] = store.id
        session.save()


class SellPageTests(PagesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.batch(self.khan, 3)
        self.batch(self.scentory, 1)
        self.pick(self.scentory)

    def sell(self, qty, price, sample=False):
        total = Decimal(price) * qty
        return self.client.post(reverse("outbound"), {
            "items_json": json.dumps([{"barcode": self.product.barcode, "qty": qty, "price": price,
                                       "sample": sample}]),
            "payments_json": json.dumps([{"method": "cash", "amount": f"{total:.2f}"}]),
        })

    def test_the_till_sees_both_shops_and_its_sample(self):
        data = self.client.get(reverse("check_barcode"), {"barcode": self.product.barcode}).json()
        self.assertEqual(data["stock"], 2)          # Khan spares 2; Scentory's one bottle is its sample
        self.assertTrue(data["sample_here"])
        self.assertEqual([(s["name"], s["units"], s["here"]) for s in data["shops"]],
                         [("Khan Perfume", 3, False), ("Scentory", 1, True)])

    def test_a_full_price_sale_is_brought_down_from_the_other_shop(self):
        self.assertEqual(self.sell(1, "50.00").status_code, 302)
        self.assertEqual((self.units(self.scentory), self.units(self.khan)), (1, 2))
        sale = Sale.objects.get()
        self.assertFalse(sale.is_sample)
        self.assertEqual(sale.store, self.scentory)
        self.assertEqual(StockTransfer.objects.get().sale_order, sale.order)

    def test_the_sample_sells_at_the_typed_price(self):
        self.assertEqual(self.sell(1, "20.00", sample=True).status_code, 302)
        sale = Sale.objects.get()
        self.assertTrue(sale.is_sample)
        self.assertEqual(sale.unit_price, Decimal("20.00"))
        self.assertEqual((self.units(self.scentory), self.units(self.khan)), (0, 3))
        self.assertFalse(StockTransfer.objects.exists())

    def test_a_deleted_order_puts_the_goods_back_in_the_shop_that_sold_them(self):
        self.sell(2, "50.00")
        delete_sale_order_correction(order=SaleOrder.objects.get(), changed_by=self.admin, reason="test")
        self.assertEqual((self.units(self.scentory), self.units(self.khan)), (3, 1))

    def test_the_product_pages_show_each_shop(self):
        html = self.client.get(reverse("product_list")).content.decode()
        # in Scentory: its own units in the pill, Khan's under it
        self.assertIn('title="Scentory 1 · Khan Perfume 3">1</span>', html)
        self.assertIn('<span class="shop-other">Khan <b>3</b></span>', html)
        panel = self.client.get(reverse("product_panel", args=[self.product.id])).content.decode()
        self.assertIn("Khan 3 · Scentory 1", panel)


class InboundSplitTests(PagesMixin, TestCase):
    def stock_in(self, alloc, qty):
        return self.client.post(reverse("inbound"), {"items_json": json.dumps(
            [{"barcode": self.product.barcode, "qty": qty, "cost_price": 10, "alloc": alloc}])})

    def test_stock_in_without_a_supplier_must_be_split(self):
        self.stock_in({}, 5)
        self.assertFalse(Purchase.objects.exists())
        self.stock_in({str(self.khan.id): 3, str(self.scentory.id): 2}, 6)
        self.assertFalse(Purchase.objects.exists())                # 5 of 6 placed
        self.stock_in({str(self.khan.id): 4, str(self.scentory.id): 2}, 6)
        self.assertEqual((self.units(self.khan), self.units(self.scentory)), (4, 2))

    def test_the_page_offers_a_column_per_shop(self):
        html = self.client.get(reverse("inbound")).content.decode()
        self.assertIn('class="alloc-th"', html)
        self.assertIn('"Scentory"', html)

    def test_receiving_a_pending_order_needs_the_split(self):
        supplier = Supplier.objects.create(name="Sup")
        order = InboundOrder.objects.create(supplier=supplier, status="pending_receipt")
        line = InboundPendingItem.objects.create(inbound_order=order, product=self.product, quantity=5,
                                                 cost_price=Decimal("10"))
        form = {"supplier": str(supplier.pk), "invoice_no": "", "invoice_date": "", "note": "",
                "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "1", "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000", "lines-0-id": str(line.pk),
                "lines-0-inbound_order": str(order.pk), "lines-0-quantity": "5", "lines-0-cost_price": "10"}
        url = reverse("inbound_receive", args=[order.pk])
        self.client.post(url, dict(form, action="receive", **{f"alloc-{line.pk}-{self.scentory.id}": "2"}))
        order.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(order.status, "pending_receipt")
        self.assertEqual(line.allocation, {str(self.scentory.id): 2})        # what was typed is kept
        self.client.post(url, dict(form, action="receive", **{f"alloc-{line.pk}-{self.scentory.id}": "2",
                                                              f"alloc-{line.pk}-{self.khan.id}": "3"}))
        order.refresh_from_db()
        self.assertEqual(order.status, "received")
        self.assertEqual((self.units(self.khan), self.units(self.scentory)), (3, 2))
        self.assertEqual(set(Purchase.objects.values_list("supplier", flat=True)), {supplier.id})


class TransfersPageTests(PagesMixin, TestCase):
    def test_moving_and_settling_through_the_page(self):
        self.batch(self.khan, 5)
        url = reverse("stock_transfers")
        self.client.post(url, {"action": "move", "barcode": self.product.barcode, "quantity": "2",
                               "direction": f"{self.khan.id}-{self.scentory.id}"})
        self.assertEqual((self.units(self.khan), self.units(self.scentory)), (3, 2))
        html = self.client.get(url).content.decode()
        self.assertIn("<b>Scentory</b> owes <b>Khan Perfume</b>", html)
        self.assertIn("26.00", html)
        self.client.post(url, {"action": "settle", "payer": self.scentory.id, "payee": self.khan.id,
                               "amount": "26.00", "settled_on": "2026-11-01"})
        self.assertEqual(StoreSettlement.objects.get().amount, Decimal("26.00"))
        self.assertIn("The shops are square.", self.client.get(url).content.decode())

    def test_an_unknown_barcode_is_reported(self):
        response = self.client.post(reverse("stock_transfers"), {
            "action": "move", "barcode": "000", "quantity": "1",
            "direction": f"{self.khan.id}-{self.scentory.id}"}, follow=True)
        self.assertContains(response, "No product with barcode 000")

    def test_employees_cannot_open_it(self):
        employee = get_user_model().objects.create_user("ss_emp", password="pw123456")
        self.client.force_login(employee)
        self.assertEqual(self.client.get(reverse("stock_transfers")).status_code, 302)


class OpeningSplitTests(ShopsMixin, TestCase):
    def run_split(self, *args):
        out = StringIO()
        call_command("split_opening_stock", "--to", "Scentory", "--units", "2", *args, stdout=out)
        return out.getvalue()

    def test_preview_then_apply_gives_two_of_each(self):
        other = Product.objects.create(name="Rose", brand="B", barcode="9940000000002", category=self.cat,
                                       default_price=Decimal("30"))
        self.batch(self.khan, 6)
        Purchase.objects.create(product=other, store=self.khan, quantity=1, remaining=1, cost_price=Decimal("5"))
        self.assertIn("2 products, 3 units", self.run_split())
        self.assertEqual(self.units(self.scentory), 0)                  # the preview changes nothing
        self.run_split("--apply")
        self.assertEqual((self.units(self.khan), self.units(self.scentory)), (4, 2))
        self.assertEqual(store_stock.store_units(other, self.scentory), 1)
        self.assertEqual(store_stock.balances(), [])                      # opening stock owes nothing
        with self.assertRaises(CommandError):
            self.run_split("--apply")


class ShopViewsTests(PagesMixin, TestCase):
    """2026-11-01: each shop's profit, the stock pill, one line per delivery."""

    def test_in_one_shop_the_list_counts_that_shops_stock(self):
        self.batch(self.khan, 3)
        self.pick(self.scentory)
        html = self.client.get(reverse("product_list")).content.decode()
        self.assertIn('<span class="stock-pill zero" title="Scentory 0 · Khan Perfume 3">0</span>', html)
        self.assertIn('<span class="shop-other">Khan <b>3</b></span>', html)
        self.assertNotIn("{#", html)            # a template comment must not leak onto the page
        # out of stock here, though Khan has it
        out = self.client.get(reverse("product_list"), {"stock_status": "out_of_stock"})
        self.assertIn(self.product, list(out.context["page_obj"]))
        self.pick(self.khan)
        out = self.client.get(reverse("product_list"), {"stock_status": "out_of_stock"})
        self.assertNotIn(self.product, list(out.context["page_obj"]))

    def test_all_shops_show_the_total_and_each_shop(self):
        self.batch(self.khan, 3)
        self.batch(self.scentory, 4)
        session = self.client.session
        session["active_store_id"] = "all"
        session.save()
        html = self.client.get(reverse("product_list")).content.decode()
        self.assertIn('title="7 in all">7</span><span class="shop-other"><span class="lbl">Khan</span><span class="ini">K</span> <b>3</b>'
                      ' · <span class="lbl">Scentory</span><span class="ini">S</span> <b>4</b></span>', html)

    def test_a_delivery_lists_each_product_once_with_each_shops_part(self):
        supplier = Supplier.objects.create(name="Naypes")
        order = InboundOrder.objects.create(supplier=supplier, status="received")
        store_stock.receive_batches(product=self.product, quantity=6, cost_price=Decimal("10"),
                                    allocation={self.khan.id: 4, self.scentory.id: 2},
                                    supplier=supplier, inbound_order=order)
        html = self.client.get(reverse("sales_records"), {"view": "purchases"}).content.decode()
        self.assertEqual(html.count('class="ord-item"'), 1)
        self.assertIn("&times;6", html)
        self.assertIn("Khan <b>4</b> · Scentory <b>2</b>", html)
        sup = self.client.get(reverse("supplier_detail", args=[supplier.id])).content.decode()
        self.assertIn("Khan <b>4</b> · Scentory <b>2</b>", sup)
        pdp = self.client.get(reverse("product_detail", args=[self.product.id])).content.decode()
        self.assertIn("6 bought", pdp)
        self.assertIn("(Khan 4 · Scentory 2)", pdp)

    def test_a_split_direct_stock_in_offers_an_adjust_link_per_shop(self):
        store_stock.receive_batches(product=self.product, quantity=3, cost_price=Decimal("10"),
                                    allocation={self.khan.id: 2, self.scentory.id: 1})
        html = self.client.get(reverse("sales_records"), {"view": "purchases"}).content.decode()
        self.assertIn(">Adjust Khan</a>", html)
        self.assertIn(">Adjust Scentory</a>", html)

    def test_the_dashboard_counts_the_transfer_markup_in_a_shops_profit(self):
        from .services.stock_ops import consume_stock_fifo
        self.batch(self.khan, 5, cost="10")
        order = SaleOrder.objects.create(store=self.scentory)
        cost = consume_stock_fifo(self.product, 2, self.scentory, sale_order=order)
        Sale.objects.create(product=self.product, quantity=2, unit_price=Decimal("50"), order=order,
                            store=self.scentory, cost_basis=cost)
        self.pick(self.scentory)
        page = self.client.get(reverse("dashboard"))
        # sold 100, cost 20, paid Khan 2 x 3 markup
        self.assertEqual(page.context["month_sales_profit"], Decimal("74.00"))
        self.assertContains(page, "transfers &minus;EUR 6.00")
        cache.clear()
        self.pick(self.khan)
        self.assertEqual(self.client.get(reverse("dashboard")).context["month_sales_profit"], Decimal("6.00"))

    def test_the_muted_row_rule_is_one_selector_list(self):
        # 2026-11-01: a style block dropped into the middle of this list gave
        # the bell-off, sold-out rows pill borders on their name and prices
        html = self.client.get(reverse("product_list")).content.decode()
        self.assertIn("tr.is-muted .price-stack,\n  tr.is-muted .stock-pill,", html)
