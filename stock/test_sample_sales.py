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


class CorrectionTests(ShopsMixin, TestCase):
    """Orders entered or corrected by hand follow the till's rule."""

    def setUp(self):
        super().setUp()
        import json
        from django.contrib.auth import get_user_model
        self.json = json
        self.client.force_login(get_user_model().objects.create_superuser("sc_admin", password="pw123456"))

    def post(self, lines, *, order=None, affects_stock=True):
        from django.urls import reverse
        from django.utils import timezone
        items = [{"product_id": self.product.id, "qty": qty, "price": "10.00", "payment": "cash",
                  **({"sample": True} if sample else {})} for qty, sample in lines]
        total = sum(qty for qty, _ in lines) * 10
        data = {"order_datetime": timezone.localtime().strftime("%Y-%m-%dT%H:%M"), "note": "By hand",
                "reason": "Entered late", "store": self.khan.id, "items_json": self.json.dumps(items),
                "payments_json": self.json.dumps([{"method": "cash", "amount": f"{total}.00"}])}
        if affects_stock and order is None:
            data["affects_stock"] = "on"
        url = (reverse("sale_order_correction_edit", args=[order.pk]) if order
               else reverse("sale_order_correction_create"))
        return self.client.post(url, data)

    def sales(self):
        from .models import Sale
        return [(s.quantity, s.is_sample) for s in Sale.objects.order_by("id")]

    def test_a_past_sale_with_only_samples_left_saves(self):
        self.batch(self.khan, 1)
        self.assertEqual(self.post([(1, False)]).status_code, 302)
        self.assertEqual(self.sales(), [(1, True)])
        self.assertEqual(self.units(self.khan), 0)

    def test_a_past_sale_splits_like_the_till(self):
        self.batch(self.khan, 2)
        self.post([(2, False)])
        self.assertEqual(self.sales(), [(1, False), (1, True)])

    def test_editing_a_split_order_keeps_one_pair(self):
        from .models import SaleOrder
        self.batch(self.khan, 2)
        self.post([(2, False)])
        order = SaleOrder.objects.get()
        self.assertEqual(self.post([(1, False), (1, True)], order=order).status_code, 302)
        self.assertEqual(self.sales(), [(1, False), (1, True)])
        self.assertEqual(self.units(self.khan), 0)

    def test_a_ticked_line_without_stock_effect_is_marked_only(self):
        self.batch(self.khan, 3)
        self.post([(1, True)], affects_stock=False)
        self.assertEqual(self.sales(), [(1, True)])
        self.assertEqual(self.units(self.khan), 3)

    def test_the_form_reloads_each_lines_flag(self):
        from django.urls import reverse
        from .models import SaleOrder
        self.batch(self.khan, 2)
        self.post([(2, False)])
        html = self.client.get(reverse("sale_order_correction_edit", args=[SaleOrder.objects.get().pk])).content.decode()
        cart = self.json.loads(html.split('id="initialCart" type="application/json">', 1)[1].split("</script>", 1)[0])
        self.assertEqual([(row["qty"], row["sample"]) for row in cart], [(1, False), (1, True)])


