"""The customer's way through the catalogue: cart, order rules, request, my orders."""
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from stock.models import CartLine, Customer, OrderRequest, Product, Purchase, Sale
from stock.services.wholesale_benefits import MAX_QUANTITY, build_quote, progress, tier_for, zone_of
from stock.services.wholesale_cart import add_to_cart, cart_quote, cart_units, set_quantity, submit_request
from stock.services.wholesale_catalog import catalog_rows, customer_price
from stock.test_wholesale_catalog import CatalogMixin

MAINLAND = {'delivery': 'delivery', 'country': 'PT', 'postal_code': '2700-123'}
AZORES = {'delivery': 'delivery', 'country': 'PT', 'postal_code': '9500-100'}
MADEIRA = {'delivery': 'delivery', 'country': 'PT', 'postal_code': '9000-001'}
SPAIN = {'delivery': 'delivery', 'country': 'ES', 'postal_code': '28001'}
PICKUP = {'delivery': 'pickup'}
NOWHERE = {'delivery': 'delivery'}


def line(price, quantity=1, base=None, discount=D('0')):
    return {'product_id': 1, 'name': 'X', 'barcode': '1', 'quantity': quantity, 'base_price': D(base or price),
            'customer_discount': discount, 'catalog_price': D(price), 'ok': True}


class TierTests(SimpleTestCase):
    def test_the_thresholds_include_the_exact_amount(self):
        for amount, tier in (("499.99", 0), ("500", 1), ("999.99", 1), ("1000", 2), ("5000", 2)):
            self.assertEqual(tier_for(D(amount)), D(tier), amount)

    def test_the_top_tier_replaces_the_one_below(self):
        self.assertEqual(build_quote([line("100", 10)], MAINLAND)["lines"][0]["final_price"], D("98"))

    def test_the_discount_counts_units_not_products(self):
        quote = build_quote([line("25", 20)], MAINLAND)
        self.assertEqual(quote["subtotal"], D("500"))
        self.assertEqual(quote["tier_total"], D("20"))
        self.assertEqual(quote["goods_total"], D("480"))

    def test_the_tier_is_read_before_it_is_applied(self):
        quote = build_quote([line("25", 20)], MAINLAND)
        self.assertEqual(quote["tier_discount"], D("1"))
        self.assertTrue(quote["free_shipping"])

    def test_it_stacks_on_the_customer_discount(self):
        quote = build_quote([line("14", 80, base="15", discount=D("1"))], MAINLAND)
        self.assertEqual(quote["subtotal"], D("1120"))
        self.assertEqual(quote["lines"][0]["final_price"], D("12"))


class MinimumTests(SimpleTestCase):
    def test_below_the_minimum_cannot_be_sent(self):
        quote = build_quote([line("149.99")], MAINLAND)
        self.assertFalse(quote["can_submit"])
        self.assertEqual(quote["short_of_minimum"], D("0.01"))

    def test_the_exact_minimum_can(self):
        self.assertTrue(build_quote([line("150")], MAINLAND)["can_submit"])

    def test_an_empty_cart_cannot(self):
        self.assertFalse(build_quote([], MAINLAND)["can_submit"])

    def test_a_line_that_is_not_ok_stops_it(self):
        bad = line("200")
        bad["ok"] = False
        self.assertFalse(build_quote([bad], MAINLAND)["can_submit"])

    def test_a_price_at_or_below_zero_stops_it(self):
        quote = build_quote([line("1", 500)], MAINLAND)
        self.assertEqual(quote["subtotal"], D("500"))
        self.assertTrue(quote["lines"][0]["needs_price"])
        self.assertFalse(quote["can_submit"])


