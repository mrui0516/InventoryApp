# stock/models.py
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.core.validators import MaxValueValidator, MinValueValidator
from .attendance_models import AttendanceRecord

class Category(models.Model):
    name = models.CharField(max_length=50, db_index=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE)

    def __str__(self):
        return self.name


class Brand(models.Model):
    name = models.CharField(max_length=80, unique=True, db_index=True)
    categories = models.ManyToManyField(Category, blank=True, related_name='brands')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ProductSeries(models.Model):
    brand = models.ForeignKey('Brand', on_delete=models.CASCADE, related_name='series')
    name = models.CharField(max_length=100, db_index=True)

    class Meta:
        ordering = ['brand__name', 'name']
        constraints = [
            models.UniqueConstraint(fields=['brand', 'name'], name='unique_series_per_brand')
        ]

    def __str__(self):
        return f"{self.brand.name} - {self.name}"


class Supplier(models.Model):
    name = models.CharField(max_length=100, db_index=True, verbose_name="Name")
    phone = models.CharField(max_length=20, blank=True, null=True, verbose_name="WhatsApp")
    country = models.CharField(max_length=50, blank=True, null=True)
    address = models.CharField(max_length=200, blank=True, null=True)
    product_types = models.ManyToManyField(Category, blank=True, verbose_name="Supplied Categories")

    def __str__(self):
        return self.name


class Customer(models.Model):
    nif = models.CharField(max_length=9, unique=True, verbose_name="NIF")
    name = models.CharField(max_length=100, db_index=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    notes = models.TextField(blank=True, null=True, verbose_name="Notes")

    def __str__(self):
        return f"{self.name} ({self.nif})"


class PrintProfile(models.Model):
    name = models.CharField(max_length=120, default='KHAN PERFUME')
    nif = models.CharField(max_length=32, blank=True, default='517067226')
    phone = models.CharField(max_length=40, blank=True, default='(+351) 920 106 263')
    address = models.CharField(max_length=255, blank=True, default='CENTRO BABILONIA LOJA 90A, AMADORA, 2700-337')
    email = models.EmailField(blank=True, default='SADIWALIKHAN@YAHOO.COM')
    footer_note = models.CharField(max_length=160, blank=True, default='Thank you for your purchase.')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Print profile'
        verbose_name_plural = 'Print profile'

    def __str__(self):
        return self.name or 'Print profile'

    @classmethod
    def get_solo(cls):
        profile, _created = cls.objects.get_or_create(pk=1)
        return profile


class Product(models.Model):
    name = models.CharField(max_length=100, db_index=True)
    model = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    barcode = models.CharField(max_length=13, unique=True, db_index=True)
    brand = models.CharField(max_length=50, db_index=True)
    brand_master = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    series_master = models.ForeignKey(ProductSeries, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, db_index=True)
    spec = models.CharField(max_length=80, blank=True, null=True, db_index=True)
    color = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # ✅ 新增：默认售价，作为“查价变量”
    default_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, db_index=True)
    wholesale_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, db_index=True)

    def __str__(self):
        return f'{self.barcode} - {self.display_name}'

    def save(self, *args, **kwargs):
        if self.series_master_id and (
            not self.brand_master_id or self.series_master.brand_id != self.brand_master_id
        ):
            self.brand_master = self.series_master.brand

        if self.brand_master_id:
            self.brand = (self.brand_master.name or '').strip()
        else:
            self.brand = (self.brand or '').strip()

        if self.series_master_id:
            self.model = (self.series_master.name or '').strip()
        else:
            self.model = (self.model or '').strip() or None

        self.name = (self.name or '').strip()
        self.spec = (self.spec or '').strip() or None
        self.color = (self.color or '').strip() or None
        super().save(*args, **kwargs)

    @property
    def retail_price(self):
        return self.default_price

    @retail_price.setter
    def retail_price(self, value):
        self.default_price = value

    @property
    def variant_label(self):
        return " ".join([part for part in [self.spec, self.color] if part])

    @property
    def display_name(self):
        head = " - ".join([part for part in [self.brand, self.model, self.name] if part])
        tail = self.variant_label
        if head and tail:
            return f"{head} {tail}"
        return head or tail or (self.barcode or 'Unnamed product')

    # 你原来的方法保留
    def current_fifo_cost_price(self):
        purchases = self.purchase_set.filter(remaining__gt=0).order_by('date')
        return purchases.first().cost_price if purchases.exists() else self.last_known_cost_price()

    def total_stock(self):
        result = self.purchase_set.aggregate(total=Sum('remaining'))
        return result['total'] or 0

    def last_known_cost_price(self):
        purchases = self.purchase_set.order_by('date')
        return purchases.last().cost_price if purchases.exists() else Decimal('0.00')

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
    product = models.ForeignKey(Product, on_delete=models.CASCADE, db_index=True)
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
    product = models.ForeignKey(Product, on_delete=models.CASCADE, db_index=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
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


# ✅ 新增：订单头
class SaleOrder(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
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
    product = models.ForeignKey(Product, on_delete=models.CASCADE, db_index=True)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField(validators=[MaxValueValidator(1000), MinValueValidator(1)])
    # ✅ 字段更名：sale_price -> unit_price（语义更清晰）
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
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


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='product_images/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} Image"


class DailySalesSummary(models.Model):
    date = models.DateField(unique=True)
    total_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    total_profit = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    total_items_sold = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Sales on {self.date}"

    class Meta:
        ordering = ['-date']



class ARInvoice(models.Model):
    STATUS_CHOICES = [
        ("unpaid", "Unpaid"),
        ("partial", "Partial"),
        ("paid", "Paid"),
    ]
    customer = models.ForeignKey('Customer', on_delete=models.PROTECT, related_name='ar_invoices', db_index=True)
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
