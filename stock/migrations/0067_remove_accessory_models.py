"""The accessory code is gone (2026-11-01): device fitment, the shelf map,
shop-defined category attributes and internal barcodes.

Whole tables are dropped, the ones that point at others first, so SQLite never
has to rebuild a table around a constraint naming a column already removed.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0066_perfume_type'),
    ]

    operations = [
        # shop-defined attributes
        migrations.DeleteModel(name='ProductAttributeValue'),
        migrations.DeleteModel(name='AttributeOption'),
        migrations.DeleteModel(name='CategoryAttribute'),
        # shelf map
        migrations.DeleteModel(name='ShelfStock'),
        migrations.DeleteModel(name='ModelShelfNote'),
        migrations.DeleteModel(name='ShelfStyle'),
        migrations.DeleteModel(name='ShelfOption'),
        migrations.DeleteModel(name='ShelfAxis'),
        # device fitment
        migrations.RemoveField(model_name='product', name='device_models'),
        migrations.RemoveField(model_name='product', name='compatibility_groups'),
        migrations.RemoveField(model_name='product', name='universal_fit'),
        migrations.DeleteModel(name='DeviceAlias'),
        migrations.DeleteModel(name='CompatibilityGroup'),
        migrations.DeleteModel(name='DeviceModel'),
        # internal barcodes
        migrations.RemoveField(model_name='product', name='barcode_is_internal'),
        migrations.AlterField(
            model_name='category',
            name='form_kind',
            field=models.CharField(choices=[('general', 'General'), ('perfume', 'Perfume')], default='general', max_length=12),
        ),
    ]
