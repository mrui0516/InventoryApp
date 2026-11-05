"""Product types become rows; the rules become switches on them."""
import django.db.models.deletion
from django.db import migrations, models

# code, name, name_word, customer_label, concentration, discount, gift, contents, scent
SEED = [
    ('perfume', 'Perfume', '', 'Perfume', True, True, False, False, True),
    ('mini', 'Mini', 'Mini', 'Mini', True, False, False, False, True),
    ('oil_perfume', 'Oil perfume', 'Oil perfume', '\u00d3leo de perfume', False, False, False, False, True),
    ('body_mist', 'Body mist / splash', 'Body mist', 'Body mist', False, False, False, True, False),
    ('body_spray', 'Body spray / Deo', 'Body spray', 'Body spray', False, False, False, True, False),
    ('air_freshener', 'Air freshener', 'Air freshener', 'Ambientador', False, False, False, True, False),
    ('coffret', 'Coffret (gift set)', 'Coffret', 'Coffret', False, False, True, True, False),
]


def seed(apps, schema_editor):
    ProductType = apps.get_model('stock', 'ProductType')
    Category = apps.get_model('stock', 'Category')
    perfumes = Category.objects.filter(parent=None, name__iexact='Perfumes').first()
    for n, row in enumerate(SEED, 1):
        code, name, word, label, conc, discount, gift, contents, scent = row
        ProductType.objects.create(
            code=code, name=name, name_word=word, customer_label=label, default_category=perfumes,
            takes_concentration=conc, takes_volume=True, wholesale_discount=discount, is_gift=gift,
            has_contents=contents, shows_scent_details=scent, sort_order=n * 10)


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0074_oil_perfume_body_mist_types'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=16, unique=True)),
                ('name', models.CharField(max_length=60)),
                ('name_word', models.CharField(blank=True, default='', max_length=40)),
                ('customer_label', models.CharField(blank=True, default='', max_length=60)),
                ('takes_concentration', models.BooleanField(default=False)),
                ('takes_volume', models.BooleanField(default=True)),
                ('wholesale_discount', models.BooleanField(default=False)),
                ('is_gift', models.BooleanField(default=False)),
                ('has_contents', models.BooleanField(default=False)),
                ('shows_scent_details', models.BooleanField(default=True)),
                ('sort_order', models.PositiveIntegerField(default=100)),
                ('default_category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='product_types', to='stock.category')),
            ],
            options={'ordering': ['sort_order', 'name']},
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
        migrations.RenameField(model_name='product', old_name='perfume_type', new_name='product_type'),
        migrations.AlterField(
            model_name='product',
            name='product_type',
            field=models.ForeignKey(default='perfume', on_delete=django.db.models.deletion.PROTECT, related_name='products', to='stock.producttype', to_field='code', verbose_name='Type'),
        ),
    ]
