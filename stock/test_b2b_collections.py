"""The catalogue's Novidades and Reposição, the side-scrolling rows and the cart bar."""
from datetime import timedelta
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from stock.models import Purchase, StockTransfer, StockTransferLine
from stock.services.wholesale_collections import NEW, RESTOCK, arrivals
from stock.test_portal import PortalMixin


class CollectionMixin(PortalMixin):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.boss)
        self.fresh = self._product(name="Fresh", units=5, days_ago=3)
        self.back = self._product(name="Back", units=5, days_ago=200)
        self._batch(self.back, 4, 6)
        self.old = self._product(name="Old", units=5, days_ago=200)

    def rows(self, **params):
        return {row["pk"]: row for row in self.client.get(reverse("catalog"), params).context["page_obj"]}


class ArrivalTests(CollectionMixin):
    def test_first_delivery_lately_is_new_a_later_one_is_a_restock(self):
        found = arrivals([self.fresh.pk, self.back.pk, self.old.pk])
        self.assertEqual(found[self.fresh.pk][0], NEW)
        self.assertEqual(found[self.back.pk][0], RESTOCK)
        self.assertNotIn(self.old.pk, found)

    def test_a_move_between_the_shops_is_not_a_delivery(self):
        transfer = StockTransfer.objects.create(from_store=self.khan, to_store=self.scentory)
        line = StockTransferLine.objects.create(transfer=transfer, product=self.old, quantity=1,
                                                cost_total=D("17.77"), amount=D("17.77"))
        batch = self._batch(self.old, 1, 1, store=self.scentory)
        Purchase.objects.filter(pk=batch.pk).update(transfer_line=line)
        self.assertNotIn(self.old.pk, arrivals([self.old.pk]))


class CataloguePageTests(CollectionMixin):
    def test_the_chips_count_each_collection(self):
        tabs = {t["value"]: t["n"] for t in self.client.get(reverse("catalog")).context["collection_tabs"]}
        self.assertEqual(tabs, {"new": 1, "restock": 1})

    def test_choosing_new_lists_only_new(self):
        self.assertEqual(list(self.rows(col="new")), [self.fresh.pk])

    def test_choosing_restock_lists_only_restocks(self):
        self.assertEqual(list(self.rows(col="restock")), [self.back.pk])

    def test_rows_are_flagged(self):
        page = self.client.get(reverse("catalog"))
        self.assertContains(page, 'class="wc-flag is-new"')
        self.assertContains(page, 'class="wc-flag is-restock"')

    def test_no_chips_when_nothing_arrived(self):
        Purchase.objects.update(date=timezone.now() - timedelta(days=300))
        self.assertNotContains(self.client.get(reverse("catalog")), 'class="wc-coll"')

    def test_the_flag_sits_outside_the_title(self):
        # Inside the title it pushed the name right and the rows no longer lined up.
        html = self.client.get(reverse("catalog")).content.decode()
        self.assertNotIn('<span class="wc-title"><span class="wc-flag', html)
        self.assertIn('class="wc-badge is-restock" role="img" aria-label="Reposto"', html)

    def test_filters_in_use_are_marked_and_cleared_in_one_place(self):
        html = self.client.get(reverse("catalog"), {"sort": "name"}).content.decode()
        self.assertIn('class="form-select is-set"', html)
        self.assertIn('class="wc-clear"', html)
        self.assertNotIn('class="fchip"', html)

    def test_side_scrolling_rows_have_arrows(self):
        page = self.client.get(reverse("catalog"))
        self.assertContains(page, "data-wc-scroll>", count=3)
        self.assertContains(page, "wc-scroll-btn next", count=3)


class CartBarTests(CollectionMixin):
    def test_adding_answers_with_the_progress(self):
        item = self._product(name="Item", units=9, wholesale_price=D("100"))
        data = self.client.post(reverse("wholesale_cart_add"), {"product": item.pk, "quantity": 1},
                                headers={"X-Requested-With": "fetch"}).json()
        self.assertGreater(data["percent"], 0)

    def test_the_customer_page_keeps_no_room_for_a_tab_bar(self):
        self.activate()
        self.assertContains(self.client.get(reverse("catalog")), "--tabbar-live:0px")