class PedidoTests(ShopsMixin, TestCase):
    """Pedido: every entry is its own row, and a row can be a Tester."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        self.boss = get_user_model().objects.create_superuser("sp_admin", password="pw123456")
        self.client.force_login(self.boss)

    def post(self, rows, save_only=True):
        from unittest import mock
        from django.urls import reverse
        with mock.patch("stock.pedido_views.resolve_active_store", return_value=(self.khan, False)):
            return self.client.post(reverse("pedido"), {
                "customer_name": "Loja", "discount_kind": "amount",
                "product_id": [self.product.pk] * len(rows), "quantity": [qty for qty, _ in rows],
                "unit_price": ["20"] * len(rows), "picks": [""] * len(rows),
                "sample": ["1" if sample else "" for _, sample in rows],
                **({"save_only": "1"} if save_only else {})})

    def draft(self, response):
        from .models import PedidoDraft
        return PedidoDraft.objects.get(pk=response.json()["draft"])

    def test_two_entries_stay_two_lines(self):
        self.assertEqual(len(self.draft(self.post([(1, False), (1, False)])).lines), 2)

    def test_tester_is_kept_and_printed(self):
        import io
        from pypdf import PdfReader
        lines = self.draft(self.post([(2, False), (1, True)])).lines
        self.assertEqual([bool(line.get("sample")) for line in lines], [False, True])
        pdf = self.post([(2, False), (1, True)], save_only=False)
        text = " ".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
        self.assertEqual(text.count("Tester"), 1)

    def test_a_tester_line_reaches_the_till(self):
        from .views import _pedido_cart
        cart = _pedido_cart(self.draft(self.post([(2, False), (1, True)])))
        self.assertEqual([(line["sample"], line["base"]) for line in cart["lines"]], [(False, "20.00"), (True, "20.00")])

    def test_an_old_draft_without_the_flag_sells_normally(self):
        from .views import _pedido_cart
        draft = self.draft(self.post([(1, True)]))
        draft.lines = [{key: value for key, value in line.items() if key != "sample"} for line in draft.lines]
        draft.save()
        self.assertEqual([line["sample"] for line in _pedido_cart(draft)["lines"]], [False])


class WholesaleOrderTests(ShopsMixin, TestCase):
    """A B2B order follows what the shop quoted, Tester rows included."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        from .models import OrderRequest, OrderRequestLine, PedidoDraft
        self.user = get_user_model().objects.create_superuser("sw_admin", password="pw123456")
        self.order = OrderRequest.objects.create(
            submitted_by=self.user, contact_name="Loja", phone="900", subtotal=Decimal("20"),
            tier_discount=Decimal("0"), tier_total=Decimal("0"), goods_total=Decimal("20"),
            shipping_state="free", shipping_fee=Decimal("0"))
        OrderRequestLine.objects.create(
            request=self.order, product=self.product, name="B Oud", barcode=self.product.barcode, quantity=1,
            base_price=Decimal("20"), customer_discount=Decimal("0"), catalog_price=Decimal("20"),
            tier_discount=Decimal("0"), final_price=Decimal("20"), line_total=Decimal("20"))
        self.draft = PedidoDraft.objects.create(store=self.khan, created_by=self.user,
                                                goods_total=Decimal("20"), total=Decimal("20"))
        self.order.pedido = self.draft
        self.order.save()

    def quote(self, lines):
        from .services.wholesale_orders import confirm_from_pedido
        self.draft.lines = lines
        self.draft.goods_total = self.draft.total = sum(Decimal(l["unit_price"]) * l["quantity"] for l in lines)
        self.draft.save()
        confirm_from_pedido(self.draft)

    def test_the_order_follows_a_tester_line(self):
        self.quote([{"product_id": self.product.pk, "quantity": 1, "unit_price": "20"},
                    {"product_id": self.product.pk, "quantity": 1, "unit_price": "15", "sample": True}])
        lines = list(self.order.lines.order_by("id"))
        self.assertEqual([(l.final_price, l.is_sample) for l in lines], [(Decimal("20"), False), (Decimal("15"), True)])

    def test_picks_stay_with_their_own_line(self):
        a = [{"id": 1, "name": "Yara", "quantity": 1}]
        b = [{"id": 2, "name": "Asad", "quantity": 2}]
        self.quote([{"product_id": self.product.pk, "quantity": 1, "unit_price": "20", "picks": a},
                    {"product_id": self.product.pk, "quantity": 2, "unit_price": "20", "picks": b}])
        self.assertEqual([l.picks for l in self.order.lines.order_by("id")], [a, b])

    def test_saved_to_pedido_keeps_the_flag(self):
        from .services.wholesale_orders import save_to_pedido
        self.order.lines.update(is_sample=True)
        save_to_pedido(self.order)
        self.draft.refresh_from_db()
        self.assertTrue(self.draft.lines[0].get("sample"))

    def test_the_customer_sees_tester(self):
        from django.urls import reverse
        self.order.lines.update(is_sample=True)
        self.client.force_login(self.user)
        page = self.client.get(reverse("wholesale_order", args=[self.order.public_id]))
        self.assertContains(page, "Tester")
