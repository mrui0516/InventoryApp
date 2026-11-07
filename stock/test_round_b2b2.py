"""Decant picks, phone prefixes, restock WhatsApp, one orders page, the Stock menu, quick prices."""
import json
from decimal import Decimal as D
from urllib.parse import unquote

from django.urls import reverse
from django.utils import timezone

from stock.models import (CartLine, Customer, CustomerAccount, OrderRequest, PedidoDraft, Product, ProductContent,
                          Purchase, RestockRequest)
from stock.services import portal
from stock.services.contacts import with_prefix
from stock.services.wholesale_picks import perfume_choices
from stock.test_portal import PASSWORD, PortalMixin
from stock.test_wholesale_picks import PicksMixin


class DecantPicksTests(PicksMixin):
    def ids(self, **kw):
        return [row["id"] for row in perfume_choices(**kw)]

    def test_a_perfume_down_to_its_sample_can_be_chosen(self):
        last = self._product(name="Yara Last", units=1)
        self.assertIn(last.pk, self.ids(query="yara"))
        self.assertEqual(self._post(self.sample, [{"id": last.pk, "quantity": 1}]).status_code, 200)

    def test_a_sold_out_perfume_cannot(self):
        gone = self._product(name="Yara Gone", units=0)
        self.assertNotIn(gone.pk, self.ids(query="yara"))

    def test_a_brand_lists_without_typing(self):
        other = self._product(name="Oud", brand="Rasasi", units=4)
        self.assertEqual(self.ids(brand="Rasasi"), [other.pk])

    def test_by_gender(self):
        woman = self._product(name="Yara Rosa", units=4, gender="women")
        self.assertEqual(self.ids(query="yara", gender="women"), [woman.pk])

    def test_a_to_z(self):
        b = self._product(name="Yara B", units=4)
        a = self._product(name="Yara A", units=4)
        names = [row["name"] for row in perfume_choices(query="yara", sort="name")]
        self.assertEqual(names, sorted(names))
        self.assertLess(names.index(next(n for n in names if n.endswith("Yara A"))),
                        names.index(next(n for n in names if n.endswith("Yara B"))))

    def test_the_search_answers_with_filters(self):
        self._product(name="Oud", brand="Rasasi", units=4)
        rows = self.client.get(reverse("wholesale_pick_search"), {"brand": "Rasasi"}).json()["results"]
        self.assertEqual(len(rows), 1)

    def test_the_panel_offers_brand_and_sort(self):
        page = self.client.get(reverse("catalog")).content.decode()
        panel = page.split(f'id="wc-pick-{self.sample.pk}"')[1].split("</template>")[0]
        self.assertIn('data-wc-pick-brand', panel)
        self.assertIn('data-wc-pick-sort', panel)


class PhonePrefixTests(PortalMixin):
    def test_the_helper(self):
        self.assertEqual(with_prefix("34", "612 345 678"), "+34 612 345 678")
        self.assertEqual(with_prefix("351", "912 345 678"), "912 345 678")
        self.assertEqual(with_prefix("34", "+44 7700 900123"), "+44 7700 900123")

    def test_adding_a_spanish_customer(self):
        self.client.force_login(self.boss)
        self.client.post(reverse("add_customer_ajax"), {"name": "Tienda", "phone": "612 345 678", "phone_prefix": "34",
                                                        "kind": "revenda"})
        self.assertEqual(Customer.objects.get(name="Tienda").phone, "+34 612 345 678")

    def test_editing_keeps_the_prefix(self):
        shop = Customer.objects.create(name="Tienda", kind="revenda", phone="+34 612 345 678")
        self.client.force_login(self.boss)
        page = self.client.get(reverse("edit_customer", args=[shop.pk]))
        self.assertContains(page, 'value="34" selected')
        self.client.post(reverse("edit_customer", args=[shop.pk]), {"name": "Tienda", "kind": "revenda",
                                                                    "phone_prefix": "33", "phone": "612 345 678"})
        shop.refresh_from_db()
        self.assertEqual(shop.phone, "+33 612 345 678")

    def test_a_foreign_customer_logs_in(self):
        shop = Customer.objects.create(name="Tienda", kind="revenda", phone="+34 612 345 678")
        token = self.token_of(self.invite(shop))
        self.client.logout()
        self.client.post(reverse("portal_invite", args=[token]), {"phone_prefix": "34", "phone": "612345678",
                                                                  "password1": PASSWORD, "password2": PASSWORD})
        self.client.logout()
        response = self.client.post(reverse("portal_login"), {"phone_prefix": "34", "phone": "612 345 678",
                                                              "password": PASSWORD})
        self.assertRedirects(response, reverse("catalog"), fetch_redirect_response=False)


