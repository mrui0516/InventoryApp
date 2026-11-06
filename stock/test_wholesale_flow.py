"""An order after it is sent: the shop's Pedido, its status, and who can still change it."""
from decimal import Decimal as D
from unittest import mock

from django.urls import reverse

from stock.models import CartLine, InboundOrder, InboundPendingItem, OrderRequest, PedidoDraft, ProductContent, Supplier
from stock.services.wholesale_cart import add_to_cart
from stock.services.wholesale_orders import complete_from_sale
from stock.test_wholesale_orders import PagesMixin


class FlowMixin(PagesMixin):
    def _send(self, quantity=2):
        self._add(quantity=quantity)
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        self.client.post(reverse("wholesale_cart_submit"), {**self.form, "fingerprint": fingerprint, "note": "Urgente"})
        return OrderRequest.objects.latest("id")

    def _download(self, draft, **changes):
        session = self.client.session
        session["active_store"] = str(draft.store_id)
        session.save()
        with mock.patch("stock.pedido_views.resolve_active_store", return_value=(draft.store, False)):
            return self.client.post(reverse("pedido"), {
                "draft_id": draft.pk, "customer_name": draft.customer_name, "discount_kind": "amount",
                "product_id": [self.item.pk], "quantity": [changes.get("quantity", 2)],
                "unit_price": [changes.get("price", "100")], "shipping": changes.get("shipping", "0"),
                "note": draft.note, **({"save_only": "1"} if changes.get("save_only") else {})})


class ThePedidoTests(FlowMixin):
    def test_a_sent_order_lands_in_pedido(self):
        order = self._send()
        draft = order.pedido
        self.assertEqual((draft.status, draft.store, draft.total), ("open", self.khan, D("205")))
        self.assertEqual(draft.lines, [{"product_id": self.item.pk, "quantity": 2, "unit_price": "100.00"}])
        self.assertEqual((draft.customer_name, draft.customer_phone), ("Ana", "912"))
        self.assertIn("Rua 1", draft.customer_address)

    def test_the_shipping_goes_with_it(self):
        self.assertEqual(self._send().pedido.shipping, D("5"))

    def test_the_customers_choice_is_in_the_note(self):
        mix = self._product(name="Mix", units=9, is_mix=True, product_type_id="body_spray", wholesale_price=D("100"))
        one = ProductContent.objects.create(owner=mix, name="Yara", sort_order=1)
        ProductContent.objects.create(owner=mix, name="Asad", sort_order=2)
        add_to_cart(self.boss, mix, None, picks=[{"id": one.pk, "quantity": 3}])
        note = self._send().pedido.note
        self.assertIn("3 × Yara", note)
        self.assertIn("Urgente", note)

    def test_the_draft_list_says_where_it_came_from(self):
        order = self._send()
        with mock.patch("stock.pedido_views.resolve_active_store", return_value=(order.pedido.store, False)):
            self.assertContains(self.client.get(reverse("pedido")), f"Online #{order.pk}")


class TheCustomerSideTests(FlowMixin):
    def test_after_sending_the_customer_is_told_what_happens(self):
        order = self._send()
        page = self.client.get(reverse("wholesale_order", args=[order.public_id]))
        self.assertContains(page, "Vamos confirmar o stock")
        self.assertContains(page, "920 106 263")

    def test_while_waiting_it_can_be_changed_or_cancelled(self):
        page = self.client.get(reverse("wholesale_order", args=[self._send().public_id]))
        self.assertContains(page, "Alterar pedido")
        self.assertContains(page, "Cancelar pedido")

    def test_changing_puts_it_back_in_the_cart(self):
        order = self._send()
        self.client.post(reverse("wholesale_order_edit", args=[order.public_id]))
        self.assertEqual(CartLine.objects.get(user=self.boss).quantity, 2)
        self.assertContains(self.client.get(reverse("wholesale_cart")), f"pedido #{order.pk}")

    def test_sending_again_keeps_the_same_order_and_pedido(self):
        order = self._send()
        draft_id = order.pedido_id
        self.client.post(reverse("wholesale_order_edit", args=[order.public_id]))
        self.client.post(reverse("wholesale_cart_update"), {"product": self.item.pk, "quantity": 3})
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        self.client.post(reverse("wholesale_cart_submit"), {**self.form, "fingerprint": fingerprint})
        self.assertEqual(OrderRequest.objects.count(), 1)
        order.refresh_from_db()
        self.assertEqual((order.units, order.pedido_id, order.goods_total), (3, draft_id, D("300")))
        self.assertEqual(PedidoDraft.objects.get().lines[0]["quantity"], 3)
        self.assertFalse(CartLine.objects.exists())

    def test_cancelling_also_drops_the_pedido(self):
        order = self._send()
        self.client.post(reverse("wholesale_order_cancel", args=[order.public_id]))
        order.refresh_from_db()
        self.assertEqual((order.status, PedidoDraft.objects.count()), ("cancelled", 0))


