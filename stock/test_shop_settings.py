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
