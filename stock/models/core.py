# stock/models/core.py
"""Cross-cutting configuration models used across every domain."""
from django.conf import settings
from django.db import models


class Store(models.Model):
    """A physical shop. Inventory / inbound / suppliers are shared across all
    stores; sales, employees, AR and reporting are scoped per store."""
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, unique=True)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # Which categories this store actually sells. Khan Perfume carries perfume
    # and phone accessories; Scentory is perfume only, and its staff should not
    # be wading through phone cases at the till. Left empty the store sells
    # everything, so adding a category later is a tick box, not a code change.
    sellable_categories = models.ManyToManyField(
        'Category', blank=True, related_name='stores')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def sells_everything(self):
        return not self.sellable_categories.exists()

    @classmethod
    def get_default(cls):
        return cls.objects.filter(is_default=True).first() or cls.objects.order_by('id').first()


class StoreProfile(models.Model):
    """Each user's home store. Employees are locked to it; managers/admins can
    switch the active store (and view all stores)."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='store_profile',
    )
    store = models.ForeignKey(
        Store, on_delete=models.SET_NULL, null=True, blank=True, related_name='staff',
    )

    def __str__(self):
        return f"{self.user} @ {self.store or 'no store'}"


class PrintProfile(models.Model):
    store = models.OneToOneField(
        Store, on_delete=models.CASCADE, null=True, blank=True, related_name='print_profile',
    )
    name = models.CharField(max_length=120, default='KHAN PERFUME')
    nif = models.CharField(max_length=32, blank=True, default='517067226')
    phone = models.CharField(max_length=40, blank=True, default='(+351) 920 106 263')
    address = models.CharField(max_length=255, blank=True, default='CENTRO BABILONIA LOJA 90A, AMADORA, 2700-337')
    email = models.EmailField(blank=True, default='SADIWALIKHAN@YAHOO.COM')
    footer_note = models.CharField(max_length=160, blank=True, default='Thank you for your purchase.')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Print profile'
        verbose_name_plural = 'Print profiles'

    def __str__(self):
        return f"{self.name} ({self.store.name})" if self.store_id else (self.name or 'Print profile')

    @classmethod
    def get_for_store(cls, store):
        """Return the print header for a store, creating one seeded from the
        default store's profile (or model defaults) the first time."""
        if store is None:
            store = Store.get_default()
        if store is None:
            # No stores exist yet — fall back to a legacy singleton.
            profile, _ = cls.objects.get_or_create(store__isnull=True, defaults={})
            return profile

        profile = cls.objects.filter(store=store).first()
        if profile:
            return profile

        default_store = Store.get_default()
        base = cls.objects.filter(store=default_store).first() if default_store and default_store != store else None
        if base:
            return cls.objects.create(
                store=store, name=store.name or base.name, nif=base.nif, phone=base.phone,
                address=base.address, email=base.email, footer_note=base.footer_note,
            )
        return cls.objects.create(store=store, name=store.name or 'KHAN PERFUME')

    @classmethod
    def get_solo(cls):
        """Backward-compatible accessor → the default store's print header."""
        return cls.get_for_store(Store.get_default())
