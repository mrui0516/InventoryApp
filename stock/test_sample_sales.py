"""Shop samples are sold, never refused, and always marked (2026-11-26).

A line takes full-price units first; what it needs beyond them comes out of
the shops' samples and becomes its own ``is_sample`` part. Only when all the
shops together lack units is a sale refused."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import StockTransfer, Store
from .services import store_stock
from .services.stock_ops import SalePart, consume_for_sale, split_for_sale
from .test_store_stock import ShopsMixin


class SplitTests(ShopsMixin, TestCase):
    def test_fits_full_price(self):
        self.batch(self.khan, 3)
        self.assertEqual(split_for_sale(self.product, 2, self.khan), [(2, False)])

    def test_one_over_takes_a_sample(self):
        self.batch(self.khan, 2)
        self.batch(self.scentory, 1)
        self.assertEqual(split_for_sale(self.product, 2, self.khan), [(1, False), (1, True)])
        parts = consume_for_sale(self.product, 2, self.khan)
        self.assertEqual([(p.quantity, p.is_sample) for p in parts], [(1, False), (1, True)])
        self.assertEqual((self.units(self.khan), self.units(self.scentory)), (0, 1))

    def test_only_samples_left(self):
        self.batch(self.khan, 1)
        self.batch(self.scentory, 1)
        self.assertEqual(split_for_sale(self.product, 2, self.khan), [(2, True)])
        consume_for_sale(self.product, 2, self.khan)
        self.assertEqual((self.units(self.khan), self.units(self.scentory)), (0, 0))
        self.assertEqual(StockTransfer.objects.filter(from_store=self.scentory, to_store=self.khan).count(), 1)

    def test_explicit_sample_from_the_other_shop(self):
        self.batch(self.scentory, 1, cost="12")
        parts = consume_for_sale(self.product, 1, self.khan, sample=True)
        self.assertEqual(parts, [SalePart(1, True, Decimal("12.00"))])
        self.assertEqual(self.units(self.scentory), 0)
        self.assertTrue(StockTransfer.objects.filter(from_store=self.scentory, to_store=self.khan).exists())

    def test_nothing_anywhere_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "Needed 1, both shops have 0"):
            consume_for_sale(self.product, 1, self.khan)
        with self.assertRaisesMessage(ValidationError, "Needed 1, both shops have 0"):
            consume_for_sale(self.product, 1, self.khan, sample=True)

    def test_without_shops_nothing_splits(self):
        Store.objects.update(is_active=False)
        self.batch(None, 2)
        self.assertEqual(split_for_sale(self.product, 2, None), [(2, False)])
        self.assertEqual(split_for_sale(self.product, 2, None, sample=True), [(2, True)])

    def test_obtainable_counts_full_price_everywhere(self):
        self.batch(self.khan, 3)
        self.batch(self.scentory, 2)
        self.batch(None, 1)
        self.assertEqual(store_stock.full_price_obtainable(self.product, self.khan), 2 + 1 + 1)
