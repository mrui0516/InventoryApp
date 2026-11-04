"""Oil perfume and body mist stop being concentrations and become types.

A perfume or mini that carried one of the two takes the new type. Every other
type loses its concentration: only perfume and mini are asked for one now.
"""
from django.db import migrations, models
from django.db.models import Q

MOVED = (
    ('oil_perfume', 'Attar / Oil', 'Attar', 50),
    ('body_mist', 'Body Mist', 'Mist', 60),
)


def forward(apps, schema_editor):
    Product = apps.get_model('stock', 'Product')
    Concentration = apps.get_model('stock', 'Concentration')
    for kind, name, short, _sort in MOVED:
        old = Concentration.objects.filter(Q(name=name) | Q(short=short))
        Product.objects.filter(concentration__in=old, perfume_type__in=('perfume', 'mini')).update(perfume_type=kind)
        Product.objects.filter(concentration__in=old).update(concentration=None)
        old.delete()
    (Product.objects.exclude(perfume_type__in=('perfume', 'mini')).exclude(concentration=None)
     .update(concentration=None))


def backward(apps, schema_editor):
    Product = apps.get_model('stock', 'Product')
    Concentration = apps.get_model('stock', 'Concentration')
    for kind, name, short, sort in MOVED:
        row, _ = Concentration.objects.get_or_create(name=name, defaults={'short': short, 'sort_order': sort})
        Product.objects.filter(perfume_type=kind).update(perfume_type='perfume', concentration=row)


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0073_inbound_iva_shipping_invoice'),
    ]

    operations = [
        migrations.AlterField(
            model_name='product',
            name='perfume_type',
            field=models.CharField(choices=[('perfume', 'Perfume'), ('mini', 'Mini'), ('oil_perfume', 'Oil perfume'), ('body_mist', 'Body mist / splash'), ('body_spray', 'Body spray / Deo'), ('air_freshener', 'Air freshener'), ('coffret', 'Coffret (gift set)')], db_index=True, default='perfume', max_length=16, verbose_name='Type'),
        ),
        migrations.RunPython(forward, backward),
    ]
