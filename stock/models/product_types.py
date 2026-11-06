"""What kind of product this is, and the rules that come with it.

A table, so the shop adds a type (Creme, say) without a code change. The rules
that used to name types in the code are switches on the row.

Names read the type on every row of every list, so the rows are held in memory
for a moment rather than joined everywhere. Another worker sees an edit within
``TTL`` seconds.
"""
import time

from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

TTL = 30


class ProductType(models.Model):
    # Fixed when the row is made: it is what products point at, so renaming a
    # type changes nothing but its words.
    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=60)
    # The word in a product's name, "Body mist"; empty says nothing.
    name_word = models.CharField(max_length=40, blank=True, default='')
    # On the customer's PDF, in Portuguese; empty uses ``name``.
    customer_label = models.CharField(max_length=60, blank=True, default='')
    # Auto pricing and the wholesale discount go by the category.
    default_category = models.ForeignKey('Category', on_delete=models.PROTECT, null=True, blank=True,
                                         related_name='product_types')
    takes_concentration = models.BooleanField(default=False)
    takes_volume = models.BooleanField(default=True)
    wholesale_discount = models.BooleanField(default=False)
    is_gift = models.BooleanField(default=False)
    has_contents = models.BooleanField(default=False)
    # Made to order from a perfume the customer picks: its own stock does not count.
    picks_perfume = models.BooleanField(default=False)
    shows_scent_details = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name

    @classmethod
    def all_cached(cls):
        return list(_rows().values())

    @classmethod
    def for_code(cls, code):
        rows = _rows()
        if code not in rows:                  # made on another worker just now
            clear_type_cache()
            rows = _rows()
        return rows.get(code) or cls(code=code, name=code)


_held = (0.0, {})


def _rows():
    global _held
    until, rows = _held
    if time.monotonic() >= until:
        rows = {row.code: row for row in ProductType.objects.all()}
        _held = (time.monotonic() + TTL, rows)
    return rows


def clear_type_cache():
    global _held
    _held = (0.0, {})


@receiver(post_save, sender=ProductType)
@receiver(post_delete, sender=ProductType)
def _forget_types(sender, **kwargs):
    clear_type_cache()
