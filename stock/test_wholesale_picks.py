"""Choosing what a mix or a sample is made of: the customer's choice, kept down to
the order the shop receives."""
import json
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.urls import reverse

from stock.models import CartLine, OrderRequest, OrderRequestLine, ProductContent, ProductType, Purchase
from stock.services.wholesale_cart import add_to_cart, cart_quote, set_quantity
from stock.services.wholesale_picks import perfume_choices
from stock.test_wholesale_orders import MAINLAND, PagesMixin


class PicksMixin(PagesMixin):
    def setUp(self):
        super().setUp()
        self.mix = self._product(name="Mix", units=9, is_mix=True, product_type_id="body_spray", wholesale_price=D("10"))
        self.yara_row = ProductContent.objects.create(owner=self.mix, name="Yara", sort_order=1)
        self.asad_row = ProductContent.objects.create(owner=self.mix, name="Asad", sort_order=2)
        self.sample = self._product(name="5ml", brand="Decante", units=0, product_type_id="amostra",
                                    wholesale_price=D("5"))
        self.yara = self._product(name="Yara", units=9)

    def _post(self, product, picks, **extra):
        return self.client.post(reverse("wholesale_cart_add"), {"product": product.pk, "picks": json.dumps(picks), **extra},
                                headers={"X-Requested-With": "fetch"})

    def _rows(self, **params):
        return {row["pk"]: row for row in self.client.get(reverse("catalog"), params).context["page_obj"]}


class WhatIsOfferedTests(PicksMixin):
    def test_a_mix_offers_its_list(self):
        row = self._rows()[self.mix.pk]
        self.assertEqual(row["pick_mode"], "list")
        self.assertEqual([o["name"] for o in row["pick_options"]], ["Yara", "Asad"])

    def test_one_name_leaves_nothing_to_choose(self):
        self.asad_row.delete()
        self.assertEqual(self._rows()[self.mix.pk]["pick_mode"], "")

    def test_a_coffret_is_not_chosen_from(self):
        box = self._product(name="Box", units=9, product_type_id="coffret")
        ProductContent.objects.create(owner=box, name="A")
        ProductContent.objects.create(owner=box, name="B")
        self.assertEqual(self._rows()[box.pk]["pick_mode"], "")

    def test_a_plain_perfume_has_no_choice(self):
        self.assertEqual(self._rows()[self.yara.pk]["pick_mode"], "")

    def test_the_sample_type_exists(self):
        self.assertTrue(ProductType.objects.get(code="amostra").picks_perfume)

    def test_a_sample_is_made_to_order_so_its_own_stock_does_not_matter(self):
        row = self._rows()[self.sample.pk]
        self.assertEqual((row["pick_mode"], row["state"]), ("perfume", "in-stock"))

    def test_the_row_asks_to_choose(self):
        response = self.client.get(reverse("catalog"))
        self.assertContains(response, f'data-layer="wc-pick-{self.mix.pk}"')
        self.assertContains(response, f'id="wc-pick-{self.sample.pk}"')


