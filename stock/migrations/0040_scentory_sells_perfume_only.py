"""Scentory is a pure perfume shop; Khan Perfume is warehouse + shop and sells
everything it stocks, which an empty category set already means. Data only, so
a store that has been reconfigured by hand keeps its own choice.
"""
from django.db import migrations


def configure(apps, schema_editor):
    Store = apps.get_model('stock', 'Store')
    Category = apps.get_model('stock', 'Category')
    scentory = Store.objects.filter(code='SHOP2').first()
    if scentory is None or scentory.sellable_categories.exists():
        return
    perfume = Category.objects.filter(name__icontains='perfum')
    if perfume.exists():
        scentory.sellable_categories.set(perfume)


def unconfigure(apps, schema_editor):
    Store = apps.get_model('stock', 'Store')
    scentory = Store.objects.filter(code='SHOP2').first()
    if scentory is not None:
        scentory.sellable_categories.clear()


class Migration(migrations.Migration):
    dependencies = [('stock', '0039_store_sellable_categories')]
    operations = [migrations.RunPython(configure, unconfigure)]