class RestockWhatsappTests(PortalMixin):
    def setUp(self):
        super().setUp()
        self.gone = self._product(name="Gone", units=0)
        self.activate()
        self.client.post(reverse("restock_request", args=[self.gone.pk]))
        self.client.force_login(self.boss)

    def arrive(self):
        from datetime import timedelta
        self._batch(self.gone, 5, 0)
        Purchase.objects.filter(product=self.gone).update(date=timezone.now() + timedelta(seconds=5))

    def test_the_request_names_the_customer(self):
        self.assertEqual(RestockRequest.objects.get().customer, self.shop)

    def test_a_staff_preview_names_the_customer_too(self):
        other = self._product(name="Other", units=0)
        self.client.post(reverse("wholesale_customer"), {"customer": self.shop.pk})
        self.client.post(reverse("restock_request", args=[other.pk]))
        self.assertEqual(RestockRequest.objects.get(product=other).customer, self.shop)

    def test_nothing_to_tell_before_it_arrives(self):
        self.assertNotContains(self.client.get(reverse("restock_requests")), "wa.me")

    def test_after_the_delivery_each_customer_has_a_button(self):
        self.arrive()
        page = self.client.get(reverse("restock_requests"))
        self.assertContains(page, "Loja Ana")
        self.assertContains(page, reverse("restock_notify", args=[RestockRequest.objects.get().pk]))

    def test_the_button_opens_whatsapp_and_marks_it_told(self):
        self.arrive()
        ask = RestockRequest.objects.get()
        response = self.client.post(reverse("restock_notify", args=[ask.pk]))
        self.assertTrue(response["Location"].startswith("https://wa.me/351912345678?text="))
        self.assertIn("Gone", unquote(response["Location"]))
        ask.refresh_from_db()
        self.assertIsNotNone(ask.notified_at)
        self.assertNotContains(self.client.get(reverse("restock_requests")), reverse("restock_notify", args=[ask.pk]))


class OneOrdersPageTests(PortalMixin):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.boss)
        self.draft = PedidoDraft.objects.create(store=self.khan, customer_name="Balcão Lda", total=D("50"))

    def test_shop_drafts_show_beside_online_orders(self):
        page = self.client.get(reverse("order_requests"))
        self.assertContains(page, "Balcão Lda")
        self.assertContains(page, f"?draft={self.draft.pk}&amp;switch=1")
        self.assertContains(page, "New Pedido")

    def test_the_tab_lists_drafts(self):
        old = OrderRequest.objects.create(contact_name="Old", phone="1", subtotal=0, tier_discount=0, tier_total=0,
                                          goods_total=0, shipping_state="pickup", status="cancelled")
        self.assertNotIn(old, list(self.client.get(reverse("order_requests")).context["orders"]))
        self.assertIn(old, list(self.client.get(reverse("order_requests"), {"show": "all"}).context["orders"]))
        page = self.client.get(reverse("order_requests"), {"show": "drafts"})
        self.assertEqual([d.pk for d in page.context["drafts"]], [self.draft.pk])
        self.assertEqual(list(page.context["orders"]), [])

    def test_opening_a_draft_from_there_switches_shop(self):
        session = self.client.session
        session["active_store_id"] = "all"
        session.save()
        response = self.client.get(reverse("pedido"), {"draft": self.draft.pk, "switch": "1"})
        self.assertEqual(self.client.session["active_store_id"], self.khan.id)
        self.assertEqual(response.status_code, 302)


class MenuTests(PortalMixin):
    def nav(self):
        self.client.force_login(self.boss)
        self.client.post(reverse("set_active_store"), {"store": self.khan.id})
        html = self.client.get(reverse("product_list")).content.decode()
        start = html.index('<nav class="erp-nav">')
        return html[start:html.index("</nav>", start)]

    def test_stock_then_products_then_b2b(self):
        nav = self.nav()
        order = [nav.index(f">{g}<") for g in ("Stock", "Products", "B2B", "Customers")]
        self.assertEqual(order, sorted(order))
        stock = nav[nav.index(">Stock<"):nav.index(">Products<")]
        for name in ("outbound", "inbound", "stock_transfers", "supplier_list"):
            self.assertIn(f'href="{reverse(name)}"', stock, name)
        self.assertNotIn(">Purchasing<", nav)

    def test_pedido_lives_in_the_orders_page(self):
        nav = self.nav()
        b2b = nav[nav.index(">B2B<"):nav.index(">Customers<")]
        self.assertIn("Pedidos", b2b)
        self.assertNotIn(f'href="{reverse("pedido")}"', nav)


class QuickPricesTests(PortalMixin):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.boss)

    def test_the_page_lists_prices_as_fields(self):
        page = self.client.get(reverse("product_prices"), {"q": "Item"})
        self.assertContains(page, f'name="wholesale-{self.item.pk}"')
        self.assertContains(page, f'name="retail-{self.item.pk}"')

    def test_only_what_changed_is_saved_and_locked(self):
        other = self._product(name="Other", units=1, wholesale_price=D("20"), default_price=D("30"))
        self.client.post(reverse("product_prices"), {
            f"wholesale-{self.item.pk}": "110", f"retail-{self.item.pk}": "",
            f"was-wholesale-{self.item.pk}": "100.00", f"was-retail-{self.item.pk}": "",
            f"wholesale-{other.pk}": "20.00", f"retail-{other.pk}": "30.00",
            f"was-wholesale-{other.pk}": "20.00", f"was-retail-{other.pk}": "30.00"})
        self.item.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((self.item.wholesale_price, self.item.price_locked), (D("110.00"), True))
        self.assertEqual(other.wholesale_price, D("20.00"))

    def test_a_wrong_number_saves_nothing(self):
        response = self.client.post(reverse("product_prices"), {f"wholesale-{self.item.pk}": "abc",
                                                                f"was-wholesale-{self.item.pk}": "100.00"})
        self.item.refresh_from_db()
        self.assertEqual(self.item.wholesale_price, D("100"))
        self.assertEqual(response.status_code, 200)

    def test_the_list_offers_it(self):
        self.assertContains(self.client.get(reverse("product_list")), reverse("product_prices"))

    def test_an_employee_cannot(self):
        from django.contrib.auth import get_user_model
        self.client.force_login(get_user_model().objects.create_user("emp", password="pw"))
        self.assertNotEqual(self.client.get(reverse("product_prices")).status_code, 200)