class AddingTests(PicksMixin):
    def test_the_quantity_is_what_was_chosen(self):
        response = self._post(self.mix, [{"id": self.yara_row.pk, "quantity": 3}, {"id": self.asad_row.pk, "quantity": 2}])
        self.assertEqual(response.json()["in_cart"], 5)
        line = CartLine.objects.get(product=self.mix)
        self.assertEqual(line.quantity, 5)
        self.assertEqual([(p["name"], p["quantity"]) for p in line.picks], [("Yara", 3), ("Asad", 2)])

    def test_a_typed_quantity_cannot_differ_from_the_choice(self):
        self._post(self.mix, [{"id": self.yara_row.pk, "quantity": 3}], quantity=50)
        self.assertEqual(CartLine.objects.get(product=self.mix).quantity, 3)

    def test_adding_again_adds_up(self):
        self._post(self.mix, [{"id": self.yara_row.pk, "quantity": 3}])
        self._post(self.mix, [{"id": self.yara_row.pk, "quantity": 1}, {"id": self.asad_row.pk, "quantity": 2}])
        line = CartLine.objects.get(product=self.mix)
        self.assertEqual((line.quantity, [p["quantity"] for p in line.picks]), (6, [4, 2]))

    def test_changing_the_choice_replaces_it(self):
        self._post(self.mix, [{"id": self.yara_row.pk, "quantity": 3}])
        self._post(self.mix, [{"id": self.asad_row.pk, "quantity": 1}], replace="1")
        line = CartLine.objects.get(product=self.mix)
        self.assertEqual((line.quantity, [p["name"] for p in line.picks]), (1, ["Asad"]))

    def test_nothing_chosen_is_refused(self):
        for picks in ([], [{"id": self.yara_row.pk, "quantity": 0}]):
            self.assertEqual(self._post(self.mix, picks).status_code, 400)
        self.assertFalse(CartLine.objects.exists())

    def test_a_broken_choice_is_refused(self):
        response = self.client.post(reverse("wholesale_cart_add"), {"product": self.mix.pk, "picks": "{nope"},
                                    headers={"X-Requested-With": "fetch"})
        self.assertEqual(response.status_code, 400)

    def test_a_name_off_the_list_is_refused(self):
        other = ProductContent.objects.create(owner=self.item, name="Other")
        self.assertEqual(self._post(self.mix, [{"id": other.pk, "quantity": 1}]).status_code, 400)

    def test_a_sample_takes_a_perfume_in_stock(self):
        self.assertEqual(self._post(self.sample, [{"id": self.yara.pk, "quantity": 2}]).status_code, 200)
        pick = CartLine.objects.get(product=self.sample).picks[0]
        self.assertEqual((pick["id"], pick["barcode"], pick["quantity"]), (self.yara.pk, self.yara.barcode, 2))
        self.assertIn("Yara", pick["name"])

    def test_a_sample_refuses_a_sold_out_perfume(self):
        gone = self._product(name="Gone", units=0)
        self.assertEqual(self._post(self.sample, [{"id": gone.pk, "quantity": 1}]).status_code, 400)

    def test_the_last_bottle_is_the_shops_sample(self):
        last = self._product(name="Last", units=1)
        self.assertEqual(self._post(self.sample, [{"id": last.pk, "quantity": 1}]).status_code, 400)

    def test_a_sample_refuses_what_is_not_a_perfume(self):
        self.assertEqual(self._post(self.sample, [{"id": self.mix.pk, "quantity": 1}]).status_code, 400)

    def test_a_mix_limit_counts_the_whole_line(self):
        add_to_cart(self.boss, self.mix, None, picks=[{"id": self.yara_row.pk, "quantity": 999}])
        self.assertEqual(self._post(self.mix, [{"id": self.yara_row.pk, "quantity": 1}]).status_code, 400)

    def test_a_plain_product_still_takes_a_quantity(self):
        self._add(self.yara, 2)
        self.assertEqual(CartLine.objects.get(product=self.yara).picks, [])


class CartTests(PicksMixin):
    def setUp(self):
        super().setUp()
        self._post(self.mix, [{"id": self.yara_row.pk, "quantity": 3}, {"id": self.asad_row.pk, "quantity": 2}])

    def test_the_cart_says_what_was_chosen(self):
        response = self.client.get(reverse("wholesale_cart"))
        self.assertContains(response, "Yara &times; 3")
        self.assertContains(response, f'data-layer="wc-pick-{self.mix.pk}"')

    def test_the_change_panel_starts_from_the_choice(self):
        options = self.client.get(reverse("wholesale_cart")).context["quote"]["lines"][0]["pick_options"]
        self.assertEqual([o["quantity"] for o in options], [3, 2])

    def test_a_line_with_a_choice_has_no_free_quantity(self):
        self.assertRaises(ValueError, set_quantity, self.boss, self.mix.pk, 9)
        self.assertEqual(CartLine.objects.get(product=self.mix).quantity, 5)

    def test_it_can_still_be_removed(self):
        set_quantity(self.boss, self.mix.pk, 0)
        self.assertFalse(CartLine.objects.exists())

    def test_a_line_from_before_the_choice_must_choose(self):
        CartLine.objects.filter(product=self.mix).update(picks=[])
        line = cart_quote(self.boss, None, MAINLAND)["lines"][0]
        self.assertFalse(line["ok"])
        self.assertIn("Escolha", line["problem"])

    def test_a_perfume_sold_out_meanwhile_is_named(self):
        self._post(self.sample, [{"id": self.yara.pk, "quantity": 1}])
        Purchase.objects.filter(product=self.yara).update(remaining=0)
        line = next(l for l in cart_quote(self.boss, None, MAINLAND)["lines"] if l["product_id"] == self.sample.pk)
        self.assertFalse(line["ok"])
        self.assertIn("Yara", line["problem"])

    def test_a_changed_choice_changes_the_fingerprint(self):
        before = cart_quote(self.boss, None, MAINLAND)["fingerprint"]
        self._post(self.mix, [{"id": self.yara_row.pk, "quantity": 2}, {"id": self.asad_row.pk, "quantity": 3}], replace="1")
        self.assertNotEqual(cart_quote(self.boss, None, MAINLAND)["fingerprint"], before)


