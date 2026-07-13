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
    nif = models.CharField(max_length=9, unique=True, verbose_name="NIF")
    name = models.CharField(max_length=100, db_index=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    notes = models.TextField(blank=True, null=True, verbose_name="Notes")

    def __str__(self):
        return f"{self.name} ({self.nif})"
