"""2026-11-01: free moves from the product page, undoing / waiving transfers,
each shop's card fee and sales targets, and the product page's lists."""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (Category, Purchase, Sale, SaleOrder, SalesTarget, StockTransfer,
                     StoreSettlement)
from .services import card_fees, store_stock
from .services.dashboard import build_target_progress
from .services.stock_ops import consume_stock_fifo
from .test_store_stock_pages import PagesMixin


class FreeMoveTests(PagesMixin, TestCase):
    def test_the_edit_page_moves_units_free_and_comes_back(self):
        self.batch(self.khan, 5)
        edit_url = reverse("edit_product", args=[self.product.id])
        page = self.client.get(edit_url).content.decode()
        self.assertIn("Move between shops", page)
        self.assertIn(f'value="{self.khan.id}-{self.scentory.id}"', page)
        response = self.client.post(reverse("stock_transfers"), {
            "action": "move", "free": "1", "barcode": self.product.barcode, "quantity": "2",
            "direction": f"{self.khan.id}-{self.scentory.id}", "next": edit_url})
        self.assertRedirects(response, edit_url, fetch_redirect_response=False)
        self.assertEqual((self.units(self.khan), self.units(self.scentory)), (3, 2))
        transfer = StockTransfer.objects.get()
        self.assertEqual(transfer.reason, StockTransfer.COUNT)
        self.assertEqual(transfer.amount, Decimal("0.00"))
        self.assertEqual(store_stock.balances(), [])
        today = timezone.localdate()
        self.assertEqual(store_stock.transfer_margins(today, today), [])

    def test_another_site_is_not_a_place_to_come_back_to(self):
        self.batch(self.khan, 5)
        response = self.client.post(reverse("stock_transfers"), {
            "action": "move", "barcode": self.product.barcode, "quantity": "1",
            "direction": f"{self.khan.id}-{self.scentory.id}", "next": "https://evil.example/"})
        self.assertRedirects(response, reverse("stock_transfers"), fetch_redirect_response=False)
        self.assertEqual(StockTransfer.objects.get().reason, StockTransfer.MANUAL)


