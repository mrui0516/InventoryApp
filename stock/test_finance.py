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
