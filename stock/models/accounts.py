"""Customer accounts for the wholesale catalogue (services/portal.py).

A customer logs in with the phone on its account; its password is Django's, set
by the customer from a one-time link the shop sends. A customer's User has no
staff flag and no group, and the portal guard keeps it inside the catalogue.
"""
from django.conf import settings
from django.db import models


class CustomerAccount(models.Model):
    INVITED, ACTIVE, SUSPENDED = 'invited', 'active', 'suspended'
    STATUSES = [(INVITED, 'Invited'), (ACTIVE, 'Active'), (SUSPENDED, 'Suspended')]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='customer_account')
    customer = models.ForeignKey('stock.Customer', on_delete=models.PROTECT, related_name='accounts')
    # Digits with the country code (services/contacts.international): the login.
    phone = models.CharField(max_length=20, unique=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=INVITED)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    invited_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.customer} ({self.get_status_display()})'


class AccountLink(models.Model):
    """A one-time link to set the password: the first time, or after forgetting it.
    Only the digest is kept; a new link ends the ones before it."""
    INVITE, RESET = 'invite', 'reset'
    account = models.ForeignKey(CustomerAccount, on_delete=models.CASCADE, related_name='links')
    token_hash = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(max_length=10, choices=[(INVITE, 'Invite'), (RESET, 'New password')])
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
