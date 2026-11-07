"""Settings -> Rules: each number the owner sets, and that the system follows it."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

from stock.models import BusinessSettings, InboundOrder, InboundPendingItem, OrderRequest, Product, Supplier
from stock.models.core import forget_current
from stock.services import finance, pricing, rules, store_stock
from stock.services.wholesale_cart import cart_quote
from stock.test_wholesale_orders import MAINLAND, PagesMixin

DEFAULTS = {name: None for name, *_ in rules.FIELDS}


def form(**changes):
    row = BusinessSettings.get()
    data = {name: str(getattr(row, name)) for name, *_ in rules.FIELDS}
    data.update({k: str(v) for k, v in changes.items()})
    return data


class RulesMixin(PagesMixin):
    def setUp(self):
        super().setUp()
        self.addCleanup(forget_current)

    def change(self, **values):
        return self.client.post(reverse("business_rules"), form(**values))


class ThePageTests(RulesMixin):
    def test_every_rule_is_on_the_page(self):
        page = self.client.get(reverse("business_rules"))
        for name, *_ in rules.FIELDS:
            self.assertContains(page, f'name="{name}"')

    def test_it_saves(self):
        self.change(wholesale_minimum="200", iva_rate="6")
        row = BusinessSettings.get()
        self.assertEqual((row.wholesale_minimum, row.iva_rate), (Decimal("200.00"), Decimal("6.00")))

    def test_a_wrong_number_saves_nothing(self):
        response = self.change(wholesale_minimum="-5", iva_rate="6")
        self.assertContains(response, "Minimum order")
        self.assertEqual(BusinessSettings.get().iva_rate, Decimal("23.00"))

    def test_the_bigger_discount_starts_higher(self):
        self.assertContains(self.change(wholesale_tier2_from="400"), "start above")

    def test_the_sizes_are_in_order(self):
        self.assertContains(self.change(discount_min_ml="130"), "smallest size")

    def test_it_is_for_the_admin(self):
        user = get_user_model().objects.create_user("mgr", password="pw")
        user.groups.add(Group.objects.get_or_create(name="Managers")[0])
        self.client.force_login(user)
        self.assertNotEqual(self.client.get(reverse("business_rules")).status_code, 200)


class TheyAreFollowedTests(RulesMixin):
    def quote(self, quantity):
        from stock.services.wholesale_cart import set_quantity
        from stock.models import CartLine
        CartLine.objects.update_or_create(user=self.boss, product=self.item, defaults={"quantity": quantity})
        return cart_quote(self.boss, None, MAINLAND)

    def test_the_minimum(self):
        self.change(wholesale_minimum="250")
        self.assertFalse(self.quote(2)["can_submit"])
        self.assertTrue(self.quote(3)["can_submit"])

    def test_free_shipping_and_the_fee(self):
        self.change(wholesale_free_shipping="400", wholesale_shipping_fee="7.50")
        self.assertEqual(self.quote(3)["shipping_fee"], Decimal("7.50"))
        self.assertEqual(self.quote(4)["shipping_state"], "free")

    def test_the_volume_discounts(self):
        self.change(wholesale_tier1_from="200", wholesale_tier1_off="3", wholesale_tier2_from="900", wholesale_tier2_off="5")
        self.assertEqual(self.quote(2)["tier_discount"], Decimal("3"))
        self.assertEqual(self.quote(9)["tier_discount"], Decimal("5"))

    def test_an_order_keeps_the_rules_it_was_placed_under(self):
        self._add(quantity=2)
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        self.client.post(reverse("wholesale_cart_submit"), {**self.form, "fingerprint": fingerprint})
        self.change(wholesale_minimum="900")
        self.assertEqual(OrderRequest.objects.get().policy["minimum"], "150.00")

    def test_the_customer_discount_sizes(self):
        from stock.services.wholesale_discount import discount_applies
        self.assertTrue(discount_applies(self.item))        # 100ml
        self.change(discount_min_ml="30", discount_max_ml="50")
        self.assertFalse(discount_applies(self.item))

    def test_the_supplier_term(self):
        self.change(supplier_term_days="45")
        order = InboundOrder.objects.create(supplier=Supplier.objects.create(name="S"), invoice_date=self.now.date())
        self.assertEqual(finance.default_due(order), self.now.date() + timedelta(days=45))

    def test_iva_on_receipt(self):
        self.change(iva_rate="6")
        order = InboundOrder.objects.create(supplier=Supplier.objects.create(name="S"), status="pending_receipt")
        line = InboundPendingItem.objects.create(inbound_order=order, product=self.item, quantity=1, cost_price=Decimal("10"))
        self.client.post(reverse("inbound_receive", args=[order.pk]), {
            "action": "save", "supplier": order.supplier_id, "shipping": "0", "add_iva": "1",
            "lines-TOTAL_FORMS": 1, "lines-INITIAL_FORMS": 1, "lines-MIN_NUM_FORMS": 0, "lines-MAX_NUM_FORMS": 1000,
            "lines-0-id": line.pk, "lines-0-quantity": 1, "lines-0-cost_price": "10", "lines-0-shipping_share": "0"})
        order.refresh_from_db()
        self.assertEqual(order.iva_rate, Decimal("6.00"))
        self.assertContains(self.client.get(reverse("inbound")), "Add IVA 6%")

    def test_the_pedido_validity(self):
        self.change(pedido_valid_days="15")
        self.client.post(reverse("set_active_store"), {"store": self.khan.id})
        self.assertEqual(self.client.get(reverse("pedido")).context["default_valid_days"], 15)

    def test_stock_reduzido(self):
        from stock.services.wholesale_catalog import availability
        self.assertEqual(availability(4)[0], "in-stock")
        self.change(catalog_low_stock_below="5")
        self.assertEqual(availability(4)[0], "low-stock")

    def test_samples_per_shop(self):
        self.assertEqual(store_stock.sample_reserve(self.khan, 5), 1)
        self.change(samples_per_shop="2")
        self.assertEqual(store_stock.sample_reserve(self.khan, 5), 2)
        self.assertEqual(store_stock.sample_reserve(self.khan, 1), 1)
        self.change(samples_per_shop="0")
        self.assertEqual(store_stock.sample_reserve(self.khan, 5), 0)

    def test_auto_pricing(self):
        self.change(wholesale_markup="8", retail_markup="15", pvp_uplift="3")
        Product.objects.filter(pk=self.item.pk).update(price_locked=False)
        self.item.refresh_from_db()
        self.assertTrue(pricing.sync_perfume_price(self.item))
        self.assertEqual((self.item.wholesale_price, self.item.default_price), (Decimal("26"), Decimal("41")))
        from stock.views import pvp_price
        self.assertEqual(pvp_price(Decimal("41")), Decimal("44.00"))

    def test_the_low_stock_alert(self):
        from stock.services import low_stock
        self.assertEqual(low_stock.alert_below(), 5)
        self.change(low_stock_alert_below="20")
        self.assertEqual(low_stock.alert_below(), 20)


class BadgeTests(PagesMixin):
    def send(self):
        self._add()
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        self.client.post(reverse("wholesale_cart_submit"), {**self.form, "fingerprint": fingerprint})

    def test_waiting_orders_show_on_the_menu(self):
        self.client.post(reverse("set_active_store"), {"store": self.khan.id})
        self.send()
        page = self.client.get(reverse("product_list"))
        self.assertEqual((page.context["requests_waiting"], page.context["pedido_waiting"]), (1, 1))
        self.assertContains(page, 'class="nav-n"')

    def test_nothing_waiting_shows_nothing(self):
        page = self.client.get(reverse("product_list"))
        self.assertEqual(page.context["requests_waiting"], 0)
        self.assertNotContains(page, 'class="nav-n"')

    def test_a_confirmed_order_leaves_order_requests_but_waits_in_pedido(self):
        self.client.post(reverse("set_active_store"), {"store": self.khan.id})
        self.send()
        OrderRequest.objects.update(status="confirmed")
        page = self.client.get(reverse("product_list"))
        self.assertEqual((page.context["requests_waiting"], page.context["pedido_waiting"]), (0, 1))
