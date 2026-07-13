from django.conf import settings
from django.db import migrations


def seed_default_store(apps, schema_editor):
    Store = apps.get_model('stock', 'Store')
    StoreProfile = apps.get_model('stock', 'StoreProfile')
    SaleOrder = apps.get_model('stock', 'SaleOrder')
    Sale = apps.get_model('stock', 'Sale')
    ARInvoice = apps.get_model('stock', 'ARInvoice')
    User = apps.get_model(settings.AUTH_USER_MODEL)

    store, _created = Store.objects.get_or_create(
        code='MAIN',
        defaults={'name': 'Amadora', 'is_default': True, 'is_active': True},
    )
    if not store.is_default:
        store.is_default = True
        store.save(update_fields=['is_default'])

    SaleOrder.objects.filter(store__isnull=True).update(store=store)
    Sale.objects.filter(store__isnull=True).update(store=store)
    ARInvoice.objects.filter(store__isnull=True).update(store=store)

    for user in User.objects.all():
        StoreProfile.objects.get_or_create(user=user, defaults={'store': store})


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0028_store_arinvoice_store_sale_store_saleorder_store_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_default_store, migrations.RunPython.noop),
    ]
