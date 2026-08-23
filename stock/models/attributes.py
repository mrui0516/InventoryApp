"""Attributes a shop can define for itself, without touching code.

Perfume needed volume, concentration and fragrance family, so those became
real columns. Accessories cannot work that way: a case has a type (rubber,
flip, magsafe), a screen protector has an edge (flat, curved) and a glue
(full, edge-only), and next month it is something else entirely. Adding a
column and a migration for each is not a shop workflow.

So the shop defines them:

* ``CategoryAttribute`` - "Accessories have a field called Case type, it is a
  choice, and it distinguishes stock rows."
* ``AttributeOption``   - the choices for it.
* ``ProductAttributeValue`` - what one product answers, stored in a typed
  column so numbers sort as numbers.

Attributes are **inherited down the category tree**: define "Colour" once on
Accessories and every subcategory has it.

``variant_attribute`` is the important flag. It marks the attributes that make
two things separate stock rows rather than the same thing described twice - a
black case and a clear case are different products, but the same case
described as "soft-touch" is not. Step 4's matrix entry reads exactly this
flag to decide which grid to draw.
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models

TEXT = 'text'
CHOICE = 'choice'
NUMBER = 'number'
BOOLEAN = 'boolean'

DATA_TYPES = [
    (TEXT, 'Text'),
    (CHOICE, 'Choice'),
    (NUMBER, 'Number'),
    (BOOLEAN, 'Yes / No'),
]


class CategoryAttribute(models.Model):
    """A field the shop has defined for a category and everything under it."""
    category = models.ForeignKey('stock.Category', on_delete=models.CASCADE,
                                 related_name='attributes')
    name = models.CharField(max_length=60)
    # Stable key for templates and imports; the name can be renamed freely.
    code = models.SlugField(max_length=40)
    data_type = models.CharField(max_length=10, choices=DATA_TYPES, default=TEXT)
    unit = models.CharField(max_length=12, blank=True, help_text='e.g. mm, W, cm')
    required = models.BooleanField(default=False)
    # Does this attribute make two items *different products*? Colour does;
    # "soft-touch finish" does not. Matrix entry is built from these.
    variant_attribute = models.BooleanField(default=False)
    show_in_list = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']
        unique_together = [('category', 'code')]

    def __str__(self):
        return f'{self.category.name} / {self.name}'

    def clean(self):
        if self.data_type != CHOICE and self.pk and self.options.exists():
            raise ValidationError(
                {'data_type': 'This attribute has options, so it must stay a Choice.'})


class AttributeOption(models.Model):
    """One of the answers a Choice attribute accepts."""
    attribute = models.ForeignKey(CategoryAttribute, on_delete=models.CASCADE,
                                  related_name='options')
    label = models.CharField(max_length=60)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'label']
        unique_together = [('attribute', 'label')]

    def __str__(self):
        return self.label


class ProductAttributeValue(models.Model):
    """What one product answers for one attribute.

    Typed columns rather than one text blob, so a number sorts and filters as
    a number. Exactly one of them is filled, chosen by the attribute's type.
    """
    product = models.ForeignKey('stock.Product', on_delete=models.CASCADE,
                                related_name='attribute_values')
    attribute = models.ForeignKey(CategoryAttribute, on_delete=models.CASCADE,
                                  related_name='values')
    value_text = models.CharField(max_length=200, blank=True)
    value_number = models.DecimalField(max_digits=12, decimal_places=3,
                                       null=True, blank=True)
    value_boolean = models.BooleanField(null=True, blank=True)
    value_option = models.ForeignKey(AttributeOption, on_delete=models.PROTECT,
                                     null=True, blank=True, related_name='values')

    class Meta:
        ordering = ['attribute__sort_order', 'attribute__name']
        unique_together = [('product', 'attribute')]

    def __str__(self):
        return f'{self.product_id}: {self.attribute.name} = {self.display()}'

    # -- reading -----------------------------------------------------------
    @property
    def value(self):
        kind = self.attribute.data_type
        if kind == CHOICE:
            return self.value_option
        if kind == NUMBER:
            return self.value_number
        if kind == BOOLEAN:
            return self.value_boolean
        return self.value_text

    def display(self):
        """How this reads on a product page or a till line."""
        kind = self.attribute.data_type
        if kind == CHOICE:
            return self.value_option.label if self.value_option else ''
        if kind == NUMBER:
            if self.value_number is None:
                return ''
            text = format(self.value_number.normalize(), 'f')
            return f'{text} {self.attribute.unit}'.strip()
        if kind == BOOLEAN:
            if self.value_boolean is None:
                return ''
            return 'Yes' if self.value_boolean else 'No'
        return self.value_text

    # -- writing -----------------------------------------------------------
    def set_value(self, raw):
        """Put ``raw`` in the column its attribute calls for.

        Everything arrives as a string from a form, so the parsing lives here
        rather than in every caller.
        """
        self.value_text = ''
        self.value_number = None
        self.value_boolean = None
        self.value_option = None

        kind = self.attribute.data_type
        if raw is None or raw == '':
            return self

        if kind == CHOICE:
            self.value_option = self._resolve_option(raw)
        elif kind == NUMBER:
            try:
                self.value_number = Decimal(str(raw).strip().replace(',', '.'))
            except (InvalidOperation, ValueError):
                raise ValidationError(f'{self.attribute.name} must be a number.')
        elif kind == BOOLEAN:
            self.value_boolean = str(raw).strip().lower() in {'1', 'true', 'yes', 'on', 'y'}
        else:
            self.value_text = str(raw).strip()[:200]
        return self

    def _resolve_option(self, raw):
        if isinstance(raw, AttributeOption):
            if raw.attribute_id != self.attribute_id:
                raise ValidationError('That option belongs to a different attribute.')
            return raw
        options = self.attribute.options
        text = str(raw).strip()
        option = options.filter(pk=text).first() if text.isdigit() else None
        if option is None:
            option = options.filter(label__iexact=text).first()
        if option is None:
            raise ValidationError(
                f'"{raw}" is not one of the options for {self.attribute.name}.')
        return option

    def clean(self):
        # An option from a different attribute would quietly mislabel a product.
        if self.value_option and self.value_option.attribute_id != self.attribute_id:
            raise ValidationError(
                {'value_option': 'That option belongs to a different attribute.'})


def attributes_for_category(category):
    """Every attribute that applies to ``category``, its own and inherited.

    Attributes cascade down the tree, so "Colour" defined on Accessories is
    answered by every subcategory under it. The nearest definition of a code
    wins, which lets a subcategory override one it inherited.
    """
    if category is None:
        return []

    chain, node, seen = [], category, set()
    while node is not None and node.pk not in seen:
        seen.add(node.pk)
        chain.append(node.pk)
        node = node.parent

    found = {}
    # Walk from the category upwards, so the nearest definition wins.
    for category_id in chain:
        for attribute in CategoryAttribute.objects.filter(category_id=category_id):
            found.setdefault(attribute.code, attribute)
    return sorted(found.values(), key=lambda a: (a.sort_order, a.name))


def variant_attributes_for_category(category):
    """The attributes that make two items separate products - matrix entry."""
    return [a for a in attributes_for_category(category) if a.variant_attribute]
