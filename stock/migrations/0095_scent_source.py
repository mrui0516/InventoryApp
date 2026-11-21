import django.db.models.deletion
from django.db import migrations, models


def forward(apps, schema_editor):
    """A list of one linked perfume on anything but a gift was a body mist or a
    mini pointing at its perfume (the Mix tick was the only way to link before):
    it becomes the scent source. A mix is now two or more scents, not a tick."""
    Product = apps.get_model('stock', 'Product')
    ProductContent = apps.get_model('stock', 'ProductContent')
    ProductType = apps.get_model('stock', 'ProductType')
    gifts = set(ProductType.objects.filter(is_gift=True).values_list('code', flat=True))
    owners = Product.objects.filter(pk__in=ProductContent.objects.values('owner_id'))
    for owner in owners:
        rows = list(ProductContent.objects.filter(owner=owner).order_by('sort_order', 'id'))
        gift = owner.product_type_id in gifts
        if not gift and len(rows) == 1 and rows[0].linked_id and rows[0].linked_id != owner.pk:
            parent = Product.objects.get(pk=rows[0].linked_id)
            root = parent.scent_source_id or parent.pk
            if root != owner.pk:
                owner.scent_source_id = root
                rows[0].delete()
                rows = []
        owner.is_mix = not gift and len(rows) >= 2
        owner.save(update_fields=['scent_source', 'is_mix'])
    Product.objects.filter(is_mix=True).exclude(pk__in=ProductContent.objects.values('owner_id')).update(is_mix=False)
    # A converted before B pointed A at B, then B at C: point A at C.
    sources = dict(Product.objects.filter(scent_source__isnull=False).values_list('pk', 'scent_source_id'))
    for pk in sources:
        root, seen = sources[pk], {pk}
        while root in sources and root not in seen:
            seen.add(root)
            root = sources[root]
        if root != sources[pk] and root != pk:
            Product.objects.filter(pk=pk).update(scent_source_id=root)


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0094_shopify_preorder_reservations'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='scent_source',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='scent_versions', to='stock.product'),
        ),
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
