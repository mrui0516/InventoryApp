"""A mix or a sample in Pedido: the scents are chosen before the row, kept on the
line (not in Observações), and printed under the product."""
import json
from decimal import Decimal as D
from unittest import mock

from django.urls import reverse

from stock.models import OrderRequest, PedidoDraft, ProductContent
from stock.services import doc_pdf
from stock.services.pedido_pdf import _product_cell
from stock.services.wholesale_cart import add_to_cart
from stock.test_wholesale_flow import FlowMixin


class PedidoPicksMixin(FlowMixin):
    def setUp(self):
        super().setUp()
        self.mix = self._product(name="Mix", units=9, is_mix=True, product_type_id="body_spray", wholesale_price=D("10"))
        self.yara_row = ProductContent.objects.create(owner=self.mix, name="Yara", sort_order=1)
        self.asad_row = ProductContent.objects.create(owner=self.mix, name="Asad", sort_order=2)
        self.sample = self._product(name="5ml", brand="Decante", units=0, product_type_id="amostra",
                                    wholesale_price=D("5"))
        self.yara = self._product(name="Yara", units=3)
        self.gone = self._product(name="Gone", units=0)

    def _post(self, product, picks, quantity=1, save_only=True, draft=None):
        with mock.patch("stock.pedido_views.resolve_active_store", return_value=(self.khan, False)):
            return self.client.post(reverse("pedido"), {
                "customer_name": "Loja", "discount_kind": "amount", "product_id": [product.pk],
                "quantity": [quantity], "unit_price": ["10"], "picks": [json.dumps(picks) if picks is not None else ""],
                **({"draft_id": draft.pk} if draft else {}), **({"save_only": "1"} if save_only else {})})


class ChoosingTests(PedidoPicksMixin):
    def test_a_mix_is_kept_with_its_scents_and_their_sum(self):
        response = self._post(self.mix, [{"id": self.yara_row.pk, "quantity": 2}, {"id": self.asad_row.pk, "quantity": 1}],
                              quantity=1)
        line = PedidoDraft.objects.get(pk=response.json()["draft"]).lines[0]
        self.assertEqual(line["quantity"], 3)
        self.assertEqual([(p["name"], p["quantity"]) for p in line["picks"]], [("Yara", 2), ("Asad", 1)])

    def test_nothing_chosen_is_refused(self):
        response = self._post(self.mix, None)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Escolha pelo menos uma fragrância", status_code=400)

    def test_a_scent_of_another_mix_is_refused(self):
        other = ProductContent.objects.create(owner=self.item, name="Other", sort_order=1)
        self.assertEqual(self._post(self.mix, [{"id": other.pk, "quantity": 1}]).status_code, 400)

    def test_a_sample_is_poured_from_a_perfume_in_stock(self):
        response = self._post(self.sample, [{"id": self.yara.pk, "quantity": 4}])
        line = PedidoDraft.objects.get(pk=response.json()["draft"]).lines[0]
        self.assertEqual((line["quantity"], line["picks"][0]["id"]), (4, self.yara.pk))

    def test_a_sold_out_perfume_cannot_be_poured(self):
        response = self._post(self.sample, [{"id": self.gone.pk, "quantity": 1}])
        self.assertContains(response, "Sem stock", status_code=400)

    def test_a_plain_product_needs_no_choice(self):
        response = self._post(self.item, None, quantity=2)
        self.assertEqual(PedidoDraft.objects.get(pk=response.json()["draft"]).lines[0],
                         {"product_id": self.item.pk, "quantity": 2, "unit_price": "10.00"})

    def test_the_search_says_what_to_choose(self):
        data = self.client.get(reverse("products_autocomplete"), {"scope": "stock", "q": "Mix"}).json()["results"]
        row = next(r for r in data if r["id"] == self.mix.pk)
        self.assertEqual(row["pick_mode"], "list")
        self.assertEqual([o["name"] for o in row["pick_options"]], ["Yara", "Asad"])

    def test_a_reopened_draft_brings_its_choice_back(self):
        draft = PedidoDraft.objects.get(pk=self._post(self.mix, [{"id": self.yara_row.pk, "quantity": 2}]).json()["draft"])
        with mock.patch("stock.pedido_views.resolve_active_store", return_value=(self.khan, False)):
            row = self.client.get(reverse("pedido"), {"draft": draft.pk}).context["draft_rows"][0]
        self.assertEqual((row["pick_mode"], row["picks"][0]["name"]), ("list", "Yara"))

    def test_the_page_has_the_picker(self):
        with mock.patch("stock.pedido_views.resolve_active_store", return_value=(self.khan, False)):
            page = self.client.get(reverse("pedido"))
        self.assertContains(page, 'id="pd-picker"')
        self.assertContains(page, reverse("wholesale_pick_search"))


class PrintedTests(PedidoPicksMixin):
    def test_the_scents_print_under_the_product(self):
        cell = _product_cell(doc_pdf.styles(), {
            "title": "Mix", "picks": [{"id": 1, "name": "Yara", "quantity": 2}]})
        self.assertIn("2 × Yara", cell.text)

    def test_a_plain_line_is_its_title(self):
        self.assertEqual(_product_cell({}, {"title": "Item"}), "Item")


class OnlineOrderTests(PedidoPicksMixin):
    def _send_mix(self):
        add_to_cart(self.boss, self.mix, None, picks=[{"id": self.yara_row.pk, "quantity": 3}])
        return self._send()

    def test_the_choice_lands_on_the_line_not_in_the_note(self):
        draft = self._send_mix().pedido
        line = next(l for l in draft.lines if l["product_id"] == self.mix.pk)
        self.assertEqual(line["picks"][0]["name"], "Yara")
        self.assertEqual(draft.note, "Urgente")

    def test_a_choice_changed_in_pedido_reaches_the_order(self):
        order = self._send_mix()
        draft = order.pedido
        session = self.client.session
        session["active_store"] = str(draft.store_id)
        session.save()
        with mock.patch("stock.pedido_views.resolve_active_store", return_value=(draft.store, False)):
            self.client.post(reverse("pedido"), {
                "draft_id": draft.pk, "customer_name": draft.customer_name, "discount_kind": "amount",
                "product_id": [self.item.pk, self.mix.pk], "quantity": [2, 3], "unit_price": ["100", "10"],
                "picks": ["", json.dumps([{"id": self.asad_row.pk, "quantity": 3}])], "shipping": "0"})
        line = OrderRequest.objects.get(pk=order.pk).lines.get(product=self.mix)
        self.assertEqual([p["name"] for p in line.picks], ["Asad"])
