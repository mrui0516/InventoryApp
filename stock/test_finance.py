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


FINANCE_URLS = ['finance_expenses', 'finance_templates']


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
