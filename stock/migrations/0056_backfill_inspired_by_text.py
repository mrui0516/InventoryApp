"""Carry the five recorded inspirations into the free-text field.

"Inspired by" is typed in now rather than chosen from a list, so the rows that
already used the lookup keep their value instead of appearing blank the next
time somebody opens the product.
"""
from django.db import migrations


def copy_forward(apps, schema_editor):
    Product = apps.get_model('stock', 'Product')
    for product in Product.objects.filter(inspired_by__isnull=False,
                                          inspired_by_text=''):
        reference = product.inspired_by
        text = ' '.join(part for part in [reference.house, reference.name] if part)
        if text:
            product.inspired_by_text = text[:120]
            product.save(update_fields=['inspired_by_text'])


def clear(apps, schema_editor):
    apps.get_model('stock', 'Product').objects.update(inspired_by_text='')


class Migration(migrations.Migration):
    dependencies = [('stock', '0055_alter_product_name')]
    operations = [migrations.RunPython(copy_forward, clear)]
