"""Stock per shop (2026-11-01): batches belong to a store, a sale takes what its
shop lacks from the other one and the shops owe each other cost + markup."""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from .models import (BusinessSettings, Category, InboundOrder, Product, Purchase, SaleOrder,
                     StockTransfer, StockTransferLine, Store, Supplier)
from .services import store_stock
from .services.stock_ops import consume_stock_fifo, restore_stock_fifo


class ShopsMixin:
    def setUp(self):
        Store.objects.update(is_default=False, is_active=False)     # the store a migration seeds
        self.khan = Store.objects.create(name="Khan Perfume", code="KP", is_default=True, keeps_samples=True)
        self.scentory = Store.objects.create(name="Scentory", code="SC", keeps_samples=True)
        self.cat = Category.objects.create(name="Perfumes", form_kind="perfume")
        self.product = Product.objects.create(name="Oud", brand="B", barcode="9940000000001",
                                              category=self.cat, default_price=Decimal("50"))

    def batch(self, store, qty, cost="10", days_ago=0, **extra):
        b = Purchase.objects.create(product=self.product, store=store, quantity=qty, remaining=qty,
                                    cost_price=Decimal(cost), **extra)
        if days_ago:
            Purchase.objects.filter(pk=b.pk).update(date=timezone.now() - timedelta(days=days_ago))
            b.refresh_from_db()
        return b

    def units(self, store):
        return store_stock.store_units(self.product, store)


class BatchStoreTests(ShopsMixin, TestCase):
    def test_a_batch_without_a_shop_lands_in_the_default_one(self):
        b = Purchase.objects.create(product=self.product, quantity=3, remaining=3, cost_price=Decimal("5"))
        self.assertEqual(b.store, self.khan)

    def test_stock_by_store_counts_each_shop(self):
        self.batch(self.khan, 5)
        self.batch(self.scentory, 2)
        self.assertEqual(store_stock.stock_by_store([self.product.id])[self.product.id],
                         {self.khan.id: 5, self.scentory.id: 2})


class SaleFromAShopTests(ShopsMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.batch(self.khan, 5, cost="10")
        self.batch(self.scentory, 2, cost="10")

    def test_a_sale_takes_from_its_own_shop_and_leaves_the_sample(self):
        cost = consume_stock_fifo(self.product, 1, self.scentory)
        self.assertEqual(cost, Decimal("10.00"))
        self.assertEqual((self.units(self.scentory), self.units(self.khan)), (1, 5))
        self.assertFalse(StockTransfer.objects.exists())

    def test_what_the_shop_lacks_comes_from_the_other_and_is_owed(self):
        order = SaleOrder.objects.create(store=self.scentory)
        consume_stock_fifo(self.product, 3, self.scentory, sale_order=order)
        # Scentory sold its one full-price bottle and two brought down; its sample stays
        self.assertEqual((self.units(self.scentory), self.units(self.khan)), (1, 3))
        line = StockTransferLine.objects.get()
        self.assertEqual(line.transfer.from_store, self.khan)
        self.assertEqual(line.transfer.to_store, self.scentory)
        self.assertEqual(line.transfer.reason, StockTransfer.SALE)
        self.assertEqual(line.transfer.sale_order, order)
        self.assertEqual(line.quantity, 2)
        self.assertEqual(line.amount, Decimal("26.00"))          # 2 x (10 + 3)

    def test_the_markup_is_the_one_set_at_the_time(self):
        settings = BusinessSettings.get()
        settings.transfer_markup = Decimal("5.00")
        settings.save()
        consume_stock_fifo(self.product, 2, self.scentory)
        self.assertEqual(StockTransferLine.objects.get().amount, Decimal("15.00"))

    def test_only_samples_left_must_be_sold_as_a_sample(self):
        consume_stock_fifo(self.product, 5, self.scentory)        # 1 own + 4 from Khan
        self.assertEqual((self.units(self.scentory), self.units(self.khan)), (1, 1))
        with self.assertRaisesMessage(ValidationError, "sell it as a sample"):
            consume_stock_fifo(self.product, 1, self.scentory)
        consume_stock_fifo(self.product, 1, self.scentory, sample=True)
        self.assertEqual(self.units(self.scentory), 0)

    def test_a_sample_is_never_taken_from_the_other_shop(self):
        consume_stock_fifo(self.product, 2, self.scentory, sample=True)
        with self.assertRaisesMessage(ValidationError, "no sample"):
            consume_stock_fifo(self.product, 1, self.scentory, sample=True)

    def test_more_than_both_shops_hold_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "Not enough stock"):
            consume_stock_fifo(self.product, 9, self.scentory)
        self.assertEqual((self.units(self.scentory), self.units(self.khan)), (2, 5))

    def test_a_shop_without_samples_sells_its_last_bottle(self):
        self.scentory.keeps_samples = False
        self.scentory.save()
        consume_stock_fifo(self.product, 2, self.scentory)
        self.assertEqual(self.units(self.scentory), 0)
        self.assertFalse(StockTransfer.objects.exists())

    def test_a_return_goes_back_to_the_shop_that_sold_it(self):
        consume_stock_fifo(self.product, 3, self.scentory)
        restore_stock_fifo(self.product, 3, self.scentory)
        self.assertEqual((self.units(self.scentory), self.units(self.khan)), (4, 3))

    def test_without_a_shop_all_batches_are_one_pool(self):
        consume_stock_fifo(self.product, 6)
        self.assertEqual(self.units(self.khan) + self.units(self.scentory), 1)


