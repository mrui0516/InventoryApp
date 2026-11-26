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


class SellTests(ShopsMixin, TestCase):
    """The till: a line beyond full-price stock is sold, its sample units split off."""

    def setUp(self):
        super().setUp()
        import json
        from django.contrib.auth import get_user_model
        from .stores import ACTIVE_STORE_SESSION_KEY
        self.json = json
        self.client.force_login(get_user_model().objects.create_superuser("ss_admin", password="pw123456"))
        session = self.client.session
        session[ACTIVE_STORE_SESSION_KEY] = self.khan.id
        session.save()

    def sell(self, *lines, token="0123456789abcdef0123456789abcd01"):
        from django.urls import reverse
        items = [{"barcode": self.product.barcode, "qty": qty, "price": "10.00", **({"sample": True} if sample else {})}
                 for qty, sample in lines]
        total = sum(qty for qty, _ in lines) * 10
        return self.client.post(reverse("outbound"), {
            "items_json": self.json.dumps(items),
            "payments_json": self.json.dumps([{"method": "card", "amount": f"{total}.00"}]),
            "submit_token": token})

    def sales(self):
        from .models import Sale
        return [(s.quantity, s.is_sample, s.unit_price, s.payment_method) for s in Sale.objects.order_by("id")]

    def test_one_over_full_price_is_recorded_as_two_lines(self):
        self.batch(self.khan, 2)
        self.assertEqual(self.sell((2, False)).status_code, 302)
        self.assertEqual(self.sales(), [(1, False, Decimal("10.00"), "card"), (1, True, Decimal("10.00"), "card")])

    def test_a_sample_line_of_two(self):
        self.batch(self.khan, 1)
        self.batch(self.scentory, 1)
        self.assertEqual(self.sell((2, True)).status_code, 302)
        self.assertEqual(self.sales(), [(2, True, Decimal("10.00"), "card")])
        self.assertEqual((self.units(self.khan), self.units(self.scentory)), (0, 0))

    def test_two_lines_of_one_product_share_the_stock(self):
        from .models import SaleOrder
        self.batch(self.khan, 2)
        self.assertEqual(self.sell((1, False), (1, True)).status_code, 302)
        self.assertEqual([s[:2] for s in self.sales()], [(1, False), (1, True)])
        self.batch(self.khan, 2)
        response = self.sell((2, False), (1, True), token="0123456789abcdef0123456789abcd02")
        self.assertContains(response, "Not enough stock")
        self.assertEqual(SaleOrder.objects.count(), 1)

    def test_nothing_anywhere_is_still_refused(self):
        from .models import SaleOrder
        response = self.sell((1, False))
        self.assertContains(response, "both shops have 0")
        self.assertEqual(SaleOrder.objects.count(), 0)

    def test_check_barcode_reports_the_total(self):
        from django.urls import reverse
        self.batch(self.khan, 1)
        self.batch(self.scentory, 1)
        data = self.client.get(reverse("check_barcode"), {"barcode": self.product.barcode}).json()
        self.assertEqual((data["stock"], data["total"]), (0, 2))
