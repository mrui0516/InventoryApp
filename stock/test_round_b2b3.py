"""Restock requests in detail, the catalogue's sort and filters, folding, and Pedido's source."""
from datetime import timedelta
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from stock.models import Concentration, PedidoDraft, Product, Purchase, RestockRequest
from stock.test_portal import PortalMixin


class RestockMixin(PortalMixin):
    def setUp(self):
        super().setUp()
        self.gone = self._product(name="Gone", units=0)
        self.activate()
        self.client.post(reverse("restock_request", args=[self.gone.pk]))
        self.client.force_login(self.boss)
        self.ask = RestockRequest.objects.get()

    def arrive(self, product=None):
        product = product or self.gone
        self._batch(product, 5, 0)
        Purchase.objects.filter(product=product).update(date=timezone.now() + timedelta(seconds=5))


class RestockTests(RestockMixin):
    def test_the_page_says_who_asked_and_how(self):
        page = self.client.get(reverse("restock_requests"))
        self.assertContains(page, "Loja Ana")
        self.assertContains(page, "B2B")
        self.assertContains(page, "912 345 678")

    def test_a_request_can_be_deleted(self):
        self.client.post(reverse("restock_delete", args=[self.ask.pk]))
        self.assertFalse(RestockRequest.objects.exists())

    def test_a_products_requests_can_be_cleared(self):
        RestockRequest.objects.create(product=self.gone, requested_by=self.boss)
        self.client.post(reverse("restock_clear", args=[self.gone.pk]))
        self.assertFalse(RestockRequest.objects.filter(product=self.gone).exists())

    def test_in_stock_leaves_the_open_list_and_waits_to_be_told(self):
        self.arrive()
        page = self.client.get(reverse("restock_requests"))
        self.assertEqual(page.context["rows"], [])
        self.assertEqual(len(page.context["arrived"]), 1)

    def test_stock_without_a_delivery_counts_too(self):
        Purchase.objects.create(product=self.gone, quantity=4, remaining=4, cost_price=D("5"), store=self.khan)
        Purchase.objects.filter(product=self.gone).update(date=timezone.now() - timedelta(days=400))
        self.assertEqual(len(self.client.get(reverse("restock_requests")).context["arrived"]), 1)

    def test_the_menu_shows_who_is_waiting_to_be_told(self):
        self.arrive()
        self.assertEqual(self.client.get(reverse("product_list")).context["restock_waiting"], 1)

    def test_receiving_says_customers_asked(self):
        from stock.models import InboundOrder, InboundPendingItem, Supplier
        order = InboundOrder.objects.create(supplier=Supplier.objects.create(name="S"), status="pending_receipt")
        line = InboundPendingItem.objects.create(inbound_order=order, product=self.gone, quantity=2, cost_price=D("5"),
                                                 allocation={str(self.khan.id): 2})
        response = self.client.post(reverse("inbound_receive", args=[order.pk]), {
            "action": "receive", "supplier": order.supplier_id, "shipping": "0", "pay_mode": "later",
            "lines-TOTAL_FORMS": 1, "lines-INITIAL_FORMS": 1, "lines-MIN_NUM_FORMS": 0, "lines-MAX_NUM_FORMS": 1000,
            "lines-0-id": line.pk, "lines-0-quantity": 2, "lines-0-cost_price": "5", "lines-0-shipping_share": "0",
            f"alloc-{line.pk}-{self.khan.pk}": 2, f"alloc-{line.pk}-{self.scentory.pk}": 0}, follow=True)
        self.assertContains(response, "asked for")


class CatalogueSortTests(PortalMixin):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.boss)
        edt = Concentration.objects.get_or_create(short="EDT", defaults={"name": "Eau de Toilette"})[0]
        self.cheap = self._product(name="Cheap", units=9, wholesale_price=D("10"), volume_ml=50)
        self.dear = self._product(name="Dear", units=9, wholesale_price=D("200"), volume_ml=100, concentration=edt)

    def pks(self, **params):
        return [r["pk"] for r in self.client.get(reverse("catalog"), params).context["page_obj"]]

    def test_price_up_and_down(self):
        up = self.pks(sort="price_asc")
        self.assertLess(up.index(self.cheap.pk), up.index(self.dear.pk))
        down = self.pks(sort="price_desc")
        self.assertLess(down.index(self.dear.pk), down.index(self.cheap.pk))

    def test_a_to_z(self):
        rows = self.client.get(reverse("catalog"), {"sort": "name"}).context["page_obj"]
        titles = [r["title"].lower() for r in rows]
        self.assertEqual(titles, sorted(titles))

    def test_newest_first(self):
        self.assertEqual(self.pks(sort="new")[0], self.dear.pk)

    def test_by_volume(self):
        self.assertEqual(self.pks(ml="50"), [self.cheap.pk])

    def test_by_strength(self):
        self.assertEqual(self.pks(strength="EDT"), [self.dear.pk])

    def test_the_choices_are_on_the_page(self):
        page = self.client.get(reverse("catalog"))
        for text in ('name="sort"', 'name="ml"', 'name="strength"', "Preço"):
            self.assertContains(page, text)

    def test_brand_rows_only_in_the_brand_order(self):
        self.assertNotContains(self.client.get(reverse("catalog"), {"sort": "price_asc"}), 'class="wc-group"')


class FoldTests(PortalMixin):
    def test_long_lists_fold(self):
        self.client.force_login(self.boss)
        page = self.client.get(reverse("order_requests"))
        self.assertContains(page, "data-fold=")

    def test_menu_groups_fold(self):
        self.client.force_login(self.boss)
        self.assertContains(self.client.get(reverse("product_list")), "data-nav-fold")


class PedidoSourceTests(PortalMixin):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.boss)
        self.client.post(reverse("set_active_store"), {"store": self.khan.id})
        self.app_draft = PedidoDraft.objects.create(store=self.khan, customer_name="Balcão")

    def test_pedido_no_longer_lists_drafts(self):
        self.assertNotContains(self.client.get(reverse("pedido")), 'class="pd-draft-list"')

    def test_the_editor_says_where_it_came_from(self):
        self.assertContains(self.client.get(reverse("pedido"), {"draft": self.app_draft.pk}), "App")

    def test_pedidos_marks_and_filters_the_source(self):
        page = self.client.get(reverse("order_requests"))
        self.assertContains(page, 'class="src app"')
        self.assertEqual(list(self.client.get(reverse("order_requests"), {"src": "b2b"}).context["drafts"]), [])
        self.assertEqual(list(self.client.get(reverse("order_requests"), {"src": "app"}).context["orders"]), [])