class ConfirmingTests(FlowMixin):
    def test_the_pdf_confirms_the_stock(self):
        order = self._send()
        self._download(order.pedido, shipping="5")
        order.refresh_from_db()
        self.assertEqual(order.status, "confirmed")
        self.assertIsNotNone(order.confirmed_at)

    def test_saving_does_not_confirm(self):
        order = self._send()
        self._download(order.pedido, shipping="5", save_only=True)
        order.refresh_from_db()
        self.assertEqual(order.status, "submitted")

    def test_a_confirmed_order_is_closed_to_the_customer(self):
        order = self._send()
        self._download(order.pedido, shipping="5")
        page = self.client.get(reverse("wholesale_order", args=[order.public_id]))
        self.assertNotContains(page, "Alterar pedido")
        self.assertNotContains(page, "Cancelar pedido")
        self.client.post(reverse("wholesale_order_cancel", args=[order.public_id]))
        self.client.post(reverse("wholesale_order_edit", args=[order.public_id]))
        order.refresh_from_db()
        self.assertEqual((order.status, CartLine.objects.count()), ("confirmed", 0))

    def test_an_edit_in_progress_cannot_overrule_the_confirmation(self):
        order = self._send()
        self.client.post(reverse("wholesale_order_edit", args=[order.public_id]))
        self._download(order.pedido, shipping="5")
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        self.client.post(reverse("wholesale_cart_submit"), {**self.form, "fingerprint": fingerprint})
        self.assertEqual(OrderRequest.objects.count(), 1)
        self.assertEqual(OrderRequest.objects.get().status, "confirmed")
        self.assertFalse(CartLine.objects.exists())

    def test_the_customer_is_told_when_that_happens(self):
        order = self._send()
        self.client.post(reverse("wholesale_order_edit", args=[order.public_id]))
        self._download(order.pedido, shipping="5")
        self.assertContains(self.client.get(reverse("wholesale_cart")), "foi confirmado")

    def test_what_the_shop_changed_reaches_the_customer(self):
        order = self._send()
        self._download(order.pedido, quantity=1, price="90", shipping="7")
        order.refresh_from_db()
        line = order.lines.get()
        self.assertEqual((line.quantity, line.final_price, line.name), (1, D("90"), line.name))
        self.assertEqual((order.goods_total, order.shipping_fee, order.total), (D("90"), D("7"), D("97")))

    def test_the_shop_can_still_cancel(self):
        order = self._send()
        self._download(order.pedido, shipping="5")
        self.client.post(reverse("order_requests"), {"id": order.pk, "action": "cancel"})
        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")

    def test_deleting_the_pedido_cancels_the_order(self):
        order = self._send()
        draft = order.pedido
        with mock.patch("stock.pedido_views.resolve_active_store", return_value=(draft.store, False)):
            self.client.post(reverse("pedido_draft_delete", args=[draft.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")

    def test_the_shop_page_links_to_pedido(self):
        order = self._send()
        self.assertContains(self.client.get(reverse("order_requests")), f"?draft={order.pedido_id}")


class CompletingTests(FlowMixin):
    def test_selling_the_pedido_completes_it(self):
        order = self._send()
        complete_from_sale(order.pedido, None)
        order.refresh_from_db()
        self.assertEqual(order.status, "completed")
        self.assertIsNotNone(order.completed_at)
        self.assertContains(self.client.get(reverse("wholesale_order", args=[order.public_id])), "Concluído")

    def test_a_done_order_cannot_be_cancelled(self):
        order = self._send()
        complete_from_sale(order.pedido, None)
        self.client.post(reverse("order_requests"), {"id": order.pk, "action": "cancel"})
        order.refresh_from_db()
        self.assertEqual(order.status, "completed")

    def test_a_draft_from_the_shop_alone_is_left_alone(self):
        draft = PedidoDraft.objects.create(store=self.khan)
        complete_from_sale(draft, None)
        self.assertFalse(OrderRequest.objects.exists())


class OnTheWayTests(FlowMixin):
    def _on_way(self, product):
        order = InboundOrder.objects.create(supplier=Supplier.objects.create(name="S"), status="pending_receipt")
        InboundPendingItem.objects.create(inbound_order=order, product=product, quantity=5, cost_price=D("1"))

    def _row(self, product, **params):
        rows = self.client.get(reverse("catalog"), params).context["page_obj"]
        return next((row for row in rows if row["pk"] == product.pk), None)

    def test_sold_out_but_on_the_way_is_on_show_and_can_be_ordered(self):
        item = self._product(name="Soon", units=0)
        self._on_way(item)
        row = self._row(item)
        self.assertEqual((row["state"], row["state_label"]), ("incoming", "Brevemente em stock"))
        self.assertContains(self.client.get(reverse("catalog")), f'value="{item.pk}"')
        self._add(item, 3)
        self.assertEqual(CartLine.objects.get(product=item).quantity, 3)

    def test_it_can_be_sent(self):
        item = self._product(name="Soon", units=0, wholesale_price=D("100"))
        self._on_way(item)
        self.item.delete()
        order = self._send_item(item)
        self.assertEqual(order.units, 2)

    def _send_item(self, item):
        self._add(item, 2)
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        self.client.post(reverse("wholesale_cart_submit"), {**self.form, "fingerprint": fingerprint})
        return OrderRequest.objects.latest("id")

    def test_a_little_left_and_more_coming_says_both(self):
        item = self._product(name="Few", units=2)
        self._on_way(item)
        self.assertEqual(self._row(item)["state_label"], "Mais a caminho")

    def test_plenty_left_says_nothing_more(self):
        self._on_way(self.item)
        self.assertEqual(self._row(self.item)["state_label"], "Disponível")

    def test_sold_out_and_not_coming_cannot_be_ordered(self):
        item = self._product(name="Gone", units=0)
        self.assertIsNone(self._row(item))
        self._add(item, 1)
        self.assertFalse(CartLine.objects.filter(product=item).exists())
