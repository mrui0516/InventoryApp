# stock/models/expenses.py
"""The money going out, and the owner's own money coming in.

An ``Expense`` has two dates on purpose: ``period`` is the month whose profit it
counts against, ``paid_on`` the day the money actually left (empty: still owed).
October's rent paid on 2 November is October's cost and November's cash.

A ``SupplierPayment`` is cash, not cost: the goods reach profit as cost of sale
when they are sold, so a payment never touches net profit. What each shop owes
the shop that paid is worked out from these rows (services/finance), never
stored.
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

CASH = 'cash'
BANK = 'bank'
OWNER = 'owner'             # the owner paid it from their own pocket
SHOP_METHODS = [(CASH, 'Cash'), (BANK, 'Bank')]
PAID_METHODS = SHOP_METHODS + [(OWNER, "Owner's money")]


class ExpenseCategory(models.Model):
    name = models.CharField(max_length=60, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name_plural = 'expense categories'

    def __str__(self):
        return self.name


class ExpenseTemplate(models.Model):
    """A cost that comes every month: rent, a salary. Set once; each month it
    waits to be confirmed on the Finance page."""
    store = models.ForeignKey('Store', on_delete=models.PROTECT, related_name='expense_templates')
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='templates')
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    payee = models.CharField(max_length=120, blank=True, default='')
    note = models.CharField(max_length=200, blank=True, default='')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['store_id', 'category__sort_order', 'payee']

    def __str__(self):
        return f'{self.category} {self.payee} €{self.amount}'.replace('  ', ' ')


class ExpenseTemplateSkip(models.Model):
    """A month a template was deliberately left out."""
    template = models.ForeignKey(ExpenseTemplate, on_delete=models.CASCADE, related_name='skips')
    period = models.DateField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['template', 'period'], name='uniq_template_skip')]


class Expense(models.Model):
    store = models.ForeignKey('Store', on_delete=models.PROTECT, related_name='expenses')
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='expenses')
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    period = models.DateField(db_index=True)        # the 1st of the month it belongs to
    paid_on = models.DateField(null=True, blank=True, db_index=True)
    method = models.CharField(max_length=10, choices=PAID_METHODS, blank=True, default='')
    payee = models.CharField(max_length=120, blank=True, default='')
    note = models.CharField(max_length=200, blank=True, default='')
    template = models.ForeignKey(ExpenseTemplate, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='expenses')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-period', '-id']
        constraints = [models.UniqueConstraint(fields=['template', 'period'], condition=Q(template__isnull=False),
                                               name='uniq_expense_template_period')]

    def __str__(self):
        return f'{self.category} €{self.amount} ({self.period:%Y-%m})'


class SupplierPayment(models.Model):
    inbound_order = models.ForeignKey('InboundOrder', on_delete=models.PROTECT, related_name='payments')
    store = models.ForeignKey('Store', on_delete=models.PROTECT, related_name='supplier_payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    # € per unit the other shop pays on top of cost, as set when this was paid.
    share_markup = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    paid_on = models.DateField(db_index=True)
    method = models.CharField(max_length=10, choices=PAID_METHODS, default=BANK)
    note = models.CharField(max_length=200, blank=True, default='')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-paid_on', '-id']

    def __str__(self):
        return f'€{self.amount} on inbound #{self.inbound_order_id}'


class OwnerFunding(models.Model):
    """The owner putting their own money into a shop, or taking it out."""
    IN = 'in'
    OUT = 'out'
    KIND_CHOICES = [(IN, 'Put in'), (OUT, 'Taken out')]

    store = models.ForeignKey('Store', on_delete=models.PROTECT, related_name='owner_funding')
    kind = models.CharField(max_length=3, choices=KIND_CHOICES, default=IN)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    date = models.DateField(db_index=True)
    method = models.CharField(max_length=10, choices=SHOP_METHODS, default=BANK)
    note = models.CharField(max_length=200, blank=True, default='')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.get_kind_display()} €{self.amount} ({self.date})'


class FinanceLog(models.Model):
    """Who changed what in Finance: read by Settings -> Audit."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='+')
    action = models.CharField(max_length=20)
    kind = models.CharField(max_length=20)
    ref_id = models.PositiveIntegerField(null=True, blank=True)
    summary = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']