class ZoneTests(SimpleTestCase):
    def test_the_zones(self):
        for destination, zone in ((MAINLAND, "mainland"), (AZORES, "islands"), (MADEIRA, "islands"), (SPAIN, "eu"),
                                  (NOWHERE, "unknown"), (PICKUP, "pickup")):
            self.assertEqual(zone_of(destination), zone, destination)

    def test_a_bad_postal_code_is_unknown_not_mainland(self):
        for code in ("2700", "abcd-123", "0999-000", "9400-000", ""):
            self.assertEqual(zone_of({"delivery": "delivery", "country": "PT", "postal_code": code}), "unknown", code)


class ShippingTests(SimpleTestCase):
    def _state(self, amount, destination):
        return build_quote([line(amount)], destination)["shipping_state"]

    def test_mainland_is_free_from_300(self):
        self.assertEqual(self._state("299.99", MAINLAND), "pending")
        self.assertEqual(self._state("300", MAINLAND), "free")

    def test_islands_and_the_eu_stay_to_be_confirmed(self):
        for destination in (AZORES, SPAIN):
            self.assertEqual(self._state("5000", destination), "pending")

    def test_an_unknown_destination_is_never_free(self):
        self.assertEqual(self._state("5000", NOWHERE), "pending")

    def test_pickup_is_pickup_not_an_offer(self):
        self.assertEqual(self._state("10", PICKUP), "pickup")


class ProgressTests(SimpleTestCase):
    def _text(self, amount, destination=MAINLAND):
        return progress(D(amount), zone_of(destination))["message"]

    def test_the_messages(self):
        self.assertEqual(self._text("120"), "Faltam 30€ para o pedido mínimo de 150€.")
        self.assertEqual(self._text("240"), "Faltam 60€ para portes grátis em Portugal Continental.")
        self.assertEqual(self._text("420"), "Faltam 80€ para mais 1€ de desconto por unidade.")
        self.assertEqual(self._text("750"), "Faltam 250€ para 2€ de desconto por unidade (agora 1€).")
        self.assertEqual(self._text("1000"), "Escalão máximo: 2€ extra de desconto por unidade.")

    def test_cents_are_shown_when_there_are_any(self):
        self.assertEqual(self._text("149.50"), "Faltam 0.50€ para o pedido mínimo de 150€.")

    def test_no_free_shipping_promise_outside_the_mainland(self):
        for destination in (AZORES, SPAIN, PICKUP):
            self.assertEqual(self._text("240", destination), "Faltam 260€ para mais 1€ de desconto por unidade.")

    def test_the_bar_has_four_marks(self):
        self.assertEqual([m["amount"] for m in progress(D("0"), "unknown")["marks"]], [150, 300, 500, 1000])

    def test_the_bar_does_not_run_past_the_end(self):
        self.assertEqual(progress(D("9000"), "mainland")["percent"], 100)


class PriceTests(CatalogMixin):
    def setUp(self):
        super().setUp()
        self.customer = Customer.objects.create(name="Loja", kind="revenda", wholesale_discount=D("2"))

    def test_no_customer_pays_wholesale(self):
        self.assertEqual(customer_price(self._product(units=9, volume_ml=100), None), (D("20"), D("0")))

    def test_the_customer_discount_comes_off_what_it_applies_to(self):
        self.assertEqual(customer_price(self._product(units=9, volume_ml=100), self.customer), (D("18"), D("2")))

    def test_it_does_not_apply_outside_the_rule(self):
        self.assertEqual(customer_price(self._product(units=9, volume_ml=50), self.customer), (D("20"), D("0")))
        spray = self._product(units=9, volume_ml=100, product_type_id="body_spray")
        self.assertEqual(customer_price(spray, self.customer), (D("20"), D("0")))

    def test_a_price_it_would_wipe_out_needs_confirming(self):
        Customer.objects.filter(pk=self.customer.pk).update(wholesale_discount=D("25"))
        self.customer.refresh_from_db()
        self.assertEqual(customer_price(self._product(units=9, volume_ml=100), self.customer)[0], None)

    def test_no_wholesale_price_has_no_price(self):
        self.assertEqual(customer_price(self._product(units=9, wholesale_price=None), None)[0], None)

    def test_the_catalogue_shows_the_customer_price(self):
        self._product(units=9, volume_ml=100)
        row = catalog_rows(customer=self.customer)[0]
        self.assertEqual((row["price"], row["base_price"]), (D("18"), D("20")))