class MoveUnitsTests(ShopsMixin, TestCase):
    def test_a_split_keeps_cost_date_supplier_and_delivery(self):
        supplier = Supplier.objects.create(name="Sup")
        order = InboundOrder.objects.create(supplier=supplier)
        source = self.batch(self.khan, 10, cost="12", days_ago=40, supplier=supplier, inbound_order=order)
        source.remaining = 8
        Purchase.objects.filter(pk=source.pk).update(remaining=8)    # two sold already
        store_stock.move_units(self.product, 3, self.khan, self.scentory)
        source.refresh_from_db()
        moved = Purchase.objects.get(store=self.scentory)
        self.assertEqual((source.quantity, source.remaining), (7, 5))
        self.assertEqual((moved.quantity, moved.remaining), (3, 3))
        self.assertEqual(moved.cost_price, Decimal("12"))
        self.assertEqual(moved.date, source.date)
        self.assertEqual(moved.supplier, supplier)
        self.assertEqual(moved.inbound_order, order)
        # nothing was bought twice
        self.assertEqual(sum(Purchase.objects.values_list("quantity", flat=True)), 10)

    def test_a_whole_untouched_batch_just_changes_shop(self):
        b = self.batch(self.khan, 2)
        store_stock.move_units(self.product, 2, self.khan, self.scentory)
        b.refresh_from_db()
        self.assertEqual(b.store, self.scentory)
        self.assertEqual(Purchase.objects.count(), 1)

    def test_oldest_batches_move_first(self):
        self.batch(self.khan, 2, cost="8", days_ago=30)
        self.batch(self.khan, 2, cost="20")
        line = store_stock.move_units(self.product, 3, self.khan, self.scentory)
        self.assertEqual(line.cost_total, Decimal("36.00"))         # 8 + 8 + 20

    def test_the_opening_split_owes_nothing(self):
        self.batch(self.khan, 4)
        transfer = StockTransfer.objects.create(from_store=self.khan, to_store=self.scentory,
                                                reason=StockTransfer.OPENING)
        line = store_stock.move_units(self.product, 2, self.khan, self.scentory, transfer=transfer)
        self.assertEqual(line.amount, Decimal("0.00"))
        self.assertEqual(store_stock.balances(), [])

    def test_moving_more_than_the_shop_holds_is_refused(self):
        self.batch(self.khan, 1)
        with self.assertRaisesMessage(ValidationError, "has only 1"):
            store_stock.move_units(self.product, 2, self.khan, self.scentory)


class BalanceTests(ShopsMixin, TestCase):
    def test_transfers_both_ways_net_and_a_settlement_pays_them_off(self):
        self.batch(self.khan, 5, cost="10")
        self.batch(self.scentory, 5, cost="10")
        store_stock.move_units(self.product, 3, self.khan, self.scentory)     # Scentory owes 39
        store_stock.move_units(self.product, 1, self.scentory, self.khan)     # Khan owes 13
        [row] = store_stock.balances()
        self.assertEqual((row["debtor"], row["creditor"], row["amount"]),
                         (self.scentory, self.khan, Decimal("26.00")))
        store_stock.settle(self.scentory, self.khan, Decimal("26.00"), settled_on=timezone.localdate())
        self.assertEqual(store_stock.balances(), [])

    def test_a_settlement_must_have_an_amount(self):
        with self.assertRaises(ValidationError):
            store_stock.settle(self.scentory, self.khan, Decimal("0"), settled_on=timezone.localdate())


