import uuid

from django.db import models
from django.utils import timezone


class RestockSubscription(models.Model):
    PENDING, ACTIVE, QUEUED, NOTIFIED, CANCELLED = 'pending', 'active', 'queued', 'notified', 'cancelled'
    STATUS_CHOICES = [(PENDING,'Awaiting email confirmation'),(ACTIVE,'Waiting for stock'),
                      (QUEUED,'Notification queued'),(NOTIFIED,'Notified'),(CANCELLED,'Cancelled')]
    email = models.EmailField()
    variant_id = models.CharField(max_length=80)
    sku = models.CharField(max_length=100, db_index=True)
    product_title = models.CharField(max_length=255)
    variant_title = models.CharField(max_length=255)
    product_handle = models.SlugField(max_length=255)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    confirm_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    unsubscribe_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    generation = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    requested_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['email','variant_id'],name='restock_unique_email_variant')]
        ordering = ['-requested_at']

    def __str__(self):
        return f'{self.product_title} · {self.variant_title} · {self.email}'


class RestockEmail(models.Model):
    CONFIRM, RESTOCK = 'confirm', 'restock'
    PENDING, PROCESSING, SENT, FAILED, SKIPPED = 'pending','processing','sent','failed','skipped'
    subscription = models.ForeignKey(RestockSubscription,on_delete=models.CASCADE,related_name='emails')
    kind = models.CharField(max_length=12,choices=[(CONFIRM,'Confirm subscription'),(RESTOCK,'Back in stock')])
    generation = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=12,default=PENDING,db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now,db_index=True)
    lease_until = models.DateTimeField(null=True,blank=True)
    sent_at = models.DateTimeField(null=True,blank=True)
    last_error = models.CharField(max_length=80,blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['subscription','kind','generation'],name='restock_unique_email_job')]

    def __str__(self):
        return f'{self.kind} · {self.subscription_id} · {self.status}'