class UndoTransferTests(PagesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.batch(self.khan, 5, cost="10")

    def post(self, **data):
        return self.client.post(reverse("stock_transfers"), data)

    def test_a_manual_transfer_goes_back_with_its_debt(self):
        line = store_stock.move_units(self.product, 3, self.khan, self.scentory)
        self.assertTrue(store_stock.balances())
        self.post(action="undo", transfer=line.transfer_id)
        self.assertEqual((self.units(self.khan), self.units(self.scentory)), (5, 0))
        self.assertFalse(StockTransfer.objects.exists())
        self.assertEqual(store_stock.balances(), [])
        # the units are back at their own cost and day
        self.assertEqual(sum(b.cost_price * b.remaining for b in Purchase.objects.all()), Decimal("50"))

    def test_nothing_is_undone_once_the_shop_sold_some(self):
        line = store_stock.move_units(self.product, 2, self.khan, self.scentory)
        consume_stock_fifo(self.product, 1, self.scentory, sample=True)
        with self.assertRaisesMessage(ValidationError, "no longer holds all of it"):
            store_stock.undo_transfer(line.transfer)
        self.assertEqual((self.units(self.khan), self.units(self.scentory)), (3, 1))
        self.assertTrue(StockTransfer.objects.exists())

    def test_a_sales_transfer_is_waived_not_undone(self):
        order = SaleOrder.objects.create(store=self.scentory)
        consume_stock_fifo(self.product, 2, self.scentory, sale_order=order)
        transfer = StockTransfer.objects.get()
        with self.assertRaisesMessage(ValidationError, "waive"):
            store_stock.undo_transfer(transfer)
        page = self.client.get(reverse("stock_transfers")).content.decode()
        self.assertIn('value="waive"', page)
        self.post(action="waive", transfer=transfer.id)
        transfer.refresh_from_db()
        self.assertIsNotNone(transfer.waived_at)
        self.assertEqual(transfer.amount, Decimal("0.00"))
        self.assertEqual(store_stock.balances(), [])
        self.assertEqual(self.units(self.khan), 3)       # the goods stay sold

    def test_a_settlement_can_be_deleted(self):
        store_stock.move_units(self.product, 1, self.khan, self.scentory)
        settled = store_stock.settle(self.scentory, self.khan, Decimal("13.00"),
                                     settled_on=timezone.localdate())
        self.assertEqual(store_stock.balances(), [])
        self.post(action="unsettle", settlement=settled.id)
        self.assertFalse(StoreSettlement.objects.exists())
        self.assertEqual(store_stock.balances()[0]["amount"], Decimal("13.00"))


class ShopSettingsTests(PagesMixin, TestCase):
    def test_each_shop_has_its_own_card_fee(self):
        self.khan.card_fee_pct = Decimal("1.00")
        self.khan.save()
        self.scentory.card_fee_pct = Decimal("1.40")
        self.scentory.save()
        self.assertEqual(card_fees.fee_for({"card": 100}, self.khan), Decimal("1.00"))
        self.assertEqual(card_fees.fee_for({"card": 100}, self.scentory), Decimal("1.40"))
        order = SaleOrder.objects.create(store=self.scentory)
        card_fees.record(order, {"card": Decimal("50")})
        self.assertEqual(order.card_fee, Decimal("0.70"))

    def test_the_page_saves_fees_and_targets_per_shop(self):
        other = Category.objects.create(name="Body spray")
        response = self.client.post(reverse("business_settings"), {
            f"card_fee_{self.khan.id}": "1", f"card_fee_{self.scentory.id}": "1,2",
            f"target_{self.khan.id}_{self.cat.id}": "5000", f"target_{self.scentory.id}_{self.cat.id}": "3000",
            f"target_{self.scentory.id}_{other.id}": "", "transfer_markup": "4"})
        self.assertRedirects(response, reverse("business_settings"), fetch_redirect_response=False)
        self.scentory.refresh_from_db()
        self.assertEqual(self.scentory.card_fee_pct, Decimal("1.20"))
        self.assertEqual(store_stock.transfer_markup(), Decimal("4.00"))
        self.assertEqual(SalesTarget.objects.get(store=self.scentory).monthly_amount, Decimal("3000.00"))
        # clearing a target removes it
        self.client.post(reverse("business_settings"), {f"target_{self.khan.id}_{self.cat.id}": ""})
        self.assertFalse(SalesTarget.objects.filter(store=self.khan).exists())

    def test_a_bad_number_saves_nothing(self):
        response = self.client.post(reverse("business_settings"), {
            f"card_fee_{self.khan.id}": "12", f"target_{self.scentory.id}_{self.cat.id}": "3000"})
        self.assertContains(response, "between 0 and 10")
        self.assertFalse(SalesTarget.objects.exists())

    def test_the_dashboard_target_is_the_shops_or_both(self):
        SalesTarget.objects.create(store=self.khan, category=self.cat, monthly_amount=Decimal("5000"))
        SalesTarget.objects.create(store=self.scentory, category=self.cat, monthly_amount=Decimal("3000"))
        today = timezone.localdate()
        month = {"month_start": today.replace(day=1), "period_end": today, "is_current_month": True}
        self.assertEqual(build_target_progress(month, Decimal("100"), store=self.scentory)["target_amount"],
                         Decimal("3000"))
        self.assertEqual(build_target_progress(month, Decimal("100"))["target_amount"], Decimal("8000"))


class ProductPageListsTests(PagesMixin, TestCase):
    def test_purchases_older_than_three_months_fold_and_sales_cover_30_days(self):
        self.batch(self.khan, 4, cost="10", days_ago=20)
        self.batch(self.khan, 6, cost="9", days_ago=200)
        sold = Sale.objects.create(product=self.product, quantity=1, unit_price=Decimal("50"), store=self.khan)
        Sale.objects.filter(pk=sold.pk).update(date=timezone.now() - timedelta(days=25))
        html = self.client.get(reverse("product_detail", args=[self.product.id])).content.decode()
        self.assertIn("Purchases &middot; last 3 months", html)
        recent, older = html.split('class="pdp-fold"', 1)
        self.assertIn("4 bought", recent)
        self.assertNotIn("6 bought", recent)
        self.assertIn("1 older purchase<", older)
        self.assertIn("6 bought", older)
        self.assertIn("Sales &middot; last 30 days", html)
        self.assertNotIn("No sales in the last 30 days", html)


class InboundRecordsPerShopTests(PagesMixin, TestCase):
    """2026-11-02: each shop's inbound record is what it received, also after transfers."""

    def setUp(self):
        super().setUp()
        from .models import InboundOrder, Supplier
        supplier = Supplier.objects.create(name="Naypes")
        self.order = InboundOrder.objects.create(supplier=supplier, status="received")
        store_stock.receive_batches(product=self.product, quantity=6, cost_price=Decimal("10"),
                                    allocation={self.khan.id: 4, self.scentory.id: 2},
                                    supplier=supplier, inbound_order=self.order)

    def purchases(self, store):
        session = self.client.session
        session["active_store_id"] = store
        session.save()
        return self.client.get(reverse("sales_records"), {"view": "purchases"})

    def test_a_transfer_does_not_change_what_a_shop_received(self):
        store_stock.move_units(self.product, 3, self.khan, self.scentory)
        self.assertEqual(sorted(Purchase.objects.values_list("received_store_id", flat=True)),
                         sorted([self.khan.id, self.khan.id, self.scentory.id]))
        page = self.purchases(self.scentory.id)
        self.assertEqual(page.context["total_purchase_qty"], 2)
        self.assertEqual(page.context["total_purchase_amount"], Decimal("20.00"))
        page = self.purchases(self.khan.id)
        self.assertEqual(page.context["total_purchase_qty"], 4)

    def test_all_shops_show_each_shops_quantity_and_cost(self):
        page = self.purchases("all")
        self.assertEqual(page.context["total_purchase_qty"], 6)
        self.assertEqual([(s["label"], s["qty"], s["cost"]) for s in page.context["purchase_shop_totals"]],
                         [("Khan", 4, Decimal("40.00")), ("Scentory", 2, Decimal("20.00"))])
        html = page.content.decode()
        self.assertIn("Khan <b>4</b> &middot; &euro;40.00", html)
        self.assertIn("Scentory <b>2</b> &middot; &euro;20.00", html)

    def test_a_shop_that_received_nothing_has_no_record(self):
        from .models import Store
        other = Store.objects.create(name="Third", code="T3")
        self.assertEqual(self.purchases(other.id).context["purchases"], [])

    def test_the_inbound_page_links_to_the_records(self):
        html = self.client.get(reverse("inbound")).content.decode()
        self.assertIn(">Inbound records</a>", html)
        self.assertIn("view=purchases", html)


class StockBatchesTests(PagesMixin, TestCase):
    def test_a_purchase_is_one_row_with_each_shops_batches_and_old_ones_fold(self):
        from .models import InboundOrder
        order = InboundOrder.objects.create(status="received")
        store_stock.receive_batches(product=self.product, quantity=5, cost_price=Decimal("10"),
                                    allocation={self.khan.id: 3, self.scentory.id: 2}, inbound_order=order)
        for n in range(4):               # older purchases, sold out
            self.batch(self.khan, 0, cost=str(20 + n), days_ago=100 + n)
        page = self.client.get(reverse("edit_product", args=[self.product.id]))
        shown, folded = page.context["batch_shown"], page.context["batch_folded"]
        self.assertEqual(len(shown), 3)            # the delivery + the two newest empty ones
        self.assertEqual(len(folded), 2)
        first = shown[0]
        self.assertEqual([(c["shop"].name, [b.remaining for b in c["batches"]]) for c in first.cells],
                         [("Khan Perfume", [3]), ("Scentory", [2])])
        self.assertContains(page, "2 older batches, sold out")


class SalesChannelTests(PagesMixin, TestCase):
    """2026-11-02: Orders -> Sales shows Retail, Revenda or both; Revenda orders are tagged."""

    def setUp(self):
        super().setUp()
        from .models import Customer
        session = self.client.session
        session["active_store_id"] = "all"
        session.save()
        trade = Customer.objects.create(name="Loja Atacado", kind="revenda")
        self.revenda = SaleOrder.objects.create(store=self.khan, customer=trade)
        self.retail = SaleOrder.objects.create(store=self.khan)
        for order, price in ((self.revenda, "30"), (self.retail, "50")):
            Sale.objects.create(order=order, product=self.product, quantity=1, unit_price=Decimal(price),
                                store=self.khan, customer=order.customer)

    def ids(self, **params):
        page = self.client.get(reverse("sales_records"), params)
        return {row["order_id"] for row in page.context["orders"]}, page

    def test_the_channel_picks_the_orders(self):
        self.assertEqual(self.ids()[0], {self.revenda.id, self.retail.id})
        self.assertEqual(self.ids(ch="revenda")[0], {self.revenda.id})
        ids, page = self.ids(ch="retail")
        self.assertEqual(ids, {self.retail.id})
        self.assertEqual(page.context["total_sales_amount"], Decimal("50"))

    def test_all_tags_the_revenda_orders(self):
        _ids, page = self.ids()
        html = page.content.decode()
        self.assertEqual(html.count('Loja Atacado<span class="ch-tag revenda">Revenda</span>'), 1)
        self.assertIn("ch=revenda\">Revenda</a>", html)

    def test_the_pdf_follows_the_channel(self):
        response = self.client.get(reverse("export_sales_purchases_pdf"), {"ch": "revenda"})
        self.assertEqual(response.status_code, 200)

    def test_today_tags_the_revenda_orders(self):
        html = self.client.get(reverse("daily_summary")).content.decode()
        self.assertIn('<span class="ch-tag revenda">Revenda</span>', html)


class BatchGroupCorrectionTests(PagesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.old = self.batch(self.scentory, 3, days_ago=5)
        self.new = self.batch(self.scentory, 2)

    def post(self, ids, n):
        import json
        return self.client.post(reverse("api_adjust_batch_group"),
                                json.dumps({"purchase_ids": ids, "new_remaining": n}),
                                content_type="application/json").json()

    def test_fewer_come_off_the_oldest_and_more_go_into_the_newest(self):
        self.assertTrue(self.post([self.old.id, self.new.id], 4)["success"])
        self.old.refresh_from_db(); self.new.refresh_from_db()
        self.assertEqual((self.old.remaining, self.new.remaining), (2, 2))
        Purchase.objects.filter(pk=self.new.pk).update(remaining=0)
        self.assertTrue(self.post([self.old.id, self.new.id], 3)["success"])
        self.old.refresh_from_db(); self.new.refresh_from_db()
        self.assertEqual((self.old.remaining, self.new.remaining), (2, 1))

    def test_no_more_than_came_in_and_one_shop_only(self):
        self.assertIn("At most 5", self.post([self.old.id, self.new.id], 6)["error"])
        khan = self.batch(self.khan, 1)
        self.assertFalse(self.post([self.old.id, khan.id], 1)["success"])


class TodayStockReceivedTests(PagesMixin, TestCase):
    """2026-11-02: Today counts goods received that day - not orders placed that day - per shop."""

    def setUp(self):
        super().setUp()
        from .models import InboundOrder
        placed = InboundOrder.objects.create(status="pending_receipt")
        InboundOrder.objects.filter(pk=placed.pk).update(created_at=timezone.now() - timedelta(days=3))
        store_stock.receive_batches(product=self.product, quantity=5, cost_price=Decimal("10"),
                                    allocation={self.khan.id: 3, self.scentory.id: 2}, inbound_order=placed)
        InboundOrder.objects.create(status="pending_receipt")       # placed today, not here yet

    def today(self, store):
        session = self.client.session
        session["active_store_id"] = store
        session.save()
        return self.client.get(reverse("daily_summary"))

    def test_an_order_placed_earlier_and_received_today_counts_today(self):
        page = self.today("all")
        self.assertEqual(page.context["received_units"], 5)
        self.assertEqual(page.context["received_orders"], 1)
        self.assertEqual([(s["label"], s["units"], s["cost"]) for s in page.context["received_shops"]],
                         [("Khan", 3, Decimal("30.00")), ("Scentory", 2, Decimal("20.00"))])
        self.assertContains(page, "view=purchases")

    def test_one_shop_sees_what_it_received_also_after_a_transfer(self):
        store_stock.move_units(self.product, 2, self.khan, self.scentory)
        page = self.today(self.scentory.id)
        self.assertEqual(page.context["received_units"], 2)
        self.assertEqual(page.context["received_shops"], [])
        self.assertEqual(self.today(self.khan.id).context["received_units"], 3)


class PurchasesByDateTests(PagesMixin, TestCase):
    """2026-11-02: Purchases by the day goods arrived (default) or were paid for."""

    def setUp(self):
        super().setUp()
        from .models import InboundOrder, InboundPendingItem
        session = self.client.session
        session["active_store_id"] = "all"
        session.save()
        self.paid_on = timezone.localdate() - timedelta(days=4)
        self.order = InboundOrder.objects.create(status="pending_receipt", invoice_no="NP-9")
        InboundOrder.objects.filter(pk=self.order.pk).update(
            created_at=timezone.now() - timedelta(days=4))
        store_stock.receive_batches(product=self.product, quantity=5, cost_price=Decimal("10"),
                                    allocation={self.khan.id: 3, self.scentory.id: 2}, inbound_order=self.order)
        self.on_the_way = InboundOrder.objects.create(status="pending_receipt", invoice_no="OW-1")
        InboundPendingItem.objects.create(inbound_order=self.on_the_way, product=self.product, quantity=4,
                                          cost_price=Decimal("9"), allocation={str(self.scentory.id): 4})

    def keys(self, day, **extra):
        page = self.client.get(reverse("sales_records"), {"view": "purchases", "start_date": day.isoformat(),
                                                           "end_date": day.isoformat(), **extra})
        return {g["key"] for g in page.context["purchases"]}, page

    def test_received_today_shows_today_with_the_day_it_was_paid(self):
        today = timezone.localdate()
        keys, page = self.keys(today)
        self.assertEqual(keys, {f"in-{self.order.id}"})
        self.assertContains(page, f'paid {self.paid_on.day} {self.paid_on:%b}')
        self.assertEqual(self.keys(self.paid_on)[0], set())

    def test_paid_shows_it_on_the_day_paid_and_what_is_on_the_way(self):
        today = timezone.localdate()
        keys, page = self.keys(self.paid_on, pdate="paid")
        self.assertEqual(keys, {f"in-{self.order.id}"})
        self.assertContains(page, "received ")
        keys, page = self.keys(today, pdate="paid")
        self.assertEqual(keys, {f"in-{self.on_the_way.id}"})
        self.assertContains(page, "On the way")
        self.assertEqual(page.context["total_purchase_amount"], Decimal("36.00"))

    def test_a_shop_sees_its_part_of_an_order_on_the_way(self):
        session = self.client.session
        session["active_store_id"] = self.khan.id
        session.save()
        self.assertEqual(self.keys(timezone.localdate(), pdate="paid")[0], set())

    def test_a_30_day_range(self):
        _keys, page = self.keys(timezone.localdate())
        html = page.content.decode()
        start = (timezone.localdate() - timedelta(days=29)).isoformat()
        self.assertIn(f"start_date={start}", html)
        self.assertIn(">30 days</a>", html)


class TodayChannelTests(PagesMixin, TestCase):
    def test_today_splits_retail_and_revenda(self):
        from .models import Customer
        trade = Customer.objects.create(name="Atacado", kind="revenda")
        for customer, qty, price in ((trade, 3, "20"), (None, 1, "50"), (None, 2, "40")):
            order = SaleOrder.objects.create(store=self.khan, customer=customer)
            Sale.objects.create(order=order, product=self.product, quantity=qty, unit_price=Decimal(price),
                                store=self.khan, customer=customer)
        session = self.client.session
        session["active_store_id"] = "all"
        session.save()
        page = self.client.get(reverse("daily_summary"))
        rows = {r["kind"]: (r["amount"], r["orders"], r["qty"]) for r in page.context["channel_rows"]}
        self.assertEqual(rows, {"retail": (Decimal("130.00"), 2, 3), "revenda": (Decimal("60.00"), 1, 3)})
        self.assertContains(page, "ch=revenda")
