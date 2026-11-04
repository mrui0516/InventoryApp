"""The Finance section (2026-11-04): expenses, supplier payments and the split
between the shops, the owner's money, and the monthly figures. Admin only."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (BusinessSettings, Category, Expense, ExpenseCategory, ExpenseTemplate, FinanceLog,
                     InboundOrder, InboundPendingItem, OwnerFunding, Product, Purchase, Sale, SaleOrder,
                     SaleOrderCharge, SaleOrderPayment, Store, StoreSettlement, Supplier, SupplierPayment)
from .services import finance, store_stock


class FinanceMixin:
    def setUp(self):
        cache.clear()
        Store.objects.update(is_default=False, is_active=False)     # the store a migration seeds
        self.khan = Store.objects.create(name="Khan Perfume", code="KP", is_default=True)
        self.scentory = Store.objects.create(name="Scentory", code="SC")
        self.admin = get_user_model().objects.create_superuser("fin_admin", password="pw123456")
        self.client.force_login(self.admin)
        self.rent = ExpenseCategory.objects.get(name="Rent")
        self.salaries = ExpenseCategory.objects.get(name="Salaries")
        self.today = timezone.localdate()
        self.month = self.today.replace(day=1)
        settings = BusinessSettings.get()
        settings.payables_from = self.today - timedelta(days=30)
        settings.purchasing_store = self.scentory
        settings.save()
        cat = Category.objects.create(name="Perfumes", form_kind="perfume")
        self.product = Product.objects.create(name="Oud", brand="B", barcode="9960000000001", category=cat,
                                              default_price=Decimal("50"))

    def expense(self, amount="100", store=None, category=None, period=None, paid_on=None, method="", **extra):
        return Expense.objects.create(store=store or self.khan, category=category or self.rent,
                                      amount=Decimal(amount), period=period or self.month,
                                      paid_on=paid_on, method=method, **extra)


class FinanceModelTests(FinanceMixin, TestCase):
    def test_migration_seeds_the_categories(self):
        names = set(ExpenseCategory.objects.values_list("name", flat=True))
        self.assertEqual(names, {"Rent", "Utilities", "Salaries", "Marketing", "Packaging", "Shipping",
                                 "Accounting", "Software", "Maintenance", "Other"})

    def test_a_template_makes_one_expense_a_month(self):
        template = ExpenseTemplate.objects.create(store=self.khan, category=self.rent, amount=Decimal("900"))
        self.expense(template=template)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.expense(template=template)


FINANCE_URLS = ['finance', 'finance_expenses', 'finance_templates', 'finance_payables', 'finance_owner']


class ExpensesPageTests(FinanceMixin, TestCase):
    def post(self, **data):
        base = {"store": self.khan.id, "category": self.rent.id, "amount": "120,50",
                "month": self.month.strftime("%Y-%m"), "payee": "Landlord"}
        return self.client.post(reverse("finance_expenses"), {**base, **data})

    def pick(self, store):
        session = self.client.session
        session["active_store_id"] = store
        session.save()

    def test_only_admins_reach_finance(self):
        manager = get_user_model().objects.create_user("fin_mgr", password="pw123456", is_staff=True)
        employee = get_user_model().objects.create_user("fin_emp", password="pw123456")
        for user in (manager, employee):
            self.client.force_login(user)
            for name in FINANCE_URLS:
                self.assertNotEqual(self.client.get(reverse(name)).status_code, 200, name)
            self.assertNotContains(self.client.get(reverse("product_list")), reverse("finance_expenses"))
            self.post()
        self.assertFalse(Expense.objects.exists())

    def test_add_edit_delete_an_expense(self):
        self.post(paid="1", paid_on=self.today.isoformat(), method="bank")
        expense = Expense.objects.get()
        self.assertEqual((expense.amount, expense.payee, expense.created_by), (Decimal("120.50"), "Landlord", self.admin))
        self.post(id=expense.id, amount="130", paid="1", paid_on=self.today.isoformat(), method="cash")
        expense.refresh_from_db()
        self.assertEqual((expense.amount, expense.method), (Decimal("130.00"), "cash"))
        self.client.post(reverse("finance_expense_delete", args=[expense.id]))
        self.assertFalse(Expense.objects.exists())

    def test_period_is_stored_as_the_first_of_the_month(self):
        self.post(month="2026-10")
        self.assertEqual(Expense.objects.get().period, date(2026, 10, 1))

    def test_paid_needs_a_method(self):
        response = self.post(paid="1", paid_on=self.today.isoformat(), method="")
        self.assertFalse(Expense.objects.exists())
        self.assertEqual(response.status_code, 302)

    def test_unpaid_can_be_marked_paid(self):
        expense = self.expense()
        self.client.post(reverse("finance_expense_paid", args=[expense.id]),
                         {"paid_on": self.today.isoformat(), "method": "owner"})
        expense.refresh_from_db()
        self.assertEqual((expense.paid_on, expense.method), (self.today, "owner"))

    def test_filter_by_month_category_and_unpaid(self):
        self.pick("all")
        shown = self.expense("10")
        self.expense("20", paid_on=self.today, method="cash")
        self.expense("30", category=self.salaries)
        self.expense("40", period=date(2025, 1, 1))
        page = self.client.get(reverse("finance_expenses"), {"category": self.rent.id, "status": "unpaid"})
        self.assertEqual(list(page.context["expenses"]), [shown])

    def test_a_shop_sees_its_own_expenses(self):
        self.pick(self.scentory.id)
        mine = self.expense("10", store=self.scentory)
        self.expense("20")
        self.assertEqual(list(self.client.get(reverse("finance_expenses")).context["expenses"]), [mine])

    def test_every_change_is_logged(self):
        self.post()
        expense = Expense.objects.get()
        self.post(id=expense.id, amount="9")
        self.client.post(reverse("finance_expense_delete", args=[expense.id]))
        self.assertEqual(sorted(FinanceLog.objects.values_list("action", flat=True)), ["added", "changed", "deleted"])

    def test_a_used_category_can_only_be_deactivated(self):
        self.expense()
        self.client.post(reverse("finance_category"), {"id": self.rent.id, "action": "delete"})
        self.assertTrue(ExpenseCategory.objects.filter(id=self.rent.id).exists())
        self.client.post(reverse("finance_category"), {"id": self.rent.id, "action": "toggle"})
        self.rent.refresh_from_db()
        self.assertFalse(self.rent.is_active)
        self.client.post(reverse("finance_category"), {"name": "Insurance", "action": "add"})
        self.assertTrue(ExpenseCategory.objects.filter(name="Insurance").exists())


class TemplateTests(FinanceMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.template = ExpenseTemplate.objects.create(store=self.khan, category=self.rent, amount=Decimal("900"),
                                                       payee="Landlord")

    def confirm(self, period=None, **extra):
        return finance.confirm_template(self.template, period or self.month, amount=Decimal("900"),
                                        paid_on=self.today, method="bank", user=self.admin, **extra)

    def test_a_template_is_due_until_confirmed_or_skipped(self):
        self.assertEqual(finance.due_templates(self.month), [self.template])
        self.confirm()
        self.assertEqual(finance.due_templates(self.month), [])
        later = date(self.month.year + 1, 1, 1)
        self.assertEqual(finance.due_templates(later), [self.template])
        finance.skip_template(self.template, later, self.admin)
        self.assertEqual(finance.due_templates(later), [])

    def test_confirm_twice_makes_one_expense(self):
        self.assertEqual(self.confirm().id, self.confirm().id)
        self.assertEqual(Expense.objects.count(), 1)

    def test_changing_a_template_leaves_past_expenses(self):
        expense = self.confirm()
        self.client.post(reverse("finance_templates"), {
            "id": self.template.id, "store": self.khan.id, "category": self.rent.id, "amount": "1000", "payee": "X"})
        self.template.refresh_from_db()
        expense.refresh_from_db()
        self.assertEqual((self.template.amount, expense.amount), (Decimal("1000.00"), Decimal("900.00")))

    def test_an_earlier_month_can_still_be_confirmed(self):
        earlier = date(2026, 3, 1)
        self.assertEqual(finance.due_templates(earlier), [self.template])
        self.assertEqual(self.confirm(earlier).period, earlier)

    def test_salaries_are_expenses_with_a_payee(self):
        pay = ExpenseTemplate.objects.create(store=self.scentory, category=self.salaries, amount=Decimal("870"),
                                             payee="Ana")
        expense = finance.confirm_template(pay, self.month, amount=Decimal("900"), paid_on=None, method="",
                                           user=self.admin)
        self.assertEqual((expense.payee, expense.amount, expense.store, expense.paid_on),
                         ("Ana", Decimal("900.00"), self.scentory, None))

    def test_a_turned_off_template_is_not_due(self):
        self.template.is_active = False
        self.template.save()
        self.assertEqual(finance.due_templates(self.month), [])

    def test_a_shop_sees_its_own_due_templates(self):
        self.assertEqual(finance.due_templates(self.month, self.scentory), [])

    def test_the_views_confirm_and_skip(self):
        url = reverse("finance_template_confirm", args=[self.template.id])
        self.client.post(url, {"month": self.month.strftime("%Y-%m"), "amount": "950", "paid": "1", "method": "bank"})
        self.assertEqual(Expense.objects.get().amount, Decimal("950.00"))
        self.client.post(reverse("finance_template_skip", args=[self.template.id]), {"month": "2026-03"})
        self.assertEqual(finance.due_templates(date(2026, 3, 1)), [])


class SupplierPaymentTests(FinanceMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.supplier = Supplier.objects.create(name="Naypes")
        self.order = InboundOrder.objects.create(supplier=self.supplier, status="received")
        store_stock.receive_batches(product=self.product, quantity=10, cost_price=Decimal("100"),
                                    allocation={self.khan.id: 6, self.scentory.id: 4},
                                    supplier=self.supplier, inbound_order=self.order)

    def pay(self, amount="1000", order=None, store=None, **extra):
        return finance.add_payment(order or self.order, store=store or self.scentory, amount=Decimal(amount),
                                   paid_on=self.today, method=extra.pop("method", "bank"), note="", user=self.admin)

    def balance(self):
        rows = store_stock.balances()
        return (rows[0]["debtor"].name, rows[0]["creditor"].name, rows[0]["amount"]) if rows else None

    def pending(self, allocation=None, cost="10", qty=5):
        order = InboundOrder.objects.create(supplier=self.supplier, status="pending_receipt")
        InboundPendingItem.objects.create(inbound_order=order, product=self.product, quantity=qty,
                                          cost_price=Decimal(cost), allocation=allocation or {})
        return order

    def test_owing_is_received_cost_or_pending_lines_less_payments(self):
        self.assertEqual(finance.order_due(self.order), Decimal("1000.00"))
        self.pay("300")
        self.assertEqual(finance.order_paid(self.order), Decimal("300.00"))
        self.assertEqual(finance.order_due(self.pending()), Decimal("50.00"))
        owed = finance.payables()
        self.assertEqual((owed[0]["supplier"], owed[0]["owed"]), ("Naypes", Decimal("750.00")))

    def test_orders_before_the_cut_off_owe_nothing(self):
        InboundOrder.objects.filter(pk=self.order.pk).update(created_at=timezone.now() - timedelta(days=90))
        self.assertEqual(finance.payables(), [])

    def test_a_paid_order_leaves_the_list(self):
        self.pay()
        self.assertEqual(finance.payables(), [])

    def test_paying_over_shows_negative(self):
        self.pay("1100")
        self.assertEqual(finance.payables()[0]["owed"], Decimal("-100.00"))

    def test_khan_owes_its_share_of_what_scentory_paid(self):
        self.pay()
        self.assertEqual(self.balance(), ("Khan Perfume", "Scentory", Decimal("600.00")))

    def test_a_part_payment_splits_the_same_way(self):
        self.pay("500")
        self.assertEqual(self.balance(), ("Khan Perfume", "Scentory", Decimal("300.00")))

    def test_transfers_do_not_change_the_share(self):
        Purchase.objects.filter(store=self.khan).update(remaining=6)
        BusinessSettings.objects.update(transfer_markup=Decimal("0"))
        self.pay()
        before = finance.purchase_shares(self.order.payments.get())
        store_stock.move_units(self.product, 2, self.khan, self.scentory, reason="count")
        self.assertEqual(finance.purchase_shares(self.order.payments.get()), before)

    def test_share_nets_against_transfer_debt_and_settlement(self):
        self.pay()
        # Scentory took 2 bottles from Khan: 2 x (100 + 3) = 206 the other way
        store_stock.move_units(self.product, 2, self.khan, self.scentory)
        self.assertEqual(self.balance(), ("Khan Perfume", "Scentory", Decimal("394.00")))
        store_stock.settle(self.khan, self.scentory, Decimal("394.00"), settled_on=self.today)
        self.assertIsNone(self.balance())

    def test_the_balance_says_what_it_is_made_of(self):
        self.pay()
        store_stock.move_units(self.product, 2, self.khan, self.scentory)
        store_stock.settle(self.khan, self.scentory, Decimal("100.00"), settled_on=self.today)
        self.assertEqual(store_stock.balances()[0]["parts"],
                         {"transfers": Decimal("-206.00"), "purchases": Decimal("600.00"),
                          "settled": Decimal("-100.00")})

    def test_no_split_yet_no_debt_until_received(self):
        order = self.pending()
        self.pay("50", order=order)
        self.assertIsNone(self.balance())
        order.pending_items.all().delete()
        store_stock.receive_batches(product=self.product, quantity=5, cost_price=Decimal("10"),
                                    allocation={self.khan.id: 5}, inbound_order=order)
        self.assertEqual(self.balance(), ("Khan Perfume", "Scentory", Decimal("50.00")))

    def test_a_pending_split_counts(self):
        order = self.pending({str(self.khan.id): 4, str(self.scentory.id): 1})
        self.pay("50", order=order)
        self.assertEqual(self.balance(), ("Khan Perfume", "Scentory", Decimal("40.00")))

    def test_rollback_returns_to_the_pending_split(self):
        from .services.inbound_rollback import roll_back_receipt
        self.pay()
        ok, _message = roll_back_receipt(self.order, self.admin)
        self.assertTrue(ok)
        self.assertEqual(self.balance(), ("Khan Perfume", "Scentory", Decimal("600.00")))

    def test_zero_cost_order_owes_nothing(self):
        order = self.pending(cost="0", allocation={str(self.khan.id): 5})
        self.pay("10", order=order)
        self.assertEqual(finance.purchase_shares(order.payments.get()), {})
        self.assertIsNone(self.balance())

    def test_shares_add_up_to_the_payment(self):
        third = Store.objects.create(name="Third", code="T3")
        order = InboundOrder.objects.create(status="received")
        store_stock.receive_batches(product=self.product, quantity=3, cost_price=Decimal("10"),
                                    allocation={self.khan.id: 1, self.scentory.id: 1, third.id: 1},
                                    inbound_order=order)
        shares = finance.purchase_shares(self.pay("100", order=order))
        self.assertEqual(sum(v["cost"] for v in shares.values()), Decimal("100.00"))

    def test_the_shop_that_received_all_of_it_owes_nothing_to_itself(self):
        order = InboundOrder.objects.create(status="received")
        store_stock.receive_batches(product=self.product, quantity=2, cost_price=Decimal("10"),
                                    allocation={self.scentory.id: 2}, inbound_order=order)
        self.pay("20", order=order)
        self.assertIsNone(self.balance())

    def test_markup_defaults_to_nothing(self):
        self.assertEqual(self.pay().share_markup, Decimal("0.00"))

    def test_markup_is_kept_on_the_payment_when_the_setting_changes(self):
        BusinessSettings.objects.update(purchase_share_markup=Decimal("2.00"))
        self.pay()
        self.assertEqual(self.balance(), ("Khan Perfume", "Scentory", Decimal("612.00")))    # 600 + 6 x 2
        BusinessSettings.objects.update(purchase_share_markup=Decimal("9.00"))
        self.assertEqual(self.balance(), ("Khan Perfume", "Scentory", Decimal("612.00")))

    def test_markup_moves_profit_between_shops_and_nets_to_zero(self):
        BusinessSettings.objects.update(purchase_share_markup=Decimal("2.00"))
        self.pay()
        rows = store_stock.transfer_margins(self.today, self.today)
        self.assertEqual(sorted((s, a) for s, _d, a in rows),
                         sorted([(self.scentory.id, Decimal("12.00")), (self.khan.id, Decimal("-12.00"))]))

    def test_an_order_with_payments_cannot_be_cancelled_or_deleted(self):
        order = self.pending()
        self.pay("10", order=order)
        self.client.post(reverse("inbound_receive", args=[order.id]), {"action": "cancel"})
        self.assertTrue(InboundOrder.objects.filter(pk=order.pk).exists())

    def test_the_page_pays_and_deletes(self):
        url = reverse("finance_payment_add", args=[self.order.id])
        self.client.post(url, {"amount": "1000", "paid_on": self.today.isoformat(), "method": "bank",
                               "store": self.scentory.id})
        payment = SupplierPayment.objects.get()
        self.assertEqual(payment.store, self.scentory)
        self.assertEqual(self.client.get(reverse("finance_payables")).context["groups"], [])
        self.client.post(reverse("finance_payment_delete", args=[payment.id]))
        self.assertFalse(SupplierPayment.objects.exists())
        self.assertEqual(len(self.client.get(reverse("finance_payables")).context["groups"]), 1)

    def test_shop_settings_set_who_pays_and_the_markup(self):
        self.client.post(reverse("business_settings"), {"purchasing_store": self.khan.id,
                                                        "purchase_share_markup": "1,50"})
        row = BusinessSettings.get()
        self.assertEqual((row.purchasing_store, row.purchase_share_markup), (self.khan, Decimal("1.50")))


class MonthFiguresTests(FinanceMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start = self.month
        self.end = self.today
        self.drinks = Category.objects.create(name="Other goods")
        self.other = Product.objects.create(name="Mist", brand="B", barcode="9960000000002", category=self.drinks,
                                            default_price=Decimal("10"))

    def sale(self, store=None, price="100", cost="40", method="cash", product=None, charge=None, fee=None):
        order = SaleOrder.objects.create(store=store or self.khan)
        Sale.objects.create(order=order, product=product or self.product, quantity=1, unit_price=Decimal(price),
                            cost_basis=Decimal(cost), store=order.store, payment_method=method)
        extra = Decimal(charge or "0")
        SaleOrderPayment.objects.create(order=order, method=method, amount=Decimal(price) + extra)
        if charge:
            SaleOrderCharge.objects.create(order=order, label="Shipping", amount=extra)
        if fee:
            SaleOrder.objects.filter(pk=order.pk).update(card_fee=Decimal(fee))
        return order

    def profit(self, store=None):
        return finance.month_profit(self.start, self.end, store)

    def cash(self, store=None):
        return finance.month_cash(self.start, self.end, store)

    def test_net_is_gross_plus_charges_less_expenses(self):
        self.sale(charge="7.50")
        self.expense("20")
        figures = self.profit()
        self.assertEqual((figures["gross"], figures["charges"], figures["expenses"], figures["net"]),
                         (Decimal("60.00"), Decimal("7.50"), Decimal("20.00"), Decimal("47.50")))

    def test_supplier_payments_do_not_touch_net(self):
        order = InboundOrder.objects.create(status="received")
        store_stock.receive_batches(product=self.product, quantity=1, cost_price=Decimal("500"),
                                    allocation={self.scentory.id: 1}, inbound_order=order)
        finance.add_payment(order, store=self.scentory, amount=Decimal("500"), paid_on=self.today, method="bank",
                            note="", user=self.admin)
        self.assertEqual(self.profit()["net"], Decimal("0.00"))

    def test_gross_counts_every_category_not_just_perfumes(self):
        self.sale(price="10", cost="4", product=self.other)
        self.assertEqual(self.profit()["gross"], Decimal("6.00"))

    def test_a_shops_net_includes_the_transfer_markup(self):
        Purchase.objects.create(product=self.product, store=self.khan, quantity=5, remaining=5,
                                cost_price=Decimal("10"))
        store_stock.move_units(self.product, 2, self.khan, self.scentory)
        self.assertEqual(self.profit(self.khan)["net"], Decimal("6.00"))
        self.assertEqual(self.profit(self.scentory)["net"], Decimal("-6.00"))

    def test_all_shops_equal_the_sum_of_each(self):
        self.sale(self.khan)
        self.sale(self.scentory, price="50", cost="20")
        self.expense("10", store=self.khan)
        self.expense("5", store=self.scentory)
        self.assertEqual(self.profit()["net"], self.profit(self.khan)["net"] + self.profit(self.scentory)["net"])

    def test_rent_paid_next_month_splits_profit_and_cash(self):
        last_month = (self.month - timedelta(days=1)).replace(day=1)
        self.expense("300", period=last_month, paid_on=self.month, method="bank")
        self.assertEqual(self.profit()["expenses"], Decimal("0.00"))
        self.assertEqual(finance.month_profit(last_month, self.month - timedelta(days=1), None)["expenses"],
                         Decimal("300.00"))
        self.assertEqual(self.cash()["bank"]["out"], Decimal("300.00"))

    def test_categories_compare_with_last_month(self):
        last_month = (self.month - timedelta(days=1)).replace(day=1)
        self.expense("300", period=last_month)
        self.expense("350")
        rows = {r["category"]: r for r in self.profit()["by_category"]}
        self.assertEqual((rows["Rent"]["amount"], rows["Rent"]["previous"]), (Decimal("350.00"), Decimal("300.00")))

    def test_cash_flow_by_tender(self):
        self.sale(price="100", method="cash")
        self.sale(price="60", method="card")
        self.sale(price="40", method="mbway")
        self.expense("30", paid_on=self.today, method="cash")
        figures = self.cash()
        self.assertEqual((figures["cash"]["in"], figures["cash"]["out"], figures["bank"]["in"]),
                         (Decimal("100.00"), Decimal("30.00"), Decimal("100.00")))
        self.assertEqual(figures["net"], Decimal("170.00"))

    def test_card_fees_come_off_bank(self):
        self.sale(price="100", method="card", fee="1.00")
        self.assertEqual(self.cash()["bank"]["net"], Decimal("99.00"))

    def test_iou_payments_count_in_cash_not_profit(self):
        from .models import ARInvoice, ARPayment, Customer
        invoice = ARInvoice.objects.create(customer=Customer.objects.create(name="C"), store=self.khan,
                                           total_amount=Decimal("80"))
        ARPayment.objects.create(invoice=invoice, amount=Decimal("80"), method="bank")
        self.assertEqual(self.cash()["bank"]["in"], Decimal("80.00"))
        self.assertEqual(self.profit()["net"], Decimal("0.00"))

    def test_an_unpaid_expense_is_not_cash(self):
        self.expense("30")
        self.assertEqual(self.cash()["net"], Decimal("0.00"))
        self.assertEqual(finance.unpaid()["expenses"], Decimal("30.00"))

    def test_settlements_show_per_shop_and_not_in_all_shops(self):
        store_stock.settle(self.khan, self.scentory, Decimal("600"), settled_on=self.today)
        self.assertEqual(self.cash(self.khan)["cash"]["out"], Decimal("600.00"))
        self.assertEqual(self.cash(self.scentory)["cash"]["in"], Decimal("600.00"))
        self.assertEqual(self.cash()["net"], Decimal("0.00"))

    def test_owner_paid_stays_out_of_cash_and_bank(self):
        self.expense("30", paid_on=self.today, method="owner")
        figures = self.cash()
        self.assertEqual((figures["net"], figures["owner_paid"]), (Decimal("0.00"), Decimal("30.00")))

    def test_a_month_with_nothing_shows_zeros(self):
        page = self.client.get(reverse("finance"), {"month": "2024-01"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["profit"]["net"], Decimal("0.00"))

    def test_the_page_shows_a_column_per_shop_for_all_stores(self):
        session = self.client.session
        session["active_store_id"] = "all"
        session.save()
        self.sale(self.khan)
        self.expense("20", store=self.scentory)
        page = self.client.get(reverse("finance"))
        self.assertEqual([c["shop"].name for c in page.context["columns"]], ["Khan Perfume", "Scentory"])
        self.assertContains(page, "60.00")


class OwnerMoneyTests(FinanceMixin, TestCase):
    def fund(self, amount="1000", kind="in", when=None, store=None, method="bank"):
        return OwnerFunding.objects.create(store=store or self.khan, kind=kind, amount=Decimal(amount),
                                           date=when or self.today, method=method)

    def test_invested_counts_funding_and_what_the_owner_paid(self):
        self.fund("1000")
        self.expense("30", paid_on=self.today, method="owner")
        order = InboundOrder.objects.create(status="received")
        finance.add_payment(order, store=self.scentory, amount=Decimal("200"), paid_on=self.today, method="owner",
                            note="", user=self.admin)
        summary = finance.owner_summary(self.today)
        self.assertEqual((summary["in"], summary["paid_by_owner"], summary["invested"]),
                         (Decimal("1000.00"), Decimal("230.00"), Decimal("1230.00")))

    def test_taking_money_out_lowers_net(self):
        self.fund("1000")
        self.fund("300", kind="out")
        summary = finance.owner_summary(self.today)
        self.assertEqual((summary["out"], summary["net"]), (Decimal("300.00"), Decimal("700.00")))

    def test_it_counts_up_to_the_month_shown(self):
        self.fund("1000", when=date(2026, 1, 15))
        self.fund("500", when=date(2026, 3, 15))
        self.assertEqual(finance.owner_summary(date(2026, 2, 28))["net"], Decimal("1000.00"))

    def test_a_shop_sees_its_own(self):
        self.fund("1000")
        self.fund("500", store=self.scentory)
        self.assertEqual(finance.owner_summary(self.today, self.scentory)["net"], Decimal("500.00"))

    def test_funding_shows_in_cash_flow_by_method(self):
        self.fund("1000", method="bank")
        self.fund("100", kind="out", method="cash")
        figures = finance.month_cash(self.month, self.today, None)
        self.assertEqual((figures["bank"]["in"], figures["cash"]["out"]), (Decimal("1000.00"), Decimal("100.00")))

    def test_funding_is_not_profit(self):
        self.fund("1000")
        self.assertEqual(finance.month_profit(self.month, self.today, None)["net"], Decimal("0.00"))

    def test_past_funding_can_be_entered_edited_and_deleted(self):
        self.client.post(reverse("finance_owner"), {"store": self.khan.id, "kind": "in", "amount": "5000,50",
                                                    "date": "2025-06-01", "method": "bank", "note": "start"})
        row = OwnerFunding.objects.get()
        self.assertEqual((row.amount, row.date, row.created_by), (Decimal("5000.50"), date(2025, 6, 1), self.admin))
        self.client.post(reverse("finance_owner"), {"id": row.id, "store": self.khan.id, "kind": "in", "amount": "4000",
                                                    "date": "2025-06-01", "method": "cash"})
        row.refresh_from_db()
        self.assertEqual((row.amount, row.method), (Decimal("4000.00"), "cash"))
        self.client.post(reverse("finance_owner_delete", args=[row.id]))
        self.assertFalse(OwnerFunding.objects.exists())
        self.assertEqual(FinanceLog.objects.filter(kind="owner money").count(), 3)

    def test_value_is_stock_plus_owed_to_us_less_owed_by_us(self):
        from .models import ARInvoice, Customer
        Purchase.objects.create(product=self.product, store=self.khan, quantity=10, remaining=4,
                                cost_price=Decimal("25"))
        ARInvoice.objects.create(customer=Customer.objects.create(name="C"), total_amount=Decimal("80"),
                                 amount_paid=Decimal("30"), status="partial")
        order = InboundOrder.objects.create(status="pending_receipt")
        InboundPendingItem.objects.create(inbound_order=order, product=self.product, quantity=2,
                                          cost_price=Decimal("15"))
        self.expense("20")
        value = finance.business_value()
        self.assertEqual((value["stock"], value["receivable"], value["owed_suppliers"], value["unpaid_expenses"],
                          value["value"]),
                         (Decimal("100.00"), Decimal("50.00"), Decimal("30.00"), Decimal("20.00"), Decimal("100.00")))

    def test_value_is_for_all_shops_only(self):
        session = self.client.session
        session["active_store_id"] = self.khan.id
        session.save()
        self.assertIsNone(self.client.get(reverse("finance")).context["value"])
        session["active_store_id"] = "all"
        session.save()
        page = self.client.get(reverse("finance"))
        self.assertEqual(page.context["value"]["value"], Decimal("0.00"))
        self.assertContains(page, "cash in hand")

    def test_profit_to_date_starts_at_the_cut_off(self):
        order = SaleOrder.objects.create(store=self.khan)
        Sale.objects.create(order=order, product=self.product, quantity=1, unit_price=Decimal("100"),
                            cost_basis=Decimal("40"), store=self.khan, payment_method="cash")
        early = SaleOrder.objects.create(store=self.khan)
        Sale.objects.create(order=early, product=self.product, quantity=1, unit_price=Decimal("999"),
                            cost_basis=Decimal("1"), store=self.khan, payment_method="cash")
        SaleOrder.objects.filter(pk=early.pk).update(created_at=timezone.now() - timedelta(days=200))
        self.expense("10", period=self.today.replace(day=1))
        self.assertEqual(finance.profit_to_date(self.today), Decimal("50.00"))


class ExistingPagesTests(FinanceMixin, TestCase):
    def test_today_and_customer_pages_say_profit(self):
        from .models import Customer
        customer = Customer.objects.create(name="C")
        self.assertNotContains(self.client.get(reverse("daily_summary")), "Net Profit")
        self.assertNotContains(self.client.get(reverse("customer_detail", args=[customer.id])), "Net Profit")

    def test_purchases_panel_shows_paid_to_admins_only(self):
        order = InboundOrder.objects.create(status="received")
        store_stock.receive_batches(product=self.product, quantity=2, cost_price=Decimal("50"),
                                    allocation={self.khan.id: 2}, inbound_order=order)
        finance.add_payment(order, store=self.scentory, amount=Decimal("30"), paid_on=self.today, method="bank",
                            note="", user=self.admin)
        url = reverse("sales_records")
        self.assertContains(self.client.get(url, {"view": "purchases"}), "<dt>Owing</dt><dd>EUR 70.00</dd>", html=False)
        manager = get_user_model().objects.create_user("fin_mgr2", password="pw123456", is_staff=True)
        self.client.force_login(manager)
        self.assertNotContains(self.client.get(url, {"view": "purchases"}), "<dt>Owing</dt>")

    def test_audit_lists_finance_changes(self):
        finance.log(self.admin, "deleted", "expense", 1, "Khan Perfume · Rent · 2026-11 · €900")
        page = self.client.get(reverse("audit_finance"))
        self.assertContains(page, "Rent")
        self.assertContains(page, "Deleted")


class OverviewDetailsTests(FinanceMixin, TestCase):
    def test_the_first_month_to_date_equals_that_months_net(self):
        BusinessSettings.objects.update(payables_from=self.today)
        order = SaleOrder.objects.create(store=self.khan)
        Sale.objects.create(order=order, product=self.product, quantity=1, unit_price=Decimal("100"),
                            cost_basis=Decimal("40"), store=self.khan, payment_method="cash")
        SaleOrder.objects.filter(pk=order.pk).update(created_at=timezone.now().replace(day=1))
        self.expense("10")
        self.assertEqual(finance.profit_to_date(self.today),
                         finance.month_profit(self.month, self.today, None)["net"])

    def test_a_category_links_to_its_expenses(self):
        self.expense("10")
        html = self.client.get(reverse("finance")).content.decode()
        self.assertIn(f'{reverse("finance_expenses")}?category={self.rent.id}', html)
