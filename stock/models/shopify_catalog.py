"""Durable work and receipt evidence for Shopify catalogue automation."""
from django.db import models
from django.utils import timezone


class ShopifyCatalogState(models.Model):
    product = models.OneToOneField('Product', on_delete=models.CASCADE,
                                   related_name='shopify_catalog_state')
    requested_revision = models.PositiveBigIntegerField(default=0)
    synced_revision = models.PositiveBigIntegerField(default=0)
    last_restocked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    product_gid = models.CharField(max_length=100, blank=True, default='')
    creation_handle = models.CharField(max_length=255, blank=True, default='')
    published_by_automation = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_error = models.TextField(blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)


class ShopifyCatalogLease(models.Model):
    name = models.CharField(max_length=30, primary_key=True)
    token = models.CharField(max_length=32, blank=True, default='')
    expires_at = models.DateTimeField(default=timezone.now)