class CartTests(CatalogMixin):
    def setUp(self):
        super().setUp()
        self.item = self._product(name="Item", units=9, wholesale_price=D("50"))
        self.other = User.objects.create_user("other", password="pw")

    def test_adding_twice_adds_up(self):
        add_to_cart(self.boss, self.item, 2)
        add_to_cart(self.boss, self.item, 3)
        self.assertEqual(cart_units(self.boss), 5)

    def test_each_person_has_their_own_cart(self):
        add_to_cart(self.boss, self.item, 2)
        self.assertEqual(cart_units(self.other), 0)

    def test_a_sold_out_product_cannot_be_added(self):
        with self.assertRaises(ValueError):
            add_to_cart(self.boss, self._product(name="Gone", units=0), 1)

    def test_a_product_without_a_price_cannot_be_added(self):
        with self.assertRaises(ValueError):
            add_to_cart(self.boss, self._product(name="X", units=9, wholesale_price=None), 1)

    def test_a_silly_quantity_is_refused(self):
        for quantity in (0, -1, MAX_QUANTITY + 1):
            with self.assertRaises(ValueError):
                add_to_cart(self.boss, self.item, quantity)
        self.assertEqual(cart_units(self.boss), 0)

    def test_adding_cannot_pass_the_limit(self):
        add_to_cart(self.boss, self.item, MAX_QUANTITY)
        with self.assertRaises(ValueError):
            add_to_cart(self.boss, self.item, 1)

    def test_any_quantity_up_to_the_limit_is_taken_so_stock_cannot_be_probed(self):
        add_to_cart(self.boss, self.item, 500)
        self.assertEqual(cart_units(self.boss), 500)

    def test_setting_zero_removes_the_line(self):
        add_to_cart(self.boss, self.item, 2)
        set_quantity(self.boss, self.item.pk, 0)
        self.assertEqual(cart_units(self.boss), 0)

    def test_a_product_that_sold_out_meanwhile_blocks_the_order(self):
        add_to_cart(self.boss, self.item, 4)
        Purchase.objects.filter(product=self.item).update(remaining=0)
        quote = cart_quote(self.boss, None, MAINLAND)
        self.assertFalse(quote["lines"][0]["ok"])
        self.assertFalse(quote["can_submit"])

    def test_an_archived_product_leaves_the_cart(self):
        add_to_cart(self.boss, self.item, 2)
        Product.objects.filter(pk=self.item.pk).update(archived_at=timezone.now())
        self.assertEqual(cart_quote(self.boss, None, MAINLAND)["lines"], [])


