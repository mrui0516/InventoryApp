"""Which phone does this case fit?

Accessories are not sold by what they are, they are sold by what they fit. A
customer asks for "a case for a 15 Pro Max" and says it a different way every
time - "15PM", "iphone15 pro max", "A2849". Three things model that:

* ``DeviceModel``  - one real handset, "iPhone 15 Pro Max".
* ``DeviceAlias``  - every other way somebody writes that handset. Data, not
  code, so a new spelling is a row in the admin.
* ``CompatibilityGroup`` - handsets that share a dimension, so one screen
  protector fits all of them. The group is named once and reused by every
  product that fits it, instead of re-ticking the same six phones each time.

A product can fit specific models, fit whole groups, or be ``universal_fit``
(cables, chargers, mice - things that fit everything). All three are OR-ed
together by ``Product.fits()``.
"""
import re

from django.db import models


def normalise_device_text(text):
    """Fold a handset spelling down to a comparable key.

    "iPhone 15 Pro Max", "iphone-15 pro max" and "IPHONE15PROMAX" all become
    ``iphone15promax``, so the till finds the same phone whichever way it is
    typed. Accent-free on purpose: these are Latin-alphabet model numbers.
    """
    return re.sub(r'[^a-z0-9]+', '', (text or '').lower())


class DeviceModel(models.Model):
    """A handset (or tablet, or watch) that accessories are made to fit."""
    brand = models.ForeignKey('stock.Brand', on_delete=models.CASCADE,
                              related_name='device_models')
    name = models.CharField(max_length=80, db_index=True)
    # Kept in sync by save(); the till searches this, never the raw name.
    normalised = models.CharField(max_length=80, db_index=True, editable=False)
    release_year = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    # Digits zero-padded so "iPhone 9" sorts before "iPhone 11" and a newly
    # added model lands in its place with no re-sorting pass. See shelf.py.
    sort_key = models.CharField(max_length=160, db_index=True, editable=False, default='')

    class Meta:
        ordering = ['brand__name', 'sort_key', 'name']
        unique_together = [('brand', 'name')]

    def __str__(self):
        return f'{self.brand.name} {self.name}'

    def save(self, *args, **kwargs):
        # Keyed on the name alone: staff type "iPhone 15 Pro Max", not
        # "Apple iPhone 15 Pro Max". resolve_device() strips a leading brand
        # for the people who do type it.
        self.normalised = normalise_device_text(self.name)
        from .shelf import natural_key
        self.sort_key = natural_key(self.name)[:160]
        super().save(*args, **kwargs)


class DeviceAlias(models.Model):
    """Another way people write a handset: "15PM", "15 Pro Max", "A2849"."""
    device = models.ForeignKey(DeviceModel, on_delete=models.CASCADE,
                               related_name='aliases')
    alias = models.CharField(max_length=80)
    normalised = models.CharField(max_length=80, db_index=True, editable=False)

    class Meta:
        ordering = ['alias']
        verbose_name_plural = 'device aliases'
        # The same shorthand must not point at two handsets, or the till has
        # no way to choose between them.
        constraints = [models.UniqueConstraint(fields=['normalised'],
                                               name='unique_device_alias')]

    def __str__(self):
        return f'{self.alias} -> {self.device}'

    def save(self, *args, **kwargs):
        self.normalised = normalise_device_text(self.alias)
        super().save(*args, **kwargs)


class CompatibilityGroup(models.Model):
    """Handsets that share a dimension, so one accessory fits all of them."""
    name = models.CharField(max_length=120, unique=True)
    devices = models.ManyToManyField(DeviceModel, blank=True,
                                     related_name='compatibility_groups')
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


def resolve_device(text):
    """Find the handset somebody typed, by name or by any known alias."""
    key = normalise_device_text(text)
    if not key:
        return None
    for candidate in _brand_stripped(key):
        device = DeviceModel.objects.filter(normalised=candidate).first()
        if device:
            return device
        alias = (DeviceAlias.objects
                 .filter(normalised=candidate).select_related('device').first())
        if alias:
            return alias.device
    return None


def _brand_stripped(key):
    """``key`` as typed, then again with a leading brand name removed.

    Somebody typing "Apple iPhone 15 Pro Max" and somebody typing
    "iPhone 15 Pro Max" mean the same handset. Brands come from the database,
    so a new one needs no code.
    """
    yield key
    from stock.models import Brand
    for brand_name in Brand.objects.values_list('name', flat=True):
        prefix = normalise_device_text(brand_name)
        if prefix and key.startswith(prefix) and len(key) > len(prefix):
            yield key[len(prefix):]


def search_devices(text, limit=10):
    """Handsets whose name or alias contains what was typed - for a picker."""
    key = normalise_device_text(text)
    if not key:
        return DeviceModel.objects.none()
    matches = models.Q()
    for candidate in _brand_stripped(key):
        matches |= (models.Q(normalised__contains=candidate) |
                    models.Q(aliases__normalised__contains=candidate))
    return (DeviceModel.objects
            .filter(matches)
            .filter(is_active=True)
            .distinct()[:limit])


def products_fitting(device, queryset=None):
    """Every product that fits ``device``, including universal goods.

    Three ways to fit, OR-ed: the product names the handset, the product names
    a group the handset belongs to, or the product fits everything.
    ``distinct()`` matters - a product listing both the model and its group
    would otherwise come back twice.
    """
    from stock.models import Product
    queryset = Product.objects.all() if queryset is None else queryset
    if device is None:
        return queryset.filter(universal_fit=True)
    return queryset.filter(
        models.Q(universal_fit=True) |
        models.Q(device_models=device) |
        models.Q(compatibility_groups__devices=device)
    ).distinct()
