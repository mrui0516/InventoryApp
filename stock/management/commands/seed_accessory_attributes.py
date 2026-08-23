"""Give the accessory categories a sensible starting set of attributes.

Everything here is editable in the admin afterwards - this only saves the shop
from typing the obvious ones by hand. Re-running it adds what is missing and
leaves anything already defined alone, so it is safe after hand-editing.

Dry-run by default; pass --apply to write.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from stock.models import AttributeOption, Category, CategoryAttribute

# category name -> attributes. Each attribute is
# (name, code, data_type, variant?, unit, [options])
PLAN = {
    'Accessories': [
        ('Colour', 'colour', 'choice', True, '',
         ['Black', 'Clear', 'White', 'Blue', 'Red', 'Green', 'Pink', 'Gold',
          'Silver', 'Purple']),
    ],
    'Cases': [
        ('Case type', 'case_type', 'choice', True, '',
         ['Soft TPU', 'Hard PC', 'Silicone', 'Flip / Wallet', 'MagSafe',
          'Rugged / Armour', 'Transparent']),
        ('Wireless charging', 'wireless_ok', 'boolean', False, '', []),
    ],
    'Screen protectors': [
        ('Protector type', 'protector_type', 'choice', True, '',
         ['Tempered glass', 'Hydrogel / Film', 'Privacy', 'Matte']),
        ('Glue', 'glue', 'choice', True, '',
         ['Full glue', 'Edge glue', 'UV glue']),
        ('Edge', 'edge', 'choice', True, '',
         ['Flat', 'Curved 3D', '2.5D']),
        ('Hardness', 'hardness', 'text', False, '', []),
    ],
    'Cables & chargers': [
        ('Connector', 'connector', 'choice', True, '',
         ['USB-C to USB-C', 'USB-C to Lightning', 'USB-A to USB-C',
          'USB-A to Lightning', 'USB-A to Micro-USB']),
        ('Length', 'length_m', 'number', True, 'm', []),
        ('Power', 'power_w', 'number', False, 'W', []),
    ],
    'Audio': [
        ('Form', 'audio_form', 'choice', True, '',
         ['In-ear', 'Over-ear', 'TWS earbuds', 'Wired earphones', 'Speaker']),
        ('Connection', 'connection', 'choice', True, '',
         ['Bluetooth', 'Wired 3.5mm', 'USB-C']),
        ('Noise cancelling', 'anc', 'boolean', False, '', []),
    ],
    'Computer peripherals': [
        ('Device type', 'peripheral_type', 'choice', True, '',
         ['Mouse', 'Keyboard', 'Mousepad', 'Hub / Dock', 'Webcam']),
        ('Connection', 'connection', 'choice', True, '',
         ['Wired', 'Wireless 2.4G', 'Bluetooth']),
    ],
}

# Subcategories created under Accessories so the tree carries Colour down.
CHILDREN = ['Cases', 'Screen protectors', 'Cables & chargers', 'Audio',
            'Computer peripherals']


class Command(BaseCommand):
    help = 'Create the starting attribute set for accessory categories.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='write changes (default: dry run)')

    def handle(self, *args, **opts):
        apply = opts['apply']
        created = {'categories': 0, 'attributes': 0, 'options': 0}

        with transaction.atomic():
            parent = Category.objects.filter(name__iexact='Accessories').first()
            if parent is None:
                parent = Category(name='Accessories')
                created['categories'] += 1
                if apply:
                    parent.save()

            for name in CHILDREN:
                if not Category.objects.filter(name__iexact=name).exists():
                    created['categories'] += 1
                    if apply:
                        Category.objects.create(name=name, parent=parent)

            for category_name, attributes in PLAN.items():
                category = Category.objects.filter(name__iexact=category_name).first()
                for order, spec in enumerate(attributes, start=1):
                    label, code, kind, variant, unit, options = spec
                    attribute = None
                    if category is not None:
                        attribute = CategoryAttribute.objects.filter(
                            category=category, code=code).first()
                    if attribute is None:
                        created['attributes'] += 1
                        if apply:
                            attribute = CategoryAttribute.objects.create(
                                category=category, name=label, code=code,
                                data_type=kind, unit=unit,
                                variant_attribute=variant, sort_order=order)
                    if attribute is None:
                        # Dry run on a category that does not exist yet: every
                        # option would be new, so say so rather than under-count.
                        created['options'] += len(options)
                        continue
                    for position, option_label in enumerate(options, start=1):
                        if attribute.options.filter(label__iexact=option_label).exists():
                            continue
                        created['options'] += 1
                        if apply:
                            AttributeOption.objects.create(
                                attribute=attribute, label=option_label,
                                sort_order=position)

            if not apply:
                transaction.set_rollback(True)

        for key, value in created.items():
            self.stdout.write(f'  {key:<14} {value}')
        self.stdout.write(self.style.SUCCESS('Applied.') if apply
                          else self.style.WARNING('Dry run - nothing written. Use --apply.'))
