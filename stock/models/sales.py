# stock/models/sales.py
"""Sales domain: sale orders, sale line items and the order-correction audit log."""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


# ✅ 新增：订单头
class SaleOrder(models.Model):
    customer = models.ForeignKey('Customer', on_delete=models.SET_NULL, null=True, blank=True)
    store = models.ForeignKey('Store', on_delete=models.SET_NULL, null=True, blank=True, db_index=True, related_name='sale_orders')
    affects_stock = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    note = models.CharField(max_length=200, blank=True, null=True)

    def __str__(self):
        return f"Order #{self.id} - {self.created_at:%Y-%m-%d}"

    @property
    def total_amount(self):
        return sum((i.quantity * i.unit_price for i in self.items.all()), Decimal('0.00'))

    @property
    def total_items(self):
        return sum((i.quantity for i in self.items.all()), 0)


# ✅ 原来的 Sale 作为 “销售行项目”
class Sale(models.Model):  # 可保留表名不改
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('card', 'Card'),
        ('mbway', 'MBWay'),
    ]

    order = models.ForeignKey(SaleOrder, on_delete=models.CASCADE, related_name='items', null=True, db_index=True)
    product = models.ForeignKey('Product', on_delete=models.CASCADE, db_index=True)
    store = models.ForeignKey('Store', on_delete=models.SET_NULL, null=True, blank=True, db_index=True, related_name='sales')
    customer = models.ForeignKey('Customer', on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField(validators=[MaxValueValidator(1000), MinValueValidator(1)])
    # ✅ 字段更名：sale_price -> unit_price（语义更清晰）
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    # True FIFO cost of the units this line consumed, captured at sale time so profit
    # is exact (not re-derived afterward). Null on old rows / backfill / non-stock
    # sales — those fall back to the reconstructed cost.
    cost_basis = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES)
    date = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['date']),
            models.Index(fields=['product', 'date']),
            models.Index(fields=['order', 'product']),
        ]


class SaleOrderChangeLog(models.Model):
    ACTION_CHOICES = [
        ('create', 'Create'),
        ('update', 'Update'),
        ('delete', 'Delete'),
    ]

    order = models.ForeignKey(
        SaleOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='change_logs',
    )
    order_id_snapshot = models.PositiveIntegerField(db_index=True)
    action = models.CharField(max_length=12, choices=ACTION_CHOICES, db_index=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sale_order_change_logs',
    )
    reason = models.CharField(max_length=255, blank=True)
    before_data = models.JSONField(default=dict, blank=True)
    after_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"Order change #{self.id} · order {self.order_id_snapshot} · {self.action}"


class SaleOrderPayment(models.Model):
    """Order-level split tender: how a single order's total was paid across methods.

    The authoritative record of payment allocation (e.g. EUR 60 card + EUR 40
    cash for one order). ``Sale.payment_method`` is still kept per line (set to
    the order's primary/largest method for split orders) so the existing
    category-aware payment reporting keeps working.
    """
    order = models.ForeignKey(SaleOrder, on_delete=models.CASCADE, related_name='payments')
    method = models.CharField(max_length=10, choices=Sale.PAYMENT_METHOD_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.get_method_display()} EUR {self.amount:.2f} (order {self.order_id})"


class SaleOrderCharge(models.Model):
    """Shipping or a fee on an order - money taken that is not a product.

    Kept apart from ``Sale`` on purpose: a charge has no product, no stock and
    no cost, and sales, profit, targets and best-sellers are all sums over
    ``Sale``. Leaving charges out of that table keeps every one of those
    figures goods-only without a filter at each call site.

    What a charge does change is the money taken: it is paid with the goods, so
    the order's ``SaleOrderPayment`` rows add up to goods + charges, and the
    cash/card/MB WAY collected that Today shows includes it.
    """
    order = models.ForeignKey(SaleOrder, on_delete=models.CASCADE, related_name='charges')
    label = models.CharField(max_length=60)
    amount = models.DecimalField(max_digits=10, decimal_places=2,
                                 validators=[MinValueValidator(Decimal('0.01'))])
    # The method it was paid with, so a reopened order (Correct Order) can
    # rebuild its payment split without guessing.
    payment_method = models.CharField(max_length=10, choices=Sale.PAYMENT_METHOD_CHOICES, default='cash')

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.label} EUR {self.amount:.2f} (order {self.order_id})"


class PedidoDraft(models.Model):
    """A Pedido kept after its PDF, until it is sold or thrown away.

    One store's draft: a quote is made from a shop's stock and sold from the
    same till. The form is kept as the user filled it (lines, shipping, fees,
    discount), so Edit reopens exactly what was printed.
    """
    OPEN = 'open'
    SOLD = 'sold'
    STATUS_CHOICES = [(OPEN, 'Open'), (SOLD, 'Sold')]

    store = models.ForeignKey('Store', on_delete=models.CASCADE, related_name='pedido_drafts')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='+')
    customer = models.ForeignKey('Customer', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='pedido_drafts')
    customer_name = models.CharField(max_length=100, blank=True, default='')
    customer_phone = models.CharField(max_length=40, blank=True, default='')
    customer_address = models.TextField(blank=True, default='')
    # [{"product_id": 1, "quantity": 2, "unit_price": "12.50"}, ...]
    lines = models.JSONField(default=list)
    shipping = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    # [{"label": "Embalagem", "amount": "3.00"}, ...]
    extras = models.JSONField(default=list)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    discount_kind = models.CharField(max_length=10, default='amount')
    valid_days = models.PositiveIntegerField(default=7)
    note = models.TextField(blank=True, default='')
    # Totals as printed, so the list does not recompute every draft.
    goods_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=OPEN, db_index=True)
    sale_order = models.ForeignKey(SaleOrder, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='pedido_drafts')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']

    def __str__(self):
        return f"Pedido draft #{self.id} - {self.customer_name or 'cliente'}"

    @property
    def line_count(self):
        return len(self.lines or [])
