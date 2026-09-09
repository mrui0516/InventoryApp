"""Split customers into Retail and Revenda.

Before this field existed the only way to mark a reseller was to type "Revenda"
into their name, so that is where the backfill reads it from. It matches on the
stem "revend" to catch "Revenda", "REVENDA" and "revendedor" alike, and prints
the names it changed: this runs once against a live shop database and the shop
owner needs to see whether the list matches what they expect before trusting it.

Anybody who resells without the word in their name is left as Retail on purpose
- guessing from order volume would put real retail customers on the wholesale
side. They are switched over by hand on the customer page.
"""
from django.db import migrations, models


def mark_revenda_from_name(apps, schema_editor):
    Customer = apps.get_model('stock', 'Customer')
    matched = list(Customer.objects.filter(name__icontains='revend')
                   .order_by('name').values_list('name', flat=True))
    if not matched:
        print('  customer kind: no name contains "revend" - all Retail')
        return
    Customer.objects.filter(name__icontains='revend').update(kind='revenda')
    print(f'  customer kind: {len(matched)} marked Revenda from their name')
    for name in matched:
        print(f'    - {name}')


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0056_backfill_inspired_by_text'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='kind',
            field=models.CharField(
                choices=[('retail', 'Retail'), ('revenda', 'Revenda')],
                db_index=True, default='retail', max_length=10,
                verbose_name='Customer type'),
        ),
        # Reverse is a no-op: undoing this migration drops the column anyway.
        migrations.RunPython(mark_revenda_from_name, migrations.RunPython.noop),
    ]
