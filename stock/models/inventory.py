# stock/models/inventory.py
"""Inventory domain: inbound orders, FIFO purchase batches and stock-adjustment audit."""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class InboundOrder(models.Model):
    STATUS_CHOICES = [
        ('pending_receipt', 'Pending receipt'),
        ('received', 'Received'),
    ]
    supplier = models.ForeignKey('Supplier', on_delete=models.SET_NULL, null=True, blank=True)
    invoice_no = models.CharField(max_length=64, blank=True, null=True)
    invoice_date = models.DateField(blank=True, null=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    note = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='received', db_index=True)
    received_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        inv = self.invoice_no or 'No-INV'
        sup = self.supplier.name if self.supplier else '—'
        return f'InboundOrder #{self.id} | {inv} | {sup} | €{self.total_amount:.2f}'


class InboundPendingItem(models.Model):
    inbound_order = models.ForeignKey('InboundOrder', on_delete=models.CASCADE, related_name='pending_items')
    product = models.ForeignKey('Product', on_delete=models.CASCADE, db_index=True)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"Pending {self.product.display_name} x {self.quantity}"


class Purchase(models.Model):
    inbound_order = models.ForeignKey(
        InboundOrder, on_delete=models.CASCADE, related_name='items',
        null=True, blank=True  # 兼容历史数据
    )
    product = models.ForeignKey('Product', on_delete=models.CASCADE, db_index=True)
    supplier = models.ForeignKey('Supplier', on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateTimeField(auto_now_add=True, db_index=True)
    remaining = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.product.name} - {self.quantity} - {self.date:%Y-%m-%d %H:%M}"

    class Meta:
        indexes = [
            models.Index(fields=['product', 'date']),
            models.Index(fields=['product', 'remaining']),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(remaining__gte=0), name='purchase_remaining_non_negative'),
            models.CheckConstraint(check=models.Q(quantity__gte=models.F('remaining')), name='purchase_remaining_le_quantity'),
        ]


class StockAdjustmentLog(models.Model):
    ADJUSTMENT_TYPE_CHOICES = [
        ('purchase_remaining', 'Purchase Remaining'),
        ('total_stock', 'Total Stock'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stock_adjustment_logs',
    )
    product = models.ForeignKey('Product', on_delete=models.CASCADE, related_name='stock_adjustment_logs')
    purchase = models.ForeignKey(
        Purchase, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='adjustment_logs',
    )
    adjustment_type = models.CharField(max_length=20, choices=ADJUSTMENT_TYPE_CHOICES)
    old_value = models.IntegerField()
    new_value = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"{self.product.display_name}: {self.old_value} -> {self.new_value} ({self.get_adjustment_type_display()})"
