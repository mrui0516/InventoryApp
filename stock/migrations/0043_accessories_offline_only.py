"""Phone accessories and shisha are shop-floor only; perfume is the online range.

Data only, so a category someone has already configured by hand is left alone
- this just sets the starting position for the categories that exist today.
"""
from django.db import migrations


def offline(apps, schema_editor):
    Category = apps.get_model('stock', 'Category')
    (Category.objects
     .exclude(name__icontains='perfum')
     .update(sync_to_shopify=False))


def online(apps, schema_editor):
    apps.get_model('stock', 'Category').objects.update(sync_to_shopify=True)


class Migration(migrations.Migration):
    dependencies = [('stock', '0042_category_sync_to_shopify')]
    operations = [migrations.RunPython(offline, online)]
