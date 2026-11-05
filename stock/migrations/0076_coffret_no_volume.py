"""A coffret holds several things, so it has no volume of its own."""
from django.db import migrations


def forward(apps, schema_editor):
    Product = apps.get_model('stock', 'Product')
    ProductType = apps.get_model('stock', 'ProductType')
    ProductType.objects.filter(code='coffret').update(takes_volume=False)
    Product.objects.filter(product_type='coffret').update(volume_ml=None, spec='')


def backward(apps, schema_editor):
    apps.get_model('stock', 'ProductType').objects.filter(code='coffret').update(takes_volume=True)


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0075_product_type_table'),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
