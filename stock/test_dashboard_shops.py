"""Dashboard: with one shop picked, every figure is that shop's own."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.utils import timezone

from stock.models import Purchase, Sale, SaleOrder
from stock.services.dashboard import build_monthly_dashboard_snapshot
from stock.services.sell_through import sell_through
from stock.test_portal import PortalMixin


class ShopFiguresTests(PortalMixin):
    def setUp(self):
        super().setUp()
        self.item = self._product(name="Spend", units=0)
        self.today = timezone.localdate()
        self.start = self.today.replace(day=1)
        khan = self._batch(self.item, 10, 0, store=self.khan)
        scent = self._batch(self.item, 4, 0, store=self.scentory)
        Purchase.objects.filter(pk=khan.pk).update(cost_price=D("10"), received_store=self.khan)
        Purchase.objects.filter(pk=scent.pk).update(cost_price=D("10"), received_store=self.scentory)

    def spend(self, store=None):
        return build_monthly_dashboard_snapshot(self.start, self.today, store=store)["month_purchase_amount"]

    def test_purchase_spend_follows_the_shop(self):
        self.assertEqual(self.spend(self.khan), D("100"))
        self.assertEqual(self.spend(self.scentory), D("40"))
        self.assertEqual(self.spend(), D("140"))

    def test_a_transfer_neither_adds_nor_moves_spend(self):
        Purchase.objects.filter(received_store=self.khan).update(store=self.scentory)
        self.assertEqual(self.spend(self.khan), D("100"))
        self.assertEqual(self.spend(self.scentory), D("40"))
        self.assertEqual(self.spend(), D("140"))

    def test_top_suppliers_are_the_shops_own(self):
        rows = build_monthly_dashboard_snapshot(self.start, self.today, store=self.scentory)["month_top_suppliers"]
        self.assertEqual(sum(r["amount"] for r in rows), D("40"))

    def test_sell_through_reads_one_shops_shelf(self):
        self.assertEqual(sell_through(self.today, store=self.khan), sell_through(self.today, store=self.khan))
        Sale.objects.create(order=SaleOrder.objects.create(store=self.scentory), product=self.item, quantity=1,
                            unit_price=D("20"), payment_method="cash", store=self.scentory)
        self.assertIsInstance(sell_through(self.today, store=self.scentory), list)