class SubmitTests(CatalogMixin):
    def setUp(self):
        super().setUp()
        self.item = self._product(name="Item", units=9, wholesale_price=D("100"), volume_ml=100)
        add_to_cart(self.boss, self.item, 2)
        self.contact = {"contact_name": "Ana", "phone": "912", "email": "", "business_name": "", "address": "Rua 1",
                        "city": "Amadora", "note": "", **MAINLAND}

    def _print(self, customer=None):
        return cart_quote(self.boss, customer, MAINLAND)["fingerprint"]

    def _submit(self, customer=None, fingerprint=None):
        return submit_request(self.boss, customer, self.contact, fingerprint or self._print(customer))

    def test_a_request_keeps_every_part_of_the_price(self):
        customer = Customer.objects.create(name="Loja", kind="revenda", wholesale_discount=D("5"))
        add_to_cart(self.boss, self.item, 4)
        order = self._submit(customer)
        row = order.lines.get()
        self.assertEqual((row.quantity, row.base_price, row.customer_discount, row.catalog_price, row.tier_discount,
                          row.final_price, row.line_total),
                         (6, D("100"), D("5"), D("95"), D("1"), D("94"), D("564")))
        self.assertEqual((order.subtotal, order.tier_discount, order.tier_total, order.goods_total),
                         (D("570"), D("1"), D("6"), D("564")))
        self.assertEqual(order.shipping_state, "free")
        self.assertEqual(order.customer, customer)
        self.assertEqual(order.policy["minimum"], "150")

    def test_a_request_empties_the_cart(self):
        self._submit()
        self.assertEqual(cart_units(self.boss), 0)

    def test_it_moves_no_stock_and_records_no_sale(self):
        self._submit()
        self.assertEqual(Purchase.objects.get(product=self.item).remaining, 9)
        self.assertEqual(Sale.objects.count(), 0)

    def test_a_changed_price_needs_a_second_look(self):
        stale = self._print()
        Product.objects.filter(pk=self.item.pk).update(wholesale_price=D("120"))
        with self.assertRaisesMessage(ValueError, "mudaram"):
            self._submit(fingerprint=stale)
        self.assertEqual(OrderRequest.objects.count(), 0)
        self.assertEqual(cart_units(self.boss), 2)

    def test_below_the_minimum_is_refused_by_the_server(self):
        set_quantity(self.boss, self.item.pk, 1)
        Product.objects.filter(pk=self.item.pk).update(wholesale_price=D("10"))
        with self.assertRaises(ValueError):
            self._submit()

    def test_later_price_changes_leave_the_request_alone(self):
        order = self._submit()
        Product.objects.filter(pk=self.item.pk).update(wholesale_price=D("999"), name="Renamed")
        row = order.lines.get()
        self.assertEqual((row.catalog_price, row.name.endswith("Renamed")), (D("100"), False))

    def test_the_contact_is_a_snapshot(self):
        order = self._submit()
        self.assertEqual((order.contact_name, order.city, order.postal_code, order.country),
                         ("Ana", "Amadora", "2700-123", "PT"))


class PagesMixin(CatalogMixin):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.boss)
        self.item = self._product(name="Item", units=9, wholesale_price=D("100"), volume_ml=100)
        self.form = {"delivery": "delivery", "contact_name": "Ana", "phone": "912", "email": "", "business_name": "",
                     "address": "Rua 1", "postal_code": "2700-123", "city": "Amadora", "country": "PT", "note": ""}

    def _add(self, product=None, quantity=2):
        return self.client.post(reverse("wholesale_cart_add"), {"product": (product or self.item).pk, "quantity": quantity})