class EachShopsProfitTests(ShopsMixin, TestCase):
    """A transfer is a sale to the other shop at cost + markup (2026-11-01)."""

    def test_the_markup_is_plus_for_one_shop_and_minus_for_the_other(self):
        self.batch(self.khan, 5, cost="10")
        store_stock.move_units(self.product, 2, self.khan, self.scentory)
        today = timezone.localdate()
        self.assertEqual(store_stock.transfer_margin(self.khan, today, today), Decimal("6.00"))
        self.assertEqual(store_stock.transfer_margin(self.scentory, today, today), Decimal("-6.00"))
        self.assertEqual(sum(a for _s, _d, a in store_stock.transfer_margins(today, today)), 0)
        self.assertEqual(store_stock.transfer_margin(None, today, today), 0)

    def test_the_opening_split_and_other_days_carry_nothing(self):
        self.batch(self.khan, 5)
        opening = StockTransfer.objects.create(from_store=self.khan, to_store=self.scentory,
                                               reason=StockTransfer.OPENING)
        store_stock.move_units(self.product, 2, self.khan, self.scentory, transfer=opening)
        today = timezone.localdate()
        self.assertEqual(store_stock.transfer_margins(today, today), [])
        store_stock.move_units(self.product, 1, self.khan, self.scentory)
        yesterday = today - timedelta(days=1)
        self.assertEqual(store_stock.transfer_margins(yesterday, yesterday), [])

    def test_a_channel_counts_only_transfers_made_for_its_sales(self):
        self.batch(self.khan, 5)
        store_stock.move_units(self.product, 1, self.khan, self.scentory)        # manual
        today = timezone.localdate()
        self.assertEqual(store_stock.transfer_margin(self.khan, today, today, channel="retail"), 0)
        order = SaleOrder.objects.create(store=self.scentory)
        store_stock.bring_in_for_sale(self.product, 2, self.scentory, sale_order=order)
        self.assertEqual(store_stock.transfer_margin(self.khan, today, today, channel="retail"),
                         Decimal("6.00"))       # its one bottle is the sample: 2 brought down
        self.assertEqual(store_stock.transfer_margin(self.khan, today, today, channel="revenda"), 0)


class MergeBatchesTests(ShopsMixin, TestCase):
    def test_a_received_line_split_between_shops_is_one_line(self):
        order = InboundOrder.objects.create(status="received")
        store_stock.receive_batches(product=self.product, quantity=5, cost_price=Decimal("10"),
                                    allocation={self.khan.id: 3, self.scentory.id: 2}, inbound_order=order)
        store_stock.move_units(self.product, 1, self.khan, self.scentory)
        lines = store_stock.merge_batches(order.items.all())
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual((line.quantity, line.remaining, len(line.parts)), (5, 5, 3))
        self.assertEqual([(s["label"], s["quantity"]) for s in line.shops], [("Khan", 2), ("Scentory", 3)])

    def test_a_direct_stock_in_split_between_shops_is_one_moment(self):
        made = store_stock.receive_batches(product=self.product, quantity=3, cost_price=Decimal("10"),
                                           allocation={self.khan.id: 2, self.scentory.id: 1})
        dates = set(Purchase.objects.filter(pk__in=[b.pk for b in made]).values_list("date", flat=True))
        self.assertEqual(len(dates), 1)
        self.assertEqual(len(store_stock.merge_batches(Purchase.objects.all())), 1)

    def test_different_prices_or_deliveries_stay_apart(self):
        self.batch(self.khan, 2, cost="10", inbound_order=InboundOrder.objects.create())
        self.batch(self.khan, 2, cost="12", inbound_order=InboundOrder.objects.get())
        self.batch(self.scentory, 2, cost="10", inbound_order=InboundOrder.objects.create())
        lines = store_stock.merge_batches(Purchase.objects.order_by("id"))
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(line.shops == [] for line in lines))
