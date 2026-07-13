# stock/models/finance.py
"""Finance domain: accounts-receivable invoices, line items and payments."""
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class ARInvoice(models.Model):
    STATUS_CHOICES = [
        ("unpaid", "Unpaid"),
        ("partial", "Partial"),
        ("paid", "Paid"),
    ]
    customer = models.ForeignKey('Customer', on_delete=models.PROTECT, related_name='ar_invoices', db_index=True)
    store = models.ForeignKey('Store', on_delete=models.SET_NULL, null=True, blank=True, db_index=True, related_name='ar_invoices')
    date = models.DateField(auto_now_add=True, db_index=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="unpaid", db_index=True)
    note = models.CharField(max_length=255, blank=True, null=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"AR #{self.id} - {self.customer.name} - €{self.total_amount:.2f}"

    @property
    def balance(self) -> Decimal:
        return (self.total_amount or Decimal('0.00')) - (self.amount_paid or Decimal('0.00'))


class ARItem(models.Model):
    invoice = models.ForeignKey(ARInvoice, on_delete=models.CASCADE, related_name='items')
    product_name = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(100000)])
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])

    def __str__(self):
        return f"{self.product_name} x {self.quantity}"

    @property
    def line_total(self) -> Decimal:
        return (self.unit_price or Decimal('0.00')) * (self.quantity or 0)


class ARPayment(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('card', 'Card'),
        ('mbway', 'MBWay'),
        ('bank', 'Bank'),
        ('other', 'Other'),
    ]
    invoice = models.ForeignKey(ARInvoice, on_delete=models.CASCADE, related_name='payments')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES, default='cash')
    note = models.CharField(max_length=200, blank=True, null=True)

    def __str__(self):
        return f"Payment €{self.amount:.2f} to AR #{self.invoice_id}"
