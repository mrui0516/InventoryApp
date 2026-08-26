"""Generalise the shelf grid: styles pick the axis that forms their columns.

Cases are stocked by colour; screen protectors are not - they are stocked by
glue and edge. Hardcoding colour as the only column set would have meant a
code change for the next kind of goods, so the columns become data.

Renames rather than drops: the shelf tables were created days ago and carry no
stock rows yet, but the ten colours the shop seeded are kept and moved onto a
"Colour" axis rather than being recreated.
"""
from django.db import migrations, models
import django.db.models.deletion


def seed_axis(apps, schema_editor):
    """Put everything that exists onto a Colour axis."""
    ShelfAxis = apps.get_model('stock', 'ShelfAxis')
    ShelfOption = apps.get_model('stock', 'ShelfOption')
    ShelfStyle = apps.get_model('stock', 'ShelfStyle')

    colour, _created = ShelfAxis.objects.get_or_create(
        slug='colour', defaults={'name': 'Colour', 'sort_order': 1})
    ShelfOption.objects.filter(axis__isnull=True).update(axis=colour)
    ShelfStyle.objects.filter(axis__isnull=True).update(axis=colour)


def unseed_axis(apps, schema_editor):
    apps.get_model('stock', 'ShelfOption').objects.update(axis=None)
    apps.get_model('stock', 'ShelfStyle').objects.update(axis=None)


class Migration(migrations.Migration):

    dependencies = [('stock', '0050_casestyle_shelfcolour_alter_devicemodel_options_and_more')]

    operations = [
        migrations.RenameModel(old_name='CaseStyle', new_name='ShelfStyle'),
        migrations.RenameModel(old_name='ShelfColour', new_name='ShelfOption'),
        migrations.RenameModel(old_name='CaseStock', new_name='ShelfStock'),
        migrations.RenameField(model_name='shelfstock', old_name='colour',
                               new_name='option'),
        migrations.AlterUniqueTogether(
            name='shelfstock', unique_together={('style', 'model', 'option')}),
        migrations.AlterModelOptions(
            name='shelfstock', options={'ordering': ['model', 'option']}),
        migrations.CreateModel(
            name='ShelfAxis',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=40, unique=True)),
                ('slug', models.SlugField(max_length=40, unique=True)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
            ],
            options={'ordering': ['sort_order', 'name'],
                     'verbose_name_plural': 'shelf axes'},
        ),
        migrations.AddField(
            model_name='shelfoption', name='axis',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='options', to='stock.shelfaxis'),
        ),
        migrations.AddField(
            model_name='shelfstyle', name='axis',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='styles', to='stock.shelfaxis'),
        ),
        migrations.RunPython(seed_axis, unseed_axis),
        migrations.AlterField(
            model_name='shelfoption', name='axis',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name='options', to='stock.shelfaxis'),
        ),
        migrations.AlterField(
            model_name='shelfstyle', name='axis',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                    related_name='styles', to='stock.shelfaxis'),
        ),
        migrations.AlterField(
            model_name='shelfoption', name='name',
            field=models.CharField(max_length=40),
        ),
        migrations.AlterUniqueTogether(
            name='shelfoption', unique_together={('axis', 'name')}),
    ]
