# stock/models/catalog.py
"""Product catalog domain: categories, brands, series, products and their images."""
import re
from decimal import Decimal

from django.db import models
from django.db.models import Sum

from .product_types import ProductType


def product_image_upload_to(instance, filename):
    """Store product photos under product_images/<brand>/<filename>.

    Brand is sanitized for filesystem/URL safety; a blank brand falls back to
    product_images/<filename>.
    """
    brand = (getattr(getattr(instance, "product", None), "brand", "") or "").strip()
    brand = re.sub(r'[\\/:*?"<>|]+', "", brand).strip()
    return f"product_images/{brand}/{filename}" if brand else f"product_images/{filename}"


GENERAL = 'general'
PERFUME = 'perfume'

FORM_KINDS = [
    (GENERAL, 'General'),
    (PERFUME, 'Perfume'),
]


class Category(models.Model):
    name = models.CharField(max_length=50, db_index=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE)
    # Perfume is sold online; phone accessories are shop-floor only. Untick and
    # the whole category stops being pushed to Shopify - no code change needed.
    sync_to_shopify = models.BooleanField(default=True)
    # Which form a new product in this category gets. A perfume is asked for
    # its volume and concentration; a case is asked what it fits. Held as data
    # so a new category picks its form in the admin instead of in a name regex.
    form_kind = models.CharField(max_length=12, default=GENERAL, choices=FORM_KINDS)

    def __str__(self):
        return self.name

    @property
    def effective_form_kind(self):
        """This category's form kind, inherited from its parent if unset.

        A subcategory of Perfumes is a perfume unless it says otherwise, so
        creating "Minis" needs no extra thought.
        """
        node, seen = self, set()
        while node is not None and node.pk not in seen:
            if node.form_kind != GENERAL:
                return node.form_kind
            seen.add(node.pk)
            node = node.parent
        return GENERAL


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
    """Perfume strength: EDT / EDP / Extrait de Parfum ...

    Only some product types have one (``ProductType.takes_concentration``).
    Oil perfume and body mist are types of their own since 2026-11-04.

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
    # Optional: many perfumes are known by their series alone ("Khamrah"), and
    # requiring a name made people repeat the series into it - which is how
    # "Rayhaan Pharaoh Pharaoh" came about.
    name = models.CharField(max_length=100, db_index=True, blank=True, default='')
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
    # The restock bell (2026-10-26). Off = the shop is not buying this again for
    # now, so it stays out of the low-stock list - but the product, its stock
    # and its sales are untouched, and every analysis still sees it.
    restock = models.BooleanField(default=True, db_index=True)

    # --- perfume attributes (see docs/PERFUME_ATTRIBUTES.md) ---
    volume_ml = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    concentration = models.ForeignKey('Concentration', on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name='products')
    # What kind of product this is: the rows and their rules are in ProductType.
    PERFUME, BODY_SPRAY, AIR_FRESHENER, COFFRET, MINI = 'perfume', 'body_spray', 'air_freshener', 'coffret', 'mini'
    OIL_PERFUME, BODY_MIST = 'oil_perfume', 'body_mist'
    product_type = models.ForeignKey(ProductType, to_field='code', default=PERFUME, on_delete=models.PROTECT,
                                     related_name='products', verbose_name='Type')
    fragrance_families = models.ManyToManyField('FragranceFamily', blank=True, related_name='products')
    inspired_by = models.ForeignKey('Inspiration', on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='products')
    # Typed in rather than chosen: the reference differs for almost every
    # bottle, so the lookup table meant maintaining a list nobody reused. The
    # FK above stays only so the handful of existing rows are not lost.
    inspired_by_text = models.CharField(max_length=120, blank=True, default='')

    # The three note layers, as the shop writes them on the bottle. Free text
    # on purpose: these are prose ("Bergamota, Maca Verde"), not a fixed list.
    notes_top = models.CharField(max_length=200, blank=True, default='')
    notes_heart = models.CharField(max_length=200, blank=True, default='')
    notes_base = models.CharField(max_length=200, blank=True, default='')

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

    # -- how a perfume reads ----------------------------------------------
    @property
    def volume_label(self):
        """"100ml", from the structured volume rather than the old free text.

        ``spec`` was defaulted to 100ML for perfumes at one point, so a 20ml
        bottle can still be carrying "100ML" there. The recorded volume wins.
        """
        if self.volume_ml:
            return f'{self.volume_ml}ml'
        return (self.spec or '').strip()

    @property
    def strength_label(self):
        return getattr(self.concentration, 'short', '') or ''

    def title_parts(self, include_brand=True):
        """Brand, series, name, strength, size - skipping whatever is missing.

        The name is optional and often repeats the series, so a series equal to
        the name is said once.
        """
        series = (self.model or '').strip()
        name = (self.name or '').strip()
        if series and name and series.lower() == name.lower():
            name = ''
        parts = [(self.brand or '').strip() if include_brand else '', series, name]
        text = ' '.join(p for p in parts if p)

        strength = self.strength_label
        if strength and strength.lower() not in text.lower():
            text = f'{text} {strength}'.strip()
        size = self.volume_label
        if size and size.lower() not in text.lower():
            text = f'{text} {size}'.strip()
        return text

    @property
    def inspiration_label(self):
        """What this is inspired by - the typed text, or the old lookup row."""
        typed = (self.inspired_by_text or '').strip()
        return typed or (str(self.inspired_by) if self.inspired_by_id else '')

    @property
    def note_lines(self):
        """``[(label, notes)]`` for the layers that have been filled in."""
        pairs = [('Notas de Topo', self.notes_top),
                 ('Notas de Coração', self.notes_heart),
                 ('Notas de Base', self.notes_base)]
        return [(label, value.strip()) for label, value in pairs if (value or '').strip()]

    def composed_description(self, include_inspiration=True):
        """The short description with the structured details written under it.

        The shop writes one paragraph; the family, the three note layers and
        the reference are already recorded as fields, so repeating them by hand
        into every description is copying data that is already there - and the
        copy goes stale the moment a field changes.
        """
        blocks = []
        body = (self.description or '').strip()
        if body:
            blocks.append(body)

        families = ', '.join(f.name for f in self.fragrance_families.all())
        if families:
            blocks.append(f'Família olfativa: {families}')

        notes = [f'{label}: {value}' for label, value in self.note_lines]
        if notes:
            blocks.append(chr(10).join(notes))

        # Named on the shop's own paperwork, not on the public storefront:
        # printing another house's product on a listing is a trademark risk the
        # shop should take deliberately, not as a side effect of tidying up a
        # description. shopify_sync passes include_inspiration=False.
        inspiration = self.inspiration_label if include_inspiration else ''
        if inspiration:
            blocks.append(f'Inspirado em: {inspiration}')
        return (chr(10) * 2).join(blocks)

    @property
    def variant_label(self):
        size = f'{self.volume_ml}ml' if self.volume_ml else self.spec
        return " ".join([part for part in [size, self.color] if part])

    @property
    def type_info(self):
        return ProductType.for_code(self.product_type_id)

    @property
    def type_label(self):
        """The kind in a few letters: a perfume's concentration (EDP), otherwise
        the type (Body spray, Coffret ...)."""
        word = self.type_info.name_word
        if word:
            return word
        concentration = self.concentration
        return (concentration.short or concentration.name) if concentration else ''

    @property
    def list_name(self):
        """Name for lists that already show the brand: model and name, then
        the kind and the size - ``Club De Nuit Iconic · EDP · 105ml``."""
        parts = [self.model, self.name]
        if self.model and self.name and self.model.strip().lower() == self.name.strip().lower():
            parts = [self.model]                      # "Milano 22 Milano 22" says it once
        head = " ".join(part for part in parts if part) or self.brand or self.barcode
        words = head.lower().split()
        # "Lyric EDP" already says EDP, "Roll Oil 10ml" already says 10ml
        extra = [part for part in [self.type_label, self.variant_label]
                 if part and part.lower() not in words and part.lower() not in head.lower().replace(' ', '')]
        return " · ".join([head] + extra)

    @property
    def display_name(self):
        head = " - ".join([part for part in [self.brand, self.model, self.name] if part])
        tail = self.variant_label
        # A gift set is often named exactly as its perfume: say which this is.
        kind = self.type_info.name_word
        if kind and kind.lower() not in head.lower():
            head = f"{head} {kind}" if head else kind
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