class CataloguePageTests(PagesMixin):
    def test_a_row_on_sale_can_be_added(self):
        self.assertContains(self.client.get(reverse("catalog")), "Adicionar")

    def test_adding_from_the_list_answers_with_the_cart(self):
        response = self.client.post(reverse("wholesale_cart_add"), {"product": self.item.pk, "quantity": 2},
                                    headers={"X-Requested-With": "fetch"})
        data = response.json()
        self.assertEqual((data["units"], data["in_cart"], data["subtotal"]), (2, 2, "200.00"))
        self.assertIn("Faltam", data["message"])

    def test_the_answer_holds_no_stock(self):
        response = self.client.post(reverse("wholesale_cart_add"), {"product": self.item.pk, "quantity": 1},
                                    headers={"X-Requested-With": "fetch"})
        self.assertEqual(set(response.json()), {"units", "in_cart", "subtotal", "message", "url"})

    def test_a_refused_add_says_why(self):
        gone = self._product(name="Gone", units=0)
        response = self.client.post(reverse("wholesale_cart_add"), {"product": gone.pk, "quantity": 1},
                                    headers={"X-Requested-With": "fetch"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_a_price_from_the_page_is_ignored(self):
        self.client.post(reverse("wholesale_cart_add"), {"product": self.item.pk, "quantity": 2, "price": "0.01"})
        self.assertEqual(self.client.get(reverse("wholesale_cart")).context["quote"]["subtotal"], D("200"))

    def test_a_row_says_what_is_in_the_cart(self):
        self._add()
        self.assertEqual(self.client.get(reverse("catalog")).context["page_obj"][0]["in_cart"], 2)

    def test_previewing_a_customer_changes_the_price(self):
        customer = Customer.objects.create(name="Loja", kind="revenda", wholesale_discount=D("5"))
        self.client.post(reverse("wholesale_customer"), {"customer": customer.pk})
        self.assertEqual(self.client.get(reverse("catalog")).context["page_obj"][0]["price"], D("95"))
        self.client.post(reverse("wholesale_customer"), {"customer": ""})
        self.assertEqual(self.client.get(reverse("catalog")).context["page_obj"][0]["price"], D("100"))

    def test_only_a_revenda_can_be_previewed(self):
        shop = Customer.objects.create(name="Walk in", kind="retail", wholesale_discount=D("5"))
        self.client.post(reverse("wholesale_customer"), {"customer": shop.pk})
        self.assertEqual(self.client.get(reverse("catalog")).context["page_obj"][0]["price"], D("100"))

    def test_only_managers_get_in(self):
        self.client.force_login(User.objects.create_user("staff", password="pw"))
        for name in ("wholesale_cart", "wholesale_orders", "order_requests"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)
        self.assertEqual(self._add().status_code, 302)
        self.assertEqual(CartLine.objects.count(), 0)


class CartPageTests(PagesMixin):
    def test_an_empty_cart_says_so(self):
        self.assertContains(self.client.get(reverse("wholesale_cart")), "O carrinho está vazio")

    def test_the_page_shows_the_amounts(self):
        self._add()
        response = self.client.get(reverse("wholesale_cart"))
        self.assertContains(response, "200.00")
        self.assertContains(response, "Quantidade sujeita a confirmação")

    def test_changing_a_quantity(self):
        self._add()
        self.client.post(reverse("wholesale_cart_update"), {"product": self.item.pk, "quantity": 5})
        self.assertEqual(cart_units(self.boss), 5)

    def test_a_bad_quantity_changes_nothing(self):
        self._add()
        response = self.client.post(reverse("wholesale_cart_update"), {"product": self.item.pk, "quantity": "abc"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cart_units(self.boss), 2)

    def test_removing_a_line(self):
        self._add()
        self.client.post(reverse("wholesale_cart_update"), {"product": self.item.pk, "quantity": 0})
        self.assertEqual(cart_units(self.boss), 0)

    def test_the_summary_follows_the_destination(self):
        self._add(quantity=4)
        url = reverse("wholesale_cart_summary")
        self.assertContains(self.client.get(url, {"delivery": "delivery", "country": "PT", "postal_code": "2700-123"}), "Portes grátis")
        self.assertContains(self.client.get(url, {"delivery": "delivery", "country": "PT", "postal_code": "9500-100"}), "Portes por confirmar")
        self.assertContains(self.client.get(url, {"delivery": "pickup"}), "Levantamento em loja")

    def test_sending_makes_a_request_and_shows_it(self):
        self._add()
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        response = self.client.post(reverse("wholesale_cart_submit"), {**self.form, "fingerprint": fingerprint}, follow=True)
        order = OrderRequest.objects.get()
        self.assertRedirects(response, reverse("wholesale_order", args=[order.public_id]))
        self.assertContains(response, "não reserva stock")

    def test_sending_twice_makes_one_request(self):
        self._add()
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        data = {**self.form, "fingerprint": fingerprint, "submit_token": "a" * 32}
        self.client.post(reverse("wholesale_cart_submit"), data)
        self._add()
        self.client.post(reverse("wholesale_cart_submit"), data)
        self.assertEqual(OrderRequest.objects.count(), 1)

    def test_missing_details_stop_it_and_keep_the_cart(self):
        self._add()
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        response = self.client.post(reverse("wholesale_cart_submit"), {**self.form, "fingerprint": fingerprint, "phone": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OrderRequest.objects.count(), 0)
        self.assertEqual(cart_units(self.boss), 2)

    def test_pickup_needs_no_address(self):
        self._add()
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        self.client.post(reverse("wholesale_cart_submit"), {"delivery": "pickup", "contact_name": "Ana", "phone": "912",
                                                            "country": "PT", "fingerprint": fingerprint})
        self.assertEqual(OrderRequest.objects.get().shipping_state, "pickup")

    def test_a_previewed_customer_fills_the_form(self):
        customer = Customer.objects.create(name="Loja Ana", kind="revenda", phone="911", address="Rua 2")
        self.client.post(reverse("wholesale_customer"), {"customer": customer.pk})
        self._add()
        form = self.client.get(reverse("wholesale_cart")).context["form"]
        self.assertEqual((form.initial["business_name"], form.initial["phone"], form.initial["address"]),
                         ("Loja Ana", "911", "Rua 2"))


class MyOrdersTests(PagesMixin):
    def setUp(self):
        super().setUp()
        self._add()
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        self.client.post(reverse("wholesale_cart_submit"), {**self.form, "fingerprint": fingerprint})
        self.order = OrderRequest.objects.get()
        self.stranger = User.objects.create_superuser("stranger", password="pw")

    def test_my_orders_lists_mine(self):
        self.assertEqual(list(self.client.get(reverse("wholesale_orders")).context["orders"]), [self.order])

    def test_nobody_else_sees_it_there(self):
        self.client.force_login(self.stranger)
        self.assertEqual(list(self.client.get(reverse("wholesale_orders")).context["orders"]), [])
        self.assertEqual(self.client.get(reverse("wholesale_order", args=[self.order.public_id])).status_code, 404)

    def test_nobody_else_can_cancel_it(self):
        self.client.force_login(self.stranger)
        response = self.client.post(reverse("wholesale_order_cancel", args=[self.order.public_id]))
        self.assertEqual(response.status_code, 404)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "submitted")

    def test_cancelling_keeps_the_record(self):
        self.client.post(reverse("wholesale_order_cancel", args=[self.order.public_id]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "cancelled")
        self.assertIsNotNone(self.order.cancelled_at)

    def test_a_link_cannot_cancel(self):
        self.assertEqual(self.client.get(reverse("wholesale_order_cancel", args=[self.order.public_id])).status_code, 405)

    def test_a_cancelled_order_cannot_be_cancelled_again(self):
        self.client.post(reverse("wholesale_order_cancel", args=[self.order.public_id]))
        first = OrderRequest.objects.get().cancelled_at
        self.client.post(reverse("wholesale_order_cancel", args=[self.order.public_id]))
        self.assertEqual(OrderRequest.objects.get().cancelled_at, first)


class BackOfficeTests(PagesMixin):
    def setUp(self):
        super().setUp()
        self._add()
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        self.client.post(reverse("wholesale_cart_submit"), {**self.form, "fingerprint": fingerprint})
        self.order = OrderRequest.objects.get()

    def test_the_shop_sees_the_request(self):
        response = self.client.get(reverse("order_requests"))
        self.assertContains(response, "Ana")
        self.assertContains(response, "200.00")

    def test_the_shop_can_cancel_it(self):
        self.client.post(reverse("order_requests"), {"id": self.order.id, "action": "cancel"})
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.cancelled_by), ("cancelled", self.boss))

    def test_a_row_gone_meanwhile_is_not_a_crash(self):
        self.assertEqual(self.client.post(reverse("order_requests"), {"id": 999999, "action": "cancel"}, follow=True).status_code, 200)
