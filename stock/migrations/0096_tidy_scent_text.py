from collections import defaultdict

from django.db import migrations


def forward(apps, schema_editor):
    """Once, for the whole catalogue, what saving the product form now does
    (services/scent_text.tidy): a name equal to the model goes, and labelled
    note / family / "Inspirado em" lines leave the description for their
    fields. A field already filled wins; prose is left as written. Run on
    deploy without a preview (the owner's choice, 2026-11-16), after backup_db."""
    from stock.services.scent_text import NOTE_FIELDS, tidy

    Product = apps.get_model('stock', 'Product')
    FragranceFamily = apps.get_model('stock', 'FragranceFamily')
    Through = Product.fragrance_families.through
    families = list(FragranceFamily.objects.values_list('id', 'name', 'shopify_tag'))
    own = defaultdict(list)
    for product_id, family_id in Through.objects.values_list('product_id', 'fragrancefamily_id'):
        own[product_id].append(family_id)

    fields = ['name', 'description', 'inspired_by_text', *NOTE_FIELDS.values()]
    for row in Product.objects.values('pk', 'model', *fields).iterator():
        data = {**row, 'family_ids': own.get(row['pk'], [])}
        tidied = tidy(data, families)
        changes = {key: tidied[key] for key in fields if tidied[key] != data[key]}
        if changes:
            Product.objects.filter(pk=row['pk']).update(**changes)
        if tidied['family_ids'] != data['family_ids']:
            Through.objects.bulk_create(
                [Through(product_id=row['pk'], fragrancefamily_id=pk) for pk in tidied['family_ids']])


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0095_scent_source'),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
