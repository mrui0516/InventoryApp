from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('stock', '0093_b2b_is_scentory')]
    operations = [migrations.AddField(
        model_name='shopifycatalogstate', name='preorder_variant_gid',
        field=models.CharField(max_length=100, blank=True, default=''),
    )]
