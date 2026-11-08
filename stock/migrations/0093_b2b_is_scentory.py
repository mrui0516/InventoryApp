"""B2B is Scentory's for now, and decants are made to order.

* Wholesale orders go to Scentory (its Pedido, its phone on the customer's pages)
  when the setting was left empty or on the default shop.
* Decant products still filed as Perfume become Amostra: 0082 only moved the brand
  "Decante", and production's are filed as brand "Amostra", model "Decante".
"""
from django.db import migrations
from django.db.models import Q


def forward(apps, schema_editor):
    Store = apps.get_model('stock', 'Store')
    BusinessSettings = apps.get_model('stock', 'BusinessSettings')
    ProductType = apps.get_model('stock', 'ProductType')
    Product = apps.get_model('stock', 'Product')

    scentory = Store.objects.filter(name__istartswith='Scentory', is_active=True).first()
    if scentory is not None:
        for row in BusinessSettings.objects.all():
            current = row.wholesale_store
            if current is None or current.is_default:
                row.wholesale_store = scentory
                row.save(update_fields=['wholesale_store'])

    sample = ProductType.objects.filter(code='amostra').first()
    if sample is not None:
        if not sample.picks_perfume:
            sample.picks_perfume = True
            sample.save(update_fields=['picks_perfume'])
        decant = Q(brand__icontains='decant') | Q(model__icontains='decant') | Q(name__icontains='decant')
        Product.objects.filter(decant, product_type_id='perfume', is_mix=False).update(product_type_id='amostra')


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0092_restock_notified'),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
