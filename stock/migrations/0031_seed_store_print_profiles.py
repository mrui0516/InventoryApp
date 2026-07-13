from django.db import migrations


def seed_store_print_profiles(apps, schema_editor):
    Store = apps.get_model('stock', 'Store')
    PrintProfile = apps.get_model('stock', 'PrintProfile')

    default_store = Store.objects.filter(is_default=True).first() or Store.objects.order_by('id').first()
    if not default_store:
        return

    # Attach the existing singleton print header to the default store.
    base = PrintProfile.objects.filter(store__isnull=True).order_by('id').first()
    if base:
        base.store = default_store
        base.save(update_fields=['store'])
    else:
        base = PrintProfile.objects.filter(store=default_store).first()

    # Give every other store its own header, seeded from the default one.
    for store in Store.objects.exclude(id=default_store.id):
        if PrintProfile.objects.filter(store=store).exists():
            continue
        PrintProfile.objects.create(
            store=store,
            name=store.name or (base.name if base else 'KHAN PERFUME'),
            nif=base.nif if base else '',
            phone=base.phone if base else '',
            address=base.address if base else '',
            email=base.email if base else '',
            footer_note=base.footer_note if base else 'Thank you for your purchase.',
        )


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0030_alter_printprofile_options_printprofile_store'),
    ]

    operations = [
        migrations.RunPython(seed_store_print_profiles, migrations.RunPython.noop),
    ]
