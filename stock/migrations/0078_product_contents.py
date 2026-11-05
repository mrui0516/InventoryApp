import django.db.models.deletion
from django.db import migrations, models


def forward(apps, schema_editor):
    apps.get_model('stock', 'Product').objects.filter(model__iexact='mix').update(is_mix=True)


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0077_brand_logo'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='is_mix',
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name='ProductContent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('linked', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='contained_in', to='stock.product')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contents', to='stock.product')),
            ],
            options={'ordering': ['sort_order', 'id']},
        ),
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
