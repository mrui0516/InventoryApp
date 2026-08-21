# stock/models/catalog.py
"""Product catalog domain: categories, brands, series, products and their images."""
import re
from decimal import Decimal

from django.db import models
from django.db.models import Sum


def product_image_upload_to(instance, filename):
    """Store product photos under product_images/<brand>/<filename>.

    Brand is sanitized for filesystem/URL safety; a blank brand falls back to
    product_images/<filename>.
    """
    brand = (getattr(getattr(instance, "product", None), "brand", "") or "").strip()
    brand = re.sub(r'[\\/:*?"<>|]+', "", brand).strip()
    return f"product_images/{brand}/{filename}" if brand else f"product_images/{filename}"


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



class Concentration(models.Model):
    """Perfume strength: EDT / EDP / Extrait de Parfum / Attar / Oil ...

    A table rather than ``choices`` so the shop can add a new strength itself
    without a code change. ``shopify_tag`` is optional — blank means the value
    stays internal and is never pushed to the storefront.
    """
    name = models.CharField(max_length=60, unique=True)
    short = models.CharField(max_length=12, blank=True, default='')   # e.g. EDP
    shopify_tag = models.CharField(max_length=60, blank=True, default='')
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.short or self.name


class FragranceFamily(models.Model):
    """Broad scent family (floral, oud, fruity, woody, sweet, fresh ...).

    A perfume usually belongs to several, hence the many-to-many on Product.
    """
    name = models.CharField(max_length=60, unique=True)
    shopify_tag = models.CharField(max_length=60, blank=True, default='')
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name_plural = 'fragrance families'

    def __str__(self):
        return self.name


class Inspiration(models.Model):
    """The designer/niche fragrance an Arabic perfume is inspired by.

    Internal only: it drives in-store search ("which one smells like Baccarat?")
    and is deliberately never synced to Shopify, since publishing another
    house's trademark on a storefront carries legal risk.
    """
    house = models.CharField(max_length=80)                 # Givenchy
    name = models.CharField(max_length=120)                 # L'Interdit

    class Meta:
        ordering = ['house', 'name']
        constraints = [models.UniqueConstraint(fields=['house', 'name'], name='unique_inspiration')]

    def __str__(self):
        return f'{self.house} {self.name}'.strip()


class ActiveProductManager(models.Manager):
    """Default manager: the live catalogue only.

    Deleting a product archives it instead of destroying rows, because its sales
    are the books. Every catalogue query (lists, search, scanning, exports,
    Shopify sync) goes through ``objects`` and so skips archived products, while
    ``all_objects`` still sees them. ``Meta.base_manager_name`` points at
    ``all_objects`` so a historical ``sale.product`` keeps resolving.
    """

    def get_queryset(self):
        return super().get_queryset().filter(archived_at__isnull=True)


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

    # ✅ 新增：默认售价，作为”查价变量”
    default_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, db_index=True)
    wholesale_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, db_index=True)
    price_locked = models.BooleanField(default=False)

    GENDER_CHOICES = [
        ('men', 'Men'),
        ('women', 'Women'),
        ('unisex', 'Unisex'),
    ]
    # Shopify tag per gender (Portuguese storefront; drives the smart collections).
    GENDER_SHOPIFY_TAGS = {'men': 'Homem', 'women': 'Mulher', 'unisex': 'Unissexo'}
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, default='', db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # --- perfume attributes (see docs/PERFUME_ATTRIBUTES.md) ---
    volume_ml = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    concentration = models.ForeignKey('Concentration', on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name='products')
    fragrance_families = models.ManyToManyField('FragranceFamily', blank=True, related_name='products')
    inspired_by = models.ForeignKey('Inspiration', on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='products')

    objects = ActiveProductManager()   # live catalogue
    all_objects = models.Manager()     # includes archived — history, admin

    class Meta:
        base_manager_name = 'all_objects'

    @property
    def is_archived(self):
        return self.archived_at is not None

    @property
    def gender_shopify_tag(self):
        return self.GENDER_SHOPIFY_TAGS.get(self.gender, '')

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
        size = f'{self.volume_ml}ml' if self.volume_ml else self.spec
        return " ".join([part for part in [size, self.color] if part])

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


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to=product_image_upload_to)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} Image"
