from django.db import migrations, models


def add_sample_type(apps, schema_editor):
    ProductType = apps.get_model('stock', 'ProductType')
    Product = apps.get_model('stock', 'Product')
    perfume = ProductType.objects.filter(code='perfume').first()
    last = ProductType.objects.order_by('-sort_order').first()
    ProductType.objects.get_or_create(code='amostra', defaults={
        'name': 'Amostra', 'name_word': 'Amostra', 'customer_label': 'Amostra',
        'default_category': perfume.default_category if perfume else None,
        'takes_concentration': False, 'takes_volume': True, 'wholesale_discount': False, 'is_gift': False,
        'has_contents': False, 'shows_scent_details': False, 'picks_perfume': True,
        'sort_order': (last.sort_order if last else 0) + 10,
    })
    Product.objects.filter(brand__iexact='Decante', product_type_id='perfume').update(product_type_id='amostra')


def remove_sample_type(apps, schema_editor):
    Product = apps.get_model('stock', 'Product')
    Product.objects.filter(product_type_id='amostra').update(product_type_id='perfume')
    apps.get_model('stock', 'ProductType').objects.filter(code='amostra').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0081_order_shipping_fee'),
    ]

    operations = [
        migrations.AddField(
            model_name='producttype',
            name='picks_perfume',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='cartline',
            name='picks',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='orderrequestline',
            name='picks',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(add_sample_type, remove_sample_type),
    ]
