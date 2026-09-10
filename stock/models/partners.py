# stock/models/partners.py
"""Business partners domain: customers and suppliers."""
from django.db import models


class Supplier(models.Model):
    name = models.CharField(max_length=100, db_index=True, verbose_name="Name")
    contact_person = models.CharField(max_length=100, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True, verbose_name="WhatsApp")
    email = models.EmailField(blank=True, null=True)
    website = models.CharField(max_length=200, blank=True, null=True)
    nif = models.CharField(max_length=32, blank=True, null=True, verbose_name="Tax ID / NIF")
    country = models.CharField(max_length=50, blank=True, null=True)
    address = models.CharField(max_length=200, blank=True, null=True)
    # cross-domain reference (catalog.Category) kept as a string to avoid import coupling
    product_types = models.ManyToManyField('Category', blank=True, verbose_name="Supplied Categories")

    def __str__(self):
        return self.name


class Customer(models.Model):
    """Somebody we sell to. Two kinds, and they are not the same business.

    A Retail customer walks in and buys a bottle. A Revenda buys to sell on:
    wholesale prices, larger quantities, and - once the wholesale catalogue
    exists - the only kind that gets a login to see it. The distinction used to
    live in the name (people typed "Revenda" into it), which is why the
    migration reads it from there; from now on it is a field, so the name is
    free to be just the name.
    """

    RETAIL = 'retail'
    REVENDA = 'revenda'
    KIND_CHOICES = [
        (RETAIL, 'Retail'),
        (REVENDA, 'Revenda'),
    ]

    nif = models.CharField(max_length=9, unique=True, verbose_name="NIF")
    name = models.CharField(max_length=100, db_index=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=RETAIL,
                            db_index=True, verbose_name="Customer type")
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    # Free text on purpose: a Portuguese address is written as a block
    # ("Rua X, nº 12, 2º Esq / 2700-123 Amadora") and a Revenda's delivery
    # address is what gets copied onto the paperwork, not queried on.
    address = models.TextField(blank=True, default='', verbose_name="Address")
    notes = models.TextField(blank=True, null=True, verbose_name="Notes")

    def __str__(self):
        return f"{self.name} ({self.nif})"

    @property
    def is_revenda(self):
        return self.kind == self.REVENDA

    @property
    def kind_label(self):
        return dict(self.KIND_CHOICES).get(self.kind, self.kind)