class TheOrderTests(PicksMixin):
    def setUp(self):
        super().setUp()
        self._post(self.mix, [{"id": self.yara_row.pk, "quantity": 20}])
        self._post(self.sample, [{"id": self.yara.pk, "quantity": 3}])
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        self.client.post(reverse("wholesale_cart_submit"), {**self.form, "fingerprint": fingerprint})
        self.order = OrderRequest.objects.get()

    def test_the_choice_is_kept_on_the_order(self):
        line = OrderRequestLine.objects.get(product=self.sample)
        self.assertEqual((line.quantity, line.picks[0]["quantity"]), (3, 3))

    def test_a_rename_leaves_the_order_as_it_was(self):
        ProductContent.objects.filter(pk=self.yara_row.pk).update(name="Other")
        self.assertEqual(OrderRequestLine.objects.get(product=self.mix).picks[0]["name"], "Yara")

    def test_the_shop_sees_each_choice_and_its_quantity(self):
        page = self.client.get(reverse("order_requests"))
        self.assertContains(page, "20 &times; Yara")
        self.assertContains(page, self.yara.barcode)

    def test_the_customer_sees_it_too(self):
        page = self.client.get(reverse("wholesale_order", args=[self.order.public_id]))
        self.assertContains(page, "Yara &times; 20")


class SearchTests(PicksMixin):
    def _ids(self, query):
        return [row["id"] for row in perfume_choices(query)]

    def test_it_finds_a_perfume_in_stock(self):
        self.assertEqual(self._ids("yara"), [self.yara.pk])

    def test_it_leaves_out_what_cannot_be_used(self):
        self._product(name="Yara Gone", units=0)
        self._product(name="Yara Last", units=1)
        self._product(name="Yara Spray", units=9, product_type_id="body_spray")
        self._product(name="Yara Blend", units=9, is_mix=True)
        self.assertEqual(self._ids("yara"), [self.yara.pk])

    def test_a_short_word_finds_nothing(self):
        self.assertEqual(self._ids("y"), [])
        self.assertEqual(self._ids(""), [])

    def test_the_answer_has_no_stock_or_price(self):
        response = self.client.get(reverse("wholesale_pick_search"), {"q": "yara"})
        self.assertEqual(set(response.json()["results"][0]), {"id", "name", "ean"})

    def test_it_is_for_managers(self):
        self.client.force_login(User.objects.create_user("emp", password="pw"))
        self.assertNotEqual(self.client.get(reverse("wholesale_pick_search"), {"q": "yara"}).status_code, 200)


class SmallFixesTests(PicksMixin):
    def test_the_badge_sits_on_the_button(self):
        self._add(self.yara, 2)
        self.assertContains(self.client.get(reverse("catalog")), 'title="No carrinho: 2"')

    def test_the_setting_can_be_switched(self):
        row = ProductType.objects.get(code="coffret")
        self.client.post(reverse("catalog_types"), {"action": "save", "id": row.pk, "name": row.name,
                                                    "default_category": self.category.pk, "picks_perfume": "on"})
        row.refresh_from_db()
        self.assertTrue(row.picks_perfume)

    def test_the_panel_starts_at_nothing(self):
        panel = self.client.get(reverse("catalog")).content.decode().split(f'id="wc-pick-{self.mix.pk}"')[1]
        self.assertIn("<b data-wc-pick-total>0</b>", panel.split("</template>")[0])
