"""Transfers: kept apart from Finance for now, clearing a debt, and moving several products."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from stock.models import (Category, InboundOrder, Product, StockTransfer, StoreSettlement, Supplier)
from stock.services import finance, store_stock
from stock.test_finance import FinanceMixin


class TransferMixin(FinanceMixin):
    def setUp(self):
        super().setUp()
        cat = Category.objects.get(name="Perfumes")
        self.yara = Product.objects.create(name="Yara", brand="Lattafa", barcode="9960000000002", category=cat,
                                           default_price=Decimal("30"))
        for product in (self.product, self.yara):
            store_stock.receive_batches(product=product, quantity=10, cost_price=Decimal("10"),
                                        allocation={self.khan.id: 10})

    def post(self, **data):
        return self.client.post(reverse("stock_transfers"), data)

    def owed(self):
        return [(b["debtor"].name, b["creditor"].name, b["amount"]) for b in store_stock.balances()]

    def move(self, *lines, direction=None, **extra):
        return self.post(action="move", direction=direction or f"{self.khan.id}-{self.scentory.id}",
                         product_id=[p.pk for p, _q in lines], quantity=[q for _p, q in lines], **extra)


class KeptApartFromFinanceTests(TransferMixin, TestCase):
    def test_supplier_payments_do_not_make_the_shops_owe(self):
        supplier = Supplier.objects.create(name="S")
        order = InboundOrder.objects.create(supplier=supplier, status="received")
        store_stock.receive_batches(product=self.product, quantity=10, cost_price=Decimal("100"),
                                    allocation={self.khan.id: 10}, supplier=supplier, inbound_order=order)
        finance.add_payment(order, store=self.scentory, amount=Decimal("1000"), paid_on=self.today,
                            method="bank", note="", user=self.admin)
        self.assertEqual(self.owed(), [])

    def test_a_settlement_is_not_cash_in_finance(self):
        StoreSettlement.objects.create(payer=self.khan, payee=self.scentory, amount=Decimal("50"),
                                       settled_on=self.today)
        lines = finance.month_cash(self.month, self.today, self.scentory)["lines"]
        self.assertNotIn("Between shops", [line["label"] for line in lines])


class ClearingTests(TransferMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.move((self.product, 2))

    def test_clearing_squares_the_shops(self):
        before = self.owed()
        self.assertEqual(len(before), 1)
        self.post(action="clear", payer=self.scentory.id, payee=self.khan.id)
        self.assertEqual(self.owed(), [])
        row = StoreSettlement.objects.get()
        self.assertTrue(row.cleared)
        self.assertEqual(row.amount, before[0][2])

    def test_it_says_cleared_in_the_history(self):
        self.post(action="clear", payer=self.scentory.id, payee=self.khan.id)
        self.assertContains(self.client.get(reverse("stock_transfers")), "Cleared")

    def test_deleting_it_brings_the_debt_back(self):
        before = self.owed()
        self.post(action="clear", payer=self.scentory.id, payee=self.khan.id)
        self.post(action="unsettle", settlement=StoreSettlement.objects.get().pk)
        self.assertEqual(self.owed(), before)

    def test_nothing_to_clear_writes_nothing(self):
        self.post(action="clear", payer=self.scentory.id, payee=self.khan.id)
        self.post(action="clear", payer=self.scentory.id, payee=self.khan.id)
        self.assertEqual(StoreSettlement.objects.count(), 1)

    def test_the_page_offers_it(self):
        self.assertContains(self.client.get(reverse("stock_transfers")), 'value="clear"')


class SeveralProductsTests(TransferMixin, TestCase):
    def held(self, product):
        return store_stock.stock_by_store([product.id]).get(product.id, {})

    def test_two_products_go_in_one_transfer(self):
        self.move((self.product, 2), (self.yara, 3))
        transfer = StockTransfer.objects.get()
        self.assertEqual(sorted(line.quantity for line in transfer.lines.all()), [2, 3])
        self.assertEqual(self.held(self.yara).get(self.scentory.id), 3)

    def test_one_too_many_moves_nothing(self):
        response = self.move((self.product, 2), (self.yara, 30))
        self.assertFalse(StockTransfer.objects.exists())
        self.assertEqual(self.held(self.product).get(self.scentory.id, 0), 0)
        self.assertContains(self.client.get(reverse("stock_transfers")), "has only 10")
        self.assertEqual(response.status_code, 302)

    def test_the_same_product_twice_adds_up(self):
        self.move((self.product, 2), (self.product, 1))
        self.assertEqual(StockTransfer.objects.get().lines.get().quantity, 3)

    def test_no_charge_moves_owe_nothing(self):
        self.move((self.product, 2), free="1")
        self.assertEqual(self.owed(), [])

    def test_nothing_chosen_is_refused(self):
        self.post(action="move", direction=f"{self.khan.id}-{self.scentory.id}")
        self.assertFalse(StockTransfer.objects.exists())

    def test_search_finds_by_name_with_each_shops_stock(self):
        rows = self.client.get(reverse("transfer_search"), {"q": "yara"}).json()["results"]
        self.assertEqual([r["id"] for r in rows], [self.yara.pk])
        self.assertEqual(rows[0]["stock"], {str(self.khan.id): 10})

    def test_search_finds_an_ean(self):
        rows = self.client.get(reverse("transfer_search"), {"q": self.yara.barcode}).json()["results"]
        self.assertEqual([r["id"] for r in rows], [self.yara.pk])

    def test_search_leaves_out_what_no_shop_has(self):
        Product.objects.create(name="Yara Gone", brand="Lattafa", barcode="9960000000003",
                               category=Category.objects.get(name="Perfumes"), default_price=Decimal("1"))
        rows = self.client.get(reverse("transfer_search"), {"q": "yara"}).json()["results"]
        self.assertEqual([r["id"] for r in rows], [self.yara.pk])

    def test_search_is_for_managers(self):
        self.client.force_login(get_user_model().objects.create_user("emp", password="pw"))
        self.assertNotEqual(self.client.get(reverse("transfer_search"), {"q": "yara"}).status_code, 200)

    def test_the_page_searches_instead_of_asking_a_barcode(self):
        page = self.client.get(reverse("stock_transfers"))
        self.assertContains(page, "data-tr-search")
        self.assertNotContains(page, 'name="barcode"')
