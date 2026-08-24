"""Say which form each existing category gets.

Perfume categories ask for volume and concentration; accessory categories ask
what the item fits. Data only, so a category someone has already set by hand
keeps its choice.
"""
from django.db import migrations


def seed(apps, schema_editor):
    Category = apps.get_model('stock', 'Category')
    Category.objects.filter(name__icontains='perfum').update(form_kind='perfume')

    accessories = Category.objects.filter(name__iexact='Accessories').first()
    if accessories is None:
        return
    ids = [accessories.pk] + list(
        Category.objects.filter(parent=accessories).values_list('pk', flat=True))
    Category.objects.filter(pk__in=ids).update(form_kind='accessory')


def unseed(apps, schema_editor):
    apps.get_model('stock', 'Category').objects.update(form_kind='general')


class Migration(migrations.Migration):
    dependencies = [('stock', '0046_category_form_kind')]
    operations = [migrations.RunPython(seed, unseed)]
