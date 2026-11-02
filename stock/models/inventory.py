# stock/models/inventory.py
"""Inventory domain: inbound orders, FIFO purchase batches and stock-adjustment audit."""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


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
    # When the order was placed with the supplier, and when it landed. Both
    # are needed to measure a lead time, and both are stamped by this model
    # rather than by whichever view happens to be doing the work - a receipt
    # that forgets to stamp is a lead time lost for good.
    #
    # Orders that predate this field keep placed_at NULL and are simply not
    # measured: there is no honest way to invent when they were ordered.
    placed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    received_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        inv = self.invoice_no or 'No-INV'
        sup = self.supplier.name if self.supplier else '—'
        return f'InboundOrder #{self.id} | {inv} | {sup} | €{self.total_amount:.2f}'

    def save(self, *args, **kwargs):
        # A new order is being placed now, by definition - whichever code path
        # creates it, including the admin.
        if self._state.adding:
            if self.placed_at is None:
                self.placed_at = timezone.now()
            # status defaults to 'received', so a row created straight as
            # received would otherwise carry no receipt time at all. That is
            # how 40 orders ended up unmeasurable.
            if self.status == 'received' and self.received_at is None:
                self.received_at = self.placed_at
        super().save(*args, **kwargs)

    def mark_received(self, when=None):
        """Confirm the goods landed, stamping the time exactly once."""
        self.status = 'received'
        self.received_at = when or timezone.now()
        self.save(update_fields=['status', 'received_at'])
        return self.received_at

    @property
    def lead_time_days(self):
        """Days from placed to received, or ``None`` when not measurable."""
        if self.placed_at is None or self.received_at is None:
            return None
        return (self.received_at - self.placed_at).total_seconds() / 86400.0


class InboundPendingItem(models.Model):
    inbound_order = models.ForeignKey('InboundOrder', on_delete=models.CASCADE, related_name='pending_items')
    product = models.ForeignKey('Product', on_delete=models.CASCADE, db_index=True)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    # How the line is split between the shops: {"<store id>": units}. Set
    # before the goods are received; the receipt makes one batch per shop.
    allocation = models.JSONField(default=dict, blank=True)
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
    # The shop the batch is in. Left empty it lands in the default store; it
    # stays empty only where no store exists at all.
    store = models.ForeignKey('Store', on_delete=models.PROTECT, null=True, blank=True,
                              related_name='batches', db_index=True)
    # Set on a batch made by moving units from another shop's batch.
    transfer_line = models.ForeignKey('StockTransferLine', on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name='batches')
    # The shop the goods were delivered to. It never changes: a transfer moves
    # the units (``store``) and the batch it makes keeps this, so a shop's
    # inbound record stays what it received.
    received_store = models.ForeignKey('Store', on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='received_batches')

    def __str__(self):
        return f"{self.product.name} - {self.quantity} - {self.date:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        if self.store_id is None and self._state.adding:
            from .core import Store
            self.store = Store.get_default()
        if self.received_store_id is None and self._state.adding:
            self.received_store_id = self.store_id
        super().save(*args, **kwargs)

    class Meta:
        indexes = [
            models.Index(fields=['product', 'date']),
            models.Index(fields=['product', 'remaining']),
            models.Index(fields=['product', 'store', 'remaining']),
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
