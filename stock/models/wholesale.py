"""The customer's side of the wholesale catalogue: a cart and order requests.

An order request is what a customer asks for, not a sale: it moves no stock and
counts for nothing in the books. It keeps every part of its price as it stood,
so later changes to products or rules never rewrite it.
"""
import uuid

from django.db import models


class CartLine(models.Model):
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='cart_lines')
    product = models.ForeignKey('stock.Product', on_delete=models.CASCADE, related_name='cart_lines')
    quantity = models.PositiveIntegerField()
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'product'], name='unique_cart_line')]


class OrderRequest(models.Model):
    SUBMITTED, CANCELLED = 'submitted', 'cancelled'
    STATUSES = [(SUBMITTED, 'Enviado'), (CANCELLED, 'Cancelado')]
    SHIPPING = [('standard', 'Portes'), ('free', 'Portes grátis'), ('pending', 'Portes por confirmar'),
                ('pickup', 'Levantamento em loja')]

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    customer = models.ForeignKey('stock.Customer', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='order_requests')
    submitted_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, related_name='order_requests')
    status = models.CharField(max_length=12, choices=STATUSES, default=SUBMITTED, db_index=True)

    # As the customer gave them, for this order: not the customer's record.
    delivery = models.CharField(max_length=10, default='delivery')
    business_name = models.CharField(max_length=120, blank=True, default='')
    contact_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30)
    email = models.EmailField(blank=True, default='')
    address = models.TextField(blank=True, default='')
    postal_code = models.CharField(max_length=20, blank=True, default='')
    city = models.CharField(max_length=80, blank=True, default='')
    country = models.CharField(max_length=2, blank=True, default='PT')
    note = models.TextField(blank=True, default='')

    subtotal = models.DecimalField(max_digits=10, decimal_places=2)         # what the tiers are read from
    tier_discount = models.DecimalField(max_digits=6, decimal_places=2)     # per unit
    tier_total = models.DecimalField(max_digits=10, decimal_places=2)
    goods_total = models.DecimalField(max_digits=10, decimal_places=2)
    shipping_state = models.CharField(max_length=10, choices=SHIPPING)
    shipping_fee = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)   # empty: to confirm
    policy = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['-created_at', '-id']

    @property
    def total(self):
        return self.goods_total + (self.shipping_fee or 0)

    @property
    def units(self):
        return sum(line.quantity for line in self.lines.all())


class OrderRequestLine(models.Model):
    request = models.ForeignKey(OrderRequest, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('stock.Product', on_delete=models.SET_NULL, null=True, related_name='+')
    name = models.CharField(max_length=250)
    barcode = models.CharField(max_length=20, blank=True, default='')
    quantity = models.PositiveIntegerField()
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    customer_discount = models.DecimalField(max_digits=8, decimal_places=2)
    catalog_price = models.DecimalField(max_digits=10, decimal_places=2)
    tier_discount = models.DecimalField(max_digits=6, decimal_places=2)
    final_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ['id']
